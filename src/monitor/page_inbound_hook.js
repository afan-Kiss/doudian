(() => {
  if (window.__feigeInboundHookInstalled) {
    return;
  }
  window.__feigeInboundHookInstalled = true;
  window.__feigeInboundQueue = window.__feigeInboundQueue || [];
  window.__feigeDomSeenKeys = window.__feigeDomSeenKeys || new Set();

  const NOISE =
    /欢迎光临|商家配置|客服\S*接入|超时未回复|系统关闭会话|人工客服|请问有什么可以帮助|CurrentServer|paas_|track_info|用户已等待|请尽快回复|其他无意义|无意义内容|common_|llm_intent|c_foot|msg_foot/;

  const isNoiseText = (text, nickname = "") => {
    const value = String(text || "").trim();
    if (!value || NOISE.test(value)) {
      return true;
    }
    const nick = String(nickname || "").trim();
    if (nick && value === nick) {
      return true;
    }
    if (/^[\u4e00-\u9fff]$/.test(value) || /^(1|2|ok)$/i.test(value)) {
      return true;
    }
    return false;
  };

  const pushInbound = (item) => {
    if (!item || !item.text) {
      return;
    }
    const text = String(item.text || "").trim();
    const nickname = String(item.nickname || "").trim();
    if (!text || isNoiseText(text, nickname)) {
      return;
    }
    if (window.__feigeInboundQueue.length > 300) {
      window.__feigeInboundQueue.shift();
    }
    window.__feigeInboundQueue.push({
      ...item,
      text,
      ts: Date.now(),
    });
  };

  const parseMsgBodyPayload = (payload, url = "") => {
    if (!payload || !payload.includes("msg_body_list")) {
      return;
    }
    try {
      const data = JSON.parse(payload);
      const list = data.msg_body_list;
      if (!Array.isArray(list)) {
        return;
      }
      for (const msg of list) {
        const text = String(msg.content || "").trim();
        if (!text) {
          continue;
        }
        const senderRole = String(msg.sender_role || (msg.ext || {}).sender_role || "");
        let role = "system";
        if (senderRole === "1") {
          role = "buyer";
        } else if (senderRole === "2") {
          role = "seller";
        }
        const ext = msg.ext || {};
        const flowExtra = String(ext.flow_extra || "");
        const dirMatch = flowExtra.match(/"direction"\s*:\s*(\d+)/);
        if (dirMatch) {
          const direction = parseInt(dirMatch[1], 10);
          if (direction === 1) {
            role = "buyer";
          } else if (direction === 2) {
            role = "seller";
          } else if (direction >= 3) {
            role = "system";
          }
        }
        pushInbound({
          source: "page_http",
          kind: role === "buyer" ? "buyer_message" : role === "seller" ? "seller_message" : "system_message",
          role,
          text,
          nickname: ext.nickname || ext.uname || "",
          conversation_id: String(ext.talk_id || data.talk_id || ""),
          conversation_route: ext.security_conversation_id || ext.security_biz_conversation_id || "",
          server_message_id: String(msg.serverId || ext.b_temp_track_message_id || ""),
          url,
        });
      }
    } catch (error) {
      // ignore malformed JSON
    }
  };

  const extractBuyerFromWs = (bytes) => {
    if (!bytes || bytes.length < 64) {
      return;
    }
    const blob = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    if (!blob.includes("flow_extra")) {
      return;
    }
    const dirMatch = blob.match(/"direction"\s*:\s*(\d+)/);
    const direction = dirMatch ? parseInt(dirMatch[1], 10) : 0;
    const senderMatch = blob.match(/sender_role[\x12\x01"]([12])/);
    let role = null;
    if (direction === 2 || senderMatch?.[1] === "2") {
      role = "seller";
    } else if (direction === 1 || senderMatch?.[1] === "1") {
      role = "buyer";
    } else if (direction >= 3) {
      role = "system";
    }
    if (role !== "buyer") {
      return;
    }

    const nicknameMatch =
      blob.match(/"nickname"\s*:\s*"([^"]+)"/) || blob.match(/"uname"\s*:\s*"([^"]+)"/);
    const nickname = nicknameMatch?.[1] || "";

    let text = "";
    if (blob.includes("point_info") && blob.includes("product_id")) {
      text = "[商品卡片]";
    } else {
      const cn = blob.match(/[\u4e00-\u9fffA-Za-z0-9，。！？,.!? ]{2,40}/g) || [];
      const filtered = cn
        .map((s) => s.trim())
        .filter((s) => s && !NOISE.test(s) && s !== nickname && s.length >= 2 && s.length <= 40);
      text = filtered[0] || "";
    }
    if (!text) {
      return;
    }

    const routeMatch = blob.match(
      /security(?:_biz)?_conversation_id[\x12\|:]["']?([A-Za-z0-9_:/-]{20,160})/
    );
    pushInbound({
      source: "page_ws",
      kind: "buyer_message",
      role: "buyer",
      text,
      nickname,
      conversation_route: routeMatch?.[1] || "",
      server_message_id: "",
      url: "",
    });
  };

  const hookFetch = () => {
    if (window.__feigeFetchHooked || !window.fetch) {
      return;
    }
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      try {
        const input = args[0];
        const init = args[1] || {};
        const url = typeof input === "string" ? input : input?.url || "";
        if (
          url.includes("answerRecommend") ||
          url.includes("pigeon_im") ||
          url.includes("pigeon.jinritemai.com")
        ) {
          response
            .clone()
            .text()
            .then((text) => parseMsgBodyPayload(text, url))
            .catch(() => {});
        }
      } catch (error) {
        // ignore
      }
      return response;
    };
    window.__feigeFetchHooked = true;
  };

  const hookXhr = () => {
    if (window.__feigeXhrHooked || !window.XMLHttpRequest) {
      return;
    }
    const OriginalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function (...args) {
      const xhr = new OriginalXHR(...args);
      let requestUrl = "";
      const originalOpen = xhr.open;
      xhr.open = function (method, url, ...rest) {
        requestUrl = String(url || "");
        return originalOpen.call(this, method, url, ...rest);
      };
      const originalSend = xhr.send;
      xhr.send = function (body) {
        // Outbound request bodies are seller sends; only parse inbound responses.
        xhr.addEventListener("load", () => {
          try {
            parseMsgBodyPayload(String(xhr.responseText || ""), requestUrl);
          } catch (error) {
            // ignore
          }
        });
        return originalSend.call(this, body);
      };
      return xhr;
    };
    window.__feigeXhrHooked = true;
  };

  const patchWsTracker = () => {
    if (!window.__feigeCapturedSockets) {
      return;
    }
    for (const ws of window.__feigeCapturedSockets) {
      if (ws.__feigeInboundPatched) {
        continue;
      }
      ws.__feigeInboundPatched = true;
      ws.addEventListener("message", (event) => {
        let bytes = null;
        if (event.data instanceof ArrayBuffer) {
          bytes = new Uint8Array(event.data);
        } else if (event.data instanceof Uint8Array) {
          bytes = event.data;
        }
        extractBuyerFromWs(bytes);
      });
    }
  };

  window.__feigeDrainInboundQueue = () => {
    const items = window.__feigeInboundQueue.splice(0, window.__feigeInboundQueue.length);
    return items;
  };

  window.__feigeScanDomInbound = () => {
    const out = [];
    let activeNickname = "";
    for (const selector of [
      '[class*="conversation-title"]',
      '[class*="chat-title"]',
      '[class*="nick-name"]',
      '[class*="nickname"]',
      '[class*="user-name"]',
    ]) {
      const node = document.querySelector(selector);
      const text = String(node?.innerText || node?.textContent || "").trim();
      if (text && text.length <= 32 && !NOISE.test(text)) {
        activeNickname = text;
        break;
      }
    }
    const selectors = [
      '[class*="msg-content"]',
      '[class*="message-content"]',
      '[class*="msgContent"]',
      '[class*="message-text"]',
      '[class*="text-content"]',
      '[class*="im-message"]',
      '[class*="chat-message"]',
    ];
    const roots = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        roots.add(el);
      }
    }
    for (const el of roots) {
      const text = String(el.innerText || el.textContent || "").trim();
      if (!text || text.length > 200 || NOISE.test(text)) {
        continue;
      }
      const host =
        el.closest('[class*="message"], [class*="msg-item"], [class*="chat-item"], [class*="msgItem"]') ||
        el.parentElement;
      if (!host) {
        continue;
      }
      const rect = host.getBoundingClientRect();
      const container =
        el.closest('[class*="message-list"], [class*="chat-main"], [class*="msg-list"], main, [role="main"]') ||
        document.body;
      const containerRect = container.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const isLeft = centerX < containerRect.left + containerRect.width * 0.55;
      if (!isLeft) {
        continue;
      }
      const key = `${text}::${Math.round(rect.top)}`;
      if (window.__feigeDomSeenKeys.has(key)) {
        continue;
      }
      window.__feigeDomSeenKeys.add(key);
      if (window.__feigeDomSeenKeys.size > 500) {
        window.__feigeDomSeenKeys.clear();
      }
      out.push({
        source: "page_dom",
        kind: "buyer_message",
        role: "buyer",
        text,
        nickname: activeNickname,
        conversation_route: "",
        server_message_id: "",
        url: location.href,
      });
    }
    return out;
  };

  hookFetch();
  hookXhr();
  setInterval(() => {
    hookFetch();
    hookXhr();
    patchWsTracker();
  }, 1000);
})();
