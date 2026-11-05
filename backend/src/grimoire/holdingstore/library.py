"""Store-backed counted holdings (Sprint 023): read, plan, preview, approve and commit.

``HoldingsLibrary`` is trusted application code and is never handed to a worker. ``apply`` reads every
holder an operation touches, runs the pure mutator and returns a ``HoldingsChange``: one store
transaction a human can ``preview`` (exact bytes) and review (``summary``) before ``commit``. The store
applies CAS, authorization, link/V1 guards, journal and recovery to every holder file.

Retry contract: after ``StaleWrite``, ``WriteConflict`` or ``CommitIncomplete`` (or reopening a store
that raised ``PendingRecovery``), call ``apply`` again with the **same** operation and the first
change's ``holder_etags`` as ``holders=``. It is then ``already_applied``, a refused ``partial_op``/
``beyond_history``/``holder_replaced``, or a fresh plan. A re-created change has new bytes and needs
fresh approvals. ``HolderReplaced`` (a ``StaleWrite``) is never retried automatically: the holder's
instance file was replaced, perhaps by a different individual, and a human decides.

No API here deletes a holdings file or changes a count except through a logged operation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import HoldingsRef, InstanceRef
from grimoire.holdings import (
    HOLDINGS_SCHEMA, AlreadyApplied, Holdings, HoldingsCodecError, HoldingsRefused, LogEntry, Operation,
    SummaryRow, entry_to_payload, holdings_from_payload, holdings_to_payload, plan,
)
from grimoire.library import LibraryPathError
from grimoire.store import (
    LibraryStore, MalformedRecord, RecordNotFound, ScopeRecord, StaleWrite, StoreError, TakenIdsUnlisted,
    UnstorableIdentifier,
)
from grimoire.store.envelope import scope_record_document
from grimoire.store.layout import address_parts, classify

_ENTRY_DEPTH = 6  # spaces before an op entry's lines: document > payload > ops > entry


@dataclass(frozen=True)
class HoldingsView:
    """One holder's holdings and the etag they were read at (``None``: no file yet)."""

    holder: InstanceRef
    holdings: Holdings
    etag: str | None
    envelope: ScopeRecord | None = field(default=None, repr=False, compare=False)

    def counts(self) -> dict[str, int]:
        return dict(self.holdings.stacks)

    def lookup(self, op_id: str) -> LogEntry | None:
        return self.holdings.entry(op_id)

    @property
    def evicted_through(self) -> int:
        return self.holdings.evicted_through


@dataclass(frozen=True)
class HoldingsProblem:
    """A holdings entry that cannot be used as it is: ``undecodable``, ``missing_holder`` or ``unrecognized``
    (something under ``holdings/`` that is not a holder file, such as ``Mara-1.json``). It is kept.

    ``reason`` says why a file is undecodable: the codec's reason (``unsupported_schema`` means "upgrade the
    app", anything else "repair by hand") or the store's load-problem code (``malformed``, ``unsupported_format``,
    ``address_mismatch``).
    """

    path: str
    holder: InstanceRef | None
    code: str
    reason: str | None = None


@dataclass(frozen=True)
class CampaignHoldings:
    views: Mapping[InstanceRef, HoldingsView]
    problems: tuple[HoldingsProblem, ...]


@dataclass
class HoldingsChange:
    """One planned operation, not yet committed: ``summary``/``preview`` for a human, then ``commit``.

    ``puts`` lets a larger writer (021's accepted effects, 030) fold these files into its own transaction. It must then hold
    ``store.lock`` from ``recheck()`` through its own commit, must not put another change for the same
    holder in that transaction (merge them into one operation instead), and approvals stay pinned to
    these bytes.
    """

    library: HoldingsLibrary
    outcome: str
    summary: tuple[SummaryRow, ...] = ()
    entries: Mapping[InstanceRef, LogEntry] = field(default_factory=dict)
    puts: tuple[tuple[ScopeRecord, str | None], ...] = ()
    #: each holder's instance-file etag when planned; a replaced file may be another individual
    holder_etags: Mapping[InstanceRef, str] = field(default_factory=dict)
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.library.store.transaction()
        for envelope, expected in self.puts:
            transaction.put(envelope, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each holder file will hold, keyed by logical path; empty if already applied."""
        return self._transaction().preview() if self.puts else {}

    def recheck(self) -> None:
        """Every holder still has the very instance file it was planned against (same bytes): a file
        replaced since then may hold a different individual under the same id. The caller holds
        ``store.lock`` through the commit."""
        store = self.library.store
        with store.lock:
            gone, changed = [], []
            for envelope, _ in self.puts:
                holder = envelope.address.holder  # type: ignore[union-attr]
                path = "/".join(address_parts(holder))
                etag = (self.library._instance_etag(holder)
                        if holder in store.campaign_instances(holder.campaign_id) else None)
                if etag is None:  # vanished, or no longer readable (a link, an I/O error): no holder to pin
                    gone.append((path, "holder gone"))
                elif etag != self.holder_etags.get(holder):
                    changed.append((path, "holder changed"))
            if changed:
                raise HolderReplaced(changed + gone)
            if gone:
                raise StaleWrite(gone)

    def commit(self, approvals: Mapping[str, str] | None = None) -> dict[str, str]:
        store = self.library.store
        with store.lock:
            if self._committed:
                raise StoreError("this change was already committed")
            if not self.puts:  # already applied: nothing to write, even if a holder has since gone
                self._committed = True
                return {}
            self.recheck()
            result = self._transaction().commit(self.library.handle, approvals)
            self._committed = True
        return dict(result.etags)


class HolderReplaced(StaleWrite):
    """A holder's instance file changed since the change was planned: perhaps a different individual
    now has the id. Never retried automatically (``apply(..., holders=)`` refuses it as ``holder_replaced``)."""


class HoldingsUnlisted(TakenIdsUnlisted):
    """A folder that could hold the campaign's holder files is a link or cannot be listed (or the
    campaign folder exists only as a look-alike), so the holder ids it may contain cannot be reserved:
    allocation fails closed rather than guess. One case of the store's ``TakenIdsUnlisted``."""


def holdings_holder_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids that have a holdings file in the campaign, readable or not, from file names only.

    Every instance allocator must treat these as taken (as 017's ``relation_endpoint_ids``), so a
    hand-deleted holder's id is never given to a different individual that would silently inherit
    its holdings. No holder file is read, so the cost does not grow with holdings history. Raises
    ``HoldingsUnlisted`` when the listing is incomplete.
    """
    refs, complete = store.campaign_holding_names(campaign_id)
    if not complete:
        raise HoldingsUnlisted(f"campaigns/{campaign_id}/holdings cannot be fully listed (a linked or unlistable "
                               "folder, or a look-alike campaign folder), so holder ids cannot be reserved",
                               (f"campaigns/{campaign_id}/holdings",))
    return frozenset(ref.instance_id for ref in refs)


#: problems that name no holder file the store could read: a look-alike (whose own id
#: ``holdings_holder_ids`` keeps taken) or an entry that can never be a holder file
_UNREAD_BY_DESIGN = frozenset({"name_mismatch", "unrecognized"})


def holdings_peer_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids named inside the campaign's holder files: a move's holder, target and ``peers``.

    A hand-deleted holder's id can live on in another holder's log as a move's counterparty; a new
    individual given it would read as that move's party. The payloads are scanned raw (every
    ``<kind>/<id>`` text, key or value), so a log the codec refuses still protects its ids. A holder
    file the store cannot read (malformed, a link, a newer format) raises ``TakenIdsUnlisted``. A
    look-alike is not read; its own id stays taken by ``holdings_holder_ids``. Reads every holder file.
    """
    found = store.campaign_holdings(campaign_id)
    unread = tuple(sorted({problem.path for problem in found.problems if problem.code not in _UNREAD_BY_DESIGN}))
    if unread:
        raise TakenIdsUnlisted(f"holder files of {campaign_id} cannot all be read ({', '.join(unread)}), so the "
                               "instance ids their logs name cannot be reserved", unread)
    taken: set[str] = set()
    stack: list[object] = [stored.record.payload for stored in found.records.values()]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            kind, _, instance_id = value.partition("/")
            try:
                InstanceRef(campaign_id, kind, instance_id)
            except ValueError:
                continue
            taken.add(instance_id)
        elif isinstance(value, Mapping):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(taken)


class _StoreSizer:
    """Sizes holder files exactly as the store will render them (without its 8 MiB refusal)."""

    def __init__(self, envelopes: Mapping[InstanceRef, tuple[str | None, Mapping[str, object], str | None]]) -> None:
        self._envelopes = envelopes  # holder -> (owner, extra, previous etag)

    def document(self, holdings: Holdings) -> int:
        owner, extra, previous = self._envelopes[holdings.holder]
        record = ScopeRecord(HoldingsRef.of(holdings.holder), HOLDINGS_SCHEMA, holdings_to_payload(holdings),
                             owner=owner, extra=extra)
        text = json.dumps(scope_record_document(record, previous), sort_keys=True, indent=2, ensure_ascii=False,
                          allow_nan=False)
        return len((text + "\n").encode("utf-8"))

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        text = json.dumps(entry_to_payload(holdings.holder, entry), sort_keys=True, indent=2, ensure_ascii=False)
        return len(text.replace("\n", "\n" + " " * _ENTRY_DEPTH).encode("utf-8")) + _ENTRY_DEPTH + 2


class HoldingsLibrary:
    """Counted holdings over one open, writable ``LibraryStore``."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 owner: str | None = None) -> None:
        if owner is not None and (type(owner) is not str or not owner.strip()):
            raise ValueError("owner must be nonempty text or None")
        self.store = store
        self.handle = handle
        self._clock = clock
        self._owner = owner

    def read(self, holder: InstanceRef) -> HoldingsView:
        """The holder's holdings (empty without a file); ``missing_holder`` without an instance file."""
        if type(holder) is not InstanceRef:
            raise TypeError(f"a holder is an InstanceRef, not {type(holder).__name__}")
        if holder not in self.store.campaign_instances(holder.campaign_id):
            raise HoldingsRefused("missing_holder", f"no instance file for {holder}")
        return self._view(holder)

    def _instance_etag(self, holder: InstanceRef) -> str | None:
        try:
            return self.store.read(holder).etag
        except MalformedRecord as error:  # malformed, newer-format or misaddressed: pin its exact bytes
            return error.etag
        except (StoreError, LibraryPathError):
            return None  # no bytes to pin (an OS error, a link): not a usable holder (``missing_holder``)

    def _view(self, holder: InstanceRef) -> HoldingsView:
        if type(holder) is not InstanceRef:
            raise TypeError(f"a holder is an InstanceRef, not {type(holder).__name__}")
        try:
            stored = self.store.read(HoldingsRef.of(holder))
        except (RecordNotFound, UnstorableIdentifier):  # an id no file can have holds nothing: plan refuses it
            return HoldingsView(holder, Holdings.empty(holder), None)
        record = stored.record  # type: ignore[union-attr]
        return HoldingsView(holder, holdings_from_payload(holder, record.schema, record.payload), stored.etag,
                            record)

    def apply(self, op: Operation, *, basis: Mapping[InstanceRef, int] | None = None,
              holders: Mapping[InstanceRef, str] | None = None) -> HoldingsChange:
        """Plan ``op`` against the holders' current files; refusals raise ``HoldingsRefused``.

        ``holders`` is a retry's pin: the ``holder_etags`` of the change first planned for this
        operation. A holder whose instance file differs now is refused as ``holder_replaced``.
        """
        if type(op) is not Operation:
            raise TypeError("apply takes an Operation")
        pins = _pins(holders, op.holders)
        with self.store.lock:  # one consistent read: no commit in this process can land between two holders
            views = {holder: self._view(holder) for holder in op.holders}  # an undecodable holder refuses here
            instances = set(self.store.campaign_instances(op.campaign_id))
            holder_etags = {holder: etag for holder in op.holders if holder in instances
                            if (etag := self._instance_etag(holder)) is not None}  # unreadable: no holder
        replaced = sorted(str(holder) for holder, pinned in pins.items()
                          if holder in holder_etags and holder_etags[holder] != pinned)
        envelopes = {}
        for holder, view in views.items():
            if view.envelope is None:
                envelopes[holder] = (self._owner, {}, None)
            else:
                envelopes[holder] = (view.envelope.owner, view.envelope.extra, view.etag)
        try:  # replay first: an operation that landed is already_applied whatever its holders became since
            result = plan(op, {holder: view.holdings if view.etag else None for holder, view in views.items()},
                          self._clock(), exists=holder_etags.__contains__, size=_StoreSizer(envelopes), basis=basis)
        except HoldingsRefused:
            if not replaced:
                raise
            result = None
        if isinstance(result, AlreadyApplied):
            return HoldingsChange(self, result.outcome, entries=result.entries)
        if replaced:
            raise HoldingsRefused("holder_replaced", f"instance files changed since first planned: {replaced}")
        assert result is not None
        puts = []
        for holder in sorted(result.values, key=str):  # a pinned order: the files publish in this order
            owner, extra, previous = envelopes[holder]
            value = result.values[holder]
            puts.append((ScopeRecord(HoldingsRef.of(holder), HOLDINGS_SCHEMA, holdings_to_payload(value),
                                     owner=owner, extra=extra), previous))
        entries = {holder: value.log[-1] for holder, value in result.values.items()}
        return HoldingsChange(self, result.outcome, result.summary, entries, tuple(puts), holder_etags)

    def campaign_holdings(self, campaign_id: str) -> CampaignHoldings:
        """Every holder file of one campaign, decoded, plus the files that cannot be used as they are.

        Reads only this campaign's ``holdings/`` folder (``store.campaign_holdings``), never the whole library.
        """
        with self.store.lock:
            found_records = self.store.campaign_holdings(campaign_id)
            instances = set(self.store.campaign_instances(campaign_id))
        views: dict[InstanceRef, HoldingsView] = {}
        problems: list[HoldingsProblem] = []
        for ref, stored in found_records.records.items():
            path = "/".join(address_parts(ref))
            try:
                holdings = holdings_from_payload(ref.holder, stored.record.schema, stored.record.payload)
            except HoldingsCodecError as error:
                problems.append(HoldingsProblem(path, ref.holder, "undecodable", error.reason))
                continue
            views[ref.holder] = HoldingsView(ref.holder, holdings, stored.etag, stored.record)
            if ref.holder not in instances:
                problems.append(HoldingsProblem(path, ref.holder, "missing_holder"))
        for problem in found_records.problems:  # bad JSON, newer envelope, misaddressed, strays and look-alikes
            found = classify(tuple(problem.path.split("/")))
            if found is not None and found[0] == "holdings":
                holder = found[1].holder  # type: ignore[union-attr]
                problems.append(HoldingsProblem(problem.path, holder, "undecodable",
                                                problem.code))
            else:
                problems.append(HoldingsProblem(problem.path, None, "unrecognized", problem.code))
        return CampaignHoldings(dict(sorted(views.items(), key=lambda pair: str(pair[0]))),
                                tuple(sorted(problems, key=lambda p: (p.path, p.code))))


def _pins(holders: Mapping[InstanceRef, str] | None, touched: tuple[InstanceRef, ...]) -> Mapping[InstanceRef, str]:
    """A retry's ``holders`` pin: every key is a holder the operation touches, every value an etag."""
    if holders is None:
        return {}
    if not isinstance(holders, Mapping):
        raise HoldingsRefused("invalid_field", "holders must be a mapping of holder to instance etag")
    for holder, pinned in holders.items():
        if holder not in touched:
            raise HoldingsRefused("invalid_field", f"holders names {holder!r}, which the operation does not touch")
        if type(pinned) is not str:
            raise HoldingsRefused("invalid_field", f"the pinned etag for {holder} must be text")
    return holders
