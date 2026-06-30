from __future__ import annotations

import re
from typing import Any

MAX_CURRENT_CUSTOMER_MESSAGES = 8
RECENT_CONTEXT_LIMIT = 20

SERVICE_ROLES = {"service", "robot", "seller", "客服"}

SYSTEM_NOISE_PATTERN = re.compile(
    r"欢迎光临|有什么可以帮助|客服.*接入|超时未回复|系统关闭|关闭会话|\[客服",
    re.I,
)
EMOJI_ONLY_PATTERN = re.compile(r"^\[[^\]]{1,12}\]$")
WELCOME_PATTERN = re.compile(r"欢迎光临|有什么可以帮助|Hi[,，]?\s*欢迎", re.I)


def _is_substantive_service_reply(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if EMOJI_ONLY_PATTERN.match(t):
        return False
    if WELCOME_PATTERN.search(t):
        return False
    if SYSTEM_NOISE_PATTERN.search(t):
        return False
    if len(t) <= 2:
        return False
    if re.match(r"^[\[【].*[\]】]$", t) and len(t) <= 8:
        return False
    return True


def _strip_trailing_system_noise(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ignore welcome / agent-join system lines after the latest buyer message."""
    result = list(messages)
    while result and result[-1]["role"] == "system":
        text = result[-1]["text"]
        if SYSTEM_NOISE_PATTERN.search(text):
            result.pop()
            continue
        break
    return result


def normalize_role(raw: str) -> str:
    r = raw.strip().lower()
    if r in {"customer", "客户", "buyer"}:
        return "customer"
    if r in {"service", "客服", "seller"}:
        return "service"
    if r in {"robot", "机器人"}:
        return "robot"
    if r in {"system", "系统"}:
        return "system"
    return r


def _ensure_ending_punctuation(text: str) -> str:
    t = text.strip()
    if not t:
        return ""
    if re.search(r"[。！？!?]$", t):
        return t
    tail = (t.split("？")[-1] if "？" in t else t.split("?")[-1] if "?" in t else t)
    if re.search(r"都\d+天|天了$|还没发$|等了", tail):
        return f"{t}。"
    if re.search(r"[吗呢么]$", t) or re.search(r"(是不是|有没有|能不能|可不可以|行不行)$", t):
        return f"{t}？"
    if re.search(r"什么|多少|怎么|为什么", t):
        return f"{t}？"
    return f"{t}。"


def merge_customer_fragments(texts: list[str]) -> str:
    parts = [t.strip() for t in texts if t.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return _ensure_ending_punctuation(parts[0])

    merged = parts[0]
    for part in parts[1:]:
        if re.search(r"[？?。！!；;]$", merged):
            merged += part
            continue
        if re.search(r"[吗呢]$", merged) and not re.search(r"[？?]$", merged) and re.match(
            r"^[有是我你他都可]", part
        ):
            merged += f"？{part}"
            continue
        if re.search(r"[吗呢]$", merged) and re.match(r"^(可以|能|有没有|怎么|都)", part):
            merged += f"？{part}"
            continue
        if len(merged) <= 6 or re.match(r"^(吗|呢|啊|呀|吧|的|了|是不是|有没有|怎么|可以|能)", part):
            merged += part
            continue
        if re.match(r"^[是有能可怎都我你这那]", part) and len(part) <= 12:
            merged += part
            continue
        merged += part if re.search(r"[？?]$", merged) else f"{'，' if not part.startswith('？') else ''}{part}"

    merged = re.sub(r"(发)(都)", r"\1？\2", merged)
    merged = re.sub(r"([吗呢])([有是我你他都可])", r"\1？\2", merged)
    merged = re.sub(r"，+", "，", merged)
    return _ensure_ending_punctuation(merged)


def _parse_time_ms(time: str | None) -> int | None:
    if not time or not str(time).strip():
        return None
    if re.match(r"^\d{1,2}:\d{2}$", str(time).strip()):
        return None
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(str(time).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        raw = str(time).strip()
        if raw.isdigit():
            n = int(raw)
            return n if n > 1_000_000_000_000 else n * 1000
        return None


def _message_sort_key(m: dict[str, Any], index: int) -> tuple[int, int]:
    parsed = _parse_time_ms(str(m.get("time") or ""))
    return (parsed if parsed is not None else index, index)


def _is_ignorable_between(m: dict[str, Any]) -> bool:
    return m["role"] in {"system", "robot"}


def find_pending_customer_messages(
    messages: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find buyer messages after the last service reply; ignore system/card noise."""
    _ = state
    normalized = [
        {
            "role": normalize_role(str(m.get("role") or "customer")),
            "text": str(m.get("text") or "").strip(),
            "time": str(m.get("time") or ""),
            "message_id": str(m.get("message_id") or m.get("id") or ""),
        }
        for m in messages
        if str(m.get("text") or "").strip()
    ]

    if not normalized:
        return {
            "should_reply": False,
            "pending_customer_messages": [],
            "current_customer_question": "",
            "latest_customer_message_id": "",
            "latest_customer_message_time": "",
            "latest_service_message_id": "",
            "latest_service_message_time": "",
            "reason": "no_messages",
        }

    effective = _strip_trailing_system_noise(normalized)

    last_service_index = -1
    last_service: dict[str, Any] | None = None
    for i in range(len(effective) - 1, -1, -1):
        role = effective[i]["role"]
        if role not in SERVICE_ROLES:
            continue
        if role == "robot":
            continue
        if not _is_substantive_service_reply(effective[i]["text"]):
            continue
        last_service_index = i
        last_service = effective[i]
        break

    pending: list[dict[str, Any]] = []
    if last_service_index >= 0:
        for m in effective[last_service_index + 1 :]:
            if _is_ignorable_between(m):
                continue
            if m["role"] == "customer" and m["text"]:
                pending.append(m)
    else:
        for m in effective:
            if _is_ignorable_between(m):
                continue
            if m["role"] == "customer" and m["text"]:
                pending.append(m)

    pending = pending[-MAX_CURRENT_CUSTOMER_MESSAGES:]

    latest_customer: dict[str, Any] | None = None
    for m in reversed(effective):
        if m["role"] == "customer" and m["text"]:
            latest_customer = m
            break

    should_reply = bool(pending)
    reason = "pending_customer_after_last_service" if should_reply else "already_replied"

    if latest_customer and last_service:
        svc_ms = _parse_time_ms(last_service.get("time"))
        cust_ms = _parse_time_ms(latest_customer.get("time"))
        if svc_ms is not None and cust_ms is not None and svc_ms > cust_ms:
            should_reply = False
            pending = []
            reason = "service_after_customer_by_time"
        elif (
            not pending
            and last_service_index >= 0
            and latest_customer
            and effective.index(latest_customer) <= last_service_index
        ):
            should_reply = False
            reason = "already_replied"

    question = merge_customer_fragments([m["text"] for m in pending])
    latest = pending[-1] if pending else (latest_customer or {})

    return {
        "should_reply": should_reply and bool(question),
        "pending_customer_messages": pending,
        "current_customer_question": question,
        "latest_customer_message_id": str(latest.get("message_id") or ""),
        "latest_customer_message_time": str(latest.get("time") or ""),
        "latest_service_message_id": str((last_service or {}).get("message_id") or ""),
        "latest_service_message_time": str((last_service or {}).get("time") or ""),
        "reason": reason if should_reply else reason,
    }


def aggregate_conversation(
    messages: list[dict[str, Any]],
    *,
    conversation_id: str = "",
    customer_hash: str = "",
    reply_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = [
        {
            "role": normalize_role(str(m.get("role") or "customer")),
            "text": str(m.get("text") or "").strip(),
            "time": str(m.get("time") or ""),
            "message_id": str(m.get("message_id") or m.get("id") or ""),
        }
        for m in messages
        if str(m.get("text") or "").strip()
    ]

    recent_messages = normalized[-RECENT_CONTEXT_LIMIT:]
    pending_info = find_pending_customer_messages(normalized, reply_state)

    last_service_message = ""
    for m in reversed(normalized):
        if m["role"] in SERVICE_ROLES and _is_substantive_service_reply(m["text"]):
            last_service_message = m["text"]
            break

    return {
        "conversation_id": conversation_id,
        "customer_hash": customer_hash,
        "current_customer_question": pending_info["current_customer_question"],
        "recent_messages": recent_messages,
        "last_service_message": last_service_message,
        "last_customer_messages": pending_info["pending_customer_messages"],
        "should_reply": pending_info["should_reply"],
        "latest_customer_message_id": pending_info["latest_customer_message_id"],
        "latest_customer_message_time": pending_info["latest_customer_message_time"],
        "has_unreplied_customer_message": pending_info["should_reply"],
        "pending_reason": pending_info["reason"],
    }
