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
({ targetKey }) => {
  let store = null;
  window.__monaGlobalStore?.getData?.('initContextData')?.doAction?.((s) => { store = s; });
  const convMap = store?.conversationsInfo?.conversationMap;
  const keys = convMap ? [...convMap.keys()] : [];
  const exact = keys.find((k) => k === targetKey);
  const prefix = keys.find((k) => String(k).startsWith(String(targetKey).slice(0, 40)));
  return {
    targetLen: String(targetKey).length,
    keyCount: keys.length,
    firstKeyLen: keys[0] ? String(keys[0]).length : 0,
    exact: Boolean(exact),
    prefix: prefix ? String(prefix).slice(0, 80) : null,
    getDirect: Boolean(convMap?.get?.(targetKey)),
    getPrefix: prefix ? Boolean(convMap?.get?.(prefix)) : false,
  };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    best = await im.evaluate(
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
          return { targetKey, targetSize };
        }
        """
    )
    data = await im.evaluate(PROBE, best)
    print(json.dumps({**best, **data}, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
