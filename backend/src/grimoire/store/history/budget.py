"""Fit actual Jinja-rendered history, including the section's own frame.

S2 freezes macro-expanded item blocks and fixed frame runs from these same
templates. This module never formats a second copy of their heading or joins.
Trimming changes both excerpt text and its post payload: downstream evidence
must describe the exact prefix the narrator received.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from ... import prompts
from ..inference import limits
from . import model, settings

MIN_ITEM_TOKENS = 80
WINDOW_SHARE = 0.10
BUDGET_SHARE = 0.15


def render_item(item: model.EvidenceItem) -> str:
    return prompts.render("scene/sections/history_item.j2", item=item).strip()


def render_blocks(blocks: Sequence[str]) -> str:
    """The authoritative frame; also accepts S2's already-expanded blocks."""
    return prompts.render("scene/sections/history_recall.j2", history_blocks=list(blocks)).strip()


def render(items: Sequence[model.EvidenceItem]) -> str:
    return render_blocks([render_item(item) for item in items])


def ceiling(cfg: dict, resolved) -> int:
    cap = settings.integer(cfg.get("history_recall_budget"), 1200)
    if resolved is not None:
        window = limits.prompt_ceiling(resolved).tokens
        if window is not None:
            cap = min(cap, int(WINDOW_SHARE * window))
    context = settings.integer(cfg.get("context_budget"), 0)
    if context:
        cap = min(cap, int(BUDGET_SHARE * context))
    return cap


def _excerpt(posts: tuple[model.EvidencePost, ...]) -> model.Excerpt:
    # 08 gives the rendered speaker on every ordinary post; a transition has
    # no stored speaker and is shown as Grimoire by transcript.j2. No raw
    # transcript is read here: all contents came from the prompt adapter.
    text = prompts.render(
        "snippets/transcript.j2",
        messages=[{"role": "assistant", "speaker": p.speaker, "content": p.text} for p in posts],
    )
    return model.Excerpt(posts, text)


def _trim(
    item: model.EvidenceItem, allowance: int, count: Callable[[str], int]
) -> model.EvidenceItem | None:
    while count(render_item(item)) > allowance:
        if not item.excerpts:
            return None
        last = item.excerpts[-1]
        if not last.posts:
            item = replace(item, excerpts=item.excerpts[:-1], evidence_ids=())
        elif len(last.posts) > 1:
            item = replace(
                item, excerpts=(*item.excerpts[:-1], _excerpt(last.posts[:-1])), evidence_ids=()
            )
        else:
            post = last.posts[0]
            # A binary prefix search pays logarithmically for long posts. The
            # final rendered count below remains the authority with any counter.
            lo, hi, best = 0, len(post.text) - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                shortened = replace(post, text=post.text[:mid].rstrip() + "…")
                trial = replace(
                    item, excerpts=(*item.excerpts[:-1], _excerpt((shortened,))), evidence_ids=()
                )
                if count(render_item(trial)) <= allowance:
                    best, lo = trial, mid + 1
                else:
                    hi = mid - 1
            item = (
                best
                if best is not None
                else replace(item, excerpts=item.excerpts[:-1], evidence_ids=())
            )
    if not item.excerpts and (not item.summary or item.summary_omission):
        return None
    return replace(item, tokens=count(render_item(item)))


def fit(
    items: Sequence[model.EvidenceItem], ceiling: int, count: Callable[[str], int]
) -> list[model.EvidenceItem]:
    if ceiling < MIN_ITEM_TOKENS:
        return []
    cfg = settings.read()
    depth = cfg.depth if cfg is not None else max(len(items), 1)
    share = max(MIN_ITEM_TOKENS, ceiling // depth)
    kept: list[model.EvidenceItem] = []
    for item in items:
        fitted = _trim(item, share, count)
        if fitted is None:
            continue
        # The heading and separators are charged in the final section, rather
        # than pretending item counts add exactly under a real tokenizer.
        allowance = min(share, ceiling)
        while fitted is not None and count(render([*kept, fitted])) > ceiling:
            allowance -= max(1, count(render([*kept, fitted])) - ceiling)
            if allowance <= 0:
                fitted = None
                break
            fitted = _trim(fitted, allowance, count)
        if fitted is not None:
            kept.append(fitted)
    return kept
