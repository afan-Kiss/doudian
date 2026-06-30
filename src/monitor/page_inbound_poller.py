from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from src.chat.hub import ChatHub
from src.monitor.inbound_listener import InboundListener

_HOOK_PATH = Path(__file__).resolve().parent / "page_inbound_hook.js"

_CONVERSATION_DIRECTORY_JS = """
() => {
  const rows = [];
  const push = (id, nickname, talkId = "") => {
    const convId = String(id || "").trim();
    const name = String(nickname || "").trim();
    if (!convId || !name) {
      return;
    }
    rows.push({
      id: convId,
      conversation_route: convId,
      conversation_id: String(talkId || "").trim(),
      nickname: name,
      name,
    });
  };

  const visitList = (list) => {
    if (!Array.isArray(list)) {
      return;
    }
    for (const item of list) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const id =
        item.id ||
        item.conversationId ||
        item.securityConversationId ||
        item.securityBizConversationId ||
        "";
      const name =
        item.name ||
        item.nickname ||
        item.userName ||
        item.buyerName ||
        item.title ||
        "";
      const talkId = item.talkId || item.talk_id || "";
      push(id, name, talkId);
    }
  };

  try {
    const ctx = window.__monaGlobalStore?.getData?.("initContextData");
    ctx?.doAction?.((store) => {
      const info = store?.conversationsInfo || {};
      visitList(info.conversationList);
      visitList(info.list);
      visitList(info.conversations);
      visitList(info.data?.list);
      visitList(info.data?.conversationList);
    });
  } catch (error) {
    // ignore
  }

  return rows;
}
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PageInboundPoller:
    """Poll page-level hooks (fetch/XHR/WS/DOM) for buyer messages CDP may miss."""

    def __init__(
        self,
        page: Page,
        inbound_listener: InboundListener,
        hub: ChatHub | None = None,
        *,
        interval_sec: float = 1.0,
    ) -> None:
        self.page = page
        self.inbound_listener = inbound_listener
        self.hub = hub
        self.interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._directory_ticks = 0

    async def rebind_page(self, page: Page) -> None:
        self.page = page
        self._directory_ticks = 0
        await self._ensure_hook_installed()
        if self.hub:
            await self._sync_conversation_directory()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        await self._ensure_hook_installed()
        self._task = asyncio.create_task(self._run())

    async def _ensure_hook_installed(self) -> None:
        hook_js = _HOOK_PATH.read_text(encoding="utf-8")
        for frame in self.page.frames:
            try:
                await frame.evaluate(hook_js)
            except Exception:
                continue

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                continue

    async def _poll_once(self) -> None:
        if self.hub:
            self._directory_ticks += 1
            if self._directory_ticks % 2 == 1:
                await self._sync_conversation_directory()

        items: list[dict[str, Any]] = []
        for frame in self.page.frames:
            try:
                drained = await frame.evaluate("() => window.__feigeDrainInboundQueue?.() || []")
                if drained:
                    items.extend(drained)
            except Exception:
                pass
            try:
                dom_items = await frame.evaluate("() => window.__feigeScanDomInbound?.() || []")
                if dom_items:
                    items.extend(dom_items)
            except Exception:
                pass

        if not items:
            return

        raw_event = {"ts": utc_now_iso(), "source": "page_hook", "type": "page_inbound"}
        for item in items:
            role = str(item.get("role") or "")
            if role != "buyer":
                continue
            parsed = {
                "kind": item.get("kind") or "buyer_message",
                "role": "buyer",
                "text": item.get("text") or "",
                "nickname": item.get("nickname") or "",
                "conversation_id": item.get("conversation_id") or "",
                "conversation_route": item.get("conversation_route") or "",
                "server_message_id": item.get("server_message_id") or "",
                "url": item.get("url") or "",
                "source": item.get("source") or "page_hook",
            }
            if self.hub:
                resolved = self.hub.resolve_nickname(
                    nickname=str(parsed.get("nickname") or ""),
                    conversation_route=str(parsed.get("conversation_route") or ""),
                    conversation_id=str(parsed.get("conversation_id") or ""),
                )
                if resolved:
                    parsed["nickname"] = resolved
            self.inbound_listener.handle_parsed_event(parsed, raw_event)

    async def _sync_conversation_directory(self) -> None:
        if not self.hub:
            return
        entries: list[dict[str, Any]] = []
        for frame in self.page.frames:
            try:
                rows = await frame.evaluate(_CONVERSATION_DIRECTORY_JS)
                if rows:
                    entries.extend(rows)
            except Exception:
                continue
        if entries:
            self.hub.sync_conversation_directory(entries)
