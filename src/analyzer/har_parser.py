from __future__ import annotations

import base64
import json
from pathlib import Path


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze_har_deep(har_path: Path) -> dict:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = har["log"]["entries"]

    report: dict = {
        "frontier_access": None,
        "websocket_connections": [],
        "pigeon_im_apis": [],
        "monitor_events": [],
        "send_message_hints": [],
    }

    for entry in entries:
        url = entry["request"]["url"]
        method = entry["request"]["method"]

        if "get_frontier_access_info" in url:
            resp_text = entry.get("response", {}).get("content", {}).get("text", "")
            if resp_text:
                report["frontier_access"] = json.loads(resp_text)

        if "_webSocketMessages" in entry:
            ws = {
                "url": url,
                "query_params": dict(
                    p.split("=", 1) if "=" in p else (p, "")
                    for p in url.split("?", 1)[-1].split("&")
                    if url.startswith("wss://")
                ),
                "messages": [],
            }
            for msg in entry["_webSocketMessages"]:
                payload = msg.get("data", "")
                item = {
                    "direction": msg.get("type"),
                    "opcode": msg.get("opcode"),
                    "time": msg.get("time"),
                    "payload": payload,
                    "payload_length": len(payload),
                }
                if msg.get("opcode") == 2:
                    try:
                        item["payload_base64_decoded_hex"] = base64.b64decode(payload).hex()
                    except Exception:
                        pass
                ws["messages"].append(item)
            report["websocket_connections"].append(ws)

        if "pigeon_im" in url:
            post = entry["request"].get("postData", {})
            resp_text = entry.get("response", {}).get("content", {}).get("text", "")
            item = {
                "method": method,
                "url": url.split("?")[0],
                "request": post.get("text") if post else None,
                "response_preview": resp_text[:3000] if resp_text else None,
            }
            report["pigeon_im_apis"].append(item)

        post = entry["request"].get("postData", {})
        text = post.get("text", "") if post else ""
        if text and ("pigeon_conversation_monitor" in text or "troubleshoot_pigeon" in text):
            try:
                events = json.loads(text)
                for batch in events if isinstance(events, list) else [events]:
                    for ev in batch.get("events", [batch]):
                        params = ev.get("params", "")
                        if isinstance(params, str):
                            try:
                                p = json.loads(params)
                                report["monitor_events"].append(
                                    {"event": ev.get("event"), "params": p}
                                )
                            except json.JSONDecodeError:
                                pass
            except json.JSONDecodeError:
                pass

        if text and ("测试" in text or "msg_body" in text or "client_message_id" in text):
            report["send_message_hints"].append({"url": url, "body": text[:5000]})

    return report


def build_send_template(report: dict) -> dict:
    frontier = report.get("frontier_access", {})
    data = frontier.get("data", {}) if frontier else {}

    ws_url_template = (
        "wss://frontier.snssdk.com/ws/v2"
        "?access_key={access_key}"
        "&fpid=117"
        "&aid=1522"
        "&device_id={device_id}"
        "&device_platform=web"
        "&version_code=fws_1.0.0"
        "&token={token}"
    )

    conv_id = None
    talk_id = None
    client_message_id = None
    msg_type = None
    cmd = None
    content = None
    sender_id = None
    security_uid = None
    conversation_short_id = None
    server_message_id = None

    for ev in report.get("monitor_events", []):
        p = ev.get("params", {})
        ext = p.get("extJson", "")
        if isinstance(ext, str) and ext:
            try:
                ext_parsed = json.loads(ext)
                if ext_parsed.get("security_biz_conversation_id"):
                    conv_id = ext_parsed["security_biz_conversation_id"]
                if ext_parsed.get("s:client_message_id"):
                    client_message_id = ext_parsed["s:client_message_id"]
            except json.JSONDecodeError:
                pass

        if p.get("sub_con_short_id"):
            talk_id = p["sub_con_short_id"]
        if p.get("client_message_id"):
            client_message_id = p["client_message_id"]
        if p.get("msg_type"):
            msg_type = p["msg_type"]
        if p.get("cmd"):
            cmd = p["cmd"]
        if p.get("sender_id"):
            sender_id = p["sender_id"]

        pigeon_info = p.get("pigeon_info", "")
        if isinstance(pigeon_info, str) and pigeon_info:
            try:
                pi = json.loads(pigeon_info)
                security_uid = pi.get("uid") or security_uid
            except json.JSONDecodeError:
                pass

        imcloud_info = p.get("imcloud_info", "")
        if isinstance(imcloud_info, str) and imcloud_info:
            try:
                ii = json.loads(imcloud_info)
                conversation_short_id = ii.get("conversation_short_id") or conversation_short_id
                server_message_id = ii.get("server_message_id") or server_message_id
            except json.JSONDecodeError:
                pass

    for hint in report.get("send_message_hints", []):
        body = hint.get("body", "")
        if "msg_body_list" in body:
            try:
                parsed = json.loads(body)
                for msg in parsed.get("msg_body_list", []):
                    content = msg.get("content")
                    sender_id = msg.get("sender") or sender_id
            except json.JSONDecodeError:
                pass

    return {
        "transport": "websocket",
        "protocol": "frontier_im",
        "ws_url_template": ws_url_template,
        "handshake": {
            "client_send": "hi",
            "server_reply": "hi",
            "description": "连接建立后先发 hi 心跳握手",
        },
        "connection_params": {
            "access_key": data.get("access_key"),
            "token": data.get("token"),
            "device_id": "3995004761016570",
            "fpid": "117",
            "aid": "1522",
            "version_code": "fws_1.0.0",
        },
        "frontier_access_api": {
            "url": "https://im.jinritemai.com/doudian/ai/get_frontier_access_info",
            "method": "GET",
            "params": {"device_id": "{device_id}", "_bid": "ffa_dou_xiaoer"},
        },
        "message_context": {
            "conversation_id": conv_id,
            "security_uid": security_uid,
            "talk_id": talk_id,
            "conversation_short_id": conversation_short_id,
            "client_message_id": client_message_id,
            "server_message_id": server_message_id,
            "msg_type": msg_type,
            "cmd": cmd,
            "content_sample": content,
            "sender_id": sender_id,
            "shop_id": "263636465",
            "app_id": "1383",
            "inbox_type": "3",
            "conv_type": "10",
            "send_by_method": "ws",
        },
        "notes": [
            "HAR 中 WS 仅捕获到 hi 握手帧，实际发消息帧为二进制 protobuf，Chrome HAR 未完整记录",
            "发消息走 frontier WebSocket，cmd=616 为发送命令，msg_type=50001 为文本消息",
            "conversation_id 格式: {security_uid}:{shop_id}::{inbox_type}:1:pigeon",
            "需通过 CDP 实时抓包获取完整 protobuf 发消息帧",
        ],
    }


def export_har_analysis(har_path: Path, schema_dir: Path) -> dict:
    report = analyze_har_deep(har_path)
    template = build_send_template(report)

    save_json(schema_dir / "har_analysis.json", report)
    save_json(
        schema_dir / "send_template.json",
        {"latest": template, "templates": [template], "count": 1},
    )
    save_json(
        schema_dir / "ws_endpoints.json",
        {"endpoints": [c["url"] for c in report["websocket_connections"]]},
    )
    save_json(
        schema_dir / "har_summary.json",
        {
            "frontier_access": report.get("frontier_access"),
            "ws_connections": len(report["websocket_connections"]),
            "ws_messages": sum(len(c["messages"]) for c in report["websocket_connections"]),
            "pigeon_im_apis": len(report["pigeon_im_apis"]),
            "monitor_events": len(report["monitor_events"]),
            "send_template": template,
        },
    )
    return {"report": report, "template": template}
