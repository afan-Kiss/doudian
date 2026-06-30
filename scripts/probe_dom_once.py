#!/usr/bin/env python3
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.frame_context import find_im_frame

PROBE_JS = (ROOT / "src" / "cdp" / "live_dom_probe.js").read_text(encoding="utf-8")


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    result = await im.evaluate(f"async () => {{ const run = {PROBE_JS}; return await run(); }}")
    out = ROOT.parent / "data" / "probe-once.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out))


if __name__ == "__main__":
    asyncio.run(main())
