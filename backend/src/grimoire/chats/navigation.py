"""Derived chat navigation: one canonical chat, many views (Sprint 107).

The index is a pure function of canonical scope and chat records. It holds only IDs, refs,
labels, titles, timestamps and problem codes: never premise text, extras, session-zero data or
transcript content. There is no persisted cache in this slice. Any later cache must equal
``build`` over the same records (compare ``to_json``), so deleting one changes no answer.

Every query returns ``ChatRef``s, each chat at most once. Matching is by qualified entity
(``SourceRef.entity_key``), never by name. Definition links (``alternate_portrayal``,
``derived_variant``, ``same_individual``) are never followed: equivalence filters belong to
022/065.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from grimoire.core.identity import ChatRef, ScopeRef, SourceRef

from .model import ChatRecord, PlayScope

UNDECODABLE = "undecodable"  # the chat file exists but this version cannot read its payload
ORPHAN_CHAT = "orphan_chat"  # the chat's play scope record is missing or unreadable
MAX_RECENT = 1000

EntityKey = tuple[str, str, str]
#: (role, entity key, revision the reference meant, active)
Mention = tuple[str, EntityKey, str | None, bool]


@dataclass(frozen=True, slots=True)
class NavEntry:
    """What navigation knows about one chat file."""

    ref: ChatRef
    label: str | None = None
    last_activity: str | None = None
    mentions: tuple[Mention, ...] = ()
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeEntry:
    ref: ScopeRef
    title: str | None = None
    world: str | None = None


def nav_entry(ref: ChatRef, record: ChatRecord | None) -> NavEntry:
    """The navigation facts of one chat; ``None`` marks a chat this version cannot decode."""
    if record is None:
        return NavEntry(ref, problem=UNDECODABLE)
    mentions: list[Mention] = []
    launch = record.launch
    if launch.ref is not None:
        start = next(b for b in record.bindings if b.introduced_seq == 0 and b.ref == launch.ref)
        mentions.append((launch.kind, launch.ref.entity_key, start.resolved_revision, True))  # provenance
    for binding in record.bindings:
        mentions.append((binding.role, binding.ref.entity_key, binding.resolved_revision, binding.active))
    bound = {binding.actor for binding in record.bindings if binding.actor is not None}
    for entry in record.roster:  # an actor's binding already says whether it is current and what it meant
        if entry.source is not None and entry.actor not in bound:
            mentions.append((entry.role, entry.source.entity_key, entry.source.revision, True))
    return NavEntry(ref, record.label, record.last_activity, tuple(sorted(set(mentions), key=repr)))


def scope_entry(scope: PlayScope) -> ScopeEntry:
    return ScopeEntry(scope.ref, scope.title, scope.world.source if scope.world is not None else None)


class NavigationIndex:
    """Inverse lookups over chats; build it from canonical records, never edit it."""

    def __init__(self, chats: tuple[NavEntry, ...], scopes: tuple[ScopeEntry, ...]) -> None:
        self._chats = chats
        self._scopes = {scope.ref.campaign_id: scope for scope in scopes}
        self._order = tuple(entry.ref for entry in sorted(
            chats, key=lambda entry: (entry.last_activity is None, _descending(entry.last_activity or ""),
                                      str(entry.ref))))
        self._by_ref = {entry.ref: entry for entry in chats}

    @classmethod
    def build(cls, chats: Iterable[NavEntry], scopes: Iterable[ScopeEntry] = ()) -> NavigationIndex:
        chat_list, scope_list = list(chats), list(scopes)
        if len({entry.ref for entry in chat_list}) != len(chat_list):
            raise ValueError("a chat appears twice; the index is over canonical records")
        if len({scope.ref for scope in scope_list}) != len(scope_list):
            raise ValueError("a scope appears twice")
        known = {scope.ref.campaign_id for scope in scope_list}
        flagged = tuple(sorted(
            (entry if entry.problem is not None or entry.ref.campaign_id in known else
             NavEntry(entry.ref, entry.label, entry.last_activity, entry.mentions, ORPHAN_CHAT)
             for entry in chat_list), key=lambda entry: str(entry.ref)))
        return cls(flagged, tuple(sorted(scope_list, key=lambda scope: str(scope.ref))))

    # -- queries ---------------------------------------------------------------------------------

    def entry(self, ref: ChatRef) -> NavEntry:
        return self._by_ref[ref]

    def problems(self) -> tuple[tuple[ChatRef, str], ...]:
        return tuple((entry.ref, entry.problem) for entry in self._chats if entry.problem is not None)

    def all_chats(self) -> tuple[ChatRef, ...]:
        """Every chat file, newest activity first; undecodable and orphan chats included."""
        return self._order

    def recent(self, limit: int = 50) -> tuple[ChatRef, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_RECENT:
            raise ValueError(f"limit must be 1..{MAX_RECENT}")
        return self._order[:limit]

    def by_source(self, ref: SourceRef, *, role: str | None = None,
                  include_detached: bool = False) -> tuple[ChatRef, ...]:
        """Chats whose launch, a binding or a roster actor is this qualified entity.

        A pinned ``ref`` also requires that revision. Detached bindings count only on request.
        The launch always counts: it is immutable provenance.
        """
        def matches(mention: Mention) -> bool:
            mention_role, key, revision, active = mention
            return (key == ref.entity_key and (role is None or mention_role == role)
                    and (not ref.pinned or revision == ref.revision) and (active or include_detached))

        return tuple(chat for chat in self._order if any(matches(m) for m in self._by_ref[chat].mentions))

    def by_scope(self, campaign_id: str) -> tuple[ChatRef, ...]:
        return tuple(chat for chat in self._order if chat.campaign_id == campaign_id)

    def campaigns(self) -> tuple[ScopeRef, ...]:
        """Scopes the player named as campaigns; an unnamed quick scope is shown as its chat."""
        return tuple(scope.ref for scope in self._scopes.values() if scope.title is not None)

    def scope(self, campaign_id: str) -> ScopeEntry | None:
        return self._scopes.get(campaign_id)

    def by_world(self, source_id: str) -> tuple[ChatRef, ...]:
        worlds = {cid for cid, scope in self._scopes.items() if scope.world == source_id}
        return tuple(chat for chat in self._order if chat.campaign_id in worlds)

    # -- equality and serialization ----------------------------------------------------------------

    def to_json(self) -> dict:
        """A deterministic, JSON-compatible form: what any persisted cache must equal."""
        return {
            "chats": [{"ref": str(e.ref), "label": e.label, "last_activity": e.last_activity,
                       "problem": e.problem,
                       "mentions": [[role, list(key), revision, active] for role, key, revision, active in e.mentions]}
                      for e in self._chats],
            "scopes": [{"ref": str(s.ref), "title": s.title, "world": s.world} for s in self._scopes.values()],
        }

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NavigationIndex) and self.to_json() == other.to_json()

    __hash__ = None  # type: ignore[assignment]


def _descending(text: str) -> tuple[int, ...]:
    """Sort key that orders timestamps newest first (they compare as text)."""
    return tuple(-ord(char) for char in text)
