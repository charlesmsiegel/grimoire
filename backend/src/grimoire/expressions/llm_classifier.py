"""LLM-backed expression classifier.

A single Haiku-level call per post that emits a JSON map of
``{character_id: emotion}``. Returned ``ExpressionChange`` items merge
with the heuristic strategy's via highest-confidence-wins.

This module is intentionally light on dependencies on the LLM gateway:
the classifier accepts a callable ``llm_call`` that returns the JSON
payload, so unit tests can supply a deterministic stub and the real
gateway integration lives outside this file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from grimoire.types.expressions import (
    CoreExpression,
    ExpressionChange,
    is_known_label,
)

logger = logging.getLogger(__name__)

LlmCall = Callable[[str], Awaitable[str]]


_PROMPT_TEMPLATE = """Classify the expression of each present character in the
following passage. Use only labels from this vocabulary: {vocab}.
Emit JSON: a single object mapping character id to emotion. Do not include
characters whose emotion is uncertain.

Present characters:
{present}

Passage:
{text}
"""


def build_prompt(
    *,
    text: str,
    present_characters: Iterable[tuple[str, str]],
    vocabulary: Iterable[str],
) -> str:
    present_lines = "\n".join(f"- {cid}: {name}" for cid, name in present_characters)
    vocab = ", ".join(sorted(set(vocabulary)))
    return _PROMPT_TEMPLATE.format(text=text, present=present_lines, vocab=vocab)


async def llm_classify(
    *,
    llm_call: LlmCall,
    scene_post_text: str,
    present_characters: Iterable[tuple[str, str]],
    module_extensions: dict[str, list[str]] | None = None,
    scene_id: str = "",
    post_id: str = "",
    confidence: float = 0.8,
) -> list[ExpressionChange]:
    """Run the LLM classifier and parse the JSON response.

    ``llm_call`` is awaited with the full prompt; it must return the raw
    text body of the model response. Malformed JSON or unknown labels
    are logged and dropped.
    """
    present_list = list(present_characters)
    if not present_list:
        return []
    vocab: list[str] = [e.value for e in CoreExpression]
    if module_extensions:
        for mod_id, labels in module_extensions.items():
            vocab.extend(f"{mod_id}.{label}" for label in labels)

    prompt = build_prompt(
        text=scene_post_text, present_characters=present_list, vocabulary=vocab
    )
    try:
        response = await llm_call(prompt)
    except Exception as exc:
        logger.warning("expression LLM call failed: %s", exc)
        return []

    payload = _parse_json_safely(response)
    if not isinstance(payload, dict):
        return []

    present_ids = {cid for cid, _ in present_list}
    out: list[ExpressionChange] = []
    for cid, emotion in payload.items():
        if cid not in present_ids:
            continue
        if not isinstance(emotion, str):
            continue
        if not is_known_label(emotion, module_extensions=module_extensions or {}):
            logger.info("LLM proposed unknown emotion label %r; discarding", emotion)
            continue
        out.append(
            ExpressionChange(
                character_id=cid,
                scene_id=scene_id,
                post_id=post_id,
                emotion=emotion,
                confidence=confidence,
                evidence=scene_post_text[:240],
            )
        )
    return out


def _parse_json_safely(text: str) -> Any:
    # Models routinely wrap JSON in markdown fences or trailing
    # commentary; trim to the first balanced object.
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # Drop a leading "json" hint if present.
        if s.lower().startswith("json"):
            s = s[4:]
    # Find the first opening brace and the matching closing brace.
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


def merge_changes(
    heuristic: list[ExpressionChange],
    llm: list[ExpressionChange],
) -> list[ExpressionChange]:
    """Highest-confidence-wins merge on ``character_id``."""
    by_id: dict[str, ExpressionChange] = {}
    for change in (*heuristic, *llm):
        existing = by_id.get(change.character_id)
        if existing is None or change.confidence > existing.confidence:
            by_id[change.character_id] = change
    return list(by_id.values())


__all__ = ["build_prompt", "llm_classify", "merge_changes"]
