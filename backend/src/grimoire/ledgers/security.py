"""The library's durable authority: grants, approvals and the decision audit on disk.

``DurableAuthority`` owns the kernel ``Authorizer`` rebuilt from the ``security``
ledger and is the only writer of the ``authority/`` ledgers. Every decision passes
through its sink before it takes effect. An allow for ``apply`` or ``execute`` is
on disk before it is returned (inside a commit's consequential scope, once at the
scope's end, before anything is staged); other decisions are flushed with the
next flush, at 500 pending, or on ``close()``. Any failure to persist poisons the
authority: every handle is denied ``internal_error`` until it is reopened from
disk. See docs/TOOL_AUTHORITY.md.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Collection, Iterator, Mapping
from datetime import datetime, timezone

from grimoire.security import (
    Approval, AuditRecord, Authorizer, Grant, WorkerHandle, is_human,
)
from grimoire.security.policy import CONSEQUENTIAL
from grimoire.store import LibraryStore
from grimoire.store.errors import AuthorityUnavailable, LedgerInvalid, TakenIdsUnlisted
from grimoire.store.layout import CAMPAIGNS, HOLDINGS, INSTANCES, PLACEMENTS, SOURCES, SUFFIX, name_key
from grimoire.store.ledger import MAX_BATCH_ENTRIES, Head, Ledger, entry_cost
from grimoire.store.library import LedgerWriter

from .codec import (
    decode_approval, decode_decision, decode_grant, encode_approval, encode_decision,
    encode_grant, envelope, exact,
)

SECURITY = "security"
Clock = Callable[[], str]
#: campaign folders whose next two segments are ``<kind>/<instance id>``: an address there names one individual
_INSTANCE_FAMILIES = frozenset({INSTANCES, HOLDINGS, PLACEMENTS})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DurableAuthority:
    """Trusted application code only; workers receive handles from ``handle_for``."""

    def __init__(self, store: LibraryStore, authorizer: Authorizer, owners: frozenset[str], clock: Clock) -> None:
        self._store = store
        self._kernel = authorizer
        self._owners = owners
        self._clock = clock
        self._pending: list[dict[str, object]] = []
        self._local = threading.local()
        self._poisoned: BaseException | None = None
        self._closed = False
        self._capability: object | None = None
        self._writers: dict[str, LedgerWriter] = {}
        self.views: dict[str, object] = {}  # replayed ledger views (sovereignty), guarded by ``lock``

    # -- opening ------------------------------------------------------------------
    @classmethod
    def open(cls, store: LibraryStore, owners, *, clock: Clock | None = None) -> DurableAuthority:
        """Replay the ``security`` ledger, bind the store, and start recording.

        Callers must not continue with the store after a failed open.
        """
        owners = frozenset(owners)
        ledger = store.read_ledger(SECURITY)
        kernel = Authorizer(owners)
        _replay(kernel, owners, ledger.entries)
        authority = cls(store, kernel, owners, clock or utc_now)
        authority._capability = store.bind_authority(authority)
        writer = authority._writers[SECURITY] = store.ledger_writer(SECURITY, capability=authority._capability)
        if writer.head != ledger.head:
            raise AuthorityUnavailable("the security ledger changed while it was being opened")
        kernel.attach_sink(authority._sink, lock=store.lock)  # decide, record and consume as one step
        if not ledger.batches:
            with store.lock:
                authority._pending.append(envelope("genesis", authority._clock(), owners=sorted(owners)))
                authority._flush_locked()
        return authority

    # -- what workers and the store use ----------------------------------------------
    @property
    def lock(self) -> threading.RLock:
        return self._store.lock

    @property
    def owners(self) -> frozenset[str]:
        return self._owners

    def handle_for(self, principal: str, *, trusted: bool = False) -> WorkerHandle:
        self._require_usable()
        return self._kernel.handle_for(principal, trusted=trusted)

    def issued(self, handle: object) -> bool:
        return self._kernel.issued(handle)

    def active_grants(self) -> tuple[Grant, ...]:
        with self.lock:
            return self._kernel.active_grants()

    def pending_approvals(self) -> tuple[Approval, ...]:
        with self.lock:
            return self._kernel.pending_approvals()

    def audit(self) -> tuple[AuditRecord, ...]:
        with self.lock:
            return self._kernel.audit()

    def describe_policy(self) -> tuple[str, ...]:
        with self.lock:
            return self._kernel.describe_policy()

    @property
    def poisoned(self) -> bool:
        return self._poisoned is not None

    @property
    def usable(self) -> bool:
        """Whether decisions can be made and recorded: neither closed nor poisoned. A read: decides,
        records and raises nothing (what ``handle_for`` and the sink require)."""
        return not self._closed and self._poisoned is None

    # -- the sink and flushing -------------------------------------------------------------
    def _depth(self) -> int:
        return getattr(self._local, "depth", 0)

    def _sink(self, record: AuditRecord) -> None:
        with self.lock:
            self._require_usable()
            try:
                entry = envelope("decision", self._clock(), decision=encode_decision(record))
            except Exception as error:  # a clock or encoding failure must not leave an audit gap
                self._poison(error)
                raise
            self._pending.append(entry)
            consequential = record.allowed and record.permission in CONSEQUENTIAL
            # inside a save's scope everything waits for its single flush (appends split into batches)
            if self._depth() == 0 and (consequential or len(self._pending) >= MAX_BATCH_ENTRIES):
                self._flush_locked()

    def _flush_locked(self) -> None:
        if self._poisoned is not None:
            raise AuthorityUnavailable("the authority failed to persist; reopen it") from self._poisoned
        if not self._pending:
            return
        writer = self._writers[SECURITY]
        try:
            writer.append(list(self._pending), writer.head)
        except Exception as error:
            self._poison(error)
            raise AuthorityUnavailable(f"could not persist the authority ledger: {error}") from error
        self._pending.clear()

    def flush(self) -> None:
        """Write every pending decision; raises ``AuthorityUnavailable`` if poisoned or failing."""
        with self.lock:
            self._flush_locked()

    @contextlib.contextmanager
    def consequential_scope(self) -> Iterator[None]:
        """For ``LibraryStore`` commits only: allows inside are flushed once, at the end of the
        outermost scope on this thread, and the commit stages nothing unless that flush succeeds.
        Elsewhere, an allow would take effect before it is on disk."""
        self._local.depth = self._depth() + 1
        try:
            yield
        except Exception:
            self._local.depth -= 1
            if self._depth() == 0:
                with self.lock:
                    if self._poisoned is None:
                        self._flush_locked()  # decisions were made; a failure surfaces with this as context
            raise
        except BaseException:
            self._local.depth -= 1  # a crash: no further I/O
            raise
        else:
            self._local.depth -= 1
            if self._depth() == 0:
                self.flush()  # nothing is staged unless this succeeds

    def _poison(self, error: BaseException) -> None:
        if self._poisoned is None:
            self._poisoned = error

    def _require_usable(self) -> None:
        if self._closed:
            raise AuthorityUnavailable("the authority is closed")
        if self._poisoned is not None:
            raise AuthorityUnavailable("the authority failed to persist; reopen it") from self._poisoned

    # -- lifecycle (durable before returning) -------------------------------------------------
    def _lifecycle(self, entry_kind: str, check: Callable[[], None], apply: Callable[[], None],
                   fields: Callable[[], Mapping[str, object]]) -> None:
        """Validate, apply, record; the entry is on disk before this returns."""
        with self.lock:
            self._require_usable()
            check()  # refusals change nothing
            recorded_at = self._clock()
            apply()  # the kernel validates before it changes anything
            try:
                self._pending.append(envelope(entry_kind, recorded_at, **fields()))
            except BaseException as error:  # applied but not recordable: never act on it
                self._poison(error)
                raise
            self._flush_locked()

    def add_grant(self, grant: Grant) -> None:
        self._lifecycle("grant_added", lambda: encode_grant(grant), lambda: self._kernel.add_grant(grant),
                        lambda: {"grant": encode_grant(grant)})

    def revoke_grant(self, grant_id: str) -> None:
        def check() -> None:
            if type(grant_id) is not str or not self._kernel.is_grant_active(grant_id):
                raise ValueError(f"grant {grant_id!r} is not active")
        self._lifecycle("grant_revoked", check, lambda: self._kernel.revoke_grant(grant_id),
                        lambda: {"grant_id": grant_id})

    def register_approval(self, approval: Approval) -> None:
        def fields() -> Mapping[str, object]:
            return {"approval": encode_approval(approval, self._kernel.approval_grant(approval.approval_id))}
        self._lifecycle("approval_registered", lambda: encode_approval(approval, ""),
                        lambda: self._kernel.register_approval(approval), fields)

    def revoke_approval(self, approval_id: str) -> None:
        def check() -> None:
            if type(approval_id) is not str or not self._kernel.is_approval_pending(approval_id):
                raise ValueError(f"approval {approval_id!r} is not pending")
        self._lifecycle("approval_revoked", check, lambda: self._kernel.revoke_approval(approval_id),
                        lambda: {"approval_id": approval_id})

    # -- other ledgers (sovereignty) ----------------------------------------------------------------
    def read_ledger(self, name: str) -> Ledger:
        with self.lock:
            self._require_usable()
            return self._store.read_ledger(name)

    def _append(self, name: str, entries: list[dict[str, object]], expected_head: Head | None) -> Head:
        """For ``SovereigntyLedger`` only, which checks provenance first: durably append to another
        ``authority/`` ledger after ``expected_head`` (the head it replayed). ``ValueError`` for an entry no batch could hold, changing nothing; any
        failure to write, including a head that moved, poisons the authority."""
        if name == SECURITY:
            raise ValueError("the security ledger is written only by the authority itself")
        with self.lock:
            self._require_usable()
            for entry in entries:
                entry_cost(entry)
            try:
                writer = self._writers.get(name)
                if writer is None:
                    writer = self._writers[name] = self._store.ledger_writer(name, capability=self._capability)
                return writer.append(entries, expected_head)
            except Exception as error:
                self._poison(error)
                raise AuthorityUnavailable(f"could not persist ledger {name}: {error}") from error

    def now(self) -> str:
        return self._clock()

    # -- closing --------------------------------------------------------------------------------
    def close(self) -> None:
        """Flush (unless poisoned) and release the ledger capability; call before ``store.close()``."""
        with self.lock:
            if self._closed:
                return
            try:
                if self._poisoned is None:
                    self._flush_locked()
            finally:
                self._closed = True
                self._store.release_ledger_writers(self._capability)
                self._writers.clear()
                self.views.clear()

    def __enter__(self) -> DurableAuthority:
        return self

    def __exit__(self, exc_type: object, *exc_info: object) -> None:
        if exc_type is not None and not issubclass(exc_type, (Exception, SystemExit)):  # type: ignore[arg-type]
            self._closed = True  # an interrupt or crash: no further I/O; the store's writers stay unusable
            return
        self.close()


def _replay(kernel: Authorizer, owners: frozenset[str], entries) -> None:
    """Rebuild the kernel from the ledger; ``LedgerInvalid`` for anything it could not have written."""
    for index, entry in enumerate(entries):
        kind = entry.get("kind") if isinstance(entry, Mapping) else None
        try:
            if index == 0 or kind == "genesis":
                if index != 0 or kind != "genesis":
                    raise LedgerInvalid("genesis is exactly the first entry")
                recorded = exact(entry, "genesis", {"owners"})["owners"]
                if not isinstance(recorded, tuple) or frozenset(recorded) != owners or len(recorded) != len(owners):
                    raise LedgerInvalid("the library's owner set differs from the one this ledger was created with")
                if any(not is_human(owner) for owner in recorded):
                    raise LedgerInvalid("owners must be human principals")
            elif kind == "grant_added":
                kernel.add_grant(decode_grant(exact(entry, kind, {"grant"})["grant"]))
            elif kind == "grant_revoked":
                grant_id = exact(entry, kind, {"grant_id"})["grant_id"]
                if type(grant_id) is not str or not kernel.is_grant_active(grant_id):
                    raise LedgerInvalid(f"revocation of a grant that is not active: {grant_id!r}")
                kernel.revoke_grant(grant_id)
            elif kind == "approval_registered":
                approval, pinned = decode_approval(exact(entry, kind, {"approval"})["approval"])
                kernel.register_approval(approval)
                if kernel.approval_grant(approval.approval_id) != pinned:
                    raise LedgerInvalid(f"approval {approval.approval_id} was pinned to another grant")
            elif kind == "approval_revoked":
                approval_id = exact(entry, kind, {"approval_id"})["approval_id"]
                if type(approval_id) is not str or not kernel.is_approval_pending(approval_id):
                    raise LedgerInvalid(f"revocation of an approval that is not pending: {approval_id!r}")
                kernel.revoke_approval(approval_id)
            elif kind == "decision":
                kernel.replay_decision(decode_decision(exact(entry, kind, {"decision"})["decision"]))
            else:
                raise LedgerInvalid(f"unknown security entry kind {kind!r}")
        except LedgerInvalid:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise LedgerInvalid(f"security ledger entry {index + 1} ({kind}): {error}") from None


# -- ids the security ledger keeps taken ------------------------------------------------------------

def security_campaign_ids(store: LibraryStore, *, spent: Collection[tuple[str, str, str]] = ()) -> frozenset[str]:
    """Every campaign id a grant or approval in the security ledger names: ``campaigns/<id>`` or anything
    under it, except the approvals in ``spent`` (see ``_granted_addresses``).

    A grant outlives a hand-deleted ``campaigns/<id>`` folder and covers whatever is later written
    there, so a new campaign given that id would hand the grant's worker the new campaign. A campaign
    id allocator must therefore find its id outside this set, as it must find no sovereignty ledger
    (``campaign_authority_presence``). Scanned raw and failing closed, as ``security_instance_ids``.
    """
    return frozenset(parts[1] for parts in _granted_addresses(store, spent)
                     if len(parts) >= 2 and parts[0] == CAMPAIGNS)


def security_source_named(store: LibraryStore, source_id: str, *,
                          spent: Collection[tuple[str, str, str]] = ()) -> bool:
    """Whether a grant or approval in the security ledger names ``sources/<source id path>``: the
    source's own folder or anything under it, or a folder above it that is finer than a whole namespace
    (a multi-segment id ``world:salt:marsh`` lies under ``sources/world/salt``). The approvals in
    ``spent`` are left out (see ``_granted_addresses``).

    As for campaigns, a grant outlives a hand-deleted source folder and would cover a new source given
    that id (042 proposes new world sources), so a new source must be named by none. A grant on
    ``sources`` or on a whole namespace (``sources/world``) names no source and covers every new one
    by design. Scanned raw and failing closed, as ``security_instance_ids``.
    """
    own = (name_key(SOURCES), *(name_key(part) for part in source_id.split(":")))
    for parts in _granted_addresses(store, spent):
        if len(parts) < 3 or parts[0] != own[0]:
            continue
        shorter = min(len(parts), len(own))
        if parts[:shorter] == own[:shorter]:
            return True
    return False


def security_instance_ids(store: LibraryStore, campaign_id: str, *,
                          spent: Collection[tuple[str, str, str]] = ()) -> frozenset[str]:
    """Every instance id of this campaign that a grant or approval in the security ledger is scoped to.

    Part of the one reservation set (``grimoire.chatstore.campaign_taken_ids``): a grant on one
    individual's instance file (``campaigns/<id>/instances/<kind>/<instance id>.json``), holder file
    (023) or placement folder (018), or on anything finer under them, survives a hand delete of that
    individual and would cover a new one given the same id, who would inherit the permission. A grant
    on a whole campaign, family or kind folder names no individual, so it reserves no instance id: it
    covers every new actor there by design.

    Batches are scanned raw, with no replay or chain check, so a broken ledger still protects its ids.
    A revoked grant still reserves, as the sovereignty scan reserves on any mention: a raw scan cannot
    tell which revocations replay would accept, and ``LibraryStore.repair_ledger`` moving a broken
    tail aside undoes the revocations in it, so the grant can come back. A ledger folder or batch that
    cannot be listed or read (a link, a look-alike, malformed) raises ``TakenIdsUnlisted``.

    The cost grows with the whole library's audit history, not only this campaign's: the security
    ledger also holds every recorded decision, and every batch must be read to fail closed. It is
    paid only when a new id is needed.
    """
    folded = name_key(campaign_id)
    taken: set[str] = set()
    for parts in _granted_addresses(store, spent):
        if len(parts) >= 5 and parts[0] == CAMPAIGNS and parts[1] == folded and parts[2] in _INSTANCE_FAMILIES:
            name = parts[4]
            # an instance or holder file name carries the suffix; a placement folder is the bare id
            taken.add(name[:-len(SUFFIX)] if name.endswith(SUFFIX) else name)
    return frozenset(taken)


def _granted_addresses(store: LibraryStore,
                       spent: Collection[tuple[str, str, str]] = ()) -> tuple[tuple[str, ...], ...]:
    """The address of every grant's ``scope`` and every approval's ``resource`` in the security ledger,
    split into segments, each folded with ``name_key``.

    Only grants and approvals confer authority. A decision's ``resource`` is audit history: it names
    whatever was asked for, allowed or not, and covers nothing later, so it is not collected (reserving
    on it would keep every campaign ever touched taken, for no protection). Keyed on field names alone,
    as the sovereignty scan keys on ``actor_id``: a grant is the object with a ``scope`` (the security
    ledger holds no delegations), an approval the one with a ``payload_digest``. Replay accepts only
    canonical addresses, so folding changes nothing for a sound ledger; for a garbled one it errs
    toward taking the id an odd spelling folds to, as a look-alike campaign folder counts as taken.

    ``spent`` holds ``(approval_id, resource, payload_digest)`` for the approvals the caller's own
    commit supplies: an approval of the very bytes that commit writes, at the address it writes them
    to. Such an approval reserves nothing, because it is how a new campaign or actor is written in the
    first place (preview, register its approvals, commit), and all it can ever authorize is that
    write, for the individual that commit creates. Only an exact match is spent: another approval of
    the same address, or one of other bytes, still reserves.
    """
    documents, unread = store.ledger_documents(SECURITY)
    if unread:
        raise TakenIdsUnlisted(f"the security ledger cannot all be read ({', '.join(unread)}), so the campaign "
                               "and instance ids its grants name cannot be reserved", unread)
    found: list[tuple[str, ...]] = []
    stack: list[object] = list(documents)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            if value.get("kind") == "decision":  # most of the ledger, and never collected: not walked
                continue
            approval = "payload_digest" in value
            address = value.get("resource") if approval else value.get("scope")
            if approval and spent and _spent(value, spent):
                continue  # this commit's own approval: it names nothing else, and is not walked
            if isinstance(address, str):
                found.append(tuple(name_key(part) for part in address.split("/")))
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return tuple(found)


def _spent(approval: Mapping[object, object], spent: Collection[tuple[str, str, str]]) -> bool:
    key = (approval.get("approval_id"), approval.get("resource"), approval.get("payload_digest"))
    return all(type(part) is str for part in key) and key in spent
