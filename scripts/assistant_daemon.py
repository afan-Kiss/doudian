#!/usr/bin/env python3
"""Long-lived CDP daemon for douyin-customer-assistant integration."""
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
    parser = argparse.ArgumentParser(description="抖店 CDP 助手常驻服务")
    parser.add_argument("--host", default=None, help="API 地址")
    parser.add_argument("--port", type=int, default=None, help="API 端口")
    parser.add_argument("--roles", default="buyer,seller,system", help="监听角色")
    parser.add_argument("--dedupe-ms", type=int, default=None, help="去重毫秒")
    parser.add_argument("--page-wait", type=int, default=10, help="打开飞鸽后等待秒数")
    parser.add_argument("--no-save", action="store_true", help="不保存原始抓包")
    parser.add_argument("--no-bot", action="store_true", default=True, help="禁用内置 bot")
    args = parser.parse_args()
    args.no_bot = True
    raise SystemExit(asyncio.run(run_chat_ui(args)))


if __name__ == "__main__":
    main()
