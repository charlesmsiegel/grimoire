"""Review and apply World Change Sets (043) against the native store.

``ChangeSetLibrary(store, handle)`` is trusted application code, like the other store
services: it is never handed to a worker. ``review`` turns a change set into the exact bytes
it would write, a readable diff of each file, the conflicts that stop it and the
authorization each file needs. ``apply`` commits exactly those bytes in one guarded store
transaction, after checking every target again under the store lock; ``reject`` ends the
review without writing anything. See docs/CHANGESETS.md.

Conflicts are judged per record, not per file. A change set fails only where a record it
touches, or pins, changed since review: an entity created by someone else, a head that
moved, or a revision file edited by hand. The source manifest is shared by every record of
the world, so an unrelated change to it (another entity's head, the title) is not a conflict
of any record, but the manifest bytes a human approved are then out of date: the review is
refused as ``manifest_changed`` and a fresh review is needed. Nothing is ever merged silently.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.changesets import ChangeSet, ChangeSetError, Precondition, RecordChange, summarize, unified
from grimoire.core.identity import SourceRef
from grimoire.core.json_value import thaw
from grimoire.security import Approval, Permission, ResourcePath, payload_digest
from grimoire.store import (
    ImpreciseNumber, InstanceIdConflict, InvalidEnvelope, InvalidSourceState, LibraryStore, NameCollision, RecordEnvelope, RecordNotFound,
    SourceManifest, StaleWrite, StoreError, StoredSource, UnstorableIdentifier, WriteDenied,
)
from grimoire.store.errors import AuthorityUnavailable
from grimoire.store.layout import definition_parts, manifest_parts

_REVISION = re.compile(r"r([0-9]{1,9})")
#: Store refusals of a whole commit that no re-review of the same records can fix by itself.
_BLOCKING = (InvalidSourceState, NameCollision, InstanceIdConflict, UnstorableIdentifier, ImpreciseNumber)
_UNSET = object()


@dataclass(frozen=True)
class Conflict:
    """Why a change set cannot be applied as reviewed.

    ``target`` is a record key (``kind/entity_id``), ``manifest`` or ``source``. Reasons:
    ``exists``, ``entity_id_taken``, ``missing``, ``no_head``, ``head_moved``, ``base_edited``,
    ``unreadable``, ``revision_taken``, ``manifest_changed``, ``source_unavailable`` and, from the
    store's own compare-and-swap, ``changed``/``absent`` with the file's path as ``target``.
    """

    target: str
    reason: str
    detail: str = ""


class ChangeSetConflict(ChangeSetError):
    """Applying was refused because the library changed since review; nothing was written."""

    def __init__(self, conflicts: tuple[Conflict, ...]) -> None:
        self.conflicts = tuple(conflicts)
        super().__init__("conflict", "; ".join(f"{c.target}: {c.reason}" for c in self.conflicts))

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.conflicts,))

    @property
    def needs_redraft(self) -> bool:
        """True when a record conflicts; False when only the shared manifest moved (re-review)."""
        return any(c.reason != "manifest_changed" for c in self.conflicts)


@dataclass(frozen=True)
class FileChange:
    """One file the change set writes, for a reviewer: the diff is for reading, ``new`` is what
    is approved and committed (``digest`` is its ``sha256:``)."""

    path: str  # the logical address: what an approval names
    action: str  # create | revise | manifest
    target: str  # kind/entity_id, or the source id for its manifest
    summary: str
    old_path: str | None  # what the diff compares with: the base revision, or the manifest itself
    old: bytes | None
    new: bytes
    digest: str
    diff: str


@dataclass(frozen=True)
class PermissionCheck:
    """How the library's authority decides ``apply`` for one file, with no approval yet."""

    path: str
    allowed: bool
    reason: str  # granted | approval_required | not_granted | reserved_resource | ...


@dataclass
class ChangeSetReview:
    """What a human is shown before deciding, and what ``apply`` commits byte for byte."""

    changeset: ChangeSet
    files: tuple[FileChange, ...]
    conflicts: tuple[Conflict, ...]
    permissions: tuple[PermissionCheck, ...]
    blocked: tuple[str, ...]  # store refusals that stop the commit whatever is approved
    manifest_etag: str
    _library: ChangeSetLibrary = field(repr=False)
    _puts: tuple[tuple[RecordEnvelope | SourceManifest, str | None], ...] = field(repr=False)
    _state: str = field(default="open", repr=False)  # open | applied | rejected | failed
    _approvals: list[tuple[object, str]] = field(default_factory=list, repr=False)

    @property
    def applicable(self) -> bool:
        """Whether nothing but authorization stands between this review and a commit."""
        return self._state == "open" and not self.conflicts and not self.blocked

    @property
    def needs_approval(self) -> tuple[str, ...]:
        """Files the handle may write only with a human's approval of their exact bytes."""
        return tuple(check.path for check in self.permissions if check.reason == "approval_required")

    @property
    def state(self) -> str:
        return self._state

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each file will hold, keyed by the address an approval names."""
        return {item.path: item.new for item in self.files}

    def describe_approvals(self, approved_by: str) -> dict[str, str]:
        """The ``Approval.describe()`` sentence of each approval ``approve`` would issue, by path, so
        the approval UI shows them beside the staged bytes their digests cover (007's obligation)."""
        principal = self._library.handle.principal  # type: ignore[attr-defined]
        return {item.path: _approval("pending", principal, item, approved_by).describe()
                for item in self.files if item.path in self.needs_approval}


@dataclass(frozen=True)
class AppliedChangeSet:
    changeset_id: str
    revisions: tuple[SourceRef, ...]  # the pinned revisions written, in change order
    etags: Mapping[str, str]  # logical address -> new etag, the manifest included


def _approval(approval_id: str, principal: str, item: FileChange, approved_by: str) -> Approval:
    return Approval(approval_id, principal, Permission.APPLY, ResourcePath.parse(item.path), item.digest,
                    approved_by)


def _path(parts: tuple[str, ...]) -> str:
    return "/".join(parts)


class ChangeSetLibrary:
    """Draft, review, approve, apply and reject World Change Sets in one library."""

    def __init__(self, store: LibraryStore, handle: object) -> None:
        self.store = store
        self.handle = handle

    # -- drafting -------------------------------------------------------------------------------
    def head(self, source: str, kind: str, entity_id: str) -> tuple[str, RecordEnvelope, str]:
        """``(revision, envelope, etag)`` of an entity's current head, the base a revise starts from.

        The head is the manifest's declared head, or the only revision when none is declared.
        Refusals (``ChangeSetError``): ``missing_source``, ``excluded_source``, ``missing``,
        ``no_head`` and ``unreadable``."""
        with self.store.lock:
            stored = self._source(source)
            entities = self.store.source_entities(source)
            revision, problem = _head_of(stored.manifest, entities, kind, entity_id)
            if problem is not None:
                raise ChangeSetError(problem, f"{kind}/{entity_id}")
            record, problem = self._read(SourceRef(source, kind, entity_id, revision))
            if problem is not None:
                raise ChangeSetError(problem, f"{kind}/{entity_id}@{revision}")
            return revision, record.envelope, record.etag  # type: ignore[union-attr]

    def create(self, source: str, kind: str, entity_id: str, payload: object, *, name: str | None = None,
               evidence: tuple[str, ...] = (), revision: str = "r1") -> RecordChange:
        """A change creating a new entity. Whether it already exists is judged at review."""
        try:
            SourceRef(source, kind, entity_id, revision)
        except ValueError as error:
            raise ChangeSetError("invalid_field", str(error)) from None
        return RecordChange(kind, entity_id, revision, payload, name=name, evidence=evidence)  # type: ignore[arg-type]

    def revise(self, source: str, kind: str, entity_id: str, payload: object, *, name: object = _UNSET,
               evidence: tuple[str, ...] = ()) -> RecordChange:
        """A change giving an entity a new revision with ``payload``, based on its current head.

        ``name`` defaults to the head's display name. The new revision is the next free ``r<N>``
        after every revision file of the entity, headed or not."""
        with self.store.lock:
            base, envelope, base_etag = self.head(source, kind, entity_id)
            revisions = self.store.source_entities(source).get((kind, entity_id), ())
        numbers = [int(match.group(1)) for match in map(_REVISION.fullmatch, revisions) if match]
        taken = {revision.casefold() for revision in revisions}
        number = max(numbers, default=0) + 1
        while f"r{number}" in taken:
            number += 1
        return RecordChange(kind, entity_id, f"r{number}", payload,  # type: ignore[arg-type]
                            name=envelope.name if name is _UNSET else name,  # type: ignore[arg-type]
                            base=base, base_etag=base_etag, evidence=evidence)

    def pin(self, source: str, kind: str, entity_id: str) -> Precondition:
        """A precondition that an unchanged entity keeps its current head and bytes."""
        revision, _, current = self.head(source, kind, entity_id)
        return Precondition(kind, entity_id, revision, current)

    # -- review -----------------------------------------------------------------------------------
    def review(self, changeset: ChangeSet) -> ChangeSetReview:
        """The exact files ``changeset`` would write, their diffs, conflicts and authorization.

        Reads under one store lock and writes nothing. Permission is checked with the store's
        read-only ``Transaction.check(handle)``: those decisions are audited like any other, and
        no approval is consumed. Refusals: ``missing_source``, ``excluded_source``, and
        ``unstorable`` for proposed content the store cannot encode (too large, a number it
        cannot write exactly)."""
        if type(changeset) is not ChangeSet:
            raise ChangeSetError("invalid_field", "review takes a ChangeSet")
        store = self.store
        with store.lock:
            stored = self._source(changeset.source)
            entities = store.source_entities(changeset.source)
            conflicts = self._conflicts(changeset, stored, entities)
            heads = dict(stored.manifest.heads)
            puts: list[tuple[RecordEnvelope | SourceManifest, str | None]] = []
            olds: list[tuple[RecordChange | None, str | None, bytes | None]] = []
            for change in changeset.changes:
                envelope, old_path, old = self._proposed(changeset, change)
                puts.append((envelope, None))
                olds.append((change, old_path, old))
                heads[change.key] = change.revision
            manifest = SourceManifest(stored.manifest.source, stored.manifest.owner, stored.manifest.title,
                                      heads, stored.manifest.links, extra=stored.manifest.extra)
            puts.append((manifest, stored.etag))
            manifest_path = _path(manifest_parts(changeset.source))
            olds.append((None, manifest_path, self._exact(manifest_path)))
            transaction = store.transaction()
            try:
                keys = [transaction.put(envelope, expected) for envelope, expected in puts]
                preview = transaction.preview()
            except InvalidEnvelope as error:
                raise ChangeSetError("unstorable", str(error)) from None
            permissions, blocked = self._check(transaction, keys, conflicts)
        files = []
        for key, (change, old_path, old) in zip(keys, olds):
            new = preview[key]
            if change is None:
                action, target, summary = "manifest", changeset.source, f"update the heads of {changeset.source}"
            else:
                action, target, summary = change.action, change.key, summarize(change)
            files.append(FileChange(key, action, target, summary, old_path, old, new, payload_digest(new),
                                    unified(old_path, old, key, new)))
        return ChangeSetReview(changeset, tuple(files), tuple(conflicts), permissions, blocked, stored.etag,
                               self, tuple(puts))

    # -- decisions --------------------------------------------------------------------------------
    def approve(self, review: ChangeSetReview, authority: object, *, approved_by: str,
                new_id: Callable[[], str]) -> dict[str, str]:
        """Register one ``apply`` approval, for this library's handle, per file that needs one
        (``review.needs_approval``), each bound to that file's exact bytes.

        Call it only from a human's direct action on this review, after showing its files'
        bytes and ``describe_approvals`` (007): it is the approval UI's trusted half, never
        reachable from a worker. ``authority`` is the library's ``DurableAuthority`` (or a bare
        ``Authorizer``); ``approved_by`` must hold an active grant covering every file. All or
        nothing: if one registration fails, those already made are revoked. Approving a review
        again first withdraws the approvals issued for it before (an apply that succeeds consumes
        its one set, so none is left behind). Returns the
        ``approvals`` mapping ``apply`` takes; empty when the handle needs none."""
        with self.store.lock:  # one decision at a time: never approve a review being applied
            return self._approve(review, authority, approved_by, new_id)

    def _approve(self, review: ChangeSetReview, authority: object, approved_by: str,
                 new_id: Callable[[], str]) -> dict[str, str]:
        self._require_open(review)
        if review.conflicts or review.blocked:
            raise ChangeSetError("not_applicable", "resolve the conflicts and review again before approving")
        refused = [check.path for check in review.permissions
                   if not check.allowed and check.reason != "approval_required"]
        if refused or not review.permissions:
            raise ChangeSetError("not_applicable", f"the handle may not write {', '.join(refused) or 'these files'}")
        self._withdraw(review, authority)  # approving again replaces the earlier set: never two usable sets
        principal = self.handle.principal  # type: ignore[attr-defined]
        needed = [item for item in review.files if item.path in review.needs_approval]
        pending = {approval.approval_id for approval in authority.pending_approvals()}  # type: ignore[attr-defined]
        made: list[str] = []
        try:
            for item in needed:
                approval = _approval(new_id(), principal, item, approved_by)
                if approval.approval_id in pending or approval.approval_id in made:
                    # another approval's id: never taken over, so a rollback cannot withdraw it
                    raise ChangeSetError("invalid_field", f"approval id {approval.approval_id!r} is in use")
                # tracked before it is registered: a registration that raises may still have been
                # persisted (a failure after the ledger's commit point), so its withdrawal must be
                # resolved through the authority, never assumed
                review._approvals.append((authority, approval.approval_id))
                made.append(approval.approval_id)
                authority.register_approval(approval)  # type: ignore[attr-defined]
        except BaseException:
            for approval_id in made:
                try:
                    _revoke(authority, approval_id)
                except Exception:  # noqa: BLE001 - it stays tracked; reject withdraws it or fails
                    continue
                review._approvals.remove((authority, approval_id))
            raise
        return {item.path: approval_id for item, approval_id in zip(needed, made)}

    def apply(self, review: ChangeSetReview, approvals: Mapping[str, str] | None = None) -> AppliedChangeSet:
        """Commit exactly the reviewed bytes, all or nothing.

        Under the store lock every changed and pinned record is checked again, and the manifest
        must be the one reviewed: otherwise ``ChangeSetConflict`` and nothing is written. The
        store's own refusals propagate (``WriteDenied`` leaves the review open for a retry with
        approvals; any other failure ends it, and withdraws the approvals ``approve`` issued for
        it). ``WriteConflict`` is the store's pinned R7 race."""
        changeset, store = review.changeset, self.store
        with store.lock:  # the state is checked and moved under the lock, so reject cannot race it
            self._require_open(review)
            if review.conflicts or review.blocked:
                # its envelopes were built from what the conflict changed: never applied, even if the
                # conflict has since gone away; review the change set again
                self._fail(review)
                if review.conflicts:
                    raise ChangeSetConflict(review.conflicts)
                raise ChangeSetError("not_applicable", "the store refuses this review; review it again")
            try:
                stored = self._source(changeset.source)
            except ChangeSetError as error:
                self._fail(review)
                raise ChangeSetConflict((Conflict("source", "source_unavailable", error.reason),)) from None
            conflicts = self._conflicts(changeset, stored, store.source_entities(changeset.source))
            if stored.etag != review.manifest_etag:
                conflicts.append(Conflict("manifest", "manifest_changed",
                                          "the source manifest changed since review; review again"))
            if conflicts:
                self._fail(review)
                raise ChangeSetConflict(tuple(conflicts))
            transaction = store.transaction()
            for envelope, expected in review._puts:
                transaction.put(envelope, expected)
            if transaction.preview() != review.preview():  # deterministic encoding; never expected
                self._fail(review)
                raise ChangeSetError("preview_mismatch", "the staged bytes differ from the reviewed bytes")
            try:
                result = transaction.commit(self.handle, approvals)
            except WriteDenied:
                raise  # nothing written; approvals it consumed are named on the error
            except StaleWrite as error:
                self._fail(review)
                raise ChangeSetConflict(tuple(Conflict(path, reason) for path, reason in error.stale)) from error
            except BaseException:
                self._fail(review)
                raise
            review._state = "applied"
            try:  # the commit consumed what it needed; nothing issued for this review outlives it
                self._withdraw(review)
            except Exception:  # noqa: BLE001 - the change set is written; reject cannot run now
                pass
        return AppliedChangeSet(changeset.changeset_id, tuple(changeset.target(c) for c in changeset.changes),
                                dict(result.etags))

    def reject(self, review: ChangeSetReview, authority: object | None = None) -> None:
        """End the review without writing; approvals ``approve`` issued for it are revoked.

        A review whose apply failed may be rejected too: that withdraws any of its approvals the
        failed apply could not (the authority was unavailable then). After the authority failed,
        pass the reopened one as ``authority``: the approvals are withdrawn (or found already
        withdrawn) through it instead of the instance that failed."""
        with self.store.lock:
            if type(review) is not ChangeSetReview or review._library is not self:
                raise ChangeSetError("invalid_field", "not a review made by this library")
            if review._state not in ("open", "failed"):
                raise ChangeSetError("already_decided", f"this review was {review._state}")
            self._withdraw(review, authority)  # first: if the authority fails, the review stays as it was
            review._state = "rejected"

    def _fail(self, review: ChangeSetReview) -> None:
        """End a review whose apply failed, withdrawing its approvals so none outlives it. The
        apply's own error is what the caller sees; if the authority is unavailable now, the
        approvals stay listed and ``reject`` withdraws them later."""
        review._state = "failed"
        try:
            self._withdraw(review)
        except Exception:  # noqa: BLE001 - never mask why the apply failed; reject retries
            pass

    @staticmethod
    def _withdraw(review: ChangeSetReview, authority: object | None = None) -> None:
        """Withdraw every approval tracked for ``review``: through ``authority`` when given (a
        reopened one replaces the instance that issued them), else through that instance."""
        while review._approvals:
            issuer, approval_id = review._approvals[0]
            _revoke(issuer if authority is None else authority, approval_id)
            review._approvals.pop(0)

    # -- internals --------------------------------------------------------------------------------
    def _require_open(self, review: ChangeSetReview) -> None:
        if type(review) is not ChangeSetReview or review._library is not self:
            raise ChangeSetError("invalid_field", "not a review made by this library")
        if review._state != "open":
            raise ChangeSetError("already_decided", f"this review was {review._state}")

    def _source(self, source: str) -> StoredSource:
        found = self.store.check_source(source)
        if isinstance(found, str):
            raise ChangeSetError(found, source)
        return found

    def _exact(self, path: str) -> bytes | None:
        """A file's bytes for the diff only (``None`` when absent or unreadable)."""
        try:
            return self.store.read_exact(path)
        except (StoreError, ValueError, OSError):
            return None

    def _read(self, ref: SourceRef) -> tuple[object | None, str | None]:
        try:
            return self.store.read(ref), None
        except RecordNotFound:
            return None, "missing"
        except (StoreError, ValueError):
            return None, "unreadable"

    def _conflicts(self, changeset: ChangeSet, stored: StoredSource,
                   entities: Mapping[tuple[str, str], tuple[str, ...]]) -> list[Conflict]:
        conflicts: list[Conflict] = []
        manifest = stored.manifest
        for change in changeset.changes:
            key = change.key
            if change.base is None:
                if (change.kind, change.entity_id) in entities or key in manifest.heads:
                    conflicts.append(Conflict(key, "exists", "the entity was created since the change was drafted"))
                elif any(entity_id == change.entity_id for _, entity_id in entities):
                    conflicts.append(Conflict(key, "entity_id_taken", "another kind of this source uses the id"))
                continue
            found = self._expect(changeset.source, manifest, entities, change.kind, change.entity_id,
                                 change.base, change.base_etag)  # type: ignore[arg-type]
            conflicts += found
            folded = {revision.casefold() for revision in entities.get((change.kind, change.entity_id), ())}
            if not found and change.revision.casefold() in folded:  # else the head moved there: said already
                conflicts.append(Conflict(key, "revision_taken", f"{change.revision} already exists"))
        for item in changeset.preconditions:
            conflicts += self._expect(changeset.source, manifest, entities, item.kind, item.entity_id,
                                      item.revision, item.etag)
        return conflicts

    def _expect(self, source: str, manifest: SourceManifest, entities: Mapping[tuple[str, str], tuple[str, ...]],
                kind: str, entity_id: str, revision: str, expected_etag: str) -> list[Conflict]:
        key = f"{kind}/{entity_id}"
        head, problem = _head_of(manifest, entities, kind, entity_id)
        if problem is not None:
            return [Conflict(key, problem)]
        if head != revision:
            return [Conflict(key, "head_moved", f"the head is {head}, not {revision}")]
        record, problem = self._read(SourceRef(source, kind, entity_id, revision))
        if problem is not None:
            return [Conflict(key, problem, f"{revision} cannot be read")]
        if record.etag != expected_etag:  # type: ignore[union-attr]
            return [Conflict(key, "base_edited", f"{revision} was edited since the change was drafted")]
        return []

    def _proposed(self, changeset: ChangeSet, change: RecordChange
                  ) -> tuple[RecordEnvelope, str | None, bytes | None]:
        """The new revision's envelope, and the file a reviewer compares it with."""
        ref = changeset.target(change)
        payload = thaw(change.payload)
        if change.base is None:
            return RecordEnvelope(ref, payload, name=change.name, owner=changeset.author), None, None
        base_ref = ref.at(change.base)
        old_path = _path(definition_parts(base_ref))
        old = self._exact(old_path)
        record, _ = self._read(base_ref)
        owner, extra = (record.envelope.owner, record.envelope.extra) if record is not None else (None, {})  # type: ignore[union-attr]
        return RecordEnvelope(ref, payload, name=change.name, owner=owner, extra=extra), old_path, old

    def _check(self, transaction: object, keys: list[str], conflicts: list[Conflict]
               ) -> tuple[tuple[PermissionCheck, ...], tuple[str, ...]]:
        """Authorization per file and store refusals, from the read-only ``check``."""
        try:
            transaction.check(self.handle)  # type: ignore[attr-defined]
        except WriteDenied as error:
            if not error.decisions:  # not about any path: an unissued handle, say
                return (), (str(error),)
            checks = {}
            for decision in error.decisions:
                resource = str(getattr(decision, "resource", "") or "")
                checks[resource] = PermissionCheck(resource, bool(getattr(decision, "allowed", False)),
                                                   str(getattr(decision, "reason", "")))
            return tuple(checks.get(key, PermissionCheck(key, False, "undecided")) for key in keys), ()
        except StaleWrite as error:
            if not conflicts:  # a record conflict already explains the store's own compare-and-swap
                conflicts += [Conflict(path, reason) for path, reason in error.stale]
            return (), ()
        except _BLOCKING as error:
            return (), (f"{type(error).__name__}: {error}",)
        return tuple(PermissionCheck(key, True, "granted") for key in keys), ()


def _head_of(manifest: SourceManifest, entities: Mapping[tuple[str, str], tuple[str, ...]],
             kind: str, entity_id: str) -> tuple[str | None, str | None]:
    """The revision ``load()`` resolves the entity to, or why there is none."""
    revisions = entities.get((kind, entity_id), ())
    declared = manifest.heads.get(f"{kind}/{entity_id}")
    if not revisions:
        return None, "missing"
    if declared is not None:
        return (declared, None) if declared in revisions else (None, "missing")
    if len(revisions) == 1:
        return revisions[0], None
    return None, "no_head"


def _revoke(authority: object, approval_id: str) -> None:
    """Withdraw an approval still pending; one already consumed or revoked has nothing to withdraw.
    A failure of the authority itself (``AuthorityUnavailable``) propagates.

    A durable authority that failed to persist (``usable`` is False) may already show an approval
    as revoked in memory while its ledger still holds it pending, so its pending list is not
    trusted: nothing counts as withdrawn until it can be reopened and asked again."""
    if getattr(authority, "usable", True) is False:
        raise AuthorityUnavailable("the authority cannot confirm a withdrawal; reopen it and reject again")
    pending = {approval.approval_id for approval in authority.pending_approvals()}  # type: ignore[attr-defined]
    if approval_id in pending:
        authority.revoke_approval(approval_id)  # type: ignore[attr-defined]
