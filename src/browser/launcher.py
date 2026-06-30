from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from src.browser.chrome_cdp import cdp_endpoint, ensure_chrome_cdp, feige_url, is_cdp_ready
from src.config import load_config

_HOOK_DIR = Path(__file__).resolve().parent.parent / "monitor"


class BrowserLauncher:
    """Connect to local Chrome via CDP (port 9222). Never downloads Playwright Chromium."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._connect_only = True
        self._cdp_port = int(self.config.get("browser", {}).get("debug_port") or 9222)

    @property
    def user_data_dir(self) -> str:
        return str(self.config["_user_data_dir"])

    @property
    def debug_port(self) -> int:
        return self._cdp_port

    @property
    def doudian_url(self) -> str:
        return self.config["urls"]["doudian"]

    @property
    def feige_url(self) -> str:
        return feige_url(self.config)

    async def _inject_scripts(self, context: BrowserContext) -> None:
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            if (!window.__feigeHookInstalled) {
                window.__feigeCapturedSockets = [];
                window.__feigeWsState = { lastSeq: 0, sendCount: 0, lastSize: 0, recvCount: 0, lastRecvSize: 0 };
                window.__feigeHookInstalled = true;
            }
            """
        )
        page_inbound_hook = (_HOOK_DIR / "page_inbound_hook.js").read_text(encoding="utf-8")
        await context.add_init_script(page_inbound_hook)

    async def _inject_existing_pages(self, context: BrowserContext) -> None:
        page_inbound_hook = (_HOOK_DIR / "page_inbound_hook.js").read_text(encoding="utf-8")
        for page in context.pages:
            try:
                await page.evaluate(page_inbound_hook)
            except Exception:
                continue

    def _score_feige_page(self, page: Page) -> int:
        url = page.url or ""
        if "127.0.0.1" in url or "localhost" in url or ":8799" in url:
            return -1000
        score = 0
        if "im.jinritemai.com" in url:
            score += 100
        elif "jinritemai.com" in url:
            score += 50
        if "/pc_seller_v2/main" in url or "/main/workspace" in url:
            score += 30
        elif "workspace" in url or "/main" in url:
            score += 20
        return score

    def _collect_open_pages(self) -> list[Page]:
        pages: list[Page] = []
        if not self._browser:
            return pages
        for ctx in self._browser.contexts:
            for page in ctx.pages:
                if page.is_closed():
                    continue
                try:
                    _ = page.url
                    pages.append(page)
                except Exception:
                    continue
        return pages

    def _pick_best_page(self, pages: list[Page]) -> Page | None:
        if not pages:
            return None
        ranked = sorted(pages, key=self._score_feige_page, reverse=True)
        return ranked[0]

    async def _ensure_browser(self) -> Browser:
        self._cdp_port = int(self.config.get("browser", {}).get("debug_port") or 9222)
        endpoint = cdp_endpoint(self._cdp_port)
        if not is_cdp_ready(self._cdp_port):
            ensure_chrome_cdp(self.config)

        if self._browser:
            try:
                _ = self._browser.contexts
                return self._browser
            except Exception:
                self._browser = None

        if not self._playwright:
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        return self._browser

    async def get_active_feige_page(self) -> Page:
        browser = await self._ensure_browser()
        pages = self._collect_open_pages()
        picked = self._pick_best_page(pages)
        if picked:
            self.page = picked
            self.context = picked.context
            return picked

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        await self._inject_scripts(context)
        self.context = context
        self.page = await context.new_page()
        await self.page.goto(self.feige_url, wait_until="domcontentloaded")
        await self._inject_existing_pages(context)
        return self.page

    async def rebind_feige_page(self) -> Page:
        self.page = None
        self.context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        page = await self.get_active_feige_page()
        if page.context:
            await self._inject_existing_pages(page.context)
        return page

    async def reload_feige_page(self) -> Page:
        page = await self.get_active_feige_page()
        await page.reload(wait_until="domcontentloaded")
        if page.context:
            await self._inject_existing_pages(page.context)
        self.page = page
        self.context = page.context
        return page

    async def start(self, open_url: str | None = None) -> Page:
        page = await self.get_active_feige_page()
        target = open_url or self.feige_url
        if "jinritemai.com" not in (page.url or ""):
            await page.goto(target, wait_until="domcontentloaded")
        return page

    async def active_page(self) -> Page:
        return await self.get_active_feige_page()

    async def stop(self) -> None:
        self.page = None
        self.context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
