#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.monitor.cdp_client import CDPMonitor
from src.monitor.inbound_listener import InboundListener


async def run(args: argparse.Namespace) -> int:
    config = load_config()
    capture_dir = config["_capture_dir"]
    monitor_cfg = config["monitor"]
    inbound_cfg = config.get("inbound") or {}

    roles = set(args.roles.split(",")) if args.roles else set(inbound_cfg.get("roles") or ["buyer"])
    dedupe_ms = args.dedupe_ms or int(inbound_cfg.get("dedupe_window_ms") or 4000)

    launcher = BrowserLauncher(config)
    page = await launcher.start(open_url=config["urls"]["feige"])
    await page.wait_for_timeout(args.page_wait * 1000)

    inbound = InboundListener(
        roles=roles,
        dedupe_window_ms=dedupe_ms,
        console_log=not args.quiet,
    )

    if not launcher.context:
        raise RuntimeError("Browser context not available")

    monitor = CDPMonitor(
        context=launcher.context,
        capture_dir=capture_dir,
        filter_hosts=monitor_cfg["filter_hosts"],
        save_raw=monitor_cfg.get("save_raw", True) and not args.no_save,
        console_log=monitor_cfg.get("console_log", True) and args.ws_log,
        inbound_listener=inbound,
    )
    await monitor.start()

    role_text = "、".join(
        {"buyer": "买家", "seller": "卖家", "system": "系统"}.get(role, role) for role in sorted(roles)
    )
    print("=" * 60, flush=True)
    print("飞鸽实时消息监听已启动", flush=True)
    print("=" * 60, flush=True)
    print(f"监听角色: {role_text}")
    print(f"飞鸽地址: {config['urls']['feige']}")
    print(f"抓包目录: {capture_dir}")
    print()
    print("请在飞鸽中保持登录，买家发消息后会实时打印，例如：")
    print("  [12:34:56] [买家] 一只小青蛙 | 会话=... | 你好")
    print()
    print("按 Ctrl+C 结束监听。")
    print("=" * 60, flush=True)

    stop_event = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: request_stop())

    if launcher.context:
        launcher.context.on("close", lambda _: request_stop())

    await stop_event.wait()

    print(f"\n监听结束，共识别 {len(inbound.messages)} 条消息。")
    await monitor.stop()
    await launcher.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="实时监听飞鸽买家/卖家 WS 消息")
    parser.add_argument("--roles", default=None, help="监听角色，逗号分隔: buyer,seller,system")
    parser.add_argument("--dedupe-ms", type=int, default=None, help="去重时间窗毫秒")
    parser.add_argument("--page-wait", type=int, default=8, help="打开飞鸽后等待秒数")
    parser.add_argument("--no-save", action="store_true", help="不保存原始抓包文件")
    parser.add_argument("--quiet", action="store_true", help="不打印识别到的聊天消息")
    parser.add_argument("--ws-log", action="store_true", help="同时打印原始 WS 帧日志")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
