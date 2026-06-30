import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.frame_context import find_im_frame

DIAG_JS = """
async () => {
  const out = { bubbles: [], headers: [] };
  const all = document.querySelectorAll('div, span, p');
  for (const el of all) {
    const t = String(el.innerText || "").trim();
    if (!t || t.length > 200) continue;
    if (/发货|快递|订单号|欢迎光临|什么时候|摸头|青蛙|钢铁侠/.test(t)) {
      const rect = el.getBoundingClientRect();
      out.bubbles.push({
        text: t.slice(0, 120),
        class_name: String(el.className || "").slice(0, 160),
        tag: el.tagName,
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        parent_class: String(el.parentElement?.className || "").slice(0, 120),
      });
    }
  }
  for (const sel of ['[class*="message"]', '[class*="msg"]', '[class*="chat"]', '[class*="bubble"]']) {
    const nodes = document.querySelectorAll(sel);
    if (nodes.length > 2 && nodes.length < 80) {
      out.headers.push({ sel, count: nodes.length, sample_class: String(nodes[0]?.className||"").slice(0,100) });
    }
  }
  return out;
}
"""


async def main():
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    im = await find_im_frame(page)
    result = await im.evaluate(DIAG_JS)
    out_path = ROOT.parent / "data" / "diag-chat.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    asyncio.run(main())
