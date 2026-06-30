#!/usr/bin/env python3
"""Acceptance tests for multi-conversation scheduling logic."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.cdp.conversation_aggregate import find_pending_customer_messages
from src.cdp.page_action_lock import is_page_busy, page_action_lock
from src.reply.multi_conversation_dispatcher import build_pending_queue


def _msg(role: str, text: str, mid: str = "", time: str = "") -> dict:
    return {"role": role, "text": text, "message_id": mid, "time": time}


def test_scenario_a_customer_last() -> None:
    msgs = [_msg("customer", "发什么快递", "a1")]
    out = find_pending_customer_messages(msgs)
    assert out["should_reply"] is True, out


def test_scenario_b_customer_then_system() -> None:
    msgs = [
        _msg("customer", "在吗", "b1"),
        _msg("system", "欢迎光临"),
        _msg("system", "客服已接入"),
    ]
    out = find_pending_customer_messages(msgs)
    assert out["should_reply"] is True, out


def test_scenario_c_customer_then_service() -> None:
    msgs = [
        _msg("customer", "发什么快递", "c1"),
        _msg("service", "亲，默认中通哦", "c2"),
    ]
    out = find_pending_customer_messages(msgs)
    assert out["should_reply"] is False, out


def test_scenario_d_first_customer() -> None:
    msgs = [_msg("customer", "你好", "d1")]
    out = find_pending_customer_messages(msgs)
    assert out["should_reply"] is True, out


def test_scenario_e_repeat_question_different_time() -> None:
    first = find_pending_customer_messages([_msg("customer", "发什么快递", "e1", "100")])
    second = find_pending_customer_messages([_msg("customer", "发什么快递", "e2", "200")])
    assert first["should_reply"] is True
    assert second["should_reply"] is True
    assert first["latest_customer_message_id"] != second["latest_customer_message_id"]


def test_build_pending_queue_sort() -> None:
    rows = [
        {
            "conversation_id": "a",
            "has_unreplied_customer_message": True,
            "unread": False,
            "closed": False,
            "latest_customer_message_time": "100",
        },
        {
            "conversation_id": "b",
            "has_unreplied_customer_message": True,
            "unread": True,
            "closed": False,
            "latest_customer_message_time": "50",
        },
    ]
    queue = build_pending_queue(rows)
    assert queue[0]["conversation_id"] == "b"


async def test_page_action_lock_serializes() -> None:
    order: list[str] = []

    async def worker(name: str) -> None:
        async with page_action_lock(name):
            order.append(f"{name}-start")
            await asyncio.sleep(0.05)
            order.append(f"{name}-end")

    await asyncio.gather(worker("a"), worker("b"))
    assert order.index("a-start") < order.index("a-end")
    assert order.index("b-start") < order.index("b-end")
    assert not (order[1] == "b-start" and order[0] == "a-start" and order[2] == "a-end")


def main() -> int:
    tests = [
        test_scenario_a_customer_last,
        test_scenario_b_customer_then_system,
        test_scenario_c_customer_then_service,
        test_scenario_d_first_customer,
        test_scenario_e_repeat_question_different_time,
        test_build_pending_queue_sort,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")

    try:
        asyncio.run(test_page_action_lock_serializes())
        print("PASS test_page_action_lock_serializes")
    except Exception as exc:
        failed += 1
        print(f"FAIL test_page_action_lock_serializes: {exc}")

    print(f"\n{'=' * 40}\nfailed={failed}\nbusy={is_page_busy()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
