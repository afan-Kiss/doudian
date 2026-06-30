from __future__ import annotations

from typing import Any

from src.monitor.cdp_client import CDPMonitor


class WebSocketCapture:
    """Filter and summarize WebSocket events from CDP monitor."""

    WS_TYPES = {"ws_created", "ws_frame_received", "ws_frame_sent"}

    def __init__(self, monitor: CDPMonitor) -> None:
        self.monitor = monitor

    def frames(self) -> list[dict[str, Any]]:
        return [e for e in self.monitor.events if e.get("type") in self.WS_TYPES]

    def sent_frames(self) -> list[dict[str, Any]]:
        return [e for e in self.frames() if e.get("type") == "ws_frame_sent"]

    def received_frames(self) -> list[dict[str, Any]]:
        return [e for e in self.frames() if e.get("type") == "ws_frame_received"]

    def endpoints(self) -> list[str]:
        urls = []
        for event in self.frames():
            url = event.get("url")
            if url and url not in urls:
                urls.append(url)
        return urls
