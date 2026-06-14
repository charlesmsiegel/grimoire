"""LLM-driven atmosphere generation (§3 of world remaining-design)."""

from __future__ import annotations

import logging
from typing import Any

from grimoire.templates import render as render_template
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.world._generation_llm import complete_to_dict

logger = logging.getLogger(__name__)


async def generate_atmosphere(
    *,
    gateway: Any,
    world_id: str,
    name: str,
    tags: list[str] | None = None,
    description: str = "",
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM gateway for a world atmosphere block.

    Returns the parsed JSON dict, or ``{}`` on any failure (malformed JSON,
    gateway error, no gateway provided). Callers should treat ``{}`` as
    "leave atmosphere empty".
    """
    if gateway is None:
        return {}
    system_text = render_template("world_atmosphere_system")
    user_text = render_template(
        "world_atmosphere_user",
        name=name,
        tags=list(tags or []),
        description=description or "",
    )
    request = CompletionRequest(
        model="",  # the gateway fills in the route's model
        messages=[Message(role=MessageRole.USER, content=user_text)],
        system=system_text,
        max_tokens=512,
        temperature=0.7,
    )
    parsed = await complete_to_dict(
        gateway=gateway,
        route="world_atmosphere",
        request=request,
        campaign_id=campaign_id,
        logger=logger,
        error_label="atmosphere generation failed",
    )
    if parsed is None:
        return {}
    return {
        "default_register": str(parsed.get("default_register") or ""),
        "default_palette": str(parsed.get("default_palette") or ""),
        "mood_tags": [str(t) for t in (parsed.get("mood_tags") or [])],
    }
