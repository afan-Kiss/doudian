#!/usr/bin/env python3
"""Deep analysis of Feige HAR - WS + HTTP message structures."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_har(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def try_json(text: str):
    if not text:
        return None
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return None


def analyze_ws(entries: list) -> dict:
    result = {"connections": [], "all_messages": []}
    for i, entry in enumerate(entries):
        url = entry.get("request", {}).get("url", "")
        msgs = entry.get("_webSocketMessages", [])
        if not url.startswith("ws") and not msgs:
            continue
        conn = {"index": i, "url": url, "messages": []}
        for m in msgs:
            direction = "send" if m.get("type") == "send" else "recv"
            opcode = m.get("opcode", 1)
            data = m.get("data", "")
            parsed = try_json(data)
            msg = {
                "direction": direction,
                "opcode": opcode,
                "length": len(data),
                "raw": data,
                "parsed": parsed,
            }
            # Try base64 decode for binary-looking data
            if opcode == 2 or (not parsed and data):
                try:
                    decoded = base64.b64decode(data)
                    msg["base64_decoded_hex"] = decoded[:200].hex()
                    msg["base64_decoded_len"] = len(decoded)
                except Exception:
                    pass
            conn["messages"].append(msg)
            result["all_messages"].append({**msg, "url": url})
        if conn["messages"] or url.startswith("ws"):
            result["connections"].append(conn)
    return result


def analyze_frontier_access(entries: list) -> list:
    results = []
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "get_frontier_access_info" not in url:
            continue
        resp = entry.get("response", {}).get("content", {}).get("text", "")
        parsed = try_json(resp)
        results.append({"url": url, "response": parsed or resp[:500]})
    return results


def analyze_pigeon_im(entries: list) -> list:
    results = []
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "pigeon_im" not in url and "pigeon.jinritemai" not in url:
            continue
        method = entry.get("request", {}).get("method", "")
        post = entry.get("request", {}).get("postData", {}).get("text", "")
        resp = entry.get("response", {}).get("content", {}).get("text", "")
        results.append(
            {
                "method": method,
                "url": url.split("?")[0],
                "query": url.split("?")[1][:200] if "?" in url else "",
                "post": try_json(post) or (post[:300] if post else None),
                "response": try_json(resp),
            }
        )
    return results


def find_message_content_in_responses(entries: list) -> list:
    """Find actual chat message content in HTTP responses."""
    hits = []
    for entry in entries:
        resp = entry.get("response", {}).get("content", {}).get("text", "")
        parsed = try_json(resp)
        if not parsed:
            continue
        text = json.dumps(parsed, ensure_ascii=False)
        if any(k in text for k in ("content", "message_body", "text", "msg_content", "conversation_id")):
            url = entry.get("request", {}).get("url", "")
            if "message" in url or "conversation" in url or "pigeon" in url:
                hits.append({"url": url[:120], "data": parsed})
    return hits


def main() -> None:
    har_path = Path(r"C:\Users\6\Desktop\测试.har")
    if len(sys.argv) > 1:
        har_path = Path(sys.argv[1])

    data = load_har(har_path)
    entries = data["log"]["entries"]

    ws = analyze_ws(entries)
    frontier = analyze_frontier_access(entries)
    pigeon = analyze_pigeon_im(entries)
    msg_hits = find_message_content_in_responses(entries)

    out_dir = ROOT / "captures" / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "har_file": str(har_path),
        "summary": {
            "total_entries": len(entries),
            "ws_connections": len(ws["connections"]),
            "ws_messages": len(ws["all_messages"]),
            "ws_send": sum(1 for m in ws["all_messages"] if m["direction"] == "send"),
            "ws_recv": sum(1 for m in ws["all_messages"] if m["direction"] == "recv"),
            "frontier_access_calls": len(frontier),
            "pigeon_im_calls": len(pigeon),
        },
        "ws_connections": ws["connections"],
        "frontier_access_info": frontier,
        "pigeon_im_apis": pigeon,
        "message_responses": msg_hits[:10],
    }

    report_path = out_dir / "har_analysis_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print human-readable report
    print("=" * 70)
    print("飞鸽 HAR 深度分析报告")
    print("=" * 70)
    s = report["summary"]
    print(f"总请求: {s['total_entries']}")
    print(f"WS 连接: {s['ws_connections']}, 消息: {s['ws_messages']} (send={s['ws_send']}, recv={s['ws_recv']})")
    print(f"frontier_access_info 调用: {s['frontier_access_calls']}")
    print(f"pigeon_im 相关 API: {s['pigeon_im_calls']}")

    print("\n--- WebSocket 端点 ---")
    for conn in ws["connections"]:
        print(f"  {conn['url'][:120]}")
        for m in conn["messages"]:
            print(f"    [{m['direction'].upper()}] opcode={m['opcode']} len={m['length']}")
            if m["parsed"]:
                print(f"      JSON: {json.dumps(m['parsed'], ensure_ascii=False)[:300]}")
            else:
                print(f"      raw: {m['raw'][:200]}")

    print("\n--- frontier_access_info (WS 鉴权信息) ---")
    for f in frontier:
        print(json.dumps(f["response"], ensure_ascii=False, indent=2)[:1500])

    print("\n--- pigeon_im 消息相关 API ---")
    seen_urls = set()
    for p in pigeon:
        base = p["url"]
        if base in seen_urls:
            continue
        seen_urls.add(base)
        print(f"  {p['method']} {base}")
        if p["post"]:
            print(f"    POST body: {json.dumps(p['post'], ensure_ascii=False)[:200]}")
        if p["response"] and isinstance(p["response"], dict):
            # Show structure keys
            print(f"    Response keys: {list(p['response'].keys())}")
            inner = p["response"].get("data") or p["response"]
            if isinstance(inner, dict):
                print(f"    data keys: {list(inner.keys())[:15]}")

    print("\n--- 消息内容样例 (HTTP 响应) ---")
    for hit in msg_hits[:5]:
        print(f"  URL: {hit['url']}")
        sample = json.dumps(hit["data"], ensure_ascii=False, indent=2)
        print(sample[:1500])
        if len(sample) > 1500:
            print("  ...")
        print()

    print(f"\n完整报告已保存: {report_path}")


if __name__ == "__main__":
    main()
