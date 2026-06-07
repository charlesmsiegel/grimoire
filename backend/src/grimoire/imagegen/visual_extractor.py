"""Light-LLM visual element extraction for image prompts.

Turns recent scene prose into a short list of concrete, *visual* phrases
(subjects and their action, setting, lighting, mood, framing) suitable for a
text-to-image prompt. Runs on the LIGHT tier via the LLM gateway; on any
failure it returns an empty list so :class:`~grimoire.imagegen.prompt.PromptComposer`
falls back to its deterministic keyword heuristic.

This is the "light LLM" half of the illustrate flow: the composer feeds it the
last few posts and folds the result together with the campaign style preset,
in-scene character prompts, and location description.
"""

from __future__ import annotations

import logging
from typing import Any

from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.util import extract_json_object

logger = logging.getLogger(__name__)

#: Default gateway task name — mapped to ``Tier.LIGHT`` in
#: :mod:`grimoire.llm_gateway.tiers`.
DEFAULT_TASK = "imagegen.prompt"

_SYSTEM = (
    "You convert tabletop-RPG narration into a text-to-image prompt. Read the "
    "most recent posts and list the concrete VISIBLE elements of the current "
    "moment: the main subject(s) and their visible action or pose, the setting, "
    "notable objects, lighting, time of day, weather, colour palette, mood, and "
    "camera framing. Ignore anything not visually depictable — proper names, "
    "spoken dialogue, internal thoughts, and game rules. Return STRICT JSON of "
    'the form {"elements": ["phrase", ...]} with 4 to 10 short comma-free '
    "phrases, most important first."
)


class LLMVisualExtractor:
    """Gateway-backed visual-element extractor (LIGHT tier).

    Implements the ``_VisualExtractor`` protocol consumed by
    :class:`~grimoire.imagegen.prompt.PromptComposer`.
    """

    def __init__(
        self,
        gateway: Any,
        *,
        task: str = DEFAULT_TASK,
        max_tokens: int = 300,
    ) -> None:
        self._gateway = gateway
        self._task = task
        self._max_tokens = max_tokens

    async def extract_visual_elements(self, text: str) -> list[str]:
        prose = (text or "").strip()
        if not prose:
            return []
        request = CompletionRequest(
            model="default",
            messages=[Message(role=MessageRole.USER, content=prose)],
            system=_SYSTEM,
            max_tokens=self._max_tokens,
            temperature=0.4,
        )
        try:
            response = await self._gateway.complete(self._task, request)
        except Exception as exc:
            logger.warning("imagegen visual extraction failed: %s", exc)
            return []
        out = getattr(response, "text", None)
        if not isinstance(out, str) or not out.strip():
            return []
        parsed = extract_json_object(out)
        if not isinstance(parsed, dict):
            return []
        elements = parsed.get("elements")
        if not isinstance(elements, list):
            return []
        return [str(item).strip() for item in elements if str(item).strip()]


__all__ = ["DEFAULT_TASK", "LLMVisualExtractor"]
