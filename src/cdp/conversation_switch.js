/**
 * Open a Feige conversation with verification. Args: { conversationId, customerName }
 */
async (payload) => {
  const key = String(payload?.conversationId || payload || "").trim();
  const customerName = String(payload?.customerName || payload?.nickname || "").trim();
  if (!key) return { ok: false, reason: "empty-id", verified: false };

  const norm = (id) => {
    const value = String(id || "").trim();
    if (value.startsWith("n") && value.length > 24 && value[1] === value[1].toUpperCase()) {
      return value.slice(1);
    }
    return value;
  };
  const targetNorm = norm(key);

  const readCurrent = () => {
    let currentId = "";
    let currentName = "";
    try {
      const ctx = window.__monaGlobalStore?.getData?.("initContextData");
      ctx?.doAction?.((store) => {
        const conv =
          store?.conversationsInfo?.currentConversation ||
          store?.sessionDetails?.currentConversation ||
          {};
        currentId = String(conv.id || conv.conversationId || "");
        currentName = String(conv.name || conv.nickname || conv.userName || "").trim();
      });
    } catch (e) {}
    return { currentId, currentName };
  };

  const idsMatch = (a, b) => {
    const x = norm(a);
    const y = norm(b);
    return Boolean(x && y && (x === y || x.includes(y) || y.includes(x)));
  };

  const inputVisible = () => {
    const selectors = [
      'textarea[class*="inputArea"]',
      'textarea[placeholder*="Enter"]',
      '[contenteditable="true"][role="textbox"]',
      '[contenteditable="true"]',
      'textarea',
    ];
    for (const sel of selectors) {
      const nodes = document.querySelectorAll(sel);
      if (nodes.length) return true;
    }
    return false;
  };

  const clickSidebar = () => {
    const tryClick = (el) => {
      if (!el) return false;
      try {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        el.click?.();
        return true;
      } catch (e) {
        return false;
      }
    };
    const selectors = [
      '[class*="conversation"]',
      '[class*="session"]',
      '[class*="chat-item"]',
      '[data-testid*="conversation"]',
      'li[role="listitem"]',
    ];
    for (const sel of selectors) {
      const nodes = document.querySelectorAll(sel);
      for (const node of nodes) {
        const text = String(node.innerText || node.textContent || "").trim();
        if (customerName && text.includes(customerName)) {
          if (tryClick(node)) return true;
        }
        if (node.getAttribute?.("data-id") === key) {
          if (tryClick(node)) return true;
        }
      }
    }
    return false;
  };

  const ctx = window.__monaGlobalStore?.getData?.("initContextData");
  const im = ctx?.im;
  if (!im) return { ok: false, reason: "no-im", verified: false };

  let method = "sdk";
  try {
    if (typeof im.ensureConversation === "function") await im.ensureConversation(key);
    else if (typeof im.openConversation === "function") await im.openConversation(key);
    else return { ok: false, reason: "no-open-method", verified: false };
  } catch (e) {
    return { ok: false, reason: String(e?.message || e), verified: false };
  }

  await new Promise((r) => setTimeout(r, 800));
  let { currentId, currentName } = readCurrent();
  if (!idsMatch(currentId, key)) {
    method = "dom_click";
    clickSidebar();
    await new Promise((r) => setTimeout(r, 900));
    ({ currentId, currentName } = readCurrent());
  }

  if (!idsMatch(currentId, key)) {
    try {
      if (typeof im.openConversation === "function") await im.openConversation(key);
      await new Promise((r) => setTimeout(r, 700));
      ({ currentId, currentName } = readCurrent());
      method = "search";
    } catch (e) {}
  }

  const pullTasks = [
    () => im.pullMessagesByConversationId?.(key),
    () => im.getHistoryMessages?.({ conversationId: key, limit: 30 }),
  ];
  for (const task of pullTasks) {
    try {
      await task?.();
    } catch (e) {}
  }

  const verified = idsMatch(currentId, key);
  const inputOk = inputVisible();
  return {
    ok: verified,
    conversation_id: key,
    verified,
    method,
    input_available: inputOk,
    current_conversation_id: currentId,
    current_customer_name: currentName,
    reason: verified ? "ok" : "conversation_mismatch",
  };
}
