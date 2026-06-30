from __future__ import annotations

import argparse
import asyncio
import signal

import uvicorn

from src.browser.launcher import BrowserLauncher
from src.bot.auto_replier import build_auto_replier
from src.chat.hub import ChatHub
from src.config import load_config
from src.chat.server import ChatServerState, create_app
from src.utils.chat_log import init_chat_log, log_console
from src.monitor.cdp_client import CDPMonitor
from src.monitor.inbound_listener import InboundListener
from src.monitor.page_inbound_poller import PageInboundPoller
from src.monitor.text_filters import is_meaningless_message
from src.utils.stdio_utf8 import setup_utf8_stdio


async def run_chat_ui(args: argparse.Namespace) -> int:
    setup_utf8_stdio()
    config = load_config()
    capture_dir = config["_capture_dir"]
    init_chat_log(capture_dir / "chat_ui.log")
    monitor_cfg = config["monitor"]
    inbound_cfg = config.get("inbound") or {}
    chat_cfg = config.get("chat_ui") or {}

    roles_cfg = chat_cfg.get("roles") or inbound_cfg.get("roles") or ["buyer", "seller", "system"]
    roles = set(args.roles.split(",")) if args.roles else set(roles_cfg)
    dedupe_ms = args.dedupe_ms or int(inbound_cfg.get("dedupe_window_ms") or 4000)
    host = args.host or str(chat_cfg.get("host") or "127.0.0.1")
    port = args.port or int(chat_cfg.get("port") or 4713)

    hub = ChatHub()
    loop = asyncio.get_running_loop()

    async def on_message_async(msg: dict) -> None:
        role = str(msg.get("role") or "")
        nickname = str(msg.get("nickname") or msg.get("buyer_name") or "-")
        text = str(msg.get("text") or "")
        if is_meaningless_message(text, role, nickname):
            return
        await hub.publish(msg)

    def on_message(msg: dict) -> None:
        loop.create_task(on_message_async(msg))

    launcher = BrowserLauncher(config)
    page = await launcher.start(open_url=config["urls"]["feige"])
    await page.wait_for_timeout(args.page_wait * 1000)

    from src.sender import reply_sender

    reply_sender.set_page(page)

    inbound = InboundListener(
        roles=roles,
        dedupe_window_ms=dedupe_ms,
        on_message=on_message,
        console_log=False,
    )

    if not launcher.context:
        raise RuntimeError("Browser context not available")

    monitor = CDPMonitor(
        context=launcher.context,
        capture_dir=capture_dir,
        filter_hosts=monitor_cfg["filter_hosts"],
        save_raw=monitor_cfg.get("save_raw", True) and not args.no_save,
        console_log=False,
        inbound_listener=inbound,
    )
    await monitor.start()

    page_poller = PageInboundPoller(page, inbound, hub=hub, interval_sec=1.0)
    await page_poller.start()

    server_state = ChatServerState(
        hub=hub,
        launcher=launcher,
        schema_dir=capture_dir / "schema",
    )

    auto_replier = None if getattr(args, "no_bot", False) else build_auto_replier(
        config=config,
        hub=hub,
        page=page,
        schema_dir=capture_dir / "schema",
    )
    if auto_replier:
        hub.register_bot_handler(auto_replier.handle_buyer_message)
        asyncio.create_task(auto_replier.mark_startup_history_handled())
    app = create_app(server_state)
    uvicorn_config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(uvicorn_config)

    role_text = "、".join(
        {"buyer": "买家", "seller": "卖家", "system": "系统"}.get(role, role) for role in sorted(roles)
    )
    log_console("=" * 60)
    log_console("飞鸽聊天界面已启动")
    log_console("=" * 60)
    log_console(f"聊天界面: http://{host}:{port}")
    log_console(f"监听角色: {role_text}")
    log_console(f"Chrome CDP: http://127.0.0.1:{launcher.debug_port}")
    log_console(f"飞鸽地址: {config['urls']['feige']}")
    log_console(f"运行日志: {capture_dir / 'chat_ui.log'}（仅记录买家/卖家消息）")
    if auto_replier:
        log_console("API 自动回复: 已启用（买家消息 -> LLM -> SDK 发送）")
    else:
        log_console("API 自动回复: 未启用（检查 bot.enabled 与 BOT_API_KEY / BOT_MODEL）")
    log_console("请在飞鸽中保持登录。买家消息在左，店铺回复在右。")
    log_console("按 Ctrl+C 结束。")
    log_console("=" * 60)

    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: request_stop())

    if launcher.context:
        launcher.context.on("close", lambda _: request_stop())

    serve_task = asyncio.create_task(server.serve())
    await stop_event.wait()
    server.should_exit = True
    await serve_task

    print(f"\n聊天界面结束，共识别 {len(inbound.messages)} 条消息。")
    await page_poller.stop()
    await monitor.stop()
    await launcher.stop()
    return 0
