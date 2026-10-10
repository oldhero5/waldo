"""Review pagination follows clip time and retains only known timing provenance."""

import uuid

from lib.db import Annotation, Frame, Video
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows


def test_review_orders_before_pagination_and_returns_source_pts(resources):
    session, client, _, rows = resources
    own = rows["own"]
    own["frame"].timestamp_s = 2
    own["frame"].frame_number = 20
    early = Frame(video_id=own["video"].id, timestamp_s=0, frame_number=0, minio_key="early.jpg")
    middle = Frame(video_id=own["video"].id, timestamp_s=1, frame_number=10, minio_key="middle.jpg")
    session.add_all([middle, early])
    session.flush()
    annotations = [
        Annotation(
            id=uuid.UUID(int=(0xAB << 120) + i),
            frame_id=frame.id,
            job_id=own["job"].id,
            class_name="car",
            class_index=0,
            polygon=[],
        )
        for i, frame in [(2, middle), (1, middle), (3, early)]
    ]
    session.add_all(annotations)
    own["job"].processing_summary = {
        "videos": [
            {
                "video_id": str(own["video"].id),
                "timestamp_method": "source_pts",
                "assessed_timestamps_s": [0, 1, 2],
                "assessed_source_frame_indices": [0, 10, 20],
            }
        ]
    }
    session.commit()
    url = f"/api/v1/jobs/{own['job'].id}/annotations"
    result = client.get(url).json()
    assert [row["timestamp_s"] for row in result] == [0, 1, 1, 2]
    assert [row["id"] for row in result[1:3]] == [
        str(uuid.UUID(int=(0xAB << 120) + 1)),
        str(uuid.UUID(int=(0xAB << 120) + 2)),
    ]
    assert {row["timestamp_method"] for row in result} == {"source_pts"}
    assert client.get(url, params={"offset": 1, "limit": 2}).json() == result[1:3]
    updated = client.patch(f"/api/v1/annotations/{annotations[0].id}", json={"status": "accepted"})
    assert updated.status_code == 200
    assert updated.json()["timestamp_method"] == "source_pts"
    overview = client.get(f"/api/v1/jobs/{own['job'].id}/overview")
    assert overview.status_code == 200
    assert [frame["frame_number"] for frame in overview.json()["sample_frames"]] == [0, 10, 20]
    assert [frame["annotation_count"] for frame in overview.json()["sample_frames"]] == [1, 2, 1]


def test_review_does_not_infer_exact_timing_from_other_clip_or_global_summary(resources):
    session, client, _, rows = resources
    own = rows["own"]
    other = Video(project_id=own["project"].id, filename="other.mp4", minio_key="other.mp4")
    session.add(other)
    session.flush()
    frame = Frame(video_id=other.id, timestamp_s=0.5, frame_number=1, minio_key="other.jpg")
    session.add(frame)
    session.flush()
    session.add(Annotation(frame_id=frame.id, job_id=own["job"].id, class_name="car", class_index=0, polygon=[]))
    own["job"].processing_summary = {
        "timestamp_method": "source_pts",
        "videos": [{"video_id": str(other.id), "timestamp_method": "resampled_ordinal/fps"}],
    }
    session.commit()
    result = client.get(f"/api/v1/jobs/{own['job'].id}/annotations").json()
    assert {row["source_video_id"]: row["timestamp_method"] for row in result} == {
        str(other.id): "resampled_ordinal/fps",
        str(own["video"].id): "unknown",
    }


def test_review_timing_abstains_for_unassessed_frame_and_conflicting_merged_run(resources):
    session, client, _, rows = resources
    own = rows["own"]
    clip = {"video_id": str(own["video"].id), "timestamp_method": "source_pts", "assessed_timestamps_s": [5]}
    own["job"].processing_summary = {"videos": [clip]}
    session.commit()
    url = f"/api/v1/jobs/{own['job'].id}/annotations"
    assert client.get(url).json()[0]["timestamp_method"] == "unknown"
    own["job"].processing_summary = {
        "videos": [{**clip, "assessed_timestamps_s": [0]}],
        "merged_runs": [
            {
                "processing_summary": {
                    "videos": [{**clip, "assessed_timestamps_s": [0], "timestamp_method": "frame_index/fps"}]
                }
            }
        ],
    }
    session.commit()
    assert client.get(url).json()[0]["timestamp_method"] == "unknown"
