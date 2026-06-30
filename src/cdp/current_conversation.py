from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from src.cdp.conversation_aggregate import aggregate_conversation
from src.chat.conversation_keys import conversation_ids_match, normalize_pigeon_conversation_id, normalize_route_key
from src.chat.hub import ChatHub
from src.sender.frame_context import find_im_frame
from src.sender.page_ws_encoder import PageWsEncoder

logger = logging.getLogger("current_conversation")

_CDP_DIR = Path(__file__).resolve().parent
_BUYER_EXTRACT_JS = (_CDP_DIR / "buyer_name_extract.js").read_text(encoding="utf-8")
_DOM_SNAPSHOT_RAW = (_CDP_DIR / "dom_chat_snapshot.js").read_text(encoding="utf-8")
DOM_SNAPSHOT_JS = _BUYER_EXTRACT_JS + "\n" + _DOM_SNAPSHOT_RAW

ROLE_FROM_HUB = {
    "buyer": "customer",
    "seller": "service",
    "system": "system",
    "robot": "robot",
}

_EMPTY_ORDER_CONTEXT = {
    "has_order": False,
    "source": "none",
    "orders": [],
    "latest_order": {},
    "summary": "当前买家暂无订单",
}
_EMPTY_PRODUCT_CONTEXT = {
    "has_product": False,
    "products": [],
    "latest_product": {},
    "source": "none",
}


def _mask_privacy(text: str) -> str:
    masked = re.sub(r"1[3-9]\d{9}", "***手机号***", text)
    return re.sub(r"\d{15,20}", "***订单号***", masked)


def customer_hash(conversation_id: str, customer_name: str) -> str:
    raw = f"{conversation_id}|{customer_name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _message_matches(
    msg: dict[str, Any],
    *,
    route: str,
    talk_id: str,
    conv_key: str,
) -> bool:
    msg_route = str(msg.get("conversation_route") or "").strip()
    msg_talk = str(msg.get("conversation_id") or "").strip()
    msg_key = str(msg.get("conversation_key") or "").strip()
    if route and msg_route and conversation_ids_match(route, msg_route):
        return True
    if talk_id and msg_talk and conversation_ids_match(talk_id, msg_talk):
        return True
    if conv_key and msg_key == conv_key:
        return True
    if route and msg_key and normalize_route_key(route) == normalize_route_key(msg_key):
        return True
    return False


def hub_messages_for_current(
    hub: ChatHub,
    *,
    conversation_route: str,
    conversation_id: str,
    nickname: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    resolved_route, resolved_talk = hub.resolve_conversation_ids(
        nickname=nickname,
        conversation_route=conversation_route,
        conversation_id=conversation_id,
    )
    conv_key = normalize_route_key(resolved_route) or resolved_talk or conversation_id
    matched = [
        msg
        for msg in hub.list_messages()
        if _message_matches(
            msg,
            route=resolved_route or conversation_route,
            talk_id=resolved_talk or conversation_id,
            conv_key=conv_key,
        )
    ]
    matched.sort(key=lambda m: m.get("timestamp") or "")
    return matched[-limit:]


def hub_to_node_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = ROLE_FROM_HUB.get(str(msg.get("role") or ""), str(msg.get("role") or "customer"))
        text = _mask_privacy(str(msg.get("text") or "").strip())
        if not text:
            continue
        mid = str(msg.get("server_message_id") or msg.get("client_message_id") or msg.get("id") or "")
        out.append(
            {
                "role": role,
                "text": text,
                "time": str(msg.get("timestamp") or ""),
                "message_id": mid or f"ws:{role}:{_norm_text(text)[:32]}",
                "source": "ws",
            }
        )
    return out


def merge_dom_and_ws(
    dom_messages: list[dict[str, Any]],
    ws_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()

    def add(msg: dict[str, Any]) -> None:
        mid = str(msg.get("message_id") or "").strip()
        role = str(msg.get("role") or "")
        text = str(msg.get("text") or "").strip()
        if not text:
            return
        fuzzy = f"{role}::{_norm_text(text)}"
        if mid and mid in seen_ids:
            return
        if fuzzy in seen_text:
            return
        for existing in merged:
            if existing["role"] == role and _norm_text(existing["text"]) == _norm_text(text):
                return
        if mid:
            seen_ids.add(mid)
        seen_text.add(fuzzy)
        merged.append(
            {
                "role": role,
                "text": text,
                "time": str(msg.get("time") or ""),
                "message_id": mid or f"merge:{role}:{len(merged)}",
            }
        )

    for msg in dom_messages:
        add({**msg, "source": msg.get("source", "dom")})
    for msg in ws_messages:
        add(msg)

    return merged[-20:]


def feige_page_hint(page_url: str) -> str:
    url = (page_url or "").lower()
    if "leave_msg" in url:
        return "飞鸽当前在「留言」页，请点左侧「会话」，再点开该买家的聊天窗口。"
    if "hist_conv" in url or "history" in url:
        return "飞鸽当前在「历史会话」页，请回到「当前会话」并点开该买家。"
    if not url or "workspace" not in url:
        return "请打开飞鸽「当前会话」页，并在左侧点开该买家的聊天窗口。"
    return "请先在飞鸽左侧点开该买家的会话（最后一条须是买家消息）。"


async def probe_page_state(page: Any) -> dict[str, Any]:
    im_frame = await find_im_frame(page)
    probe = await PageWsEncoder().probe(im_frame)
    env = probe.get("env") or {}
    page_url = str(getattr(page, "url", "") or "")
    logged_in = bool(env.get("hasMonaStore")) and "login" not in page_url.lower()
    conv_id = normalize_pigeon_conversation_id(str(env.get("conversationId") or "").strip())
    conv_name = str(env.get("conversationName") or "").strip()
    return {
        "connected": True,
        "logged_in": logged_in,
        "conversation_id": conv_id,
        "customer_name": conv_name,
        "page_url": page_url,
        "has_im": bool(env.get("hasIm")),
    }


def hook_to_node_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    role_map = {"buyer": "customer", "seller": "service", "system": "system", "robot": "robot"}
    for item in items:
        role = role_map.get(str(item.get("role") or ""), str(item.get("role") or "customer"))
        text = _mask_privacy(str(item.get("text") or "").strip())
        if not text:
            continue
        mid = str(item.get("server_message_id") or "")
        out.append(
            {
                "role": role,
                "text": text,
                "time": "",
                "message_id": mid or f"hook:{role}:{_norm_text(text)[:32]}",
                "source": str(item.get("source") or "hook"),
            }
        )
    return out


async def read_hook_snapshot(page: Any) -> list[dict[str, Any]]:
    script = """
    () => {
      const queue = (window.__feigeInboundQueue || []).slice(-80);
      return queue;
    }
    """
    items: list[dict[str, Any]] = []
    for target in [page, *page.frames]:
        try:
            drained = await target.evaluate(script)
            if drained:
                items.extend(drained)
        except Exception:
            continue
    return hook_to_node_messages(items)


async def read_dom_snapshot(page: Any) -> dict[str, Any]:
    script = f"(async () => {{ const run = {DOM_SNAPSHOT_JS}; return await run(); }})()"
    best_messages: list[dict[str, Any]] = []
    best_profile: dict[str, Any] = {}
    customer_name = ""
    sdk_count = 0
    dom_count = 0

    targets: list[Any] = [page, *page.frames]
    seen_frames: set[int] = set()
    for target in targets:
        fid = id(target)
        if fid in seen_frames:
            continue
        seen_frames.add(fid)
        try:
            result = await target.evaluate(script)
        except Exception:
            continue
        if not result:
            continue
        msgs = result.get("messages") or []
        if result.get("customer_name"):
            customer_name = str(result.get("customer_name"))
        if len(msgs) > len(best_messages):
            best_messages = msgs
            best_profile = result.get("selector_profile") or {}
            sdk_count = int(result.get("sdk_count") or 0)
            dom_count = int(result.get("dom_count") or 0)
            customer_name = customer_name or str(result.get("customer_name") or "")
            logger.info(
                "DOM/SDK snapshot frame=%s profile=%s sdk=%d dom=%d merged=%d",
                getattr(target, "url", "")[:80],
                best_profile,
                sdk_count,
                dom_count,
                len(msgs),
            )

    for msg in best_messages:
        msg["text"] = _mask_privacy(str(msg.get("text") or ""))
        msg["source"] = msg.get("source") or "dom"
    return {
        "customer_name": customer_name,
        "messages": best_messages,
        "selector_profile": best_profile,
        "message_count": len(best_messages),
        "sdk_count": sdk_count,
        "dom_count": dom_count,
    }


async def read_current_conversation(page: Any, hub: ChatHub) -> dict[str, Any]:
    state = await probe_page_state(page)
    if not state["logged_in"]:
        return {
            "ok": False,
            "connected": True,
            "logged_in": False,
            "error": "NOT_LOGGED_IN",
            "message": "请先登录飞鸽",
            "order_context": _EMPTY_ORDER_CONTEXT,
            "product_context": _EMPTY_PRODUCT_CONTEXT,
        }

    conv_id = state["conversation_id"]
    sdk_name = state["customer_name"]
    if not conv_id:
        page_url = state.get("page_url") or ""
        return {
            "ok": False,
            "connected": True,
            "logged_in": True,
            "error": "NO_ACTIVE_CONVERSATION",
            "page_url": page_url,
            "feige_hint": feige_page_hint(page_url),
            "message": feige_page_hint(page_url),
            "order_context": _EMPTY_ORDER_CONTEXT,
            "product_context": _EMPTY_PRODUCT_CONTEXT,
        }

    dom = await read_dom_snapshot(page)
    dom_messages = dom.get("messages") or []
    customer_name = dom.get("customer_name") or sdk_name

    hook_messages = await read_hook_snapshot(page)

    hub_raw = hub_messages_for_current(
        hub,
        conversation_route=conv_id,
        conversation_id=conv_id,
        nickname=customer_name,
    )
    ws_messages = hub_to_node_messages(hub_raw)
    sdk_messages = [m for m in dom_messages if m.get("source") == "sdk"]
    snapshot_messages = merge_dom_and_ws(dom_messages, hook_messages)
    recent_messages = merge_dom_and_ws(snapshot_messages, ws_messages)
    aggregate_source = sdk_messages if sdk_messages else recent_messages

    if not recent_messages:
        return {
            "ok": False,
            "connected": True,
            "logged_in": True,
            "conversation_id": conv_id,
            "customer_name": customer_name,
            "error": "MESSAGE_DOM_NOT_FOUND",
            "message": "已连接飞鸽，但没有识别到聊天消息，请确认已点开客户会话",
            "selector_profile": dom.get("selector_profile"),
            "dom_count": len(dom_messages),
            "sdk_count": dom.get("sdk_count", 0),
            "hook_count": len(hook_messages),
            "ws_count": len(ws_messages),
            "order_context": _EMPTY_ORDER_CONTEXT,
            "product_context": _EMPTY_PRODUCT_CONTEXT,
        }

    cust_hash = customer_hash(conv_id, customer_name)
    agg = aggregate_conversation(
        aggregate_source,
        conversation_id=conv_id,
        customer_hash=cust_hash,
    )

    from src.cdp.order_context import (
        fetch_consulting_products,
        fetch_order_context,
        fetch_product_list,
        parse_security_user_id,
    )

    order_ctx = await fetch_order_context(page, conv_id)
    security_uid = parse_security_user_id(conv_id)
    product_ctx = await fetch_consulting_products(page, security_uid)
    if not product_ctx.get("has_product"):
        product_ctx = await fetch_product_list(page, security_uid)

    has_service = any(
        str(m.get("role") or "") in {"service", "seller", "robot"} for m in recent_messages
    )

    return {
        "ok": True,
        "connected": True,
        "logged_in": True,
        "conversation_id": conv_id,
        "customer_name": customer_name or sdk_name,
        "customer_hash": cust_hash,
        "current_customer_question": agg["current_customer_question"],
        "recent_messages": agg["recent_messages"],
        "last_customer_messages": agg["last_customer_messages"],
        "last_service_message": agg["last_service_message"],
        "should_reply": agg["should_reply"],
        "is_first_customer_message": not has_service and bool(agg["current_customer_question"]),
        "order_context": order_ctx,
        "product_context": product_ctx,
        "dom_count": len(dom_messages),
        "sdk_count": dom.get("sdk_count", 0),
        "hook_count": len(hook_messages),
        "ws_count": len(ws_messages),
        "message_sources": {
            "sdk": dom.get("sdk_count", 0),
            "dom": dom.get("dom_count", 0),
            "hook": len(hook_messages),
            "ws": len(ws_messages),
            "merged": len(recent_messages),
        },
        "selector_profile": dom.get("selector_profile"),
    }
