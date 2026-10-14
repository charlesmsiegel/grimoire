"""What the Decision stage may ask about, and the items it asks (spec 11 §5.1-5.2).

The deterministic classifier (`epistemic.py`) leaves a part `unknown` when
nothing proves the actor has it. Some of those parts the actor had a way to
learn, and those -- only those -- may be put to a model:

- the part is `unknown` on a basis that proves nothing either way
  (`epistemic.eligible`);
- and a **structural channel** exists: (a) the actor stood in that scene at
  all (`Classified.stood`), the only gate for a summary; or (b) a later scene
  in this evidence, in play order, holds a post the actor witnessed beside a
  present character who deterministically has every post of the part -- a
  plausible teller.

Both channels are read off one classification of the actor's perspective over
the whole present roster (`classify(..., roster=present)`), so (b) costs no
model call. "Later" compares scene numbers (`scene_ids.parse_sid`); a scene
whose id is outside that grammar is never later, so it opens no (b) channel.

An item's excerpt is the part's own posts as 09 handed them over (already the
prompt-phase view, through 08's expansion), the actor's OWN `state.md` knows
and suspects, and for a (b) part up to two of the later posts that opened the
channel -- posts the actor witnessed. Never another actor's state, a dossier,
or a part the actor cannot see apart from the one asked about: the item is
itself a prompt the capture shows. No basis code enters it (§3.5).

Pure and read-only; it renders templates, so a caller runs it in a worker.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from .. import epistemic_access, playstate, scene_ids
from ..appearances import paths as appearances_paths
from ..history import budget as history_budget
from . import epistemic

log = logging.getLogger(__name__)

#: The longest excerpt an item carries, in characters, its context included.
#: Argued from the item's job, which is one part and what could have carried
#: it, not a scene: to be tuned against real prompts later.
EXCERPT_CAP = 2400
#: Each of the actor's own `knows` and `suspects`, at most, in characters.
STATE_CAP = 600
#: The later posts a (b) part's item quotes, at most.
CHANNEL_POSTS = 2
_ELLIPSIS = "…"

STOOD = "stood"
TOLD = "told"


@dataclass(frozen=True)
class Part:
    """One part the Decision stage may ask about."""
    key: str                       # the item's scene key
    part: str                      # "posts" | "summary"
    posts: tuple[int, ...]         # re-matched indices; () for the summary
    text: str                      # the part as 09 handed it over
    channel: str                   # STOOD or TOLD
    #: For a TOLD part, the later posts that opened the channel.
    told: tuple[str, ...] = ()

    @property
    def ident(self) -> tuple[str, str, tuple[int, ...]]:
        return (self.key, self.part, self.posts)


def ref_of(part: Part | tuple[str, str, tuple[int, ...]]) -> str:
    """A stable spelling of a part, as its question's `evidence_ref`."""
    key, kind, posts = part.ident if isinstance(part, Part) else part
    return f"{key}#{kind}:{','.join(str(i) for i in posts)}"


def _number(sid: str) -> int | None:
    parsed = scene_ids.parse_sid(sid) if sid else None
    return parsed["number"] if parsed else None


def _text(s: epistemic.Slice, c: epistemic.Classified, summaries: dict[str, str]) -> str:
    if s.part == "summary":
        return summaries.get(c.item.scene.key, "")
    return history_budget._excerpt(s.source).text if s.source else ""


def _witnessed(c: epistemic.Classified) -> dict[int, str]:
    """The actor's witnessed posts in `c`, re-matched index -> text."""
    out: dict[int, str] = {}
    for s in c.slices:
        if s.part == "posts" and s.access == "witnessed":
            out.update((i, p.text) for i, p in zip(s.posts, s.source, strict=False))
    return out


def _has(c: epistemic.Classified, ref: str, posts: Sequence[int]) -> bool:
    classes = c.by_ref.get(ref, {})
    return bool(posts) and all(
        classes.get(str(i), ("unknown", ""))[0] in epistemic.VISIBLE for i in posts)


def _told(view: epistemic.EpistemicView, c: epistemic.Classified,
          s: epistemic.Slice) -> tuple[str, ...]:
    """The later posts that make `s` a (b) part, or () when none does."""
    actor = view.perspective.actor
    tellers = [r for r in view.perspective.present
               if r != actor and _has(c, r, s.posts)]
    here = _number(c.sid)
    if not tellers or here is None:
        return ()
    out: list[str] = []
    for later in view.items:
        there = _number(later.sid)
        if not later.admitted or there is None or there <= here:
            continue
        for i, text in sorted(_witnessed(later).items()):
            if any(later.by_ref.get(t, {}).get(str(i), ("", ""))[0] == "witnessed"
                   for t in tellers):
                out.append(text)
                if len(out) == CHANNEL_POSTS:
                    return tuple(out)
    return tuple(out)


def eligible(view: epistemic.EpistemicView,
             summaries: dict[str, str] | None = None) -> tuple[Part, ...]:
    """Every part of `view` the Decision stage may ask about (§5.1), in 09's
    order. `view` is the actor's perspective classified over the present
    roster; the narrator's yields nothing. `summaries` maps an item's key to
    its summary as 09 handed it over (an actor's item is redacted, so the
    caller passes the retrieval's own)."""
    if view.perspective.actor is None:
        return ()
    out: list[Part] = []
    for c in view.items:
        if not c.admitted:
            continue
        for s in c.slices:
            if not epistemic.eligible(s):
                continue
            text = _text(s, c, summaries or {})
            if not text:
                continue
            if c.stood:
                out.append(Part(c.item.scene.key, s.part, s.posts, text, STOOD))
            elif s.part == "posts":
                told = _told(view, c, s)
                if told:
                    out.append(Part(c.item.scene.key, s.part, s.posts, text, TOLD, told))
    return tuple(out)


def eligible_for(view: epistemic.EpistemicView) -> tuple[Part, ...]:
    """`eligible` for a compose step's view: its actor's perspective
    classified again over the present roster (the (b) channel reads every
    present ref's class), with the retrieval's own summaries. One read; no
    model call."""
    if view.perspective.actor is None:
        return ()
    full = epistemic.classify(view.perspective, view.evidence.items,
                              roster=view.perspective.present)
    summaries = {i.scene.key: i.summary for i in view.evidence.items
                 if i.summary and not i.summary_omission}
    return eligible(full, summaries)


def own_state(cid: str, actor_ref: str) -> tuple[str, str]:
    """The actor's OWN `knows` and `suspects`, each capped, or ("", "") when
    there is none or it cannot be read: the item can inform its answer with
    them, and never with anyone else's."""
    kind, _, aid = actor_ref.partition(":")
    if kind != "characters" or not aid:
        return "", ""
    try:
        st = playstate.read_state(appearances_paths.locked_actor_root(cid), aid)
    except Exception:  # noqa: BLE001 -- an unreadable state only thins the item's context
        log.warning("A character's state could not be read for a Decision item")
        return "", ""
    if not st:
        return "", ""
    return _cap(st.get("knows") or "", STATE_CAP), _cap(st.get("suspects") or "", STATE_CAP)


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + _ELLIPSIS


def excerpt(part: Part, state: tuple[str, str]) -> str:
    """The item's excerpt: the part, then the actor's own state, then the
    channel's later posts, inside `EXCERPT_CAP` (the part first, so a cap
    takes the context before it takes the evidence)."""
    lines = [part.text.strip()]
    knows, suspects = state
    if knows:
        lines.append(f"What they already know: {knows}")
    if suspects:
        lines.append(f"What they already suspect: {suspects}")
    if part.told:
        lines.append("Later, with someone who was there: "
                     + " / ".join(t.strip() for t in part.told))
    return _cap("\n".join(lines), EXCERPT_CAP)


def questions(parts: Sequence[Part], *, actor_ref: str, actor_name: str,
              state: tuple[str, str]) -> tuple[epistemic_access.AccessQuestion, ...]:
    """One kit question per part, in order, with no basis codes (§3.5)."""
    return tuple(epistemic_access.AccessQuestion(
        actor_ref=actor_ref, actor_name=actor_name, evidence_ref=ref_of(p),
        excerpt=excerpt(p, state), basis=()) for p in parts)
