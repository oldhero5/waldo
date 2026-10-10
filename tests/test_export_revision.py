"""An edit during export must not make an old snapshot current again."""

from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows


def test_annotation_edit_during_export_cannot_publish_stale_training_artifact(resources, monkeypatch):
    from app.api import review

    session, client, _, rows = resources
    own = rows["own"]
    own["job"].task_type = "detect"
    own["annotation"].bbox = [0.5, 0.5, 0.2, 0.2]
    session.commit()
    uploaded = []

    def edit_during_upload(key, path):
        uploaded.append(key)
        changed = client.patch(
            f"/api/v1/annotations/{own['annotation'].id}",
            json={"bbox": [0.5, 0.5, 0.3, 0.3]},
        )
        assert changed.status_code == 200, changed.text

    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    monkeypatch.setattr(review, "upload_file", edit_during_upload)

    response = client.post(f"/api/v1/jobs/{own['job'].id}/export", json={"format": "detect"})

    assert response.status_code == 409, response.text
    assert len(uploaded) == 1
    session.refresh(own["job"])
    assert own["job"].result_minio_key is None


def test_annotation_edit_invalidates_even_when_current_key_is_already_null(resources):
    session, client, _, rows = resources
    own = rows["own"]
    assert own["job"].result_minio_key is None
    original_revision = own["job"].evidence_revision

    response = client.patch(
        f"/api/v1/annotations/{own['annotation'].id}",
        json={"class_name": "reviewed-camera"},
    )

    assert response.status_code == 200, response.text
    session.refresh(own["job"])
    assert own["job"].evidence_revision == original_revision + 1
    assert own["job"].result_minio_key is None


def test_merge_refreshes_preloaded_job_under_row_lock(resources):
    from sqlalchemy.orm import Session

    from lib.db import LabelingJob
    from lib.tasks import _merge_completed_labeling_job

    session, _, _, rows = resources
    master = rows["own"]["job"]
    master.processing_summary = {"marker": "old"}
    child = LabelingJob(project_id=master.project_id, status="completed", task_type="detect")
    session.add(child)
    session.commit()
    child_id = child.id

    with Session(session.bind) as other:
        current = other.get(LabelingJob, master.id)
        current.processing_summary = {"marker": "fresh"}
        other.commit()

    assert master.processing_summary == {"marker": "old"}
    _merge_completed_labeling_job(session, child_id, master.id)
    session.commit()
    assert master.processing_summary["marker"] == "fresh"
