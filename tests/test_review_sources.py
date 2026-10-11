"""Saved sources must follow job evidence and never disclose unrelated capabilities."""

import uuid

import pytest

from app.api import review
from lib.db import Annotation, Frame, LabelingJob, Video
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows


def add_source(session, own, filename, key, *, job=None):
    video = Video(project_id=own["project"].id, filename=filename, minio_key=key, duration_s=2)
    session.add(video)
    session.flush()
    frame = Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key="frame.jpg")
    session.add(frame)
    session.flush()
    session.add(
        Annotation(frame_id=frame.id, job_id=(job or own["job"]).id, class_name="car", class_index=0, polygon=[])
    )
    session.commit()
    return video


def test_sources_deduplicate_sort_and_do_not_sign_urls(resources, monkeypatch):
    session, client, _, rows = resources
    own = rows["own"]
    add_source(session, own, "z.mp4", "z.mp4")
    add_source(session, own, "a.mp4", "a.mp4")
    session.add(Annotation(frame_id=own["frame"].id, job_id=own["job"].id, class_name="car", class_index=0, polygon=[]))
    session.commit()

    def unexpected_signing(key):
        pytest.fail("Metadata listing must not sign source URLs")

    monkeypatch.setattr(review, "get_download_url", unexpected_signing)
    response = client.get(f"/api/v1/jobs/{own['job'].id}/sources")
    assert response.status_code == 200
    result = response.json()
    assert [source["filename"] for source in result] == ["a.mp4", "own.mp4", "z.mp4"]
    assert set(result[0]) == {"id", "filename", "duration_s", "has_source_video"}
    assert result[0]["duration_s"] == 2
    assert all(source["has_source_video"] for source in result)


def test_copied_job_resolves_saved_sources_and_signs_fresh_capability(resources, monkeypatch):
    session, client, _, rows = resources
    own = rows["own"]
    copied = LabelingJob(project_id=own["project"].id, video_id=None, status="completed")
    session.add(copied)
    session.flush()
    session.add(Annotation(frame_id=own["frame"].id, job_id=copied.id, class_name="car", class_index=0, polygon=[]))
    session.commit()
    urls = iter(["/signed/first", "/signed/second"])
    monkeypatch.setattr(
        review, "get_download_url", lambda key: next(urls) if key == "videos/own.mp4" else pytest.fail(key)
    )
    path = f"/api/v1/jobs/{copied.id}/sources"
    assert client.get(path).json()[0]["id"] == str(own["video"].id)
    for expected in ["/signed/first", "/signed/second"]:
        response = client.get(f"{path}/{own['video'].id}/playback")
        assert response.status_code == 200
        assert response.json() == {"video_id": str(own["video"].id), "url": expected}


@pytest.mark.parametrize("filename,key", [("__standalone_images__", "placeholder"), ("missing.mp4", "")])
def test_unplayable_sources_keep_frame_evidence_but_have_no_capability(resources, filename, key):
    session, client, _, rows = resources
    own = rows["own"]
    video = add_source(session, own, filename, key)
    path = f"/api/v1/jobs/{own['job'].id}/sources"
    response = client.get(path)
    assert response.status_code == 200
    assert next(source for source in response.json() if source["id"] == str(video.id))["has_source_video"] is False
    assert client.get(f"{path}/{video.id}/playback").status_code == 404
    assert len(client.get(f"/api/v1/jobs/{own['job'].id}/annotations").json()) == 2


@pytest.mark.parametrize("prefix", ["foreign", "legacy"])
def test_source_routes_deny_foreign_jobs_and_sources(resources, prefix):
    session, client, _, rows = resources
    own, foreign = rows["own"], rows[prefix]
    # Even malformed evidence linking a foreign frame cannot authorize it.
    session.add(
        Annotation(frame_id=foreign["frame"].id, job_id=own["job"].id, class_name="bad", class_index=0, polygon=[])
    )
    session.commit()
    assert client.get(f"/api/v1/jobs/{foreign['job'].id}/sources").status_code == 404
    assert client.get(f"/api/v1/jobs/{foreign['job'].id}/sources/{foreign['video'].id}/playback").status_code == 404
    assert client.get(f"/api/v1/jobs/{own['job'].id}/sources/{foreign['video'].id}/playback").status_code == 404
    response = client.get(f"/api/v1/jobs/{own['job'].id}/sources")
    assert response.status_code == 200
    assert [source["id"] for source in response.json()] == [str(own["video"].id)]


def test_source_routes_deny_unrelated_same_workspace_video(resources):
    session, client, _, rows = resources
    own = rows["own"]
    unrelated = Video(project_id=own["project"].id, filename="unrelated.mp4", minio_key="other.mp4")
    session.add(unrelated)
    session.commit()
    assert client.get(f"/api/v1/jobs/{own['job'].id}/sources/{unrelated.id}/playback").status_code == 404
    assert client.get(f"/api/v1/jobs/{own['job'].id}/sources/{uuid.uuid4()}/playback").status_code == 404


def test_source_routes_require_authentication(resources):
    _, client, _, rows = resources
    own = rows["own"]
    client.headers.pop("Authorization")
    assert client.get(f"/api/v1/jobs/{own['job'].id}/sources").status_code == 401
    assert client.get(f"/api/v1/jobs/{own['job'].id}/sources/{own['video'].id}/playback").status_code == 401
