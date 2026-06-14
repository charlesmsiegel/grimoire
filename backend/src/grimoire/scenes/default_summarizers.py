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

from typing import Protocol

from grimoire.scenes._summary_llm import complete_text
from grimoire.scenes.types import Post, Scene
from grimoire.types.llm import CompletionRequest
from grimoire.util import extract_json_object


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
# Distinct task routes so the rolling/running summary (cheap, frequent) can be
# tiered separately from the final scene close-out summary (durable artifact).
# See ``llm_gateway/tiers.py``: running → Light, final → Heavy.
_RUNNING_TASK = "scenes.running_summary"
_FINAL_TASK = "scenes.final_summary"
_DEFAULT_MAX_TOKENS = 1024


def _post_window(posts: list[Post], n: int = 10) -> str:
    parts: list[str] = []
    for p in posts[-n:]:
        parts.append(f"[Post {p.order_in_scene} — {p.author_label}]\n{p.body.strip()}")
    return "\n\n".join(parts)


_ROLLING_SYSTEM = (
    "You are a tight-prose scene summarizer for a tabletop RPG companion. "
    "Maintain a rolling summary that captures the most important narrative "
    "developments so far. Aim for 3-5 short sentences. No bullet lists."
)
_FINAL_SYSTEM_TEMPLATE = (
    "You are a scene close-out summarizer for a tabletop RPG companion. "
    "Given the full post history, return a short final summary plus a "
    "list of {max_key_beats} or fewer key beats that drove the scene. "
    "Respond with a JSON object ONLY, no prose, no markdown fences."
)


async def _run_rolling_summary(
    gateway: _Gateway,
    task: str,
    *,
    previous: str | None,
    posts: list[Post],
    window: int,
    model: str,
    max_tokens: int,
    label: str,
) -> str:
    """Produce/extend a rolling running summary; return ``previous`` on failure."""
    if not posts:
        return previous or ""
    previous_block = (previous or "(no prior summary)").strip()
    user = (
        f"Previous running summary:\n{previous_block}\n\n"
        f"Recent posts:\n{_post_window(posts, n=window)}\n\n"
        "Return only the updated running summary."
    )
    text = await complete_text(
        gateway,
        task,
        system=_ROLLING_SYSTEM,
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=0.4,
        label=label,
    )
    return text if text is not None else (previous or "")


async def _run_final_summary(
    gateway: _Gateway,
    task: str,
    *,
    scene: Scene,
    posts: list[Post],
    running: str | None,
    model: str,
    max_tokens: int,
    max_key_beats: int,
    label: str,
) -> tuple[str, list[str]]:
    """Produce the final ``(summary, key_beats)``; fall back to trivial output."""
    if not posts:
        return running or scene.running_summary or "", []
    system = _FINAL_SYSTEM_TEMPLATE.format(max_key_beats=max_key_beats)
    running_block = (running or scene.running_summary or "(none)").strip()
    user = (
        f"Scene title: {scene.title or scene.slug}\n"
        f"Running summary so far: {running_block}\n\n"
        f"Full scene posts:\n{_post_window(posts, n=len(posts))}\n\n"
        'Return JSON of the form: {"summary": "...", "key_beats": ["...", "..."]}'
    )
    text = await complete_text(
        gateway,
        task,
        system=system,
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=0.3,
        label=label,
    )
    if text is None:
        return running or _trivial_summary(scene, posts), []
    parsed = extract_json_object(text)
    if parsed is None:
        return text, []
    summary = str(parsed.get("summary") or "").strip()
    beats_raw = parsed.get("key_beats") or []
    beats = [str(b).strip() for b in beats_raw if isinstance(b, (str, int))]
    beats = [b for b in beats if b][:max_key_beats]
    if not summary:
        summary = _trivial_summary(scene, posts)
    return summary, beats


def make_default_running_summarizer(
    gateway: _Gateway,
    *,
    task: str = _RUNNING_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
):
    """Build the production-default running-summary callable.

    Signature matches ``SceneManager.summarizer``: takes the previous summary
    (or ``None``) plus the recent posts and returns the new summary string.
    """

    async def _summarize(previous: str | None, recent: list[Post]) -> str:
        return await _run_rolling_summary(
            gateway,
            task,
            previous=previous,
            posts=recent,
            window=10,
            model=model,
            max_tokens=max_tokens,
            label="running summary",
        )

    return _summarize


def make_default_final_summarizer(
    gateway: _Gateway,
    *,
    task: str = _FINAL_TASK,
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
        return await _run_final_summary(
            gateway,
            task,
            scene=scene,
            posts=posts,
            running=None,
            model=model,
            max_tokens=max_tokens,
            max_key_beats=max_key_beats,
            label="final summary",
        )

    return _finalize


def _trivial_summary(scene: Scene, posts: list[Post]) -> str:
    if scene.running_summary:
        return scene.running_summary
    if not posts:
        return ""
    first = posts[0].body.split("\n", 1)[0]
    last = posts[-1].body.split("\n", 1)[0]
    return f"{first} … {last}"


_FALLBACK_CONTEXT_WINDOW = 100_000


class _AdaptiveGateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> object: ...

    def resolve_route(self, task: str, campaign_id: str | None = None) -> object: ...

    async def get_model_info(self, provider_id: str, model: str) -> object | None: ...


def make_adaptive_summarizer(
    gateway: _AdaptiveGateway,
    *,
    task: str | None = None,
    running_task: str = _RUNNING_TASK,
    final_task: str = _FINAL_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
    max_key_beats: int = 5,
):
    """Build a summarizer that adapts between single-pass and windowed mode.

    When the total post tokens fit in half the model's context window, uses
    the single-pass final summarizer. Otherwise, processes posts in windows
    using a rolling summary, then produces the final summary from the
    accumulated context.

    The intermediate rolling passes route through ``running_task`` (Light
    tier) while the final close-out pass routes through ``final_task``
    (Heavy tier). ``task``, if given, overrides both for back-compat.
    """
    if task is not None:
        running_task = task
        final_task = task

    async def _get_context_window() -> int:
        try:
            route = gateway.resolve_route(final_task)
            info = await gateway.get_model_info(route.provider_id, route.model)
            if info is not None:
                cw = getattr(info, "context_window", 0) or 0
                if cw > 0:
                    return cw
        except Exception:
            pass
        return _FALLBACK_CONTEXT_WINDOW

    async def _rolling_pass(previous: str | None, posts: list[Post]) -> str:
        return await _run_rolling_summary(
            gateway,
            running_task,
            previous=previous,
            posts=posts,
            window=len(posts),
            model=model,
            max_tokens=max_tokens,
            label="adaptive rolling summary",
        )

    async def _final_pass(
        scene: Scene, posts: list[Post], running: str | None
    ) -> tuple[str, list[str]]:
        return await _run_final_summary(
            gateway,
            final_task,
            scene=scene,
            posts=posts,
            running=running,
            model=model,
            max_tokens=max_tokens,
            max_key_beats=max_key_beats,
            label="adaptive final summary",
        )

    async def _adaptive(scene: Scene, posts: list[Post]) -> tuple[str, list[str]]:
        if not posts:
            return scene.running_summary or "", []

        context_window = await _get_context_window()
        total_chars = sum(len(p.body) for p in posts)
        total_tokens_est = total_chars // 4
        budget = context_window // 2

        if total_tokens_est <= budget:
            return await _final_pass(scene, posts, scene.running_summary)

        window_chars = budget * 4
        running = scene.running_summary
        windows: list[list[Post]] = []
        current_window: list[Post] = []
        current_chars = 0
        for p in posts:
            if current_chars + len(p.body) > window_chars and current_window:
                windows.append(current_window)
                current_window = []
                current_chars = 0
            current_window.append(p)
            current_chars += len(p.body)
        if current_window:
            windows.append(current_window)

        for window in windows[:-1]:
            running = await _rolling_pass(running, window)

        return await _final_pass(scene, posts, running)

    return _adaptive


__all__ = [
    "make_adaptive_summarizer",
    "make_default_final_summarizer",
    "make_default_running_summarizer",
]
