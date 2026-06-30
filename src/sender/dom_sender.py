from __future__ import annotations

from playwright.async_api import Frame, Page, TimeoutError as PlaywrightTimeoutError


class DOMSender:
    """Send Feige messages through the real UI so the app builds fresh WS frames."""

    SEND_BUTTON_SELECTORS = [
        'button:has-text("发送")',
        'span:has-text("发送")',
        '[class*="send-btn"]',
        '[class*="SendButton"]',
        '[class*="send"] button',
        'button[type="submit"]',
    ]

    EDITOR_SELECTORS = [
        'textarea[class*="inputArea"]',
        'textarea[placeholder*="Enter"]',
        'textarea[placeholder*="输入"]',
        'textarea[placeholder*="回复"]',
        '.public-DraftEditor-content[contenteditable="true"]',
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"]',
        '[data-contents="true"]',
        'div[role="textbox"]',
        "textarea",
    ]

    async def fill_only(self, page: Page, text: str) -> bool:
        for frame in page.frames:
            if await self._fill_in_frame(frame, text):
                return True
        return False

    async def _fill_in_frame(self, frame: Frame, text: str) -> bool:
        editor = await self._find_editor(frame)
        if not editor:
            return False
        try:
            await editor.scroll_into_view_if_needed(timeout=3000)
            await editor.click(timeout=3000)
        except PlaywrightTimeoutError:
            pass
        return await self._fill_editor(frame, editor, text)

    async def send(self, page: Page, text: str) -> bool:
        for frame in page.frames:
            if await self._send_in_frame(frame, text):
                return True
        return False

    async def _send_in_frame(self, frame: Frame, text: str) -> bool:
        editor = await self._find_editor(frame)
        if not editor:
            return False

        try:
            await editor.scroll_into_view_if_needed(timeout=3000)
            await editor.click(timeout=3000)
        except PlaywrightTimeoutError:
            return False

        typed = await self._fill_editor(frame, editor, text)
        if not typed:
            return False

        if await self._click_send(frame):
            return True

        try:
            await frame.page.keyboard.press("Enter")
            return True
        except PlaywrightTimeoutError:
            return False

    async def _fill_editor(self, frame: Frame, editor, text: str) -> bool:
        try:
            tag = await editor.evaluate("el => el.tagName?.toLowerCase?.() || ''")
        except PlaywrightTimeoutError:
            tag = ""

        if tag == "textarea":
            try:
                await editor.fill(text, timeout=3000)
                current = await editor.input_value()
                if text in current or current == text:
                    return True
            except PlaywrightTimeoutError:
                pass

        try:
            await editor.fill("")
        except PlaywrightTimeoutError:
            pass

        try:
            await frame.page.keyboard.press("Control+A")
            await frame.page.keyboard.press("Backspace")
            await frame.page.keyboard.type(text, delay=25)
            if tag == "textarea":
                current = await editor.input_value()
                return text in current or len(current) >= min(len(text), 8)
            inner = await editor.inner_text()
            return text in inner
        except PlaywrightTimeoutError:
            pass

        return await frame.evaluate(
            """
            ({ selectors, text }) => {
                let editor = null;
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    if (nodes.length) {
                        editor = nodes[nodes.length - 1];
                        break;
                    }
                }
                if (!editor) {
                    return false;
                }

                editor.focus();
                if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
                    editor.value = text;
                    editor.dispatchEvent(new Event('input', { bubbles: true }));
                    editor.dispatchEvent(new Event('change', { bubbles: true }));
                    return editor.value.includes(text);
                }

                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                editor.dispatchEvent(new ClipboardEvent('paste', {
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true,
                }));

                const current = String(editor.innerText || editor.textContent || '');
                if (!current.includes(text)) {
                    editor.textContent = text;
                    editor.dispatchEvent(new InputEvent('input', { bubbles: true }));
                }
                return String(editor.innerText || editor.textContent || '').includes(text);
            }
            """,
            {"selectors": self.EDITOR_SELECTORS, "text": text},
        )

    async def _click_send(self, frame: Frame) -> bool:
        try:
            send_btn = frame.get_by_role("button", name="发送").first
            if await send_btn.count() > 0:
                await send_btn.click(timeout=3000)
                return True
        except PlaywrightTimeoutError:
            pass

        for selector in self.SEND_BUTTON_SELECTORS:
            button = frame.locator(selector).first
            if await button.count() == 0:
                continue
            try:
                await button.click(timeout=3000)
                return True
            except PlaywrightTimeoutError:
                continue
        return False

    async def _find_editor(self, frame: Frame):
        for selector in self.EDITOR_SELECTORS:
            locator = frame.locator(selector).last
            if await locator.count() > 0:
                return locator

        try:
            textbox = frame.get_by_role("textbox").last
            if await textbox.count() > 0:
                return textbox
        except PlaywrightTimeoutError:
            pass
        return None
