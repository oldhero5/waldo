"""Public Playground result shape shared by workers and direct API replies."""


def normalize_preview_result(result: dict) -> dict:
    frames = []
    for frame in result.get("frames", []):
        frames.append(
            {
                "frame_idx": frame.get("frame_idx", frame.get("frame_index")),
                "image_b64": frame["image_b64"],
                "timestamp_s": frame["timestamp_s"],
                "width": frame["width"],
                "height": frame["height"],
                "source_width": frame.get("source_width", frame["width"]),
                "source_height": frame.get("source_height", frame["height"]),
                "frame_duration_s": frame.get("frame_duration_s"),
                "timestamp_method": frame.get("timestamp_method") or "unknown",
                "detections": [
                    {
                        "bbox": detection["bbox"],
                        "score": detection["score"],
                        "label": detection["label"],
                        "polygon": detection.get("polygon"),
                        "track_id": detection.get("track_id"),
                    }
                    for detection in frame.get("detections", [])
                ],
            }
        )
    return {
        "frames": frames,
        "total_detections": result.get("total_detections", 0),
        "unique_track_count": result.get("unique_track_count", 0),
        "fps": result.get("fps", 0.0),
        "video_duration_s": result.get("video_duration_s", 0.0),
        "mode": result.get("mode", "sample"),
    }
