"""Thin OpenRouter client: the shared chat-completions wire (openai_wire.py)
with the endpoint fixed and the API key required."""

from __future__ import annotations

from typing import AsyncIterator

from .llm import LLMError
from .openai_wire import ChatCompletionsClient

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(LLMError):
    pass


class OpenRouterClient(ChatCompletionsClient):
    error_cls = OpenRouterError

    async def stream(self, messages, model: str, key: str) -> AsyncIterator[str]:
        if not key:
            raise OpenRouterError("missing_key", "OpenRouter API key is not set")
        payload = {"model": model, "messages": messages, "stream": True}
        async for delta in self._stream_chat(API_URL, key, payload):
            yield delta

    async def complete(self, messages, model: str, key: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key)])
