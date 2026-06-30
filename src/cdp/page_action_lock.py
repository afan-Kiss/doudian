from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

_lock = asyncio.Lock()
_busy_reason = ""


def is_page_busy() -> bool:
    return _lock.locked()


def busy_reason() -> str:
    return _busy_reason


@asynccontextmanager
async def page_action_lock(reason: str = "") -> AsyncIterator[None]:
    global _busy_reason
    async with _lock:
        _busy_reason = reason
        try:
            yield
        finally:
            _busy_reason = ""
