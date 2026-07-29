"""Per-campaign serialization: the app's one general-purpose lock domain.

A campaign is the unit of mutual exclusion in grimoire. Everything that
reads-validates-writes campaign-scoped state — scene transcripts, sheets,
audit baselines, roll proposals, the roll log, and the module-pack swap that
can invalidate most of them — serializes on the *same* ``campaign_lock(cid)``.
That unification is deliberate: a module edit holding a campaign's lock must
exclude a proposal derived from the pack it is about to replace, so the two
cannot live in separate lock domains. **This list is the domain**: a
campaign-scoped mutator that keeps a private registry instead is invisible
here and silently outside the exclusion (#255 — ``rolls`` was, until it
joined).

Who takes it:

- every ``scenes`` mutator (``scenes._serialized``) — a scene file is
  rewritten whole, so two unlocked read-modify-writes lose one of them, and
  a transcript is the one thing here that cannot be regenerated (#254);
- every campaign-sheet mutator (``sheets.write``, ``write_creation``,
  ``delete``, ``set_field``, ``advance``);
- ``audit.capture_baseline`` / ``audit.apply_delta``;
- every ``proposals`` state transition, and the routes that wrap a whole
  derive-and-persist span (``routes/streaming.py`` proposal finalizers) so a proposal
  cannot be derived from a pack that is swapped away before it lands;
- every ``rolls`` mutator (``append``, ``find_or_append_by_proposal``,
  ``repoint_scenes``) plus its ``find_by_proposal`` reader — a logged roll
  can carry the id of the proposal it resolved, and proposals are in this
  domain, so the two belong in one (#255);
- ``module_edit`` publication and the world-module rebind route, the only
  actors that hold *every* campaign's lock at once, across the swap;
  ``PUT /campaigns/{cid}/module`` holds just that campaign's;
- read paths that must not observe a half-published pack:
  ``checks.resolve_check``, ``context._mechanics``,
  ``routes.mechanics._continuation_rule_bodies``.

Ordering rules (deadlock avoidance):

- campaign lock -> audit baseline lock, never reversed (``store/audit.py``).
- The module-edit lock is taken *before* campaign locks; multi-campaign
  holders (module publication, world-module rebind) are the only actors
  that hold more than one campaign lock, and no LLM play flow ever holds
  more than its own (``store/module_edit.py``).

The lock is an ``RLock`` so a caller can compose lower-level mutators —
``audit.apply_delta`` calls ``sheets.set_field`` under an already-held lock.
Locks are process-local; the store is single-process by design.
"""

from __future__ import annotations

import threading
import time
from contextlib import ExitStack, contextmanager

_registry_guard = threading.Lock()
_campaign_locks: dict[str, threading.RLock] = {}

# Longer than any legitimate hold, but bounded so a cross-process lock-order
# inversion surfaces as a 409 naming the campaign rather than a wedged server.
# Not a proof: a module migration over a large library on a synced or removable
# filesystem can still exceed it, and its waiter then gets a retryable 409.
LOCK_TIMEOUT = 30.0


class StoreBusy(Exception):
    """Another *process* holds a store lock. One handler maps this to HTTP 409."""

    def __init__(self, name: str, what: str = "resource"):
        super().__init__(f"another grimoire process is editing this {what}")
        self.name = name


class CampaignBusy(StoreBusy):
    def __init__(self, cid: str):
        super().__init__(cid, "campaign")


class ModuleEditBusy(StoreBusy):
    def __init__(self, name: str = "module-edit"):
        super().__init__(name, "module library")


def _remaining(deadline) -> float:
    """RLock treats -1 as "no timeout" and rejects every other negative, so a
    computed remainder must be clamped rather than passed through."""
    if deadline is None:
        return -1
    return max(0.0, deadline - time.monotonic())


def campaign_lock(cid: str) -> threading.RLock:
    """Get-or-create the per-campaign lock atomically -- a plain
    ``if cid not in _campaign_locks: ...`` is a check-then-act race that can
    hand two concurrent first-ever callers different lock objects."""
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.RLock())


@contextmanager
def hold_all(cids):
    """Hold every named campaign lock, in sorted order, under ONE deadline.

    **Sorted order.** The two multi-campaign holders -- ``module_edit.
    _campaign_locks`` and the world-module rebind route -- already agree
    today, but only by accident: ``list_campaigns()`` walks
    ``sorted(base.iterdir())`` and reports the directory name as the id, which
    is the same key the route sorts by. Nothing states that, and one refactor
    of ``list_campaigns`` (sort by name, by ``updated``, drop the sort) turns
    the accident into a cross-process deadlock. Routing both holders through
    here makes the rule explicit and enforced in one place instead of assumed
    in two.

    **One deadline**, not one per lock: applying ``LOCK_TIMEOUT`` to each of N
    locks while holding the earlier ones would give an N x LOCK_TIMEOUT convoy.

    ``ExitStack`` rather than a hand-rolled reversed loop: it registers each
    lock the instant it is acquired, and it runs EVERY registered exit even
    when one of them raises. ``for lock in reversed(held): lock.release()``
    strands every remaining lock the moment one release fails.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    with ExitStack() as stack:
        for cid in sorted(set(cids)):
            lock = campaign_lock(cid)
            if not lock.acquire(timeout=_remaining(deadline)):
                raise CampaignBusy(cid)
            stack.callback(lock.release)
        yield
