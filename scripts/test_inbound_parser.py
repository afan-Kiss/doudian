#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monitor.inbound_listener import InboundListener
from src.monitor.pigeon_frame_parser import extract_body_text, parse_inbound_frame


def main() -> None:
    listener = InboundListener(roles={"buyer", "seller", "system"}, console_log=False)
    count = 0
    for path in sorted(glob.glob(str(ROOT / "captures/raw/*ws_frame_received.json"))):
        event = json.load(open(path, encoding="utf-8"))
        parsed = parse_inbound_frame(event)
        msg = listener.handle_ws_event(event)
        if msg:
            count += 1
            print(msg["role_label"], repr(msg["text"][:60]), parsed.get("payload_bytes"))
        elif parsed.get("kind") != "ws_frame":
            raw = bytes.fromhex(event.get("payload_hex", ""))
            print("unhandled", parsed.get("kind"), "role", parsed.get("role"), "body", repr(extract_body_text(raw)[:40]))
    print("parsed messages:", count)


if __name__ == "__main__":
    main()
