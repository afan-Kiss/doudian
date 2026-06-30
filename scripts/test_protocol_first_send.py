#!/usr/bin/env python3
"""Unit tests for send semantics: auto must not fill_only succeed; WS replay unverified fails."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def test_auto_no_fill_only_success() -> None:
    from src.sender import reply_sender

    page = MagicMock()
    with patch.object(reply_sender, "_send_via_sdk", new=AsyncMock(return_value={"ok": False, "detail": {"reason": "sdk_send_failed"}, "send_mode": "sdk"})):
        with patch.object(reply_sender, "_fill_input", new=AsyncMock(return_value=True)) as fill_mock:
            result = await reply_sender.send_reply_async(
                conversation_id="AQ:test:123",
                customer_hash="hash",
                text="你好",
                mode="auto",
                page=page,
            )
    assert result["sent"] is False
    assert result["ok"] is False
    assert result["filled"] is False
    fill_mock.assert_not_called()


async def test_semi_auto_allows_fill_only() -> None:
    from src.sender import reply_sender

    page = MagicMock()
    with patch.object(reply_sender, "_fill_input", new=AsyncMock(return_value=True)):
        result = await reply_sender.send_reply_async(
            conversation_id="AQ:test:123",
            customer_hash="hash",
            text="你好",
            mode="semi_auto",
            page=page,
        )
    assert result["sent"] is False
    assert result["ok"] is True
    assert result["filled"] is True


async def test_ws_replay_unverified_not_success() -> None:
    from src.sender.api_sender import APISender

    sender = APISender(ROOT / "captures" / "schema")
    sender.last_send_mode = "ws_replay_unverified"
    sender.last_send_detail = {"reason": "ws_replay_unverified", "ack": False, "dom_visible": False}

    assert sender._sdk_send_verified({"ok": True, "capturedCount": 0, "sendDelta": 0, "mode": "mona_im_sendText"}) is False
    assert sender.last_send_mode == "ws_replay_unverified"
    assert sender.last_send_detail.get("reason") == "ws_replay_unverified"


async def test_system_notice_not_pending() -> None:
    from src.reply.multi_conversation_dispatcher import build_pending_queue

    rows = [
        {
            "conversation_id": "sys1",
            "customer_name": "智能客服",
            "last_message_text": "智能客服功能升级",
            "has_unreplied_customer_message": True,
            "is_system_notice": True,
        },
        {
            "conversation_id": "buyer1",
            "customer_name": "买家A",
            "has_unreplied_customer_message": True,
        },
    ]
    queue = build_pending_queue(rows)
    assert len(queue) == 1
    assert queue[0]["conversation_id"] == "buyer1"


def main() -> int:
    asyncio.run(test_auto_no_fill_only_success())
    asyncio.run(test_semi_auto_allows_fill_only())
    asyncio.run(test_ws_replay_unverified_not_success())
    asyncio.run(test_system_notice_not_pending())
    print("protocol-first send semantics: 4/4 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
