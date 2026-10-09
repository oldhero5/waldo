"""Terminal inference state survives pubsub loss and late-ack redelivery."""

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import redis_client, storage, tasks
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows
pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def durable(monkeypatch):
    values, events, order = {}, [], []

    def save(key, ttl, value):
        values[key] = value
        order.append("saved")

    client = SimpleNamespace(get=values.get, setex=save, publish=lambda *args: events.append(args))
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)
    monkeypatch.setattr(storage, "delete_object", lambda key: order.append("deleted"))
    monkeypatch.setattr(storage, "download_file", lambda key, path: Path(path).write_bytes(b"source"))
    monkeypatch.setattr(tasks, "_native_video_available", lambda: True)
    return SimpleNamespace(values=values, events=events, order=order)


def _run(kind, session, key):
    if kind == "predict":
        return tasks.predict_video_task.run(key, 0.2, session, model_id="model")
    if kind == "compare":
        return tasks.compare_models_task.run(session, key, True, "a", "b", 0.2)
    return tasks.predict_sam_task.run(key, True, ["camera"], 0.2)


@pytest.mark.parametrize("kind", ["predict", "compare", "sam"])
def test_redelivery_returns_saved_result_without_redownloading_deleted_input(durable, monkeypatch, kind):
    session = str(uuid.uuid4())
    key = f"inference/{uuid.uuid4()}/{session}/input.mp4"
    result = {
        "session_id": session,
        "status": "completed",
        "frames": [{"frame_index": 3}],
        "total_frames": 1,
        "results": {"a": {}, "b": {}},
    }
    calls = []

    def infer(*args, **kwargs):
        calls.append(args)
        return result

    for name in ("_predict_video_from_file", "_compare_models_from_file", "_sam_video_result"):
        monkeypatch.setattr(tasks, name, infer)
    first = _run(kind, session, key)
    assert durable.values, "terminal state must be saved before object cleanup"
    assert durable.order.index("saved") < durable.order.index("deleted")
    monkeypatch.setattr(storage, "download_file", lambda *a: pytest.fail("redelivery attempted deleted input"))
    second = _run(kind, session, key)
    assert second == first
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["predict", "compare"])
def test_download_failure_is_durable_and_publishes_terminal_failure(durable, monkeypatch, kind):
    session = str(uuid.uuid4())
    key = f"inference/{uuid.uuid4()}/{session}/input.mp4"

    def missing(*args):
        raise OSError("download unavailable")

    monkeypatch.setattr(storage, "download_file", missing)
    try:
        _run(kind, session, key)
    except OSError:
        pass
    assert durable.values, "failed transfer must not leave the UI running forever"
    assert durable.order.index("saved") < durable.order.index("deleted")
    assert any(json.loads(value)["status"] == "failed" for value in durable.values.values())
    assert durable.events
    cached = _run(kind, session, key)
    assert cached["status"] == "failed"
    assert "download unavailable" in cached["error"]


@pytest.mark.parametrize("kind,path", [("predict", "/predict/video/result/"), ("compare", "/comparisons/result/")])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_polling_recovers_terminal_state_and_requires_session_workspace(resources, durable, kind, path, status):
    _, client, member, _ = resources
    session = str(uuid.uuid4())
    durable.values[f"waldo:task:workspace:{session}"] = str(member.workspace_id)
    durable.values[f"waldo:inference:{kind}:result:{session}"] = json.dumps(
        {
            "session_id": session,
            "status": status,
            "error": "download unavailable" if status == "failed" else None,
            "frames": [],
            "total_frames": 0,
            "results": {"a": {}, "b": {}},
        }
    )
    response = client.get(f"/api/v1{path}{session}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    durable.values[f"waldo:task:workspace:{session}"] = str(uuid.uuid4())
    assert client.get(f"/api/v1{path}{session}").status_code == 404


def test_ephemeral_input_lifecycle_keeps_existing_bucket_rules(monkeypatch):
    from minio.commonconfig import ENABLED, Filter
    from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

    existing = Rule(ENABLED, rule_filter=Filter(prefix="other/"), rule_id="other", expiration=Expiration(days=20))
    configs = []
    client = SimpleNamespace(
        get_bucket_lifecycle=lambda bucket: LifecycleConfig([existing]),
        set_bucket_lifecycle=lambda bucket, config: configs.append(config),
    )
    monkeypatch.setattr(storage, "get_client", lambda: client)
    assert hasattr(storage, "ensure_inference_lifecycle"), "unconsumed inputs need bounded retention"
    storage.ensure_inference_lifecycle()
    assert existing in configs[0].rules
    rule = next(rule for rule in configs[0].rules if rule.rule_id == "waldo-inference-inputs")
    assert rule.rule_filter.prefix == "inference/"
    assert rule.expiration.days == 2


def test_ephemeral_input_lifecycle_initializes_absent_sdk_policy(monkeypatch):
    configs = []
    client = SimpleNamespace(
        get_bucket_lifecycle=lambda bucket: None,
        set_bucket_lifecycle=lambda bucket, config: configs.append(config),
    )
    monkeypatch.setattr(storage, "get_client", lambda: client)
    storage.ensure_inference_lifecycle()
    rule = configs[0].rules[0]
    assert rule.rule_filter.prefix == "inference/"
    assert rule.expiration.days == 2
    assert rule.noncurrent_version_expiration.noncurrent_days == 2
    assert rule.abort_incomplete_multipart_upload.days_after_initiation == 2


@pytest.mark.parametrize(
    "failures,error",
    [
        ((True, True), RuntimeError("Cannot open video")),
        ((True, False), RuntimeError("Cannot open video")),
        ((False, False), RuntimeError("Cannot open video")),
        ((True, True), TimeoutError()),
    ],
)
def test_comparison_terminal_failure_requires_both_models_to_fail(durable, monkeypatch, failures, error):
    attempts = iter(failures)

    def process(*args, **kwargs):
        if next(attempts):
            raise error
        return []

    monkeypatch.setitem(sys.modules, "labeler.video_labeler", SimpleNamespace(process_video_native=process))
    session = str(uuid.uuid4())
    key = f"inference/{uuid.uuid4()}/{session}/input.mp4"
    result = tasks.compare_models_task.run(session, key, True, "sam3.1", "sam3.1", 0.2, ["bus"])
    all_failed = all(failures)
    assert result["status"] == ("failed" if all_failed else "completed")
    assert bool(result.get("error")) == all_failed
    for failed, side in zip(failures, result["results"].values()):
        assert bool(side["error"]) == failed
    cached = json.loads(durable.values[f"waldo:inference:compare:result:{session}"])
    assert cached == result
    assert durable.order.index("saved") < durable.order.index("deleted")


def test_invalid_input_cannot_delete_persistent_source_object(durable):
    result = tasks.predict_video_task.run("videos/persistent.mp4", 0.2, str(uuid.uuid4()))
    assert result["status"] == "failed"
    assert "deleted" not in durable.order


def test_result_store_failure_preserves_input_for_redelivery(durable, monkeypatch):
    def unavailable(*args):
        raise ConnectionError("Redis unavailable")

    from lib import inference_results

    monkeypatch.setattr(inference_results, "save_inference_result", unavailable)
    monkeypatch.setattr(tasks, "_predict_video_from_file", lambda *a, **kw: {"status": "completed"})
    key = f"inference/{uuid.uuid4()}/{uuid.uuid4()}/input.mp4"
    with pytest.raises(ConnectionError, match="Redis unavailable"):
        tasks.predict_video_task.run(key, 0.2, "session")
    assert "deleted" not in durable.order


@pytest.mark.parametrize("kind", ["predict", "compare", "sam"])
def test_transient_result_store_errors_trigger_actual_celery_retry(monkeypatch, kind):
    from celery.exceptions import Retry
    from redis.exceptions import ConnectionError

    def unavailable(key):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(redis_client, "get_redis", lambda: SimpleNamespace(get=unavailable))
    task = {"predict": tasks.predict_video_task, "compare": tasks.compare_models_task, "sam": tasks.predict_sam_task}[
        kind
    ]
    attempts = []

    def retry(**kwargs):
        attempts.append(kwargs)
        raise Retry("retry scheduled")

    monkeypatch.setattr(task, "retry", retry)
    key = f"inference/{uuid.uuid4()}/{uuid.uuid4()}/input.mp4"
    with pytest.raises(Retry, match="retry scheduled"):
        _run(kind, "session", key)
    assert isinstance(attempts[0]["exc"], ConnectionError)


def test_worker_success_logs_do_not_embed_result_bodies():
    from celery.utils.saferepr import saferepr

    result = saferepr({"thumbnail": "private base64 payload"}, tasks.app.Task.resultrepr_maxsize)
    assert tasks.app.Task.resultrepr_maxsize == 0
    assert "private base64 payload" not in result


def test_polling_reports_exhausted_celery_failure_after_result_store_recovers(resources, durable, monkeypatch):
    _, client, member, _ = resources
    session, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    durable.values[f"waldo:task:workspace:{session}"] = str(member.workspace_id)
    durable.values[f"waldo:inference:predict:result:{session}"] = json.dumps(
        {"session_id": session, "status": "running", "celery_task_id": task_id}
    )
    seen = []
    monkeypatch.setattr(
        tasks.app,
        "AsyncResult",
        lambda value: (
            seen.append(value)
            or SimpleNamespace(state="FAILURE", result=RuntimeError("result store retries exhausted"))
        ),
    )
    response = client.get(f"/api/v1/predict/video/result/{session}")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "retries exhausted" in response.json()["error"]
    assert seen == [task_id]


def test_worker_does_not_treat_queued_metadata_as_a_cached_completion(durable, monkeypatch):
    session = str(uuid.uuid4())
    key = f"inference/{uuid.uuid4()}/{session}/input.mp4"
    durable.values[f"waldo:inference:predict:result:{session}"] = json.dumps(
        {"session_id": session, "status": "running", "celery_task_id": "task"}
    )
    monkeypatch.setattr(tasks, "_predict_video_from_file", lambda *a, **kw: {"status": "completed", "frames": []})
    assert tasks.predict_video_task.run(key, 0.2, session)["status"] == "completed"
