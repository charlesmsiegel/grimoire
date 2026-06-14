"""Shared LLM-call plumbing for scene summarizers and analyzers.

The default/adaptive summarizers (``default_summarizers``) and the scene
analyzers (``analysis``) all share the same call shape: build a single-message
request, call the gateway, and recover from failure by returning a fallback.
This module owns that shape so a change to error handling lands once.
"""

from __future__ import annotations

import logging
from typing import Any

from grimoire.types.llm import CompletionRequest, Message, MessageRole

logger = logging.getLogger(__name__)


async def complete_text(
    gateway: Any,
    task: str,
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    temperature: float,
    campaign_id: str | None = None,
    label: str,
) -> str | None:
    """Run a single completion and return the stripped response text.

    Returns ``None`` on any failure — a gateway exception or empty/non-string
    text — so callers fall back to their own trivial output. Exceptions are
    logged at WARNING as ``"<label> LLM call failed: <exc>"``.
    """
    request = CompletionRequest(
        model=model,
        messages=[Message(role=MessageRole.USER, content=user)],
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    try:
        response = await gateway.complete(task, request, campaign_id=campaign_id)
    except Exception as exc:
        logger.warning("%s LLM call failed: %s", label, exc)
        return None
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()
