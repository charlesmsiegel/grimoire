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
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone

from grimoire.security import (
    Approval, AuditRecord, Authorizer, Grant, WorkerHandle, is_human,
)
from grimoire.security.policy import CONSEQUENTIAL
from grimoire.store import LibraryStore
from grimoire.store.errors import AuthorityUnavailable, LedgerInvalid
from grimoire.store.ledger import MAX_BATCH_ENTRIES
from grimoire.store.library import LedgerWriter

from .codec import (
    MAX_ID, check_size, decode_approval, decode_decision, decode_grant, encode_approval, encode_decision,
    encode_grant, envelope, exact,
)

SECURITY = "security"
Clock = Callable[[], str]


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
        authority._writers[SECURITY] = store.ledger_writer(SECURITY, capability=authority._capability)
        kernel.attach_sink(authority._sink)
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
        return self._kernel.active_grants()

    def pending_approvals(self) -> tuple[Approval, ...]:
        return self._kernel.pending_approvals()

    def audit(self) -> tuple[AuditRecord, ...]:
        return self._kernel.audit()

    def describe_policy(self) -> tuple[str, ...]:
        return self._kernel.describe_policy()

    @property
    def poisoned(self) -> bool:
        return self._poisoned is not None

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
            if (consequential and self._depth() == 0) or len(self._pending) >= MAX_BATCH_ENTRIES:
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
        """Allows inside are flushed once, at the end of the outermost scope on this thread."""
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
    def _lifecycle(self, entry_kind: str, fields: Mapping[str, object], apply: Callable[[], None]) -> None:
        with self.lock:
            self._require_usable()
            apply()  # the kernel validates before it changes anything
            self._pending.append(envelope(entry_kind, self._clock(), **fields))
            self._flush_locked()

    def add_grant(self, grant: Grant) -> None:
        fields = {"grant": encode_grant(grant)}  # exact type and size, before any change
        self._lifecycle("grant_added", fields, lambda: self._kernel.add_grant(grant))

    def revoke_grant(self, grant_id: str) -> None:
        if not self._kernel.is_grant_active(grant_id):
            raise ValueError(f"grant {grant_id!r} is not active")
        self._lifecycle("grant_revoked", {"grant_id": grant_id}, lambda: self._kernel.revoke_grant(grant_id))

    def register_approval(self, approval: Approval) -> None:
        check_size(approval.approval_id if type(approval) is Approval else None, "approval_id", MAX_ID)
        with self.lock:
            self._require_usable()
            self._kernel.register_approval(approval)
            fields = {"approval": encode_approval(approval, self._kernel.approval_grant(approval.approval_id))}
            self._pending.append(envelope("approval_registered", self._clock(), **fields))
            self._flush_locked()

    def revoke_approval(self, approval_id: str) -> None:
        if not self._kernel.is_approval_pending(approval_id):
            raise ValueError(f"approval {approval_id!r} is not pending")
        self._lifecycle("approval_revoked", {"approval_id": approval_id},
                        lambda: self._kernel.revoke_approval(approval_id))

    # -- other ledgers (sovereignty) ----------------------------------------------------------------
    def append(self, name: str, entries: list[dict[str, object]]) -> None:
        """Durably append to another ``authority/`` ledger; a failure poisons the authority."""
        with self.lock:
            self._require_usable()
            writer = self._writers.get(name)
            try:
                if writer is None:
                    writer = self._writers[name] = self._store.ledger_writer(name, capability=self._capability)
                writer.append(entries, writer.head)
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

    def __enter__(self) -> DurableAuthority:
        return self

    def __exit__(self, *exc_info: object) -> None:
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
