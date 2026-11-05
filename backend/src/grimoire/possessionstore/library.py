"""Store-backed unique possessions (Sprint 034): read, plan, preview, approve and commit.

``PossessionLibrary`` is trusted application code and is never handed to a worker. A unique item is a
campaign instance whose own instance file carries the ``possession`` record (``grimoire.possessions``),
so the item, its 009 entity and its record are one file under one compare-and-swap. ``create`` and
``apply`` return an ``ItemChange``: one store transaction a human can ``preview`` (exact bytes) and
review (``before``/``after``) before ``commit``. The store applies CAS, authorization, link/V1 guards,
journal and recovery.

**At commit** the change is planned again under the store lock with the same inputs and time. Any
difference (another item took the slot, a party's id was taken by another kind, the creation id was
used meanwhile) is ``StaleWrite``; a party whose instance file was replaced since the plan (perhaps a
different individual under the same id) is ``PartyReplaced``, never retried automatically. The bytes a
human approved are the bytes written, or nothing is.

Retry contract: after ``StaleWrite``, ``WriteConflict`` or ``CommitIncomplete`` (or reopening a store
that raised ``PendingRecovery``), call ``apply`` (or ``create``) again with the **same** operation and
the first change's ``party_etags`` as ``parties=``. An operation that landed is ``already_applied``
while its entry is in the item's log, and a creation while its item exists; an item replaced by another
since (``item_replaced``) or a party whose instance file differs from the pin (``party_replaced``) is
refused; otherwise the item's revision has moved and
the retry is refused as ``stale_revision``, never applied twice. ``PartyReplaced`` is never retried
automatically: a human decides.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.chatstore import DeviceIdentityUnusable, campaign_taken_ids, device_tag
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, instance_to_payload
from grimoire.core.facets import FacetBundle, Physical, Portable
from grimoire.core.identity import InstanceRef, SourceRef, paired_instances, validate_segment
from grimoire.core.portrayal import PortrayalError
from grimoire.effective import entity_decoder
from grimoire.library import LibraryPathError
from grimoire.possessions import (
    POSSESSION_KEY, AlreadyApplied, Creation, LogEntry, Operation, Possession, PossessionCodecError, PossessionError,
    PossessionRefused, State, custody_chain, originate, plan, possession_from_payload, possession_to_payload,
)
from grimoire.store import (
    LibraryStore, MalformedRecord, RecordEnvelope, RecordNotFound, StaleWrite, StoreError, UnsupportedFormat,
)
from grimoire.store.errors import AddressMismatch
from grimoire.store.jsonfmt import thaw
from grimoire.store.layout import address_parts

#: problems that mean an item may be hidden from a campaign-wide check (a slot, a custody chain, a creation id)
BLIND = frozenset({"unreadable", "undecodable"})
_MAX_ID_TRIES = 10_000


@dataclass(frozen=True)
class ItemView:
    """One unique item's record and the etag of its instance file as read."""

    item: InstanceRef
    possession: Possession
    etag: str
    envelope: RecordEnvelope = field(repr=False, compare=False)

    @property
    def name(self) -> str | None:
        """The instance's own label (009 ``local_name``); ``None`` for a sourced item that has none (its
        definition's name is the composed entity's to show)."""
        payload = self.envelope.payload
        value = payload.get("local_name") if isinstance(payload, Mapping) else None
        return value if isinstance(value, str) else None

    @property
    def rev(self) -> int:
        return self.possession.rev


@dataclass(frozen=True)
class ItemProblem:
    """Something about the campaign's items that cannot be used as it is. Nothing is repaired.

    ``unreadable`` (the store could not read an instance file: ``reason`` is ``malformed``,
    ``unsupported_format``, ``address_mismatch`` or ``unreadable``), ``undecodable`` (the possession
    record: the codec's reason, ``unsupported_schema`` meaning "upgrade the app"), ``missing_party``
    (a current owner or holder has no instance file), ``instance_id_conflict`` (another kind uses
    the item's id), ``slot_conflict`` (two items worn in one slot by one holder), ``custody_cycle`` and
    ``duplicate_individual`` (two items of one particular artifact; ``reason`` names them all).
    """

    path: str
    item: InstanceRef | None
    code: str
    reason: str | None = None


@dataclass(frozen=True)
class CampaignItems:
    views: Mapping[InstanceRef, ItemView]
    problems: tuple[ItemProblem, ...]
    #: every instance file of the campaign, as listed with the views
    instances: frozenset[InstanceRef] = frozenset()

    @property
    def complete(self) -> bool:
        """Whether every instance file was read: no item can be hidden from a campaign-wide check."""
        return not any(problem.code in BLIND for problem in self.problems)

    def held_by(self, holder: InstanceRef) -> tuple[ItemView, ...]:
        return tuple(view for view in self.views.values() if view.possession.holder == holder)

    def equipped_by(self, wearer: InstanceRef) -> dict[str, tuple[ItemView, ...]]:
        """The items each slot of ``wearer`` holds (more than one only after a hand edit or a sync merge)."""
        slots: dict[str, list[ItemView]] = {}
        for view in self.held_by(wearer):
            if view.possession.equipped is not None:
                slots.setdefault(view.possession.equipped, []).append(view)
        return {slot: tuple(views) for slot, views in sorted(slots.items())}


class PartyReplaced(StaleWrite):
    """A party's instance file changed since the change was planned: perhaps a different individual now
    has the id. Never retried automatically (``parties=`` refuses it as ``party_replaced``)."""


@dataclass
class ItemChange:
    """One planned creation or operation, not yet committed: ``before``/``after``/``preview``, then ``commit``."""

    library: PossessionLibrary
    outcome: str
    item: InstanceRef
    before: State | None = None
    after: State | None = None
    entry: LogEntry | None = None
    puts: tuple[tuple[RecordEnvelope, str | None], ...] = ()
    #: each party's instance-file etag when planned; a replaced file may be another individual
    party_etags: Mapping[InstanceRef, str] = field(default_factory=dict)
    _replan: Callable[[], ItemChange] | None = field(default=None, repr=False)
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for envelope, expected in self.puts:
            transaction.put(envelope, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes the item's instance file will hold, keyed by logical path; empty if already applied."""
        return self._transaction().preview() if self.puts else {}

    def recheck(self) -> None:
        """Plan again under the store lock with the same inputs: anything that would differ is stale."""
        store = self.library.store
        path = "/".join(address_parts(self.item))
        with store.lock:  # a replaced party outranks any other difference: a human decides
            instances = frozenset(store.campaign_instances(self.item.campaign_id))
            parties = {party: etag for party, etag in self.party_etags.items() if party != self.item}
            now = self.library._party_etags(parties, instances)  # the item itself: CAS and the replan below
            changed = sorted(str(party) for party, etag in parties.items() if now.get(party) != etag)
            if self.item in self.party_etags and self._item_replaced(self.party_etags[self.item]):
                changed.append(str(self.item))
            if changed:
                raise PartyReplaced([(path, f"party changed: {', '.join(changed)}")])
            try:
                again = self._replan() if self._replan is not None else self
            except StaleWrite:
                raise
            except PossessionError as error:  # a refusal, or the item's record became undecodable
                raise StaleWrite([(path, error.reason)]) from None
            except (StoreError, LibraryPathError) as error:  # the item's file can no longer be read
                raise StaleWrite([(path, _unreadable_reason(error))]) from None
            if again.outcome != self.outcome or again.puts != self.puts:
                raise StaleWrite([(path, "changed")])

    def _item_replaced(self, pinned: str) -> bool:
        """Whether the item's file now holds a different, readable item; a broken one is the replan's to report."""
        try:
            stored = self.library.store.read(self.item)
            payload = stored.envelope.payload  # type: ignore[union-attr]
            possession_from_payload(self.item, payload[POSSESSION_KEY])  # type: ignore[index]
        except (StoreError, LibraryPathError, PossessionError, KeyError, TypeError):
            return False
        return _individual_pin(stored.envelope) != pinned  # type: ignore[arg-type]

    def commit(self, approvals: Mapping[str, str] | None = None) -> dict[str, str]:
        store = self.library.store
        with store.lock:
            if self._committed:
                raise StoreError("this change was already committed")
            if not self.puts:  # already applied: nothing to write
                self._committed = True
                return {}
            self.recheck()
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return dict(result.etags)


def _envelope_with(envelope: RecordEnvelope, record: Possession) -> RecordEnvelope:
    """The instance file with only its possession record replaced: every other field kept verbatim."""
    payload = dict(thaw(envelope.payload))  # type: ignore[arg-type]
    payload[POSSESSION_KEY] = possession_to_payload(record)
    return RecordEnvelope(envelope.address, payload, name=envelope.name, owner=envelope.owner,
                          derived_from=envelope.derived_from, base=envelope.base, base_etag=envelope.base_etag,
                          extra=thaw(envelope.extra))  # type: ignore[arg-type]


def _individual_pin(envelope: RecordEnvelope) -> str | None:
    """A unique item's identity apart from its possession state: its entity, envelope and origin."""
    payload = envelope.payload
    if not isinstance(payload, Mapping) or not isinstance(payload.get(POSSESSION_KEY), Mapping):
        return None
    origin = payload[POSSESSION_KEY].get("origin")
    if isinstance(envelope.address, InstanceRef):
        try:  # canonical, so a hand-edited but equivalent origin pins the same before and after a rewrite
            decoded = possession_from_payload(envelope.address, payload[POSSESSION_KEY])
            origin = possession_to_payload(decoded)["origin"]
        except PossessionError:
            pass
    document = thaw({"payload": {key: value for key, value in payload.items() if key != POSSESSION_KEY},
                     "origin": origin, "name": envelope.name, "owner": envelope.owner,
                     "derived_from": envelope.derived_from, "base": envelope.base, "base_etag": envelope.base_etag,
                     "extra": envelope.extra})
    text = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "item:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _unreadable_reason(error: Exception) -> str:
    if isinstance(error, UnsupportedFormat):
        return "unsupported_format"
    if isinstance(error, AddressMismatch):
        return "address_mismatch"
    if isinstance(error, MalformedRecord):
        return "malformed"
    return "unreadable"


class PossessionLibrary:
    """Unique possessions over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 owner: str | None = None) -> None:
        if owner is not None and (type(owner) is not str or not owner.strip()):
            raise ValueError("owner must be nonempty text or None")
        self.store = store
        self.handle = handle
        self._clock = clock
        self._owner = owner

    # -- reading ---------------------------------------------------------------------------------

    def campaign_items(self, campaign_id: str) -> CampaignItems:
        """Every unique item of one campaign, decoded, plus what cannot be used as it is.

        Reads every instance file of the campaign (an item can be of any kind), under one store lock.
        """
        validate_segment(campaign_id, "campaign_id")
        with self.store.lock:
            instances = frozenset(self.store.campaign_instances(campaign_id))
            views: dict[InstanceRef, ItemView] = {}
            problems: list[ItemProblem] = []
            for ref in sorted(instances, key=str):
                path = "/".join(address_parts(ref))
                try:
                    stored = self.store.read(ref)
                except RecordNotFound:  # removed between the listing and the read: not an item now
                    continue
                except (StoreError, LibraryPathError) as error:  # its record, if any, cannot be seen
                    problems.append(ItemProblem(path, ref, "unreadable", _unreadable_reason(error)))
                    continue
                payload = stored.envelope.payload  # type: ignore[union-attr]
                if not isinstance(payload, Mapping) or POSSESSION_KEY not in payload:
                    continue  # an instance that is not a unique item
                try:
                    record = possession_from_payload(ref, payload[POSSESSION_KEY])
                except PossessionCodecError as error:
                    problems.append(ItemProblem(path, ref, "undecodable", error.reason))
                    continue
                views[ref] = ItemView(ref, record, stored.etag, stored.envelope)  # type: ignore[union-attr]
        paired = paired_instances(instances)
        possessions = {ref: view.possession for ref, view in views.items()}
        for ref, view in views.items():
            path = "/".join(address_parts(ref))
            record = view.possession
            if ref in paired:
                problems.append(ItemProblem(path, ref, "instance_id_conflict"))
            for party in (record.holder, record.owner):
                if party is not None and party not in instances:
                    problems.append(ItemProblem(path, ref, "missing_party", str(party)))
            if ref in custody_chain(record.holder, possessions):
                problems.append(ItemProblem(path, ref, "custody_cycle"))
        problems += self._duplicate_individuals(views)
        worn: dict[tuple[InstanceRef, str], list[InstanceRef]] = {}
        for ref, view in views.items():
            if view.possession.equipped is not None:
                worn.setdefault((view.possession.holder, view.possession.equipped), []).append(ref)
        for (holder, slot), refs in worn.items():
            if len(refs) > 1:
                problems += [ItemProblem("/".join(address_parts(ref)), ref, "slot_conflict", f"{holder} {slot}")
                             for ref in refs]
        return CampaignItems(dict(sorted(views.items(), key=lambda pair: str(pair[0]))),
                             tuple(sorted(problems, key=lambda p: (p.path, p.code, p.reason or ""))), instances)

    def _duplicate_individuals(self, views: Mapping[InstanceRef, ItemView]) -> list[ItemProblem]:
        """``duplicate_individual`` for every item of an ``individual`` definition that has a twin.

        ``create`` refuses a second copy of one particular artifact, but two copies can still meet:
        one made before a library had a device identity (``starfall-1``) and one made on an
        identified device (``starfall-0f8a3e79-1``) are different files, so 019 copies both without
        a conflict (Wave 4 audit). Reported, never repaired, like ``slot_conflict``: which copy is
        the artifact is a human's call. Only sources shared by two or more items are read.
        """
        shared: dict[str, list[ItemView]] = {}
        for view in views.values():
            source = view.possession.origin.source
            if source is not None:
                shared.setdefault(source.entity_key, []).append(view)
        problems: list[ItemProblem] = []
        for twins in shared.values():
            if len(twins) < 2 or not any(self._is_individual(view.possession.origin.source) for view in twins):
                continue
            names = ", ".join(sorted(str(view.item) for view in twins))
            problems += [ItemProblem("/".join(address_parts(view.item)), view.item, "duplicate_individual", names)
                         for view in twins]
        return problems

    def _is_individual(self, source: SourceRef | None) -> bool:
        """Whether the definition revision an item names is one particular artifact; one that no longer
        resolves or decodes says nothing (its item reports elsewhere, if at all). A hand-edited source the
        store cannot even address (``aux/item/x@r1``: a reserved device name) says nothing either, so one
        bad record never stops the whole listing, nor the ``create`` and ``apply`` that read it."""
        if source is None:
            return False
        try:
            stored = self.store.check_definition(source)
        except (StoreError, LibraryPathError):
            return False
        if isinstance(stored, str):
            return False
        try:
            return entity_decoder(stored).nature == "individual"
        except PortrayalError:
            return False

    def read(self, item: InstanceRef) -> ItemView:
        """One item's record. ``missing_item`` without an instance file, ``not_an_item`` without a record;
        an undecodable record raises ``PossessionCodecError``, an unreadable file the store's error."""
        if type(item) is not InstanceRef:
            raise TypeError(f"an item is an InstanceRef, not {type(item).__name__}")
        try:
            stored = self.store.read(item)
        except RecordNotFound:
            raise PossessionRefused("missing_item", f"no instance file for {item}") from None
        payload = stored.envelope.payload  # type: ignore[union-attr]
        if not isinstance(payload, Mapping) or POSSESSION_KEY not in payload:
            raise PossessionRefused("not_an_item", f"{item} has no possession record")
        return ItemView(item, possession_from_payload(item, payload[POSSESSION_KEY]), stored.etag,
                        stored.envelope)  # type: ignore[union-attr]

    def _party_etags(self, parties, instances: frozenset[InstanceRef]) -> dict[InstanceRef, str]:  # type: ignore[no-untyped-def]
        """What each existing party was checked against: its exact bytes (a malformed file by its raw
        bytes), or, for a party that is itself a unique item, everything but its changing possession state,
        so a chest that moved since the plan is replanned, not taken for a different individual."""
        etags = {}
        for party in parties:
            if party not in instances:
                continue
            try:
                stored = self.store.read(party)
                etags[party] = _individual_pin(stored.envelope) or stored.etag  # type: ignore[arg-type]
            except MalformedRecord as error:
                if error.etag is not None:
                    etags[party] = error.etag
            except (StoreError, LibraryPathError):
                continue  # no bytes to pin (a link, an I/O error): not a usable party (``missing_party``)
        return etags

    # -- creating --------------------------------------------------------------------------------

    def create(self, creation: Creation, *, kind: str = "item",
               parties: Mapping[InstanceRef, str] | None = None) -> ItemChange:
        """Plan a new unique item, held by ``creation.holder``, under a fresh instance id.

        A campaign original (no ``source``) is a physical, portable 009 entity named ``creation.name``.
        A sourced item is an instance of that definition; an ``individual`` definition (one particular
        artifact) may be a unique item only once per campaign (``duplicate_individual``). ``parties`` is a
        retry's pin, as for ``apply``.

        The id is ``<base>-<n>``, the first free number for the definition's entity id (or the kind). On a
        library with a device identity (028) the base carries a tag: the device's ``device_tag``, so two
        devices making the same template offline never mint one id; for an ``individual``, a digest of the
        definition's unpinned source ref instead, so every device draws the same id for that one artifact
        (and only for it) and two devices that each made it offline collide on one file, which 019 keeps
        both of as a conflict. A damaged or unreadable identity refuses (``device_identity``).
        """
        if type(creation) is not Creation:
            raise TypeError("create takes a Creation")
        pins = _pins(parties, {party for party in (creation.holder, creation.owner) if party is not None})
        try:
            validate_segment(kind, "kind")
        except ValueError as error:
            raise PossessionRefused("invalid_field", str(error)) from None
        at = self._clock()
        return self._plan_create(creation, kind, at, pins)

    def _plan_create(self, creation: Creation, kind: str, at: str, pins: Mapping[InstanceRef, str]) -> ItemChange:
        campaign = creation.campaign_id
        with self.store.lock:
            found = self.campaign_items(campaign)
            for view in found.views.values():  # replay first: a create that landed is already applied
                if view.possession.origin.op == creation.op_id:
                    if creation.same(view.possession.origin) and view.item.kind == kind:
                        return ItemChange(self, "already_applied", view.item)
                    raise PossessionRefused("op_id_reused", f"{creation.op_id} created {view.item} with other "
                                            "content")
            if not found.complete:
                raise PossessionRefused("unreadable_items", "some instance files of the campaign cannot be read, "
                                        f"so a creation id cannot be checked: {_blind(found)}")
            parties = [party for party in (creation.holder, creation.owner) if party is not None]
            paired = sorted(str(party) for party in parties if party in paired_instances(found.instances))
            if paired:
                raise PossessionRefused("instance_id_conflict", f"another kind also uses these ids: {paired}")
            etags = self._party_etags(parties, found.instances)
            _check_pins(pins, etags)
            source_fields: dict[str, object] = {}
            individual = False
            if creation.source is not None:
                source_fields, individual = self._source(creation.source, kind, found)
            taken = campaign_taken_ids(self.store, campaign)
            base = (creation.source.entity_id if creation.source is not None else kind)[:100]
            try:
                tag = device_tag(self.store)
            except DeviceIdentityUnusable as error:
                raise PossessionRefused("device_identity", str(error)) from error
            if tag and individual and creation.source is not None:  # one artifact: one id on every device
                tag = "-" + hashlib.sha256(str(creation.source.unpinned()).encode()).hexdigest()[:8]
            base += tag
            item = next((InstanceRef(campaign, kind, f"{base}-{n}") for n in range(1, _MAX_ID_TRIES)
                         if f"{base}-{n}" not in taken), None)
            if item is None:
                raise PossessionRefused("too_large", f"no free instance id for {base}")
            record = originate(creation, item, at=at, exists=etags.__contains__)
        if creation.source is None:
            entity = EntityInstance(item, local_name=creation.name, added=FacetBundle((Physical(), Portable())))
        else:
            entity = EntityInstance(item, source=creation.source, local_name=creation.name)
        payload = instance_to_payload(entity)
        payload[POSSESSION_KEY] = possession_to_payload(record)
        envelope = RecordEnvelope(item, payload, owner=self._owner, extra={"schema": ENTITY_SCHEMA},
                                  **source_fields)  # type: ignore[arg-type]
        after = record.state
        return ItemChange(self, "applied", item, None, after, None, ((envelope, None),), etags,
                          lambda: self._plan_create(creation, kind, at, pins))

    def _source(self, source: SourceRef, kind: str, found: CampaignItems) -> tuple[dict[str, object], bool]:
        """The definition a sourced item is an instance of, checked: the envelope's lineage fields, and
        whether it is an ``individual`` (one particular artifact)."""
        stored = self.store.check_definition(source)
        if isinstance(stored, str):
            raise PossessionRefused("missing_definition", f"{source} does not resolve: {stored}")
        try:
            definition = entity_decoder(stored)
        except PortrayalError as error:
            raise PossessionRefused(error.reason, str(error)) from None
        if definition.nature == "individual":
            if definition.ref.kind != kind:
                raise PossessionRefused("kind_mismatch", f"an instance of {definition.ref} is a {definition.ref.kind}"
                                        f", not a {kind}")
            twins = sorted(str(view.item) for view in found.views.values()
                           if view.possession.origin.source is not None
                           and view.possession.origin.source.entity_key == source.entity_key)
            if twins:
                raise PossessionRefused("duplicate_individual", f"{source.unpinned()} is one particular thing, "
                                        f"already the unique item {', '.join(twins)}")
        return ({"derived_from": str(source), "base": str(stored.ref), "base_etag": stored.etag},
                definition.nature == "individual")

    # -- changing --------------------------------------------------------------------------------

    def apply(self, item: InstanceRef, op: Operation, *, expected_rev: int,
              parties: Mapping[InstanceRef, str] | None = None) -> ItemChange:
        """Plan ``op`` on ``item`` as read at revision ``expected_rev``; refusals raise ``PossessionRefused``.

        ``parties`` is a retry's pin: the ``party_etags`` of the change first planned for this operation.
        A party whose instance file differs now is refused as ``party_replaced`` (after replay: an
        operation that landed is ``already_applied`` whatever its parties became since).
        """
        if type(item) is not InstanceRef or type(op) is not Operation:
            raise TypeError("apply takes an InstanceRef and an Operation")
        pins = _pins(parties, {item} | {party for step in op.steps for party in step.parties})
        at = self._clock()
        return self._plan_apply(item, op, expected_rev, at, pins)

    def _plan_apply(self, item: InstanceRef, op: Operation, expected_rev: int, at: str,
                    pins: Mapping[InstanceRef, str]) -> ItemChange:
        with self.store.lock:  # one consistent read: no commit in this process lands between two reads
            found = self.campaign_items(item.campaign_id)
            view = found.views.get(item)
            if view is None:
                self.read(item)  # raises the precise refusal: missing, not an item, undecodable or unreadable
                raise StaleWrite([("/".join(address_parts(item)), "changed")])  # it changed between the two reads
            current = view.possession
            replay = current.origin.op == op.op_id or current.entry(op.op_id) is not None
            if not replay:
                if item in paired_instances(found.instances):  # load() leaves both out: no one item (IDENTITY.md)
                    raise PossessionRefused("instance_id_conflict", f"another kind also uses {item.instance_id}")
                if any(step.kind in ("move", "equip") for step in op.steps) and not found.complete:
                    raise PossessionRefused("unreadable_items", "some instance files of the campaign cannot be "
                                            f"read, so slots and custody cannot be checked: {_blind(found)}")
                parties = sorted({party for step in op.steps for party in step.parties}, key=str)
                paired = sorted(str(party) for party in parties if party in paired_instances(found.instances))
                if paired:
                    raise PossessionRefused("instance_id_conflict", f"another kind also uses these ids: {paired}")
            parties = sorted({party for step in op.steps for party in step.parties}, key=str)
            etags = self._party_etags(parties, found.instances)
            pin = _individual_pin(view.envelope)
            if pin is not None:  # the item itself: a retry never lands on a new item given its id since
                etags[item] = pin
            if not replay:
                _check_pins(pins, etags, item)
            others = {ref: other.possession for ref, other in found.views.items() if ref != item}
            result = plan(op, current, expected_rev=expected_rev, at=at, exists=etags.__contains__, others=others)
        if isinstance(result, AlreadyApplied):
            return ItemChange(self, result.outcome, item, result.entry.before, result.entry.after, result.entry)
        envelope = _envelope_with(view.envelope, result.value)
        return ItemChange(self, result.outcome, item, result.before, result.after, result.value.log[-1],
                          ((envelope, view.etag),), etags,
                          lambda: self._plan_apply(item, op, expected_rev, at, pins))


def _blind(found: CampaignItems) -> str:
    return ", ".join(problem.path for problem in found.problems if problem.code in BLIND)


def _pins(parties: Mapping[InstanceRef, str] | None, touched: set[InstanceRef]) -> Mapping[InstanceRef, str]:
    """A retry's ``parties`` pin: every key is a party the operation names, every value an etag."""
    if parties is None:
        return {}
    if not isinstance(parties, Mapping):
        raise PossessionRefused("invalid_field", "parties must be a mapping of party to instance etag")
    for party, pinned in parties.items():
        if party not in touched:
            raise PossessionRefused("invalid_field", f"parties names {party!r}, which the operation does not name")
        if type(pinned) is not str:
            raise PossessionRefused("invalid_field", f"the pinned etag for {party} must be text")
    return dict(parties)


def _check_pins(pins: Mapping[InstanceRef, str], etags: Mapping[InstanceRef, str],
                item: InstanceRef | None = None) -> None:
    replaced = sorted(str(party) for party, pinned in pins.items() if etags.get(party) != pinned)
    if item is not None and str(item) in replaced:
        raise PossessionRefused("item_replaced", f"{item} is not the item first planned: its file was replaced")
    if replaced:
        raise PossessionRefused("party_replaced", f"instance files changed since first planned: {replaced}")
