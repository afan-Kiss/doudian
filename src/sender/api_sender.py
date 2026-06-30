from __future__ import annotations

import base64
import copy
import json
import time
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from src.sender.frame_context import find_im_frame
from src.sender.page_ws_encoder import PageWsEncoder
from src.sender.ws_frame_builder import WSFrameBuilder, read_varint


CONTENT_KEYS = ("content", "text", "message", "body")
CONVERSATION_KEYS = ("conversation_id", "conv_id", "session_id", "chat_id")
WS_URL_HINTS = (
    "ws.fxg.jinritemai.com",
    "frontier.snssdk.com",
    "fxg.jinritemai.com",
)

SEND_PAYLOADS_JS = """
async ({ payloadsB64, wsUrlHints }) => {
    const decodeBase64 = (value) => {
        const binary = atob(value);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
    };

    const readVarint = (bytes, pos) => {
        let result = 0;
        let shift = 0;
        let index = pos;
        while (index < bytes.length) {
            const byte = bytes[index++];
            result |= (byte & 0x7f) << shift;
            if (!(byte & 0x80)) {
                break;
            }
            shift += 7;
        }
        return [result, index];
    };

    const sockets = window.__feigeCapturedSockets || [];
    const openSockets = sockets.filter((ws) => ws.readyState === WebSocket.OPEN);
    if (!openSockets.length) {
        return { ok: false, reason: "no_open_socket" };
    }

    const pickSocket = () => {
        for (const hint of wsUrlHints) {
            const match = openSockets.find((ws) => (ws.url || "").includes(hint));
            if (match) {
                return match;
            }
        }
        return openSockets[0];
    };

    const target = pickSocket();
    const sent = [];
    for (const payloadB64 of payloadsB64) {
        const payload = decodeBase64(payloadB64);
        let seq = 0;
        if (payload[0] === 0x08) {
            [seq] = readVarint(payload, 1);
        }
        target.send(payload.buffer);
        sent.push({ length: payload.length, seq });

        if (window.__feigeWsState && seq > 0) {
            if (seq > window.__feigeWsState.lastSeq) {
                window.__feigeWsState.lastSeq = seq;
            }
            window.__feigeWsState.sendCount += 1;
            window.__feigeWsState.lastSize = payload.length;
        }
    }

    return {
        ok: true,
        url: target.url || "",
        sent,
        total: sent.length,
    };
}
"""


class APISender:
    """Send Feige messages via page SDK (signed) or rebuilt WS protobuf frames."""

    def __init__(self, schema_dir: Path) -> None:
        self.schema_dir = schema_dir
        self.template_path = schema_dir / "send_template.json"
        self.page_encoder = PageWsEncoder()
        self.last_send_mode: str | None = None
        self.last_send_detail: dict[str, Any] = {}

    def _load_schema(self) -> dict[str, Any]:
        if not self.template_path.exists():
            return {}
        return json.loads(self.template_path.read_text(encoding="utf-8"))

    def load_template(self) -> dict[str, Any] | None:
        data = self._load_schema()
        latest = data.get("latest")
        if latest and latest.get("is_message_send"):
            return latest

        templates = data.get("templates") or []
        return self._pick_best_template(templates)

    def load_followup_template(self) -> dict[str, Any] | None:
        templates = self._load_schema().get("templates") or []
        for item in reversed(templates):
            if item.get("transport") != "websocket":
                continue
            payload_length = item.get("payload_length", 0)
            if not (1520 <= payload_length <= 1560):
                continue
            payload_hex = item.get("payload_hex", "")
            if not payload_hex:
                continue
            try:
                raw = bytes.fromhex(payload_hex)
            except ValueError:
                continue
            if b":pigeon" in raw and b"s:client_message_id" not in raw:
                return item
        return None

    def _pick_best_template(self, templates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not templates:
            return None

        message_frames = [item for item in templates if item.get("is_message_send")]
        if message_frames:
            return message_frames[-1]

        for item in reversed(templates):
            if item.get("transport") != "websocket":
                continue
            payload_hex = item.get("payload_hex", "")
            if payload_hex:
                try:
                    raw = bytes.fromhex(payload_hex)
                    if b"s:client_message_id" in raw:
                        return item
                except ValueError:
                    pass

        ws_frames = [item for item in templates if item.get("transport") == "websocket"]
        if ws_frames:
            return max(ws_frames, key=lambda item: item.get("payload_length", 0))

        return templates[-1]

    def _sdk_send_verified(self, result: dict[str, Any] | None) -> bool:
        if not result or not result.get("ok"):
            return False
        send_delta = int(result.get("sendDelta") or 0)
        captured = int(result.get("capturedCount") or 0)
        payload_len = int(result.get("payloadLength") or 0)
        mode = str(result.get("mode") or "")
        # im.sendText can return without delivering; require a captured outbound WS frame.
        if captured > 0 and payload_len >= 2500:
            return True
        if mode == "mona_im_sendText" and send_delta > 0 and payload_len >= 2500:
            return True
        if mode not in {"mona_im_sendText", "pigeon_sendTextMessage_event"} and send_delta > 0:
            return True
        return False

    async def send(self, page: Page, text: str, conversation_id: str | None = None) -> bool:
        im_frame = await find_im_frame(page)
        sdk_result = await self.page_encoder.send_text(im_frame, text, conversation_id)
        if self._sdk_send_verified(sdk_result):
            self.last_send_mode = str(sdk_result.get("mode") or "sdk")
            self.last_send_detail = sdk_result
            return True

        self.last_send_detail = sdk_result or {"reason": "sdk_send_failed"}

        template = self.load_template()
        if not template:
            return False

        transport = template.get("transport")
        if transport == "http":
            return await self._send_http(page, template, text, conversation_id)
        if transport == "websocket":
            return await self._send_websocket(page, template, text, conversation_id)
        return False

    async def _send_http(
        self,
        page: Page,
        template: dict[str, Any],
        text: str,
        conversation_id: str | None,
    ) -> bool:
        url = template.get("url")
        if not url:
            return False

        payload = self._build_payload(template.get("payload_template"), text, conversation_id)
        headers = copy.deepcopy(template.get("headers") or {})
        headers.setdefault("content-type", "application/json")

        response = await page.request.post(
            url,
            data=json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload),
            headers=headers,
        )
        return response.ok

    async def _send_websocket(
        self,
        page: Page,
        template: dict[str, Any],
        text: str,
        conversation_id: str | None,
    ) -> bool:
        message_text = text or "你好"
        im_frame = await find_im_frame(page)

        sdk_result = await self.page_encoder.send_text(im_frame, message_text, conversation_id)
        if self._sdk_send_verified(sdk_result):
            self.last_send_mode = str(sdk_result.get("mode") or "sdk")
            self.last_send_detail = sdk_result
            return True

        self.last_send_detail = sdk_result or {}

        try:
            builder = WSFrameBuilder.from_template_dict(template)
        except ValueError:
            return False

        ws_state = await im_frame.evaluate(
            "() => window.__feigeWsState || { lastSeq: 0, sendCount: 0, lastSize: 0 }"
        )
        last_seq = int(ws_state.get("lastSeq") or 0)
        old_seq, _ = read_varint(builder.template, 1)
        new_seq = max(last_seq + 1, old_seq + 1)
        now_ms = int(time.time() * 1000)

        payloads: list[bytes] = [builder.build(message_text, seq=new_seq, timestamp_ms=now_ms)]

        followup = self.load_followup_template()
        if followup:
            try:
                sync_builder = WSFrameBuilder.from_template_dict(followup)
                payloads.append(sync_builder.build_sync_frame(seq=new_seq + 1, timestamp_ms=now_ms))
            except ValueError:
                pass

        payloads_b64 = [base64.b64encode(item).decode("ascii") for item in payloads]
        result = await im_frame.evaluate(
            SEND_PAYLOADS_JS,
            {"payloadsB64": payloads_b64, "wsUrlHints": list(WS_URL_HINTS)},
        )
        if result:
            self.last_send_detail = {
                **self.last_send_detail,
                "frame_count": result.get("total"),
                "sent": result.get("sent"),
                "warning": "signature_not_regenerated",
            }
        return bool(result and result.get("ok"))

    def ws_replay_diagnostics(self, template: dict[str, Any] | None = None) -> str:
        template = template or self.load_template()
        if not template:
            return "no template"
        if template.get("transport") != "websocket":
            return f"transport={template.get('transport')}"
        followup = self.load_followup_template()
        followup_len = followup.get("payload_length") if followup else None
        return (
            f"ws text template: {template.get('payload_length')}B, "
            f"is_message_send={template.get('is_message_send')}, "
            f"followup={followup_len}B"
        )

    def _build_payload(
        self,
        template_payload: Any,
        text: str,
        conversation_id: str | None,
    ) -> Any:
        if isinstance(template_payload, dict):
            payload = copy.deepcopy(template_payload)
            self._replace_content(payload, text)
            if conversation_id:
                self._replace_conversation(payload, conversation_id)
            return payload

        if isinstance(template_payload, str):
            try:
                parsed = json.loads(template_payload)
                return self._build_payload(parsed, text, conversation_id)
            except json.JSONDecodeError:
                return text

        return text

    def _replace_content(self, data: Any, text: str) -> None:
        if isinstance(data, dict):
            for key, value in list(data.items()):
                lower = key.lower()
                if lower in CONTENT_KEYS:
                    data[key] = text
                else:
                    self._replace_content(value, text)
        elif isinstance(data, list):
            for item in data:
                self._replace_content(item, text)

    def _replace_conversation(self, data: Any, conversation_id: str) -> None:
        if isinstance(data, dict):
            for key, value in list(data.items()):
                lower = key.lower()
                if lower in CONVERSATION_KEYS:
                    data[key] = conversation_id
                else:
                    self._replace_conversation(value, conversation_id)
        elif isinstance(data, list):
            for item in data:
                self._replace_conversation(item, conversation_id)
