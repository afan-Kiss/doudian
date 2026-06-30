import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.cdp.current_conversation import read_current_conversation, read_dom_snapshot, probe_page_state
from src.chat.hub import ChatHub
from src.config import load_config


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    state = await probe_page_state(page)
    dom = await read_dom_snapshot(page)
    hub = ChatHub()
    result = await read_current_conversation(page, hub)
    out = {
        "state": state,
        "dom_count": dom.get("message_count"),
        "sdk_count": dom.get("sdk_count"),
        "result_ok": result.get("ok"),
        "error": result.get("error"),
        "recent_count": len(result.get("recent_messages") or []),
        "question": result.get("current_customer_question", ""),
        "message_sources": result.get("message_sources"),
        "profile": result.get("selector_profile"),
    }
    (ROOT / "acceptance_out.json").write_text(
        json.dumps({**out, "recent_messages": result.get("recent_messages")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
