"""WebSocket endpoints for streaming training metrics and prediction results."""
import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lib.config import settings
from trainer.metrics_streamer import CHANNEL_PREFIX, get_latest_metrics

router = APIRouter()

PREDICT_CHANNEL_PREFIX = "waldo:predict:frames:"


@router.websocket("/ws/training/{run_id}")
async def training_metrics_ws(websocket: WebSocket, run_id: str):
    await websocket.accept()

    # Send latest cached metrics immediately
    latest = get_latest_metrics(run_id)
    if latest:
        await websocket.send_json(latest)

    # Subscribe to Redis channel for real-time updates
    client = aioredis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    channel = f"{CHANNEL_PREFIX}{run_id}"

    try:
        await pubsub.subscribe(channel)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)
                if data.get("status") in ("completed", "failed"):
                    break

            # Check if client disconnected
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
            except TimeoutError:
                pass  # No message from client, continue
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await client.close()


@router.websocket("/ws/predict/{session_id}")
async def predict_ws(websocket: WebSocket, session_id: str):
    """Stream per-frame prediction results from a video inference task."""
    await websocket.accept()

    client = aioredis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    channel = f"{PREDICT_CHANNEL_PREFIX}{session_id}"

    try:
        await pubsub.subscribe(channel)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)
                if data.get("status") in ("completed", "failed"):
                    break

            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await client.close()
