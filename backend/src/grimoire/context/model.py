"""Values of an audience-aware context pack (Sprint 031): lore entries, budgets and the pack itself.

A ``ContextPack`` is what trusted code may put in front of one actor for one turn: lines of text,
each with its provenance beside it, that fit a stated input-token budget. It is **disposable** and
never a canonical record. Nothing here reads files, calls a model or knows any file schema.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import InstanceRef, SourceRef, validate_segment

from .trigger import key_words

#: The provisional name of this in-memory shape; nothing persists it.
FORMAT = "grimoire.context-pack/0"
#: The lanes of a pack, in priority order: the actor's own portrayal, what it holds, setting lore.
LANES = ("portrayal", "memory", "lore")
#: Who may read a lore entry. ``common``: anyone in the setting may know it. ``director``: no actor.
#: There is no ``self`` audience for lore: a lorebook binds no actor. A card's own sections are
#: self-only by rule, not by label (see ``pack_actor_context``).
AUDIENCES = frozenset({"common", "director"})
#: Bounds, so a pack's cost is predictable whatever a reader hands back.
MAX_ENTRY_TEXT = 8000
MAX_KEYS = 64
MAX_KEY_TEXT = 100
MAX_ENTRIES = 4096
MAX_SCENE_TEXT = 100_000
MAX_PRESENT = 256
MAX_TOKENS = 1_000_000
MAX_OVERHEAD = 1000
MAX_ORDER = 1_000_000
#: control, invisible format (bidi overrides, tags, zero-width space ...), surrogate, separators
_UNSAFE = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
#: tab and newline, and the zero-width (non-)joiners that Persian and Indic text need
_ALLOWED = frozenset("\n\t\u200c\u200d")
_ASCII_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f]")


class ContextError(ValueError):
    """A request this slice refuses; ``reason`` is machine-readable and ``detail`` never holds text."""

    def __init__(self, message: str, reason: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.detail))


def _fail(message: str, reason: str, detail: str | None = None) -> ContextError:
    return ContextError(message, reason, detail)


def safe_text(value: object, limit: int) -> bool:
    """Nonblank text of at most ``limit`` characters with no control, invisible format, surrogate or
    separator character but tab, newline and the zero-width (non-)joiners."""
    if type(value) is not str or not value.strip() or len(value) > limit:
        return False
    if value.isascii():  # the only unsafe ASCII characters are controls
        return _ASCII_CONTROL.search(value) is None
    return not value.translate(_UNSAFE_ONLY)


class _UnsafeTable(dict[int, str | None]):
    """A ``str.translate`` table filled on demand that deletes every safe character, so a text is safe
    when nothing is left. Each code point is classified once, then looked up in C."""

    def __missing__(self, point: int) -> str | None:
        if len(self) >= 1 << 16:  # start over rather than grow for good on hostile text
            self.clear()
        char = chr(point)
        value = char if char not in _ALLOWED and unicodedata.category(char) in _UNSAFE else None
        self[point] = value
        return value


_UNSAFE_ONLY = _UnsafeTable()


@dataclass(frozen=True, slots=True)
class LoreEntry:
    """One retrievable piece of authored setting text, as a source's reader hands it over.

    ``keys`` trigger the entry when one occurs, as whole words, in the audience-visible scene text;
    a ``constant`` entry is a candidate without a trigger. ``audience`` defaults to ``director``: an
    entry nobody classified never reaches an actor. ``order`` is the author's ordering, lowest first.
    """

    entry_id: str
    text: str
    keys: tuple[str, ...] = ()
    constant: bool = False
    audience: str = "director"
    order: int = 0
    #: each key's normalized words, computed once here so every actor's lookup reuses them
    key_sequences: tuple[tuple[str, ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            validate_segment(self.entry_id, "entry_id")
        except ValueError as error:
            raise _fail(str(error), "invalid_entry") from None
        if not safe_text(self.text, MAX_ENTRY_TEXT):
            raise _fail(f"entry {self.entry_id}: text must be nonblank, safe and at most {MAX_ENTRY_TEXT} "
                        "characters", "invalid_entry")
        if isinstance(self.keys, (str, bytes)) or not isinstance(self.keys, Iterable):
            raise _fail(f"entry {self.entry_id}: keys must be a collection of text", "invalid_entry")
        keys = tuple(self.keys)
        if not all(type(key) is str for key in keys):
            raise _fail(f"entry {self.entry_id}: keys must be a collection of text", "invalid_entry")
        if len(keys) > MAX_KEYS or len(set(keys)) != len(keys):
            raise _fail(f"entry {self.entry_id}: at most {MAX_KEYS} distinct keys", "invalid_entry")
        sequences = tuple(key_words(key) if safe_text(key, MAX_KEY_TEXT) and "\n" not in key and "\t" not in key
                          else () for key in keys)
        if not all(sequences):
            raise _fail(f"entry {self.entry_id}: a key must be one safe line of at most {MAX_KEY_TEXT} characters "
                        "with at least one word", "invalid_entry")
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "key_sequences", sequences)
        if type(self.constant) is not bool:
            raise _fail(f"entry {self.entry_id}: constant must be a bool", "invalid_entry")
        if type(self.audience) is not str or self.audience not in AUDIENCES:
            raise _fail(f"entry {self.entry_id}: audience must be one of {sorted(AUDIENCES)}", "invalid_entry")
        if type(self.order) is not int or not -MAX_ORDER <= self.order <= MAX_ORDER:
            raise _fail(f"entry {self.entry_id}: order must be an int within ±{MAX_ORDER}", "invalid_entry")


#: A source's reader: its stored payload -> its lore entries. Registered by record schema.
EntryReader = Callable[[Mapping[str, object]], Iterable[LoreEntry]]
#: A route's token counter: text -> a non-negative int.
TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """A deterministic token **estimate**: UTF-8 bytes / 3, rounded up.

    Common BPE vocabularies average three to four bytes a token on English prose, so this usually
    over-counts there. It is not a bound for every text (digit runs, punctuation, CJK and emoji can
    cost more on a real tokenizer), which is why the pack records its ``measurement``; pass the
    route's own counter, with ``measurement="counted"``, when the budget is a hard limit.
    """
    return math.ceil(len(text.encode("utf-8", "surrogatepass")) / 3)


@dataclass(frozen=True, slots=True)
class Budget:
    """An input-token budget: a total, optional per-lane caps and a per-item framing allowance.

    ``item_overhead`` (at least 1) is added to every item's counted line, for the separator
    ``ContextPack.render`` puts between items and any label a renderer adds. A ``memory`` or ``lore``
    lane capped at 0 contributes nothing; the ``portrayal`` cap must hold at least the actor's name
    line, or packing raises ``budget_too_small``. A real tokenizer is not additive across a join;
    choose an overhead that covers that for the route.
    """

    max_tokens: int
    #: given as a mapping or pairs of lane to tokens; kept as ``(lane, cap)`` pairs in lane order
    lane_caps: Mapping[str, int] | tuple[tuple[str, int], ...] = ()
    item_overhead: int = 4

    def __post_init__(self) -> None:
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= MAX_TOKENS:
            raise _fail(f"max_tokens must be an int from 1 to {MAX_TOKENS}", "invalid_budget")
        if isinstance(self.lane_caps, Mapping):
            pairs = tuple(self.lane_caps.items())
        elif isinstance(self.lane_caps, (tuple, list)) and all(
                isinstance(p, (tuple, list)) and len(p) == 2 for p in self.lane_caps):
            pairs = tuple((p[0], p[1]) for p in self.lane_caps)
        else:
            raise _fail("lane_caps must be a mapping, or pairs, of lane to tokens", "invalid_budget")
        if not all(type(lane) is str for lane, _ in pairs):
            raise _fail(f"lanes are {LANES}", "invalid_budget")
        caps = dict(pairs)
        if len(caps) != len(pairs):
            raise _fail("a lane is capped twice", "invalid_budget")
        for lane, cap in caps.items():
            if lane not in LANES:
                raise _fail(f"unknown lane {lane!r}; lanes are {LANES}", "invalid_budget")
            if type(cap) is not int or not 0 <= cap <= self.max_tokens:
                raise _fail(f"the {lane} cap must be an int from 0 to max_tokens", "invalid_budget")
        object.__setattr__(self, "lane_caps", tuple(sorted(caps.items(), key=lambda item: LANES.index(item[0]))))
        if type(self.item_overhead) is not int or not 1 <= self.item_overhead <= MAX_OVERHEAD:
            # at least 1: the newline ``render`` puts between items must be paid for
            raise _fail(f"item_overhead must be an int from 1 to {MAX_OVERHEAD}", "invalid_budget")

    def cap(self, lane: str) -> int:
        """The lane's cap: its own, or the whole budget when none was given."""
        return dict(self.lane_caps).get(lane, self.max_tokens)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Origin:
    """Where an item came from. Structured data for the trusted caller; never rendered into a prompt.

    ``kind`` and the fields it uses:
    - ``identity``: the name line; ``source`` (the pinned leaf revision) and ``actor``; a campaign
      original has no source;
    - ``description``: ``source`` (the revision that authored it, or the leaf) and ``actor``;
    - ``section``: ``source`` (the revision that authored the composed text), ``actor`` and ``name``;
    - ``perception``: ``record`` (the evidence id);
    - ``proposition``: ``record`` (the proposition id); ``evidence``, the ids of the reports the actor
      received or sent about it plus the evidence its current belief cites; ``belief``, that belief's id;
    - ``lore``: ``source`` (the pinned binding revision) and ``name`` (the entry id).
    """

    kind: str
    source: SourceRef | None = None
    actor: InstanceRef | None = None
    name: str | None = None
    record: str | None = None
    evidence: tuple[str, ...] = ()
    belief: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimReading:
    """017's typed reading of a memory item, from ``claims_about`` for one focus node.

    ``node`` is the focus node asked about (the audience or a present actor), never the claim's
    other endpoint; ``position`` is ``subject``, ``object`` or ``either``.
    """

    predicate: str
    position: str
    node: InstanceRef


@dataclass(frozen=True, slots=True)
class PackedItem:
    """One line of a pack, what it cost, and where it came from."""

    lane: str
    line: str
    tokens: int
    origin: Origin
    #: memory items: the actor's current stance (``believed``, ``suspected``, ``rejected``) or ``None``
    stance: str | None = None
    #: memory items: the moment sequence the actor last perceived, heard, sent or concluded it
    sequence: int | None = None
    #: memory items: typed claim readings about focus nodes, when the caller passed 017 claim shapes
    claims: tuple[ClaimReading, ...] = ()
    #: lore items: the entry's audience (``common``; a director lore pack may also hold ``director``)
    audience: str | None = None


@dataclass(frozen=True, slots=True)
class Omission:
    """A candidate the budget shed: which, from which lane, why and what it would have cost.

    ``reason`` is ``lane_cap`` (it fit the pack's remainder but not its lane's) or ``over_budget``.
    """

    lane: str
    origin: Origin
    reason: str
    tokens: int


@dataclass(frozen=True, slots=True)
class SourceProblem:
    """A problem or notice about a source this pack read: one that contributed nothing (and why), or one
    that contributed but drifted. It describes the source as read, whole, so it is never a failure of
    the pack."""

    code: str
    source: SourceRef
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionBasis:
    """What a pack read from its snapshot's selection, so a brief can refuse one built over another.

    Two chats of one campaign can share an actor (107's ``reuse_actors``) and her card revision yet
    bind different lore, and one chat's lorebook can be detached or re-pinned between turns: the
    actor, the card and the moment alone cannot tell such packs apart, and a hand edit of a file in
    place leaves even the references equal. ``bindings`` are the sorted ``(role, selected ref,
    resolved ref or None, contents)`` of every ``lorebook`` and ``scenario`` binding and, for an
    actor's pack, of the actor's own bindings; ``contents`` are the content identities (stored etag,
    else a digest) of each revision read through the binding: a lore source's selected revision, or
    every layer of the actor's composed portrayal. ``instance_etag`` is the captured actor
    instance's etag. ``selection_basis`` computes it; two packs read the same inputs when their bases
    are equal.
    """

    campaign_id: str
    #: the audience whose own bindings were read; ``None`` for the director's lore
    actor: InstanceRef | None
    bindings: tuple[tuple[str, SourceRef, SourceRef | None, tuple[str, ...]], ...]
    instance_etag: str | None = None


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A bounded, audience-safe context for one actor at one moment.

    ``items`` are in lane order, then rank order. ``tokens`` is their total cost, overhead included,
    and never exceeds ``budget.max_tokens``. ``untriggered`` counts, per bound source, the common
    entries that are not constant and whose keys occur neither in the scene text nor in the actor's
    held memory: listed by count, so a large catalog is never dumped.
    What audience rules withheld is deliberately not reported here: that is the director's.
    ``notes`` are pack-level facts the trusted caller should see, such as ``knowledge_quarantined``.
    ``basis`` is the campaign and the bindings it read (``SelectionBasis``). ``knowledge`` is the
    digest of the actor's 012 projection it read (``knowledge_digest``): a ledger reload that
    quarantines a record shrinks the projection at the same ``as_of`` and binding, so only this tells
    a pack still holding the lost record from a current one.
    It is a provisional in-memory value (``FORMAT``), not a persisted schema.
    """

    actor: InstanceRef
    as_of: int
    budget: Budget
    measurement: str
    items: tuple[PackedItem, ...]
    omitted: tuple[Omission, ...]
    untriggered: tuple[tuple[SourceRef, int], ...]
    problems: tuple[SourceProblem, ...]
    basis: SelectionBasis
    knowledge: str
    notes: tuple[str, ...] = ()

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.items)

    def lane(self, lane: str) -> tuple[PackedItem, ...]:
        if lane not in LANES:
            raise _fail(f"unknown lane {lane!r}", "invalid_lane")
        return tuple(item for item in self.items if item.lane == lane)

    def lines(self) -> tuple[str, ...]:
        return tuple(item.line for item in self.items)

    def render(self) -> str:
        """The items' lines, one per line, in order. The newline is part of each item's overhead.

        An item's line never holds a line break (the packer joins a multi-line text into one line), so
        a source's text cannot forge another item's line."""
        return "\n".join(self.lines())


@dataclass(frozen=True, slots=True)
class LorePack:
    """Bounded lore for the scene-level director reply: no actor, so no portrayal or memory.

    Every item is in the ``lore`` lane and carries its entry's ``audience``: ``common`` lore is what
    anyone in the setting may know, ``director`` lore is the director's alone and must never be
    stated as table fact. ``omitted``, ``untriggered`` and ``problems`` read as in ``ContextPack``;
    nothing here belongs to an actor, so nothing is withheld unreported. ``basis`` is the campaign
    and the lore bindings it read (``SelectionBasis``, with no actor). ``table_text`` is the
    table-visible text its entries were triggered on: the table saw it, so a brief counts it as
    exposed. It is a provisional in-memory value, never persisted.
    """

    budget: Budget
    measurement: str
    items: tuple[PackedItem, ...]
    omitted: tuple[Omission, ...]
    untriggered: tuple[tuple[SourceRef, int], ...]
    problems: tuple[SourceProblem, ...]
    basis: SelectionBasis
    table_text: str = ""

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.items)

    def audience(self, audience: str) -> tuple[PackedItem, ...]:
        if audience not in AUDIENCES:
            raise _fail(f"unknown audience {audience!r}", "invalid_audience")
        return tuple(item for item in self.items if item.audience == audience)

    def lines(self) -> tuple[str, ...]:
        return tuple(item.line for item in self.items)

    def render(self) -> str:
        """The items' lines, one per line, in order (see ``ContextPack.render``)."""
        return "\n".join(self.lines())


__all__ = [
    "AUDIENCES", "FORMAT", "LANES", "MAX_ENTRIES", "MAX_ENTRY_TEXT", "MAX_KEYS", "MAX_KEY_TEXT", "MAX_PRESENT",
    "MAX_SCENE_TEXT", "MAX_TOKENS", "Budget", "ClaimReading", "ContextError", "ContextPack", "EntryReader", "LoreEntry",
    "LorePack", "Omission", "Origin", "PackedItem", "SelectionBasis", "SourceProblem", "TokenCounter",
    "estimate_tokens", "safe_text",
]
