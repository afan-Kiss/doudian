from __future__ import annotations

from pathlib import Path
from typing import Any

from src.cdp.page_action_lock import page_action_lock
from src.sender.frame_context import find_im_frame

_CDP_DIR = Path(__file__).parent
_BUYER_EXTRACT_JS = (_CDP_DIR / "buyer_name_extract.js").read_text(encoding="utf-8")
_SCAN_JS = _BUYER_EXTRACT_JS + "\n" + (_CDP_DIR / "conversation_list_scan.js").read_text(encoding="utf-8")
_SWITCH_JS = (_CDP_DIR / "conversation_switch.js").read_text(encoding="utf-8")


async def _scan_conversation_list_impl(page: Any, name_cache: dict[str, str] | None = None) -> dict[str, Any]:
    im = await find_im_frame(page)
    payload = {"nameCache": name_cache or {}}
    result = await im.evaluate(_SCAN_JS, payload)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "bad-result", "conversations": []}
    return result


async def scan_conversation_list(page: Any, name_cache: dict[str, str] | None = None) -> dict[str, Any]:
    async with page_action_lock("scan_conversation_list"):
        return await _scan_conversation_list_impl(page, name_cache=name_cache)


async def _switch_conversation_impl(
    page: Any,
    conversation_id: str,
    *,
    customer_name: str = "",
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    im = await find_im_frame(page)
    result = await im.evaluate(
        _SWITCH_JS,
        {"conversationId": cid, "customerName": customer_name},
    )
    if not isinstance(result, dict):
        return {"ok": False, "reason": "bad-result", "verified": False}
    return result


async def switch_conversation(
    page: Any,
    conversation_id: str,
    *,
    customer_name: str = "",
) -> dict[str, Any]:
    async with page_action_lock("switch_conversation"):
        return await _switch_conversation_impl(
            page,
            conversation_id,
            customer_name=customer_name,
        )
