"""Ending a ``merge_review`` (028): an owner's decision on one campaign's ledger, written as a sync is.

``resolve_ledger`` plans the pair again with the decision for that campaign and writes only that
campaign's ledger unit, through ``apply_sync``'s own steps (data, verify, archive, pending), so it is as
crash-safe as a sync and the next sync finds the ledger agreed. Two decisions:

- ``MERGE`` (``grimoire.sync.MERGE``): the merge the review would make once its policy reasons (``ledgermerge.POLICY``: one chat
  on both devices, a revocation the other device built on) are accepted. Nothing is discarded: every
  event of both devices is kept. A review with any other reason has no such merge, and is refused.
- a device id (a ``str``; ``"merge"`` too names a device, never the merge): that device's ledger files as
  they stand, for both devices. The other device's changes
  to this ledger are **discarded**, so only an owner of the library that loses them may decide it (the
  handle of that device's commit must be an owner's). There is no delete in 021 or 023: a file only the
  losing device holds (an event it accepted) is refused; revoke it on that device first.

``token`` is the ``Conflict.token`` the review was reported with: if either device's ledger changed
since, the decision would be about something the owner never saw, so it is refused. A merged file that
names a record its receiver lacks (an instance only the sync's other copies would bring) is refused too:
sync first. This is the only way to decide a review: ``plan_sync`` takes no decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.store import LibraryStore

from .errors import ResolveRefused
from .model import MERGE, LedgerMerge
from .session import SyncPlan, SyncReport, _dependencies, _lock_order, _plan, apply_sync
from . import units as ledger_units


def resolve_ledger(left: LibraryStore, right: LibraryStore, campaign: str, keep: str | LedgerMerge, token: str,
                   handles: Mapping[str, object], *, owners: Mapping[str, Iterable[str]] | None = None,
                   approvals: Mapping[str, Mapping[str, str]] | None = None) -> SyncReport:
    """Decide campaign ``campaign``'s ledger review: ``keep`` is ``MERGE`` or the device id to keep.

    ``owners`` are each device's library owners (its ``DurableAuthority.owners``): keeping a device
    discards the other device's changes, so that device's handle must be an owner's. Returns the
    report of the writes; raises ``ResolveRefused`` with why when the decision cannot be taken.
    """
    plan = plan_resolution(left, right, campaign, keep, token, handles, owners=owners)
    return apply_sync(plan, handles, approvals)


def plan_resolution(left: LibraryStore, right: LibraryStore, campaign: str, keep: str | LedgerMerge, token: str,
                    handles: Mapping[str, object], *, owners: Mapping[str, Iterable[str]] | None = None,
                    decided_by: str | None = None) -> SyncPlan:
    """``resolve_ledger``'s plan, checked: only that campaign's ledger unit is written (028's peer
    transport applies it with its own replay, as it applies a sync's plan). A discard is decided by
    ``decided_by``, or else by the principal of the losing device's handle."""
    if keep is not MERGE and type(keep) is not str:
        raise ResolveRefused(f"keep must be MERGE or a device id, not {keep!r}")
    first, second = _lock_order((left, right))
    with first.cached_reads(), second.cached_reads():
        plan = _plan(left, right, handles, {campaign: keep})
    if keep is not MERGE and keep not in plan.devices:
        raise ResolveRefused(f"keep must be MERGE or one of the devices {list(plan.devices)}, not {keep!r}")
    if campaign not in set(plan.units.values()):
        raise ResolveRefused(f"campaign {campaign} has no ledger files on either device")
    if token != ledger_units.token(plan, campaign):
        raise ResolveRefused(f"campaign {campaign}'s ledger changed on a device since its review was reported; "
                             "sync again to see the review as it stands")
    if keep is not MERGE:  # a device id, whatever it spells: a discard, decided only by an owner
        loser = plan.other(keep)  # type: ignore[arg-type]
        principal = decided_by if decided_by is not None else getattr(handles.get(loser), "principal", None)
        if principal is None or principal not in set((owners or {}).get(loser, ())):
            raise ResolveRefused(f"keeping {keep}'s ledger discards {loser}'s changes to campaign {campaign}'s "
                                 f"ledger; only an owner of {loser}'s library may decide that, on {loser}")
    stuck = sorted(path for path in plan.conflicts if plan.units.get(path) == campaign)
    if stuck:
        found = plan.conflicts[stuck[0]]
        reasons = "; ".join(f"{item.reason} {item.subject}: {item.detail}" for item in found.review[:3]) or found.detail
        raise ResolveRefused(f"campaign {campaign}'s ledger cannot be resolved so: {found.code}: {reasons}")
    for device in plan.devices:  # this campaign's ledger only: every other path waits for the next sync
        plan.copies[device] = [copy for copy in plan.copies[device] if copy.unit == campaign]
        plan.keeps[device] = []
        plan.deletes[device] = []  # no propagated deletion either (and so no holds step): a ledger file has none
        # the ledger was admitted beside the sync's other copies: a record it names that only one of those
        # would have brought (an instance a holder file or an event names) must be on the receiver already
        held = _dependencies(plan, device, plan.copies[device])[1]
        if held:
            path = min(held)
            raise ResolveRefused(f"campaign {campaign}'s ledger cannot be resolved yet: {path} needs on {device} "
                                 f"what is not synchronized there ({held[path]}); sync again first")
    return plan


__all__ = ["plan_resolution", "resolve_ledger"]
