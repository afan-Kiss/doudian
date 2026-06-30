/**
 * Shared Feige buyer nickname extraction (Mona SDK + DOM).
 * Exposes window.__feigeBuyerNameExtract for scan/snapshot scripts.
 */
(() => {
  const NAME_FIELDS = [
    "nickname",
    "nickName",
    "nick_name",
    "userName",
    "username",
    "screenName",
    "displayName",
    "display_name",
    "buyerName",
    "buyer_name",
    "customerName",
    "customer_name",
    "name",
    "title",
    "remarkName",
    "remark_name",
    "uname",
    "peerName",
    "user_name",
  ];

  const NOISE =
    /欢迎光临|商家配置|客服\S*接入|超时未回复|系统关闭会话|人工客服|请问有什么可以帮助|CurrentServer|paas_|track_info|用户已等待|请尽快回复|无意义内容|common_|llm_intent|c_foot|msg_foot|已读|未读|全部会话|当前会话|留言|历史会话/;

  const isInternalId = (text) => {
    const s = String(text || "").trim();
    if (!s) return true;
    if (s.startsWith("AQ") && s.length > 24) return true;
    if (s.includes("pigeon") || s.includes("::")) return true;
    if (/^[a-f0-9]{16,}$/i.test(s)) return true;
    return false;
  };

  const isGoodName = (text) => {
    const s = String(text || "").trim();
    if (!s || isInternalId(s)) return false;
    if (s.length > 40) return false;
    if (NOISE.test(s)) return false;
    if (/^\d+$/.test(s)) return false;
    return true;
  };

  const tryParseJson = (raw) => {
    if (raw && typeof raw === "object") return raw;
    if (!raw || typeof raw !== "string") return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };

  const pickFromObject = (obj, depth = 0) => {
    if (!obj || typeof obj !== "object" || depth > 3) return "";
    for (const f of NAME_FIELDS) {
      const v = obj[f];
      if (isGoodName(v)) return String(v).trim();
    }
    for (const nested of ["user", "customer", "buyer", "peer", "profile", "member"]) {
      const v = pickFromObject(obj[nested], depth + 1);
      if (v) return v;
    }
    return "";
  };

  const pickFromMessage = (m) => {
    const ext = m?.ext || {};
    const biz = tryParseJson(ext.biz_ext) || ext.biz_ext || {};
    const foot = tryParseJson(ext.foot_desc) || ext.foot_desc || {};
    const candidates = [
      ext.nickname,
      ext.nickName,
      ext.uname,
      ext.userName,
      ext.displayName,
      ext.display_name,
      biz?.nickname,
      biz?.nickName,
      biz?.uname,
      biz?.userName,
      foot?.nickname,
      m?.nickname,
      m?.userName,
      m?.uname,
    ];
    for (const c of candidates) {
      if (isGoodName(c)) return String(c).trim();
    }
    return "";
  };

  const pickFromMessages = (rows) => {
    if (!Array.isArray(rows) || !rows.length) return "";
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const m = rows[i];
      const sr = String(m?.sender_role || m?.ext?.sender_role || "");
      const direction = Number(m?.direction || m?.ext?.direction || 0);
      const isCustomer = sr === "1" || direction === 1;
      if (!isCustomer) continue;
      const name = pickFromMessage(m);
      if (name) return name;
    }
    for (const m of rows) {
      const name = pickFromMessage(m);
      if (name) return name;
    }
    return "";
  };

  const collectListNames = (store) => {
    const map = new Map();
    const push = (id, name) => {
      const convId = String(id || "").trim();
      const n = String(name || "").trim();
      if (!convId || !isGoodName(n)) return;
      if (!map.has(convId)) map.set(convId, n);
    };
    const visitList = (list) => {
      if (!Array.isArray(list)) return;
      for (const item of list) {
        if (!item || typeof item !== "object") continue;
        const id = item.id || item.conversationId || item.securityConversationId || "";
        const name = pickFromObject(item);
        push(id, name);
      }
    };
    try {
      const info = store?.conversationsInfo || {};
      visitList(info.conversationList);
      visitList(info.list);
      visitList(info.conversations);
      visitList(info.data?.list);
      visitList(info.data?.conversationList);
      const convMap = info.conversationMap;
      if (convMap && typeof convMap.forEach === "function") {
        convMap.forEach((item, key) => {
          const name = pickFromObject(item);
          push(key, name);
          push(item?.id || item?.conversationId, name);
        });
      }
    } catch {
      // ignore
    }
    return map;
  };

  const scanSidebarDomNames = () => {
    const byId = new Map();
    const ordered = [];
    const itemSelectors = [
      '[class*="conversation-list"] [class*="item"]',
      '[class*="session-list"] [class*="item"]',
      '[class*="conv-list"] li',
      '[class*="chat-list"] [class*="item"]',
      'li[role="listitem"]',
    ];
    const nameSelectors = [
      '[class*="nick"]',
      '[class*="name"]',
      '[class*="title"]',
      '[class*="user"]',
    ];
    const seen = new Set();
    for (const sel of itemSelectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (seen.has(el)) continue;
        seen.add(el);
        let name = "";
        for (const ns of nameSelectors) {
          const node = el.querySelector(ns);
          const text = String(node?.innerText || node?.textContent || "").trim().split("\n")[0].trim();
          if (isGoodName(text)) {
            name = text;
            break;
          }
        }
        if (!name) {
          const text = String(el.innerText || el.textContent || "").trim().split("\n")[0].trim();
          if (isGoodName(text)) name = text;
        }
        if (!name) continue;
        ordered.push(name);
        const dataId =
          el.getAttribute("data-id") ||
          el.getAttribute("data-conversation-id") ||
          el.getAttribute("data-key") ||
          "";
        if (dataId && !isInternalId(dataId)) byId.set(String(dataId), name);
      }
    }
    return { byId, ordered };
  };

  const correlateSidebarOrder = (store, orderedNames) => {
    const map = new Map();
    const ids = [];
    try {
      const info = store?.conversationsInfo || {};
      for (const list of [
        info.conversationList,
        info.list,
        info.conversations,
        info.data?.list,
      ]) {
        if (!Array.isArray(list)) continue;
        for (const item of list) {
          const id = item?.id || item?.conversationId;
          if (id) ids.push(String(id));
        }
      }
    } catch {
      // ignore
    }
    for (let i = 0; i < Math.min(ids.length, orderedNames.length); i += 1) {
      const id = ids[i];
      const name = orderedNames[i];
      if (id && name && isGoodName(name) && !map.has(id)) map.set(id, name);
    }
    return map;
  };

  const pickHeaderTitle = () => {
    const selectors = [
      '[class*="conversation-title"]',
      '[class*="chat-title"]',
      '[class*="nick-name"]',
      '[class*="nickname"]:not([class*="list"])',
      '[class*="user-name"]',
      '[class*="header"] [class*="name"]',
      '[class*="chat-header"] [class*="name"]',
    ];
    for (const sel of selectors) {
      const node = document.querySelector(sel);
      const text = String(node?.innerText || node?.textContent || "").trim();
      if (isGoodName(text)) return text;
    }
    return "";
  };

  const pickSessionDetails = (store) => {
    const sd = store?.sessionDetails || {};
    const cur =
      sd.currentConversation ||
      store?.conversationsInfo?.currentConversation ||
      sd.conversation ||
      {};
    return (
      pickFromObject(cur) ||
      pickFromObject(sd.customer) ||
      pickFromObject(sd.user) ||
      pickFromObject(sd)
    );
  };

  const lookupListName = (listMap, convId) => {
    if (!convId || !listMap) return "";
    const target = String(convId);
    if (listMap.has(target)) return listMap.get(target);
    for (const [k, v] of listMap) {
      const key = String(k);
      const prefixLen = Math.min(60, key.length, target.length);
      const prefix = target.slice(0, prefixLen);
      if (key.startsWith(prefix) || target.startsWith(key.slice(0, prefixLen))) return v;
    }
    return "";
  };

  const resolveBuyerName = ({
    convId = "",
    conv = {},
    store = null,
    messages = [],
    listNameMap = null,
    domNameMap = null,
    cachedName = "",
    isCurrent = false,
  }) => {
    const listMap = listNameMap || (store ? collectListNames(store) : new Map());
    const sidebarDom = domNameMap || scanSidebarDomNames();
    const domById =
      sidebarDom instanceof Map
        ? sidebarDom
        : sidebarDom?.byId || new Map();
    const domOrderMap =
      sidebarDom instanceof Map
        ? new Map()
        : correlateSidebarOrder(store, sidebarDom?.ordered || []);

    const fromList = lookupListName(listMap, convId);
    if (fromList) return { name: fromList, source: "sidebar-list" };

    const fromDomOrder = lookupListName(domOrderMap, convId);
    if (fromDomOrder) return { name: fromDomOrder, source: "dom-sidebar-order" };

    const fromDomId = lookupListName(domById, convId);
    if (fromDomId) return { name: fromDomId, source: "dom-sidebar-id" };

    if (isCurrent) {
      const header = pickHeaderTitle();
      if (header) return { name: header, source: "chat-header" };
    }

    if (isCurrent && store) {
      const sessionName = pickSessionDetails(store);
      if (sessionName) return { name: sessionName, source: "sessionDetails" };
    }

    const convName = pickFromObject(conv);
    if (convName) return { name: convName, source: "conv-object" };

    const msgName = pickFromMessages(messages);
    if (msgName) return { name: msgName, source: "message-ext" };

    if (isCurrent) {
      const header = pickHeaderTitle();
      if (header) return { name: header, source: "dom-title" };
    }

    if (isGoodName(cachedName)) return { name: String(cachedName).trim(), source: "cache" };

    return { name: "", source: "" };
  };

  window.__feigeBuyerNameExtract = {
    NAME_FIELDS,
    isInternalId,
    isGoodName,
    pickFromObject,
    pickFromMessage,
    pickFromMessages,
    collectListNames,
    scanSidebarDomNames,
    correlateSidebarOrder,
    pickHeaderTitle,
    pickSessionDetails,
    lookupListName,
    resolveBuyerName,
  };
})();
