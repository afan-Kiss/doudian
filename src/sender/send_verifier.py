from __future__ import annotations

from playwright.async_api import Frame, Page


class SendVerifier:
    """Verify that a send actually triggered WebSocket traffic and server ACK."""

    async def ws_send_stats(self, page: Page | Frame) -> dict:
        return await page.evaluate(
            """
            () => ({
                count: (window.__feigeWsState && window.__feigeWsState.sendCount) || 0,
                lastSize: (window.__feigeWsState && window.__feigeWsState.lastSize) || 0,
                lastSeq: (window.__feigeWsState && window.__feigeWsState.lastSeq) || 0,
                recvCount: (window.__feigeWsState && window.__feigeWsState.recvCount) || 0,
                lastRecvSize: (window.__feigeWsState && window.__feigeWsState.lastRecvSize) || 0,
                open: (window.__feigeCapturedSockets || [])
                    .filter((ws) => ws.readyState === WebSocket.OPEN).length,
            })
            """
        )

    async def wait_for_new_ws_send(
        self,
        page: Page | Frame,
        before_count: int,
        timeout_ms: int = 15000,
        min_size: int = 500,
    ) -> dict | None:
        elapsed = 0
        step = 300
        while elapsed < timeout_ms:
            stats = await self.ws_send_stats(page)
            if stats["count"] > before_count and stats["lastSize"] >= min_size:
                return stats
            await page.wait_for_timeout(step)
            elapsed += step
        return None

    async def wait_for_server_ack(
        self,
        page: Page | Frame,
        before_recv_count: int,
        timeout_ms: int = 15000,
        min_size: int = 2500,
    ) -> dict | None:
        elapsed = 0
        step = 300
        while elapsed < timeout_ms:
            stats = await self.ws_send_stats(page)
            if stats["recvCount"] > before_recv_count and stats["lastRecvSize"] >= min_size:
                return stats
            await page.wait_for_timeout(step)
            elapsed += step
        return None

    async def message_visible(self, page: Page, text: str) -> bool:
        for frame in page.frames:
            try:
                locator = frame.get_by_text(text, exact=False).last
                if await locator.count() > 0:
                    return await locator.is_visible()
            except Exception:
                continue
        return False
