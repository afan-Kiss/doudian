from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.chat.hub import ChatHub
from src.chat.conversation_keys import conv_id_variants, conversation_ids_match, normalize_route_key
from src.sender.api_sender import APISender
from src.sender.feige_navigator import FeigeNavigator
from src.sender.frame_context import find_im_frame
from src.sender.page_ws_encoder import PageWsEncoder


@dataclass(frozen=True)
class ConversationTarget:
    nickname: str
    conversation_key: str
    conversation_id: str
    conversation_route: str

    @classmethod
    def from_message(cls, msg: dict[str, Any]) -> ConversationTarget:
        nickname = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
        conversation_route = str(msg.get("conversation_route") or "").strip()
        conversation_id = str(msg.get("conversation_id") or "").strip()
        conversation_key = str(
            msg.get("conversation_key")
            or conversation_route
            or conversation_id
            or nickname
        ).strip()
        return cls(
            nickname=nickname,
            conversation_key=conversation_key,
            conversation_id=conversation_id,
            conversation_route=conversation_route,
        )

    @property
    def sdk_conversation_id(self) -> str:
        return self.conversation_route or self.conversation_id


async def _read_current_conversation_id(page: Any) -> str:
    im_frame = await find_im_frame(page)
    probe = await PageWsEncoder().probe(im_frame)
    return str((probe.get("env") or {}).get("conversationId") or "").strip()


async def _prepare_bot_conversation(
    page: Any,
    target: ConversationTarget,
) -> tuple[ConversationTarget, list[str]]:
    """Switch Mona store to buyer session and build conv-id candidates (UI id first)."""
    navigator = FeigeNavigator()
    expected = target.sdk_conversation_id
    switched = await navigator.switch_conversation_in_store(
        page,
        conversation_id=expected,
        nickname=target.nickname,
    )
    current = await _read_current_conversation_id(page)
    if not current or (expected and not conversation_ids_match(expected, current)):
        opened = await navigator.open_chat_for_target(
            page,
            nickname=target.nickname,
            conversation_id=expected,
            timeout_ms=8000,
        )
        await page.wait_for_timeout(600)
        current = await _read_current_conversation_id(page)
        if not opened and not current:
            pass

    candidates: list[str] = []
    if current:
        candidates.append(current)
    for value in (current, target.conversation_route, target.conversation_id, expected):
        for variant in conv_id_variants(str(value or "")):
            if variant and variant not in candidates:
                candidates.append(variant)
    return target, candidates


async def _ensure_conversation_open(
    page: Any,
    target: ConversationTarget,
    *,
    required: bool = True,
    max_attempts: int = 3,
) -> None:
    navigator = FeigeNavigator()
    expected = target.sdk_conversation_id

    if not target.nickname:
        raise RuntimeError("Missing buyer nickname for send")

    for attempt in range(max_attempts):
        current = await navigator.read_current_conversation_id(page)
        if expected and current and conversation_ids_match(expected, current):
            return

        opened = await navigator.open_chat_for_target(
            page,
            nickname=target.nickname,
            conversation_id=expected,
            timeout_ms=20000 if attempt == 0 else 12000,
        )
        await page.wait_for_timeout(900)
        current = await navigator.read_current_conversation_id(page)
        if expected and current and conversation_ids_match(expected, current):
            return

        if not opened:
            continue

        if expected and current and not conversation_ids_match(expected, current):
            continue

    current = await navigator.read_current_conversation_id(page)
    if expected and current and conversation_ids_match(expected, current):
        return

    message = (
        f"Could not open chat for {target.nickname!r} "
        f"(expected={expected[:48]!r}, current={current[:48]!r})"
    )
    if required:
        raise RuntimeError(message)


async def send_text_message(
    *,
    page: Any,
    schema_dir: Path,
    hub: ChatHub,
    contact: str,
    text: str,
    conversation_id: str | None = None,
    conversation_key: str | None = None,
    conversation_route: str | None = None,
    source: str = "ui_send",
) -> dict[str, Any]:
    target = ConversationTarget(
        nickname=contact.strip(),
        conversation_key=(conversation_key or conversation_route or conversation_id or contact).strip(),
        conversation_id=(conversation_id or "").strip(),
        conversation_route=(conversation_route or conversation_key or "").strip(),
    )
    text = text.strip()
    if not target.nickname or not text:
        raise ValueError("contact and text are required")

    pending = await hub.add_pending_outbound(
        {
            "text": text,
            "nickname": target.nickname,
            "conversation_id": target.conversation_id,
            "conversation_route": target.conversation_route,
            "conversation_key": target.conversation_key,
        }
    )

    conv_id = target.sdk_conversation_id or target.conversation_id or None
    sender = APISender(schema_dir)

    force_open = source in {"bot_api", "bot"}
    if force_open:
        target, conv_candidates = await _prepare_bot_conversation(page, target)
        conv_id = target.sdk_conversation_id or target.conversation_id or None
        if None not in conv_candidates:
            conv_candidates.append(None)
    else:
        conv_candidates: list[str | None] = []
        for value in (target.conversation_route, target.conversation_id, conv_id):
            for variant in conv_id_variants(str(value or "")):
                if variant and variant not in conv_candidates:
                    conv_candidates.append(variant)
        for value in (target.conversation_route, target.conversation_id, conv_id, None):
            if value and value not in conv_candidates:
                conv_candidates.append(value)
        if None not in conv_candidates:
            conv_candidates.append(None)
        await _ensure_conversation_open(page, target, required=True)

    resolved_route, resolved_talk = hub.resolve_conversation_ids(
        nickname=target.nickname,
        conversation_route=target.conversation_route,
        conversation_id=target.conversation_id,
    )
    if resolved_route and len(resolved_route) >= len(target.conversation_route or ""):
        target = ConversationTarget(
            nickname=target.nickname,
            conversation_key=normalize_route_key(resolved_route) or resolved_route,
            conversation_id=resolved_talk or target.conversation_id,
            conversation_route=resolved_route,
        )
        conv_id = target.sdk_conversation_id or target.conversation_id or None
        if force_open:
            _, conv_candidates = await _prepare_bot_conversation(page, target)
            if None not in conv_candidates:
                conv_candidates.append(None)
        else:
            conv_candidates = []
            for value in (target.conversation_route, target.conversation_id, conv_id):
                for variant in conv_id_variants(str(value or "")):
                    if variant and variant not in conv_candidates:
                        conv_candidates.append(variant)
            for value in (target.conversation_route, target.conversation_id, conv_id, None):
                if value and value not in conv_candidates:
                    conv_candidates.append(value)
            if None not in conv_candidates:
                conv_candidates.append(None)

    success = False
    winning_conv: str | None = None
    for candidate in conv_candidates:
        success = await sender.send(page, text, candidate)
        if success:
            winning_conv = candidate
            break

    current = await _read_current_conversation_id(page)
    expected = target.sdk_conversation_id
    if success and current and expected and not conversation_ids_match(expected, current):
        retry = await sender.send(page, text, current)
        if retry:
            winning_conv = current
        else:
            success = False

    if not success and force_open:
        await _ensure_conversation_open(page, target, required=False)
        for candidate in conv_candidates:
            success = await sender.send(page, text, candidate)
            if success:
                break
        if not success:
            success = await sender.send(page, text, None)

    if not success and not force_open:
        await _ensure_conversation_open(page, target, required=True)
        success = await sender.send(page, text, None)

    if not success:
        detail = sender.last_send_detail or {}
        reason = detail.get("reason") or detail.get("hint") or detail.get("error") or "Send failed"
        raise RuntimeError(str(reason))

    current = await _read_current_conversation_id(page)
    expected = target.sdk_conversation_id

    confirmed = await hub.publish(
        {
            "id": pending["id"],
            "role": "seller",
            "role_label": "卖家",
            "text": text,
            "nickname": target.nickname,
            "conversation_id": target.conversation_id,
            "conversation_route": target.conversation_route,
            "conversation_key": target.conversation_key,
            "source": source,
        }
    )
    return {
        "ok": True,
        "message": confirmed or pending,
        "send_mode": sender.last_send_mode,
        "conversation_id": current or conv_id or expected,
    }


async def send_text_to_buyer_message(
    *,
    page: Any,
    schema_dir: Path,
    hub: ChatHub,
    buyer_message: dict[str, Any],
    text: str,
    source: str = "bot_api",
) -> dict[str, Any]:
    target = ConversationTarget.from_message(buyer_message)
    if not target.nickname:
        raise RuntimeError("Buyer message missing nickname")
    return await send_text_message(
        page=page,
        schema_dir=schema_dir,
        hub=hub,
        contact=target.nickname,
        text=text,
        conversation_id=target.conversation_id or None,
        conversation_key=target.conversation_key,
        conversation_route=target.conversation_route or None,
        source=source,
    )
