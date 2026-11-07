"""Values of the narration continuity referee (Sprint 047, first slice).

A ``ContinuityClaim`` is one atomic, labelled statement a proposed narration makes about the fiction, anchored
to the exact characters of the prose it reads. ``Facts`` are the immutable records other sprints own, at one
campaign sequence: 041's stage, 023's holdings, 034's possessions, 012's knowledge, 029's resolutions,
040's obstacle states and 039's choices. A ``Finding`` is what the referee reports about one claim, with the
records it rests on.

Nothing here reads prose by itself, calls a model, writes anything or changes the narration: the labels come
from a labeller that is not the narrator, and a finding is a report, never a repair.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import islice
from dataclasses import dataclass, field

from ..core import InstanceRef, Knowledge
from ..core.perspective import actor_knowledge
from ..core.authority import (
    ControlAssignment, Delegation, ImposedCondition, NarrationClaim, NarrationContext, NarrationReview,
    PlayerContribution, Resolution, Revocation,
)
from ..core.identity import validate_segment
from ..holdings import Holdings
from ..obstacles import ObstacleState
from ..possessions import Possession
from ..comms import CHANNELS
from ..goals import MAX_TAGS, Choice
from ..staging import Stage

#: The categories of continuity the referee checks, and the claim kinds of each.
CATEGORIES = ("presence", "staging", "possession", "knowledge", "outcome", "intention")
KINDS = {
    "present": "presence",     # the actor is in the scene, doing or saying something there
    "reaches": "staging",      # the actor gets a thing into hand: takes, touches, handles it
    "sees": "staging",         # the actor sees a target
    "hears": "staging",        # the actor hears a speaker on a 032 channel
    "spends": "possession",    # the actor uses up ``count`` of a 023 counted item
    "wields": "possession",    # the actor uses a 034 unique item, which needs it in their custody
    "knows": "knowledge",      # the actor's words or acts reveal a 012 proposition
    "resolves": "outcome",     # a 029 attempt came out a given way
    "overcomes": "outcome",    # a 040 obstacle is overcome (by a given approach)
    "acts": "intention",       # the actor does an act of the given 039 kinds (approach tags: trade, threat...)
}
#: How the labeller read the claim. Only a ``literal`` claim can be a contradiction.
MODES = ("literal", "figurative", "unreliable")
#: What a finding says about a claim. Only a contradiction is material.
CLASSIFICATIONS = ("contradiction", "uncertain", "metaphor", "unreliable")
#: Where a finding's evidence comes from.
SOURCES = ("stage", "holdings", "possession", "knowledge", "resolution", "obstacle", "intention", "narration")
#: The longest narration one review reads, and the most claims it takes.
MAX_TEXT = 200_000
MAX_CLAIMS = 256
MAX_COUNT = 10 ** 12
_FIELDS = ("target", "item", "count", "channel", "proposition_id", "attempt_id", "result", "obstacle_id",
           "approach_id", "tags")
_NEEDS = {
    "present": (), "reaches": ("target",), "sees": ("target",), "hears": ("target", "channel"),
    "spends": ("item", "count"), "wields": ("target",), "knows": ("proposition_id",),
    "resolves": ("attempt_id", "result"), "overcomes": ("obstacle_id",), "acts": ("tags",),
}
_OPTIONAL = {"overcomes": ("approach_id",)}


class RefereeError(ValueError):
    """A malformed claim, fact or review: a machine ``reason`` and no text, label or reference."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def _fail(reason: str) -> RefereeError:
    return RefereeError(reason)


def _segment(value: object) -> None:
    if type(value) is not str:  # a str subclass could redefine equality and slip past a match (as 039 refuses)
        raise _fail("invalid_field")
    try:
        validate_segment(value, "id")
    except (TypeError, ValueError):
        raise _fail("invalid_field") from None


def _ref(value: object, campaign_id: str) -> None:
    if type(value) is not InstanceRef:
        raise _fail("invalid_field")
    if value.campaign_id != campaign_id:
        raise _fail("campaign_mismatch")


def _tuple(value: object, kind: type) -> tuple:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise _fail("invalid_field")
    try:
        items = tuple(value)
    except Exception:  # an iterator that fails part-way is a bad field, not a crash
        raise _fail("invalid_field") from None
    if not all(type(item) is kind for item in items):
        raise _fail("invalid_field")
    return items


def check_text(text: object) -> str:
    """The narration under review: a nonempty string of at most ``MAX_TEXT`` characters."""
    if type(text) is not str or not text or len(text) > MAX_TEXT:
        raise _fail("invalid_text")
    return text


def _span(span: object) -> tuple[int, int]:
    if (type(span) is not tuple or len(span) != 2 or not all(type(i) is int for i in span)
            or not 0 <= span[0] < span[1] <= MAX_TEXT):
        raise _fail("invalid_field")
    return span


@dataclass(frozen=True, slots=True)
class ContinuityClaim:
    """One atomic statement of a proposed narration, as a labeller that is not the narrator read it.

    ``span`` is the half-open character range of the narration the claim reads, and ``quote`` those exact
    characters, so a finding points at the words it is about and a misanchored label is caught. ``actor`` is
    who the claim is about. The other fields are the kind's (see ``KINDS``): ``target`` is the thing reached
    or wielded, the individual seen or the speaker heard; ``item`` and ``count`` a counted stack spent;
    ``channel`` a 032 channel; ``proposition_id`` a 012 proposition revealed; ``attempt_id`` and ``result`` a
    029 outcome; ``obstacle_id`` and ``approach_id`` a 040 obstacle overcome; ``tags`` the 039 kinds of act an
    ``acts`` claim narrates (``trade``, ``threat``...). A field a kind does not take must be ``None``.

    ``mode`` is how the labeller read it: ``literal`` (the narration states it as so), ``figurative`` (a
    metaphor or turn of phrase: "the news hit her like a locked door") or ``unreliable`` (deliberately false
    narration, such as a lying narrator's). ``certain`` is false when the labeller was unsure of its own
    reading. Neither ever hides a claim: it is still checked, and a figurative or unreliable claim that would
    contradict the facts is reported as such, never as material.
    """

    campaign_id: str
    claim_id: str
    kind: str
    actor: InstanceRef
    span: tuple[int, int]
    quote: str
    target: InstanceRef | None = None
    item: str | None = None
    count: int | None = None
    channel: str | None = None
    proposition_id: str | None = None
    attempt_id: str | None = None
    result: str | None = None
    obstacle_id: str | None = None
    approach_id: str | None = None
    tags: frozenset[str] | None = None
    mode: str = "literal"
    certain: bool = True

    def __post_init__(self) -> None:
        _segment(self.campaign_id)
        _segment(self.claim_id)
        if type(self.kind) is not str or self.kind not in KINDS:
            raise _fail("unknown_kind")
        _ref(self.actor, self.campaign_id)
        _span(self.span)
        if type(self.quote) is not str or len(self.quote) != self.span[1] - self.span[0]:
            raise _fail("invalid_field")
        if type(self.mode) is not str or self.mode not in MODES or type(self.certain) is not bool:
            raise _fail("invalid_field")
        allowed = _NEEDS[self.kind] + _OPTIONAL.get(self.kind, ())
        for name in _FIELDS:
            value = getattr(self, name)
            if value is None:
                if name in _NEEDS[self.kind]:
                    raise _fail("missing_field")
                continue
            if name not in allowed:
                raise _fail("invalid_field")
            if name == "target":
                _ref(value, self.campaign_id)
            elif name == "count":
                if type(value) is not int or not 1 <= value <= MAX_COUNT:
                    raise _fail("invalid_field")
            elif name == "channel":
                if type(value) is not str or value not in CHANNELS:
                    raise _fail("invalid_field")
            elif name == "tags":  # 039's approach tags, by the same rule an agenda's are
                if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
                    raise _fail("invalid_field")
                try:
                    tags = tuple(islice(value, MAX_TAGS + 1))  # never read past the limit
                except Exception:
                    raise _fail("invalid_field") from None
                if not 1 <= len(tags) <= MAX_TAGS:
                    raise _fail("invalid_field")
                for tag in tags:
                    _segment(tag)
                object.__setattr__(self, "tags", frozenset(tags))
            elif name in ("result", "attempt_id"):  # 003's and 029's opaque tokens, not id segments
                if type(value) is not str or not value.strip() or len(value) > 512 or not value.isprintable():
                    raise _fail("invalid_field")
            else:
                _segment(value)

    @property
    def category(self) -> str:
        return KINDS[self.kind]


@dataclass(frozen=True, slots=True)
class LabelledClaim:
    """A 003 authorship claim, anchored to the narration like a ``ContinuityClaim``.

    ``voiced_by`` is the actor id whose own words or voluntary acts the claim's text carries, in the
    labeller's reading (``None``: nobody's, or the subject's own as labelled). It is what lets the referee
    route a possible mislabel: a claim about an NPC that speaks for a PC, or PC speech bundled into an
    outcome, which 003's correction flow cannot reach.
    """

    claim: NarrationClaim
    span: tuple[int, int]
    quote: str
    voiced_by: str | None = None

    def __post_init__(self) -> None:
        if type(self.claim) is not NarrationClaim:
            raise _fail("invalid_field")
        _span(self.span)
        if type(self.quote) is not str or len(self.quote) != self.span[1] - self.span[0]:
            raise _fail("invalid_field")
        if self.claim.text != self.quote:  # 003 reviews exactly the anchored words, never other text
            raise _fail("invalid_field")
        if self.voiced_by is not None:
            _segment(self.voiced_by)


def _record_key(record) -> tuple[str, ...]:
    def text(value) -> str:
        return repr(sorted(value)) if isinstance(value, frozenset) else repr(value)
    return tuple(text(getattr(record, field)) for field in record.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class Authority:
    """003's authority inputs for the authorship review, exactly as ``review_narration`` takes them."""

    context: NarrationContext
    controls: tuple[ControlAssignment, ...]
    contributions: tuple[PlayerContribution, ...] = ()
    delegations: tuple[Delegation, ...] = ()
    revocations: tuple[Revocation, ...] = ()
    conditions: tuple[ImposedCondition, ...] = ()

    def __post_init__(self) -> None:
        if type(self.context) is not NarrationContext:
            raise _fail("invalid_field")
        # Canonical order: 003 takes the first matching record (a delegation's id becomes a claim's basis),
        # so the same records in another order must give the same review.
        for name, kind in (("controls", ControlAssignment), ("contributions", PlayerContribution),
                           ("delegations", Delegation), ("revocations", Revocation),
                           ("conditions", ImposedCondition)):
            records = _tuple(getattr(self, name), kind)
            object.__setattr__(self, name, tuple(sorted(records, key=_record_key)))


@dataclass(frozen=True)
class Facts:
    """The authoritative records one review reads, all of one campaign, at campaign sequence ``as_of``.

    Each family is optional; a claim whose facts are missing is reported unchecked, never passed. ``stage`` must
    be the scene at ``as_of`` itself (``stale_stage`` otherwise): a stage has no lineage, so the caller keeps
    the latest one (041 milestone 2) and moves it on with ``Stage.at`` only when nothing physical changed.
    ``heard`` lists ``(actor, proposition_id)`` pairs said aloud in that actor's hearing this turn that 012
    has not admitted as its evidence yet, so the actor's answering them is not taken for knowing a secret.
    ``intentions`` are the 039 ``Choice`` each NPC was briefed with for this turn, one per actor, chosen from its
    knowledge as of ``as_of`` itself (``stale_intention`` otherwise: a choice is for one turn). With a ``stage``,
    each must be for the cast on it (``intention_cast_mismatch``); with ``knowledge``, from the very projection
    ``actor_knowledge`` gives that NPC at ``as_of`` (``intention_mismatch``), as 039's brief requires.
    """

    campaign_id: str
    as_of: int
    stage: Stage | None = None
    holdings: tuple[Holdings, ...] = ()
    possessions: tuple[Possession, ...] = ()
    knowledge: Knowledge | None = None
    heard: tuple[tuple[InstanceRef, str], ...] = ()
    resolutions: tuple[Resolution, ...] = ()
    obstacles: tuple[ObstacleState, ...] = ()
    intentions: tuple[Choice, ...] = ()
    _by: Mapping[str, Mapping] = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _segment(self.campaign_id)
        if type(self.as_of) is not int or self.as_of < 0:
            raise _fail("invalid_field")
        campaign = self.campaign_id
        if self.stage is not None:
            if type(self.stage) is not Stage:
                raise _fail("invalid_field")
            if self.stage.campaign_id != campaign:
                raise _fail("campaign_mismatch")
            if self.stage.as_of != self.as_of:
                raise _fail("stale_stage")
        if self.knowledge is not None:
            if type(self.knowledge) is not Knowledge:
                raise _fail("invalid_field")
            if self.knowledge.campaign_id != campaign:
                raise _fail("campaign_mismatch")
        holdings = _tuple(self.holdings, Holdings)
        possessions = _tuple(self.possessions, Possession)
        resolutions = _tuple(self.resolutions, Resolution)
        obstacles = _tuple(self.obstacles, ObstacleState)
        intentions = _tuple(self.intentions, Choice)
        heard = _tuple(self.heard, tuple)
        for pair in heard:
            if len(pair) != 2:
                raise _fail("invalid_field")
            _ref(pair[0], campaign)
            _segment(pair[1])
        by_holder = {h.holder: h for h in holdings}
        by_item = {p.item: p for p in possessions}
        by_obstacle = {o.obstacle.obstacle_id: o for o in obstacles}
        by_actor = {choice.actor: choice for choice in intentions}
        if len(by_holder) != len(holdings) or len(by_item) != len(possessions) \
                or len(by_obstacle) != len(obstacles) or len(by_actor) != len(intentions):
            raise _fail("duplicate_id")
        if any(choice.actor.campaign_id != campaign for choice in intentions):
            raise _fail("campaign_mismatch")
        if any(choice.as_of != self.as_of for choice in intentions):
            raise _fail("stale_intention")
        if self.stage is not None:  # a choice is for one scene: which limits apply hangs on who is in it
            cast = frozenset(self.stage.on_stage())
            if any(choice.scene != cast for choice in intentions):
                raise _fail("intention_cast_mismatch")
        if self.knowledge is not None:  # and for the very projection of the NPC's knowledge it was made from
            if any(choice.knowledge != actor_knowledge(self.knowledge, choice.actor, as_of=self.as_of)
                   for choice in intentions):
                raise _fail("intention_mismatch")
        if any(ref.campaign_id != campaign for ref in (*by_holder, *by_item)) \
                or any(r.campaign_id != campaign for r in resolutions) \
                or any(o.obstacle.campaign_id != campaign for o in obstacles):
            raise _fail("campaign_mismatch")
        results: dict[str, set[str]] = {}
        for resolution in resolutions:
            results.setdefault(resolution.attempt_id, set()).add(resolution.result)
        for name, value in (("holdings", holdings), ("possessions", possessions), ("resolutions", resolutions),
                            ("obstacles", obstacles), ("heard", frozenset(heard)),
                            ("intentions", tuple(sorted(intentions, key=lambda c: str(c.actor))))):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_by", {
            "holdings": by_holder, "possessions": by_item, "obstacles": by_obstacle, "intentions": by_actor,
            "results": {key: frozenset(value) for key, value in results.items()},
        })

    def holdings_of(self, holder: InstanceRef) -> Holdings | None:
        return self._by["holdings"].get(holder)

    def possession(self, item: InstanceRef) -> Possession | None:
        return self._by["possessions"].get(item)

    def obstacle(self, obstacle_id: str) -> ObstacleState | None:
        return self._by["obstacles"].get(obstacle_id)

    def results(self, attempt_id: str) -> frozenset[str] | None:
        return self._by["results"].get(attempt_id)

    def intention(self, actor: InstanceRef) -> Choice | None:
        return self._by["intentions"].get(actor)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One record a finding rests on: where it comes from, which record, and what it says (machine text).

    Director-side, like everything a stage or a ledger answers: never put it in an actor's prompt or show it
    to a player as is, since it can name a secret's holders or a hidden thing's place.
    """

    source: str
    ref: str
    detail: str


@dataclass(frozen=True, slots=True)
class Finding:
    """What the referee found about one claim: never a change to the narration.

    ``classification`` is ``contradiction`` (the established records say otherwise: material), ``uncertain``
    (the answer rests on an assumed staging fact, on related evidence an actor could have reasoned from, on a
    line heard but not yet admitted, on conflicting records, or on a labeller unsure of its reading),
    ``metaphor`` or ``unreliable`` (the labeller read the words as figurative or deliberately false; the
    literal reading would not hold, which the table can see). ``assumed`` lists the assumed staging facts
    the answer rests on, as 041 names them.
    """

    claim_id: str
    category: str
    kind: str
    classification: str
    reason: str
    span: tuple[int, int]
    quote: str
    evidence: tuple[Evidence, ...]
    assumed: tuple[str, ...] = ()

    @property
    def material(self) -> bool:
        return self.classification == "contradiction"


@dataclass(frozen=True, slots=True)
class Mislabel:
    """An authorship claim that may be labelled wrong, routed to review rather than to 003's corrections.

    ``reason`` is ``speaks_for_pc`` (the claim is about someone else but carries a player character's own
    words or acts) or ``speech_in_outcome`` (an outcome carries a player character's words or acts). ``actor_id``
    is that player character: its controller may flag the claim (``flag_mislabel``).
    """

    campaign_id: str
    claim_id: str
    reason: str
    actor_id: str
    span: tuple[int, int]
    quote: str


@dataclass(frozen=True, slots=True)
class RefereeReport:
    """One review of one proposed narration, which it leaves exactly as it was.

    ``text_sha256`` names the narration reviewed. ``findings`` are sorted by position; ``clean`` lists the claims
    that checked against the facts with nothing to report, and ``unchecked`` the claims that could not be
    checked, with why (facts missing, an anchor that does not match the prose, a label that names a record
    the facts lack). ``authorship`` is 003's review of the labelled authorship claims, and ``mislabels`` the
    ones routed to review. Nothing in the report is a ruling, a repair or a block: what follows from a
    finding is milestone 2's.
    """

    text_sha256: str
    findings: tuple[Finding, ...]
    clean: tuple[str, ...]
    unchecked: tuple[tuple[str, str], ...]
    authorship: NarrationReview | None = None
    mislabels: tuple[Mislabel, ...] = ()

    @property
    def material(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.material)


__all__ = [
    "CATEGORIES", "CLASSIFICATIONS", "KINDS", "MAX_CLAIMS", "MAX_COUNT", "MAX_TEXT", "MODES", "SOURCES",
    "Authority", "ContinuityClaim", "Evidence", "Facts", "Finding", "LabelledClaim", "Mislabel", "RefereeError",
    "RefereeReport", "check_text",
]
