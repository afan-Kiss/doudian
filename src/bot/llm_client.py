from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMClient:
    """OpenAI-compatible chat completions client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "",
        api_key_id: str = "",
        timeout_sec: float = 60.0,
        temperature: float = 0.7,
        top_p: float | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.api_key_id = api_key_id.strip()
        self.timeout_sec = timeout_sec
        self.temperature = temperature
        self.top_p = top_p

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.api_key_id:
            headers["X-Api-Key-Id"] = self.api_key_id
        return headers

    async def chat(self, messages: list[dict[str, str]], *, model: str | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                return await asyncio.to_thread(self._chat_sync, messages, model, attempt)
            except RuntimeError as exc:
                last_error = exc
                message = str(exc)
                if "HTTP 429" not in message and "ServerOverloaded" not in message:
                    raise
                wait_sec = min(30, 3 * (2 ** attempt))
                await asyncio.sleep(wait_sec)
        raise last_error or RuntimeError("LLM API request failed")

    def _chat_sync(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        attempt: int = 0,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if not payload["model"]:
            raise ValueError("LLM model is not configured. Set bot.model in config.yaml or BOT_MODEL in .env")

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise RuntimeError(
                    "火山方舟 API Key 无效。请在方舟控制台「API Key 管理」创建 Key，"
                    "写入 .env 的 BOT_API_KEY（Bearer 令牌，不是 Access Key ID）。"
                    f" 原始错误: {detail[:300]}"
                ) from exc
            if exc.code == 429:
                raise RuntimeError(f"LLM API HTTP 429: {detail[:500]}") from exc
            raise RuntimeError(f"LLM API HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM API request failed: {exc}") from exc

        text = self._extract_text(data)
        return text

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM API returned no choices: {data}")
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError(f"LLM API returned empty content: {data}")
        return content
