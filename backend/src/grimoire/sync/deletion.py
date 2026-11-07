"""Propagating a deletion, and holding one back that would orphan something (028, docs/SYNC.md).

Row 9 of the decision table: both archives agreed on E, one device still holds exactly E and the
other deleted it. For the families in ``model.PROPAGATED_DELETIONS`` the holder deletes E too, in
its data commit, through the store's ``delete_exact``: E is kept beside the path as a ``hold`` in the
same transaction (it may now exist nowhere else), recorded as ``held`` in the pair's kept-version
record in a commit of its own just before (``holds_state``, as ``resolve`` records a hold), so no
``dismiss`` can ever take it for a delivered copy. The pending marker carries ``{"from": E,
"to": null}``; both archives then record the tombstone ``{"deleted": E}``.

A deletion must never leave the receiver in a state its own stores refuse, or mean something else
there. Mirroring the copy's head-dependency rule, it is held back as ``delete_dependency`` when
anything the receiver keeps (its records, this sync's copies to it, held families included) still
needs it (``orphaned``). That is read generically and conservatively, from every text of every
record left: it errs only toward holding a deletion back, which is a conflict that keeps both
versions, never a loss.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import (
    ChatRef, ImageRef, InstanceRef, PlacementRef, RelationRef, ScopeRef, SourceRef, TurnRef,
)
from grimoire.store import LibraryStore, StoreError, UnstorableIdentifier, etag
from grimoire.store.jsonfmt import JsonError, parse_strict
from grimoire.store.layout import (
    CAMPAIGNS, SCOPE, SOURCES, address_parts, chat_parts, classify, conflict_parts, definition_parts, kept_address,
    logical, name_key,
)
from grimoire.store.replica import pinned_by

from . import bookkeeping, keeps as kept_record
from .art import art_needs
from .documents import keeps_name, syncable

_SLOT = re.compile(r"~[0-9a-f]{12}(?:\.([1-9][0-9]*))?$")
#: The text references a record can hold, read from every string of it (keys included).
_REFS = (SourceRef, InstanceRef, ChatRef, RelationRef, ScopeRef, ImageRef, PlacementRef)


@dataclass(frozen=True)
class Delete:
    """A device deletes ``data`` (the version it still holds) at ``path``, keeping it as a hold in ``slot``."""

    path: str
    data: bytes
    slot: int

    @functools.cached_property
    def expected(self) -> str:  # hashed once: a record may be up to 010's file cap, and this is read often
        return etag(self.data)

    @property
    def hold(self) -> str:
        """The kept-version file the removed bytes land in."""
        return "/".join(conflict_parts(tuple(self.path.split("/")), self.expected, self.slot))


def hold_slot(store: LibraryStore, path: str, data: bytes) -> int:
    """The slot a hold of ``data`` beside ``path`` takes (one already holding those bytes is reused)."""
    target = store.transaction().keep_version(path, data, regenerable=False)
    found = _SLOT.search(target)
    assert found is not None
    return int(found.group(1) or 0)


def stage(txn, deletes: Iterable[Delete]) -> None:  # type: ignore[no-untyped-def]
    """Each deletion with the hold of what it removes, as the store requires (``delete_exact``)."""
    for delete in deletes:
        txn.keep_version(delete.path, delete.data, slot=delete.slot, regenerable=False)  # may be the only copy
        txn.delete_exact(delete.path, delete.expected)


def keys_of(delete: Delete) -> tuple[str, str]:
    """The record's address and its hold's: both are authorized, and either denial drops the deletion."""
    parts = tuple(delete.path.split("/"))
    return str(logical(parts)), str(kept_address(parts, delete.expected, delete.slot))


# -- planning and applying (``session`` turns the results into conflicts) ---------------------------

def final_files(files: Mapping[str, bytes], blobs: Mapping[str, int],
                copies: Iterable[object]) -> tuple[dict[str, bytes], dict[str, int]]:
    """What a device will hold once ``copies`` (``session.Copy``) land, before any deletion: its
    records and blob sizes."""
    files, blobs = dict(files), dict(blobs)
    for copy in copies:
        if copy.data is None:  # type: ignore[attr-defined]
            blobs[copy.path] = copy.size  # type: ignore[attr-defined]
        else:
            files[copy.path] = copy.data  # type: ignore[attr-defined]
    return files, blobs


def admit(store: LibraryStore, current: Mapping[str, bytes], files: Mapping[str, bytes], blobs: Mapping[str, int],
          blocked: Iterable[str], paths: Iterable[str], preflight: Callable[[object, str], BaseException | None],
          context: Callable[[object], None] | None = None) -> tuple[list[Delete], dict[str, tuple[str, str]]]:
    """Row 9's deletions on a device (028): those it can make, and ``{path: (code, why)}`` for the
    rest. Held back as ``delete_dependency`` while anything left there needs them (``orphaned``), or
    ``unapplicable`` when the store refuses one (no slot for its hold, or a refused check), to a
    fixpoint: a deletion held back keeps a record whose own needs then count.

    ``context`` stages what the same commit also writes (the device's copies), so the store judges
    each deletion over what that commit leaves: a manifest copy that drops a head no longer pins the
    revision deleted with it. Ignored if it is refused alone (the plan's combined check reports it)."""
    refused: dict[str, tuple[str, str]] = {}
    candidates: dict[str, Delete] = {}
    paths = list(paths)
    if not paths:
        return [], refused  # nothing to judge: no record is read
    for path in paths:
        data = current[path]
        try:
            candidates[path] = Delete(path, data, hold_slot(store, path, data))
        except (StoreError, OSError) as error:  # no slot can take the hold: the record stays
            refused[path] = ("unapplicable", f"its version cannot be kept here: {error}")
    if context is not None:
        txn = store.transaction()
        context(txn)
        if not txn.staged() or preflight(txn, "the planned copies") is not None:
            context = None

    def check(deletes: list[Delete], what: str) -> BaseException | None:
        txn = store.transaction()
        if context is not None:
            context(txn)
        stage(txn, deletes)
        return preflight(txn, what)

    blocked = list(blocked)
    needs: dict[str, _Needs] = {}  # the same ``files`` each pass: each record is read once
    while True:
        for path, why in orphaned(files, blobs, blocked, candidates, needs).items():
            refused[path] = ("delete_dependency", why)
            del candidates[path]
        if not candidates or check(list(candidates.values()), "the deletions") is None:
            break
        # Split only on failure, greedily and in passes: each deletion joins those already admitted
        # if the store takes them together. Never one at a time alone: a source deleted as a whole
        # (its manifest, its revisions, the records that used them) is refused piece by piece.
        admitted: list[Delete] = []
        waiting = sorted(candidates)
        errors: dict[str, BaseException] = {}
        progress = True
        while waiting and progress:
            progress = False
            for path in list(waiting):
                error = check([*admitted, candidates[path]], path)
                if error is None:
                    admitted.append(candidates[path])
                    waiting.remove(path)
                    progress = True
                else:
                    errors[path] = error
        for path in waiting:
            refused[path] = ("unapplicable", str(errors[path]))
            del candidates[path]
        # again: what was refused stays, and its needs may hold back more (or all admitted pass together)
    return [candidates[path] for path in sorted(candidates)], refused


def denied(deletes: Iterable[Delete], keys: frozenset[str], files: Mapping[str, bytes], blobs: Mapping[str, int],
           blocked: Iterable[str], gone: set[str]) -> tuple[dict[str, str], dict[str, bytes | None]]:
    """In a data commit's ``on_denied``: a deletion whose own write or hold was denied is dropped with
    its hold, and so is one that what a denial left behind (an older version of a dropped copy) still
    needs. Adds each to ``gone``; returns why each newly dropped one is ``denied``, and the puts to drop."""
    deletes = list(deletes)
    refused: dict[str, str] = {}
    if not deletes:
        return refused, {}
    for delete in deletes:
        if delete.path not in gone and any(key in keys for key in keys_of(delete)):
            gone.add(delete.path)
            refused[delete.path] = "not authorized"
    standing = [delete.path for delete in deletes if delete.path not in gone]
    for path, why in orphaned(files, blobs, blocked, standing).items():
        gone.add(path)
        refused[path] = f"depends on a write that was not authorized: {why}"
    changes: dict[str, bytes | None] = {}
    for delete in deletes:
        if delete.path in gone:
            changes.update({key: None for key in keys_of(delete) if key not in keys})
    return refused, changes


def still_needed(store: LibraryStore, writes: Mapping[str, bytes], blob_sizes: Mapping[str, int],
                 paths: Iterable[str]) -> dict[str, str]:
    """``orphaned`` again over the device as it is now, under the lock of the commit that deletes
    (a fresh scan, with that commit's own record and blob writes): the plan read the device earlier,
    and a record written there since (a new chat in the campaign whose scope goes) may need what is
    about to go. The caller stops that commit; the next sync holds the deletion back."""
    paths = list(paths)
    if not paths:
        return {}
    found = store.scan()
    return orphaned({**found.files, **writes}, {**found.blobs, **blob_sizes}, found.blocked, paths)


def commit_holds(store: LibraryStore, view, device: str, peer: str, devices: tuple[str, str],  # type: ignore[no-untyped-def]
                 deletes: Iterable[Delete]) -> tuple[bool, tuple[str, bytes, str | None] | None]:
    """holds(d): before the data commit that deletes on ``device``, its kept-version records hold each
    removed version (the pair's, and no other pair's lists it as delivered), in a commit of its own,
    as ``resolve`` records a hold. Staged inside the data commit, 010's recovery could finish the
    removal and its hold without that ``.sync`` record, and a slot the hold reused (bytes once
    delivered) would stay dismissable though it may be the only copy left anywhere. Returns this
    pair's record as committed (the step a peer transfer replays), if it changed, after whether anything
    was committed (other pairs' records may change alone); ``StaleWrite`` or
    ``WriteConflict`` if a record changed meanwhile (nothing written)."""
    states = holds_state(store, view, device, peer, devices, deletes)
    if not states:
        return False, None
    txn = store.transaction()
    bookkeeping.stage(txn, states)
    txn.commit(None)
    own = keeps_name(peer)
    return True, next((state for state in states if state[0] == own), None)


# -- what a record left on the receiver needs ------------------------------------------------------

@dataclass
class _Needs:
    files: set[str] = field(default_factory=set)  # exact files it names or lives under
    entities: set[tuple[str, str, str]] = field(default_factory=set)  # unpinned references: some revision
    sources: set[str] = field(default_factory=set)  # sources it reads or belongs to: their manifest
    campaigns: set[str] = field(default_factory=set)  # campaigns it belongs to: a scope or an instance


def _strings(value: object) -> Iterator[str]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, Mapping):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def _file_of(ref: object) -> str | None:
    try:
        if isinstance(ref, SourceRef):
            return "/".join(definition_parts(ref))
        return "/".join(address_parts(ref))  # type: ignore[arg-type]
    except (ValueError, UnstorableIdentifier, TypeError, KeyError):
        return None  # a reference no library can store names nothing that could exist


def needs_of(path: str, data: bytes, files: Mapping[str, bytes], blobs: Mapping[str, int]) -> _Needs:
    """Everything the record at ``path`` can need on a device holding ``files``: every reference any
    of its texts parses as (pinned or not), its store's own pins (a manifest's heads), what its folder
    belongs to, the chat a page or turn is history of, a chat's scope, and 018 art's needs."""
    needs = _Needs()
    parts = tuple(path.split("/"))
    try:
        document = parse_strict(data)
    except JsonError:
        document = None
    for text in _strings(document):
        for kind in _REFS:
            try:
                ref = kind.parse(text)  # type: ignore[attr-defined]
            except (ValueError, TypeError):
                continue
            if isinstance(ref, SourceRef):
                needs.sources.add(ref.source)
                if not ref.pinned:
                    needs.entities.add(ref.entity_key)
                    continue
            if isinstance(ref, ScopeRef):
                needs.campaigns.add(ref.campaign_id)
                continue
            target = _file_of(ref)
            if target is not None:
                needs.files.add(target)
    try:
        needs.files |= {"/".join(definition_parts(ref)) for ref in pinned_by(parts, data)}
    except (StoreError, ValueError, UnstorableIdentifier):
        pass  # not a record pinned_by reads: its texts above are all it can name
    found = classify(parts)
    if parts[0] == SOURCES and found is not None and found[0] != "manifest":
        source = found[1] if isinstance(found[1], str) else getattr(found[1], "source", None)
        if source is None and found[0] in ("image", "placement"):
            source = _art_source(found[1])
        if source is not None:
            needs.sources.add(source)  # a revision or source art needs its manifest
    if parts[0] == CAMPAIGNS and len(parts) > 2:
        needs.campaigns.add(parts[1])
        kind = syncable(path) or (found[0] if found is not None else None)
        if kind == "chat":
            needs.files.add(f"{CAMPAIGNS}/{parts[1]}/{SCOPE}")
        elif kind == "transcript":
            target = _file_of_chat(parts[1], parts[3])  # a page is history of its chat
            if target is not None:
                needs.files.add(target)
        elif kind == "turn" and found is not None and isinstance(found[1], TurnRef):
            chat = found[1].turn_id.rsplit(".", 1)[0]  # ``<chat_id>.<n>[-tag]``: the chat it was played in
            target = _file_of_chat(parts[1], chat)
            if target is not None:
                needs.files.add(target)
    if syncable(path) in ("image", "placement"):
        try:
            art = art_needs(path, data, files, blobs)
        except (ValueError, KeyError, TypeError, StoreError):
            art = None
        if art is not None:
            needs.files |= set(art.identical) | set(art.present)
            if art.campaign is not None:
                needs.campaigns.add(art.campaign)
    needs.files.discard(path)
    return needs


def _art_source(ref: object) -> str | None:
    if isinstance(ref, ImageRef):
        return ref.source
    subject = getattr(ref, "subject", None)
    return getattr(subject, "source", None)


def _file_of_chat(campaign: str, chat: str) -> str | None:
    try:
        return "/".join(chat_parts(ChatRef(campaign, chat)))
    except (ValueError, UnstorableIdentifier):
        return None


def _provides(path: str) -> tuple[str, object] | None:
    """What a deleted file was the only possible provider of, beyond its own path."""
    found = classify(tuple(path.split("/")))
    if found is None:
        return None
    kind, ref = found
    if kind == "definition":
        return "entity", ref.entity_key  # type: ignore[union-attr]
    if kind == "manifest":
        return "source", ref
    if kind in ("scope", "instance"):
        return "campaign", path.split("/")[1]
    return None


def orphaned(files: Mapping[str, bytes], blobs: Mapping[str, int], blocked: Iterable[str],
             deleting: Iterable[str], cache: dict[str, _Needs] | None = None) -> dict[str, str]:
    """Of ``deleting``, the deletions to hold back on a device that would then hold ``files`` (its
    records with this commit's copies, before any deletion) and why: the fixpoint of every record left
    still needing one. A path blocked there (unreadable, a link, a look-alike) under ``sources`` or
    ``campaigns``, or the library root itself, may be any record, so it holds every deletion back:
    nothing can prove it does not need the deleted record. The root's other entries (``assets``, a
    stray link or note) never hold records, as for the store's own reference check."""
    pending = set(deleting)
    if not pending:
        return {}
    unknown = sorted(path for path in blocked if path == "" or name_key(path.split("/")[0]) in (SOURCES, CAMPAIGNS))
    if unknown:
        return {path: f"cannot prove nothing on this device needs it: {unknown[0]} cannot be read as a record"
                for path in pending}
    cache = {} if cache is None else cache  # one record's needs, for callers judging the same ``files`` again
    held: dict[str, str] = {}
    while True:
        remaining = [path for path in files if path not in pending]
        need_files: dict[str, str] = {}
        need: dict[str, dict[object, str]] = {"entity": {}, "source": {}, "campaign": {}}
        provided: dict[str, set[object]] = {"entity": set(), "source": set(), "campaign": set()}
        for path in sorted(remaining):
            if path not in cache:
                cache[path] = needs_of(path, files[path], files, blobs)
            found = cache[path]
            for target in found.files:
                need_files.setdefault(target, path)
            for token in found.entities:
                need["entity"].setdefault(token, path)
            for token in found.sources:
                need["source"].setdefault(token, path)
            for token in found.campaigns:
                need["campaign"].setdefault(token, path)
            provides = _provides(path)
            if provides is not None:
                provided[provides[0]].add(provides[1])
        newly = {}
        for path in sorted(pending):
            if path in need_files:
                newly[path] = f"{need_files[path]} still names it"
                continue
            provides = _provides(path)
            if provides is None:
                continue
            what, token = provides
            if token in need[what] and token not in provided[what]:
                newly[path] = f"{need[what][token]} still needs it ({_why[what]})"
        if not newly:
            return held
        held.update(newly)
        pending -= set(newly)


_why = {"entity": "an unpinned reference to its entity", "source": "a record of or reference into its source",
        "campaign": "a record of its campaign, which would have no scope or instance left"}


def holds_state(store: LibraryStore, view, device: str, peer: str, devices: tuple[str, str],  # type: ignore[no-untyped-def]
                deletes: Iterable[Delete]) -> list[kept_record.State]:
    """The kept-version records to commit before a data commit that deletes ``deletes``: the pair's
    record holding each removed version, and every other pair's record without it delivered (exactly
    what ``resolve`` records before its hold), so ``dismiss`` never takes it for a peer's copy."""
    items = sorted({(delete.path, delete.expected) for delete in deletes})
    states = []
    held = kept_record.with_holds(view, peer, devices, items)
    if held is not None:
        states.append(held)
    states += kept_record.not_delivered_elsewhere_many(store, device, peer, items)
    return states


__all__ = ["Delete", "admit", "commit_holds", "denied", "final_files", "hold_slot", "holds_state", "keys_of", "needs_of",
           "orphaned", "stage"]
