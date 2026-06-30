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
  const out = { sidebar_candidates: [], list_items: [], header: [], left_panel_text: "" };
  const leftPanels = document.querySelectorAll('[class*="left"], [class*="sidebar"], [class*="sider"], [class*="session"]');
  for (const panel of leftPanels) {
    const rect = panel.getBoundingClientRect();
    if (rect.width < 80 || rect.width > 500) continue;
    if (rect.left > 200) continue;
    const text = String(panel.innerText || "").slice(0, 500);
    if (text.includes("当前会话") || text.includes("钢铁侠") || text.includes("青蛙")) {
      out.left_panel_text = text;
      out.left_panel_class = String(panel.className || "").slice(0, 200);
      const items = panel.querySelectorAll("*");
      for (const el of items) {
        const t = String(el.innerText || "").trim();
        if (!t || t.length > 120 || t.length < 4) continue;
        if (/钢铁侠|青蛙|发货|快递|欢迎/.test(t)) {
          out.sidebar_candidates.push({
            tag: el.tagName,
            class_name: String(el.className || "").slice(0, 160),
            text: t.slice(0, 120),
            child_count: el.children.length,
          });
        }
      }
    }
  }
  const all = document.querySelectorAll('div, li, span');
  for (const el of all) {
    const t = String(el.innerText || "").trim();
    if (/^钢铁侠/.test(t) || /^一只小青蛙/.test(t)) {
      out.list_items.push({
        tag: el.tagName,
        class_name: String(el.className || "").slice(0, 160),
        text: t.slice(0, 120),
        parent_class: String(el.parentElement?.className || "").slice(0, 120),
      });
    }
  }
  for (const sel of ['[class*="title"]', '[class*="nick"]', '[class*="name"]', '[class*="header"]']) {
    for (const el of document.querySelectorAll(sel)) {
      const t = String(el.innerText || "").trim();
      if (t && t.length < 30) out.header.push({ sel, text: t, class_name: String(el.className||"").slice(0,100) });
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
    out_path = ROOT.parent / "data" / "diag-sidebar.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    asyncio.run(main())
