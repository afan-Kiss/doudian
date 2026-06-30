import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.cdp.current_conversation import read_dom_snapshot, probe_page_state
from src.config import load_config
from src.sender.feige_navigator import FeigeNavigator, _SWITCH_CONVERSATION_JS
from src.sender.frame_context import find_im_frame

GET_BEST_CONV = """
() => {
  let store = null;
  window.__monaGlobalStore?.getData?.('initContextData')?.doAction?.((s) => { store = s; });
  const msgMap = store?.conversationsInfo?.messagesByConversationId;
  let targetKey = '';
  let targetSize = 0;
  for (const key of msgMap?.keys?.() || []) {
    const size = msgMap.get(key)?.map?.size || 0;
    if (size > targetSize) {
      targetSize = size;
      targetKey = key;
    }
  }
  return { targetKey, targetSize };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    best = await im.evaluate(GET_BEST_CONV)
    target = str(best.get("targetKey") or "")
    sw = await im.evaluate(_SWITCH_CONVERSATION_JS, {"conversationId": target, "nickname": ""})
    nav = FeigeNavigator()
    switched = await nav.switch_conversation_in_store(page, conversation_id=target)
    state = await probe_page_state(page)
    dom = await read_dom_snapshot(page)
    out = {"best": best, "sw_direct": sw, "switched": switched, "state": state, "dom": dom}
    (ROOT / "debug_read.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"best": best, "switched": switched, "conv_id": state.get("conversation_id", "")[:55], "dom_count": dom.get("message_count"), "sdk_count": dom.get("sdk_count"), "profile": dom.get("selector_profile")}, ensure_ascii=False))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
