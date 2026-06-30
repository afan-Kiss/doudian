from __future__ import annotations

from pathlib import Path
from typing import Any

from src.cdp.page_action_lock import page_action_lock
from src.sender.frame_context import find_im_frame

_CDP_DIR = Path(__file__).parent
_MSG_UTILS_JS = (_CDP_DIR / "feige_message_utils.js").read_text(encoding="utf-8")
_PROBE_JS = _MSG_UTILS_JS + "\n" + (_CDP_DIR / "live_dom_probe.js").read_text(encoding="utf-8")


_EXPAND_JS = (
    _MSG_UTILS_JS
    + "\n"
    + (_CDP_DIR / "feige_sidebar_expand.js").read_text(encoding="utf-8")
)


async def ensure_sidebar_visible(page: Any) -> dict[str, Any]:
    im = await find_im_frame(page)
    try:
        result = await im.evaluate(_EXPAND_JS)
        if isinstance(result, dict):
            return result
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}
    return {"ok": False, "reason": "bad-result"}


async def run_live_dom_probe(page: Any) -> dict[str, Any]:
    im = await find_im_frame(page)
    result = await im.evaluate(_PROBE_JS)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "bad-result"}
    rows = result.get("visible_session_rows") or []
    if result.get("ok") and len(rows) == 0:
        expand = await ensure_sidebar_visible(page)
        result["sidebar_expand"] = expand
        await page.wait_for_timeout(800)
        retry = await im.evaluate(_PROBE_JS)
        if isinstance(retry, dict):
            result = {**retry, "sidebar_expand": expand, "probe_retried": True}
    return result


async def live_dom_probe(page: Any) -> dict[str, Any]:
    async with page_action_lock("live_dom_probe"):
        return await run_live_dom_probe(page)
