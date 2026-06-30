#!/usr/bin/env python3
"""Decode protobuf strings from HAR pigeon_im responses."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_strings(data: bytes, min_len: int = 4) -> list[str]:
    """Extract printable UTF-8 strings from binary protobuf."""
    results = []
    current = []
    for b in data:
        if 32 <= b < 127 or b in (0x0A,):
            if 32 <= b < 127:
                current.append(chr(b))
        else:
            if len(current) >= min_len:
                results.append("".join(current))
            current = []
    if len(current) >= min_len:
        results.append("".join(current))
    return results


def decode_protobuf_strings(raw: str) -> dict:
    """Try multiple decodings for HAR response body."""
    out = {"raw_len": len(raw), "strings": []}

    # Try as raw bytes (latin-1 preserves all bytes)
    try:
        data = raw.encode("latin-1")
        out["strings"] = extract_strings(data)
    except Exception:
        pass

    # Try base64
    try:
        data = base64.b64decode(raw)
        b64_strings = extract_strings(data)
        if len(b64_strings) > len(out["strings"]):
            out["strings"] = b64_strings
            out["decoded_as"] = "base64"
    except Exception:
        pass

    return out


def main() -> None:
    har_path = Path(r"C:\Users\6\Desktop\测试.har")
    data = json.loads(har_path.read_text(encoding="utf-8"))
    entries = data["log"]["entries"]

    analyses = []

    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "pigeon_im" not in url:
            continue

        resp_text = entry.get("response", {}).get("content", {}).get("text", "")
        post_text = entry.get("request", {}).get("postData", {}).get("text", "")
        if not resp_text:
            continue

        api = url.split("pigeon_im/")[-1].split("?")[0]
        resp_decoded = decode_protobuf_strings(resp_text)
        post_decoded = decode_protobuf_strings(post_text) if post_text else None

        # Filter interesting strings
        interesting = [
            s for s in resp_decoded["strings"]
            if any(k in s.lower() for k in (
                "pigeon", "message", "content", "text", "conv", "talk",
                "client", "send", "imcloud", "display", "2026"
            )) or (len(s) > 10 and not s.startswith("0.0.0"))
        ]

        if interesting or "get_user_message" in api:
            analyses.append({
                "api": api,
                "url": url.split("?")[0],
                "response_strings": interesting[:30],
                "all_response_strings": resp_decoded["strings"][:50],
                "post_strings": (post_decoded or {}).get("strings", [])[:20],
            })

    out_dir = ROOT / "captures" / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "protobuf_strings.json"
    out_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print key findings
    print(json.dumps({
        "analyzed_apis": len(analyses),
        "output": str(out_path),
    }, ensure_ascii=False))

    for a in analyses:
        if "get_user_message" in a["api"] or "get_by_conversation" in a["api"]:
            print(f"\n=== {a['api']} ===")
            for s in a["response_strings"][:15]:
                print(f"  {s}")


if __name__ == "__main__":
    main()
