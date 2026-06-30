from __future__ import annotations



from pathlib import Path

from typing import Any



from src.cdp.live_dom_probe import ensure_sidebar_visible
from src.cdp.page_action_lock import page_action_lock

from src.sender.frame_context import find_im_frame



_CDP_DIR = Path(__file__).parent

_BUYER_EXTRACT_JS = (_CDP_DIR / "buyer_name_extract.js").read_text(encoding="utf-8")
_MSG_UTILS_JS = (_CDP_DIR / "feige_message_utils.js").read_text(encoding="utf-8")

_SCAN_JS = (
    _BUYER_EXTRACT_JS
    + "\n"
    + _MSG_UTILS_JS
    + "\n"
    + (_CDP_DIR / "conversation_list_scan.js").read_text(encoding="utf-8")
)

_SWITCH_JS = _MSG_UTILS_JS + "\n" + (_CDP_DIR / "conversation_switch.js").read_text(encoding="utf-8")

_VERIFY_JS = (
    _MSG_UTILS_JS
    + """
(payload) => {
  const utils = window.__feigeMessageUtils;
  const target = String(payload?.customerName || "").trim();
  const header = utils?.pickHeaderTitle?.() || "";
  const ok = utils?.namesRoughMatch?.(header, target) || header === target;
  const inputOk = Boolean(
    document.querySelector('textarea[class*="inputArea"], [contenteditable="true"], textarea')
  );
  return {
    ok: ok && inputOk,
    verified: ok && inputOk,
    header_name: header,
    input_available: inputOk,
    current_customer_name: header,
    reason: ok && inputOk ? "ok" : "conversation_mismatch",
  };
}
"""
)





async def _scan_conversation_list_impl(page: Any, name_cache: dict[str, str] | None = None) -> dict[str, Any]:
    im = await find_im_frame(page)
    pre = await im.evaluate(
        _MSG_UTILS_JS
        + "\n"
        + "async () => { const rows = window.__feigeMessageUtils?.scanSessionRowsDom?.() || []; return { count: rows.length }; }"
    )
    if isinstance(pre, dict) and int(pre.get("count") or 0) == 0:
        await ensure_sidebar_visible(page)
        await page.wait_for_timeout(700)

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
    last_text: str = "",
    dom_row_index: int = -1,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    im = await find_im_frame(page)
    result = await im.evaluate(
        _SWITCH_JS,
        {
            "conversationId": cid,
            "customerName": customer_name,
            "lastText": last_text,
            "rowIndex": dom_row_index,
        },
    )
    if not isinstance(result, dict):
        return {"ok": False, "reason": "bad-result", "verified": False}

    if not result.get("ok") and customer_name.strip():
        from src.sender.feige_navigator import FeigeNavigator

        navigator = FeigeNavigator()
        clicked = await navigator.open_chat_by_name(page, customer_name.strip(), timeout_ms=8000)
        if clicked:
            await page.wait_for_timeout(1200)
            verify = await im.evaluate(
                _VERIFY_JS,
                {"customerName": customer_name.strip()},
            )
            if isinstance(verify, dict) and verify.get("ok"):
                return {
                    **verify,
                    "conversation_id": cid,
                    "method": f"{result.get('method', 'js')}+playwright",
                    "playwright_click": True,
                }
        result["playwright_click"] = clicked

    return result





async def switch_conversation(
    page: Any,
    conversation_id: str,
    *,
    customer_name: str = "",
    last_text: str = "",
    dom_row_index: int = -1,
) -> dict[str, Any]:
    async with page_action_lock("switch_conversation"):
        return await _switch_conversation_impl(
            page,
            conversation_id,
            customer_name=customer_name,
            last_text=last_text,
            dom_row_index=dom_row_index,
        )


