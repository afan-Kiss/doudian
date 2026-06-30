from __future__ import annotations

import asyncio
import re
from typing import Any

from playwright.async_api import Frame, Page, TimeoutError as PlaywrightTimeoutError

from src.chat.conversation_keys import conversation_ids_match
from src.sender.frame_context import find_im_frame
from src.sender.page_ws_encoder import PageWsEncoder


_SWITCH_CONVERSATION_JS = """
({ conversationId, nickname }) => {
    const ctx = window.__monaGlobalStore?.getData?.('initContextData');
    if (!ctx?.doAction) {
        return { ok: false, reason: 'no_mona_store' };
    }
    const norm = (id) => {
        const value = String(id || '').trim();
        if (value.startsWith('n') && value.length > 24 && value[1] === value[1].toUpperCase()) {
            return value.slice(1);
        }
        return value;
    };
    const targetNorm = norm(conversationId);
    const targetNick = String(nickname || '').trim();
    let picked = null;
    ctx.doAction((store) => {
        const info = store?.conversationsInfo || {};
        const lists = [
            info.conversationList,
            info.list,
            info.conversations,
            info.data?.list,
            info.data?.conversationList,
        ];
        const visit = (item) => {
            if (!item || typeof item !== 'object') {
                return false;
            }
            const id = String(item.id || item.conversationId || '').trim();
            const name = String(item.name || item.nickname || '').trim();
            if (targetNorm && id && (id === conversationId || norm(id) === targetNorm)) {
                picked = item;
                info.currentConversation = item;
                return true;
            }
            if (targetNick && name === targetNick) {
                picked = item;
                info.currentConversation = item;
                return true;
            }
            return false;
        };
        for (const list of lists) {
            if (!Array.isArray(list)) {
                continue;
            }
            for (const item of list) {
                if (visit(item)) {
                    return;
                }
            }
        }
        const convMap = info.conversationMap;
        if (convMap && typeof convMap.get === 'function' && targetNorm) {
            for (const key of convMap.keys()) {
                const item = convMap.get(key);
                if (visit(item)) {
                    return;
                }
            }
        }
    });
    return {
        ok: Boolean(picked),
        conversationId: picked?.id || null,
        nickname: picked?.name || picked?.nickname || null,
    };
}
"""


class FeigeNavigator:
    """Navigate Feige UI: open conversations by buyer nickname."""

    CHAT_ITEM_SELECTORS = [
        '[class*="conversation"]',
        '[class*="session"]',
        '[class*="chat-item"]',
        '[class*="contact"]',
        '[data-testid*="conversation"]',
        'li[role="listitem"]',
    ]

    SEARCH_SELECTORS = [
        'input[placeholder*="搜索"]',
        'input[placeholder*="联系人"]',
        'input[placeholder*="昵称"]',
        'input[type="search"]',
    ]

    async def read_current_conversation_id(self, page: Page) -> str:
        im_frame = await find_im_frame(page)
        probe = await PageWsEncoder().probe(im_frame)
        return str((probe.get("env") or {}).get("conversationId") or "").strip()

    async def switch_conversation_in_store(
        self,
        page: Page,
        *,
        conversation_id: str = "",
        nickname: str = "",
    ) -> bool:
        conv_id = str(conversation_id or "").strip()
        nick = str(nickname or "").strip()
        if not conv_id and not nick:
            return False
        for frame in page.frames:
            try:
                result = await frame.evaluate(
                    _SWITCH_CONVERSATION_JS,
                    {"conversationId": conv_id, "nickname": nick},
                )
            except Exception:
                continue
            if result and result.get("ok"):
                await page.wait_for_timeout(600)
                return True
        return False

    async def open_chat_for_target(
        self,
        page: Page,
        *,
        nickname: str,
        conversation_id: str = "",
        timeout_ms: int = 20000,
    ) -> bool:
        nick = str(nickname or "").strip()
        conv_id = str(conversation_id or "").strip()
        if not nick and not conv_id:
            return False

        if conv_id or nick:
            await self.switch_conversation_in_store(page, conversation_id=conv_id, nickname=nick)
            current = await self.read_current_conversation_id(page)
            if conv_id and conversation_ids_match(conv_id, current):
                return True

        if nick and await self.open_chat_by_name(page, nick, timeout_ms=timeout_ms):
            current = await self.read_current_conversation_id(page)
            if conv_id and conversation_ids_match(conv_id, current):
                return True

        if conv_id or nick:
            await self.switch_conversation_in_store(page, conversation_id=conv_id, nickname=nick)
            current = await self.read_current_conversation_id(page)
            if conv_id and conversation_ids_match(conv_id, current):
                return True

        current = await self.read_current_conversation_id(page)
        if conv_id and conversation_ids_match(conv_id, current):
            return True
        if conv_id:
            return False

        if nick:
            im_frame = await find_im_frame(page)
            probe = await PageWsEncoder().probe(im_frame)
            current_name = str((probe.get("env") or {}).get("conversationName") or "").strip()
            return current_name == nick

        return False

    async def open_chat_by_name(self, page: Page, name: str, timeout_ms: int = 60000) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000

        while asyncio.get_running_loop().time() < deadline:
            if await self._try_open_chat(page, name):
                return True
            await page.wait_for_timeout(1000)

        return await self._try_open_chat(page, name)

    async def wait_for_ws_ready(self, page: Page, timeout_ms: int = 30000) -> dict[str, Any]:
        elapsed = 0
        step = 500
        while elapsed < timeout_ms:
            status = await page.evaluate(
                """
                () => {
                    const hints = ['ws.fxg.jinritemai.com', 'frontier.snssdk.com'];
                    const sockets = window.__feigeCapturedSockets || [];
                    const open = sockets.filter((ws) => ws.readyState === WebSocket.OPEN);
                    const im = open.find((ws) => hints.some((h) => (ws.url || '').includes(h)));
                    return {
                        ok: Boolean(im),
                        total: sockets.length,
                        open: open.length,
                        url: im ? im.url : (open[0] ? open[0].url : ''),
                    };
                }
                """
            )
            if status.get("ok"):
                return status
            await page.wait_for_timeout(step)
            elapsed += step

        return {"ok": False, "total": 0, "open": 0, "url": ""}

    async def _try_open_chat(self, page: Page, name: str) -> bool:
        if await self._click_name_in_frames(page, name):
            await page.wait_for_timeout(1500)
            return True

        if await self._search_and_open(page, name):
            await page.wait_for_timeout(1500)
            return True

        return False

    async def _search_and_open(self, page: Page, name: str) -> bool:
        for frame in page.frames:
            for selector in self.SEARCH_SELECTORS:
                search = frame.locator(selector).first
                if await search.count() == 0:
                    continue
                try:
                    await search.click(timeout=2000)
                    await search.fill(name, timeout=2000)
                    await page.wait_for_timeout(800)
                    if await self._click_name_in_frame(frame, name):
                        return True
                except PlaywrightTimeoutError:
                    continue
        return False

    async def _click_name_in_frames(self, page: Page, name: str) -> bool:
        for frame in page.frames:
            if await self._click_name_in_frame(frame, name):
                return True
        return False

    async def _click_name_in_frame(self, frame: Frame, name: str) -> bool:
        try:
            exact = frame.get_by_text(name, exact=True)
            if await exact.count() > 0:
                await exact.first.click(timeout=3000)
                return True

            partial = frame.get_by_text(name, exact=False)
            if await partial.count() > 0:
                await partial.first.click(timeout=3000)
                return True
        except PlaywrightTimeoutError:
            pass

        pattern = re.compile(re.escape(name))
        for selector in self.CHAT_ITEM_SELECTORS:
            items = frame.locator(selector)
            count = await items.count()
            for index in range(min(count, 30)):
                item = items.nth(index)
                try:
                    text = await item.inner_text(timeout=500)
                except PlaywrightTimeoutError:
                    continue
                if name in text:
                    await item.click(timeout=3000)
                    return True

        try:
            await frame.locator(f'text=/{re.escape(name)}/').first.click(timeout=2000)
            return True
        except PlaywrightTimeoutError:
            return False
