from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from playwright.async_api import Page

logger = logging.getLogger("page_session")

STALE_MARKERS = (
    "target closed",
    "target page, context or browser has been closed",
    "execution context was destroyed",
    "frame was detached",
    "cannot find context",
    "page closed",
    "stale page reference",
    "browser has been closed",
    "context was destroyed",
)

T = TypeVar("T")


def is_stale_page_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in STALE_MARKERS)


class PageSession:
    """Fresh Feige page binding with automatic retry on stale references."""

    def __init__(self, launcher: Any) -> None:
        self.launcher = launcher
        self.page_rebound = False

    async def get_active_feige_page(self) -> Page:
        self.page_rebound = False
        return await self.launcher.get_active_feige_page()

    async def rebind_feige_page_if_needed(self) -> Page:
        logger.info("rebind Feige page requested")
        page = await self.launcher.rebind_feige_page()
        self.page_rebound = True
        return page

    async def refresh_feige_page(self) -> Page:
        logger.info("refresh Feige page requested")
        try:
            page = await self.launcher.reload_feige_page()
        except Exception as exc:
            if is_stale_page_error(exc):
                page = await self.rebind_feige_page_if_needed()
            else:
                raise
        self.page_rebound = True
        return page

    async def with_page_retry(
        self,
        fn: Callable[[Page], Awaitable[T]],
    ) -> tuple[T, bool]:
        rebounded = False
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                page = await self.get_active_feige_page()
                result = await fn(page)
                return result, rebounded or self.page_rebound
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and is_stale_page_error(exc):
                    logger.warning("stale page error, rebinding: %s", exc)
                    await self.rebind_feige_page_if_needed()
                    rebounded = True
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("page retry exhausted")
