"""Two synthetic devices that each accepted turn effects offline, read as 028 ledgers (no real library).

The base library is 021's synthetic harbor campaign (``effects_fixtures``): Wren (a player character of
``human:ana``) carries two keys, Tam and Ines are director characters, and two chats are open. Wren and
Tam each hold ten coins. Each device is a copy of the base given its own identity, so its new event and
relation ids carry its tag. Every name is synthetic.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef, HoldingsRef, InstanceRef, ScopeRef
from grimoire.effects import (
    ActedLine, EffectHoldings, EffectProposal, accepted_from_payload, wrapup_from_payload,
)
from grimoire.graph.codec import relation_from_payload
from grimoire.holdings import (
    HOLDINGS_SCHEMA, Holdings, Line, Operation, entry_to_payload, holdings_from_payload, holdings_to_payload,
)
from grimoire.holdingstore import HoldingsLibrary
from grimoire.ledgermerge import BASE, Kept, Ledger, Rewrite, Version, merge_ledger
from grimoire.store import LibraryStore, ScopeRecord
from grimoire.store.envelope import scope_record_document
from grimoire.store.jsonfmt import render
from grimoire.store.layout import address_parts
from grimoire.sync import identify

from chat_fixtures import Clock, Ids
from effects_fixtures import C, CHAT, CONTROLS, INES, TAM, WREN, Handles, contribution, effects_library, moment, seed
from store_helpers import OWNER

OTHER = ChatRef(C, "t-2")  # the second chat: two devices playing two chats offline is the clean case
THIRD = ChatRef(C, "t-3")  # a third chat, for a third device
COIN = "coin"


def build_base(root: Path) -> Path:
    """The agreed library: the harbor campaign, two more chats, and ten coins each for Wren and Tam."""
    path = root / "base"
    with LibraryStore.create(path) as store:
        handles = Handles()
        seed(store, handles)
        chats = ChatLibrary(store, handles.app, clock=Clock(), new_id=_After("t", 1), owner=OWNER)
        chats.start_chat(Launch("premise", premise="The fish market at dawn."), scope=ScopeRef(C)).commit()
        chats.start_chat(Launch("premise", premise="The lighthouse at dusk."), scope=ScopeRef(C)).commit()
        holdings = HoldingsLibrary(store, handles.app, clock=Clock(), owner=OWNER)
        holdings.apply(Operation("seed-coins", (Line.add(WREN, COIN, 10), Line.add(TAM, COIN, 10)))).commit()
    return path


class _After:
    """``Ids`` that start after ``skip`` values of ``prefix`` (the seed already used ``t-1``)."""

    def __init__(self, prefix: str, skip: int) -> None:
        self.ids = Ids()
        for _ in range(skip):
            self.ids(prefix)

    def __call__(self, prefix: str) -> str:
        return self.ids(prefix)


class Device:
    """One device's copy of the base, identified, with its own effects library and handles."""

    def __init__(self, base: Path, root: Path, name: str, *, identified: bool = True) -> None:
        self.name = name
        shutil.copytree(base, root / name)
        self.store = LibraryStore.open(root / name)
        if identified:
            identify(self.store, name)
        self.handles = Handles()
        self.clock = Clock()
        self.effects = effects_library(self.store, self.handles, clock=self.clock)

    def accept(self, proposal: EffectProposal):
        backed = any(item.backed_by for item in proposal.operations)
        change = self.effects.accept(proposal, controls=CONTROLS, contributions=[contribution(
            turn=proposal.moment.turn_id)] if backed else [], endorsements=[self.handles.endorse(proposal)]
            if backed else [])
        return change.commit().ref

    def revoke(self, ref, proposal: EffectProposal | None = None, *, reason: str = "revised") -> None:
        _, etag = self.effects.read(ref, requested_by="director")
        backed = proposal is not None and any(item.backed_by for item in proposal.operations)
        self.effects.revoke(ref, reason=reason, expected=etag,
                            endorsements=[self.handles.undo(ref, proposal)] if backed else []).commit()

    def hand_op(self, op: Operation) -> None:
        HoldingsLibrary(self.store, self.handles.app, clock=self.clock, owner=OWNER).apply(op).commit()

    def hand_edit(self, holder: InstanceRef, item: str, count: int) -> None:
        """Set one count in a holder file by hand, as an owner editing the file would: the log is untouched."""
        found = self.store.campaign_holdings(holder.campaign_id).records[HoldingsRef.of(holder)]
        held = holdings_from_payload(holder, found.record.schema, found.record.payload)
        stacks = {name: n for name, n in held.stacks.items() if name != item} | ({item: count} if count else {})
        record = ScopeRecord(HoldingsRef.of(holder), HOLDINGS_SCHEMA, holdings_to_payload(replace(held, stacks=stacks)),
                             owner=found.record.owner, extra=found.record.extra)
        write_back(self.store, {path_of(HoldingsRef.of(holder)): render(scope_record_document(record, found.etag))})

    def close(self) -> None:
        self.store.close()


def pay(giver: InstanceRef = TAM, to: InstanceRef = INES, amount: int = 2, *, sequence: int = 7, chat: ChatRef = OTHER,
        attempt: str | None = None, turn: str | None = None) -> EffectProposal:
    """A director effect: ``giver`` pays ``to`` coins (a holdings part, no relation operation)."""
    turn = turn or f"t{sequence}"
    return EffectProposal(C, chat, moment(sequence, turn), f"{giver.instance_id} pays {to.instance_id}.", (),
                          "director", attempt_id=attempt or f"pay-{chat.chat_id}-{sequence}",
                          holdings=EffectHoldings((ActedLine(Line.move(giver, to, COIN, amount)),)))


# -- reading a library as a ledger, and writing a merge back -------------------------------------

def _version(stored, value) -> Version:
    return Version(value, stored.etag, stored.record.owner, stored.record.extra)


def read_ledger(store: LibraryStore, campaign: str = C) -> Ledger:
    events = {ref: _version(found, accepted_from_payload(ref, found.record.schema, found.record.payload))
              for ref, found in store.campaign_accepted(campaign).records.items()}
    wrapups = {ref: _version(found, wrapup_from_payload(ref, found.record.schema, found.record.payload))
               for ref, found in store.campaign_wrapups(campaign).records.items()}
    relations = {ref: _version(found, relation_from_payload(ref, found.record.schema, found.record.payload))
                 for ref, found in store.campaign_relations(campaign).records.items()}
    holdings = {}
    for ref, found in store.campaign_holdings(campaign).records.items():
        holder = InstanceRef(ref.campaign_id, ref.kind, ref.instance_id)
        holdings[holder] = _version(found, holdings_from_payload(holder, found.record.schema, found.record.payload))
    return Ledger(campaign, events, wrapups, relations, holdings)


class StoreSizer:
    """Sizes a merged holder file exactly as the store renders it (as 023's own adapter does)."""

    def document(self, holdings: Holdings, owner, extra, previous) -> int:
        record = ScopeRecord(HoldingsRef.of(holdings.holder), HOLDINGS_SCHEMA, holdings_to_payload(holdings),
                             owner=owner, extra=extra)
        return len(render(scope_record_document(record, previous)))

    def entry(self, holdings: Holdings, entry) -> int:
        text = json.dumps(entry_to_payload(holdings.holder, entry), sort_keys=True, indent=2, ensure_ascii=False)
        return len(text.replace("\n", "\n      ").encode("utf-8")) + 8  # as 023's adapter: six spaces deep


def path_of(ref) -> str:
    return "/".join(address_parts(ref))


def merge(base: LibraryStore, devices: Mapping[str, LibraryStore], **kwargs):
    ledgers = {name: read_ledger(store) for name, store in devices.items()}
    instances = set()
    for store in devices.values():
        instances |= set(store.campaign_instances(C))
    return merge_ledger(read_ledger(base), ledgers, instances=instances, sizer=StoreSizer(), **kwargs)


def file_bytes(result, base: LibraryStore, devices: Mapping[str, LibraryStore]) -> dict[str, bytes]:
    """Every merged file's bytes, as each device would write them: a kept version's own bytes, or the rewrite
    rendered in the store's scope-record envelope."""
    stores = {BASE: base, **devices}
    found = {}
    for ref, output in result.files.items():
        if isinstance(output, Kept):
            found[path_of(ref)] = stores[output.source].root.joinpath(*address_parts(ref)).read_bytes()
        else:
            assert isinstance(output, Rewrite)
            record = ScopeRecord(ref, output.schema, output.payload, owner=output.owner, extra=output.extra)
            found[path_of(ref)] = render(scope_record_document(record, output.previous))
    return found


def write_back(store: LibraryStore, files: Mapping[str, bytes]) -> None:
    """Put the merged bytes in a library, as 028's sync wiring will (byte for byte)."""
    for path, data in files.items():
        target = store.root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


__all__ = ["CHAT", "COIN", "Device", "OTHER", "THIRD", "build_base", "file_bytes", "merge", "pay", "read_ledger",
           "write_back"]
