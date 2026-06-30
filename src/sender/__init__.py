from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api_sender import APISender
    from .dom_sender import DOMSender

__all__ = ["APISender", "DOMSender"]


def __getattr__(name: str):
    if name == "APISender":
        from .api_sender import APISender

        return APISender
    if name == "DOMSender":
        from .dom_sender import DOMSender

        return DOMSender
    raise AttributeError(name)
