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

_registry_guard = threading.Lock()
_campaign_locks: dict[str, threading.RLock] = {}


def campaign_lock(cid: str) -> threading.RLock:
    """Get-or-create the per-campaign lock atomically -- a plain
    ``if cid not in _campaign_locks: ...`` is a check-then-act race that can
    hand two concurrent first-ever callers different lock objects."""
    with _registry_guard:
        return _campaign_locks.setdefault(cid, threading.RLock())
