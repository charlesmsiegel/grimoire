"""Default LLM-driven summarizers for the Scene Manager (§4, §5).

The Scene Manager exposes two summarizer seams (``summarizer`` for the
cadence-driven running summary and ``final_summarizer`` for ``close_scene``).
This module supplies the production defaults that route through the LLM
Gateway with a small/cheap model. Both fall back to safe trivial output if
the gateway raises so a flaky model doesn't propagate exceptions into the
play loop.

Tests inject their own callables; nothing here is imported when those seams
are filled by the caller.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from grimoire.scenes.types import Post, Scene
from grimoire.types.llm import CompletionRequest, Message, MessageRole

logger = logging.getLogger(__name__)


class _Gateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> object: ...


_DEFAULT_TASK = "scene_summary"
_DEFAULT_MAX_TOKENS = 512


def _post_window(posts: list[Post], n: int = 10) -> str:
    parts: list[str] = []
    for p in posts[-n:]:
        parts.append(f"[Post {p.order_in_scene} — {p.author_label}]\n{p.body.strip()}")
    return "\n\n".join(parts)


def make_default_running_summarizer(
    gateway: _Gateway,
    *,
    task: str = _DEFAULT_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
):
    """Build the production-default running-summary callable.

    Signature matches ``SceneManager.summarizer``: takes the previous summary
    (or ``None``) plus the recent posts and returns the new summary string.
    """

    async def _summarize(previous: str | None, recent: list[Post]) -> str:
        if not recent:
            return previous or ""
        system = (
            "You are a tight-prose scene summarizer for a tabletop RPG companion. "
            "Maintain a rolling summary that captures the most important narrative "
            "developments so far. Aim for 3-5 short sentences. No bullet lists."
        )
        previous_block = (previous or "(no prior summary)").strip()
        user = (
            f"Previous running summary:\n{previous_block}\n\n"
            f"Recent posts:\n{_post_window(recent)}\n\n"
            "Return only the updated running summary."
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.4,
        )
        try:
            response = await gateway.complete(task, request)
        except Exception as exc:
            logger.warning("running summary LLM call failed: %s", exc)
            return previous or ""
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return previous or ""
        return text.strip()

    return _summarize


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = _JSON_FENCE.search(text)
    if fence is not None:
        text = fence.group(1)
    # Find the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def make_default_final_summarizer(
    gateway: _Gateway,
    *,
    task: str = _DEFAULT_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
    max_key_beats: int = 5,
):
    """Build the production-default final-summary callable.

    Returns a coroutine matching ``SceneManager.final_summarizer`` — given a
    scene and its full post list, produces ``(final_summary, key_beats)``.
    Asks the model for JSON to keep parsing tractable; on parse failure
    falls back to "first line … last line" so ``close_scene`` always
    succeeds.
    """

    async def _finalize(scene: Scene, posts: list[Post]) -> tuple[str, list[str]]:
        if not posts:
            return scene.running_summary or "", []
        system = (
            "You are a scene close-out summarizer for a tabletop RPG companion. "
            "Given the full post history, return a short final summary plus a "
            f"list of {max_key_beats} or fewer key beats that drove the scene. "
            "Respond with a JSON object ONLY, no prose, no markdown fences."
        )
        running_block = (scene.running_summary or "(none)").strip()
        user = (
            f"Scene title: {scene.title or scene.slug}\n"
            f"Running summary so far: {running_block}\n\n"
            f"Full scene posts:\n{_post_window(posts, n=len(posts))}\n\n"
            'Return JSON of the form: {"summary": "...", "key_beats": ["...", "..."]}'
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request)
        except Exception as exc:
            logger.warning("final summary LLM call failed: %s", exc)
            return _trivial_summary(scene, posts), []
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return _trivial_summary(scene, posts), []
        parsed = _extract_json(text)
        if parsed is None:
            return text.strip(), []
        summary = str(parsed.get("summary") or "").strip()
        beats_raw = parsed.get("key_beats") or []
        beats = [str(b).strip() for b in beats_raw if isinstance(b, (str, int))]
        beats = [b for b in beats if b][:max_key_beats]
        if not summary:
            summary = _trivial_summary(scene, posts)
        return summary, beats

    return _finalize


def _trivial_summary(scene: Scene, posts: list[Post]) -> str:
    if scene.running_summary:
        return scene.running_summary
    if not posts:
        return ""
    first = posts[0].body.split("\n", 1)[0]
    last = posts[-1].body.split("\n", 1)[0]
    return f"{first} … {last}"


__all__ = [
    "make_default_final_summarizer",
    "make_default_running_summarizer",
]
