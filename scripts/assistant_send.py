#!/usr/bin/env python3
"""CLI bridge for Node assistant to invoke send_reply without HTTP server."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sender.reply_sender import send_reply_async


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="JSON payload")
    args = parser.parse_args()

    payload = json.loads(args.json)
    result = asyncio_run(
        send_reply_async(
            conversation_id=str(payload.get("conversation_id") or ""),
            customer_hash=str(payload.get("customer_hash") or ""),
            text=str(payload.get("text") or ""),
            mode=mode,
            contact_name=payload.get("contact_name"),
            page=None,
        )
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok", False) or result.get("filled") else 1


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    raise SystemExit(main())
