#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.api_sender import APISender
from src.sender.feige_navigator import FeigeNavigator
from src.sender.frame_context import find_im_frame
from src.sender.send_verifier import SendVerifier


async def run(args: argparse.Namespace) -> int:
    config = load_config()
    schema_dir = config["_capture_dir"] / "schema"

    launcher = BrowserLauncher(config)
    navigator = FeigeNavigator()
    sender = APISender(schema_dir)
    verifier = SendVerifier()

    print(f"Opening Feige: {config['urls']['feige']}")
    page = await launcher.start(open_url=config["urls"]["feige"])

    print(f"Waiting for page load ({args.page_wait}s)...")
    await page.wait_for_timeout(args.page_wait * 1000)

    print(f"Opening chat: {args.contact!r}")
    opened = await navigator.open_chat_by_name(page, args.contact, timeout_ms=args.find_timeout * 1000)
    if not opened:
        print(f"Could not find chat '{args.contact}'. Check login and contact name.")
        await launcher.stop()
        return 1
    print("Chat opened.")

    print("Waiting for WebSocket connection...")
    ws_status = await navigator.wait_for_ws_ready(page, timeout_ms=args.ws_timeout * 1000)
    if not ws_status.get("ok"):
        print(
            f"WebSocket not ready (captured={ws_status.get('total')}, "
            f"open={ws_status.get('open')})."
        )
        await launcher.stop()
        return 1
    print(f"WebSocket ready: {(ws_status.get('url') or '')[:100]}")

    im_frame = await find_im_frame(page)
    frame_url = getattr(im_frame, "url", page.url)
    print(f"IM frame: {frame_url[:120]}")

    template = sender.load_template()
    if not template:
        print("No WS text-message template found. Run debug_mode, send a message, then analyze.")
        await launcher.stop()
        return 1
    print(sender.ws_replay_diagnostics(template))

    before = await verifier.ws_send_stats(im_frame)
    message_text = args.text or "你好"
    print(f"Sending WS text message: {message_text!r}")
    success = await sender.send(page, message_text, args.conversation_id)
    if not success:
        detail = await page.evaluate(
            """
            () => {
                const sockets = window.__feigeCapturedSockets || [];
                const open = sockets.filter((ws) => ws.readyState === WebSocket.OPEN);
                return {
                    captured: sockets.length,
                    open: open.length,
                    urls: open.map((ws) => ws.url || ''),
                };
            }
            """
        )
        print(f"WS send failed. sockets={detail}")
        if sender.last_send_detail:
            print(f"SDK probe: {sender.last_send_detail}")
        await launcher.stop()
        return 1

    print(f"Send mode: {sender.last_send_mode or 'unknown'}")
    if sender.last_send_detail:
        detail = sender.last_send_detail
        if detail.get("warning"):
            print(f"Warning: {detail['warning']} (buyer may not receive)")
        if detail.get("frame_count"):
            print(f"Frames sent: {detail.get('frame_count')}")
        if detail.get("sendDelta"):
            print(f"WS send delta: {detail['sendDelta']}, recv delta: {detail.get('recvDelta', 0)}")

    sdk_mode = (sender.last_send_mode or "").startswith("mona") or (sender.last_send_mode or "").startswith("pigeon")
    min_out = 500 if sdk_mode else 2500
    stats = await verifier.wait_for_new_ws_send(im_frame, before["count"], min_size=min_out)
    if stats:
        print(f"WS frame sent: {stats['lastSize']}B (total sends: {stats['count']})")
    elif sdk_mode and (sender.last_send_detail or {}).get("sendDelta", 0) > 0:
        print(f"WS traffic confirmed via SDK (send delta={(sender.last_send_detail or {}).get('sendDelta')})")
    else:
        print("Warning: no large WS outbound frame detected after send.")

    recv_delta = (sender.last_send_detail or {}).get("recvDelta", 0)
    ack = await verifier.wait_for_server_ack(im_frame, before["recvCount"], min_size=400)
    if ack:
        print(f"Server ACK received: {ack['lastRecvSize']}B (recv total: {ack['recvCount']})")
    elif recv_delta > 0:
        print(f"Server response confirmed via SDK (recv delta={recv_delta})")
    else:
        print("Warning: no server ACK within 15s.")

    visible = await verifier.message_visible(page, message_text)
    if visible:
        print("Message visible in seller chat UI.")
    else:
        print("Message not visible in seller chat UI yet.")

    if ack or recv_delta > 0:
        print("WS message accepted by server.")
    elif sdk_mode:
        print("SDK send completed; check buyer side for delivery.")
    else:
        print("WS send completed but server may have dropped the frame (invalid signature).")
    if args.keep_open > 0:
        print(f"Keeping browser open for {args.keep_open}s...")
        await asyncio.sleep(args.keep_open)

    await launcher.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="自动打开飞鸽会话并通过 WS 发消息")
    parser.add_argument("--contact", default="一只小青蛙", help="买家昵称")
    parser.add_argument("--text", default="你好", help="要发送的文本消息")
    parser.add_argument("--conversation-id", default=None, help="可选会话 ID")
    parser.add_argument("--page-wait", type=int, default=10, help="打开飞鸽后等待秒数")
    parser.add_argument("--find-timeout", type=int, default=45, help="查找联系人超时秒数")
    parser.add_argument("--ws-timeout", type=int, default=30, help="等待 WS 连接超时秒数")
    parser.add_argument("--keep-open", type=int, default=8, help="发送后保持浏览器打开秒数")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
