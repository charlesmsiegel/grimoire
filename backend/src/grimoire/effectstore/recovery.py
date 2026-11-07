"""Finishing a partly published acceptance, exactly once (Sprint 030, first slice).

An acceptance is one store transaction, so a crash alone never splits it. A step-11 publish race, staging
that vanished after the commit point, or a sync or restore that delivered only some files can still leave
it partly written. 021 reports the two shapes and refuses a retry; this service finishes them:

- ``complete(ref)``: the event landed without some of its changes (shape A). It writes exactly the changes
  its receipts name that never landed, as the acceptance would have written them, so the receipts verify.
- ``adopt(proposal)``: changes landed without their event file (shape B). The retry of that acceptance,
  checked exactly like ``accept``, writes the event under the orphan's id plus whatever is still missing.

Aborting needs no new write path: ``revoke(partial=True)`` for shape A, ``adopt`` then ``revoke`` for B.
Both operations return 021's ``EffectChange`` (preview, then commit, one journal-protected transaction) and
need only the outcome key: they publish what was already accepted. See docs/RECOVERY.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from grimoire.core.authority import DIRECTOR, PlayerContribution
from grimoire.core.identity import AcceptedRef, InstanceRef, KnowledgeRef, RelationRef
from grimoire.core.model import ControlAssignment
from grimoire.effects import (
    AcceptedEvent, AcceptedHoldings, EffectError, EffectProposal, Receipt, accepted_schema, accepted_to_payload,
    accepted_token, holdings_op_id, holdings_orphans, parse_token, plan, receipt_failure, verify_receipts,
)
from grimoire.effects.plan import candidates
from grimoire.graph import Relation
from grimoire.graphstore import InvalidRelation, RelationChange
from grimoire.holdings import Holdings, HoldingsCodecError, HoldingsRefused
from grimoire.holdingstore import HoldingsChange
from grimoire.store import ScopeRecord, StaleWrite
from grimoire.store.layout import address_parts

from . import parts
from .errors import InvalidEffect
from .errors import refused as _refused
from .library import Endorsement, EffectChange, TurnEffectsLibrary, _relations_unreadable

#: 023 refusals meaning a holder no longer matches the receipt: it is not this operation's to finish
_NOT_OURS = frozenset({"seq_moved", "op_id_reused"})


class EffectRecovery:
    """Completion and adoption of partly published acceptances, over one ``TurnEffectsLibrary``."""

    def __init__(self, library: TurnEffectsLibrary) -> None:
        if type(library) is not TurnEffectsLibrary:
            raise TypeError("EffectRecovery takes a TurnEffectsLibrary")
        self.library = library

    # -- shape A: the event without (all of) its changes ---------------------------------------------

    def complete(self, ref: AcceptedRef, *, expected: str) -> EffectChange:
        """Write the changes the active event ``ref``'s receipts name that never landed. A no-op when they verify.

        ``ref`` must be the latest active event (``not_latest``), and its file still have etag ``expected``.
        A receipt is completed only where its change is plainly unpublished: a ``begin`` whose receipted
        relation file does not exist, an ``end`` whose relation is unended (and was never ended by this event
        and reopened), a holder still exactly one seq before its receipted seq. Anything else is
        ``not_completable``: something else changed it since, and finishing would not be what was committed.
        """
        lib = self.library
        if type(ref) is not AcceptedRef:
            raise InvalidEffect("complete takes an AcceptedRef", "invalid_field")
        campaign = ref.campaign_id
        with lib.store.lock:
            snap = lib._snapshot(campaign)
            lib._require_trustworthy(snap)
            if ref not in snap.events:
                lib.store.read(ref)  # raises the store's own error (RecordNotFound ...)
                raise InvalidEffect(f"{ref} cannot be read", "undecodable_event")
            event, etag = snap.events[ref], snap.etags[ref]
            if not event.active:
                raise InvalidEffect(f"{ref} is revoked", "already_revoked")
            if etag != expected:
                raise StaleWrite([("/".join(address_parts(ref)), "changed")])
            index = snap.graph.index
            failing = [problem.op for problem in verify_receipts(event, index)]
            holdings_why = parts.duplicate_holdings(event, lib._holdings())  # damage refuses here
            if not failing and holdings_why is None:  # already complete: exactly what a retry finds, even later
                return EffectChange(lib, ref, campaign_id=campaign, state=snap.state, duplicate_of=ref)
            later = [e.event_id for e in snap.active() if e.ordinal > event.ordinal]
            if later:  # a later event's play may contradict what finishing this one would write (sequence floor)
                raise InvalidEffect(f"{ref} is not the latest accepted event", "not_latest", sorted(later))
            if _relations_unreadable(snap.graph):
                raise InvalidEffect("a relation file cannot be read, so the completion cannot be checked",
                                    "undecodable_relation")
            tokens = _tokens(lib, campaign, event.event_id)
            if any("revoked" in found for found in tokens.values()):
                raise InvalidEffect(f"a revocation of {ref} was partly published; completing it would undo that",
                                    "not_completable", ("revocation",))
            self._refuse_touched_since(event, failing, index)
            changes = [self._complete_operation(event, number, snap) for number in failing]
            holdings_change, landed = None, ()
            if holdings_why is not None:
                holdings_change, landed = self._complete_holdings(event)
            elif event.holdings is not None:  # verified as read: the commit re-checks it, like a duplicate
                landed = tuple(sorted(event.holdings.seqs, key=str))
            agents, actors = _acting(lib, event)
            gone = sorted(str(agent) for agent, etag in agents.items() if etag is None)
            if gone:  # accept refuses an act whose agent has no instance file; so does finishing one
                raise InvalidEffect(f"{', '.join(gone)} has no instance file", "missing_agent", gone)
            for number, item in enumerate(event.operations):  # what a verified begin rests on, pinned
                if number not in failing and item.op.kind == "begin":
                    agents.update({end: lib._etag(end) for end in (item.op.subject, item.op.object)})
            verified = {event.receipts[number].relation for number in range(len(event.operations))} - {
                event.receipts[number].relation for number in failing}
            return EffectChange(
                lib, ref, (), tuple(changes), campaign, snap.state, rechecks_readability=True, agent_etags=agents,
                actors=actors, holdings_changes=() if holdings_change is None else (holdings_change,),
                holder_pins={holder: lib._holder_pin(holder) for holder in landed},
                relation_pins={relation: snap.graph.etags[relation] for relation in sorted(verified, key=str)},
                rests_on=_unchanged_tokens(lib, campaign, event.event_id, tokens))

    def _refuse_touched_since(self, event: AcceptedEvent, failing: list[int], index) -> None:  # type: ignore[no-untyped-def]
        """Finishing an event a human has since repaired by hand would double the repair: refuse when a holder
        is past this event's receipted seq (logged anything after its entry, evicted or not), or a receipted relation this event began was ended."""
        accepted = event.holdings
        if accepted is not None:
            held = parts.read_holders(self.library._holdings(), tuple(accepted.seqs))
            moved = sorted(str(holder) for holder, value in held.items()  # by the receipt: an evicted entry too
                           if isinstance(value, Holdings) and value.seq > accepted.seqs[holder])
            if moved:
                raise InvalidEffect(f"{', '.join(moved)} changed after logging this event's holdings change",
                                    "not_completable", ("holdings",))
        for number, item in enumerate(event.operations):
            if number in failing or item.op.kind != "begin":
                continue
            if not {item.op.subject, item.op.object} <= index.instances:  # removed by hand since: an undo too
                raise InvalidEffect(f"an endpoint of operation {number} has no instance file", "missing_endpoint",
                                    (str(number),))
            relation = index.relation(event.receipts[number].relation.relation_id, requested_by=DIRECTOR).relation
            if relation.ending is not None:
                raise InvalidEffect(f"the relation operation {number} began was ended since", "not_completable",
                                    (str(number),))

    def _complete_operation(self, event: AcceptedEvent, number: int, snap) -> RelationChange:  # type: ignore[no-untyped-def]
        lib, item = self.library, event.operations[number]
        op, receipted = item.op, event.receipts[number].relation
        why = receipt_failure(event, number, snap.graph.index)
        token = accepted_token(event.event_id)
        relations = lib._relations()
        try:
            if op.kind == "begin" and why == "missing" and lib._absent(receipted):
                return relations.add(event.ref.campaign_id, op.predicate, op.subject, op.object, since=event.moment,
                                     provenance=token, relations=snap.relations(),
                                     relation_id=receipted.relation_id)
            if op.kind == "end" and why == "not ended by this event":
                relation = snap.graph.index.relation(receipted.relation_id, requested_by=DIRECTOR).relation
                if (relation.ending is None and relation.since.sequence < event.moment.sequence
                        and not any(r.ending.provenance == token for r in relation.reopenings)):
                    return relations.end(receipted, until=event.moment, provenance=token,
                                         expected=snap.graph.etags[receipted])
        except InvalidRelation as error:
            reason = {"duplicate_relation": "precondition_failed", "relation_id_taken": "not_completable"}.get(
                error.reason, error.reason)
            raise InvalidEffect(f"operation {number} cannot be completed: {error.reason}", reason,
                                (str(number),)) from None
        raise InvalidEffect(f"operation {number} of {event.ref} cannot be completed: {why}", "not_completable",
                            (str(number),))

    def _complete_holdings(self, event: AcceptedEvent) -> tuple[HoldingsChange | None, tuple[InstanceRef, ...]]:
        """The holder files the event's holdings receipt names that never got their entry, and the landed ones."""
        accepted = event.holdings
        assert accepted is not None
        op = accepted.holdings.operation(accepted.op_id, accepted_token(event.event_id))
        change = _complete_on(self.library, op, dict(accepted.seqs))
        if not change.puts:  # every holder logs it, yet the receipt failed: not a missing write
            raise InvalidEffect(f"the holdings of {event.ref} cannot be completed", "not_completable", ("holdings",))
        written = {envelope.address.holder for envelope, _ in change.puts}  # type: ignore[union-attr]
        return change, tuple(sorted(set(accepted.seqs) - written, key=str))

    # -- shape B: changes without their event --------------------------------------------------------

    def adopt(self, proposal: EffectProposal, *, controls: Iterable[ControlAssignment],
              contributions: Iterable[PlayerContribution] = (),
              endorsements: Iterable[Endorsement] = ()) -> EffectChange:
        """Retry an acceptance whose changes landed without its event file: checked exactly like ``accept``.

        The landed changes name the orphaned event id X (``effect:accepted/X``); exactly one X must be named
        (``nothing_to_adopt``, ``ambiguous_orphan``), and every 021 token naming X anywhere in the campaign must
        be one of these landed changes (``orphan_effect`` otherwise: X has another history). The event file is
        written under X with receipts naming the landed changes, plus whichever changes are still missing.
        """
        lib = self.library
        if type(proposal) is not EffectProposal:
            raise InvalidEffect("adopt takes an EffectProposal", "invalid_field")
        campaign = proposal.campaign_id
        controls, endorsements = tuple(controls), tuple(endorsements)
        with lib.store.lock:
            lib._require_campaign(campaign)
            if not lib._chat_exists(proposal.chat):
                raise InvalidEffect(f"{proposal.chat} does not exist", "missing_chat")
            snap = lib._snapshot(campaign)
            lib._require_trustworthy(snap)
            duplicate = lib._duplicate(proposal, snap)  # the adoption already landed: exactly accept's no-op
            if duplicate is not None:
                return duplicate
            if _relations_unreadable(snap.graph):
                raise InvalidEffect("a relation file cannot be read, so the orphan cannot be found",
                                    "undecodable_relation")
            _, held_unread = parts.holder_tokens(lib.store, campaign)
            if held_unread:
                raise InvalidEffect("a holder file cannot be read, so the orphan cannot be found",
                                    "undecodable_holdings")
            landed = _landed_relations(proposal, snap.graph.index, {e.event_id for e in snap.events.values()})
            entries, holders = self._landed_entries(proposal, {e.event_id for e in snap.events.values()})
            named = {x for _, x in landed.values()} | {x for _, x in entries.values()}
            if not named:
                raise InvalidEffect("no change of this proposal landed without its event", "nothing_to_adopt")
            if len(named) > 1:
                raise InvalidEffect("the landed changes name more than one event", "ambiguous_orphan", sorted(named))
            orphan = named.pop()
            ref, tokens = self._orphan_ref(proposal, orphan, landed, entries)
            authorization, records, pins = lib._admit(proposal, snap, controls, contributions, endorsements)
            try:
                steps = plan(proposal, snap.graph.index, landed=frozenset(landed))
            except EffectError as error:
                raise _refused(error) from None
            tag = lib._device_tag()  # what adopt still begins is this device's, as accept's would be (028)
            changes = lib._relation_changes(proposal, steps, ref, snap, tag)
            receipted: dict[int, RelationRef] = {number: relation.ref for number, (relation, _) in landed.items()}
            receipted.update({step.op: change.relation for step, change in zip(steps, changes)})
            receipts = tuple(Receipt(number, receipted[number]) for number in range(len(proposal.operations)))
            holdings_change, accepted, written = self._adopt_holdings(proposal, snap, orphan, landed, entries,
                                                                      holders, authorization.holdings_domains)
            ordinal = 1 + max((e.ordinal for e in snap.events.values()), default=0)
            event = lib._event(ref, proposal, authorization, records, receipts, accepted, ordinal)
            for number in landed:  # the very check continuity makes, on the changes this event now claims
                why = receipt_failure(event, number, snap.graph.index)
                if why is not None:
                    raise InvalidEffect(f"the landed change of operation {number} does not match: {why}",
                                        "orphan_effect", (orphan,))
            record = ScopeRecord(ref, accepted_schema(event), accepted_to_payload(event), owner=lib._owner)
            agents, actors = lib._acting(proposal)
            for number, (relation, _) in landed.items():
                if proposal.operations[number].kind == "begin":
                    if relation.ending is not None:  # ended by hand since: adopting would not be the commit
                        raise InvalidEffect(f"the landed relation of operation {number} was ended since",
                                            "not_completable", (str(number),))
                    agents.update({end: lib._etag(end) for end in (relation.subject, relation.object)})
            pins.update({holder: lib._holder_pin(holder) for holder in sorted(entries.keys() - written, key=str)})
            return EffectChange(
                lib, ref, ((record, None),), tuple(changes), campaign, snap.state, proposal.chat,
                checks_relations=True, agent_etags=agents, actors=actors,
                holdings_changes=() if holdings_change is None else (holdings_change,), holder_pins=pins,
                relation_pins={relation.ref: snap.graph.etags[relation.ref]
                               for relation, _ in sorted(landed.values(), key=lambda pair: str(pair[0].ref))},
                rests_on=_unchanged_tokens(lib, campaign, orphan, tokens), device=tag)

    def _landed_entries(self, proposal: EffectProposal, recorded: set[str]) -> tuple[dict, dict]:
        """Each touched holder logging this effect's derived op id under an X with no event file:
        ``holder -> (entry, X)``, and every touched holder's holdings. An entry with that id but other content or
        a free-text cause is a human repair (``orphan_effect``); one an event records is that event's own."""
        part = proposal.holdings
        if part is None:
            return {}, {}
        holders = parts.read_holders(self.library._holdings(), part.holders)
        parts.refuse_unreadable((holder, why, True) for holder, why in holders.items() if isinstance(why, str))
        op_id = holdings_op_id(proposal.campaign_id, proposal.fingerprint)
        found = {}
        for holder, held in holders.items():
            entry = held.entry(op_id)  # type: ignore[union-attr]
            if entry is None:
                continue
            parsed = parse_token(entry.cause)
            if parsed is not None and parsed[0] == "accepted" and parsed[1] in recorded:
                continue  # that event's own change (a revoked one's forward entry): never an orphan to adopt
            if parsed is None or parsed[0] != "accepted" or not entry.same_operation(
                    part.operation(op_id, entry.cause)):
                raise InvalidEffect(f"{holder} logs this effect's holdings change with other content or cause",
                                    "orphan_effect", (str(holder),))
            found[holder] = (entry, parsed[1])
        return found, holders  # type: ignore[return-value]

    def _orphan_ref(self, proposal: EffectProposal, orphan: str, landed: Mapping, entries: Mapping
                    ) -> tuple[AcceptedRef, dict]:
        """X as an event id that is free and has no other history than these landed changes, and every 021
        token naming X as read (the commit re-checks them)."""
        lib, campaign = self.library, proposal.campaign_id
        try:
            ref = AcceptedRef(campaign, orphan)
        except ValueError:
            raise InvalidEffect("the landed changes name an id no event can have", "orphan_effect") from None
        if not lib._absent(ref) or not lib._absent(KnowledgeRef(campaign, "event", orphan)):
            raise InvalidEffect(f"{orphan} already names an event file", "orphan_effect", (orphan,))
        expected = {relation.ref for relation, _ in landed.values()} | set(entries)
        tokens = _tokens(lib, campaign, orphan)
        for where, states in tokens.items():
            if where not in expected or states != ["accepted"]:
                raise InvalidEffect(f"{orphan} has another history in {where}", "orphan_effect", (orphan,))
        return ref, tokens

    def _adopt_holdings(self, proposal: EffectProposal, snap, orphan: str, landed: Mapping,  # type: ignore[no-untyped-def]
                        entries: Mapping, holders: Mapping, domains: tuple[str, ...]
                        ) -> tuple[HoldingsChange | None, AcceptedHoldings | None, frozenset]:
        """The holdings change still to write under X, its receipt, and the holders it writes."""
        part = proposal.holdings
        if part is None:
            return None, None, frozenset()
        lib = self.library
        decoded = {holder: held for holder, held in holders.items() if isinstance(held, Holdings)}
        others = [p for p in holdings_orphans(snap.events.values(), decoded) if p.event_id != orphan]
        if others:  # another unrecorded change on a touched holder: a human decides
            raise InvalidEffect("a holder logs a change of an effect no event records", "orphan_effect",
                                sorted({p.detail.removeprefix("holdings ") for p in others}))
        op_id = holdings_op_id(proposal.campaign_id, proposal.fingerprint)
        op = part.operation(op_id, accepted_token(orphan))
        moved = sorted(str(holder) for holder, (entry, _) in entries.items() if decoded[holder].seq != entry.seq)
        if moved:  # changed after logging it: a later operation may be a hand repair of this very orphan
            raise InvalidEffect(f"{', '.join(moved)} changed after logging this effect's holdings change",
                                "not_completable", ("holdings",))
        if not entries and landed:  # nothing logged, but the relations landed: anything logged since may repair it
            written_at = [_instant(_written_at(relation, proposal.operations[number].kind))
                          for number, (relation, _) in landed.items()]
            logged_at = {holder: [_instant(logged.at) for logged in held.log] for holder, held in decoded.items()}
            if None in written_at or any(None in times for times in logged_at.values()):
                raise InvalidEffect("a time on a landed change or a holder log cannot be read, so a hand repair "
                                    "since cannot be ruled out", "not_completable", ("holdings",))
            since = min(written_at)  # type: ignore[type-var]
            later = sorted(str(holder) for holder, times in logged_at.items()
                           if any(at >= since for at in times))  # type: ignore[operator]
            if later:
                raise InvalidEffect(f"{', '.join(later)} changed after this effect's relation changes were written",
                                    "not_completable", ("holdings",))
        if entries:  # landed on some or all holders: the rest at the seqs the landed entries' peers name
            first, (entry, _) = sorted(entries.items(), key=lambda pair: str(pair[0]))[0]
            seqs = {first: entry.seq, **entry.peers}
            if set(seqs) != set(op.holders):
                raise InvalidEffect("a landed holdings entry names other holders", "orphan_effect", (orphan,))
            change = _complete_on(lib, op, seqs)
        else:
            change = _apply_on(lib, op)
            seqs = {holder: logged.seq for holder, logged in change.entries.items()}
        written = frozenset(envelope.address.holder for envelope, _ in change.puts)  # type: ignore[union-attr]
        try:
            accepted = AcceptedHoldings(part, domains, op_id, seqs)
        except EffectError as error:
            raise _refused(error) from None
        return (change if change.puts else None), accepted, written


def _complete_on(lib: TurnEffectsLibrary, op, seqs: dict) -> HoldingsChange:  # type: ignore[no-untyped-def]
    try:
        return lib._holdings().complete(op, seqs=seqs)
    except HoldingsCodecError:
        raise InvalidEffect("a holder file cannot be decoded", "undecodable_holdings") from None
    except HoldingsRefused as error:
        reason = "not_completable" if error.reason in _NOT_OURS else error.reason
        raise InvalidEffect(f"the holdings change cannot be completed: {error.reason}", reason,
                            ("holdings",)) from None


def _apply_on(lib: TurnEffectsLibrary, op) -> HoldingsChange:  # type: ignore[no-untyped-def]
    try:
        change = lib._holdings().apply(op)
    except HoldingsCodecError:
        raise InvalidEffect("a holder file cannot be decoded", "undecodable_holdings") from None
    except HoldingsRefused as error:
        reason = "orphan_effect" if error.reason == "op_id_reused" else error.reason
        raise InvalidEffect(f"the holdings change is refused: {error.reason}", reason, ("holdings",)) from None
    if change.outcome != "applied":
        raise InvalidEffect("a holder already logs this effect's holdings change", "orphan_effect",
                            sorted(str(holder) for holder in change.entries))
    return change


def _landed_relations(proposal: EffectProposal, index, recorded: set[str]  # type: ignore[no-untyped-def]
                      ) -> dict[int, tuple[Relation, str]]:
    """Per operation, the relation that records it as written at this moment by an acceptance X that has no
    event file (an event's own change is not an orphan), and X."""
    sequence, found = proposal.moment.sequence, {}
    for number, op in enumerate(proposal.operations):
        matches = []
        for entry in candidates(index, op):
            relation = entry.relation
            if relation.retraction is not None:
                continue
            if op.kind == "begin":
                token = relation.provenance if relation.since.sequence == sequence else None
            else:
                ending = relation.ending
                token = ending.provenance if ending is not None and ending.until.sequence == sequence else None
            parsed = parse_token(token)
            if parsed is not None and parsed[0] == "accepted" and parsed[1] not in recorded:
                matches.append((relation, parsed[1]))
        if len(matches) > 1:
            raise InvalidEffect(f"operation {number} landed more than once", "ambiguous_orphan", (str(number),))
        if matches:
            found[number] = matches[0]
    return found


def _acting(lib: TurnEffectsLibrary, event: AcceptedEvent) -> tuple[dict, frozenset]:
    """An accepted event's agents' and ability users' instance files as read now, and every actor it names (as
    ``accept`` pins them through ``parts.acting_agents`` and ``parts.holdings_actors``)."""
    ops = [item.op for item in event.operations]
    users = {attempt.actor for attempt in event.ability_attempts}
    acting = {op.agent for op in ops if op.agent is not None} | users
    actors = {ref for op in ops for ref in (op.subject, op.object, op.agent) if ref is not None} | users
    if event.holdings is not None:
        acting |= event.holdings.holdings.agents()
        actors |= event.holdings.holdings.actors()
    return {agent: lib._etag(agent) for agent in sorted(acting, key=str)}, frozenset(actors)


def _tokens(lib: TurnEffectsLibrary, campaign_id: str, event_id: str) -> dict:
    """Every relation file (by ``RelationRef``) and holder file (by holder) whose raw payload names ``event_id`` in
    an exact 021 token, with each token's state."""
    found: dict = {}
    for ref, stored in lib.store.campaign_relations(campaign_id).records.items():
        states = _states(stored.record.payload, event_id)
        if states:
            found[ref] = states
    for ref, stored in lib.store.campaign_holdings(campaign_id).records.items():
        states = _states(stored.record.payload, event_id)
        if states:
            found[ref.holder] = states
    return found


def _unchanged_tokens(lib: TurnEffectsLibrary, campaign_id: str, event_id: str, seen: dict):  # type: ignore[no-untyped-def]
    """A commit-time check: the files naming ``event_id`` are still exactly those read (another history may arrive)."""
    def check() -> list[tuple[str, str]]:
        if _tokens(lib, campaign_id, event_id) != seen:
            return [(f"campaigns/{campaign_id}", f"the records naming {event_id} changed")]
        return []
    return check


def _written_at(relation: Relation, kind: str) -> str:
    """When the orphan's landed change was written: the relation's record time, or its ending's for an ``end``."""
    return relation.ending.at if kind == "end" and relation.ending is not None else relation.recorded_at


def _instant(text: str) -> datetime | None:
    """An aware time, or None when the text is not one (a hand edit or sync can leave any text)."""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def _states(value: object, event_id: str) -> list[str]:
    found: list[str] = []
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            token = parse_token(item)
            if token is not None and token[1] == event_id:
                found.append(token[0])
        elif isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return sorted(found)
