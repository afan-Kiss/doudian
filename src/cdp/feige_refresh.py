from __future__ import annotations

import logging
import time
from typing import Any

from src.cdp.conversation_list import _scan_conversation_list_impl
from src.cdp.page_action_lock import is_page_busy, page_action_lock
from src.sender.frame_context import find_im_frame

logger = logging.getLogger("feige_refresh")

_WAIT_READY_JS = """
async () => {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const url = String(location.href || "");
    const urlOk = /im\\.jinritemai\\.com/i.test(url) && (/workspace|pc_seller|main/i.test(url));
    let hasMona = false;
    let hasList = false;
    try {
      const ctx = window.__monaGlobalStore?.getData?.("initContextData");
      ctx?.doAction?.((store) => {
        hasMona = Boolean(store);
        const msgMap = store?.conversationsInfo?.messagesByConversationId;
        hasList = Boolean(msgMap && typeof msgMap.size === "number" && msgMap.size > 0);
      });
    } catch (e) {}
    if (!hasList) {
      hasList = Boolean(
        document.querySelector(
          '[class*="conversation-list"], [class*="session-list"], [class*="chat-list"], [class*="conv-list"]'
        )
      );
    }
    if (urlOk && hasMona && hasList) {
      return { ok: true, url, hasMona, hasList };
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  return { ok: false, url: String(location.href || "") };
}
"""

_CHECK_INPUT_JS = """
() => {
  const selectors = [
    'textarea[class*="inputArea"]',
    'textarea[placeholder*="Enter"]',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"]',
    'textarea',
  ];
  for (const sel of selectors) {
    const nodes = document.querySelectorAll(sel);
    if (!nodes.length) continue;
    const node = nodes[nodes.length - 1];
    const text = node.tagName === 'TEXTAREA'
      ? String(node.value || '').trim()
      : String(node.innerText || node.textContent || '').trim();
    if (text) return { has_draft: true, text: text.slice(0, 80) };
  }
  return { has_draft: false, text: '' };
}
"""


async def wait_feige_ready(page: Any, timeout_ms: int = 15000) -> dict[str, Any]:
    im = await find_im_frame(page)
    try:
        result = await im.evaluate(_WAIT_READY_JS)
        return result if isinstance(result, dict) else {"ok": False}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def check_input_draft(page: Any) -> bool:
    im = await find_im_frame(page)
    try:
        row = await im.evaluate(_CHECK_INPUT_JS)
        return bool(isinstance(row, dict) and row.get("has_draft"))
    except Exception:
        return False


async def reload_and_wait(page: Any, launcher: Any) -> dict[str, Any]:
    started = time.monotonic()
    await page.reload(wait_until="domcontentloaded")
    if page.context:
        await launcher._inject_existing_pages(page.context)
    ready = await wait_feige_ready(page)
    if not ready.get("ok"):
        return {
            "ok": False,
            "error": "feige_not_ready",
            "message": "刷新后飞鸽工作台未就绪",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "ready": ready,
        }
    return {
        "ok": True,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "ready": ready,
        "page_url": str(getattr(page, "url", "") or ""),
    }


async def refresh_feige_and_rescan(
    page: Any,
    launcher: Any,
    *,
    name_cache: dict[str, str] | None = None,
    check_idle: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    if check_idle and is_page_busy():
        return {
            "ok": False,
            "success": False,
            "skipped": True,
            "reason": "page_busy",
            "message": "飞鸽页面正在处理会话，暂不刷新",
        }
    async with page_action_lock("refresh_feige"):
        reload_result = await reload_and_wait(page, launcher)
        if not reload_result.get("ok"):
            return {
                "ok": False,
                "success": False,
                **reload_result,
            }

        scan = await _scan_conversation_list_impl(page, name_cache=name_cache)
        conversations = scan.get("conversations") or []
        pending_hint = sum(
            1
            for c in conversations
            if c.get("has_unreplied_customer_message") and not c.get("closed")
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "success": True,
            "duration_ms": duration_ms,
            "conversation_count_after_refresh": len(conversations),
            "pending_count_after_refresh": pending_hint,
            "scan": scan,
            "page_url": reload_result.get("page_url", ""),
            "PAGE_REBOUND": True,
        }
