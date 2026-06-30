from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


KNOWN_FIELDS = (
    "msg_id",
    "message_id",
    "conversation_id",
    "conv_id",
    "sender_id",
    "from_uid",
    "to_uid",
    "content",
    "text",
    "msg_type",
    "type",
    "timestamp",
    "create_time",
)


class MessageParser:
    """Parse captured payloads and extract likely message fields."""

    def __init__(self, capture_dir: Path) -> None:
        self.capture_dir = capture_dir
        self.raw_dir = capture_dir / "raw"

    def load_raw_events(self, latest: int | None = None) -> list[dict[str, Any]]:
        if not self.raw_dir.exists():
            return []

        files = sorted(self.raw_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if latest:
            files = files[-latest:]

        events: list[dict[str, Any]] = []
        for path in files:
            try:
                events.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return events

    def try_parse_payload(self, payload: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "raw": payload,
            "format": "text",
            "parsed": None,
            "fields": {},
        }

        if not payload:
            return result

        stripped = payload.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                result["format"] = "json"
                result["parsed"] = parsed
                result["fields"] = self.extract_fields(parsed)
                return result
            except json.JSONDecodeError:
                pass

        if re.fullmatch(r"[0-9a-fA-F\s]+", stripped) and len(stripped) >= 8:
            result["format"] = "hex"
            result["parsed"] = stripped.replace(" ", "")
            return result

        return result

    def extract_fields(self, data: Any, prefix: str = "") -> dict[str, Any]:
        fields: dict[str, Any] = {}

        if isinstance(data, dict):
            for key, value in data.items():
                full_key = f"{prefix}.{key}" if prefix else key
                lower_key = key.lower()
                if lower_key in KNOWN_FIELDS or any(k in lower_key for k in ("msg", "conv", "content", "text")):
                    fields[full_key] = value
                fields.update(self.extract_fields(value, full_key))
        elif isinstance(data, list):
            for index, item in enumerate(data[:5]):
                fields.update(self.extract_fields(item, f"{prefix}[{index}]"))

        return fields

    def classify_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        message_types: dict[str, list[dict[str, Any]]] = {}
        ws_endpoints: list[str] = []
        send_templates: list[dict[str, Any]] = []
        parsed_messages: list[dict[str, Any]] = []

        for event in events:
            event_type = event.get("type", "")

            if event_type == "ws_created":
                url = event.get("url")
                if url and url not in ws_endpoints:
                    ws_endpoints.append(url)
                continue

            if event_type in {"ws_frame_received", "ws_frame_sent"}:
                payload = event.get("payload", "")
                parsed = self.try_parse_payload(payload)
                msg_type = self._guess_msg_type(parsed)
                entry = {
                    "ts": event.get("ts"),
                    "direction": event.get("direction"),
                    "url": event.get("url"),
                    "msg_type": msg_type,
                    "fields": parsed.get("fields", {}),
                    "parsed": parsed.get("parsed"),
                    "raw": payload,
                }
                parsed_messages.append(entry)
                message_types.setdefault(msg_type, []).append(entry)

                if event_type == "ws_frame_sent":
                    payload_hex = event.get("payload_hex", "")
                    payload_length = event.get("payload_length", len(payload))
                    raw = b""
                    if payload_hex:
                        try:
                            raw = bytes.fromhex(payload_hex)
                        except ValueError:
                            raw = b""
                    is_message_send = (
                        b"s:client_message_id" in raw
                        and b"type\x12\x04text" in raw
                        and payload_length >= 2500
                    )
                    send_templates.append(
                        {
                            "transport": "websocket",
                            "url": event.get("url"),
                            "payload_template": event.get("payload") or payload,
                            "payload_hex": payload_hex,
                            "payload_length": payload_length,
                            "format": event.get("format", "text"),
                            "is_message_send": is_message_send,
                            "fields": parsed.get("fields", {}),
                        }
                    )
                continue

            if event_type == "http_body" and (event.get("method") or "").upper() == "POST":
                body = event.get("post_data") or event.get("response_body") or ""
                parsed = self.try_parse_payload(body)
                if parsed.get("fields"):
                    send_templates.append(
                        {
                            "transport": "http",
                            "url": event.get("url"),
                            "method": event.get("method", "POST"),
                            "headers": event.get("headers", {}),
                            "payload_template": parsed.get("parsed") or body,
                            "fields": parsed.get("fields", {}),
                        }
                    )

        return {
            "message_types": message_types,
            "ws_endpoints": ws_endpoints,
            "send_templates": send_templates,
            "parsed_messages": parsed_messages,
        }

    def _guess_msg_type(self, parsed: dict[str, Any]) -> str:
        fields = parsed.get("fields") or {}
        for key in ("msg_type", "type", "message_type"):
            for field_key, value in fields.items():
                if key in field_key.lower():
                    return str(value)

        parsed_data = parsed.get("parsed")
        if isinstance(parsed_data, dict):
            for key in ("msg_type", "type", "cmd", "method", "action"):
                if key in parsed_data:
                    return str(parsed_data[key])

        if parsed.get("format") == "json":
            return "json_unknown"
        if parsed.get("format") == "hex":
            return "binary"
        return "text"
