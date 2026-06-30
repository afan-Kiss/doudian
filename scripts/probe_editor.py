import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config

PROBE = """
() => {
  const out = [];
  for (const sel of [
    '.public-DraftEditor-content[contenteditable="true"]',
    '[contenteditable="true"]',
    'textarea',
    'div[role="textbox"]',
    '[class*="editor"] [contenteditable="true"]',
    '[class*="Editor"]',
    '[class*="input-area"] textarea',
    '[class*="input-area"] [contenteditable="true"]',
    '[class*="chat-input"]',
    '[class*="ChatInput"]',
    '[class*="im-input"]',
    '[class*="send-box"] textarea',
    '[class*="send-box"] [contenteditable="true"]',
    '[placeholder*="输入"]',
    '[placeholder*="回复"]',
    '[placeholder*="说点"]',
  ]) {
    const nodes = document.querySelectorAll(sel);
    if (!nodes.length) continue;
    const last = nodes[nodes.length - 1];
    out.push({
      sel,
      count: nodes.length,
      placeholder: last.getAttribute?.('placeholder') || '',
      className: String(last.className || '').slice(0, 80),
      visible: !!(last.offsetWidth && last.offsetHeight),
    });
  }
  return { url: location.href, out };
}
"""


async def main() -> None:
    launcher = BrowserLauncher(load_config())
    page = await launcher.get_active_feige_page()
    rows = []
    for frame in page.frames:
        try:
            rows.append({"url": frame.url[:80], **(await frame.evaluate(PROBE))})
        except Exception as exc:
            rows.append({"url": frame.url[:80], "error": str(exc)})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    await launcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
