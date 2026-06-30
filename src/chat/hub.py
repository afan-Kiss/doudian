from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.chat.conversation_keys import conversation_ids_match, normalize_route_key
from src.monitor.inbound_listener import make_dedupe_key
from src.monitor.text_filters import is_meaningless_message
from src.utils.chat_log import chat_log

BotHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

ROLE_SIDE = {
    "buyer": "left",
    "seller": "right",
    "system": "center",
}


def _is_usable_nickname(value: Any) -> bool:
    nick = str(value or "").strip()
    return bool(nick) and nick not in {"-", "?", "未知买家", "买家", "店铺"}


def _pick_nickname(msg: dict[str, Any], existing: str = "") -> str:
    incoming = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
    if _is_usable_nickname(incoming):
        return incoming
    if _is_usable_nickname(existing):
        return existing
    if str(msg.get("role") or "") == "buyer":
        return incoming or "未知买家"
    return existing or "未知买家"


def _conversation_key(msg: dict[str, Any]) -> str:
    route = str(msg.get("conversation_route") or "").strip()
    if route:
        return normalize_route_key(route)
    talk_id = str(msg.get("conversation_id") or "").strip()
    if talk_id:
        return talk_id
    nickname = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
    if nickname:
        return nickname
    msg_id = str(msg.get("server_message_id") or msg.get("client_message_id") or "").strip()
    if msg_id:
        return f"msg:{msg_id}"
    return "unknown"


def _message_id(msg: dict[str, Any]) -> str:
    for key in ("server_message_id", "client_message_id", "id"):
        value = str(msg.get(key) or "").strip()
        if value:
            return value
    return make_dedupe_key(msg)


def _timestamp_sort_key(msg: dict[str, Any]) -> float:
    ts = msg.get("timestamp")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


class ChatHub:
    """In-memory chat store with WebSocket broadcast and bot hook."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []
        self._message_ids: set[str] = set()
        self._conversations: dict[str, dict[str, Any]] = {}
        self._clients: set[Any] = set()
        self._bot_handlers: list[BotHandler] = []
        self._lock = asyncio.Lock()
        self._nickname_by_route: dict[str, str] = {}
        self._nickname_by_conv_id: dict[str, str] = {}

    def sync_conversation_directory(self, entries: list[dict[str, Any]]) -> int:
        """Update nickname lookup from Feige conversation list (Mona store / sidebar)."""
        updated = 0
        for entry in entries:
            nickname = str(entry.get("nickname") or entry.get("name") or "").strip()
            if not _is_usable_nickname(nickname):
                continue
            for field in ("conversation_route", "id", "conversation_id"):
                raw = str(entry.get(field) or "").strip()
                if not raw:
                    continue
                self._nickname_by_conv_id[raw] = nickname
                norm = normalize_route_key(raw)
                if norm:
                    self._nickname_by_conv_id[norm] = nickname
                    if self._nickname_by_route.get(norm) != nickname:
                        self._nickname_by_route[norm] = nickname
                        updated += 1
                if raw.startswith("n") and len(raw) > 24:
                    stripped = raw[1:]
                    self._nickname_by_conv_id[stripped] = nickname
        return updated

    def resolve_nickname(
        self,
        *,
        nickname: str = "",
        conversation_route: str = "",
        conversation_id: str = "",
    ) -> str:
        nick = str(nickname or "").strip()
        if _is_usable_nickname(nick):
            return nick

        route = str(conversation_route or "").strip()
        if route:
            norm = normalize_route_key(route)
            found = self._nickname_by_route.get(norm) or self._nickname_by_conv_id.get(route)
            if _is_usable_nickname(found):
                return found

        talk_id = str(conversation_id or "").strip()
        if talk_id:
            found = self._nickname_by_conv_id.get(talk_id)
            if _is_usable_nickname(found):
                return found

        for conv in self.list_conversations():
            conv_route = str(conv.get("conversation_route") or "").strip()
            conv_talk = str(conv.get("conversation_id") or "").strip()
            if route and conv_route:
                if normalize_route_key(conv_route) == normalize_route_key(route):
                    candidate = str(conv.get("nickname") or "").strip()
                    if _is_usable_nickname(candidate):
                        return candidate
            if talk_id and conv_talk == talk_id:
                candidate = str(conv.get("nickname") or "").strip()
                if _is_usable_nickname(candidate):
                    return candidate
        return nick

    def resolve_conversation_ids(
        self,
        *,
        nickname: str = "",
        conversation_route: str = "",
        conversation_id: str = "",
    ) -> tuple[str, str]:
        """Return canonical (route, talk_id) from directory when WS ids are partial."""
        route = str(conversation_route or "").strip()
        talk_id = str(conversation_id or "").strip()
        nick = self.resolve_nickname(
            nickname=nickname,
            conversation_route=route,
            conversation_id=talk_id,
        )

        best_route = route
        best_talk = talk_id
        best_len = len(route)

        for conv in self.list_conversations():
            conv_nick = str(conv.get("nickname") or "").strip()
            if nick and conv_nick and conv_nick != nick:
                continue
            conv_route = str(conv.get("conversation_route") or "").strip()
            conv_talk = str(conv.get("conversation_id") or "").strip()
            if route and conv_route:
                if normalize_route_key(conv_route) != normalize_route_key(route):
                    if not conversation_ids_match(route, conv_route):
                        continue
            elif talk_id and conv_talk and conv_talk != talk_id:
                continue
            elif nick and conv_nick != nick:
                continue

            pick_route = conv_route or route
            if len(pick_route) > best_len:
                best_route = pick_route
                best_talk = conv_talk or talk_id
                best_len = len(pick_route)

        if not best_route and nick:
            for conv in self.list_conversations():
                if str(conv.get("nickname") or "").strip() == nick:
                    conv_route = str(conv.get("conversation_route") or "").strip()
                    if len(conv_route) > best_len:
                        best_route = conv_route
                        best_talk = str(conv.get("conversation_id") or "").strip()
                        best_len = len(conv_route)

        return best_route, best_talk

    def register_bot_handler(self, handler: BotHandler) -> None:
        self._bot_handlers.append(handler)

    def attach_client(self, websocket: Any) -> None:
        self._clients.add(websocket)

    def detach_client(self, websocket: Any) -> None:
        self._clients.discard(websocket)

    def normalize(self, msg: dict[str, Any]) -> dict[str, Any]:
        role = str(msg.get("role") or "buyer")
        conv_key = _conversation_key(msg)
        normalized = {
            "id": _message_id(msg),
            "role": role,
            "role_label": msg.get("role_label") or role,
            "nickname": msg.get("nickname") or msg.get("buyer_name") or "",
            "text": str(msg.get("text") or ""),
            "conversation_id": msg.get("conversation_id") or "",
            "conversation_route": msg.get("conversation_route") or "",
            "conversation_key": conv_key,
            "server_message_id": msg.get("server_message_id") or "",
            "client_message_id": msg.get("client_message_id") or "",
            "timestamp": msg.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "side": ROLE_SIDE.get(role, "left"),
            "source": msg.get("source") or "ws_cdp",
            "pending": bool(msg.get("pending")),
        }
        return normalized

    def _update_conversation(self, msg: dict[str, Any]) -> None:
        key = msg["conversation_key"]
        existing = self._conversations.get(key) or {
            "conversation_key": key,
            "conversation_id": msg.get("conversation_id") or "",
            "conversation_route": msg.get("conversation_route") or "",
            "nickname": _pick_nickname(msg),
            "last_text": "",
            "last_timestamp": "",
            "updated_at": 0.0,
        }
        existing["nickname"] = _pick_nickname(msg, str(existing.get("nickname") or ""))
        nick = existing["nickname"]
        if _is_usable_nickname(nick):
            route = str(existing.get("conversation_route") or msg.get("conversation_route") or "").strip()
            talk_id = str(existing.get("conversation_id") or msg.get("conversation_id") or "").strip()
            if route:
                norm = normalize_route_key(route)
                self._nickname_by_route[norm] = nick
                self._nickname_by_conv_id[route] = nick
                self._nickname_by_conv_id[norm] = nick
            if talk_id:
                self._nickname_by_conv_id[talk_id] = nick
        if msg.get("conversation_id"):
            existing["conversation_id"] = msg.get("conversation_id")
        if msg.get("conversation_route"):
            existing["conversation_route"] = msg.get("conversation_route")
        existing["last_text"] = msg.get("text") or existing["last_text"]
        existing["last_timestamp"] = msg.get("timestamp") or existing["last_timestamp"]
        existing["updated_at"] = _timestamp_sort_key(msg)
        self._conversations[key] = existing

    def _nickname_for_conversation(self, item: dict[str, Any]) -> str:
        key = str(item.get("conversation_key") or "")
        nickname = str(item.get("nickname") or "")
        if _is_usable_nickname(nickname):
            return nickname
        for msg in reversed(self._messages):
            if str(msg.get("conversation_key") or "") != key:
                continue
            candidate = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
            if _is_usable_nickname(candidate):
                return candidate
        return nickname or "未知买家"

    def _dedupe_conversations(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_route: dict[str, dict[str, Any]] = {}
        orphans: list[dict[str, Any]] = []

        for item in items:
            route = str(item.get("conversation_route") or "").strip()
            if route:
                current = by_route.get(route)
                if not current:
                    by_route[route] = dict(item)
                    continue
                better_name = _pick_nickname(
                    {"nickname": item.get("nickname"), "role": "buyer"},
                    str(current.get("nickname") or ""),
                )
                if (item.get("updated_at") or 0) >= (current.get("updated_at") or 0):
                    merged = dict(item)
                    merged["nickname"] = better_name
                    by_route[route] = merged
                else:
                    current["nickname"] = _pick_nickname(
                        {"nickname": current.get("nickname"), "role": "buyer"},
                        better_name,
                    )
                continue

            talk_id = str(item.get("conversation_id") or "").strip()
            if talk_id:
                matched = next(
                    (
                        conv
                        for conv in by_route.values()
                        if str(conv.get("conversation_id") or "") == talk_id
                    ),
                    None,
                )
                if matched:
                    matched["nickname"] = _pick_nickname(
                        {"nickname": item.get("nickname"), "role": "buyer"},
                        str(matched.get("nickname") or ""),
                    )
                    if (item.get("updated_at") or 0) > (matched.get("updated_at") or 0):
                        matched["last_text"] = item.get("last_text") or matched.get("last_text")
                        matched["last_timestamp"] = item.get("last_timestamp") or matched.get("last_timestamp")
                        matched["updated_at"] = item.get("updated_at") or matched.get("updated_at")
                    continue

            orphans.append(item)

        merged = list(by_route.values()) + orphans
        for item in merged:
            item["nickname"] = self._nickname_for_conversation(item)
        merged.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
        return merged

    def _log_chat_message(self, msg: dict[str, Any]) -> None:
        role = str(msg.get("role") or "")
        if role not in {"buyer", "seller"}:
            return
        text = str(msg.get("text") or "")
        nickname = str(msg.get("nickname") or msg.get("buyer_name") or "-")
        if is_meaningless_message(text, role, nickname):
            return
        role_label = "买家" if role == "buyer" else "卖家"
        chat_log(f"[chat] [{role_label}] {nickname}: {text}")

    async def publish(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        resolved = self.resolve_nickname(
            nickname=str(msg.get("nickname") or msg.get("buyer_name") or ""),
            conversation_route=str(msg.get("conversation_route") or ""),
            conversation_id=str(msg.get("conversation_id") or ""),
        )
        if resolved:
            msg = {**msg, "nickname": resolved, "buyer_name": resolved}

        normalized = self.normalize(msg)
        normalized["pending"] = False
        msg_id = normalized["id"]

        async with self._lock:
            for index, existing in enumerate(self._messages):
                if existing["id"] == msg_id:
                    self._message_ids.add(msg_id)
                    self._messages[index] = normalized
                    self._update_conversation(normalized)
                    await self._broadcast({"type": "message_update", "message": normalized})
                    await self._broadcast(
                        {"type": "conversations", "conversations": self.list_conversations()}
                    )
                    if normalized["role"] == "buyer":
                        old_text = str(existing.get("text") or "")
                        new_text = str(normalized.get("text") or "")
                        if new_text and new_text != old_text:
                            asyncio.create_task(self._dispatch_bot_handlers(normalized))
                    return normalized

            if msg_id in self._message_ids:
                return None

            self._message_ids.add(msg_id)
            self._messages.append(normalized)
            self._update_conversation(normalized)
            self._log_chat_message(normalized)

        await self._broadcast({"type": "message", "message": normalized})
        await self._broadcast({"type": "conversations", "conversations": self.list_conversations()})

        if normalized["role"] == "buyer":
            asyncio.create_task(self._dispatch_bot_handlers(normalized))

        return normalized

    async def add_pending_outbound(self, msg: dict[str, Any]) -> dict[str, Any]:
        pending = self.normalize(
            {
                **msg,
                "id": msg.get("id") or f"pending:{uuid.uuid4()}",
                "role": "seller",
                "role_label": "卖家",
                "pending": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        async with self._lock:
            self._messages.append(pending)
            self._update_conversation(pending)
        await self._broadcast({"type": "message", "message": pending})
        await self._broadcast({"type": "conversations", "conversations": self.list_conversations()})
        return pending

    def list_conversations(self) -> list[dict[str, Any]]:
        items = list(self._conversations.values())
        return self._dedupe_conversations(items)

    def list_messages(self, conversation_key: str | None = None) -> list[dict[str, Any]]:
        if not conversation_key:
            return list(self._messages)
        return [msg for msg in self._messages if msg.get("conversation_key") == conversation_key]

    async def send_snapshot(self, websocket: Any, conversation_key: str | None = None) -> None:
        payload = {
            "type": "snapshot",
            "conversations": self.list_conversations(),
            "messages": self.list_messages(conversation_key),
            "conversation_key": conversation_key,
        }
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        text = json.dumps(payload, ensure_ascii=False)
        dead: list[Any] = []
        for client in list(self._clients):
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        for client in dead:
            self.detach_client(client)

    async def _dispatch_bot_handlers(self, msg: dict[str, Any]) -> None:
        if str(msg.get("role") or "") != "buyer":
            return
        if int(msg.get("direction") or 0) == 2:
            return
        for handler in self._bot_handlers:
            try:
                result = handler(msg)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                continue
