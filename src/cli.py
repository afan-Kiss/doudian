from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.analyzer.har_parser import export_har_analysis
from src.analyzer.parser import MessageParser
from src.analyzer.schema_dump import SchemaDumper
from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.monitor.cdp_client import CDPMonitor
from src.monitor.inbound_listener import InboundListener
from src.chat.runner import run_chat_ui
from src.sender.api_sender import APISender
from src.sender.dom_sender import DOMSender


def cmd_analyze(args: argparse.Namespace) -> int:
    config = load_config()
    capture_dir = config["_capture_dir"]
    parser = MessageParser(capture_dir)
    events = parser.load_raw_events(latest=args.latest)

    if not events:
        print("No capture files found. Run debug mode first and send some messages.")
        return 1

    analysis = parser.classify_events(events)
    outputs = SchemaDumper(capture_dir).dump(analysis)

    print(f"Analyzed {len(events)} raw events.")
    print(f"Message types: {len(analysis['message_types'])}")
    print(f"WS endpoints: {len(analysis['ws_endpoints'])}")
    print(f"Send templates: {len(analysis['send_templates'])}")
    for name, path in outputs.items():
        print(f"  -> {path}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    capture_dir = config["_capture_dir"]
    raw_dir = capture_dir / "raw"
    schema_dir = capture_dir / "schema"

    raw_count = len(list(raw_dir.glob("*.json"))) if raw_dir.exists() else 0
    schema_files = list(schema_dir.glob("*.json")) if schema_dir.exists() else []

    print(f"Capture dir: {capture_dir}")
    print(f"Raw events: {raw_count}")
    print(f"Schema files: {len(schema_files)}")

    template_path = schema_dir / "send_template.json"
    if template_path.exists():
        data = json.loads(template_path.read_text(encoding="utf-8"))
        latest = data.get("latest")
        if latest:
            transport = latest.get("transport")
            url = (latest.get("url") or "N/A")[:80]
            extra = ""
            if transport == "websocket":
                extra = (
                    f", {latest.get('payload_length')}B"
                    f", message_send={latest.get('is_message_send')}"
                )
            print(f"Latest send template: {transport} -> {url}{extra}")
        else:
            print("No send template captured yet.")
    else:
        print("No send template file yet. Run analyze after capturing traffic.")

    profile_exists = config["_user_data_dir"].exists()
    print(f"Browser profile: {config['_user_data_dir']} ({'exists' if profile_exists else 'not created'})")
    return 0


async def cmd_send_async(args: argparse.Namespace) -> int:
    config = load_config()
    capture_dir = config["_capture_dir"]
    schema_dir = capture_dir / "schema"

    launcher = BrowserLauncher(config)
    page = await launcher.start(open_url=config["urls"]["feige"])

    wait_seconds = max(0, args.wait)
    if wait_seconds:
        print(f"Waiting {wait_seconds}s — open a chat session in Feige if needed...")
        await page.wait_for_timeout(wait_seconds * 1000)

    method = args.method
    api_sender = APISender(schema_dir)
    dom_sender = DOMSender()

    success = False
    if method in ("auto", "dom"):
        success = await dom_sender.send(page, args.text)
        if success:
            print(f"Message sent via DOM: {args.text!r}")
        elif method == "dom":
            print("DOM send failed. Make sure a chat session is open in Feige.")
            await launcher.stop()
            return 1

    if not success and method in ("auto", "api"):
        success = await api_sender.send(page, args.text, args.conversation_id)
        if success:
            print("Message sent via Mona SDK WebSocket.")
        elif method == "api":
            print("WebSocket send failed. Ensure Feige is loaded and WS is connected.")
            await launcher.stop()
            return 1

    if not success:
        print("Send failed. Open a chat in Feige and retry with --wait 15")
        await launcher.stop()
        return 1

    keep_open = max(0, args.keep_open)
    if keep_open:
        print(f"Keeping browser open for {keep_open}s...")
        await asyncio.sleep(keep_open)

    await launcher.stop()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    return asyncio.run(cmd_send_async(args))


async def cmd_listen_async(args: argparse.Namespace) -> int:
    import signal

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
    print(f"监听角色: {role_text}")
    print("按 Ctrl+C 结束。")
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


def cmd_listen(args: argparse.Namespace) -> int:
    return asyncio.run(cmd_listen_async(args))


def cmd_chat(args: argparse.Namespace) -> int:
    return asyncio.run(run_chat_ui(args))


def cmd_analyze_har(args: argparse.Namespace) -> int:
    har_path = Path(args.har)
    if not har_path.exists():
        print(f"HAR file not found: {har_path}")
        return 1

    config = load_config()
    capture_dir = config["_capture_dir"]
    schema_dir = capture_dir / "schema"

    result = export_har_analysis(har_path, schema_dir)
    template = result["template"]
    report = result["report"]

    ctx = template.get("message_context", {})
    print(f"HAR analyzed: {har_path}")
    print(f"  WS connections: {len(report['websocket_connections'])}")
    print(f"  pigeon_im APIs: {len(report['pigeon_im_apis'])}")
    print(f"  monitor events: {len(report['monitor_events'])}")
    print(f"  conversation_id: {ctx.get('conversation_id')}")
    print(f"  content_sample: {ctx.get('content_sample')}")
    print(f"  send_by_method: {ctx.get('send_by_method')}")
    print(f"  cmd / msg_type: {ctx.get('cmd')} / {ctx.get('msg_type')}")
    print(f"  -> {schema_dir / 'send_template.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖店飞鸽 CDP 监控与发消息工具")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_parser = sub.add_parser("analyze", help="分析抓包数据并导出结构体")
    analyze_parser.add_argument("--latest", type=int, default=None, help="只分析最近 N 条原始事件")
    analyze_parser.set_defaults(func=cmd_analyze)

    analyze_har_parser = sub.add_parser("analyze-har", help="分析 HAR 抓包文件并导出 WS 发消息模板")
    analyze_har_parser.add_argument(
        "--har",
        default=r"C:\Users\6\Desktop\测试.har",
        help="HAR 文件路径",
    )
    analyze_har_parser.set_defaults(func=cmd_analyze_har)

    status_parser = sub.add_parser("status", help="查看抓包与模板状态")
    status_parser.set_defaults(func=cmd_status)

    send_parser = sub.add_parser("send", help="复现发送飞鸽消息")
    send_parser.add_argument("--text", required=True, help="要发送的消息文本")
    send_parser.add_argument("--conversation-id", default=None, help="可选会话 ID")
    send_parser.add_argument(
        "--method",
        choices=["auto", "api", "dom"],
        default="auto",
        help="发送方式: auto=先DOM后WS, dom=页面粘贴, api=WS二进制复现",
    )
    send_parser.add_argument("--wait", type=int, default=8, help="发送前等待秒数（用于手动打开会话）")
    send_parser.add_argument("--keep-open", type=int, default=5, help="发送后保持浏览器打开的秒数")
    send_parser.set_defaults(func=cmd_send)

    listen_parser = sub.add_parser("listen", help="实时监听买家/卖家 WS 消息")
    listen_parser.add_argument("--roles", default=None, help="buyer,seller,system")
    listen_parser.add_argument("--dedupe-ms", type=int, default=None, help="去重时间窗毫秒")
    listen_parser.add_argument("--page-wait", type=int, default=8, help="打开飞鸽后等待秒数")
    listen_parser.add_argument("--no-save", action="store_true", help="不保存原始抓包")
    listen_parser.add_argument("--quiet", action="store_true", help="不打印聊天消息")
    listen_parser.add_argument("--ws-log", action="store_true", help="打印原始 WS 帧日志")
    listen_parser.set_defaults(func=cmd_listen)

    chat_parser = sub.add_parser("chat", help="启动微信式聊天 Web 界面")
    chat_parser.add_argument("--host", default=None, help="Web 服务地址")
    chat_parser.add_argument("--port", type=int, default=None, help="Web 服务端口")
    chat_parser.add_argument("--roles", default=None, help="buyer,seller,system")
    chat_parser.add_argument("--dedupe-ms", type=int, default=None, help="去重时间窗毫秒")
    chat_parser.add_argument("--page-wait", type=int, default=8, help="打开飞鸽后等待秒数")
    chat_parser.add_argument("--no-save", action="store_true", help="不保存原始抓包")
    chat_parser.set_defaults(func=cmd_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
