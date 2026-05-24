#!/usr/bin/env python3
"""Benchmark JSON vs msgpack serialization for the predict-frame hot path.

Issue #12: per-frame video predict fires at 30+ fps and `json.dumps` shows up
in profiles. This script times serializing and deserializing a representative
predict-frame payload (sampled from the actual `predict_video_task` shape) to
verify the ≥20% reduction acceptance criterion.

Usage:
    uv run python scripts/bench_redis_serde.py

Output: timings and percent reduction for serialize-only and round-trip.
"""

from __future__ import annotations

import json
import time

from lib.redis_serde import pack, unpack

# Representative payload — mirrors the dict built in
# lib.tasks.predict_video_task#on_frame for a typical 1080p frame with a
# handful of detections (small floats, short class names, optional masks).
SAMPLE_PAYLOAD = {
    "session_id": "9c1f4f7a-3a2c-4f1d-8e7c-5b2c3d4e5f60",
    "frame_index": 142,
    "timestamp_s": 4.733333,
    "detections": [
        {
            "class_name": "person",
            "class_index": 0,
            "confidence": 0.8723,
            "bbox": [120.5, 240.0, 480.25, 720.5],
            "track_id": 3,
            "mask": None,
        },
        {
            "class_name": "car",
            "class_index": 2,
            "confidence": 0.6541,
            "bbox": [800.0, 300.0, 1200.5, 600.5],
            "track_id": 7,
            "mask": None,
        },
        {
            "class_name": "person",
            "class_index": 0,
            "confidence": 0.4298,
            "bbox": [50.0, 100.0, 150.5, 350.0],
            "track_id": 11,
            "mask": None,
        },
        {
            "class_name": "bicycle",
            "class_index": 1,
            "confidence": 0.7812,
            "bbox": [600.0, 400.0, 750.5, 580.5],
            "track_id": 4,
            "mask": None,
        },
        {
            "class_name": "dog",
            "class_index": 16,
            "confidence": 0.5532,
            "bbox": [300.0, 500.0, 420.0, 680.0],
            "track_id": 9,
            "mask": None,
        },
    ],
    "status": "processing",
}

ITERATIONS = 10_000


def bench_json_serialize() -> float:
    payload = SAMPLE_PAYLOAD
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        json.dumps(payload)
    return time.perf_counter() - t0


def bench_msgpack_serialize() -> float:
    payload = SAMPLE_PAYLOAD
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        pack(payload)
    return time.perf_counter() - t0


def bench_json_roundtrip() -> float:
    payload = SAMPLE_PAYLOAD
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        json.loads(json.dumps(payload))
    return time.perf_counter() - t0


def bench_msgpack_roundtrip() -> float:
    payload = SAMPLE_PAYLOAD
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        unpack(pack(payload))
    return time.perf_counter() - t0


def report(label: str, json_t: float, mp_t: float) -> None:
    reduction = (json_t - mp_t) / json_t * 100
    print(f"{label}:")
    print(f"  json    : {json_t * 1000:8.2f} ms  ({json_t / ITERATIONS * 1e6:6.2f} us/op)")
    print(f"  msgpack : {mp_t * 1000:8.2f} ms  ({mp_t / ITERATIONS * 1e6:6.2f} us/op)")
    print(f"  reduction: {reduction:+.1f}%")
    print()


def main() -> None:
    print(f"Benchmark: {ITERATIONS:,} iterations of predict-frame payload")
    print(f"Payload: {len(SAMPLE_PAYLOAD['detections'])} detections")
    print()

    # Warm caches
    bench_json_serialize()
    bench_msgpack_serialize()

    # Best of 3 — reduces variance from CPU jitter / GC pauses
    json_ser = min(bench_json_serialize() for _ in range(3))
    mp_ser = min(bench_msgpack_serialize() for _ in range(3))
    report("Serialize only (publisher hot path)", json_ser, mp_ser)

    json_rt = min(bench_json_roundtrip() for _ in range(3))
    mp_rt = min(bench_msgpack_roundtrip() for _ in range(3))
    report("Round-trip (publish + WS unpack)", json_rt, mp_rt)


if __name__ == "__main__":
    main()
