"""A campaign's ledger as one sync unit (028): merged with ``grimoire.ledgermerge``, never decided file by file.

A campaign's **ledger unit** is its 021 accepted events and wrap-ups and its 023 holder files, plus its 017
relations when either device holds an accepted event (``model.HOLDS_RELATIONS``). Where both devices hold
every file of a unit identically there is nothing to do. Otherwise the unit is merged:

- each device's files are decoded as that device's ledger, and the **base** is what the pair last agreed
  on (``unit_bases``), with its exact bytes from whichever device still holds them, else from the pair's
  **base cache** (``.sync/peers/<peer>/bases/<key>.json``, keyed by a digest of the path, written with the
  archive whenever an agreement on a ledger file changes, and left out where this system cannot store it);
- ``merge_ledger`` gives either the files both devices store, which travel as ordinary copies to each
  device lacking them, all of a unit or none of it, or a review, which is a ``merge_review`` conflict on
  every file of the unit the devices hold differently, carrying the review's items.

The base is chosen for the unit as a whole, so its files never mix generations. A file's archived
agreement is a generation both devices held. Where the two archives disagree (a crash between their
commits), the newer entry is used: the one an unfinished sync's marker names, else the one whose bytes are
at hand, else the lesser etag, never "the first device's", so the order of the pair never changes the merge
(the merged files' ``previous`` is the base's). A sync stopped before its archives leaves markers whose
``to`` is newer still; a merged file's ``to`` was held by neither device before it landed, so it is the
base only where the merge landed on both devices (each device's marker names it, or the device holds it).
A merge only one device took is merged again from the generation before, which gives the same files (the
merge is idempotent over the side that already took it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.identity import HoldingsRef, InstanceRef
from grimoire.effects import accepted_from_payload, wrapup_from_payload
from grimoire.graph.codec import relation_from_payload
from grimoire.graph.model import Relation
from grimoire.holdings import HOLDINGS_SCHEMA, Holdings, LogEntry, entry_to_payload, holdings_from_payload, holdings_to_payload
from grimoire.ledgermerge import (
    BASE, Kept, Ledger, LedgerReview, Merged, Review, ReviewItem, Rewrite, Version, merge_ledger,
)
from grimoire.store import ScopeRecord, StoreError, etag
from grimoire.store.envelope import parse_scope_record, scope_record_document
from grimoire.store.jsonfmt import parse_strict, render
from grimoire.store.layout import SYNC_BASES, SYNC_PEERS, address_parts, classify

from .decide import archive_candidate, derive_base
from .documents import syncable
from .model import FIRST, LEDGER_KINDS, UNCERTAIN, Base

#: The folders of a campaign a ledger unit is read from; one of them that cannot be listed, in any
#: spelling, stops that campaign's merge (what it holds is unknown).
LEDGER_FOLDERS = frozenset({"accepted", "wrapups", "holdings", "relations"})
_DETAIL = 300


def base_name(peer: str, path: str) -> str:
    """The base cache entry of ``path`` on a device, for its pair with ``peer``: keyed by a digest of the
    path, so its length never depends on the path's (``layout.SYNC_BASES``). Its bytes are used only where
    their etag is the agreed one, so two paths sharing a key (never, in practice) only lose a base."""
    return f"{SYNC_PEERS}/{peer}/{SYNC_BASES}/{hashlib.sha256(path.encode('utf-8')).hexdigest()[:32]}.json"


def unit_of(path: str, campaigns: set[str]) -> str | None:
    """The campaign whose ledger unit ``path`` belongs to, or ``None``. ``campaigns`` are those whose
    relations are in their unit (``session._held_campaigns``: any accepted event, failing closed)."""
    kind = syncable(path)
    if kind in LEDGER_KINDS:
        return path.split("/")[1]
    if kind == "relation":
        campaign = path.split("/")[1]
        if campaign in campaigns or "*" in campaigns:
            return campaign
    return None


def is_unit_path(path: str) -> bool:
    """Whether ``path`` can be part of a ledger unit (a relation only in some campaigns)."""
    return syncable(path) in LEDGER_KINDS or syncable(path) == "relation"


class _Sizer:
    """A merged holder file's exact size as the store renders it (as 023's own adapter sizes it)."""

    def document(self, holdings: Holdings, owner: str | None, extra: Mapping[str, object],
                 previous: str | None) -> int:
        record = ScopeRecord(HoldingsRef.of(holdings.holder), HOLDINGS_SCHEMA, holdings_to_payload(holdings),
                             owner=owner, extra=extra)
        return len(render(scope_record_document(record, previous)))

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        text = json.dumps(entry_to_payload(holdings.holder, entry), sort_keys=True, indent=2, ensure_ascii=False)
        return len(text.replace("\n", "\n      ").encode("utf-8")) + 8  # six spaces deep, as 023's adapter


@dataclass(frozen=True)
class UnitResult:
    """What the plan does with one unit: ``files`` (the merged bytes, by path) or ``review``."""

    campaign: str
    paths: tuple[str, ...]
    differ: tuple[str, ...]
    files: Mapping[str, bytes] | None = None
    review: Review | None = None


def unit_bases(plan, paths: Iterable[str]) -> dict[str, Base]:  # type: ignore[no-untyped-def]
    """Each file's base for one ledger unit, chosen for the unit as a whole (see the module doc).

    A file's archived agreement (``_archived``) is a generation both devices held. A sync stopped before
    its archives leaves markers whose ``to`` is newer: for a merged file, bytes neither device held before
    they landed. Those are the unit's base only where the merge landed on **both** devices: every marked
    file is either named by each device's marker or held by it now. Then every marked file merges from its
    ``to`` and every other from its archived agreement. Otherwise (a stop between the data commits) no
    ``to`` is: every file merges from the generation before (its archived agreement, else the one its
    marker was written over, else none), so the files of one unit never mix generations (an operation
    agreed in one holder file and new in another would read as half of one), and merging the device that
    took the merge with the one that did not gives the same files again (it is idempotent over the side
    that already took it). An unreadable or uncertain marker leaves that file's agreement unknown.

    A removal's marker (``{"from": E, "to": null}``, a propagated deletion of a relation before its campaign
    had a unit, 028) is no merged file: its base is what ``derive_base`` makes of it (``deleted E`` once the
    deletion verifiably landed, else uncertain), and it never decides whether the unit's merge landed.
    """
    paths = tuple(paths)
    archived = {path: _archived(plan, path) for path in paths}
    unknown = {path for path in paths if any(
        plan.pendings[device].state == "ignored" or (plan.pendings[device].entries.get(path) or {}).get("uncertain")
        for device in plan.devices)}
    removed = {path: _removal_base(plan, path) for path in paths
               if path not in unknown and any(_is_removal(entry) for entry in _entries(plan, path).values())}
    fresh: dict[str, str | None] = {}
    landed = True
    for path in paths:
        entries = _entries(plan, path)
        if path in unknown or path in removed or not entries:
            continue
        targets = {entry.get("to") for entry in entries.values()}
        to = next(iter(targets)) if len(targets) == 1 else None
        if to is not None and archived[path] is not None and archived[path].etag == to:
            continue  # stale: the archives already agree on what it published
        fresh[path] = to
        landed = landed and to is not None and all(
            device in entries or (plan.sides[path][device].state == "present" and plan.sides[path][device].etag == to)
            for device in plan.devices)
    bases: dict[str, Base] = {}
    for path in paths:
        if path in unknown:
            bases[path] = UNCERTAIN
        elif path in removed:
            bases[path] = removed[path]
        elif landed and fresh.get(path) is not None:
            bases[path] = Base("agreed", fresh[path])  # type: ignore[arg-type]
        elif archived[path] is not None:
            bases[path] = archived[path]  # type: ignore[assignment]
        else:
            bases[path] = _over(plan, path)
    return bases


def _is_removal(entry: Mapping[str, object]) -> bool:
    return "to" in entry and entry["to"] is None and entry.get("from") is not None


def _removal_base(plan, path: str) -> Base:  # type: ignore[no-untyped-def]
    """The base a removal's marker gives ``path``, exactly as a path outside a unit reads it."""
    devices = plan.devices
    return derive_base(path, tuple(plan.archives[device] for device in devices),  # type: ignore[arg-type]
                       tuple(plan.pendings[device] for device in devices),  # type: ignore[arg-type]
                       tuple(plan.sides[path][device].etag if plan.sides[path][device].state == "present" else None
                             for device in devices))  # type: ignore[arg-type]


def unit_base(plan, path: str) -> Base:  # type: ignore[no-untyped-def]
    """One file's base, as its whole unit chooses it (``unit_bases``)."""
    campaign = plan.units.get(path)
    paths = [other for other, unit in plan.units.items() if unit == campaign] if campaign is not None else [path]
    return unit_bases(plan, paths)[path]


def _entries(plan, path: str) -> dict[str, Mapping[str, object]]:  # type: ignore[no-untyped-def]
    return {device: entry for device in plan.devices if (entry := plan.pendings[device].entries.get(path)) is not None}


def _archived(plan, path: str) -> Base | None:  # type: ignore[no-untyped-def]
    """The agreement the archives record for ``path``, or ``None`` where they record none."""
    a, b = plan.devices
    left, right = plan.archives[a], plan.archives[b]
    candidate = archive_candidate(path, left, right)
    if candidate.kind in ("agreed", "deleted"):
        return candidate
    if candidate.kind == "uncertain" and left.state == right.state == "valid":
        held = [Base(*entry) for entry in (left.entries.get(path), right.entries.get(path)) if entry]  # type: ignore[arg-type]
        if held:  # the archives disagree (a crash between their commits): both are agreements the pair had
            return held[0] if len(held) == 1 else _newer(plan, path, *held)
    return None


def _over(plan, path: str) -> Base:  # type: ignore[no-untyped-def]
    """Where no archive records an agreement: the one the file's markers were written over, else none."""
    candidate = archive_candidate(path, *(plan.archives[device] for device in plan.devices))
    entries = _entries(plan, path)
    if not entries:
        return candidate
    over = {entry.get("base") for entry in entries.values()}
    if len(over) == 1 and None not in over:
        return Base("agreed", over.pop())  # type: ignore[arg-type]
    return candidate if over == {None} else UNCERTAIN  # written over no agreement; or over different ones


def _newer(plan, path: str, one: Base, other: Base) -> Base:  # type: ignore[no-untyped-def]
    """Of two archived agreements on ``path``, the one to merge from, whichever device is first.

    Each archive is written only once both devices verifiably held its entry, so either is a common
    ancestor of what the devices hold now; the newer gives the files an uninterrupted sync would have.
    The newer is the one an unfinished sync's marker names as its ``to``; failing that, the one whose
    bytes are still at hand (else the merge would stop for want of a base); failing that, the lesser
    etag, so that sync(a, b) and sync(b, a) merge from the same base.
    """
    targets = {entry.get("to") for device in plan.devices
               for entry in (plan.pendings[device].entries.get(path),) if entry is not None}
    choices = (one, other)
    for keep in (lambda base: base.etag in targets,
                 lambda base: base.kind == "deleted" or base_bytes(plan, path, base.etag) is not None):
        found = [base for base in choices if keep(base)]
        if len(found) == 1:
            return found[0]
    return min(choices, key=lambda base: (base.kind, base.etag))


def base_bytes(plan, path: str, tag: str) -> bytes | None:  # type: ignore[no-untyped-def]
    """The agreed bytes of ``path`` (etag ``tag``): a device's own file, else either device's base cache."""
    for device in plan.devices:
        data = plan.scans[device].files.get(path)
        if data is not None and etag(data) == tag:
            return data
    for device in plan.devices:
        try:
            data = plan.stores[device].read_sync_state(base_name(plan.other(device), path))
        except (StoreError, OSError, ValueError):
            continue  # an unreadable cache entry is no base; the merge then stops for review
        if data is not None and etag(data) == tag:
            return data
    return None


def plan_unit(plan, campaign: str, paths: Iterable[str], choice: str | None = None,  # type: ignore[no-untyped-def]
              bases: Mapping[str, Base] | None = None) -> UnitResult:
    """Merge one campaign's ledger unit as the plan's two devices hold it.

    ``choice`` is an owner's decision (``resolve_ledger``): ``"merge"`` takes the merge a policy-only
    review would make (its ``candidate``); a device id takes that device's ledger files as they stand.
    """
    paths = tuple(sorted(paths))
    differ = tuple(path for path in paths if not _same(plan, path))
    if not differ:
        return UnitResult(campaign, paths, differ)
    items: list[ReviewItem] = list(_unlistable(plan, campaign))
    if choice in plan.devices:
        return _taken(plan, campaign, paths, differ, choice, items)
    for path in paths:
        for device in plan.devices:
            side = plan.sides[path][device]
            if side.state == "blocked":
                items.append(ReviewItem("unreadable", path, f"{device}: {side.reason}"[:_DETAIL]))
    if items:
        return UnitResult(campaign, paths, differ, review=Review(tuple(items)))
    sides = {device: _ledger(campaign, {path: plan.scans[device].files.get(path) for path in paths}, items, device)
             for device in plan.devices}
    found: dict[str, bytes | None] = {}
    bases = unit_bases(plan, paths) if bases is None else bases
    for path in paths:
        found[path] = _base_file(plan, path, bases[path], items)
    base = _ledger(campaign, found, items, "base")
    if items:
        return UnitResult(campaign, paths, differ, review=Review(tuple(items)))
    instances = {ref for device in plan.devices for ref in _instances(plan.scans[device].files, campaign)}
    try:
        result = merge_ledger(base, sides, instances=instances, sizer=_Sizer())  # type: ignore[arg-type]
    except ValueError as error:  # inputs the merge refuses outright (an id of another campaign, say)
        return UnitResult(campaign, paths, differ, review=Review((ReviewItem("invalid", campaign, str(error)[:_DETAIL]),)))
    if isinstance(result, LedgerReview) and choice == "merge" and result.candidate is not None:
        return UnitResult(campaign, paths, differ, files=merged_bytes(plan, result.candidate, found))
    if isinstance(result, Review):
        return UnitResult(campaign, paths, differ, review=result)
    return UnitResult(campaign, paths, differ, files=merged_bytes(plan, result, found))


def _taken(plan, campaign: str, paths: tuple[str, ...], differ: tuple[str, ...], device: str,  # type: ignore[no-untyped-def]
           items: list[ReviewItem]) -> UnitResult:
    """``device``'s ledger files as they stand, for both devices. There is no delete here (021 and 023 have
    none), so a file only the other device holds stops it: revoke that event on that device first."""
    other = plan.other(device)
    for path in paths:
        for who in plan.devices:
            if plan.sides[path][who].state == "blocked":
                items.append(ReviewItem("unreadable", path, f"{who}: {plan.sides[path][who].reason}"[:_DETAIL]))
        if plan.sides[path][other].state == "present" and plan.sides[path][device].state == "absent":
            items.append(ReviewItem("not_on_kept_device", path, f"only {other} holds it, and a ledger file is never "
                                    "deleted: revoke it there first"))
    if items:
        return UnitResult(campaign, paths, differ, review=Review(tuple(items)))
    files = {path: plan.scans[device].files[path] for path in paths if plan.sides[path][device].state == "present"}
    return UnitResult(campaign, paths, differ, files=files)


def token(plan, campaign: str, paths: Iterable[str] | None = None) -> str:  # type: ignore[no-untyped-def]
    """What a review was reported on: every file of the unit (``paths``, when the caller has them), by path
    and etag, on both devices."""
    if paths is None:
        paths = [path for path, unit in plan.units.items() if unit == campaign]
    lines = [f"{device} {path} {plan.sides[path][device].etag or plan.sides[path][device].state}"
             for path in sorted(paths) for device in plan.devices]
    return etag("\n".join(lines).encode("utf-8"))


def plan_units(plan) -> None:  # type: ignore[no-untyped-def]
    """Merge every ledger unit of the plan: copies to each device lacking the merged files, or reviews."""
    from .session import Conflict, Copy  # session imports this module
    groups: dict[str, list[str]] = {}
    for path, campaign in plan.units.items():
        groups.setdefault(campaign, []).append(path)
    for device in plan.devices:
        plan.unit_copies.setdefault(device, {})
    for campaign in sorted(groups):
        # the unit's base is every file's base, for the bookkeeping too: a file's own ``derive_base`` may
        # be uncertain where the unit's is definite (a stop between the data commits), and a pending marker
        # recording that uncertainty would leave the unit with no base at the next sync
        bases = unit_bases(plan, groups[campaign])
        plan.bases.update(bases)
        result = plan_unit(plan, campaign, groups[campaign], plan.choices.get(campaign), bases)
        for path in result.paths:
            if path not in result.differ and plan.sides[path][plan.devices[0]].state != "blocked":
                plan.agreed.add(path)  # as ``decide`` agrees a path both devices hold identically
        if result.review is not None:
            reasons = ", ".join(sorted(result.review.reasons))
            named = token(plan, campaign, groups[campaign])  # once per unit: it lists every file of the unit
            for path in result.differ or result.paths[:1]:
                plan.conflicts[path] = Conflict(path, "merge_review", f"campaign {campaign}'s ledger merge needs "
                                                f"review: {reasons}", review=result.review.items, token=named)
            continue
        if result.files is None:
            continue
        stray = sorted(path for path in result.paths if path not in result.files and any(
            plan.sides[path][device].state == "present" for device in plan.devices))
        if stray:  # a merge never deletes a file: one missing from it is a fault, never a deletion
            raise AssertionError(f"campaign {campaign}'s merge leaves out {stray}")
        for device in plan.devices:
            for path, data in result.files.items():
                side = plan.sides[path][device] if path in plan.sides else None
                current = side.etag if side is not None and side.state == "present" else None
                if current == etag(data):
                    continue
                plan.copies[device].append(Copy(path, plan.other(device), device, data, current, unit=campaign))
                plan.unit_copies[device].setdefault(campaign, set()).add(path)
                plan.units.setdefault(path, campaign)


def whole(plan, device: str) -> None:  # type: ignore[no-untyped-def]
    """After admission: a unit only part of which may land on ``device`` lands not at all there."""
    from .session import Conflict
    planned = plan.unit_copies.get(device, {})
    if not planned:
        return
    landing = {copy.path for copy in plan.copies[device]}
    broken = {campaign: sorted(paths - landing) for campaign, paths in planned.items() if paths - landing}
    if not broken:
        return
    kept = []
    for copy in plan.copies[device]:
        if copy.unit in broken:
            missing = broken[copy.unit]
            why = plan.conflicts[missing[0]].detail if missing[0] in plan.conflicts else "it was not admitted"
            plan.conflicts[copy.path] = Conflict(copy.path, "unapplicable", f"campaign {copy.unit}'s merged ledger "
                                                 f"cannot land whole on {device}: {missing[0]}: {why}"[:_DETAIL * 2])
        else:
            kept.append(copy)
    plan.copies[device] = kept


def admit(store, copies: list, stage, preflight) -> tuple[list, list]:  # type: ignore[no-untyped-def]
    """Admit each ledger unit among ``copies`` as one whole: all of a campaign's merged files, or none."""
    by_unit: dict[str, list] = {}
    for copy in copies:
        if copy.unit is not None:
            by_unit.setdefault(copy.unit, []).append(copy)
    admitted, rejected = [], []
    for campaign in sorted(by_unit):
        txn = store.transaction()
        stage(txn, by_unit[campaign], [])
        error = preflight(txn, f"campaign {campaign}'s merged ledger")
        if error is None:
            admitted += by_unit[campaign]
        else:
            rejected += [(copy, error) for copy in by_unit[campaign]]
    return admitted, rejected


def denied(copies: Iterable, keys: Iterable[str]) -> set[str]:  # type: ignore[type-arg]
    """The units a commit's denial breaks: one denied file of a unit takes all of it out of that commit."""
    keys = set(keys)
    return {copy.unit for copy in copies if copy.unit is not None and copy.path in keys}


def base_states(plan, device: str, entries: Mapping[str, tuple[str, str]], published: Iterable) -> list:  # type: ignore[no-untyped-def]
    """The base cache writes of ``device``'s archive commit: each ledger file whose agreement changes there.

    Written in the same commit as the archive that records the agreement, so the cache and the archive
    never disagree. The bytes are the device's own file, which the archive step found identical on both.
    """
    if not plan.stores[device].sync_state_fits(base_name(plan.other(device), "")):
        return []  # every entry's name has one length: none fits on this system (a merge may then need review)
    view = plan.archives[device]
    old = view.entries if view.state == "valid" else {}
    copied = {copy.path: copy.data for copy in published}
    states = []
    for path, entry in sorted(entries.items()):
        if entry[0] != "agreed" or old.get(path) == entry or not is_unit_path(path):
            continue
        data = copied.get(path, plan.scans[device].files.get(path))
        if data is None or etag(data) != entry[1]:
            continue  # never cache bytes the agreement does not name
        name = base_name(plan.other(device), path)
        current = plan.stores[device].read_sync_state(name)
        if current != data:
            states.append((name, data, None if current is None else etag(current)))
    return states


def changed(plan, copies: Iterable, published: Mapping[str, Iterable]) -> str | None:  # type: ignore[no-untyped-def]
    """Why a merge this commit lands is stale, read under the sync's locks just before it commits, or ``None``.

    Each copy's own compare-and-swap guards the files it writes; this guards the rest of each unit it
    merged: a ledger file either device added or changed since the plan read it (an event accepted on
    one device mid-sync, say) means the merge no longer covers both ledgers, so nothing of it lands.
    Over the transport the serving device makes the same check of its own unit (``stale_listing``).
    """
    for campaign in sorted({copy.unit for copy in copies if copy.unit is not None}):
        for device in plan.devices:
            relations, files, blocked = expected_listing(plan, device, campaign, published.get(device, ()))
            try:
                found = unit_listing(plan.stores[device], campaign, relations)
            except OSError as error:
                return f"campaign {campaign}'s ledger on {device} cannot be read again: {error}"
            why = stale_listing(found, files, blocked)
            if why is not None:
                return f"campaign {campaign}'s ledger changed on {device} during the sync: {why}"
    return None


def relations_in_unit(plan, campaign: str) -> bool:  # type: ignore[no-untyped-def]
    """Whether the campaign's relations are part of its ledger unit (it holds an accepted event, or which
    campaigns do is unknown: ``session._held_campaigns`` fails closed)."""
    return campaign in plan.ledger_campaigns or "*" in plan.ledger_campaigns


def of_unit(path: str, campaign: str, relations: bool) -> bool:
    """Whether ``path`` is a file of the campaign's ledger unit, by name (its kind, in the campaign's folder)."""
    parts = path.split("/")
    if not (len(parts) > 3 and parts[0] == "campaigns" and parts[1] == campaign and parts[2] in LEDGER_FOLDERS):
        return False
    kind = syncable(path)
    return kind in LEDGER_KINDS or (relations and kind == "relation")


def expected_listing(plan, device: str, campaign: str, published: Iterable = ()):  # type: ignore[no-untyped-def]
    """The campaign's unit on ``device`` as the plan read it, with this sync's earlier copies there:
    ``(relations, {path: etag}, blocked paths)``. A blocked file is compared by name only (``stale_listing``):
    the scan never read it, and 019 decides it on its own."""
    relations = relations_in_unit(plan, campaign)
    scan = plan.scans[device]
    files = {path: etag(data) for path, data in scan.files.items() if of_unit(path, campaign, relations)}
    files.update({copy.path: copy.etag for copy in published if of_unit(copy.path, campaign, relations)})
    blocked = sorted(path for path in scan.blocked if path not in files and of_unit(path, campaign, relations))
    return relations, files, blocked


def unit_listing(store, campaign: str, relations: bool) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    """The campaign's unit as ``store`` holds it now: each file's etag, ``None`` for one it cannot read."""
    found: dict[str, str | None] = {}
    for folder in sorted(LEDGER_FOLDERS):
        if folder == "relations" and not relations:
            continue
        root = store.root / "campaigns" / campaign / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(store.root).as_posix()
            if not (path.is_symlink() or path.is_file()) or not of_unit(relative, campaign, relations):
                continue
            try:
                found[relative] = None if path.is_symlink() else etag(path.read_bytes())  # a link is never read
            except OSError:
                found[relative] = None
    return found


def step_listings(plan, device: str, copies: Iterable, published: Iterable = ()) -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    """For a data commit that lands merged ledger ``copies`` on ``device``, each unit as the plan read it
    there with this sync's ``published`` earlier copies (``expected_listing``). 028's transport sends them
    with the step, and the serving device checks its own unit against them under its lock before it
    commits (``stale_listing``): the client's own recheck (``changed``) reads only its stand-in."""
    listings = []
    for campaign in sorted({copy.unit for copy in copies if copy.unit is not None}):
        relations, files, blocked = expected_listing(plan, device, campaign, published)
        listings.append({"campaign": campaign, "relations": relations, "files": files, "blocked": blocked})
    return listings


def stale_listing(found: Mapping[str, str | None], files: Mapping[str, str], blocked: Iterable[str]) -> str | None:
    """The first path where a unit as listed now differs from the one expected, or ``None``."""
    blocked = set(blocked)
    names = set(found) ^ (set(files) | blocked)
    moved = names | {path for path, tag in files.items() if path in found and found[path] != tag}
    return min(moved) if moved else None


def merged_bytes(plan, result: Merged, base: Mapping[str, bytes | None]) -> dict[str, bytes]:  # type: ignore[no-untyped-def]
    """The exact bytes of every merged file, by path: a kept version's own, or the rewrite rendered."""
    files: dict[str, bytes] = {}
    for ref, output in result.files.items():
        path = "/".join(address_parts(ref))
        if isinstance(output, Kept):
            data = base.get(path) if output.source == BASE else plan.scans[output.source].files.get(path)
            if data is None or etag(data) != output.etag:
                raise AssertionError(f"the merge kept {path} at {output.etag}, which its source does not hold")
        else:
            assert isinstance(output, Rewrite)
            record = ScopeRecord(ref, output.schema, output.payload, owner=output.owner, extra=output.extra)
            data = render(scope_record_document(record, output.previous))
        files[path] = data
    return dict(sorted(files.items()))


def _same(plan, path: str) -> bool:  # type: ignore[no-untyped-def]
    left, right = (plan.sides[path][device] for device in plan.devices)
    return left.state == right.state != "blocked" and left.etag == right.etag


def _unlistable(plan, campaign: str) -> list[ReviewItem]:  # type: ignore[no-untyped-def]
    """A ledger folder of the campaign (or the campaign, ``campaigns``, the root) that cannot be listed."""
    items = []
    for device in plan.devices:
        for path in plan.scans[device].blocked:
            parts = path.split("/")
            if (path in ("", "campaigns") or (parts[0] == "campaigns" and len(parts) >= 2 and parts[1] == campaign and (
                    len(parts) == 2 or parts[2].casefold() in LEDGER_FOLDERS)) and syncable(path) is None):
                items.append(ReviewItem("unlistable", path or ".", f"{device}: {plan.scans[device].blocked_reason(path)}"[:_DETAIL]))
        for path in plan.scans[device].skipped:  # a look-alike spelling of a ledger folder hides what it holds
            parts = path.split("/")
            if (parts[0] == "campaigns" and len(parts) >= 3 and parts[1] == campaign
                    and parts[2].casefold() in LEDGER_FOLDERS and parts[2] not in LEDGER_FOLDERS):
                items.append(ReviewItem("unlistable", "/".join(parts[:3]), f"{device}: another spelling of a ledger folder"))
    return items


def _base_file(plan, path: str, base: Base, items: list[ReviewItem]) -> bytes | None:  # type: ignore[no-untyped-def]
    if base.kind in ("first", "none"):
        return None
    if base.kind == "agreed":
        data = base_bytes(plan, path, base.etag)  # type: ignore[arg-type]
        if data is not None:
            return data
    if _same(plan, path):  # no known agreement, but both devices hold one version: that is agreed now
        return plan.scans[plan.devices[0]].files.get(path)
    why = {"agreed": "the agreed version is held by neither device nor its base cache",
           "deleted": "the pair agreed this file was deleted", "uncertain": "the pair's last agreement is unknown"}
    items.append(ReviewItem("no_base", path, why.get(base.kind, base.kind)))
    return None


def _ledger(campaign: str, files: Mapping[str, bytes | None], items: list[ReviewItem], who: str) -> Ledger:
    events, wrapups, relations, holdings = {}, {}, {}, {}
    for path, data in sorted(files.items()):
        if data is None:
            continue
        found = classify(tuple(path.split("/")))
        assert found is not None
        kind, ref = found
        try:
            record, _ = _parse(data)
            if record.address != ref:
                raise ValueError(f"the file's address is {record.address}, not {ref}")
            if kind == "accepted":
                events[ref] = _version(accepted_from_payload(ref, record.schema, record.payload), data, record)  # type: ignore[arg-type]
            elif kind == "wrapup":
                wrapups[ref] = _version(wrapup_from_payload(ref, record.schema, record.payload), data, record)  # type: ignore[arg-type]
            elif kind == "relation":
                value = relation_from_payload(ref, record.schema, record.payload)  # type: ignore[arg-type]
                if not isinstance(value, Relation):
                    raise ValueError("a relation of a schema this version cannot merge")
                relations[ref] = _version(value, data, record)
            else:
                holder = InstanceRef(ref.campaign_id, ref.kind, ref.instance_id)  # type: ignore[union-attr]
                holdings[holder] = _version(holdings_from_payload(holder, record.schema, record.payload), data, record)
        except (ValueError, StoreError) as error:
            items.append(ReviewItem("unreadable", path, f"{who}: {error}"[:_DETAIL]))
    return Ledger(campaign, events, wrapups, relations, holdings)  # type: ignore[arg-type]


def _parse(data: bytes):  # type: ignore[no-untyped-def]
    return parse_scope_record(parse_strict(data))


def _version(value: object, data: bytes, record: ScopeRecord) -> Version:
    return Version(value, etag(data), record.owner, MappingProxyType(dict(record.extra)))


def _instances(files: Mapping[str, bytes], campaign: str) -> set[InstanceRef]:
    found = set()
    for path in files:
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == "campaigns" and parts[1] == campaign and syncable(path) == "instance":
            kind = classify(tuple(parts))
            if kind is not None and isinstance(kind[1], InstanceRef):
                found.add(kind[1])
    return found


__all__ = [
    "FIRST",
    "LEDGER_FOLDERS",
    "UNCERTAIN",
    "LedgerReview",
    "UnitResult",
    "base_bytes",
    "base_name",
    "is_unit_path",
    "merged_bytes",
    "plan_unit",
    "stale_listing",
    "step_listings",
    "unit_base",
    "unit_bases",
    "unit_listing",
    "unit_of",
]
