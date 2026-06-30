#!/usr/bin/env python3
"""Auto-send gate unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.safety.auto_send_gate import evaluate_auto_send_gate, detect_auto_send_forbidden


def test_product_low_risk_allowed() -> None:
    r = evaluate_auto_send_gate(
        ok=True,
        reply="亲，这款有鉴定证书，支持复检，您可以放心看证书详情。",
        intent="product_authenticity",
    )
    assert r.allowed, r.reason


def test_order_verify_allowed() -> None:
    r = evaluate_auto_send_gate(
        ok=True,
        reply="亲，我帮您核实一下物流，您发一下订单号或下单时间。",
        intent="order_logistics",
    )
    assert r.allowed, r.reason


def test_order_confirm_blocked() -> None:
    r = evaluate_auto_send_gate(
        ok=True,
        reply="亲，您的订单已经发货了，请留意物流信息。",
        intent="order_logistics",
    )
    assert not r.allowed


def test_forbidden_product_blocked() -> None:
    reply = "亲，这款百分百天然，绝对真，您放心买。"
    assert detect_auto_send_forbidden(reply)
    r = evaluate_auto_send_gate(ok=True, reply=reply, intent="product_authenticity")
    assert not r.allowed


def main() -> int:
    test_product_low_risk_allowed()
    test_order_verify_allowed()
    test_order_confirm_blocked()
    test_forbidden_product_blocked()
    print("python auto-send gate tests: 4/4 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
