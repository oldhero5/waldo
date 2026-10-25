"""Authorized, bounded native sample reads and truthful coverage."""

import base64
import json
import uuid

import pytest

from lib import db
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows


def _row(session, own, index=0, **values):
    model = getattr(db, "NativeFrameAssessment", None)
    assert model is not None, "native sample evidence has no durable table"
    row = model(
        job_id=own["job"].id,
        video_id=own["video"].id,
        source_frame_index=index,
        timestamp_s=-0.125 + index / 10,
        timestamp_method="source_pts",
        frame_duration_s=None,
        source_width=160,
        source_height=120,
        detection_count=0,
    )
    for key, value in values.items():
        setattr(row, key, value)
    session.add(row)
    return row


def _complete(own, count):
    own["job"].processing_summary = {
        "videos": [
            {
                "video_id": str(own["video"].id),
                "status": "completed",
                "native_assessment_version": 1,
                "sampled_frames": count,
            }
        ]
    }


def _cursor(job, video, index):
    return (
        base64.urlsafe_b64encode(
            json.dumps({"job_id": str(job), "video_id": str(video), "source_frame_index": index}).encode()
        )
        .decode()
        .rstrip("=")
    )


def test_assessment_pages_retain_empty_samples_and_relative_times(resources):
    session, client, _, rows = resources
    own = rows["own"]
    _row(session, own, 10, timestamp_method="frame_index/fps", detection_count=1)
    _row(session, own, 0, frame_duration_s=0.04)
    _row(session, own, 5)
    _complete(own, 3)
    session.commit()
    url = f"/api/v1/jobs/{own['job'].id}/assessments"
    response = client.get(url, params={"limit": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["coverage_status"] == "available"
    assert page["coverage_scope"] == "sampled_native_frames"
    assert [r["source_frame_index"] for r in page["items"]] == [0, 5]
    assert page["items"][0]["timestamp_s"] == -0.125
    assert page["items"][0]["frame_duration_s"] == 0.04
    last = client.get(url, params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [r["source_frame_index"] for r in last["items"]] == [10]
    assert last["items"][0]["timestamp_method"] == "frame_index/fps"
    assert last["next_cursor"] is None


@pytest.mark.parametrize(
    "case,expected",
    [
        ("legacy", "partial"),
        ("unfinished", "partial"),
        ("count_mismatch", "partial"),
        ("merged", "partial"),
        ("child", "available"),
        ("complete", "available"),
    ],
)
def test_coverage_is_based_on_original_run_samples(resources, case, expected):
    session, client, _, rows = resources
    own = rows["own"]
    _row(session, own, detection_count=8)
    _complete(own, 1)
    if case == "legacy":
        own["job"].processing_summary = {
            "videos": [{"video_id": str(own["video"].id), "status": "completed", "sampled_frames": 1}]
        }
    elif case == "unfinished":
        own["job"].status = "labeling"
    elif case == "count_mismatch":
        _complete(own, 2)
    elif case == "merged":
        own["job"].processing_summary = {
            **own["job"].processing_summary,
            "merged_runs": [{"job_id": str(uuid.uuid4())}],
        }
    elif case == "child":
        own["job"].parent_id = rows["foreign"]["job"].id
    session.commit()
    response = client.get(f"/api/v1/jobs/{own['job'].id}/assessments")
    assert response.status_code == 200, response.text
    assert response.json()["coverage_status"] == expected
    assert response.json()["items"][0]["detection_count"] == 8


def test_no_own_rows_is_unavailable_and_foreign_video_is_hidden(resources):
    session, client, _, rows = resources
    own = rows["own"]
    _row(session, own, video_id=rows["foreign"]["video"].id)
    _complete(own, 1)
    session.commit()
    response = client.get(f"/api/v1/jobs/{own['job'].id}/assessments")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["coverage_status"] == "unavailable"
    assert (
        client.get(f"/api/v1/jobs/{rows['foreign']['job'].id}/assessments", params={"cursor": "bad"}).status_code == 404
    )


@pytest.mark.parametrize("index", [True, -1, 2147483648, 1.2, "0"])
def test_cursor_rejects_invalid_index(resources, index):
    _, client, _, rows = resources
    own = rows["own"]
    response = client.get(
        f"/api/v1/jobs/{own['job'].id}/assessments", params={"cursor": _cursor(own["job"].id, own["video"].id, index)}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("cursor", ["!", "e30", "W10", "x" * 257])
def test_cursor_rejects_malformed_payload(resources, cursor):
    _, client, _, rows = resources
    assert client.get(f"/api/v1/jobs/{rows['own']['job'].id}/assessments", params={"cursor": cursor}).status_code == 400


def test_pages_cross_video_boundary_in_source_id_order(resources):
    session, client, _, rows = resources
    own = rows["own"]
    videos = [
        db.Video(
            id=uuid.UUID(int=(0xAB << 120) + i), project_id=own["project"].id, filename=f"{i}.mp4", minio_key=f"{i}.mp4"
        )
        for i in [2, 1]
    ]
    session.add_all(videos)
    session.flush()
    for video in videos:
        for index in [8, 0]:
            _row(session, own, index, video_id=video.id)
    own["job"].processing_summary = {
        "videos": [
            {"video_id": str(video.id), "status": "completed", "native_assessment_version": 1, "sampled_frames": 2}
            for video in videos
        ]
    }
    session.commit()
    url = f"/api/v1/jobs/{own['job'].id}/assessments"
    first = client.get(url, params={"limit": 3}).json()
    assert [(r["source_video_id"], r["source_frame_index"]) for r in first["items"]] == [
        (str(uuid.UUID(int=(0xAB << 120) + 1)), 0),
        (str(uuid.UUID(int=(0xAB << 120) + 1)), 8),
        (str(uuid.UUID(int=(0xAB << 120) + 2)), 0),
    ]
    last = client.get(url, params={"cursor": first["next_cursor"]}).json()
    assert [(r["source_video_id"], r["source_frame_index"]) for r in last["items"]] == [
        (str(uuid.UUID(int=(0xAB << 120) + 2)), 8)
    ]
    assert last["next_cursor"] is None
    assert client.get(url, params={"limit": 0}).status_code == 422
    assert client.get(url, params={"limit": 501}).status_code == 422
    assert client.get(url, params={"cursor": _cursor(rows["foreign"]["job"].id, videos[0].id, 0)}).status_code == 400
    assert client.get(url, params={"cursor": _cursor(own["job"].id, "not-a-uuid", 0)}).status_code == 400
    # A position in a hidden source grants no access to it.
    assert client.get(url, params={"cursor": _cursor(own["job"].id, rows["foreign"]["video"].id, 0)}).status_code == 200


def test_duplicate_and_merge_retain_original_run_assessments(resources):
    from lib.tasks import _merge_completed_labeling_job

    session, client, _, rows = resources
    own = rows["own"]
    original = _row(session, own, detection_count=1)
    _complete(own, 1)
    session.commit()
    url = f"/api/v1/jobs/{own['job'].id}/assessments"
    before = client.get(url).json()
    duplicate = client.post(f"/api/v1/jobs/{own['job'].id}/duplicate")
    assert duplicate.status_code == 200, duplicate.text
    copied_id = duplicate.json()["new_id"]
    copied = client.get(f"/api/v1/jobs/{copied_id}/assessments").json()
    assert copied["items"] == [] and copied["coverage_status"] == "unavailable"
    assert client.get(url).json() == before

    child = db.LabelingJob(
        project_id=own["project"].id,
        video_id=own["video"].id,
        parent_id=own["job"].id,
        status="completed",
        text_prompt="new class",
    )
    session.add(child)
    session.flush()
    child_own = {**own, "job": child}
    source = _row(session, child_own, detection_count=3)
    _complete(child_own, 1)
    session.add(
        db.Annotation(job_id=child.id, frame_id=own["frame"].id, class_name="new class", class_index=0, polygon=[])
    )
    session.commit()
    child_url = f"/api/v1/jobs/{child.id}/assessments"
    child_before = client.get(child_url).json()
    _merge_completed_labeling_job(session, child.id, own["job"].id)
    session.commit()
    combined = client.get(url).json()
    assert combined["coverage_status"] == "partial"
    assert [r["id"] for r in combined["items"]] == [str(original.id)]
    assert client.get(child_url).json() == child_before
    assert child_before["coverage_status"] == "available"
    assert [r["id"] for r in child_before["items"]] == [str(source.id)]
    assert session.query(db.Annotation).filter_by(job_id=child.id).count() == 0
    # A class deletion changes reviewed observations, never original inference.
    assert client.delete(f"/api/v1/jobs/{own['job'].id}/classes/new%20class").status_code == 200
    assert client.get(child_url).json() == child_before


@pytest.mark.parametrize("video_id", [None, "not-a-uuid", [], {}])
def test_insufficient_clip_identity_reports_partial(resources, video_id):
    session, client, _, rows = resources
    own = rows["own"]
    _row(session, own)
    _complete(own, 1)
    own["job"].processing_summary = {
        "videos": [
            *own["job"].processing_summary["videos"],
            {"video_id": video_id, "status": "completed", "sampled_frames": 0, "native_assessment_version": 1},
        ]
    }
    session.commit()
    response = client.get(f"/api/v1/jobs/{own['job'].id}/assessments")
    assert response.status_code == 200, response.text
    assert response.json()["coverage_status"] == "partial"


def test_owned_job_linked_to_foreign_video_is_hidden_before_cursor_decoding(resources):
    session, client, _, rows = resources
    own = rows["own"]
    own["job"].video_id = rows["foreign"]["video"].id
    session.commit()
    assert client.get(f"/api/v1/jobs/{own['job'].id}/assessments", params={"cursor": "!"}).status_code == 404
