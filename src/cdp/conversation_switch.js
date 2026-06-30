/**
 * Open a Feige conversation — SDK first, DOM click fallback, header verify.
 */
async (payload) => {
  const key = String(payload?.conversationId || payload?.id || "").trim();
  const customerName = String(
    payload?.customerName || payload?.nickname || payload?.buyer_name || ""
  ).trim();
  const lastText = String(payload?.lastText || payload?.last_text || "").trim();
  const rowIndex = Number(payload?.rowIndex ?? payload?.dom_row_index ?? -1);

  const utils = window.__feigeMessageUtils;
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
    if (!currentName) currentName = utils?.pickHeaderTitle?.() || "";
    return { currentId, currentName };
  };

  const idsMatch = (a, b) => {
    const x = norm(a);
    const y = norm(b);
    return Boolean(x && y && !x.startsWith("dom:") && (x === y || x.includes(y) || y.includes(x)));
  };

  const namesMatch = (a, b) => utils?.namesRoughMatch?.(a, b) || String(a).trim() === String(b).trim();

  const inputVisible = () => {
    const selectors = [
      'textarea[class*="inputArea"]',
      'textarea[placeholder*="Enter"]',
      '[contenteditable="true"][role="textbox"]',
      '[contenteditable="true"]',
      "textarea",
    ];
    for (const sel of selectors) {
      if (document.querySelector(sel)) return true;
    }
    return false;
  };

  const verifySwitch = () => {
    const { currentId, currentName } = readCurrent();
    const idOk = key && !key.startsWith("dom:") ? idsMatch(currentId, key) : false;
    const nameOk = customerName ? namesMatch(currentName, customerName) : false;
    const headerOk = customerName
      ? namesMatch(utils?.pickHeaderTitle?.() || "", customerName)
      : false;
    const inputOk = inputVisible();
    const verified = (idOk || nameOk || headerOk) && inputOk;
    return {
      verified,
      currentId,
      currentName,
      inputOk,
      idOk,
      nameOk,
      headerOk,
    };
  };

  let method = "none";
  const ctx = window.__monaGlobalStore?.getData?.("initContextData");
  const im = ctx?.im;

  if (im && key && !key.startsWith("dom:")) {
    method = "sdk";
    try {
      if (typeof im.ensureConversation === "function") await im.ensureConversation(key);
      else if (typeof im.openConversation === "function") await im.openConversation(key);
    } catch (e) {
      method = "sdk-failed";
    }
    await new Promise((r) => setTimeout(r, 800));
  }

  let check = verifySwitch();
  if (!check.verified && customerName) {
    method = method === "sdk" ? "sdk+dom" : "dom";
    const click = utils?.clickSessionRow?.({
      buyerName: customerName,
      lastText,
      rowIndex,
    });
    await new Promise((r) => setTimeout(r, 1200));
    check = verifySwitch();
    if (!check.verified && click?.ok) {
      await new Promise((r) => setTimeout(r, 800));
      check = verifySwitch();
    }
  }

  if (!check.verified && im && key && !key.startsWith("dom:")) {
    try {
      if (typeof im.openConversation === "function") await im.openConversation(key);
      await new Promise((r) => setTimeout(r, 700));
      check = verifySwitch();
      method = "sdk-retry";
    } catch (e) {}
  }

  if (im && key && !key.startsWith("dom:")) {
    const pullTasks = [
      () => im.pullMessagesByConversationId?.(key),
      () => im.getHistoryMessages?.({ conversationId: key, limit: 30 }),
    ];
    for (const task of pullTasks) {
      try {
        await task?.();
      } catch (e) {}
    }
  }

  const { verified, currentId, currentName, inputOk, nameOk, headerOk, idOk } = check;
  return {
    ok: verified,
    conversation_id: key,
    verified,
    method,
    input_available: inputOk,
    current_conversation_id: currentId,
    current_customer_name: currentName,
    header_name: utils?.pickHeaderTitle?.() || "",
    name_ok: nameOk,
    header_ok: headerOk,
    id_ok: idOk,
    reason: verified ? "ok" : "conversation_mismatch",
  };
};
