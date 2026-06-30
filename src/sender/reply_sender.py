from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("send_reply")
SendMode = Literal["manual", "semi_auto", "auto"]

_page: Any | None = None


def set_page(page: Any | None) -> None:
    global _page
    _page = page


async def _fill_input(page: Any, text: str) -> bool:
    from src.sender.dom_sender import DOMSender

    sender = DOMSender()
    return await sender.fill_only(page, text)


async def _send_via_sdk(page: Any, text: str, conversation_id: str | None) -> dict[str, Any]:
    from src.config import load_config
    from src.sender.api_sender import APISender

    config = load_config()
    schema_dir = config["_capture_dir"] / "schema"
    sender = APISender(schema_dir)
    success = await sender.send(page, text, conversation_id or None)
    detail = dict(sender.last_send_detail or {})
    mode = str(sender.last_send_mode or detail.get("mode") or "")
    return {
        "ok": success,
        "send_mode": mode,
        "detail": detail,
    }


async def send_reply_async(
    *,
    conversation_id: str,
    customer_hash: str,
    text: str,
    mode: SendMode,
    contact_name: str | None = None,
    page: Any | None = None,
) -> dict[str, Any]:
    text = text.strip()
    page = page or _page
    result: dict[str, Any] = {
        "ok": True,
        "sent": False,
        "filled": False,
        "mode": mode,
        "conversation_id": conversation_id,
        "customer_hash": customer_hash,
    }

    logger.info(
        "send_reply mode=%s conversation=%s customer=%s len=%d",
        mode,
        conversation_id[:32] if conversation_id else "",
        customer_hash[:16] if customer_hash else "",
        len(text),
    )

    if mode == "manual" or not text:
        return result

    if page is None:
        result["ok"] = False
        result["error"] = "CDP page not connected"
        logger.warning("send_reply failed: no page")
        return result

    try:
        if mode == "semi_auto":
            filled = await _fill_input(page, text)
            result["filled"] = filled
            if not filled:
                result["ok"] = False
                result["error"] = "fill input failed"
            return result

        sdk = await _send_via_sdk(page, text, conversation_id or None)
        detail = sdk.get("detail") or {}
        send_mode = str(sdk.get("send_mode") or detail.get("mode") or "")

        if sdk.get("ok"):
            result["sent"] = True
            result["filled"] = True
            result["message_id"] = str(detail.get("messageId") or send_mode or "sent")
            result["send_mode"] = send_mode
            return result

        reason = str(detail.get("reason") or send_mode or "send failed")
        if send_mode == "ws_replay_unverified" or reason == "ws_replay_unverified":
            reason = "ws_replay_unverified"

        result["ok"] = False
        result["sent"] = False
        result["filled"] = False
        result["error"] = reason
        result["send_mode"] = send_mode
        logger.warning("auto send failed conversation=%s reason=%s", conversation_id[:32], reason)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_reply error")
        result["ok"] = False
        result["sent"] = False
        result["filled"] = False
        result["error"] = str(exc)
        return result
