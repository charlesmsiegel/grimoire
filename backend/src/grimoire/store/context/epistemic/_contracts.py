"""Immutable perspective/slice/view vocabulary and one stable logical logger shared by classification and record decoding."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from ... import knowledge
from ...history import model

log = logging.getLogger('grimoire.store.context.epistemic')

ACCESS = ("witnessed", "known", "suspected", "narrator_only", "unknown")
VISIBLE = ("witnessed", "known", "suspected")
#: The bases an `unknown` part may carry and still be asked about (spec 11
#: §5.1 rule 2): nothing proven either way. An override's `narrator_only`, an
#: unverifiable or moved post, a keyless post under a post-listed override
#: and a group nobody can expand are never asked about.
ELIGIBLE_BASES = frozenset({"not_recorded_present", "scene_cast", "no_record",
                            "summary_needs_grant"})
#: What a Decision answer may set (§5.3): a model never produces `witnessed`
#: or `narrator_only`, and an answer that withholds stays `unknown`.
DECIDED_ACCESS = ("known", "suspected", "unknown")
DECIDED_BASES = frozenset({"decision", "decision_abstained", "decision_failed",
                           "decision_skipped"})
STATES = ("attributed", "withheld", "unattributed")
#: The closed set of §3.5, plus `unkeyed` (a keyless post in a scene with a
#: post-listed override) and `narrator` (the narrator's own slices).
BASES = frozenset({
    "override", "override_group", "override_all", "authored", "pc_speaker",
    "presence_interval", "not_recorded_present", "scene_cast", "no_record",
    "summary_needs_grant", "decision", "decision_abstained", "decision_failed",
    "decision_skipped", "unverifiable", "moved", "unkeyed", "narrator",
})
NARRATOR = "narrator"
_ACTOR_PREFIX = "actor:"
_STATUS = {knowledge.KNOWS: "known", knowledge.SUSPECTS: "suspected",
           knowledge.UNAWARE: "narrator_only"}
#: A scene digest no stored fingerprint can equal: what `SceneFacts.digest`
#: reads when the chronicle could not be read, so no whole-scene grant holds.
_NO_DIGEST = "\x00unreadable"
_ELLIPSIS = "…"


# ---------------------------------------------------------------- vocabulary


@dataclass(frozen=True)
class Perspective:
    """Who a compose step is written for (§3.1). `actor` is a ``characters:``
    ref for an NPC call and None for the narrator; `present` is every actor
    and PC ref on stage this turn, from the post-exclude cast."""
    cid: str
    sid: str
    identity: str | None
    actor: str | None
    present: tuple[str, ...]

    def to_string(self) -> str:
        """09-C1's and 10-C1's ``perspective: str``."""
        return NARRATOR if self.actor is None else _ACTOR_PREFIX + self.actor

    @staticmethod
    def of_string(s: str, *, cid: str, sid: str, identity: str | None,
                  present: tuple[str, ...]) -> Perspective:
        if s == NARRATOR:
            return Perspective(cid, sid, identity, None, tuple(present))
        if s.startswith(_ACTOR_PREFIX) and knowledge.valid_audience(s[len(_ACTOR_PREFIX):]) \
                and s[len(_ACTOR_PREFIX):].startswith("characters:"):
            return Perspective(cid, sid, identity, s[len(_ACTOR_PREFIX):], tuple(present))
        raise ValueError(f"not a perspective: {s!r}")

    @staticmethod
    def of(cid: str, sid: str, actor_ref: str | None, present: Sequence[str], *,
           identity: str | None = None) -> Perspective:
        """`actor_ref` as compose spells it: None and ``"grimoire"`` are the
        narrator, exactly `assemble`'s ``actor_scoped`` test."""
        if actor_ref in (None, "grimoire"):
            return Perspective(cid, sid, identity, None, tuple(present))
        return Perspective.of_string(_ACTOR_PREFIX + str(actor_ref), cid=cid, sid=sid,
                                     identity=identity, present=tuple(present))


@dataclass(frozen=True)
class Slice:
    """A contiguous run of one excerpt's posts in one access class, or an
    item's summary (`part="summary"`, no posts). `text` is set only on a slice
    its perspective may see; an invisible slice is for the inspector."""
    part: str
    posts: tuple[int, ...]
    access: str
    basis: tuple[str, ...]
    text: str
    #: The excerpt posts the text was (or, withheld, would be) rendered
    #: from: for a section to mark and fit (`context/history.section`), and
    #: for the Decision stage to excerpt a withheld part (11-S6). Empty for
    #: moved posts and the summary. Never in `detail()`, and never rendered
    #: from an invisible slice: every renderer reads `visible()`.
    source: tuple[model.EvidencePost, ...] = ()
    #: Each post's fingerprint as classified (`knowledge.shown_fingerprint`),
    #: for the capture's accept to send as `expect` (11-S3); empty for a
    #: moved slice and for the summary.
    keys: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class Classified:
    """One evidence item as one perspective may have it. For an actor,
    `item` is a redacted copy (header fields it may see, no summary, no
    excerpts): a renderer reads `slices`, never the evidence it came from."""
    item: model.EvidenceItem
    slices: tuple[Slice, ...]
    header: Mapping[str, str]
    state: str
    known_to: tuple[str, ...]
    suspected_by: tuple[str, ...]
    admitted: bool = True
    #: Each roster ref's own classes, for the inspector and the capture: per
    #: re-matched post index `(access, basis)`, and the whole scene's under
    #: "summary". Empty when no rule ran.
    by_ref: Mapping[str, Mapping[str, tuple[str, str]]] = field(default_factory=dict)
    #: The source scene's id when classified. An actor's `item` blanks it;
    #: the inspector and an accept still need to name the scene.
    sid: str = ""
    #: For an actor, whether it stood in the item's scene: the appearance
    #: record's scene list, the chronicle cast, or any presence interval
    #: there (spec 11 §5.1 (a)). The Decision stage's first channel.
    stood: bool = False


@dataclass(frozen=True)
class EpistemicView:
    perspective: Perspective
    items: tuple[Classified, ...]
    #: Parts the Decision stage (11-S6) made visible, and every part it was
    #: eligible to ask about, as `(scene key, part, posts)`; 0 and () when
    #: it did not run.
    decided: int = 0
    pending: tuple[tuple[str, str, tuple[int, ...]], ...] = ()
    #: The retrieval the items came from: its ceiling and depth size the
    #: sections, its coverage is the inspector's row. Its items are the
    #: narrator's; an actor's sections read `visible()`, never these.
    evidence: model.Evidence = field(default_factory=model.Evidence)
    #: What history cost the round this view's step belongs to, so far
    #: (spec 11 §14 Q7): `retrievals` and the query `embeds` they sent.
    #: Set by the turn path; empty for a preview or hand-built view.
    round_cost: Mapping[str, int] = field(default_factory=dict)

    def visible(self) -> tuple[Classified, ...]:
        """For an actor, the items with a visible slice, holding only those
        slices; an item with none is omitted, header and all. For the
        narrator, every admitted item without its dropped (`moved`) posts."""
        return tuple(shown for _c, shown in self.visible_pairs())

    def visible_pairs(self) -> tuple[tuple[Classified, Classified], ...]:
        """`visible()`, each beside the item it was cut from."""
        out = []
        for c in self.items:
            if not c.admitted:
                continue
            shown = tuple(s for s in c.slices if s.access in VISIBLE)
            if self.perspective.actor is not None and not shown:
                continue
            out.append((c, replace(c, slices=shown)))
        return tuple(out)

    def detail(self) -> dict:
        """The JSON form for the inspector and the prompt-log capture. Texts
        are left out; basis codes are in, and never reach a prompt."""
        return {
            "perspective": self.perspective.to_string(),
            "decided": self.decided,
            **({"pending": [[k, p, list(posts)] for k, p, posts in self.pending]}
               if self.pending else {}),
            **({"round": dict(self.round_cost)} if self.round_cost else {}),
            "items": [{
                "key": c.item.scene.key, "scene": c.sid or c.item.scene.sid,
                "admitted": c.admitted, "state": c.state, "header": dict(c.header),
                **({"stood": True} if c.stood else {}),
                "known_to": list(c.known_to), "suspected_by": list(c.suspected_by),
                "slices": [{"part": s.part, "posts": list(s.posts), "access": s.access,
                            "basis": list(s.basis), "keys": [dict(k) for k in s.keys]}
                           for s in c.slices],
                "by_ref": {ref: {part: list(cls) for part, cls in parts.items()}
                           for ref, parts in c.by_ref.items()},
            } for c in self.items],
        }
