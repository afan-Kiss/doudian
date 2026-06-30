/**
 * Live DOM probe — no SDK dependency for sidebar/chat read.
 */
async () => {
  const utils = window.__feigeMessageUtils;
  const NOISE =
    /欢迎光临|商家配置|客服\S*接入|超时未回复|系统关闭会话|全部会话|当前会话|留言|历史会话/;

  const sdkAvailable = Boolean(window.__monaGlobalStore?.getData?.("initContextData")?.im);
  const monaAvailable = Boolean(window.__monaGlobalStore?.getData);

  const visibleRows = (utils?.scanSessionRowsDom?.() || []).map((row, row_index) => ({
    row_index,
    buyer_name: row.buyer_name || "",
    last_text: row.last_text || "",
    time_text: row.time_text || "",
    unread_badge: row.unread_badge || "",
    is_active: Boolean(row.is_active),
    raw_text: row.raw_text || "",
    class_name: row.class_name || "",
    is_system_notice: Boolean(
      utils?.isSystemNoticeConversation?.({ buyer_name: row.buyer_name, last_text: row.last_text })
        ?.is_system_notice
    ),
  }));

  const messages = utils?.collectFeigeChatMessages?.() || [];

  const inputSelectors = [
    'textarea[class*="inputArea"]',
    'textarea[placeholder*="Enter"]',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"]',
    "textarea",
  ];
  let inputAvailable = false;
  let sendButtonAvailable = false;
  for (const sel of inputSelectors) {
    if (document.querySelector(sel)) {
      inputAvailable = true;
      break;
    }
  }
  for (const sel of ['button[class*="send"]', '[class*="send-btn"]', 'button[type="submit"]']) {
    if (document.querySelector(sel)) {
      sendButtonAvailable = true;
      break;
    }
  }

  const headerName = utils?.pickHeaderTitle?.() || "";

  return {
    ok: true,
    url: window.location.href,
    title: document.title,
    visible_session_rows: visibleRows,
    active_chat: {
      buyer_name_from_header: headerName,
      messages,
      input_available: inputAvailable,
      send_button_available: sendButtonAvailable,
    },
    sdk_available: sdkAvailable,
    mona_available: monaAvailable,
  };
};
