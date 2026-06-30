from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.monitor.pigeon_frame_parser import MESSAGE_KINDS, parse_inbound_frame
from src.monitor.text_filters import is_meaningless_message, normalize_text


def make_dedupe_key(event: dict[str, Any], bucket_ms: int = 4000) -> str:
    role = str(event.get("role") or "buyer")
    if event.get("message_id"):
        return f"{role}:mid:{event['message_id']}"
    if event.get("server_message_id"):
        return f"{role}:sid:{event['server_message_id']}"
    if event.get("client_message_id"):
        return f"{role}:cid:{event['client_message_id']}"
    text = str(event.get("text") or "").strip()
    buyer = str(event.get("nickname") or event.get("buyer_name") or event.get("buyer_id") or "?").strip()
    ts = event.get("timestamp")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = time.time()
    bucket = int(float(ts or time.time()) * 1000) // bucket_ms
    return f"{role}:txt:{buyer}:{text}:{bucket}"


def format_chat_line(event: dict[str, Any]) -> str:
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)):
        time_text = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    elif isinstance(ts, str):
        try:
            time_text = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
        except ValueError:
            time_text = ts[:19]
    else:
        time_text = datetime.now().strftime("%H:%M:%S")

    role = event.get("role_label") or event.get("role") or "?"
    nickname = event.get("nickname") or event.get("buyer_name") or "-"
    conv = event.get("conversation_route") or event.get("conversation_id") or "-"
    text = str(event.get("text") or "")
    return f"[{time_text}] [{role}] {nickname} | 会话={conv[:48]} | {text}"


class InboundListener:
    """Real-time buyer/seller message listener with dedupe."""

    def __init__(
        self,
        *,
        roles: set[str] | None = None,
        dedupe_window_ms: int = 4000,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        console_log: bool = True,
    ) -> None:
        self.roles = roles or {"buyer"}
        self.dedupe_window_ms = dedupe_window_ms
        self.on_message = on_message
        self.console_log = console_log
        self._seen: dict[str, float] = {}
        self._seen_ttl_sec = 120.0
        self._seen_max = 500
        self.messages: list[dict[str, Any]] = []

    def _prune_seen(self) -> None:
        now = time.time()
        expired = [key for key, seen_at in self._seen.items() if now - seen_at > self._seen_ttl_sec]
        for key in expired:
            self._seen.pop(key, None)
        if len(self._seen) <= self._seen_max:
            return
        sorted_items = sorted(self._seen.items(), key=lambda item: item[1])
        for key, _ in sorted_items[: len(self._seen) - self._seen_max + 50]:
            self._seen.pop(key, None)

    def _should_emit(self, event: dict[str, Any]) -> bool:
        key = make_dedupe_key(event, self.dedupe_window_ms)
        if key in self._seen:
            return False
        self._seen[key] = time.time()
        self._prune_seen()
        return True

    def _normalize_event(self, parsed: dict[str, Any], raw_event: dict[str, Any]) -> dict[str, Any] | None:
        role = str(parsed.get("role") or "")
        if int(parsed.get("direction") or 0) == 2 and role == "buyer":
            role = "seller"
        nickname = str(parsed.get("nickname") or parsed.get("buyer_name") or "")
        text = normalize_text(parsed.get("text") or "")
        if not text or is_meaningless_message(text, role, nickname):
            return None

        if role not in self.roles:
            return None

        role_labels = {"buyer": "买家", "seller": "卖家", "system": "系统"}
        kind = parsed.get("kind")
        if role == "seller":
            kind = "seller_message"
        elif role == "buyer":
            kind = "buyer_message"
        elif role == "system":
            kind = "system_message"
        return {
            "kind": kind,
            "role": role,
            "role_label": role_labels.get(role, role),
            "text": text,
            "nickname": parsed.get("nickname") or "",
            "buyer_name": parsed.get("nickname") or "",
            "conversation_id": parsed.get("conversation_id") or "",
            "conversation_route": parsed.get("conversation_route") or "",
            "security_receiver_id": parsed.get("security_receiver_id") or "",
            "shop_id": parsed.get("shop_id") or "",
            "server_message_id": parsed.get("server_message_id") or "",
            "client_message_id": parsed.get("client_message_id") or "",
            "msg_type": parsed.get("msg_type"),
            "direction": parsed.get("direction"),
            "payload_bytes": parsed.get("payload_bytes"),
            "timestamp": parsed.get("timestamp") or raw_event.get("ts") or utc_now_iso(),
            "url": parsed.get("url") or raw_event.get("url") or "",
            "source": parsed.get("source") or raw_event.get("source") or "ws_cdp",
        }

    def _emit_normalized(self, normalized: dict[str, Any]) -> dict[str, Any] | None:
        if not self._should_emit(normalized):
            return None
        self.messages.append(normalized)
        if self.console_log:
            print(format_chat_line(normalized), flush=True)
        if self.on_message:
            self.on_message(normalized)
        return normalized

    def handle_parsed_event(self, parsed: dict[str, Any], raw_event: dict[str, Any]) -> dict[str, Any] | None:
        if parsed.get("kind") not in MESSAGE_KINDS:
            return None
        normalized = self._normalize_event(parsed, raw_event)
        if not normalized:
            return None
        return self._emit_normalized(normalized)

    def handle_ws_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") != "ws_frame_received":
            return None
        if event.get("format") != "binary":
            return None

        parsed = parse_inbound_frame(event)
        if parsed.get("kind") not in MESSAGE_KINDS:
            return None

        normalized = self._normalize_event(parsed, event)
        if not normalized:
            return None
        return self._emit_normalized(normalized)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
