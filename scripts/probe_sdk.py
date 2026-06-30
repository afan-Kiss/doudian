#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.feige_navigator import FeigeNavigator
from src.sender.frame_context import find_im_frame
from src.sender.page_ws_encoder import PageWsEncoder


async def run(args: argparse.Namespace) -> int:
    config = load_config()
    launcher = BrowserLauncher(config)
    navigator = FeigeNavigator()
    encoder = PageWsEncoder()

    print(f"Opening Feige: {config['urls']['feige']}")
    page = await launcher.start(open_url=config["urls"]["feige"])
    await page.wait_for_timeout(args.page_wait * 1000)

    if args.contact:
        print(f"Opening chat: {args.contact!r}")
        opened = await navigator.open_chat_by_name(page, args.contact, timeout_ms=args.find_timeout * 1000)
        if not opened:
            print(f"Could not open chat '{args.contact}'")
        else:
            print("Chat opened.")

    ws_status = await navigator.wait_for_ws_ready(page, timeout_ms=args.ws_timeout * 1000)
    print(f"WS ready: {ws_status}")

    im_frame = await find_im_frame(page)
    print(f"IM frame: {getattr(im_frame, 'url', page.url)}")

    result = await encoder.probe(im_frame)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.keep_open > 0:
        await asyncio.sleep(args.keep_open)

    await launcher.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="探测飞鸽页面 IM SDK / webpack 模块")
    parser.add_argument("--contact", default="一只小青蛙", help="可选：先打开该联系人会话")
    parser.add_argument("--page-wait", type=int, default=12, help="打开飞鸽后等待秒数")
    parser.add_argument("--find-timeout", type=int, default=45, help="查找联系人超时秒数")
    parser.add_argument("--ws-timeout", type=int, default=30, help="等待 WS 连接超时秒数")
    parser.add_argument("--keep-open", type=int, default=5, help="探测后保持浏览器打开秒数")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
