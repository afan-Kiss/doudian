from __future__ import annotations

import re
from typing import Any

MAX_CURRENT_CUSTOMER_MESSAGES = 8
RECENT_CONTEXT_LIMIT = 20
CURRENT_MESSAGE_TIME_WINDOW_MINUTES = 5

SERVICE_ROLES = {"service", "robot", "seller", "客服"}

SYSTEM_NOISE_PATTERN = re.compile(
    r"欢迎光临|有什么可以帮助|客服.*接入|超时未回复|系统关闭|关闭会话|\[客服",
    re.I,
)


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
    # DOM often gives HH:MM only; treat as recent
    if re.match(r"^\d{1,2}:\d{2}$", str(time).strip()):
        return None
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(str(time).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def aggregate_conversation(
    messages: list[dict[str, Any]],
    *,
    conversation_id: str = "",
    customer_hash: str = "",
) -> dict[str, Any]:
    normalized = [
        {
            "role": normalize_role(str(m.get("role") or "customer")),
            "text": str(m.get("text") or "").strip(),
            "time": str(m.get("time") or ""),
            "message_id": str(m.get("message_id") or ""),
        }
        for m in messages
        if str(m.get("text") or "").strip()
    ]

    recent_messages = normalized[-RECENT_CONTEXT_LIMIT:]
    effective = _strip_trailing_system_noise(normalized)
    if not effective:
        return {
            "conversation_id": conversation_id,
            "customer_hash": customer_hash,
            "current_customer_question": "",
            "recent_messages": recent_messages,
            "last_service_message": "",
            "last_customer_messages": [],
            "should_reply": False,
        }

    last = effective[-1]
    if last["role"] != "customer":
        last_service = next((m for m in reversed(effective) if m["role"] in SERVICE_ROLES), None)
        return {
            "conversation_id": conversation_id,
            "customer_hash": customer_hash,
            "current_customer_question": "",
            "recent_messages": recent_messages,
            "last_service_message": last_service["text"] if last_service else "",
            "last_customer_messages": [],
            "should_reply": False,
        }

    last_service_index = -1
    for i in range(len(effective) - 2, -1, -1):
        if effective[i]["role"] in SERVICE_ROLES:
            last_service_index = i
            break

    if last_service_index >= 0:
        block = effective[last_service_index + 1 :]
        last_customer_messages = [
            m for m in block if m["role"] == "customer" and m["text"]
        ]
    else:
        pending: list[dict[str, Any]] = []
        for m in reversed(effective):
            if m["role"] in SERVICE_ROLES:
                break
            if m["role"] in {"system", "robot"}:
                continue
            if m["role"] == "customer" and m["text"]:
                pending.insert(0, m)
            if len(pending) >= MAX_CURRENT_CUSTOMER_MESSAGES:
                break
        last_customer_messages = pending

    last_customer_messages = last_customer_messages[-MAX_CURRENT_CUSTOMER_MESSAGES:]
    question = merge_customer_fragments([m["text"] for m in last_customer_messages])
    last_service_message = effective[last_service_index]["text"] if last_service_index >= 0 else ""

    return {
        "conversation_id": conversation_id,
        "customer_hash": customer_hash,
        "current_customer_question": question,
        "recent_messages": recent_messages,
        "last_service_message": last_service_message,
        "last_customer_messages": last_customer_messages,
        "should_reply": bool(question),
    }
