const state = {
  conversations: [],
  messages: [],
  activeConversationKey: null,
  socket: null,
};

const conversationListEl = document.getElementById("conversationList");
const messageListEl = document.getElementById("messageList");
const chatTitleEl = document.getElementById("chatTitle");
const chatSubtitleEl = document.getElementById("chatSubtitle");
const connectionStatusEl = document.getElementById("connectionStatus");
const messageInputEl = document.getElementById("messageInput");
const sendButtonEl = document.getElementById("sendButton");

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function getActiveConversation() {
  return state.conversations.find((item) => item.conversation_key === state.activeConversationKey) || null;
}

function setConnectionStatus(online) {
  connectionStatusEl.textContent = online ? "已连接，实时同步中" : "连接断开，正在重连...";
  connectionStatusEl.className = online ? "status online" : "status offline";
}

function renderConversations() {
  conversationListEl.innerHTML = "";
  if (!state.conversations.length) {
    conversationListEl.innerHTML = '<div class="empty-state">暂无会话</div>';
    return;
  }

  for (const conversation of state.conversations) {
    const item = document.createElement("div");
    item.className = "conversation-item";
    if (conversation.conversation_key === state.activeConversationKey) {
      item.classList.add("active");
    }
    item.innerHTML = `
      <div class="conversation-name">${escapeHtml(conversation.nickname || "未知买家")}</div>
      <div class="conversation-preview">${escapeHtml(conversation.last_text || "暂无消息")}</div>
    `;
    item.addEventListener("click", () => selectConversation(conversation.conversation_key));
    conversationListEl.appendChild(item);
  }
}

function renderMessages() {
  const messages = state.activeConversationKey
    ? state.messages.filter((msg) => msg.conversation_key === state.activeConversationKey)
    : [];

  if (!state.activeConversationKey) {
    messageListEl.innerHTML = '<div class="empty-state">请选择左侧会话开始查看聊天记录</div>';
    return;
  }

  if (!messages.length) {
    messageListEl.innerHTML = '<div class="empty-state">这个会话还没有消息</div>';
    return;
  }

  messageListEl.innerHTML = "";
  for (const message of messages) {
    messageListEl.appendChild(createMessageRow(message));
  }
  messageListEl.scrollTop = messageListEl.scrollHeight;
}

function createMessageRow(message) {
  const row = document.createElement("div");
  row.className = `message-row ${message.side || "left"}`;
  row.dataset.messageId = message.id;

  if (message.side === "center") {
    row.innerHTML = `<div class="bubble-wrap"><div class="bubble">${escapeHtml(message.text)}</div></div>`;
    return row;
  }

  const nickname = message.nickname || (message.side === "right" ? "店铺" : "买家");
  const avatarText = nickname.slice(0, 1) || "?";
  const pendingLabel = message.pending ? " · 发送中" : "";

  row.innerHTML = `
    <div class="avatar">${escapeHtml(avatarText)}</div>
    <div class="bubble-wrap">
      <div class="bubble-meta">${escapeHtml(nickname)} · ${formatTime(message.timestamp)}${pendingLabel}</div>
      <div class="bubble${message.pending ? " pending" : ""}">${escapeHtml(message.text)}</div>
    </div>
  `;
  return row;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function upsertMessage(message) {
  const index = state.messages.findIndex((item) => item.id === message.id);
  if (index >= 0) {
    state.messages[index] = message;
  } else {
    state.messages.push(message);
  }
  state.messages.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
}

function selectConversation(conversationKey) {
  state.activeConversationKey = conversationKey;
  const conversation = getActiveConversation();
  chatTitleEl.textContent = conversation?.nickname || "未知买家";
  chatSubtitleEl.textContent = conversation?.conversation_id
    ? `会话 ID: ${conversation.conversation_id}`
    : "买家消息在左，店铺回复在右";
  messageInputEl.disabled = !conversation;
  sendButtonEl.disabled = !conversation;
  renderConversations();
  renderMessages();
}

async function loadMessages(conversationKey) {
  const params = conversationKey ? `?conversation_key=${encodeURIComponent(conversationKey)}` : "";
  const response = await fetch(`/api/messages${params}`);
  const data = await response.json();
  if (conversationKey) {
    const others = state.messages.filter((msg) => msg.conversation_key !== conversationKey);
    state.messages = [...others, ...(data.messages || [])];
  } else {
    state.messages = data.messages || [];
  }
  renderMessages();
}

function isUsableNickname(value) {
  const nick = String(value || "").trim();
  return nick && !["-", "?", "未知买家", "买家", "店铺"].includes(nick);
}

function pickNickname(message, existing = "") {
  const incoming = String(message?.nickname || "").trim();
  if (isUsableNickname(incoming)) return incoming;
  if (isUsableNickname(existing)) return existing;
  if (message?.role === "buyer") return incoming || "未知买家";
  return existing || "未知买家";
}

function dedupeConversations(conversations) {
  const byRoute = new Map();
  const orphans = [];

  for (const item of conversations) {
    const route = String(item.conversation_route || "").trim();
    if (route) {
      const prev = byRoute.get(route);
      if (!prev) {
        byRoute.set(route, { ...item });
        continue;
      }
      const betterName = pickNickname({ nickname: item.nickname, role: "buyer" }, prev.nickname);
      if ((item.updated_at || 0) >= (prev.updated_at || 0)) {
        byRoute.set(route, { ...item, nickname: betterName });
      } else {
        byRoute.set(route, { ...prev, nickname: betterName });
      }
      continue;
    }

    const talkId = String(item.conversation_id || "").trim();
    if (talkId) {
      const matched = [...byRoute.values()].find((conv) => String(conv.conversation_id || "") === talkId);
      if (matched) {
        matched.nickname = pickNickname({ nickname: item.nickname, role: "buyer" }, matched.nickname);
        if ((item.updated_at || 0) > (matched.updated_at || 0)) {
          matched.last_text = item.last_text || matched.last_text;
          matched.last_timestamp = item.last_timestamp || matched.last_timestamp;
          matched.updated_at = item.updated_at || matched.updated_at;
        }
        continue;
      }
    }
    orphans.push(item);
  }

  return [...byRoute.values(), ...orphans].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
}

function mergeConversations(incoming) {
  if (!incoming?.length) {
    return;
  }
  const map = new Map(state.conversations.map((item) => [item.conversation_key, item]));
  for (const item of incoming) {
    const prev = map.get(item.conversation_key) || {};
    map.set(item.conversation_key, {
      ...prev,
      ...item,
      nickname: pickNickname(item, prev.nickname),
    });
  }
  state.conversations = dedupeConversations(Array.from(map.values()));
}

function handleSnapshot(payload) {
  mergeConversations(payload.conversations || []);
  if (payload.messages?.length) {
    const keys = new Set(state.messages.map((msg) => msg.id));
    for (const message of payload.messages) {
      if (!keys.has(message.id)) {
        state.messages.push(message);
      }
    }
    state.messages.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  }
  if (!state.activeConversationKey && state.conversations.length) {
    selectConversation(state.conversations[0].conversation_key);
  } else {
    renderConversations();
    renderMessages();
  }
}

function handleSocketPayload(payload) {
  if (payload.type === "snapshot") {
    handleSnapshot(payload);
    return;
  }
  if (payload.type === "conversations") {
    mergeConversations(payload.conversations || []);
    renderConversations();
    return;
  }
  if (payload.type === "message" || payload.type === "message_update") {
    upsertMessage(payload.message);
    state.conversations = mergeConversationPreview(payload.message);
    if (!state.activeConversationKey) {
      selectConversation(payload.message.conversation_key);
      return;
    }
    renderConversations();
    if (payload.message.conversation_key === state.activeConversationKey) {
      renderMessages();
    }
  }
}

function mergeConversationPreview(message) {
  const key = message.conversation_key || message.conversation_id || message.nickname;
  if (!key) {
    return state.conversations;
  }
  const existing = state.conversations.find((item) => item.conversation_key === key);
  const updated = {
    conversation_key: key,
    conversation_id: message.conversation_id || existing?.conversation_id || "",
    conversation_route: message.conversation_route || existing?.conversation_route || "",
    nickname: pickNickname(message, existing?.nickname),
    last_text: message.text,
    last_timestamp: message.timestamp,
    updated_at: Date.now() / 1000,
  };
  const others = state.conversations.filter((item) => item.conversation_key !== key);
  return dedupeConversations([updated, ...others]);
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${window.location.host}/ws`);

  state.socket.addEventListener("open", () => setConnectionStatus(true));
  state.socket.addEventListener("close", () => {
    setConnectionStatus(false);
    setTimeout(connectWebSocket, 2000);
  });
  state.socket.addEventListener("message", (event) => {
    try {
      handleSocketPayload(JSON.parse(event.data));
    } catch (error) {
      console.error("Invalid websocket payload", error);
    }
  });
}

async function sendMessage() {
  const conversation = getActiveConversation();
  const text = messageInputEl.value.trim();
  if (!conversation || !text) return;

  sendButtonEl.disabled = true;
  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        contact: conversation.nickname,
        conversation_id: conversation.conversation_id || null,
        conversation_key: conversation.conversation_key,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "发送失败");
    }
    if (data.message) {
      upsertMessage(data.message);
      renderMessages();
    }
    messageInputEl.value = "";
  } catch (error) {
    alert(error.message || "发送失败");
  } finally {
    sendButtonEl.disabled = !getActiveConversation();
  }
}

sendButtonEl.addEventListener("click", sendMessage);
messageInputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

connectWebSocket();
fetch("/api/conversations")
  .then((response) => response.json())
  .then((data) => {
    mergeConversations(data.conversations || []);
    if (!state.activeConversationKey && state.conversations.length) {
      selectConversation(state.conversations[0].conversation_key);
    } else {
      renderConversations();
    }
  })
  .catch(() => {});
