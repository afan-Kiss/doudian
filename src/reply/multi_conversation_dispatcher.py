from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from src.cdp.conversation_list import _scan_conversation_list_impl, _switch_conversation_impl
from src.cdp.current_conversation import read_current_conversation
from src.cdp.order_context import fetch_order_context, parse_security_user_id
from src.cdp.page_action_lock import page_action_lock
from src.cdp.system_notice import is_system_notice_conversation

logger = logging.getLogger("multi_dispatch")


def _parse_sort_time(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if raw.isdigit():
        n = int(raw)
        return float(n if n > 1_000_000_000_000 else n * 1000)
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return 0.0


def build_pending_queue(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = [
        c
        for c in conversations
        if c.get("has_unreplied_customer_message")
        and not c.get("closed")
        and not is_system_notice_conversation(c)
    ]

    def sort_key(c: dict[str, Any]) -> tuple:
        return (
            0 if c.get("unread") else 1,
            0 if c.get("has_unreplied_customer_message") else 1,
            -_parse_sort_time(str(c.get("latest_customer_message_time") or "")),
            1 if c.get("closed") else 0,
        )

    pending.sort(key=sort_key)
    return pending


@dataclass
class DispatcherStatus:
    running: bool = False
    pending_count: int = 0
    handoff_count: int = 0
    processing_buyer: str = ""
    last_scan_time: str = ""
    last_refresh_time: str = ""
    last_round_found: int = 0
    last_error: str = ""
    ignored_system_notice_count: int = 0
    recent_records: list[dict[str, str]] = field(default_factory=list)


class MultiConversationDispatcher:
    """Scan all conversations and process pending replies one by one."""

    def __init__(
        self,
        *,
        page_session: Any,
        hub: Any,
        schema_dir: Any,
        interval_sec: float = 5.0,
        max_per_round: int = 3,
        on_process: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.page_session = page_session
        self.hub = hub
        self.schema_dir = schema_dir
        self.interval_sec = interval_sec
        self.max_per_round = max_per_round
        self.on_process = on_process
        self.status = DispatcherStatus()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._handoff_ids: set[str] = set()

    def get_status(self) -> dict[str, Any]:
        st = self.status
        return {
            "running": st.running,
            "pending_count": st.pending_count,
            "handoff_count": st.handoff_count,
            "processing_buyer": st.processing_buyer,
            "last_scan_time": st.last_scan_time,
            "last_refresh_time": st.last_refresh_time,
            "last_round_found": st.last_round_found,
            "last_error": st.last_error,
            "ignored_system_notice_count": st.ignored_system_notice_count,
            "recent_records": list(st.recent_records[-20:]),
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.status.running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        self.status.running = False
        if self._task:
            await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_round()
            except Exception as exc:
                self.status.last_error = str(exc)
                logger.exception("dispatcher round failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                continue

    async def run_round(self) -> None:
        async with page_action_lock("dispatcher_scan"):
            page = await self.page_session.get_active_feige_page()
            scan = await _scan_conversation_list_impl(page)
        if not scan.get("ok"):
            self.status.last_error = str(scan.get("reason") or "scan_failed")
            return

        conversations = scan.get("conversations") or []
        ignored = [c for c in conversations if is_system_notice_conversation(c)]
        self.status.ignored_system_notice_count = len(ignored)
        for conv in ignored:
            buyer = str(conv.get("customer_name") or conv.get("buyer_name") or "系统通知")
            self._append_record(
                {
                    "time": time.strftime("%H:%M:%S"),
                    "buyer": buyer,
                    "question": str(conv.get("last_message_text") or "")[:80],
                    "order": "",
                    "express": "",
                    "reply": "",
                    "result": "已忽略",
                    "reason": "系统通知，不需要回复",
                }
            )

        queue = build_pending_queue(conversations)
        self.status.pending_count = len(queue)
        self.status.last_scan_time = time.strftime("%H:%M")
        self.status.last_round_found = len(queue)
        self.status.last_error = ""

        handled = 0
        for conv in queue:
            if handled >= self.max_per_round:
                break
            cid = str(conv.get("conversation_id") or "")
            if cid in self._handoff_ids:
                continue
            await self.process_one_conversation(conv)
            handled += 1

    async def process_one_conversation(self, conv: dict[str, Any]) -> dict[str, Any]:
        cid = str(conv.get("conversation_id") or "")
        buyer = str(conv.get("customer_name") or "未知买家")
        self.status.processing_buyer = buyer
        record = {
            "time": time.strftime("%H:%M:%S"),
            "buyer": buyer,
            "question": str(conv.get("latest_customer_message_text") or conv.get("last_message_text") or ""),
            "order": "",
            "express": "",
            "reply": "",
            "result": "",
            "reason": "",
        }
        try:
            async with page_action_lock("process_conversation"):
                page = await self.page_session.get_active_feige_page()
                switched = await _switch_conversation_impl(
                    page,
                    cid,
                    customer_name=buyer,
                )
                if not switched.get("verified"):
                    record["result"] = "稍后重试"
                    record["reason"] = "切换会话失败，稍后重试"
                    self._append_record(record)
                    return {"ok": False, "retry": True, "reason": record["reason"]}

                detail = await read_current_conversation(page, self.hub)
                if not detail.get("should_reply"):
                    record["result"] = "跳过"
                    record["reason"] = "无需回复"
                    self._append_record(record)
                    return {"ok": True, "skipped": True}

                order_ctx = detail.get("order_context") or {}
                expected_uid = parse_security_user_id(cid)
                order_uid = str(order_ctx.get("security_user_id") or "")
                if expected_uid and order_uid and expected_uid != order_uid:
                    record["result"] = "人工处理"
                    record["reason"] = "订单上下文不匹配"
                    self._handoff_ids.add(cid)
                    self.status.handoff_count = len(self._handoff_ids)
                    self._append_record(record)
                    return {"ok": False, "handoff": True, "reason": "order_mismatch"}

                record["order"] = str((order_ctx.get("latest_order") or {}).get("order_id") or "")
                record["express"] = str((order_ctx.get("latest_order") or {}).get("express_company") or "")

                if self.on_process:
                    outcome = await self.on_process(conv, detail)
                    record["reply"] = str(outcome.get("reply") or "")[:80]
                    record["result"] = str(outcome.get("result") or outcome.get("action") or "")
                    record["reason"] = str(outcome.get("reason") or "")
                    self._append_record(record)
                    return outcome

                record["result"] = "已扫描"
                record["reason"] = "等待助手发送"
                self._append_record(record)
                return {"ok": True, "scanned": True, "detail": detail}
        finally:
            self.status.processing_buyer = ""

    def _append_record(self, record: dict[str, str]) -> None:
        self.status.recent_records.append(record)
        if len(self.status.recent_records) > 50:
            self.status.recent_records = self.status.recent_records[-50:]

    def note_refresh(self) -> None:
        self.status.last_refresh_time = time.strftime("%H:%M")
