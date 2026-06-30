from __future__ import annotations

from playwright.async_api import Frame, Page


async def find_im_frame(page: Page) -> Page | Frame:
    """Return the frame that owns the Feige IM WebSocket, or the main page."""
    best: Page | Frame | None = None
    best_open = 0

    for frame in page.frames:
        try:
            status = await frame.evaluate(
                """
                () => {
                    const hints = ['ws.fxg.jinritemai.com', 'frontier.snssdk.com'];
                    const sockets = window.__feigeCapturedSockets || [];
                    const open = sockets.filter((ws) => ws.readyState === WebSocket.OPEN);
                    const im = open.filter((ws) => hints.some((h) => (ws.url || '').includes(h)));
                    return {
                        imOpen: im.length,
                        totalOpen: open.length,
                        hasHook: Boolean(window.__feigeHookInstalled),
                        webpack: Boolean(window.__feigeWebpackRequire),
                    };
                }
                """
            )
        except Exception:
            continue

        im_open = int(status.get("imOpen") or 0)
        if im_open > best_open:
            best_open = im_open
            best = frame
        elif best is None and status.get("hasHook"):
            best = frame

    return best or page
