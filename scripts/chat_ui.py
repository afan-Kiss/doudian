#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.console_utf8 import configure_console_utf8

configure_console_utf8()

from src.chat.runner import run_chat_ui


def main() -> None:
    parser = argparse.ArgumentParser(description="飞鸽微信式聊天界面")
    parser.add_argument("--host", default=None, help="Web 服务地址")
    parser.add_argument("--port", type=int, default=None, help="Web 服务端口")
    parser.add_argument("--roles", default=None, help="监听角色，逗号分隔: buyer,seller,system")
    parser.add_argument("--dedupe-ms", type=int, default=None, help="去重时间窗毫秒")
    parser.add_argument("--page-wait", type=int, default=8, help="打开飞鸽后等待秒数")
    parser.add_argument("--no-save", action="store_true", help="不保存原始抓包文件")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_chat_ui(args)))


if __name__ == "__main__":
    main()
