"""WebSocket endpoints for streaming training metrics and prediction results.

Both endpoints share a single module-level Redis connection pool so long-lived
WS sessions don't each open a fresh TCP connection. PubSub objects are still
per-session (required by the protocol) but get cleaned up via try/finally
regardless of whether the client disconnected mid-handshake.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lib.config import settings
from lib.redis_serde import unpack as msgpack_unpack
from trainer.metrics_streamer import CHANNEL_PREFIX, get_latest_metrics

logger = logging.getLogger(__name__)

router = APIRouter()

PREDICT_CHANNEL_PREFIX = "waldo:predict:frames:"

# Shared Redis pool — one per process, not per WS connection.
_redis_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=50,
            decode_responses=False,
        )
    return _redis_pool


def _decode_message(data: bytes | str) -> dict | None:
    """Decode a Redis pubsub payload to a Python dict.

    The internal hot-path channel (predict-frame stream) publishes msgpack;
    other channels (training metrics) still publish JSON. msgpack binary
    frames never start with `{` or `[`, so a cheap byte sniff picks the
    right decoder.

    Falls back to the other format on decode failure as cheap insurance
    during rolling deploys when publisher and consumer may briefly disagree.
    """
    if not data:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8")
    looks_json = data[:1] in (b"{", b"[")
    primary = json.loads if looks_json else msgpack_unpack
    secondary = msgpack_unpack if looks_json else json.loads
    try:
        return primary(data)
    except Exception:
        try:
            return secondary(data)
        except Exception:
            return None


async def _stream_channel(websocket: WebSocket, channel: str) -> None:
    """Forward all Redis pubsub messages for `channel` to the websocket until
    the client disconnects or a terminal {status: completed|failed} arrives.
    """
    client = aioredis.Redis(connection_pool=_get_pool())
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                data = _decode_message(message["data"])
                if data is None:
                    logger.warning("ws: dropped malformed message on %s", channel)
                    continue
                await websocket.send_json(data)
                if data.get("status") in ("completed", "failed"):
                    break

            # Best-effort disconnect check — don't block the pubsub loop
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception as e:
            logger.debug("ws: unsubscribe %s failed: %s", channel, e)
        try:
            await pubsub.aclose()
        except Exception as e:
            logger.debug("ws: pubsub close %s failed: %s", channel, e)
        try:
            await client.aclose()
        except Exception as e:
            logger.debug("ws: client close %s failed: %s", channel, e)


@router.websocket("/ws/training/{run_id}")
async def training_metrics_ws(websocket: WebSocket, run_id: str):
    await websocket.accept()

    latest = get_latest_metrics(run_id)
    if latest:
        await websocket.send_json(latest)

    await _stream_channel(websocket, f"{CHANNEL_PREFIX}{run_id}")


@router.websocket("/ws/predict/{session_id}")
async def predict_ws(websocket: WebSocket, session_id: str):
    """Stream per-frame prediction results from a video inference task."""
    await websocket.accept()
    await _stream_channel(websocket, f"{PREDICT_CHANNEL_PREFIX}{session_id}")
