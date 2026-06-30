/**
 * Scan Feige left conversation list via Mona SDK (injected in IM frame).
 */
async (opts = {}) => {
  const nameCache = opts?.nameCache || {};
  const extract = window.__feigeBuyerNameExtract;
  const ctx = window.__monaGlobalStore?.getData?.("initContextData");
  let store = null;
  ctx?.doAction?.((s) => {
    store = s;
  });
  const msgMap = store?.conversationsInfo?.messagesByConversationId;
  const convMap = store?.conversationsInfo?.conversationMap;
  if (!msgMap) return { ok: false, reason: "no-msg-map", conversations: [] };

  const listNameMap = extract?.collectListNames?.(store) || new Map();
  const sidebarDom = extract?.scanSidebarDomNames?.() || { byId: new Map(), ordered: [] };
  const domOrderMap = extract?.correlateSidebarOrder?.(store, sidebarDom.ordered || []) || new Map();
  const currentConvId = String(
    store?.conversationsInfo?.currentConversation?.id ||
      store?.sessionDetails?.currentConversation?.id ||
      ""
  );

  const normalizeRole = (m) => {
    const sr = String(m?.sender_role || m?.ext?.sender_role || "");
    if (sr === "1") return "customer";
    if (sr === "2") return "service";
    if (sr === "3") return "system";
    return "unknown";
  };

  const isCard = (text) => /卡片|【/.test(String(text || ""));

  const conversations = [];
  for (const key of msgMap.keys()) {
    const bucket = msgMap.get(key);
    const inner = bucket?.map;
    if (!inner || typeof inner.values !== "function") continue;
    const rows = [...inner.values()].sort(
      (a, b) => Number(a?.create_time || a?.created_at || 0) - Number(b?.create_time || b?.created_at || 0)
    );
    if (!rows.length) continue;
    const last = rows[rows.length - 1];
    const role = normalizeRole(last);
    const text = String(last?.content || last?.text || "").trim();
    const lastMessageId = String(
      last?.server_message_id ||
        last?.serverId ||
        last?.client_message_id ||
        last?.messageId ||
        last?.id ||
        ""
    );
    const lastMessageTime = String(
      last?.create_time || last?.created_at || last?.timestamp || last?.time || ""
    );
    const conv = convMap?.get?.(key) || {};
    const closed = Boolean(conv?.closed);
    const unread = Boolean(conv?.unread_count || conv?.unreadCount || conv?.unread);
    const cardOnly = isCard(text) || (!text && rows.some((r) => isCard(String(r?.content || r?.text || ""))));

    const resolved = extract?.resolveBuyerName?.({
      convId: key,
      conv,
      store,
      messages: rows,
      listNameMap,
      domNameMap: domOrderMap.size ? domOrderMap : sidebarDom.byId,
      cachedName: nameCache[key] || "",
      isCurrent: String(key) === currentConvId,
    }) || { name: "", source: "" };
    const name = String(resolved.name || "").trim();

    let score = 0;
    if (role === "customer") score += 100;
    if (!closed) score += 50;
    if (unread) score += 40;
    if (cardOnly) score -= 60;
    if (closed) score -= 30;
    score += Math.min(text.length, 40);
    conversations.push({
      conversation_id: key,
      customer_name: name,
      buyer_name_source: resolved.source || "",
      last_message_text: text.slice(0, 120),
      last_message_role: role,
      last_message_id: lastMessageId,
      last_message_time: lastMessageTime,
      unread,
      closed,
      card_only: cardOnly,
      message_count: rows.length,
      score,
    });
  }
  conversations.sort((a, b) => b.score - a.score);
  return { ok: true, conversations, count: conversations.length };
}
