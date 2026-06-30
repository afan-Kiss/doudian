import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.cdp.current_conversation import DOM_SNAPSHOT_JS
from src.sender.frame_context import find_im_frame


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    script = f"(async () => {{ const run = {DOM_SNAPSHOT_JS}; return await run(); }})()"
    try:
        result = await im.evaluate(script)
        print("ok", result)
    except Exception as exc:
        print("error", exc)
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
