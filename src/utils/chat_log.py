from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path


_LOG_PATH: Path | None = None
_CHAT_LINE_RE = re.compile(r"^\[chat\] \[(买家|卖家)\] ")


def init_chat_log(path: Path) -> None:
    global _LOG_PATH
    _LOG_PATH = path
    path.parent.mkdir(parents=True, exist_ok=True)


def chat_log(message: str) -> None:
    """Log buyer/seller chat lines only (file + console)."""
    line = message.rstrip("\n")
    if not _CHAT_LINE_RE.match(line):
        return
    _emit(line)


def log_console(message: str) -> None:
    """Print operational messages to console without writing chat_ui.log."""
    line = message.rstrip("\n")
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def _emit(line: str) -> None:
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)
    if _LOG_PATH is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line}\n")
