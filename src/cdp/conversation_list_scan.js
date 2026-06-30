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
    const sr = String(m?.sender_role || m?.ext?.sender_role || m?.role || "");
    if (sr === "1" || sr === "customer" || sr === "buyer") return "customer";
    if (sr === "2" || sr === "service" || sr === "seller") return "service";
    if (sr === "3" || sr === "system") return "system";
    if (sr === "robot") return "robot";
    return "unknown";
  };

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

  const msgText = (m) => String(m?.content || m?.text || "").trim();

  const isCard = (text) => /卡片|【/.test(String(text || ""));

  const isServiceRole = (role) => role === "service" || role === "seller";
  const isIgnorable = (role) => role === "system" || role === "robot";

  const parseTimeMs = (t) => {
    const s = String(t || "").trim();
    if (!s) return null;
    if (/^\d{1,2}:\d{2}$/.test(s)) return null;
    const n = Number(s);
    if (!Number.isNaN(n) && n > 0) return n > 1e12 ? n : n * 1000;
    const d = Date.parse(s);
    return Number.isNaN(d) ? null : d;
  };

  const analyzeRows = (rows) => {
    let latestCustomer = null;
    let latestService = null;
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const role = normalizeRole(rows[i]);
      const text = msgText(rows[i]);
      if (!latestCustomer && role === "customer" && text) {
        latestCustomer = { row: rows[i], role, text, index: i };
      }
      if (!latestService && isServiceRole(role)) {
        latestService = { row: rows[i], role, text, index: i };
      }
      if (latestCustomer && latestService) break;
    }

    const last = rows[rows.length - 1];
    const lastRole = normalizeRole(last);
    const lastText = msgText(last);

    let hasUnreplied = false;
    let pendingCount = 0;
    if (latestCustomer) {
      if (!latestService) {
        hasUnreplied = true;
        pendingCount = 1;
      } else {
        const custMs = parseTimeMs(msgTime(latestCustomer.row));
        const svcMs = parseTimeMs(msgTime(latestService.row));
        if (custMs != null && svcMs != null) {
          hasUnreplied = custMs > svcMs;
        } else {
          hasUnreplied = latestCustomer.index > latestService.index;
        }
        if (hasUnreplied) {
          pendingCount = 0;
          for (let i = latestService.index + 1; i < rows.length; i += 1) {
            const role = normalizeRole(rows[i]);
            if (isIgnorable(role)) continue;
            if (role === "customer" && msgText(rows[i])) pendingCount += 1;
          }
          pendingCount = Math.max(pendingCount, 1);
        }
      }
    }

    return {
      last,
      lastRole,
      lastText,
      latestCustomer,
      latestService,
      hasUnreplied,
      pendingCount,
    };
  };

  const conversations = [];
  for (const key of msgMap.keys()) {
    const bucket = msgMap.get(key);
    const inner = bucket?.map;
    if (!inner || typeof inner.values !== "function") continue;
    const rows = [...inner.values()].sort(
      (a, b) => Number(a?.create_time || a?.created_at || 0) - Number(b?.create_time || b?.created_at || 0)
    );
    if (!rows.length) continue;

    const analysis = analyzeRows(rows);
    const { last, lastRole, lastText, latestCustomer, latestService, hasUnreplied, pendingCount } =
      analysis;

    const lastMessageId = msgId(last);
    const lastMessageTime = msgTime(last);
    const conv = convMap?.get?.(key) || {};
    const closed = Boolean(conv?.closed);
    const unread = Boolean(conv?.unread_count || conv?.unreadCount || conv?.unread);
    const cardOnly = isCard(lastText) || (!lastText && rows.some((r) => isCard(msgText(r))));

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
    if (hasUnreplied) score += 120;
    if (unread) score += 40;
    if (!closed) score += 50;
    if (cardOnly) score -= 60;
    if (closed) score -= 30;
    score += Math.min(lastText.length, 40);

    conversations.push({
      conversation_id: key,
      customer_name: name,
      buyer_name_source: resolved.source || "",
      last_message_text: lastText.slice(0, 120),
      last_message_role: lastRole,
      last_message_id: lastMessageId,
      last_message_time: lastMessageTime,
      latest_customer_message_text: latestCustomer ? latestCustomer.text.slice(0, 120) : "",
      latest_customer_message_id: latestCustomer ? msgId(latestCustomer.row) : "",
      latest_customer_message_time: latestCustomer ? msgTime(latestCustomer.row) : "",
      latest_service_message_id: latestService ? msgId(latestService.row) : "",
      latest_service_message_time: latestService ? msgTime(latestService.row) : "",
      has_unreplied_customer_message: hasUnreplied && !closed,
      pending_customer_count: hasUnreplied ? pendingCount : 0,
      input_maybe_available: !closed,
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
