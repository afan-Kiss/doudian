/**
 * Shared Feige DOM/SDK message helpers.
 */
(() => {
  const WELCOME_RE =
    /欢迎光临|有什么可以帮助|客服.*接入|人工客服|很高兴为您服务|Hi[,，]?\s*欢迎/i;
  const SYSTEM_RE =
    /超时未回复|系统关闭|关闭会话|已读|未读|无意义内容|track_info|paas_/i;
  const EMOJI_ONLY_RE = /^\[[^\]]{1,12}\]$/;
  const NOISE_RE =
    /欢迎光临|商家配置|客服\S*接入|超时未回复|系统关闭会话|全部会话|当前会话|留言|历史会话/;

  const isInternalId = (text) => {
    const s = String(text || "").trim();
    if (!s) return true;
    if (s.startsWith("AQ") && s.length > 24) return true;
    if (s.includes("pigeon") || s.includes("::")) return true;
    return false;
  };

  const isGoodName = (text) => {
    const s = String(text || "").trim();
    if (!s || isInternalId(s)) return false;
    if (s.length > 40) return false;
    if (NOISE_RE.test(s)) return false;
    if (/^\d+$/.test(s)) return false;
    if (/^人工已回复|^当前会话|^最近联系|^列表设置|^等待时长|^已分组/.test(s)) return false;
    if (/^\(.+\)$/.test(s) || /^\d+\)$/.test(s)) return false;
    return true;
  };

  const isSubstantiveServiceReply = (text) => {
    const t = String(text || "").trim();
    if (!t) return false;
    if (EMOJI_ONLY_RE.test(t)) return false;
    if (WELCOME_RE.test(t)) return false;
    if (SYSTEM_RE.test(t)) return false;
    if (t.length <= 2) return false;
    if (/^[\[【].*[\]】]$/.test(t) && t.length <= 8) return false;
    return true;
  };

  const normalizeRole = (m) => {
    const sr = String(m?.sender_role || m?.ext?.sender_role || m?.role || "");
    if (sr === "1" || sr === "customer" || sr === "buyer") return "customer";
    if (sr === "2" || sr === "service" || sr === "seller") return "service";
    if (sr === "3" || sr === "system") return "system";
    if (sr === "robot") return "robot";
    return "unknown";
  };

  const msgText = (m) => String(m?.content || m?.text || m?.message || "").trim();
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

  const parseTimeMs = (t) => {
    const s = String(t || "").trim();
    if (!s) return null;
    if (/^\d{1,2}:\d{2}$/.test(s)) return null;
    if (/^\d+秒$/.test(s)) return Date.now();
    const n = Number(s);
    if (!Number.isNaN(n) && n > 0) return n > 1e12 ? n : n * 1000;
    const d = Date.parse(s);
    return Number.isNaN(d) ? null : d;
  };

  const analyzeUnreplied = (rows) => {
    const normalized = (rows || []).map((m, index) => ({
      role: normalizeRole(m),
      text: msgText(m),
      time: msgTime(m),
      message_id: msgId(m),
      index,
    }));

    let latestCustomer = null;
    let latestSubstantiveService = null;

    for (let i = normalized.length - 1; i >= 0; i -= 1) {
      const m = normalized[i];
      if (!latestCustomer && m.role === "customer" && m.text) {
        latestCustomer = m;
      }
      if (
        !latestSubstantiveService &&
        (m.role === "service" || m.role === "seller") &&
        isSubstantiveServiceReply(m.text)
      ) {
        latestSubstantiveService = m;
      }
      if (latestCustomer && latestSubstantiveService) break;
    }

    let hasUnreplied = false;
    let pendingCount = 0;
    if (latestCustomer) {
      if (!latestSubstantiveService) {
        hasUnreplied = true;
        pendingCount = 1;
      } else {
        const custMs = parseTimeMs(latestCustomer.time);
        const svcMs = parseTimeMs(latestSubstantiveService.time);
        if (custMs != null && svcMs != null) {
          hasUnreplied = custMs > svcMs;
        } else {
          hasUnreplied = latestCustomer.index > latestSubstantiveService.index;
        }
        if (hasUnreplied) {
          const start = latestSubstantiveService.index + 1;
          for (let i = start; i < normalized.length; i += 1) {
            const m = normalized[i];
            if (m.role === "system" || m.role === "robot") continue;
            if (m.role === "customer" && m.text) pendingCount += 1;
          }
          pendingCount = Math.max(pendingCount, 1);
        }
      }
    }

    const last = normalized[normalized.length - 1] || {};
    return {
      latestCustomer,
      latestSubstantiveService,
      hasUnreplied,
      pendingCount,
      lastRole: last.role || "unknown",
      lastText: last.text || "",
      lastMessageId: last.message_id || "",
      lastMessageTime: last.time || "",
    };
  };

  const parseSessionRowText = (raw) => {
    const lines = String(raw || "")
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    if (!lines.length) return null;
    const buyerName = lines[0];
    if (!isGoodName(buyerName)) return null;
    const skipLine =
      /^(重复来访|人工已回复|列表设置|等待时长|已分组|当前会话|最近联系|飞鸽|今日接待|店\d+|已读|未读)$/;
    let timeText = "";
    let lastText = "";
    for (let i = lines.length - 1; i >= 1; i -= 1) {
      const line = lines[i];
      if (skipLine.test(line)) continue;
      if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(line) || /^\d+秒$/.test(line) || /^\d+分钟$/.test(line)) {
        timeText = timeText || line;
        continue;
      }
      lastText = line;
      break;
    }
    for (const line of lines) {
      if (/^\d{1,2}:\d{2}$/.test(line)) timeText = timeText || line;
    }
    return { buyer_name: buyerName, time_text: timeText, last_text: lastText, raw_text: raw };
  };

  const pushRowFromElement = (child, rows, seen) => {
    const raw = String(child.innerText || child.textContent || "").trim();
    if (!raw || raw.length < 3 || seen.has(raw)) return;
    if (/列表设置|等待时长|已分组|当前会话/.test(raw) && !/重复来访/.test(raw)) return;
    const parsed = parseSessionRowText(raw);
    if (!parsed) return;
    seen.add(raw);
    const clickEl = child.matches?.(".auxo-dropdown-trigger")
      ? child
      : child.querySelector(".auxo-dropdown-trigger") || child;
    const cls = String(clickEl.className || child.className || "");
    const unreadNode = child.querySelector('[class*="badge"], [class*="unread"], [class*="dot"]');
    const unreadText = String(unreadNode?.innerText || unreadNode?.textContent || "").trim();
    rows.push({
      row_index: rows.length,
      buyer_name: parsed.buyer_name,
      last_text: parsed.last_text,
      time_text: parsed.time_text || unreadText,
      unread_badge: unreadText,
      is_active: /wmvLQcpt39Hk9PSISrlN|active|selected|current/i.test(cls),
      raw_text: raw.slice(0, 200),
      class_name: cls.slice(0, 120),
      element: clickEl,
    });
  };

  const scanSessionRowsDom = () => {
    const rows = [];
    const seen = new Set();
    const listRoots = document.querySelectorAll(".list_items, [class*='list_items']");
    for (const listRoot of listRoots) {
      const rect = listRoot.getBoundingClientRect();
      if (rect.left > 280 || rect.width > 400 || rect.width < 100) continue;
      for (const child of listRoot.children) {
        pushRowFromElement(child, rows, seen);
      }
    }
    if (!rows.length) {
      for (const el of document.querySelectorAll(".auxo-dropdown-trigger")) {
        const rect = el.getBoundingClientRect();
        if (rect.left > 280 || rect.width > 400) continue;
        pushRowFromElement(el, rows, seen);
      }
    }
    return rows;
  };

  const collectFeigeChatMessages = () => {
    const messages = [];
    const wraps = document.querySelectorAll(".msgItemWrap, [class*='msgItemWrap']");
    for (const wrap of wraps) {
      const notMe = wrap.querySelector('[class*="messageNotMe"]');
      const isMe = wrap.querySelector('[class*="messageIsMe"]');
      const raw = String(wrap.innerText || wrap.textContent || "").trim();
      let role = "unknown";
      let textHost = null;
      if (notMe) {
        role = "customer";
        textHost = notMe;
      } else if (isMe) {
        role = "service";
        textHost = isMe;
      } else if (/系统消息/.test(raw)) {
        role = "system";
        textHost = wrap;
      } else {
        continue;
      }
      const lines = String(textHost.innerText || textHost.textContent || "")
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean)
        .filter(
          (line) =>
            line !== "系统消息" &&
            !/^已读$/.test(line) &&
            !/^未读$/.test(line) &&
            !/商家配置发送/.test(line) &&
            !/^\d{1,2}:\d{2}(:\d{2})?$/.test(line)
        );
      const text = lines.join(" ").trim();
      if (!text) continue;
      const rect = wrap.getBoundingClientRect();
      let timeText = "";
      const timeMatch = raw.match(/\d{1,2}:\d{2}(:\d{2})?/);
      if (timeMatch) timeText = timeMatch[0];
      const position_hint =
        role === "customer" ? "left" : role === "service" ? "right" : "center";
      messages.push({
        role,
        text,
        time_text: timeText,
        raw_text: raw.slice(0, 300),
        class_name: String(textHost.className || "").slice(0, 120),
        position_hint,
        top: Math.round(rect.top),
      });
    }
    messages.sort((a, b) => a.top - b.top);
    return messages.map((m, index) => ({
      index,
      role: m.role,
      text: m.text,
      time_text: m.time_text,
      raw_text: m.raw_text,
      class_name: m.class_name,
      position_hint: m.position_hint,
    }));
  };

  const clickSessionRow = ({ buyerName = "", lastText = "", rowIndex = -1 } = {}) => {
    const rows = scanSessionRowsDom();
    const targetName = String(buyerName || "").trim();
    const targetText = String(lastText || "").trim();
    let row = null;
    if (rowIndex >= 0 && rows[rowIndex]) row = rows[rowIndex];
    if (!row && targetName) {
      row =
        rows.find((r) => r.buyer_name === targetName) ||
        rows.find((r) => r.buyer_name.includes(targetName) || targetName.includes(r.buyer_name));
    }
    if (!row && targetText) {
      row = rows.find((r) => (r.last_text || "").includes(targetText.slice(0, 12)));
    }

    const tryClick = (el) => {
      if (!el) return false;
      try {
        el.scrollIntoView({ block: "center", inline: "nearest" });
        el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
        el.click?.();
        return true;
      } catch (e) {
        return false;
      }
    };

    if (targetName) {
      for (const span of document.querySelectorAll(".list_items span, [class*='list_items'] span")) {
        const text = String(span.innerText || span.textContent || "").trim();
        if (text !== targetName) continue;
        const rect = span.getBoundingClientRect();
        if (rect.left > 320) continue;
        const clickTarget =
          span.closest(".auxo-dropdown-trigger") ||
          span.closest("[class*='Zp7bklk']") ||
          span.closest(".list_items > div") ||
          span.parentElement;
        if (tryClick(clickTarget)) {
          return {
            ok: true,
            method: "dom-span-click",
            buyer_name: targetName,
            row_index: row?.row_index ?? -1,
            rows_found: rows.length,
          };
        }
      }
    }

    if (!row?.element) return { ok: false, reason: "row-not-found", rows_found: rows.length };
    const candidates = [
      row.element,
      row.element.querySelector?.(".auxo-dropdown-trigger"),
      row.element.querySelector?.('[class*="MP1bk3"]'),
      row.element.querySelector?.("span"),
    ].filter(Boolean);
    for (const el of candidates) {
      if (tryClick(el)) {
        return {
          ok: true,
          method: "dom-row-click",
          buyer_name: row.buyer_name,
          row_index: row.row_index,
          rows_found: rows.length,
        };
      }
    }
    return { ok: false, reason: "click-failed", rows_found: rows.length, buyer_name: row.buyer_name };
  };

  const pickHeaderTitle = () => {
    const selectors = [
      "span.xsRqAowfwhuh_qMX8t1U",
      '[class*="xsRqAow"]',
      "div.J2iNkmuwzFrTdP4jEOXK",
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

  const namesRoughMatch = (a, b) => {
    const x = String(a || "").trim();
    const y = String(b || "").trim();
    if (!x || !y) return false;
    if (x === y) return true;
    return x.includes(y) || y.includes(x);
  };

  const SYSTEM_NOTICE_NAME_RE =
    /智能客服|系统通知|平台通知|服务通知|客服助手|飞鸽助手|抖店助手|官方通知/;
  const SYSTEM_NOTICE_TEXT_RE =
    /智能客服功能升级|功能升级|系统通知|平台通知|官方通知|服务通知|无需回复/;

  const isSystemNoticeConversation = (row) => {
    const name = String(row?.buyer_name || row?.customer_name || "").trim();
    const lastText = String(
      row?.last_message_text ||
        row?.latest_customer_message_text ||
        row?.last_text ||
        ""
    ).trim();
    const convId = String(row?.conversation_id || "");
    if (convId.startsWith("dom:智能客服")) {
      return { is_system_notice: true, skip_reason: "系统通知，不需要回复" };
    }
    if (SYSTEM_NOTICE_NAME_RE.test(name)) {
      return { is_system_notice: true, skip_reason: "系统通知，不需要回复" };
    }
    if (SYSTEM_NOTICE_TEXT_RE.test(lastText)) {
      return { is_system_notice: true, skip_reason: "系统通知，不需要回复" };
    }
    return { is_system_notice: false, skip_reason: "" };
  };

  const applySystemNoticeFields = (conv) => {
    const notice = isSystemNoticeConversation(conv);
    if (!notice.is_system_notice) return conv;
    return {
      ...conv,
      is_system_notice: true,
      has_unreplied_customer_message: false,
      pending_customer_count: 0,
      skip_reason: notice.skip_reason || "系统通知，不需要回复",
      score: Math.min(Number(conv?.score || 0), -100),
    };
  };

  window.__feigeMessageUtils = {
    isSubstantiveServiceReply,
    analyzeUnreplied,
    parseSessionRowText,
    scanSessionRowsDom,
    collectFeigeChatMessages,
    clickSessionRow,
    pickHeaderTitle,
    namesRoughMatch,
    isGoodName,
    normalizeRole,
    isSystemNoticeConversation,
    applySystemNoticeFields,
  };
})();
