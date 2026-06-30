(() => {
  const NOISE =
    /欢迎光临|商家配置|客服\S*接入|超时未回复|系统关闭会话|人工客服|请问有什么可以帮助|CurrentServer|paas_|track_info|用户已等待|请尽快回复|无意义内容|common_|llm_intent|c_foot|msg_foot|已读|未读/;

  const TITLE_SELECTORS = [
    { key: "conversation-title", sel: '[class*="conversation-title"]' },
    { key: "chat-title", sel: '[class*="chat-title"]' },
    { key: "nick-name", sel: '[class*="nick-name"]' },
    { key: "nickname", sel: '[class*="nickname"]:not([class*="list"])' },
    { key: "user-name", sel: '[class*="user-name"]' },
    { key: "header-name", sel: '[class*="header"] [class*="name"]' },
  ];

  const LIST_SELECTORS = [
    { key: "message-list", sel: '[class*="message-list"]' },
    { key: "msg-list", sel: '[class*="msg-list"]' },
    { key: "chat-main", sel: '[class*="chat-main"]' },
    { key: "im-message-list", sel: '[class*="im-message"]' },
    { key: "scroll-container", sel: '[class*="scroll"] [class*="container"]' },
    { key: "role-main", sel: 'main, [role="main"]' },
  ];

  const ITEM_SELECTORS = [
    { key: "message-item", sel: '[class*="message-item"]' },
    { key: "msg-item", sel: '[class*="msg-item"]' },
    { key: "msgItem", sel: '[class*="msgItem"]' },
    { key: "MessageItem", sel: '[class*="MessageItem"]' },
    { key: "chat-item", sel: '[class*="chat-item"]' },
    { key: "im-msg-row", sel: '[class*="im-msg"]' },
    { key: "auxo-list-item", sel: ".auxo-list-item, [class*='auxo-list'] [class*='item']" },
  ];

  const TEXT_SELECTORS = [
    '[class*="msg-content"]',
    '[class*="message-content"]',
    '[class*="msgContent"]',
    '[class*="message-text"]',
    '[class*="text-content"]',
    '[class*="content-text"]',
    '[class*="bubble"]',
  ];

  const pickCustomerName = () => {
    for (const item of TITLE_SELECTORS) {
      const node = document.querySelector(item.sel);
      const text = String(node?.innerText || node?.textContent || "").trim();
      if (text && text.length <= 40 && !NOISE.test(text) && !/^\d+$/.test(text)) {
        return { name: text, profile: item.key };
      }
    }
    return { name: "", profile: "" };
  };

  const pickListRoot = () => {
    for (const item of LIST_SELECTORS) {
      const node = document.querySelector(item.sel);
      if (node) {
        return { root: node, profile: item.key };
      }
    }
    return { root: document.body, profile: "body" };
  };

  const collectItems = (root) => {
    const items = [];
    const seen = new Set();
    for (const item of ITEM_SELECTORS) {
      for (const el of root.querySelectorAll(item.sel)) {
        if (seen.has(el)) continue;
        seen.add(el);
        items.push({ el, profile: item.key });
      }
    }
    if (items.length >= 2) {
      return { items, profile: items[0]?.profile || "message-item" };
    }
    // fallback: treat each text block host as item
    const fallback = [];
    for (const sel of TEXT_SELECTORS) {
      for (const textEl of root.querySelectorAll(sel)) {
        const host =
          textEl.closest('[class*="message"], [class*="msg-item"], [class*="chat-item"], [class*="msgItem"]') ||
          textEl.parentElement;
        if (!host || seen.has(host)) continue;
        seen.add(host);
        fallback.push({ el: host, profile: "text-host" });
      }
    }
    return { items: fallback, profile: fallback.length ? "text-host" : "none" };
  };

  const extractText = (host) => {
    for (const sel of TEXT_SELECTORS) {
      const node = host.querySelector(sel) || (host.matches?.(sel) ? host : null);
      if (!node) continue;
      const text = String(node.innerText || node.textContent || "").trim();
      if (text && text.length <= 500 && !NOISE.test(text)) return text;
    }
    const text = String(host.innerText || host.textContent || "").trim();
    if (text && text.length <= 500 && !NOISE.test(text)) return text.split("\n")[0].trim();
    return "";
  };

  const detectRole = (host, containerRect) => {
    const cls = String(host.className || "");
    const aria = String(host.getAttribute?.("aria-label") || "");
    if (/system|notice|tip|提示|系统/.test(cls + aria)) return "system";
    if (/robot|智能|机器人|auto-reply/.test(cls + aria)) return "robot";

    const rect = host.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const leftBound = containerRect.left + containerRect.width * 0.45;
    const rightBound = containerRect.left + containerRect.width * 0.55;

    if (centerX <= leftBound) return "customer";
    if (centerX >= rightBound) return "service";

    const style = window.getComputedStyle(host);
    const align = style.textAlign || style.justifyContent || "";
    if (/right|end|flex-end/.test(align)) return "service";
    if (/left|start|flex-start/.test(align)) return "customer";
    return centerX < containerRect.left + containerRect.width / 2 ? "customer" : "service";
  };

  const extractTime = (host) => {
    for (const sel of ['[class*="time"]', '[class*="timestamp"]', "time"]) {
      const node = host.querySelector(sel);
      const text = String(node?.innerText || node?.textContent || "").trim();
      if (text && /\d/.test(text)) return text;
    }
    return "";
  };

  const messageId = (host, role, text, top) => {
    const dataId =
      host.getAttribute?.("data-id") ||
      host.getAttribute?.("data-message-id") ||
      host.getAttribute?.("id") ||
      "";
    if (dataId) return String(dataId);
    return `dom:${role}:${top}:${text.slice(0, 48)}`;
  };

  const normalizeRole = (m) => {
    const sr = String(m?.sender_role || m?.ext?.sender_role || m?.senderRole || "");
    const direction = Number(m?.direction || m?.ext?.direction || 0);
    if (sr === "2" || direction === 2) return "service";
    if (sr === "1" || direction === 1) return "customer";
    if (sr === "3" || direction >= 3) return "system";
    const type = String(m?.type || m?.msg_type || m?.ext?.type || "");
    if (/robot|auto|智能/i.test(type)) return "robot";
    return "customer";
  };

    const mapSdkMessage = (m, idx, profile) => {
    const content = String(
      m?.content || m?.text || m?.message || m?.msg || m?.body?.content || ""
    ).trim();
    if (!content) return null;
    const role = normalizeRole(m);
    const mid =
      m?.server_message_id ||
      m?.serverId ||
      m?.client_message_id ||
      m?.messageId ||
      m?.id ||
      "";
    const rawTime = m?.create_time || m?.created_at || m?.timestamp || m?.time || "";
    const sortKey = Number(rawTime || 0);
    const time = String(rawTime || "");
    return {
      role,
      text: content,
      time,
      sort_key: sortKey,
      message_id: String(mid || `sdk:${profile}:${idx}:${content.slice(0, 24)}`),
      source: "sdk",
    };
  };

  const resolveMessageBucket = (map, convId) => {
    if (!map?.get || !convId) return null;
    const target = String(convId);
    const direct = map.get(target);
    if (direct) return direct;
    for (const key of map.keys()) {
      const k = String(key);
      if (k === target) continue;
      const prefixLen = Math.min(60, k.length, target.length);
      const prefix = target.slice(0, prefixLen);
      if (k.startsWith(prefix) || target.startsWith(k.slice(0, prefixLen))) {
        return map.get(key);
      }
    }
    return null;
  };

  const readSdkMessages = () => {
    const extract = window.__feigeBuyerNameExtract;
    const out = { messages: [], customer_name: "", profile: "", buyer_name_source: "" };
    try {
      const ctx = window.__monaGlobalStore?.getData?.("initContextData");
      let store = null;
      ctx?.doAction?.((s) => {
        store = s;
      });
      const conv =
        store?.conversationsInfo?.currentConversation ||
        store?.sessionDetails?.currentConversation ||
        {};
      const convId = conv.id || "";
      const collected = [];
      const rawSdkMessages = [];

      const sessionList = store?.sessionDetails?.messageList || [];
      if (Array.isArray(sessionList) && sessionList.length) {
        out.profile = "sdk-sessionDetails.messageList";
        rawSdkMessages.push(...sessionList);
        for (const m of sessionList.slice(-20)) {
          const row = mapSdkMessage(m, collected.length, out.profile);
          if (row) collected.push(row);
        }
      }

      const bucket = resolveMessageBucket(
        store?.conversationsInfo?.messagesByConversationId,
        convId
      );
      const innerMap = bucket?.map;
      if (innerMap && typeof innerMap.values === "function") {
        const rows = [...innerMap.values()];
        rawSdkMessages.push(...rows);
        const mapped = rows
          .map((m) => mapSdkMessage(m, 0, "sdk-messagesByConversationId.map"))
          .filter(Boolean);
        mapped.sort((a, b) => {
          const ta = Number(a.sort_key || 0);
          const tb = Number(b.sort_key || 0);
          if (ta && tb) return ta - tb;
          return 0;
        });
        if (mapped.length) {
          out.profile = out.profile || "sdk-messagesByConversationId.map";
          for (const row of mapped.slice(-20)) collected.push(row);
        }
      }

      const seen = new Set();
      out.messages = collected.filter((m) => {
        const key = m.message_id || `${m.role}:${m.text}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).slice(-20);

      const resolved = extract?.resolveBuyerName?.({
        convId,
        conv,
        store,
        messages: rawSdkMessages,
        isCurrent: true,
      }) || { name: conv.name || conv.nickname || conv.userName || "", source: "" };
      out.customer_name = String(resolved.name || "").trim();
      out.buyer_name_source = resolved.source || "";
    } catch (error) {
      out.error = String(error);
    }
    return out;
  };

  const pullCurrentConversationMessages = async () => {
    try {
      const ctx = window.__monaGlobalStore?.getData?.("initContextData");
      const im = ctx?.im;
      let convId = "";
      let currentConv = null;
      ctx?.doAction?.((store) => {
        currentConv = store?.conversationsInfo?.currentConversation || null;
        convId = currentConv?.id || "";
      });
      if (!im || !convId) return false;
      const tasks = [
        () => im.pullMessagesByConversationId?.(convId),
        () => im.getHistoryMessages?.({ conversationId: convId, limit: 30 }),
        () => im.getMessagesByConversation?.(currentConv),
      ];
      for (const task of tasks) {
        try {
          await task?.();
        } catch (error) {
          // ignore pull errors for closed/system sessions
        }
      }
      return true;
    } catch (error) {
      return false;
    }
  };

  const scrollChatIntoView = (root) => {
    for (const sel of [
      '[class*="message-list"]',
      '[class*="msg-list"]',
      '[class*="chat-main"]',
      '[class*="scroll"]',
    ]) {
      const node = root.querySelector(sel);
      if (node && node.scrollHeight > node.clientHeight) {
        node.scrollTop = node.scrollHeight;
      }
    }
  };

  const collectDomMessages = () => {
    const feige = window.__feigeMessageUtils?.collectFeigeChatMessages?.();
    if (feige && feige.length) {
      const title = pickCustomerName();
      return {
        customer_name: title.name,
        selector_profile: {
          title: title.profile,
          list: "msgItemWrap",
          item: "messageNotMe/messageIsMe",
          source: "dom-feige",
        },
        messages: feige.map((m) => ({
          role: m.role,
          text: m.text,
          time: m.time_text || "",
          message_id: `dom:${m.role}:${m.text.slice(0, 48)}`,
          source: "dom",
        })),
      };
    }

    const title = pickCustomerName();
    const list = pickListRoot();
    scrollChatIntoView(list.root);
    const collected = collectItems(list.root);
    const containerRect = list.root.getBoundingClientRect();

    const rows = collected.items
      .map(({ el, profile }) => {
        const text = extractText(el);
        if (!text) return null;
        const rect = el.getBoundingClientRect();
        const role = detectRole(el, containerRect);
        const time = extractTime(el);
        const top = Math.round(rect.top);
        return {
          role,
          text,
          time,
          message_id: messageId(el, role, text, top),
          top,
          item_profile: profile,
          source: "dom",
        };
      })
      .filter(Boolean);

    rows.sort((a, b) => a.top - b.top);
    return {
      customer_name: title.name,
      selector_profile: {
        title: title.profile,
        list: list.profile,
        item: collected.profile,
        source: "dom",
      },
      messages: rows.slice(-20).map(({ role, text, time, message_id }) => ({
        role,
        text,
        time,
        message_id,
        source: "dom",
      })),
    };
  };

  const isDomNoise = (text) => {
    const t = String(text || "").trim();
    if (!t) return true;
    if (/^\d{1,2}月\d{1,2}日/.test(t)) return true;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(t)) return true;
    return false;
  };

  const normText = (text) => String(text || "").replace(/\s+/g, "");

  return async () => {
    await pullCurrentConversationMessages();
    const sdk = readSdkMessages();
    const dom = collectDomMessages();
    const messages = [];
    const seen = new Set();
    const sdkTexts = new Set(sdk.messages.map((m) => `${m.role}::${normText(m.text)}`));

    for (const msg of sdk.messages) {
      const key = msg.message_id || `${msg.role}:${msg.text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      messages.push(msg);
    }

    for (const msg of dom.messages) {
      if (isDomNoise(msg.text)) continue;
      const fuzzy = `${msg.role}::${normText(msg.text)}`;
      if (sdkTexts.has(fuzzy)) continue;
      if (sdk.messages.length > 0 && msg.role === "service") {
        const customerVariant = `customer::${normText(msg.text)}`;
        if (sdkTexts.has(customerVariant)) continue;
      }
      const key = msg.message_id || `${msg.role}:${msg.text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      messages.push(msg);
    }

    if (sdk.messages.length > 0) {
      messages.sort((a, b) => Number(a.sort_key || 0) - Number(b.sort_key || 0));
    } else {
      messages.sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
    }

    const extract = window.__feigeBuyerNameExtract;
    let store = null;
    try {
      const ctx = window.__monaGlobalStore?.getData?.("initContextData");
      ctx?.doAction?.((s) => {
        store = s;
      });
    } catch {
      // ignore
    }
    const conv =
      store?.conversationsInfo?.currentConversation ||
      store?.sessionDetails?.currentConversation ||
      {};
    const convId = conv.id || "";
    const resolved = extract?.resolveBuyerName?.({
      convId,
      conv,
      store,
      messages: sdk.messages,
      isCurrent: true,
    }) || { name: "", source: "" };
    const customerName =
      resolved.name ||
      dom.customer_name ||
      sdk.customer_name ||
      extract?.pickHeaderTitle?.() ||
      "";

    return {
      customer_name: customerName,
      buyer_name_source: resolved.source || sdk.buyer_name_source || dom.selector_profile?.title || "",
      selector_profile: {
        sdk: sdk.profile || "sdk-empty",
        ...(dom.selector_profile || {}),
      },
      messages: messages.slice(-20),
      message_count: messages.length,
      sdk_count: sdk.messages.length,
      dom_count: dom.messages.length,
    };
  };
})();
