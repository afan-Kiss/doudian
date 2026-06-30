from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PRODUCT_WARN_PHRASES = [
    "绝对真",
    "百分百天然",
    "全店都是",
    "一定是羊脂玉",
    "假一赔十",
    "肯定没有瑕疵",
    "不用看证书",
    "保证没有任何问题",
]

ORDER_WARN_PHRASES = [
    "已经发货",
    "已经退款",
    "已经签收",
    "马上到账",
    "物流已经更新",
    "平台一定支持",
]


@dataclass
class AutoSendGateResult:
    allowed: bool
    blocked: bool
    hard_block_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    policy: str = "direct"
    safety_pass: bool = True
    reason: str = ""
    blocked_phrases: list[str] = field(default_factory=list)


def _char_count(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def detect_auto_send_forbidden(reply: str) -> list[str]:
    hits: list[str] = []
    for phrase in PRODUCT_WARN_PHRASES + ORDER_WARN_PHRASES:
        if phrase in reply:
            hits.append(phrase)
    return hits


def _collect_warnings(
    *,
    reply: str,
    intent: str,
    reply_source: str,
    reply_blocked: bool,
    forbidden_hit: list[str] | None,
) -> list[str]:
    warnings: list[str] = []
    forbidden_hit = forbidden_hit or []
    detected = detect_auto_send_forbidden(reply)

    for p in forbidden_hit:
        warnings.append(f"forbidden_hit: {p}")
    for p in detected:
        if not any(p in w for w in warnings):
            warnings.append(f"敏感词: {p}")
    if reply_blocked:
        warnings.append("relay 标记 reply_blocked")
    if reply_source == "fallback_template":
        warnings.append("reply_source=fallback_template")
    if intent in ("unknown", ""):
        warnings.append("intent=unknown")
    if re.search(r"<think>", reply, re.I):
        warnings.append("含内部思考标记")
    length = _char_count(reply)
    if length < 10 or length > 120:
        warnings.append(f"回复长度 {length} 字（建议 10～120）")
    if re.search(r"亲，这款和田玉|证书编号可随货|请您放心，感谢支持", reply):
        warnings.append("AI 味偏重")
    return warnings


def evaluate_auto_send_gate(
    *,
    ok: bool = True,
    reply: str,
    intent: str = "general",
    reply_source: str = "ai_worker",
    reply_blocked: bool = False,
    forbidden_hit: list[str] | None = None,
    should_reply: bool = True,
    already_replied: bool = False,
    test_mode: bool = False,
    **_kwargs: Any,
) -> AutoSendGateResult:
    trimmed = reply.strip()
    warnings = _collect_warnings(
        reply=trimmed,
        intent=intent,
        reply_source=reply_source,
        reply_blocked=reply_blocked,
        forbidden_hit=forbidden_hit,
    )
    blocked_phrases = detect_auto_send_forbidden(trimmed)

    def fail(reason: str) -> AutoSendGateResult:
        return AutoSendGateResult(
            allowed=False,
            blocked=True,
            hard_block_reason=reason,
            warnings=warnings,
            policy="direct",
            safety_pass=False,
            reason=reason,
            blocked_phrases=blocked_phrases,
        )

    if not trimmed:
        return fail("回复为空")
    if not should_reply:
        return fail("当前没有待回复的买家消息")
    if already_replied:
        return fail("这组客户消息已经发送过，已阻止重复发送。")
    if reply_source == "manual_test" and not test_mode:
        return fail("测试文本不允许自动发送")

    hint = f"已发送，提示：{'；'.join(warnings)}" if warnings else ""
    return AutoSendGateResult(
        allowed=True,
        blocked=False,
        hard_block_reason="",
        warnings=warnings,
        policy="direct",
        safety_pass=True,
        reason=hint,
        blocked_phrases=[],
    )
