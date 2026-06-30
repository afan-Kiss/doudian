from __future__ import annotations

from typing import Any

from src.monitor.cdp_client import CDPMonitor


class HTTPCapture:
    """Filter HTTP events from CDP monitor."""

    HTTP_TYPES = {"http_response", "http_body"}

    def __init__(self, monitor: CDPMonitor) -> None:
        self.monitor = monitor

    def responses(self) -> list[dict[str, Any]]:
        return [e for e in self.monitor.events if e.get("type") == "http_response"]

    def bodies(self) -> list[dict[str, Any]]:
        return [e for e in self.monitor.events if e.get("type") == "http_body"]

    def send_candidates(self) -> list[dict[str, Any]]:
        keywords = ("send", "message", "reply", "im")
        candidates = []
        for event in self.bodies():
            url = (event.get("url") or "").lower()
            method = (event.get("method") or "").upper()
            if method != "POST":
                continue
            if any(keyword in url for keyword in keywords):
                candidates.append(event)
        return candidates
