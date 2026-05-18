"""LLM-driven Location frontmatter generation (§9 of world remaining-design).

Companion to the rule-based "unresolved location" detector in
``extractor/rule_based.py``: the detector emits a low-confidence
EMERGENT_CREATE delta that routes to the review queue; when the user
approves it (or, in v1, when WorldService is asked to materialize it),
this module asks the LLM gateway for a Location frontmatter dict that
the campaign can store as an emergent entity.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from grimoire.templates import render as render_template
from grimoire.types.llm import CompletionRequest, Message, MessageRole

logger = logging.getLogger(__name__)


async def generate_location_frontmatter(
    *,
    gateway: Any,
    name: str,
    context: str = "",
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Return a Location frontmatter dict, or ``{}`` on any failure."""
    if gateway is None:
        return {}
    sys_text = render_template("world_location_generate_system")
    user_text = render_template(
        "world_location_generate_user",
        name=name,
        context=context,
    )
    request = CompletionRequest(
        model="",
        messages=[Message(role=MessageRole.USER, content=user_text)],
        system=sys_text,
        max_tokens=512,
        temperature=0.7,
    )
    try:
        response = await gateway.complete(
            "world_location_generate",
            request,
            campaign_id=campaign_id,
            turn_id=None,
        )
    except Exception:
        logger.warning("emergent location generation failed: gateway error", exc_info=True)
        return {}
    raw = getattr(response, "text", "") or ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed
