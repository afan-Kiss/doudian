from __future__ import annotations

import logging
from typing import Any

from src.cdp.conversation_aggregate import aggregate_conversation, find_pending_customer_messages
from src.cdp.conversation_list import _switch_conversation_impl
from src.cdp.current_conversation import read_current_conversation
from src.cdp.order_context import (
    fetch_consulting_products,
    fetch_order_context_protocol_only,
    fetch_product_list,
    parse_conversation_short_id,
    parse_security_user_id,
)
from src.cdp.page_action_lock import page_action_lock
from src.cdp.system_notice import is_system_notice_conversation
from src.sender.api_sender import APISender
from src.sender.frame_context import find_im_frame
from src.sender.send_verifier import SendVerifier

logger = logging.getLogger("protocol_first")

READ_SDK_MESSAGES_JS = """
(conversationId) => {
  const out = { ok: false, messages: [], conversation_id: conversationId || "" };
  if (!conversationId) return { ...out, reason: "missing_conversation_id" };
  let store = null;
  try {
    window.__monaGlobalStore?.getData?.("initContextData")?.doAction?.((s) => { store = s; });
  } catch (e) {}
  if (!store) return { ...out, reason: "store_unavailable" };

  const mapSdk = (m, idx) => ({
    role: m?.isFromMe || m?.fromMe ? "service" : "customer",
    text: String(m?.content || m?.text || m?.message || "").trim(),
    time: String(m?.createTime || m?.time || m?.timestamp || ""),
    message_id: String(m?.serverId || m?.messageId || m?.id || idx),
  });

  const msgMap = store?.conversationsInfo?.messagesByConversationId;
  let raw = null;
  if (msgMap) {
    if (typeof msgMap.get === "function") raw = msgMap.get(conversationId);
    else raw = msgMap[conversationId];
  }
  if (Array.isArray(raw)) {
    out.messages = raw.map(mapSdk).filter((m) => m.text);
    out.ok = out.messages.length > 0;
    out.source = "sdk-messagesByConversationId";
    return out;
  }
  if (raw && typeof raw === "object") {
    const list = raw.messages || raw.list || raw.items;
    if (Array.isArray(list)) {
      out.messages = list.map(mapSdk).filter((m) => m.text);
      out.ok = out.messages.length > 0;
      out.source = "sdk-messagesByConversationId.object";
      return out;
    }
  }
  return { ...out, reason: "sdk_messages_missing" };
}
"""


def _conv_security_user_id(conv: dict[str, Any]) -> str:
    cid = str(conv.get("conversation_id") or "")
    uid = str(conv.get("security_user_id") or conv.get("securityUserId") or "")
    if uid:
        return uid
    return parse_security_user_id(cid)


def _conv_short_id(conv: dict[str, Any]) -> str:
    return parse_conversation_short_id(
        str(conv.get("conversation_short_id") or conv.get("talk_id") or "")
    )


async def read_sdk_messages(page: Any, conversation_id: str) -> dict[str, Any]:
    im = await find_im_frame(page)
    result = await im.evaluate(READ_SDK_MESSAGES_JS, conversation_id)
    return result if isinstance(result, dict) else {"ok": False, "messages": []}


async def build_protocol_context(page: Any, conv: dict[str, Any]) -> dict[str, Any]:
    if is_system_notice_conversation(conv):
        return {"ok": False, "reason": "system_notice", "should_reply": False}

    cid = str(conv.get("conversation_id") or "").strip()
    buyer = str(conv.get("customer_name") or conv.get("buyer_name") or "")
    security_uid = _conv_security_user_id(conv)
    short_id = _conv_short_id(conv)

    if not cid:
        return {"ok": False, "reason": "missing_conversation_id", "needs_ui_fallback": True}

    sdk = await read_sdk_messages(page, cid)
    messages = sdk.get("messages") or []
    pending = find_pending_customer_messages(messages)
    scan_text = str(
        conv.get("latest_customer_message_text") or conv.get("last_message_text") or ""
    ).strip()

    if not pending.get("should_reply") and scan_text and conv.get("has_unreplied_customer_message"):
        pending = {
            "should_reply": True,
            "current_customer_question": scan_text,
            "latest_customer_message_id": str(conv.get("latest_customer_message_id") or ""),
            "latest_customer_message_time": str(conv.get("latest_customer_message_time") or ""),
            "pending_customer_messages": [{"role": "customer", "text": scan_text}],
        }

    if not pending.get("should_reply"):
        return {
            "ok": True,
            "should_reply": False,
            "conversation_id": cid,
            "customer_name": buyer,
            "reason": pending.get("reason") or "no_pending",
            "message_source": sdk.get("source") or "scan",
        }

    order_ctx = await fetch_order_context_protocol_only(
        page,
        conversation_id=cid,
        security_user_id=security_uid,
        conversation_short_id=short_id,
    )
    product_ctx = await fetch_consulting_products(page, security_uid)
    if not product_ctx.get("has_product"):
        product_ctx = await fetch_product_list(page, security_uid)

    agg = aggregate_conversation(
        messages,
        conversation_id=cid,
        customer_hash=str(conv.get("customer_hash") or ""),
    )
    agg["order_context"] = order_ctx
    agg["product_context"] = product_ctx
    agg["conversation_id"] = cid
    agg["customer_name"] = buyer
    agg["security_user_id"] = security_uid
    agg["message_source"] = sdk.get("source") or ("scan" if scan_text else "none")
    agg["protocol_first"] = True
    agg["needs_ui_fallback"] = not bool(sdk.get("ok")) and not scan_text
    if not security_uid:
        agg["needs_ui_fallback"] = True
    return {"ok": True, **agg}


async def send_via_sdk_verified(
    page: Any,
    *,
    conversation_id: str,
    text: str,
    schema_dir: Any,
) -> dict[str, Any]:
    im = await find_im_frame(page)
    sender = APISender(schema_dir)
    verifier = SendVerifier()
    stats_before = await verifier.ws_send_stats(im)

    success = await sender.send(page, text, conversation_id or None)
    detail = dict(sender.last_send_detail or {})
    mode = str(sender.last_send_mode or detail.get("mode") or "")

    if not success:
        reason = str(detail.get("reason") or mode or "sdk_send_failed")
        if mode == "ws_replay_unverified" or reason == "ws_replay_unverified":
            reason = "ws_replay_unverified"
        return {
            "ok": False,
            "sent": False,
            "mode": mode,
            "reason": reason,
            "detail": detail,
        }

    ack = await verifier.wait_for_server_ack(
        im,
        int(stats_before.get("recvCount") or 0),
        timeout_ms=6000,
    )
    visible = False
    if not ack:
        visible = await verifier.message_visible(page, text)

    verified = bool(ack or detail.get("capturedCount") or detail.get("sendDelta"))
    if not verified and not visible:
        return {
            "ok": False,
            "sent": False,
            "mode": mode,
            "reason": "send_unverified",
            "detail": detail,
        }

    return {
        "ok": True,
        "sent": True,
        "mode": mode,
        "detail": detail,
        "verified_by": "ack" if ack else ("ws_capture" if verified else "dom_visible"),
    }


async def process_conversation_protocol_first(
    page: Any,
    conv: dict[str, Any],
    *,
    schema_dir: Any,
    hub: Any = None,
    reply_text: str = "",
) -> dict[str, Any]:
    """Protocol-first: SDK messages + fetch APIs, no UI switch unless fallback needed."""
    ctx = await build_protocol_context(page, conv)
    if not ctx.get("ok"):
        return {**ctx, "action": "skip"}

    if not ctx.get("should_reply"):
        return {
            "ok": True,
            "action": "skip",
            "skipped": True,
            "reason": ctx.get("reason") or "should_reply_false",
            "detail": ctx,
        }

    if ctx.get("needs_ui_fallback"):
        return {
            "ok": False,
            "needs_ui_fallback": True,
            "reason": ctx.get("reason") or "protocol_context_incomplete",
            "detail": ctx,
        }

    if not reply_text.strip():
        return {
            "ok": True,
            "action": "context_ready",
            "detail": ctx,
            "protocol_first": True,
        }

    send_out = await send_via_sdk_verified(
        page,
        conversation_id=str(ctx.get("conversation_id") or ""),
        text=reply_text.strip(),
        schema_dir=schema_dir,
    )
    if send_out.get("sent"):
        return {
            "ok": True,
            "action": "sent",
            "sent": True,
            "detail": ctx,
            "send": send_out,
            "protocol_first": True,
        }

    return {
        "ok": False,
        "sent": False,
        "needs_ui_fallback": True,
        "reason": send_out.get("reason") or "send_failed",
        "detail": ctx,
        "send": send_out,
    }


async def process_conversation_ui_fallback(
    page: Any,
    conv: dict[str, Any],
    *,
    hub: Any,
) -> dict[str, Any]:
    """UI fallback: switch session, read DOM, validate buyer/message match."""
    cid = str(conv.get("conversation_id") or "")
    buyer = str(conv.get("customer_name") or conv.get("buyer_name") or "")
    expected_text = str(
        conv.get("latest_customer_message_text") or conv.get("last_message_text") or ""
    ).strip()
    expected_uid = _conv_security_user_id(conv)

    async with page_action_lock("ui_fallback_switch"):
        switched = await _switch_conversation_impl(page, cid, customer_name=buyer)
        if not switched.get("verified"):
            return {
                "ok": False,
                "retry": True,
                "reason": "switch_failed",
                "ui_fallback": True,
            }

        detail = await read_current_conversation(page, hub)
        actual_name = str(detail.get("customer_name") or "")
        if buyer and actual_name and buyer not in actual_name and actual_name not in buyer:
            return {
                "ok": False,
                "retry": True,
                "reason": "buyer_name_mismatch",
                "ui_fallback": True,
            }

        question = str(detail.get("current_customer_question") or "").strip()
        if expected_text and question and expected_text not in question and question not in expected_text:
            return {
                "ok": False,
                "retry": True,
                "reason": "message_mismatch",
                "ui_fallback": True,
            }

        order_uid = str((detail.get("order_context") or {}).get("security_user_id") or "")
        if expected_uid and order_uid and expected_uid != order_uid:
            return {
                "ok": False,
                "handoff": True,
                "reason": "order_uid_mismatch",
                "ui_fallback": True,
            }

        detail["ui_fallback"] = True
        detail["protocol_first"] = False
        return {"ok": True, "detail": detail, "ui_fallback": True}


def append_protocol_record(status_holder: Any, record: dict[str, str]) -> None:
    if hasattr(status_holder, "recent_records"):
        status_holder.recent_records.append(record)
        if len(status_holder.recent_records) > 50:
            status_holder.recent_records = status_holder.recent_records[-50:]
