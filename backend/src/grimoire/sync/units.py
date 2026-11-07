"""A campaign's ledger as one sync unit (028): merged with ``grimoire.ledgermerge``, never decided file by file.

A campaign's **ledger unit** is its 021 accepted events and wrap-ups and its 023 holder files, plus its 017
relations when either device holds an accepted event (``model.HOLDS_RELATIONS``). Where both devices hold
every file of a unit identically there is nothing to do. Otherwise the unit is merged:

- each device's files are decoded as that device's ledger, and the **base** is what the pair last agreed
  on: for each file the archive entry both devices' archives hold, and its exact bytes from whichever
  device still holds them, else from the pair's **base cache** (``.sync/peers/<peer>/bases/<path>``, written
  with the archive whenever an agreement on a ledger file changes);
- ``merge_ledger`` gives either the files both devices store, which travel as ordinary copies to each
  device lacking them, all of a unit or none of it, or a review, which is a ``merge_review`` conflict on
  every file of the unit the devices hold differently, carrying the review's items.

The base comes from the archives first, not from a pending marker: a merge's bytes are held by neither
device before it lands, so a marker's ``to`` says less than an archive entry. A merge interrupted after
one device took it is merged again from the archived base, which gives the same files (the merge is
idempotent over either side). Where the two archives disagree (a crash between their commits), either
entry is an agreement both devices held, and the newer is used: the one an unfinished sync's marker
names, else the one whose bytes are at hand, else the lesser etag, never "the first device's", so the
order of the pair never changes the merge (the merged files' ``previous`` is the base's). Only where no archive
records an agreement (a first sync, or one interrupted before its archives) is a marker's verified
agreement used, so a file both devices held when it was marked is never taken for a new one.
"""

from __future__ import annotations

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

from .decide import archive_candidate
from .documents import syncable
from .model import FIRST, LEDGER_KINDS, UNCERTAIN, Base

#: The folders of a campaign a ledger unit is read from; one of them that cannot be listed, in any
#: spelling, stops that campaign's merge (what it holds is unknown).
LEDGER_FOLDERS = frozenset({"accepted", "wrapups", "holdings", "relations"})
_DETAIL = 300


def base_name(peer: str, path: str) -> str:
    """The base cache entry of ``path`` on a device, for its pair with ``peer``."""
    return f"{SYNC_PEERS}/{peer}/{SYNC_BASES}/{path}"


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


def unit_base(plan, path: str) -> Base:  # type: ignore[no-untyped-def]
    """The archived agreement a ledger file merges from (never a pending marker's; see the module doc)."""
    a, b = plan.devices
    left, right = plan.archives[a], plan.archives[b]
    candidate = archive_candidate(path, left, right)
    if candidate.kind in ("agreed", "deleted"):
        return candidate
    if candidate.kind == "uncertain" and left.state == right.state == "valid":
        held = [Base(*entry) for entry in (left.entries.get(path), right.entries.get(path)) if entry]  # type: ignore[arg-type]
        if held:  # the archives disagree (a crash between their commits): both are agreements the pair had
            return held[0] if len(held) == 1 else _newer(plan, path, *held)
    # No archived agreement (a first sync, a crash before the archives, a retired archive): a marker's
    # verified agreement (``derive_base``) is the base. Over a merge only one device took, it can only
    # stop the merge for review (that device's files are not the other's plus new ones), never drop.
    return plan.bases[path]  # the archives' candidate with every marker's say: agreed, or uncertain


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


def plan_unit(plan, campaign: str, paths: Iterable[str], choice: str | None = None) -> UnitResult:  # type: ignore[no-untyped-def]
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
    for path in paths:
        found[path] = _base_file(plan, path, items)
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


def token(plan, campaign: str) -> str:  # type: ignore[no-untyped-def]
    """What a review was reported on: every file of the unit, by path and etag, on both devices."""
    lines = [f"{device} {path} {plan.sides[path][device].etag or plan.sides[path][device].state}"
             for path, unit in sorted(plan.units.items()) if unit == campaign for device in plan.devices]
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
        result = plan_unit(plan, campaign, groups[campaign], plan.choices.get(campaign))
        for path in result.paths:
            if path not in result.differ and plan.sides[path][plan.devices[0]].state != "blocked":
                plan.agreed.add(path)  # as ``decide`` agrees a path both devices hold identically
        if result.review is not None:
            reasons = ", ".join(sorted(result.review.reasons))
            for path in result.differ or result.paths[:1]:
                plan.conflicts[path] = Conflict(path, "merge_review", f"campaign {campaign}'s ledger merge needs "
                                                f"review: {reasons}", review=result.review.items,
                                                token=token(plan, campaign))
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
    """
    for campaign in sorted({copy.unit for copy in copies if copy.unit is not None}):
        for device in plan.devices:
            expected = {path: etag(data) for path, data in plan.scans[device].files.items()
                        if _in_unit_folders(path, campaign)}
            expected.update({copy.path: copy.etag for copy in published.get(device, ())
                             if _in_unit_folders(copy.path, campaign)})
            try:
                found = _listed(plan.stores[device], campaign)
            except OSError as error:
                return f"campaign {campaign}'s ledger on {device} cannot be read again: {error}"
            if found != expected:
                moved = min(set(found.items()) ^ set(expected.items()))[0]
                return f"campaign {campaign}'s ledger changed on {device} during the sync: {moved}"
    return None


def _in_unit_folders(path: str, campaign: str) -> bool:
    parts = path.split("/")
    return len(parts) > 3 and parts[0] == "campaigns" and parts[1] == campaign and parts[2] in LEDGER_FOLDERS


def _listed(store, campaign: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    found = {}
    for folder in sorted(LEDGER_FOLDERS):
        root = store.root / "campaigns" / campaign / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(store.root).as_posix()
                if syncable(relative) is not None:
                    found[relative] = etag(path.read_bytes())
    return found


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


def _base_file(plan, path: str, items: list[ReviewItem]) -> bytes | None:  # type: ignore[no-untyped-def]
    base = unit_base(plan, path)
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


__all__ = ["FIRST", "LEDGER_FOLDERS", "UNCERTAIN", "LedgerReview", "UnitResult", "base_bytes", "base_name",
           "is_unit_path", "merged_bytes", "plan_unit", "unit_base", "unit_of"]
