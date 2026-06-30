import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.frame_context import find_im_frame

SWITCH = """
async () => {
  const ctx = window.__monaGlobalStore?.getData?.('initContextData');
  let store = null;
  ctx?.doAction?.((s) => { store = s; });
  const msgMap = store?.conversationsInfo?.messagesByConversationId;
  const convMap = store?.conversationsInfo?.conversationMap;
  let targetKey = '';
  let targetSize = 0;
  for (const key of msgMap?.keys?.() || []) {
    const size = msgMap.get(key)?.map?.size || 0;
    if (size > targetSize) {
      targetSize = size;
      targetKey = key;
    }
  }
  if (!targetKey) return { ok: false };
  let conv = convMap?.get?.(targetKey) || null;
  if (!conv && convMap) {
    for (const [k, v] of convMap.entries?.() || []) {
      if (String(k).startsWith(String(targetKey).slice(0, 20))) {
        conv = v;
        break;
      }
    }
  }
  if (conv) {
    store.conversationsInfo.currentConversation = conv;
  }
  const im = ctx?.im;
  try { await im?.pullMessagesByConversationId?.(targetKey); } catch (e) {}
  await new Promise((r) => setTimeout(r, 500));
  return {
    ok: true,
    targetKey: String(targetKey).slice(0, 50),
    targetSize,
    convName: conv?.name || conv?.nickname || '',
    hasConv: Boolean(conv),
    convMapSize: convMap?.size || 0,
  };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    data = await im.evaluate(SWITCH)
    (ROOT / "switch_out.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
