from __future__ import annotations

import os
import sys


def configure_console_utf8() -> None:
    """Force UTF-8 console output on Windows (avoid garbled Chinese logs)."""
    os.environ.setdefault("PYTHONUTF8", "1")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
