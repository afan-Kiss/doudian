/**
 * Open a Feige conversation by SDK key. Args: conversationId string
 */
async (conversationId) => {
  const key = String(conversationId || "").trim();
  if (!key) return { ok: false, reason: "empty-id" };
  const ctx = window.__monaGlobalStore?.getData?.("initContextData");
  const im = ctx?.im;
  if (!im) return { ok: false, reason: "no-im" };
  try {
    if (typeof im.ensureConversation === "function") await im.ensureConversation(key);
    else if (typeof im.openConversation === "function") await im.openConversation(key);
    else return { ok: false, reason: "no-open-method" };
  } catch (e) {
    return { ok: false, reason: String(e?.message || e) };
  }
  const pullTasks = [
    () => im.pullMessagesByConversationId?.(key),
    () => im.getHistoryMessages?.({ conversationId: key, limit: 30 }),
  ];
  for (const task of pullTasks) {
    try { await task?.(); } catch (e) {}
  }
  await new Promise((r) => setTimeout(r, 1500));
  return { ok: true, conversation_id: key };
}
