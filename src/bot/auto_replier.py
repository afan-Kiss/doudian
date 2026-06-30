from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from src.bot.knowledge_base import append_reference_section, build_system_prompt, load_knowledge_text
from src.bot.llm_client import LLMClient
from src.chat.conversation_keys import normalize_route_key
from src.chat.hub import ChatHub
from src.chat.send_service import ConversationTarget, send_text_to_buyer_message
from src.cdp.page_session import PageSession, is_stale_page_error
from src.monitor.inbound_listener import make_dedupe_key
from src.monitor.text_filters import is_meaningless_message


MEDIA_SKIP_RE = re.compile(r"^\[(表情|图片|商品卡片)\]$")
MEDIA_ONLY_SKIP_RE = re.compile(r"^\[(表情|图片)\]$")
PRODUCT_CARD_TEXT = "[商品卡片]"
PRODUCT_CARD_PROMPT = "买家发来了一个商品卡片，请先礼貌打招呼并询问需要什么帮助。"


def normalize_conversation_lock_key(key: str = "") -> str:
    return normalize_route_key(key)


def lock_key_for_target(target: ConversationTarget) -> str:
    route_key = normalize_conversation_lock_key(
        target.conversation_key or target.sdk_conversation_id
    )
    if route_key:
        return route_key
    nick = str(target.nickname or "").strip()
    if nick:
        return f"nick:{nick}"
    return "unknown"


class AutoReplier:
    """Buyer message -> Volcengine Ark -> reply to the same buyer/conversation."""

    def __init__(
        self,
        *,
        hub: ChatHub,
        page_session: PageSession,
        schema_dir: Any,
        llm: LLMClient,
        system_prompt: str,
        max_history: int = 20,
        reply_to_media: bool = False,
        min_reply_interval_sec: float = 2.0,
        debounce_sec: float = 2.5,
        sales_min_rounds: int = 3,
    ) -> None:
        self.hub = hub
        self.page_session = page_session
        self.schema_dir = schema_dir
        self.llm = llm
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.reply_to_media = reply_to_media
        self.min_reply_interval_sec = min_reply_interval_sec
        self.debounce_sec = debounce_sec
        self.sales_min_rounds = max(1, int(sales_min_rounds))
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_reply_at: dict[str, float] = {}
        self._handled_message_ids: set[str] = set()
        self._pending_by_conv: dict[str, list[dict[str, Any]]] = {}
        self._debounce_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_no_route: dict[str, list[dict[str, Any]]] = {}
        self._last_product_card_reply_at: dict[str, float] = {}
        self._llm_gate = asyncio.Semaphore(1)
        self._schedule_lock = asyncio.Lock()
        self._started_at = time.time()

    async def _get_page(self) -> Any:
        return await self.page_session.get_active_feige_page()

    def _message_fingerprint(self, msg: dict[str, Any]) -> str:
        target = ConversationTarget.from_message(msg)
        conv = lock_key_for_target(target)
        mid = self._message_key(msg)
        if mid:
            return f"{conv}|id:{mid}"
        text = str(msg.get("text") or "").strip()
        ts = str(msg.get("timestamp") or msg.get("time") or "")
        return f"{conv}|{text}|{ts}"

    async def mark_startup_history_handled(self, delay_sec: float = 6.0) -> None:
        """Only skip buyer messages that already have a seller reply in hub history."""
        await asyncio.sleep(delay_sec)
        for msg in self.hub.list_messages():
            if str(msg.get("role") or "") != "buyer":
                continue
            if self._buyer_already_answered(msg):
                self._mark_handled(msg)

    def _buyer_already_answered(self, buyer_msg: dict[str, Any]) -> bool:
        target = ConversationTarget.from_message(buyer_msg)
        conv_key = lock_key_for_target(target)
        buyer_ts = self._message_sort_ts(buyer_msg)
        buyer_fp = self._message_fingerprint(buyer_msg)
        for item in self.hub.list_messages(conv_key):
            role = str(item.get("role") or "")
            if role != "seller":
                continue
            if self._message_sort_ts(item) >= buyer_ts - 0.5:
                return True
        return buyer_fp in self._handled_message_ids

    def _message_sort_ts(self, msg: dict[str, Any]) -> float:
        ts = msg.get("timestamp")
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, str):
            try:
                from datetime import datetime

                return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return time.time()

    def _content_dedupe_key(self, msg: dict[str, Any]) -> str:
        return self._message_fingerprint(msg)

    def _has_stable_message_id(self, msg: dict[str, Any]) -> bool:
        for field in ("server_message_id", "client_message_id"):
            if str(msg.get(field) or "").strip():
                return True
        msg_id = str(msg.get("id") or "").strip()
        return bool(msg_id and not msg_id.startswith("pending:"))

    def _mark_handled(self, msg: dict[str, Any]) -> None:
        message_key = self._message_key(msg)
        if message_key:
            self._handled_message_ids.add(message_key)
        fp = self._message_fingerprint(msg)
        if fp:
            self._handled_message_ids.add(fp)

    def _is_already_handled(self, msg: dict[str, Any]) -> bool:
        message_key = self._message_key(msg)
        if message_key and message_key in self._handled_message_ids:
            return True
        fp = self._message_fingerprint(msg)
        return bool(fp and fp in self._handled_message_ids)

    def _enrich_message(self, msg: dict[str, Any], target: ConversationTarget) -> tuple[dict[str, Any], ConversationTarget]:
        route, talk_id = self.hub.resolve_conversation_ids(
            nickname=str(msg.get("nickname") or msg.get("buyer_name") or target.nickname or ""),
            conversation_route=str(msg.get("conversation_route") or target.conversation_route or ""),
            conversation_id=str(msg.get("conversation_id") or target.conversation_id or ""),
        )
        updates: dict[str, Any] = {}
        if route and len(route) >= len(str(msg.get("conversation_route") or "")):
            updates["conversation_route"] = route
        if talk_id:
            updates["conversation_id"] = talk_id
        if updates:
            msg = {**msg, **updates}
            target = ConversationTarget.from_message(msg)
            if route:
                msg["conversation_key"] = normalize_route_key(route)
                target = ConversationTarget(
                    nickname=target.nickname,
                    conversation_key=normalize_route_key(route),
                    conversation_id=talk_id or target.conversation_id,
                    conversation_route=route,
                )
        return msg, target

    async def handle_buyer_message(self, msg: dict[str, Any]) -> None:
        if str(msg.get("role") or "") != "buyer":
            return
        if str(msg.get("kind") or "") == "seller_message":
            return
        if int(msg.get("direction") or 0) == 2:
            return

        target = ConversationTarget.from_message(msg)
        msg, target = self._enrich_message(msg, target)

        if self._is_already_handled(msg):
            return

        if not self._should_consider(msg):
            return

        nickname = self.hub.resolve_nickname(
            nickname=str(msg.get("nickname") or msg.get("buyer_name") or target.nickname or ""),
            conversation_route=str(msg.get("conversation_route") or target.conversation_route or ""),
            conversation_id=str(msg.get("conversation_id") or target.conversation_id or ""),
        )
        if nickname and not target.nickname:
            msg = {**msg, "nickname": nickname, "buyer_name": nickname}
            target = ConversationTarget.from_message(msg)
        if not target.nickname:
            return
        if not target.sdk_conversation_id:
            nick_key = target.nickname or "unknown"
            self._pending_no_route.setdefault(nick_key, []).append(msg)
            self._schedule_no_route_retry(nick_key)
            return

        lock_key = lock_key_for_target(target)

        async with self._schedule_lock:
            self._pending_by_conv.setdefault(lock_key, []).append(msg)
            task = self._debounce_tasks.get(lock_key)
            if task and not task.done():
                return
            self._debounce_tasks[lock_key] = asyncio.create_task(
                self._debounced_reply(lock_key, debounce_sec=self.debounce_sec)
            )

    def _schedule_no_route_retry(self, nick_key: str) -> None:
        task_key = f"route:{nick_key}"
        task = self._debounce_tasks.get(task_key)
        if task and not task.done():
            return
        self._debounce_tasks[task_key] = asyncio.create_task(self._retry_no_route(nick_key))

    async def _retry_no_route(self, nick_key: str) -> None:
        await asyncio.sleep(1.5)
        try:
            pending = self._pending_no_route.pop(nick_key, [])
            for msg in pending:
                await self.handle_buyer_message(msg)
        finally:
            self._debounce_tasks.pop(f"route:{nick_key}", None)

    def _message_key(self, msg: dict[str, Any]) -> str:
        for field in ("server_message_id", "client_message_id", "id"):
            value = str(msg.get(field) or "").strip()
            if value:
                return value
        return make_dedupe_key(msg)

    def _should_consider(self, msg: dict[str, Any]) -> bool:
        if self._is_already_handled(msg):
            return False

        message_key = self._message_key(msg)
        if message_key and message_key in self._handled_message_ids:
            return False

        text = str(msg.get("text") or "").strip()
        nickname = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
        if not text:
            return False
        if is_meaningless_message(text, "buyer", nickname):
            return False
        if MEDIA_ONLY_SKIP_RE.fullmatch(text):
            return False

        if text == PRODUCT_CARD_TEXT:
            now = time.time()
            lock_preview = normalize_conversation_lock_key(
                str(msg.get("conversation_key") or msg.get("conversation_route") or "")
            )
            last_reply = self._last_reply_at.get(lock_preview, 0.0)
            if lock_preview and last_reply > 0:
                last_pc = self._last_product_card_reply_at.get(lock_preview, 0.0)
                if now - last_pc < 120.0:
                    return False

        if nickname and text == nickname:
            return False
        return True

    async def _debounced_reply(self, lock_key: str, *, debounce_sec: float | None = None) -> None:
        await asyncio.sleep(debounce_sec if debounce_sec is not None else self.debounce_sec)
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        failed_msg: dict[str, Any] | None = None
        replied_msg: dict[str, Any] | None = None
        replied_ts = 0.0
        try:
            async with lock:
                pending = self._pending_by_conv.pop(lock_key, [])
                msg = self._pick_latest_message(pending)
                for other in pending:
                    if other is not msg:
                        self._mark_handled(other)
                if not msg:
                    return

                message_key = self._message_key(msg)
                if message_key and message_key in self._handled_message_ids:
                    self._mark_handled(msg)
                    return

                target = ConversationTarget.from_message(msg)
                msg, target = self._enrich_message(msg, target)
                nickname = self._resolve_nickname(msg, target)
                if nickname and not target.nickname:
                    msg = {**msg, "nickname": nickname, "buyer_name": nickname}
                    target = ConversationTarget.from_message(msg)

                text = str(msg.get("text") or "").strip()
                llm_text = PRODUCT_CARD_PROMPT if text == PRODUCT_CARD_TEXT else text

                failed_msg = msg
                replied_msg = msg
                replied_ts = self._message_sort_ts(msg)
                await self._reply_once(msg, target, llm_text=llm_text)
                self._mark_handled(msg)
                if str(msg.get("text") or "").strip() == PRODUCT_CARD_TEXT:
                    pc_key = normalize_conversation_lock_key(
                        target.conversation_key or target.sdk_conversation_id
                    )
                    if pc_key:
                        self._last_product_card_reply_at[pc_key] = asyncio.get_running_loop().time()
        except Exception as exc:
            if "HTTP 429" in str(exc) or "ServerOverloaded" in str(exc):
                pending = self._pending_by_conv.setdefault(lock_key, [])
                if failed_msg and failed_msg not in pending:
                    pending.append(failed_msg)
                async with self._schedule_lock:
                    task = self._debounce_tasks.get(lock_key)
                    if not task or task.done():
                        self._debounce_tasks[lock_key] = asyncio.create_task(
                            self._debounced_reply(lock_key, debounce_sec=8.0)
                        )
        finally:
            self._debounce_tasks.pop(lock_key, None)
            remaining = self._pending_by_conv.pop(lock_key, [])
            newer: list[dict[str, Any]] = []
            for item in remaining:
                if replied_msg and self._message_sort_ts(item) > replied_ts:
                    newer.append(item)
                else:
                    self._mark_handled(item)
            if newer:
                self._pending_by_conv[lock_key] = newer
                async with self._schedule_lock:
                    task = self._debounce_tasks.get(lock_key)
                    if not task or task.done():
                        self._debounce_tasks[lock_key] = asyncio.create_task(
                            self._debounced_reply(lock_key)
                        )

    def _pick_latest_message(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for msg in messages:
            text = str(msg.get("text") or "").strip()
            nickname = str(msg.get("nickname") or msg.get("buyer_name") or "").strip()
            if is_meaningless_message(text, "buyer", nickname):
                continue
            if not self._should_consider(msg):
                continue
            candidates.append(msg)
        if not candidates:
            return None
        return max(candidates, key=self._message_sort_ts)

    def _pick_best_message(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        return self._pick_latest_message(messages)

    async def _reply_once(
        self,
        msg: dict[str, Any],
        target: ConversationTarget,
        *,
        llm_text: str | None = None,
    ) -> None:
        lock_key = lock_key_for_target(target)
        now = asyncio.get_running_loop().time()
        last = self._last_reply_at.get(lock_key, 0.0)
        if now - last < self.min_reply_interval_sec:
            await asyncio.sleep(self.min_reply_interval_sec - (now - last))

        history = self._build_history(target, msg, llm_text=llm_text or str(msg.get("text") or ""))
        async with self._llm_gate:
            reply_text = (await self.llm.chat(history)).strip()
        if not reply_text:
            return

        try:
            for attempt in range(2):
                try:
                    page = await self._get_page()
                    result = await asyncio.wait_for(
                        send_text_to_buyer_message(
                            page=page,
                            schema_dir=self.schema_dir,
                            hub=self.hub,
                            buyer_message=msg,
                            text=reply_text,
                            source="bot_api",
                        ),
                        timeout=90.0,
                    )
                    break
                except Exception as exc:
                    if attempt == 0 and is_stale_page_error(exc):
                        await self.page_session.rebind_feige_page_if_needed()
                        continue
                    raise
            else:
                raise RuntimeError("发送失败：页面引用失效")
        except TimeoutError as exc:
            raise RuntimeError("发送超时（90秒），请检查飞鸽是否已登录且会话可打开") from exc
        self._last_reply_at[lock_key] = asyncio.get_running_loop().time()

    def _resolve_nickname(self, msg: dict[str, Any], target: ConversationTarget) -> str:
        return self.hub.resolve_nickname(
            nickname=str(msg.get("nickname") or msg.get("buyer_name") or target.nickname or ""),
            conversation_route=str(msg.get("conversation_route") or target.conversation_route or ""),
            conversation_id=str(msg.get("conversation_id") or target.conversation_id or ""),
        )

    def _build_history(
        self,
        target: ConversationTarget,
        latest_msg: dict[str, Any],
        *,
        llm_text: str = "",
    ) -> list[dict[str, str]]:
        buyer_hint = (
            f"当前会话买家昵称：{target.nickname}。"
            f"请只回复这位买家，不要提及其他买家。"
        )
        conv_key = target.conversation_key
        items = [
            item
            for item in self.hub.list_messages(conv_key)
            if item.get("conversation_key") == conv_key
        ][-self.max_history :]

        buyer_rounds = sum(
            1
            for item in items
            if str(item.get("role") or "") == "buyer"
            and str(item.get("text") or "").strip()
            and not MEDIA_ONLY_SKIP_RE.fullmatch(str(item.get("text") or "").strip())
        )
        if str(latest_msg.get("role") or "") == "buyer":
            buyer_rounds += 1

        if buyer_rounds <= self.sales_min_rounds:
            buyer_hint += (
                f"\n当前是第{buyer_rounds}轮买家消息，处于闲聊/初步接触阶段："
                "以陪聊、解答、共情为主，不要主动推荐具体商品、发商品卡片引导或催单。"
            )
        else:
            buyer_hint += (
                f"\n已与买家连续聊过{buyer_rounds}轮，可以自然介绍款式、圈口、预算匹配，"
                "但仍不要生硬推销或制造紧迫感。"
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"{self.system_prompt}\n\n{buyer_hint}"},
        ]

        for item in items:
            role = str(item.get("role") or "")
            text = str(item.get("text") or "").strip()
            if not text or MEDIA_SKIP_RE.fullmatch(text):
                continue
            if role == "buyer" and text == PRODUCT_CARD_TEXT:
                text = PRODUCT_CARD_PROMPT
            if role == "buyer":
                messages.append({"role": "user", "content": text})
            elif role == "seller":
                messages.append({"role": "assistant", "content": text})

        latest_text = (llm_text or str(latest_msg.get("text") or "")).strip()
        if latest_text == PRODUCT_CARD_TEXT:
            latest_text = PRODUCT_CARD_PROMPT
        if latest_text and (not messages or messages[-1].get("content") != latest_text):
            messages.append({"role": "user", "content": latest_text})

        return messages


def build_auto_replier(
    *,
    config: dict[str, Any],
    hub: ChatHub,
    page_session: PageSession,
    schema_dir: Any,
) -> AutoReplier | None:
    bot_cfg = config.get("bot") or {}
    if not bot_cfg.get("enabled"):
        return None

    api_key = os.getenv(str(bot_cfg.get("api_key_env") or "BOT_API_KEY"), "").strip()
    if not api_key:
        api_key = str(bot_cfg.get("api_key") or "").strip()
    if not api_key:
        print("Bot auto-reply enabled but BOT_API_KEY is missing.", flush=True)
        return None

    api_key_id = os.getenv(str(bot_cfg.get("api_key_id_env") or "BOT_API_KEY_ID"), "").strip()
    if not api_key_id:
        api_key_id = str(bot_cfg.get("api_key_id") or "").strip()

    model = os.getenv(str(bot_cfg.get("model_env") or "BOT_MODEL"), "").strip()
    if not model:
        model = str(bot_cfg.get("model") or "").strip()
    if not model:
        print("Bot auto-reply enabled but BOT_MODEL is missing. Set 火山方舟 Endpoint ID in .env", flush=True)
        return None

    llm = LLMClient(
        api_key=api_key,
        api_key_id=api_key_id if bot_cfg.get("send_api_key_id_header") else "",
        base_url=str(bot_cfg.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"),
        model=model,
        timeout_sec=float(bot_cfg.get("timeout_sec") or 60),
        temperature=float(bot_cfg.get("temperature") or 0.8),
        top_p=float(bot_cfg.get("top_p") or 0.9),
    )

    system_prompt = str(
        bot_cfg.get("system_prompt")
        or "你是抖店飞鸽客服助手。用简洁、礼貌的中文回复买家问题，不要编造订单或物流信息。"
    )

    prompt_file = str(bot_cfg.get("system_prompt_file") or "").strip()
    if prompt_file:
        root = config.get("_root")
        prompt_path = Path(prompt_file)
        if root and not prompt_path.is_absolute():
            prompt_path = Path(root) / prompt_path
        prompt_text = load_knowledge_text(prompt_path)
        if prompt_text:
            system_prompt = prompt_text
        else:
            print(f"[bot] system prompt file missing or empty: {prompt_path}", flush=True)

    qa_files: list[str] = []
    qa_list = bot_cfg.get("qa_knowledge_files")
    if isinstance(qa_list, list):
        qa_files.extend([str(path).strip() for path in qa_list if str(path).strip()])
    single_qa = str(bot_cfg.get("qa_knowledge_file") or "").strip()
    if single_qa and single_qa not in qa_files:
        qa_files.append(single_qa)

    root = config.get("_root")
    qa_intro = (
        "买家问题与下列 Q 接近时，优先用对应 A 的口径回复，可口语化微调，勿编造未授权承诺。"
    )
    for qa_path in qa_files:
        qa_file = Path(qa_path)
        if root and not qa_file.is_absolute():
            qa_file = Path(root) / qa_file
        qa_text = load_knowledge_text(qa_file)
        if qa_text:
            system_prompt = append_reference_section(
                system_prompt,
                qa_file.stem,
                qa_intro,
                qa_text,
            )
        else:
            print(f"[bot] QA knowledge file missing or empty: {qa_file}", flush=True)

    knowledge_path = str(bot_cfg.get("knowledge_file") or "").strip()
    if knowledge_path:
        root = config.get("_root")
        kb_path = Path(knowledge_path)
        if root and not kb_path.is_absolute():
            kb_path = Path(root) / kb_path
        knowledge_text = load_knowledge_text(kb_path)
        if knowledge_text:
            max_chars = int(bot_cfg.get("knowledge_max_chars") or 28000)
            system_prompt = build_system_prompt(system_prompt, knowledge_text, max_chars=max_chars)
        else:
            print(f"[bot] knowledge file missing or empty: {kb_path}", flush=True)

    return AutoReplier(
        hub=hub,
        page_session=page_session,
        schema_dir=schema_dir,
        llm=llm,
        system_prompt=system_prompt,
        max_history=int(bot_cfg.get("max_history") or 20),
        reply_to_media=bool(bot_cfg.get("reply_to_media")),
        min_reply_interval_sec=float(bot_cfg.get("min_reply_interval_sec") or 2.0),
        debounce_sec=float(bot_cfg.get("debounce_sec") or 2.5),
        sales_min_rounds=int(bot_cfg.get("sales_min_rounds") or 3),
    )
