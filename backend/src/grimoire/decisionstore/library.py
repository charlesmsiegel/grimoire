"""Store-backed NPC decisions (Sprint 045): one recorded decision per NPC per scene turn.

``DecisionLibrary`` records into one authority ledger per campaign, ``decisions/<campaign_id>``, through
the library's ``DurableAuthority``: append-only, hash-chained, written only by the authority's
capability, and replayed and re-checked on load (anything inconsistent is ``LedgerInvalid``). Its
handle is a trusted handle the authority issued: the director's channel, as 029's and 040's are.

A turn's decision is written once. Recording the same decision again returns it and writes nothing;
recording another for the same NPC and turn is refused (``already_decided``): a retried narration
replays the recorded one (``grimoire.decisions.replay``), and only a new turn decides anew. Nothing
here decides anything, and a recorded decision changes no world state: it is what the NPC chose, as
evidence for later review (047) and for the narration that carries it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.core.authority import AuthorityError
from grimoire.core.identity import InstanceRef, TurnRef, validate_segment
from grimoire.decisions import DecisionError, DecisionRecord, decision_from_payload, decision_to_payload
from grimoire.ledgers import DurableAuthority
from grimoire.ledgers.codec import envelope, exact
from grimoire.security import is_valid_principal
from grimoire.store import LibraryStore
from grimoire.store.errors import InvalidEnvelope, LedgerInvalid, TakenIdsUnlisted
from grimoire.store.ledger import Head

_KIND = "decision"


def ledger_for(campaign_id: str) -> str:
    """The campaign's decision ledger name; an invalid campaign id is ``invalid_field``."""
    if type(campaign_id) is not str:
        raise DecisionError("invalid_field", "campaign_id must be plain text")
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError as error:
        raise DecisionError("invalid_field", str(error)) from None
    return f"decisions/{campaign_id}"


@dataclass(frozen=True)
class NpcDecisionEntry:
    """One recorded decision and who recorded it."""

    record: DecisionRecord
    recorded_by: str


@dataclass
class _Builder:
    campaign_id: str
    entries: dict[tuple[str, InstanceRef], NpcDecisionEntry] = field(default_factory=dict)
    head: Head | None = None

    def check(self, record: DecisionRecord) -> NpcDecisionEntry | None:
        """The entry already holding this exact decision, ``None`` for a new one, or a refusal."""
        if record.turn.campaign_id != self.campaign_id:
            raise DecisionError("campaign_mismatch", "the decision belongs to another campaign")
        existing = self.entries.get((record.turn.turn_id, record.actor))
        if existing is None:
            return None
        if existing.record == record:
            return existing
        raise DecisionError("already_decided", f"{record.actor} already decided {record.turn}")

    def add(self, entry: NpcDecisionEntry) -> None:
        self.entries[(entry.record.turn.turn_id, entry.record.actor)] = entry


def decision_actor_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every actor instance id this campaign's decision ledger names (the deciders and evidence actors).

    Part of ``grimoire.chatstore.campaign_taken_ids``: a newcomer given a hand-deleted NPC's id would
    otherwise inherit its recorded decisions. Scanned raw, with no replay, so a broken ledger still
    protects its ids. A ledger that cannot all be read raises ``TakenIdsUnlisted``.
    """
    documents, unread = store.ledger_documents(ledger_for(campaign_id))
    if unread:
        raise TakenIdsUnlisted(f"the decision ledger of {campaign_id} cannot all be read ({', '.join(unread)}), "
                               "so the actor ids it names cannot be reserved", unread)
    taken: set[str] = set()
    stack: list[object] = list(documents)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif isinstance(value, str) and "campaign:" in value and "/" in value:
            # A reference, or a condition that names one (``present:<ref>``).
            value = value[value.index("campaign:"):]
            try:
                ref = InstanceRef.parse(value)
            except ValueError:
                continue
            if ref.campaign_id == campaign_id:
                taken.add(ref.instance_id)
    return frozenset(taken)


class DecisionLibrary:
    """Records NPC decisions through the library's durable authority; see the module docstring."""

    def __init__(self, store: LibraryStore, authority: DurableAuthority, handle: object) -> None:
        if type(authority) is not DurableAuthority:
            raise TypeError("expected the library's DurableAuthority")
        if not (authority.issued(handle) and getattr(handle, "trusted", False) is True):
            raise AuthorityError("the decision key must be a trusted handle issued by this library's authority")
        self.store = store
        self._authority = authority
        self._principal: str = handle.principal  # type: ignore[attr-defined]

    def get(self, turn: TurnRef, actor: InstanceRef) -> NpcDecisionEntry | None:
        """The decision ``actor`` recorded for ``turn``, or ``None``."""
        if not isinstance(turn, TurnRef) or not isinstance(actor, InstanceRef):
            raise DecisionError("invalid_field", "get needs a TurnRef and an InstanceRef")
        with self._authority.lock:
            return self._builder(turn.campaign_id).entries.get((turn.turn_id, actor))

    def decisions(self, turn: TurnRef) -> tuple[NpcDecisionEntry, ...]:
        """Every decision recorded for ``turn``, in recording order."""
        if not isinstance(turn, TurnRef):
            raise DecisionError("invalid_field", "decisions needs a TurnRef")
        with self._authority.lock:
            return tuple(entry for (turn_id, _actor), entry in self._builder(turn.campaign_id).entries.items()
                         if turn_id == turn.turn_id)

    def record(self, record: DecisionRecord) -> NpcDecisionEntry:
        """Record ``record``; the same decision again returns it and writes nothing.

        Refused: ``already_decided`` (another decision for this NPC and turn), ``missing_campaign`` and
        ``unknown_actor`` (the decider or an evidence actor that is not an instance of the campaign).
        """
        if type(record) is not DecisionRecord:
            raise TypeError("expected a DecisionRecord")
        campaign_id = record.turn.campaign_id
        payload = decision_to_payload(record)
        if decision_from_payload(payload) != record:  # what is written must read back as this very record
            raise DecisionError("bad_record", "the decision does not read back as itself")
        with self._authority.lock:
            builder = self._builder(campaign_id)
            existing = builder.check(record)
            if existing is not None:
                return existing
            if self.store.campaign_presence(campaign_id) != "present":
                raise DecisionError("missing_campaign", f"no campaign {campaign_id!r}")
            instances = set(self.store.campaign_instances(campaign_id))
            named = {record.actor} | {item.actor for item in record.evidence if item.actor is not None}
            unknown = sorted(str(actor) for actor in named if actor not in instances)
            if unknown:
                raise DecisionError("unknown_actor", f"actors that are not instances of the campaign: "
                                    f"{', '.join(unknown)}")
            entry = NpcDecisionEntry(record, self._principal)
            self._append(builder, payload)
            builder.add(entry)
            return entry

    def _append(self, builder: _Builder, payload: dict[str, object]) -> None:
        name = ledger_for(builder.campaign_id)
        entry = envelope(_KIND, self._authority.now(), recorded_by=self._principal, value=payload)
        try:
            builder.head = self._authority._append(name, [entry], builder.head)
        except InvalidEnvelope as error:  # text the store cannot hold; nothing was written
            raise DecisionError("invalid_field", str(error)) from None
        except ValueError as error:  # no batch could hold it; nothing was written
            raise DecisionError("too_large", str(error)) from None

    def _builder(self, campaign_id: str) -> _Builder:
        name = ledger_for(campaign_id)
        views = self._authority.views
        if name not in views:
            ledger = self._authority.read_ledger(name)
            views[name] = _replay(campaign_id, name, ledger.entries, ledger.head)
        return views[name]  # type: ignore[return-value]


def _replay(campaign_id: str, name: str, entries: Iterable[object], head: Head | None) -> _Builder:
    """Re-check every entry with the record-time rules; else ``LedgerInvalid``."""
    builder = _Builder(campaign_id)
    for index, entry in enumerate(entries, 1):
        kind = entry.get("kind") if isinstance(entry, Mapping) else None
        if kind != _KIND:
            raise LedgerInvalid(f"{name} entry {index}: unknown kind {kind!r}")
        data = exact(entry, kind, {"recorded_by", "value"})  # type: ignore[arg-type]
        recorded_by = data["recorded_by"]
        if not is_valid_principal(recorded_by):
            raise LedgerInvalid(f"{name} entry {index}: recorded_by is not a principal")
        try:
            record = decision_from_payload(data["value"])
            if builder.check(record) is not None:
                raise DecisionError("already_decided", "the same decision recorded twice")
        except DecisionError as error:
            raise LedgerInvalid(f"{name} entry {index}: {error} ({error.reason})") from None
        builder.add(NpcDecisionEntry(record, recorded_by))  # type: ignore[arg-type]
    builder.head = head
    return builder
