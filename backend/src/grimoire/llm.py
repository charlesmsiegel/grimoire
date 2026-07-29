"""Provider-agnostic LLM surface: the dispatch facade over the providers.

The shared error type lives in `llm_errors.py`, not here — see its docstring.
"""

from __future__ import annotations

from .claude_agent import ClaudeAgentClient
from .openai_compatible import OpenAICompatibleClient
from .openrouter import OpenRouterClient


class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, openrouter=None, claude=None, openai_compatible=None):
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())

    def stream(self, messages: list[dict], conn: dict):
        kind = conn.get("kind", "openrouter")
        if kind == "claude":
            return self._claude.stream(messages, conn.get("model") or "opus")
        if kind == "openai_compatible":
            return self._openai_compatible.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""),
                conn.get("base_url", ""), strict=conn.get("post_process") == "strict")
        return self._openrouter.stream(messages, conn["model"], conn.get("api_key", ""))

    async def complete(self, messages: list[dict], conn: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, conn)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
