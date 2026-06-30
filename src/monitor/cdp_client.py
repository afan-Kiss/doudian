from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, CDPSession, Page

from src.monitor.inbound_listener import InboundListener
from src.monitor.http_inbound_parser import parse_http_inbound_messages


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_matches(url: str, hosts: list[str]) -> bool:
    return any(host in url for host in hosts)


class CDPMonitor:
    """Attach CDP Network listeners to all pages in a browser context."""

    def __init__(
        self,
        context: BrowserContext,
        capture_dir: Path,
        filter_hosts: list[str],
        save_raw: bool = True,
        console_log: bool = True,
        inbound_listener: InboundListener | None = None,
        on_inbound_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.context = context
        self.capture_dir = capture_dir
        self.filter_hosts = filter_hosts
        self.save_raw = save_raw
        self.console_log = console_log
        self.inbound_listener = inbound_listener
        self.on_inbound_message = on_inbound_message

        if self.inbound_listener is None and self.on_inbound_message is not None:
            self.inbound_listener = InboundListener(
                console_log=False,
                on_message=self.on_inbound_message,
            )
        self._sessions: dict[int, CDPSession] = {}
        self._ws_urls: dict[str, str] = {}
        self._request_meta: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._page_handler = None

    async def start(self) -> None:
        raw_dir = self.capture_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        self._page_handler = lambda page: asyncio.create_task(self._attach_page(page))
        self.context.on("page", self._page_handler)

        for page in self.context.pages:
            await self._attach_page(page)

    async def stop(self) -> None:
        if self._page_handler:
            self.context.remove_listener("page", self._page_handler)
            self._page_handler = None

        for session in list(self._sessions.values()):
            try:
                await session.detach()
            except Exception:
                pass
        self._sessions.clear()

    async def _attach_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._sessions:
            return

        cdp = await self.context.new_cdp_session(page)
        self._sessions[page_id] = cdp
        await cdp.send("Network.enable")

        cdp.on("Network.webSocketCreated", self._on_ws_created)
        cdp.on("Network.webSocketFrameReceived", self._on_ws_received)
        cdp.on("Network.webSocketFrameSent", self._on_ws_sent)
        cdp.on("Network.requestWillBeSent", self._on_request_will_be_sent)
        cdp.on("Network.responseReceived", self._on_response_received)
        cdp.on("Network.loadingFinished", lambda params: asyncio.create_task(self._on_loading_finished(cdp, params)))

        page_url = page.url or "(new tab)"
        self._log(f"[CDP] attached: {page_url}")

    def _decode_payload(self, payload_data: str, opcode: int) -> dict[str, Any]:
        if opcode == 2:
            raw = base64.b64decode(payload_data)
            return {
                "format": "binary",
                "payload": payload_data,
                "payload_hex": raw.hex(),
                "payload_length": len(raw),
            }

        return {
            "format": "text",
            "payload": payload_data,
            "payload_length": len(payload_data),
        }

    async def _persist_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        if not self.save_raw:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ts}_{event['type']}.json"
        path = self.capture_dir / "raw" / filename
        path.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, message: str) -> None:
        if self.console_log:
            print(message, flush=True)

    async def _on_ws_created(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId", "")
        url = params.get("url", "")
        self._ws_urls[request_id] = url
        if host_matches(url, self.filter_hosts):
            event = {
                "ts": utc_now_iso(),
                "type": "ws_created",
                "request_id": request_id,
                "url": url,
            }
            await self._persist_event(event)
            self._log(f"[WS OPEN] {url}")

    async def _handle_ws_frame(
        self,
        params: dict[str, Any],
        direction: str,
        event_type: str,
    ) -> None:
        request_id = params.get("requestId", "")
        url = self._ws_urls.get(request_id, "")
        if url and not host_matches(url, self.filter_hosts):
            return

        response = params.get("response", {})
        opcode = response.get("opcode", 1)
        decoded = self._decode_payload(response.get("payloadData", ""), opcode)

        event = {
            "ts": utc_now_iso(),
            "type": event_type,
            "direction": direction,
            "request_id": request_id,
            "url": url,
            "opcode": opcode,
            **decoded,
        }
        await self._persist_event(event)

        if direction == "in" and self.inbound_listener:
            self.inbound_listener.handle_ws_event(event)

        if decoded["format"] == "binary":
            preview = decoded["payload_hex"][:120]
            self._log(f"[WS {direction.upper()}] binary({decoded['payload_length']}B) {preview}")
        else:
            preview = decoded["payload"][:120].replace("\n", " ")
            self._log(f"[WS {direction.upper()}] {preview}")

    async def _on_ws_received(self, params: dict[str, Any]) -> None:
        await self._handle_ws_frame(params, "in", "ws_frame_received")

    async def _on_ws_sent(self, params: dict[str, Any]) -> None:
        await self._handle_ws_frame(params, "out", "ws_frame_sent")

    async def _on_request_will_be_sent(self, params: dict[str, Any]) -> None:
        request = params.get("request", {})
        url = request.get("url", "")
        if not host_matches(url, self.filter_hosts):
            return

        request_id = params.get("requestId", "")
        post_data = request.get("postData")
        self._request_meta[request_id] = {
            "url": url,
            "method": request.get("method", "GET"),
            "headers": request.get("headers", {}),
            "post_data": post_data,
            "ts": utc_now_iso(),
        }

        if self.inbound_listener and post_data and "msg_body_list" in post_data:
            event = {
                "ts": utc_now_iso(),
                "type": "http_body",
                "request_id": request_id,
                "url": url,
                "method": request.get("method", "GET"),
                "headers": request.get("headers", {}),
                "post_data": post_data,
                "response_body": "",
            }
            for parsed in parse_http_inbound_messages(event):
                self.inbound_listener.handle_parsed_event(parsed, event)

    async def _on_response_received(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId", "")
        meta = self._request_meta.get(request_id)
        if not meta:
            return

        response = params.get("response", {})
        event = {
            "ts": utc_now_iso(),
            "type": "http_response",
            "request_id": request_id,
            "url": meta["url"],
            "method": meta["method"],
            "headers": meta["headers"],
            "post_data": meta["post_data"],
            "status": response.get("status"),
            "response_headers": response.get("headers", {}),
            "mime_type": response.get("mimeType"),
        }
        await self._persist_event(event)
        self._log(f"[HTTP {meta['method']}] {response.get('status')} {meta['url']}")

    async def _on_loading_finished(self, cdp: CDPSession, params: dict[str, Any]) -> None:
        request_id = params.get("requestId", "")
        meta = self._request_meta.get(request_id)
        if not meta:
            return

        try:
            body_result = await cdp.send(
                "Network.getResponseBody",
                {"requestId": request_id},
            )
            body = body_result.get("body", "")
            if body_result.get("base64Encoded"):
                body = base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception:
            body = None

        if body:
            event = {
                "ts": utc_now_iso(),
                "type": "http_body",
                "request_id": request_id,
                "url": meta["url"],
                "method": meta["method"],
                "headers": meta["headers"],
                "post_data": meta["post_data"],
                "response_body": body,
            }
            await self._persist_event(event)
            if self.inbound_listener:
                for parsed in parse_http_inbound_messages(event):
                    self.inbound_listener.handle_parsed_event(parsed, event)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
