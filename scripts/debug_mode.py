#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyzer.parser import MessageParser
from src.analyzer.schema_dump import SchemaDumper
from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.monitor.cdp_client import CDPMonitor
from src.monitor.inbound_listener import InboundListener


async def run_debug_mode() -> None:
    config = load_config()
    capture_dir = config["_capture_dir"]
    monitor_cfg = config["monitor"]
    inbound_cfg = config.get("inbound") or {}

    launcher = BrowserLauncher(config)
    page = await launcher.start(open_url=config["urls"]["feige"])

    if not launcher.context:
        raise RuntimeError("Browser context not available")

    inbound = InboundListener(
        roles=set(inbound_cfg.get("roles") or ["buyer"]),
        dedupe_window_ms=int(inbound_cfg.get("dedupe_window_ms") or 4000),
        console_log=True,
    )

    monitor = CDPMonitor(
        context=launcher.context,
        capture_dir=capture_dir,
        filter_hosts=monitor_cfg["filter_hosts"],
        save_raw=monitor_cfg["save_raw"],
        console_log=monitor_cfg["console_log"],
        inbound_listener=inbound,
    )
    await monitor.start()

    print("=" * 60, flush=True)
    print("抖店飞鸽 CDP 调试模式已启动", flush=True)
    print("=" * 60, flush=True)
    print(f"浏览器 profile: {config['_user_data_dir']}")
    print(f"CDP 端口: {config['browser']['debug_port']}")
    print(f"抓包目录: {capture_dir}")
    print()
    print("操作步骤:", flush=True)
    print("  1. 在已打开的飞鸽页面登录（如未登录）", flush=True)
    print("  2. 选择会话并发送测试消息", flush=True)
    print(f"     当前地址: {config['urls']['feige']}", flush=True)
    print("  3. 所有标签页的 WS/HTTP 都会被抓包", flush=True)
    print("  4. 买家消息会实时打印（与 listen 相同解析逻辑）", flush=True)
    print("  5. 发完消息后关闭浏览器窗口，或按 Ctrl+C 结束", flush=True)
    print()
    print("结束后可运行:")
    print("  python -m src.cli analyze")
    print("  python -m src.cli listen")
    print("  python scripts/send_ws_to_contact.py --text \"测试消息\"")
    print("=" * 60)

    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: request_stop())

    context = launcher.context
    if context:
        context.on("close", lambda _: request_stop())

    await stop_event.wait()

    print("\n正在停止监控...")
    await monitor.stop()

    events = monitor.events
    if events:
        parser = MessageParser(capture_dir)
        analysis = parser.classify_events(events)
        SchemaDumper(capture_dir).dump(analysis)
        print(f"已自动分析本次会话: {len(events)} 条事件")
        print(f"  消息类型: {len(analysis['message_types'])}")
        print(f"  发消息模板: {len(analysis['send_templates'])}")

    await launcher.stop()
    print("调试模式已退出。")


def main() -> None:
    asyncio.run(run_debug_mode())


if __name__ == "__main__":
    main()
