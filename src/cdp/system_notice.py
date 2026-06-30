from __future__ import annotations

import re
from typing import Any

SYSTEM_NOTICE_NAME_RE = re.compile(
    r"智能客服|系统通知|平台通知|服务通知|客服助手|飞鸽助手|抖店助手|官方通知"
)
SYSTEM_NOTICE_TEXT_RE = re.compile(
    r"智能客服功能升级|功能升级|系统通知|平台通知|官方通知|服务通知|无需回复"
)


def is_system_notice_conversation(conv: dict[str, Any]) -> bool:
    if conv.get("is_system_notice"):
        return True
    name = str(conv.get("buyer_name") or conv.get("customer_name") or "").strip()
    last_text = str(
        conv.get("latest_customer_message_text")
        or conv.get("last_message_text")
        or ""
    ).strip()
    conv_id = str(conv.get("conversation_id") or "")
    if conv_id.startswith("dom:智能客服"):
        return True
    if SYSTEM_NOTICE_NAME_RE.search(name):
        return True
    if SYSTEM_NOTICE_TEXT_RE.search(last_text):
        return True
    return False


def apply_system_notice_fields(conv: dict[str, Any]) -> dict[str, Any]:
    if not is_system_notice_conversation(conv):
        return conv
    out = dict(conv)
    out["is_system_notice"] = True
    out["has_unreplied_customer_message"] = False
    out["pending_customer_count"] = 0
    out["skip_reason"] = "系统通知，不需要回复"
    return out
