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
async () => {
  const ctx = window.__monaGlobalStore?.getData?.('initContextData');
  const im = ctx?.im;
  let store = null;
  ctx?.doAction?.((s) => { store = s; });
  const conv = store?.conversationsInfo?.currentConversation || {};
  const convId = conv?.id || '';
  const talkId = conv?.talkId || conv?.talk_id || '';
  const convKeys = Object.keys(conv || {});
  const convSample = {};
  for (const k of convKeys.slice(0, 25)) {
    const v = conv[k];
    convSample[k] = typeof v === 'object' ? (Array.isArray(v) ? `array(${v.length})` : 'object') : String(v).slice(0, 80);
  }
  const attempts = [];
  const variants = [
    ['pullMessagesByConversationId', convId],
    ['pullMessagesByConversationId', { conversationId: convId }],
    ['getHistoryMessages', convId],
    ['getHistoryMessages', { conversationId: convId, limit: 20 }],
    ['getHistoryMessages', { conversationId: convId, count: 20 }],
    ['getMessagesByConversation', conv],
    ['getMessagesByConversation', { conversation: conv }],
    ['getMessagesByConversationId', convId],
    ['getLatestMessageListByConversationId', convId],
  ];
  for (const [name, arg] of variants) {
    if (!im || typeof im[name] !== 'function') continue;
    try {
      const r = await im[name](arg);
      let list = r;
      if (r && typeof r === 'object' && !Array.isArray(r)) {
        list = r.messages || r.messageList || r.list || r.data || [];
      }
      const arr = Array.isArray(list) ? list : [];
      attempts.push({
        name,
        argType: typeof arg,
        len: arr.length,
        sample: arr.slice(-2).map((m) => String(m?.content || m?.text || '').slice(0, 60)),
      });
    } catch (e) {
      attempts.push({ name, error: String(e).slice(0, 120) });
    }
  }
  return { convId: convId.slice(0, 50), talkId, convSample, attempts, messageListLen: store?.sessionDetails?.messageList?.length || 0 };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    data = await im.evaluate(PROBE)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
