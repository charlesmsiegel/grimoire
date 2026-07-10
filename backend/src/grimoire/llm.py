"""Provider-agnostic LLM surface: shared error type and (Task 4) dispatch facade."""

from __future__ import annotations


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response | missing_dependency
        self.detail = detail or kind


class LLMClient:
    """Dispatches each call to the provider named in the config dict."""

    def __init__(self, openrouter=None, claude=None):
        # Imported here rather than at module top: openrouter.py imports
        # LLMError from this module, so top-level imports would be circular.
        from .claude_agent import ClaudeAgentClient
        from .openrouter import OpenRouterClient
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()

    def stream(self, messages: list[dict], cfg: dict):
        if cfg.get("provider", "openrouter") == "claude":
            return self._claude.stream(messages, cfg.get("claude_model", "opus"))
        return self._openrouter.stream(messages, cfg["model"], cfg["openrouter_key"])

    async def complete(self, messages: list[dict], cfg: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, cfg)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
