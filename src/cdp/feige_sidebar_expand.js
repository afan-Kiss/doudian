/**
 * Auto-open Feige "当前会话" tab and expand collapsed sidebar groups.
 */
async () => {
  const utils = window.__feigeMessageUtils;
  const clicked = [];
  const tryClick = (el) => {
    if (!el) return false;
    try {
      el.scrollIntoView({ block: "center", inline: "nearest" });
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      el.click?.();
      return true;
    } catch (e) {
      return false;
    }
  };

  const clickByText = (pattern, opts = {}) => {
    const maxLeft = opts.maxLeft ?? 320;
    for (const el of document.querySelectorAll("div, span, li, button, a")) {
      const text = String(el.innerText || el.textContent || "").trim();
      if (!text || text.length > 40) continue;
      if (!pattern.test(text)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (opts.sidebarOnly !== false && rect.left > maxLeft) continue;
      if (tryClick(el)) {
        clicked.push(text.slice(0, 40));
        return true;
      }
    }
    return false;
  };

  clickByText(/^当前会话(\(\d+\))?$/);
  clickByText(/^最近联系$/);
  clickByText(/^人工已回复\(\d+\)$/);
  clickByText(/^等待回复\(\d+\)$/);
  clickByText(/^未回复\(\d+\)$/);

  for (const sel of [".list_items", "[class*='list_items']", "[class*='pigeonChatNotScrollBox']"]) {
    const node = document.querySelector(sel);
    if (node) {
      node.scrollTop = 0;
      tryClick(node);
    }
  }

  await new Promise((r) => setTimeout(r, 600));

  const rows = utils?.scanSessionRowsDom?.() || [];
  return {
    ok: true,
    clicked,
    row_count: rows.length,
    buyer_names: rows.map((r) => r.buyer_name).slice(0, 10),
  };
};
