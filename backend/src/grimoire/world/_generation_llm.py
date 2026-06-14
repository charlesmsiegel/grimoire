"""Shared LLM-call plumbing for world generators (atmosphere, locations)."""

from __future__ import annotations

import json
import logging
from typing import Any

from grimoire.types.llm import CompletionRequest


async def complete_to_dict(
    *,
    gateway: Any,
    route: str,
    request: CompletionRequest,
    campaign_id: str | None,
    logger: logging.Logger,
    error_label: str,
) -> dict[str, Any] | None:
    """Run a world-generation completion and return the parsed JSON object.

    Returns ``None`` on any failure — gateway error, malformed JSON, or a
    non-object payload — so callers can fall back to an empty result. Gateway
    errors are logged at WARNING under the caller's ``logger`` as
    ``"<error_label>: gateway error"``.
    """
    try:
        response = await gateway.complete(route, request, campaign_id=campaign_id, turn_id=None)
    except Exception:
        logger.warning("%s: gateway error", error_label, exc_info=True)
        return None
    raw = getattr(response, "text", "") or ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
