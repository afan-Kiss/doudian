from __future__ import annotations

import base64
from typing import Any

from playwright.async_api import Frame, Page


SEND_TEXT_JS = """
async ({ text, conversationId }) => {
    const toB64 = (bytes) => {
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    };

    const beforeSend = (window.__feigeWsState && window.__feigeWsState.sendCount) || 0;
    const beforeRecv = (window.__feigeWsState && window.__feigeWsState.recvCount) || 0;

    const captured = [];
    const originalSend = WebSocket.prototype.send;
    WebSocket.prototype.send = function patchedSend(data) {
        let bytes = null;
        if (data instanceof ArrayBuffer) {
            bytes = new Uint8Array(data);
        } else if (data instanceof Uint8Array) {
            bytes = data;
        }
        if (bytes && bytes.length > 2500 && bytes[0] === 0x08) {
            captured.push({ size: bytes.length, b64: toB64(bytes) });
        }
        return originalSend.call(this, data);
    };

    const finish = (payload) => {
        WebSocket.prototype.send = originalSend;
        const afterSend = (window.__feigeWsState && window.__feigeWsState.sendCount) || 0;
        const afterRecv = (window.__feigeWsState && window.__feigeWsState.recvCount) || 0;
        return {
            ...payload,
            sendDelta: afterSend - beforeSend,
            recvDelta: afterRecv - beforeRecv,
        };
    };

    const getMonaContext = () => {
        const ctx = window.__monaGlobalStore?.getData?.('initContextData');
        const im = ctx?.im;
        let convId = null;
        try {
            ctx?.doAction?.((store) => {
                convId = store?.conversationsInfo?.currentConversation?.id || null;
            });
        } catch (error) {
            convId = null;
        }
        return { ctx, im, convId };
    };

    const { im, convId: currentConvId } = getMonaContext();
    const convId = (conversationId || currentConvId || "").trim() || null;
    if (im && convId && typeof im.sendText === "function") {
        try {
            const result = im.sendText(convId, text);
            if (result && typeof result.then === 'function') {
                await result;
            }
            await new Promise((resolve) => setTimeout(resolve, 400));
            const frame = captured.find((item) => item.size >= 2500) || captured[0];
            const afterSend = (window.__feigeWsState && window.__feigeWsState.sendCount) || 0;
            const sendDelta = afterSend - beforeSend;
            const payloadLen = frame ? frame.size : 0;
            const verified = captured.length > 0 && payloadLen >= 2500;
            return finish({
                ok: verified,
                mode: 'mona_im_sendText',
                conversationId: convId,
                payloadB64: frame ? frame.b64 : null,
                payloadLength: payloadLen,
                capturedCount: captured.length,
                sendDelta,
                reason: verified ? null : 'no_ws_send_detected',
            });
        } catch (error) {
            return finish({
                ok: false,
                reason: 'mona_sendText_failed',
                error: String(error),
                conversationId: convId,
            });
        }
    }

    const pigeon = window.__mona_pigeon_event || window.__monaEvent;
    const entry = pigeon?._eventMapByApp?.get?.('sendTextMessage');
    if (entry?.listener && typeof entry.listener === 'function') {
        try {
            const result = entry.listener(text, {});
            if (result && typeof result.then === 'function') {
                await result;
            }
            const frame = captured.find((item) => item.size >= 2500) || captured[0];
            const payloadLen = frame ? frame.size : 0;
            const verified = captured.length > 0 && payloadLen >= 2500;
            return finish({
                ok: verified,
                mode: 'pigeon_sendTextMessage_event',
                conversationId: convId,
                payloadB64: frame ? frame.b64 : null,
                payloadLength: payloadLen,
                capturedCount: captured.length,
                reason: verified ? null : 'no_ws_send_detected',
            });
        } catch (error) {
            return finish({
                ok: false,
                reason: 'pigeon_event_failed',
                error: String(error),
                conversationId: convId,
            });
        }
    }

    return finish({
        ok: false,
        reason: 'sdk_sender_not_found',
        hasIm: Boolean(im),
        conversationId: convId,
        hasPigeonListener: Boolean(entry?.listener),
    });
}
"""

PROBE_JS = """
() => {
    const ctx = window.__monaGlobalStore?.getData?.('initContextData');
    const im = ctx?.im;
    let convId = null;
    let convName = null;
    try {
        ctx?.doAction?.((store) => {
            const conv = store?.conversationsInfo?.currentConversation;
            convId = conv?.id || null;
            convName = conv?.name || conv?.nickname || null;
        });
    } catch (error) {
        // ignore
    }
    const proto = im ? Object.getOwnPropertyNames(Object.getPrototypeOf(im)).filter((key) => {
        return typeof im[key] === 'function' && /send/i.test(key);
    }) : [];
    return {
        hasMonaStore: Boolean(window.__monaGlobalStore),
        hasIm: Boolean(im),
        hasSendText: Boolean(im && typeof im.sendText === 'function'),
        conversationId: convId,
        conversationName: convName,
        sendMethods: proto.slice(0, 20),
        wsState: window.__feigeWsState || {},
    };
}
"""


class PageWsEncoder:
    """Send Feige text messages via Mona pigeon IM SDK (signature-aware WS)."""

    async def send_text(
        self,
        page: Page | Frame,
        text: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return await page.evaluate(
            SEND_TEXT_JS,
            {"text": text, "conversationId": (conversation_id or "").strip()},
        )

    async def encode_text_message(self, page: Page | Frame, text: str) -> bytes | None:
        result = await self.send_text(page, text)
        if not result or not result.get("ok"):
            return None
        payload_b64 = result.get("payloadB64")
        if not payload_b64:
            return None
        return base64.b64decode(payload_b64)

    async def probe(self, page: Page | Frame) -> dict[str, Any]:
        env = await page.evaluate(PROBE_JS)
        return {"env": env}
