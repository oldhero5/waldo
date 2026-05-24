"""msgpack helpers for Redis pubsub hot paths.

JSON shows up in profiles for the predict-frame stream (30+ fps per video) and
the training-metrics latest-cache. msgpack is ~2-3x faster for these payloads,
which mix small floats, short strings, and short nested lists.

Wire-format note: WS clients still receive JSON. The publisher pubsub channels
that feed `app/ws.py` use msgpack on the Redis side; the WS layer unpacks and
re-serializes JSON to clients. Channels NOT consumed by WS (the
`waldo:training:latest:{run_id}` cache, late-joiner snapshot) are pure
Python-to-Python and stay msgpack end-to-end.
"""

from __future__ import annotations

from typing import Any

import msgpack


def pack(obj: Any) -> bytes:
    """Serialize a Python object to msgpack bytes.

    `use_bin_type=True` is the modern default — strings encode as msgpack `str`
    and bytes as `bin`, matching JSON's type expectations on the round-trip.
    """
    return msgpack.packb(obj, use_bin_type=True)


def unpack(data: bytes) -> Any:
    """Deserialize msgpack bytes back to a Python object.

    `raw=False` decodes msgpack `str` to Python `str` (utf-8). `strict_map_key`
    is off because dict keys in our payloads are always strings, but msgpack
    uses the stricter default to guard against hash collisions in untrusted
    inputs — we control both ends here.
    """
    return msgpack.unpackb(data, raw=False, strict_map_key=False)
