"""Durable ephemeral inference results; pubsub is only a notification channel."""

import json
import logging

RESULT_TTL_SECONDS = 86400


def get_inference_result(kind: str, session_id: str) -> dict | None:
    from lib.redis_client import get_redis

    raw = get_redis().get(f"waldo:inference:{kind}:result:{session_id}")
    return json.loads(raw) if raw else None


def save_inference_result(kind: str, session_id: str, result: dict) -> None:
    from lib.redis_client import get_redis

    client = get_redis()
    client.setex(f"waldo:inference:{kind}:result:{session_id}", RESULT_TTL_SECONDS, json.dumps(result))
    if result.get("status") not in {"completed", "failed"}:
        return
    # A notification failure must not undo an already durable result.
    try:
        terminal = {key: result[key] for key in ("status", "session_id", "error", "total_frames") if key in result}
        if kind == "predict":
            from lib.redis_serde import pack

            client.publish(f"waldo:predict:frames:{session_id}", pack(terminal))
        elif kind == "compare":
            client.publish(f"waldo:compare:{session_id}", json.dumps(terminal))
    except Exception:
        logging.getLogger(__name__).warning("Inference result saved but notification failed", exc_info=True)
