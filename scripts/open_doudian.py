#!/usr/bin/env python3
"""Open Douyin Shop web for login; capture Feige HTTP/WS for history API research."""
from __future__ import annotations

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


async def run_open_doudian(*, feige_after_login: bool = False) -> None:
    config = load_config()
    capture_dir = config["_capture_dir"]
    monitor_cfg = config["monitor"]
    inbound_cfg = config.get("inbound") or {}

    launcher = BrowserLauncher(config)
    start_url = config["urls"]["feige"] if feige_after_login else config["urls"]["doudian"]
    await launcher.start(open_url=start_url)

    if not launcher.context:
        raise RuntimeError("Browser context not available")

    inbound = InboundListener(
        roles=set(inbound_cfg.get("roles") or ["buyer", "seller"]),
        dedupe_window_ms=int(inbound_cfg.get("dedupe_window_ms") or 4000),
        console_log=False,
    )
    monitor = CDPMonitor(
        context=launcher.context,
        capture_dir=capture_dir,
        filter_hosts=monitor_cfg["filter_hosts"],
        save_raw=monitor_cfg.get("save_raw", True),
        console_log=False,
        inbound_listener=inbound,
    )
    await monitor.start()

    print("=" * 60, flush=True)
    print("抖店浏览器已启动（CDP 抓包已开启）", flush=True)
    print("=" * 60, flush=True)
    print(f"当前页面: {start_url}", flush=True)
    print(f"登录 profile: {config['_user_data_dir']}", flush=True)
    print(f"抓包目录: {capture_dir / 'raw'}", flush=True)
    print(flush=True)
    if not feige_after_login:
        print("1. 请在浏览器中扫码/登录抖店", flush=True)
        print("2. 登录后进入「飞鸽客服」工作台", flush=True)
        print("   或直接打开:", config["urls"]["feige"], flush=True)
    print("3. 点开买家会话 — 历史消息会通过 API 加载，已自动抓包", flush=True)
    print("4. 关注 API 名: get_message_by_init / get_by_conversation", flush=True)
    print("5. 关闭浏览器或按 Ctrl+C 结束", flush=True)
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

    launcher.context.on("close", lambda _: request_stop())
    await stop_event.wait()

    print(f"\n本次抓包 {len(monitor.events)} 条事件，保存在 {capture_dir / 'raw'}")
    await monitor.stop()
    await launcher.stop()


def main() -> None:
    feige = "--feige" in sys.argv
    asyncio.run(run_open_doudian(feige_after_login=feige))


if __name__ == "__main__":
    main()
