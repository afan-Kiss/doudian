/**
 * Scan Feige conversation list — DOM-first, SDK supplements ids.
 */
async (opts = {}) => {
  const nameCache = opts?.nameCache || {};
  const extract = window.__feigeBuyerNameExtract;
  const utils = window.__feigeMessageUtils;
  const ctx = window.__monaGlobalStore?.getData?.("initContextData");
  let store = null;
  ctx?.doAction?.((s) => {
    store = s;
  });
  const msgMap = store?.conversationsInfo?.messagesByConversationId;
  const convMap = store?.conversationsInfo?.conversationMap;
  const listNameMap = extract?.collectListNames?.(store) || new Map();
  const currentConvId = String(
    store?.conversationsInfo?.currentConversation?.id ||
      store?.sessionDetails?.currentConversation?.id ||
      ""
  );

  const msgId = (m) =>
    String(
      m?.server_message_id ||
        m?.serverId ||
        m?.client_message_id ||
        m?.messageId ||
        m?.id ||
        ""
    );
  const msgTime = (m) => String(m?.create_time || m?.created_at || m?.timestamp || m?.time || "");
  const msgText = (m) => String(m?.content || m?.text || m?.message || "").trim();
  const isCard = (text) => /卡片|【/.test(String(text || ""));

  const domRows = (utils?.scanSessionRowsDom?.() || []).map((row) => ({
    ...row,
    conversation_id: "",
    source: "dom",
  }));

  const finalizeConv = (conv) => utils?.applySystemNoticeFields?.(conv) || conv;

  const sdkByName = new Map();
  const conversations = [];
  const usedDom = new Set();

  if (msgMap) {
    for (const key of msgMap.keys()) {
      const bucket = msgMap.get(key);
      const inner = bucket?.map;
      if (!inner || typeof inner.values !== "function") continue;
      const rows = [...inner.values()].sort(
        (a, b) =>
          Number(a?.create_time || a?.created_at || 0) - Number(b?.create_time || b?.created_at || 0)
      );
      if (!rows.length) continue;

      const analysis = utils?.analyzeUnreplied
        ? utils.analyzeUnreplied(rows)
        : { hasUnreplied: false, pendingCount: 0, lastRole: "unknown", lastText: "", lastMessageId: "", lastMessageTime: "", latestCustomer: null };

      const conv = convMap?.get?.(key) || {};
      let closed = Boolean(conv?.closed);
      const unread = Boolean(conv?.unread_count || conv?.unreadCount || conv?.unread);
      const lastText = analysis.lastText || msgText(rows[rows.length - 1]);
      const lastRole = analysis.lastRole || "unknown";
      const last = rows[rows.length - 1];
      const cardOnly = isCard(lastText) || (!lastText && rows.some((r) => isCard(msgText(r))));

      const resolved = extract?.resolveBuyerName?.({
        convId: key,
        conv,
        store,
        messages: rows,
        listNameMap,
        cachedName: nameCache[key] || "",
        isCurrent: String(key) === currentConvId,
      }) || { name: "", source: "" };
      const name = String(resolved.name || conv.name || conv.nickname || "").trim();

      if (name) sdkByName.set(name, key);

      let hasUnreplied = Boolean(analysis.hasUnreplied);
      let pendingCount = Number(analysis.pendingCount || 0);
      let latestCustomerText = analysis.latestCustomer?.text || "";
      let source = "sdk";

      const domMatch =
        domRows.find((d) => d.buyer_name === name) ||
        domRows.find((d) => utils?.namesRoughMatch?.(d.buyer_name, name));
      if (domMatch) {
        usedDom.add(domMatch.row_index);
        source = "mixed";
        const domPreview = String(domMatch.last_text || "").trim();
        const looksService = /欢迎光临|有什么可以帮助|Hi[,，]/.test(domPreview);
        if (/已关闭|关闭会话|超时未回复/.test(domMatch.raw_text || "")) {
          closed = true;
        } else if (domPreview && !looksService) {
          closed = false;
        }
        const looksCustomer =
          domPreview &&
          !looksService &&
          domPreview.length >= 2 &&
          !/^\[[^\]]+\]$/.test(domPreview);
        if (looksCustomer && !hasUnreplied) {
          const svc = analysis.latestSubstantiveService;
          if (!svc || domPreview !== svc.text) {
            hasUnreplied = true;
            pendingCount = Math.max(pendingCount, 1);
            latestCustomerText = domPreview;
          }
        }
        if (domMatch.unread_badge && !hasUnreplied) {
          hasUnreplied = true;
          pendingCount = Math.max(pendingCount, 1);
        }
      }

      if (String(key) === currentConvId) {
        const inputOk = Boolean(
          document.querySelector('textarea[class*="inputArea"], [contenteditable="true"], textarea')
        );
        if (inputOk) closed = false;
        const domChat = utils?.collectFeigeChatMessages?.() || [];
        const domAnalysis = utils?.analyzeUnreplied?.(
          domChat.map((m) => ({ role: m.role, text: m.text, time: m.time_text }))
        );
        if (domAnalysis?.hasUnreplied) {
          hasUnreplied = true;
          pendingCount = Math.max(pendingCount, domAnalysis.pendingCount || 1);
          latestCustomerText =
            domAnalysis.latestCustomer?.text || latestCustomerText || analysis.lastText;
          closed = false;
        }
      }

      if (closed && hasUnreplied && /系统关闭|超时未回复/.test(lastText)) {
        const inputOk = Boolean(
          document.querySelector('textarea[class*="inputArea"], [contenteditable="true"], textarea')
        );
        if (inputOk) closed = false;
      }

      let score = 0;
      if (hasUnreplied) score += 120;
      if (unread) score += 40;
      if (!closed) score += 50;
      if (cardOnly) score -= 60;
      if (closed) score -= 30;
      score += Math.min(lastText.length, 40);

      conversations.push(
        finalizeConv({
        conversation_id: key,
        customer_name: name,
        buyer_name: name,
        buyer_name_source: resolved.source || "",
        last_message_text: lastText.slice(0, 120),
        last_message_role: lastRole,
        last_message_id: analysis.lastMessageId || msgId(last),
        last_message_time: analysis.lastMessageTime || msgTime(last),
        latest_customer_message_text: latestCustomerText.slice(0, 200),
        latest_customer_message_id: analysis.latestCustomer?.message_id || "",
        latest_customer_message_time: analysis.latestCustomer?.time || "",
        has_unreplied_customer_message: hasUnreplied && !closed,
        pending_customer_count: hasUnreplied ? Math.max(pendingCount, 1) : 0,
        input_maybe_available: !closed,
        unread,
        closed,
        card_only: cardOnly,
        message_count: rows.length,
        score,
        source,
        dom_row_index: domMatch?.row_index ?? -1,
        })
      );
    }
  }

  for (const dom of domRows) {
    if (usedDom.has(dom.row_index)) continue;
    const name = dom.buyer_name;
    const preview = String(dom.last_text || "").trim();
    const looksService = /欢迎光临|有什么可以帮助|Hi[,，]/.test(preview);
    const hasUnreplied = Boolean(preview && !looksService && preview.length >= 2) || Boolean(dom.unread_badge);
    const closed = /已关闭|关闭会话/.test(dom.raw_text || "");
    let score = 0;
    if (hasUnreplied) score += 100;
    if (dom.unread_badge) score += 40;
    if (!closed) score += 40;
    score += Math.min(preview.length, 30);

    const sdkId = sdkByName.get(name) || `dom:${name}:${dom.row_index}`;
    conversations.push(
      finalizeConv({
      conversation_id: sdkId,
      customer_name: name,
      buyer_name: name,
      buyer_name_source: "dom-sidebar",
      last_message_text: preview.slice(0, 120),
      last_message_role: looksService ? "service" : "customer",
      last_message_id: "",
      last_message_time: dom.time_text || "",
      latest_customer_message_text: looksService ? "" : preview.slice(0, 200),
      latest_customer_message_id: "",
      latest_customer_message_time: dom.time_text || "",
      has_unreplied_customer_message: hasUnreplied && !closed,
      pending_customer_count: hasUnreplied ? 1 : 0,
      input_maybe_available: !closed,
      unread: Boolean(dom.unread_badge),
      closed,
      card_only: false,
      message_count: 0,
      score,
      source: "dom",
      dom_row_index: dom.row_index,
      })
    );
  }

  if (!conversations.length && !domRows.length) {
    return { ok: false, reason: "no-conversations", conversations: [] };
  }

  conversations.sort((a, b) => b.score - a.score);
  const system_notice_count = conversations.filter((c) => c.is_system_notice).length;
  return {
    ok: true,
    conversations,
    count: conversations.length,
    dom_row_count: domRows.length,
    system_notice_count,
  };
};
