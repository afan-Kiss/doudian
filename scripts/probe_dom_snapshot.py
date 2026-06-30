import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.cdp.current_conversation import read_dom_snapshot, probe_page_state
from src.sender.frame_context import find_im_frame


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    state = await probe_page_state(page)
    print("state", state)
    im = await find_im_frame(page)
    info = await im.evaluate(
        """
        () => {
          const counts = {};
          for (const sel of ['[class*="message"]', '[class*="msg"]', '[class*="chat"]', '[class*="bubble"]']) {
            counts[sel] = document.querySelectorAll(sel).length;
          }
          let sdk = { messages: [], storeKeys: [] };
          try {
            const ctx = window.__monaGlobalStore?.getData?.('initContextData');
            ctx?.doAction?.((store) => {
              sdk.storeKeys = Object.keys(store || {}).slice(0, 40);
              const sd = store?.sessionDetails || {};
              sdk.sessionKeys = Object.keys(sd).slice(0, 20);
              const list = sd.messageList;
              if (Array.isArray(list)) {
                sdk.listLen = list.length;
                sdk.messages = list.slice(-8).map((m) => ({
                  content: m?.content || m?.text || m?.message || m?.msg || m?.body?.content || '',
                  role: m?.sender_role || m?.role || m?.senderRole || m?.ext?.sender_role || m?.direction,
                  keys: Object.keys(m || {}).slice(0, 14),
                }));
              }
              const hist = store?.historyConversationData?.chats;
              if (Array.isArray(hist) && hist.length) {
                sdk.historyLen = hist.length;
                sdk.historySample = hist.slice(0, 2).map((c) => Object.keys(c || {}));
              }
            });
          } catch (e) {
            sdk.error = String(e);
          }
          const text = document.body?.innerText || '';
          return {
            url: location.href,
            counts,
            textLen: text.length,
            tail: text.slice(-800),
            sdk,
          };
        }
        """
    )
    print("frame", info)
    dom = await read_dom_snapshot(page)
    print("dom_count", dom.get("message_count"), dom.get("selector_profile"))
    for m in dom.get("messages") or []:
        print(m)
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
