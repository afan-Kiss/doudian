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
() => {
  const ctx = window.__monaGlobalStore?.getData?.('initContextData');
  let store = null;
  ctx?.doAction?.((s) => { store = s; });
  const conv = store?.conversationsInfo?.currentConversation || {};
  const convId = conv?.id || '';
  const rawMap = store?.conversationsInfo?.messagesByConversationId;
  const out = { convId: convId.slice(0, 50), entries: [] };
  if (!rawMap || typeof rawMap.get !== 'function') return out;
  for (const key of [...rawMap.keys()].slice(0, 3)) {
    const val = rawMap.get(key);
    const entry = { key: String(key).slice(0, 45), valType: typeof val };
    if (Array.isArray(val)) {
      entry.arrayLen = val.length;
      entry.sample = val.slice(-2).map((m) => String(m?.content || m?.text || '').slice(0, 60));
    } else     if (val && typeof val === 'object') {
      entry.objKeys = Object.keys(val).slice(0, 20);
      const inner = val.map;
      if (inner && typeof inner.get === 'function') {
        entry.innerSize = inner.size;
        entry.innerKeys = [...inner.keys()].slice(0, 3).map(String);
        const msgs = [...inner.values()].slice(-3);
        entry.innerSample = msgs.map((m) => ({
          content: String(m?.content || m?.text || '').slice(0, 60),
          sender_role: m?.sender_role || m?.ext?.sender_role,
        }));
      }
    }
    out.entries.push(entry);
  }
  out.sessionListLen = (store?.sessionDetails?.messageList || []).length;
  return out;
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    data = await im.evaluate(PROBE)
    out_path = ROOT / "probe_out.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}, entries={len(data.get('entries', []))}")
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
