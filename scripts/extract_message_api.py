#!/usr/bin/env python3
"""Extract Feige message structures from HAR pigeon_im APIs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    har_path = Path(r"C:\Users\6\Desktop\测试.har")
    data = json.loads(har_path.read_text(encoding="utf-8"))
    entries = data["log"]["entries"]

    message_apis = [
        "get_message_by_init",
        "get_by_conversation",
        "get_message_by_index",
        "get_user_message",
        "send",
    ]

    results = []
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if not any(api in url for api in message_apis):
            continue

        post_text = entry.get("request", {}).get("postData", {}).get("text", "")
        resp_text = entry.get("response", {}).get("content", {}).get("text", "")

        post = None
        resp = None
        try:
            if post_text:
                post = json.loads(post_text)
        except json.JSONDecodeError:
            post = post_text[:500]
        try:
            if resp_text:
                resp = json.loads(resp_text)
        except json.JSONDecodeError:
            resp = resp_text[:500]

        api_name = next((a for a in message_apis if a in url), "unknown")
        results.append(
            {
                "api": api_name,
                "method": entry.get("request", {}).get("method"),
                "url": url.split("?")[0],
                "post_body": post,
                "response": resp,
            }
        )

    out_dir = ROOT / "captures" / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "message_api_samples.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Extract message object schema from responses
    message_samples = []
    for r in results:
        resp = r.get("response")
        if not isinstance(resp, dict):
            continue
        data = resp.get("data")
        if data is None:
            continue
        # Walk to find message list
        msgs = None
        if isinstance(data, list):
            msgs = data
        elif isinstance(data, dict):
            for key in ("messages", "message_list", "msg_list", "items", "list"):
                if key in data and isinstance(data[key], list):
                    msgs = data[key]
                    break
            if msgs is None and "message" in data:
                msgs = [data["message"]]
        if msgs:
            for msg in msgs[:3]:
                if isinstance(msg, dict):
                    message_samples.append(msg)

    schema_path = out_dir / "message_object_schema.json"
    if message_samples:
        # Collect all keys seen across samples
        all_keys: dict[str, set] = {}
        for msg in message_samples:
            for k, v in msg.items():
                all_keys.setdefault(k, set()).add(type(v).__name__)
        schema = {
            "sample_count": len(message_samples),
            "fields": {k: list(v) for k, v in all_keys.items()},
            "samples": message_samples[:5],
        }
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    # Output to stdout as JSON only (avoid encoding issues)
    summary = {
        "message_api_calls": len(results),
        "apis_found": list({r["api"] for r in results}),
        "message_samples_count": len(message_samples),
        "output_files": [str(out_path), str(schema_path)],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if message_samples:
        print("\n--- Message object samples ---")
        print(json.dumps(message_samples[:2], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
