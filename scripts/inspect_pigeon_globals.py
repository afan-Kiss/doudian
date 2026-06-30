#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.browser.launcher import BrowserLauncher
from src.config import load_config
from src.sender.feige_navigator import FeigeNavigator
from src.sender.frame_context import find_im_frame

INSPECT_JS = """
() => {
    const describe = (value, depth = 0) => {
        if (value == null) {
            return String(value);
        }
        const t = typeof value;
        if (t === 'function') {
            return `function ${value.name || '(anon)'} len=${value.length} src=${value.toString().slice(0, 200)}`;
        }
        if (t !== 'object') {
            return String(value);
        }
        if (depth > 2) {
            return '[object]';
        }
        if (Array.isArray(value)) {
            return value.slice(0, 10).map((item) => describe(item, depth + 1));
        }
        const out = {};
        for (const key of Object.keys(value).slice(0, 40)) {
            try {
                out[key] = describe(value[key], depth + 1);
            } catch (error) {
                out[key] = `!${error}`;
            }
        }
        return out;
    };

    const keys = Object.keys(window).filter((key) => {
        const lower = key.toLowerCase();
        return (
            lower.includes('pigeon')
            || lower.includes('mona')
            || lower.includes('light')
            || lower.includes('im_')
            || lower.startsWith('__im')
            || lower.includes('workbench')
            || lower.includes('goofy')
        );
    });

    const globals = {};
    for (const key of keys) {
        try {
            globals[key] = describe(window[key]);
        } catch (error) {
            globals[key] = `!${error}`;
        }
    }

    const chunkKeys = Object.keys(window).filter((key) => key.includes('webpack') || key.includes('chunk'));

    let lightRuntime = null;
    if (typeof window.__get_light_runtime === 'function') {
        try {
            lightRuntime = describe(window.__get_light_runtime());
        } catch (error) {
            lightRuntime = `!${error}`;
        }
    }

    let pigeonEvent = null;
    if (window.__mona_pigeon_event) {
        pigeonEvent = describe(window.__mona_pigeon_event);
    }

    return {
        globals,
        chunkKeys: chunkKeys.slice(0, 30),
        lightRuntime,
        pigeonEvent,
    };
}
"""

TRY_SEND_JS = """
async ({ text }) => {
    const toB64 = (bytes) => {
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    };

    const before = (window.__feigeWsState && window.__feigeWsState.sendCount) || 0;
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

    const attempts = [];

    const tryCall = async (label, fn, args) => {
        try {
            const result = fn(...args);
            if (result && typeof result.then === 'function') {
                await result;
            }
            attempts.push({ label, ok: true, captured: captured.length });
        } catch (error) {
            attempts.push({ label, ok: false, error: String(error), captured: captured.length });
        }
    };

    const evt = window.__mona_pigeon_event;
    if (evt) {
        const eventNames = ['sendMessage', 'sendText', 'message.send', 'im.send', 'pigeon.send', 'send'];
        for (const name of eventNames) {
            if (typeof evt.emit === 'function') {
                await tryCall(`emit:${name}`, evt.emit, [name, { content: text, type: 'text', text }]);
                await tryCall(`emit2:${name}`, evt.emit, [name, text]);
            }
            if (typeof evt.publish === 'function') {
                await tryCall(`publish:${name}`, evt.publish, [name, { content: text, type: 'text' }]);
            }
            if (typeof evt.dispatch === 'function') {
                await tryCall(`dispatch:${name}`, evt.dispatch, [{ type: name, payload: { content: text } }]);
            }
        }
        if (typeof evt.sendMessage === 'function') {
            await tryCall('evt.sendMessage', evt.sendMessage, [{ content: text, type: 'text' }]);
        }
        if (typeof evt.sendText === 'function') {
            await tryCall('evt.sendText', evt.sendText, [text]);
        }
    }

    let runtime = null;
    if (typeof window.__get_light_runtime === 'function') {
        try {
            runtime = window.__get_light_runtime();
        } catch (error) {
            runtime = null;
        }
    }
    if (runtime) {
        const names = ['sendMessage', 'sendText', 'send', 'im', 'message'];
        for (const name of names) {
            const target = runtime[name];
            if (typeof target === 'function') {
                await tryCall(`runtime.${name}`, target, [{ content: text, type: 'text' }]);
                await tryCall(`runtime.${name}2`, target, [text]);
            }
        }
        if (runtime.im && typeof runtime.im.sendMessage === 'function') {
            await tryCall('runtime.im.sendMessage', runtime.im.sendMessage, [{ content: text, type: 'text' }]);
        }
    }

    WebSocket.prototype.send = originalSend;
    const after = (window.__feigeWsState && window.__feigeWsState.sendCount) || 0;

    return {
        attempts,
        captured: captured.length,
        sendDelta: after - before,
        ok: captured.length > 0 || after > before,
    };
}
"""


async def run(args: argparse.Namespace) -> int:
    config = load_config()
    launcher = BrowserLauncher(config)
    navigator = FeigeNavigator()

    page = await launcher.start(open_url=config["urls"]["feige"])
    await page.wait_for_timeout(args.page_wait * 1000)

    if args.contact:
        await navigator.open_chat_by_name(page, args.contact, timeout_ms=args.find_timeout * 1000)

    await navigator.wait_for_ws_ready(page, timeout_ms=args.ws_timeout * 1000)
    im_frame = await find_im_frame(page)

    inspect = await im_frame.evaluate(INSPECT_JS)
    print("=== INSPECT ===")
    print(json.dumps(inspect, ensure_ascii=False, indent=2))

    send_try = await im_frame.evaluate(TRY_SEND_JS, {"text": args.text})
    print("=== TRY SEND ===")
    print(json.dumps(send_try, ensure_ascii=False, indent=2))

    await launcher.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contact", default="一只小青蛙")
    parser.add_argument("--text", default="SDK探测消息")
    parser.add_argument("--page-wait", type=int, default=12)
    parser.add_argument("--find-timeout", type=int, default=45)
    parser.add_argument("--ws-timeout", type=int, default=30)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
