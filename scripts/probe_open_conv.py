import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.frame_context import find_im_frame

PROBE = """
async ({ targetKey }) => {
  const ctx = window.__monaGlobalStore?.getData?.('initContextData');
  const im = ctx?.im;
  let store = null;
  ctx?.doAction?.((s) => { store = s; });
  const info = store?.conversationsInfo || {};
  const actionNames = Object.keys(info).filter((k) => typeof info[k] === 'function').slice(0, 30);
  const imNames = im ? Object.getOwnPropertyNames(Object.getPrototypeOf(im)).filter((k) => /open|switch|select|chat|ensure|conversation/i.test(k) && typeof im[k] === 'function').slice(0, 25) : [];
  const tries = {};
  for (const name of imNames) {
    try {
      const r = await im[name](targetKey);
      tries[name] = { ok: true, type: typeof r };
    } catch (e) {
      tries[name] = { ok: false, error: String(e).slice(0, 100) };
    }
  }
  let convId = '';
  let msgLen = 0;
  ctx?.doAction?.((s) => {
    convId = s?.conversationsInfo?.currentConversation?.id || '';
    msgLen = s?.sessionDetails?.messageList?.length || 0;
  });
  return { actionNames, imNames, tries, convId: String(convId).slice(0, 55), msgLen };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    target = await im.evaluate(
        """
        () => {
          let store = null;
          window.__monaGlobalStore?.getData?.('initContextData')?.doAction?.((s) => { store = s; });
          const msgMap = store?.conversationsInfo?.messagesByConversationId;
          let targetKey = '';
          let targetSize = 0;
          for (const key of msgMap?.keys?.() || []) {
            const size = msgMap.get(key)?.map?.size || 0;
            if (size > targetSize) { targetSize = size; targetKey = key; }
          }
          return targetKey;
        }
        """
    )
    data = await im.evaluate(PROBE, {"targetKey": target})
    (ROOT / "open_methods.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: data[k] for k in ("imNames", "convId", "msgLen")}, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
