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

That sentence used to be the whole mechanism, and it is why this file had to
admit four mutators sitting outside the domain with nothing to stop a fifth: a
paragraph cannot fail a test run. The domain is now *declared* below, in
``DOMAIN_MODULES`` / ``OUTSIDE_DOMAIN`` / ``UNREVIEWED``, and
``tests/test_lock_domain_guard.py`` holds those declarations and the code to
each other. The prose here explains the design; the constants are what binds.

Who takes it:

- every ``scenes`` mutator (``scenes._serialized``), ``create_scene``
  included — a scene file is
  rewritten whole, so two unlocked read-modify-writes lose one of them, and
  a transcript is the one thing here that cannot be regenerated (#254);
- every campaign-sheet mutator (``sheets.write``, ``write_creation``,
  ``delete``, ``set_field``, ``advance``);
- ``audit.capture_baseline`` / ``audit.apply_delta``;
- every ``proposals`` state transition, and the routes that wrap a whole
  derive-and-persist span (``routes/streaming/_streams.py`` proposal finalizers) so a proposal
  cannot be derived from a pack that is swapped away before it lands;
- every ``rolls`` mutator (``append``, ``find_or_append_by_proposal``,
  ``repoint_scenes``) plus its ``find_by_proposal`` reader — a logged roll
  can carry the id of the proposal it resolved, and proposals are in this
  domain, so the two belong in one (#255);
- ``playing.mark_greeting``'s whole read-scan-write and the recheck-and-mark
  inside ``playing.start_from_greeting``. This closes #318's orphan-clearing
  race outright: ``mark_greeting``'s scan for a stamping scene now shares a
  lock with ``stamp_greeting``, so it can no longer find nothing and clear
  the mark while a concurrent start is mid-flight. The other #318 risk --
  two concurrent starts of the same greeting -- is narrowed, not closed:
  both callers still cast actors, stamp their scene and append the greeting
  body unlocked (the calendar-touching macro expansion in between cannot run
  under this lock, see ``playing.start_from_greeting``), so a loser's scene
  keeps that content even though only one caller now wins the mark and the
  other gets a clean, single-witness ``PlayError``. Before this, both
  callers could win the mark; that half is what changed. ``mark_greeting``'s
  hold spans ``stamping_scene``'s whole sweep -- two frontmatter reads per
  scene -- which blocks every other campaign writer, including an in-flight
  turn's ``append_reply``, for the scan's duration; correctness needs the
  scan inside the lock, so there is no cheaper version, but a large campaign
  on a slow or synced volume could approach ``LOCK_TIMEOUT``;
- ``module_edit`` publication and the world-module rebind route, the only
  actors that hold *every* campaign's lock at once, across the swap;
  ``PUT /campaigns/{cid}/module`` holds just that campaign's;
- read paths that must not observe a half-published pack:
  ``checks.resolve_check``, ``context._mechanics``,
  ``routes.mechanics._continuation_rule_bodies``.

Ordering rules (deadlock avoidance):

    module-edit lock (``module_edit._M``)
      └─ campaign locks, always in sorted cid order  (``hold_all``)
           ├─ audit baseline lock   (``store/audit/baselines.py``)
           └─ rolls lock            (``store/rolls.py``)

- **Every multi-campaign holder goes through** ``hold_all``, which sorts.
  Nothing else may hold more than one campaign lock at a time. The two that
  exist (module publication, the world-module rebind route) did **not** agree
  before ``hold_all``, though this file used to say they did: the route sorted
  by cid and module publication took ``list_campaigns()`` order, which is
  recency (#267). No LLM play flow ever holds more than its own campaign's
  lock.

  Checked twice, and the second one is what makes the rule above binding on
  code nobody has written yet. Per holder, behaviourally:
  ``test_locks_store.py`` spies on the registry and asserts the order each of
  the two actually asks for. Then structurally:
  ``test_lock_order_guard.py`` walks this package's ASTs and fails on the
  *shapes* that hold two campaign locks at once (an acquisition registered on
  an ``ExitStack``, one carried around a loop, two open at the same time for
  different campaigns) anywhere but ``hold_all``. A third holder written
  tomorrow fails
  on arrival rather than deadlocking in production, which is the gap #267 was
  filed about. Add one anyway and you route it through ``hold_all`` and add its
  behavioural test beside those two; what the guard cannot see is a lock
  reached through an alias or a wrapper object, and two locks taken on either
  side of a function call.
- campaign lock -> audit baseline lock, never reversed
  (``store/audit/baselines.py``).
- image ingest/GC lock -> image-object stripe lock, never reversed
  (``store/image_store.py``). Both are leaves: either may be taken under a
  campaign lock, and nothing takes a campaign lock while holding one.
- The image locks as a whole, outermost first: campaign ->
  ``image_collection_job_lock`` -> ``image_collection_lock`` -> image NAME
  stripes -> image SIDECAR stripes -> ingest/GC -> object stripe. The name and
  sidecar families are striped (``IMAGE_NAME_STRIPES`` each), so two unrelated
  names can share a lock; that is safe only because a holder of several stripes
  of one family takes them sorted and deduplicated, all up front
  (``image_name_locks`` / ``image_sidecar_locks``), and because a stripe taken
  while another is already held without being part of that up-front set is
  taken NON-blocking (``assets._locks_if_free``: the read-side promotion
  recovery and a nested heal).

The lock is an ``RLock`` so a caller can compose lower-level mutators —
``audit.apply_delta`` calls ``sheets.set_field`` under an already-held lock —
and it is *additionally* an OS advisory file lock (``store/proclock.py``), so
it excludes concurrent **processes** and not merely threads (#234).

Three limits on that, all deliberate:

- **Not across devices.** The lock file is machine-local, so a lock held on
  one device is invisible to another sharing the store through a synced
  folder. No filesystem lock on a sync-replicated store can do better; the
  sync client resolves simultaneous edits with conflict copies on its own
  schedule. Detecting that after the fact (compare-and-swap on a content
  digest) is a separate, larger change.
- **Not across OS users**, whose lock directories differ.
- **Not every campaign mutation** — only the ones that take this lock. Which
  those are is no longer a claim in this paragraph: see ``DOMAIN_MODULES`` and
  the two lists of what sits outside it, below.

Contention raises ``StoreBusy`` after ``LOCK_TIMEOUT``; one handler in
``main.create_app`` turns that into HTTP 409.

Spec: docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import hashlib
import os
import threading
import time
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import Literal

from .. import paths, proclock
from . import _runtime as _runtime
from ._domain import DOMAIN_MODULES as DOMAIN_MODULES
from ._domain import OUTSIDE_DOMAIN as OUTSIDE_DOMAIN
from ._domain import UNREVIEWED as UNREVIEWED
from ._runtime import _NAME_DOMAIN as _NAME_DOMAIN
from ._runtime import _SIDECAR_DOMAIN as _SIDECAR_DOMAIN
from ._runtime import IMAGE_NAME_STRIPES as IMAGE_NAME_STRIPES
from ._runtime import IMAGE_OBJECT_STRIPES as IMAGE_OBJECT_STRIPES
from ._runtime import LOCK_TIMEOUT as LOCK_TIMEOUT
from ._runtime import BackupBusy as BackupBusy
from ._runtime import CampaignBusy as CampaignBusy
from ._runtime import ConfigBusy as ConfigBusy
from ._runtime import MigrationBusy as MigrationBusy
from ._runtime import ModuleEditBusy as ModuleEditBusy
from ._runtime import StoreBusy as StoreBusy
from ._runtime import StripeLock as StripeLock
from ._runtime import WorldActorBusy as WorldActorBusy
from ._runtime import WorldLifecycleBusy as WorldLifecycleBusy
from ._runtime import _backups as _backups
from ._runtime import _campaign_holds_local as _campaign_holds_local
from ._runtime import _campaign_locks as _campaign_locks
from ._runtime import _config as _config
from ._runtime import _domain_holds as _domain_holds
from ._runtime import _fold as _fold
from ._runtime import _image_collection_job_locks as _image_collection_job_locks
from ._runtime import _image_collection_locks as _image_collection_locks
from ._runtime import _image_ingest_gc_locks as _image_ingest_gc_locks
from ._runtime import _image_object_locks as _image_object_locks
from ._runtime import _image_stripe as _image_stripe
from ._runtime import _image_stripe_locks as _image_stripe_locks
from ._runtime import _inference_migration as _inference_migration
from ._runtime import _module_edit as _module_edit
from ._runtime import _ProcessScopedLock as _ProcessScopedLock
from ._runtime import _remaining as _remaining
from ._runtime import _store_key as _store_key
from ._runtime import _stripe_holds as _stripe_holds
from ._runtime import _stripe_lock as _stripe_lock
from ._runtime import _stripe_set as _stripe_set
from ._runtime import _world_actor_locks as _world_actor_locks
from ._runtime import _world_lifecycle_locks as _world_lifecycle_locks
from ._runtime import _world_lifecycle_process as _world_lifecycle_process
from ._runtime import _WorldLifecycleLock as _WorldLifecycleLock
from ._runtime import backup_lock as backup_lock
from ._runtime import best_effort_campaign_lock as best_effort_campaign_lock
from ._runtime import campaign_lock as campaign_lock
from ._runtime import campaign_lock_nowait as campaign_lock_nowait
from ._runtime import campaign_locks_held as campaign_locks_held
from ._runtime import config_lock as config_lock
from ._runtime import hold_all as hold_all
from ._runtime import holds_campaign as holds_campaign
from ._runtime import image_collection_job_lock as image_collection_job_lock
from ._runtime import image_collection_lock as image_collection_lock
from ._runtime import image_ingest_gc_lock as image_ingest_gc_lock
from ._runtime import image_name_lock as image_name_lock
from ._runtime import image_name_locks as image_name_locks
from ._runtime import image_name_locks_for as image_name_locks_for
from ._runtime import image_object_lock as image_object_lock
from ._runtime import image_sidecar_lock as image_sidecar_lock
from ._runtime import image_sidecar_locks as image_sidecar_locks
from ._runtime import image_stripes_held as image_stripes_held
from ._runtime import inference_migration_lock as inference_migration_lock
from ._runtime import module_edit_lock as module_edit_lock
from ._runtime import world_actor_lock as world_actor_lock
from ._runtime import world_lifecycle_held as world_lifecycle_held
from ._runtime import world_lifecycle_lock as world_lifecycle_lock


def __getattr__(name: str):
    """Read process-replaced state from its single runtime owner."""
    if name == '_registry_guard':
        return _runtime._registry_guard
    if name == '_world_lifecycle_pid':
        return _runtime._world_lifecycle_pid
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted([*globals(), *['_registry_guard', '_world_lifecycle_pid']])

# Original star-import surface, captured before extraction.
__all__ = [
    'DOMAIN_MODULES',
    'IMAGE_NAME_STRIPES',
    'IMAGE_OBJECT_STRIPES',
    'LOCK_TIMEOUT',
    'OUTSIDE_DOMAIN',
    'UNREVIEWED',
    'BackupBusy',
    'CampaignBusy',
    'ConfigBusy',
    'ExitStack',
    'Iterable',
    'Literal',
    'MigrationBusy',
    'ModuleEditBusy',
    'Path',
    'StoreBusy',
    'StripeLock',
    'WorldActorBusy',
    'WorldLifecycleBusy',
    'annotations',
    'backup_lock',
    'best_effort_campaign_lock',
    'campaign_lock',
    'campaign_lock_nowait',
    'campaign_locks_held',
    'config_lock',
    'contextmanager',
    'hashlib',
    'hold_all',
    'holds_campaign',
    'image_collection_job_lock',
    'image_collection_lock',
    'image_ingest_gc_lock',
    'image_name_lock',
    'image_name_locks',
    'image_name_locks_for',
    'image_object_lock',
    'image_sidecar_lock',
    'image_sidecar_locks',
    'image_stripes_held',
    'inference_migration_lock',
    'module_edit_lock',
    'os',
    'paths',
    'proclock',
    'suppress',
    'threading',
    'time',
    'world_actor_lock',
    'world_lifecycle_held',
    'world_lifecycle_lock',
]

# Preserve deferred module annotations without copying live values.
__annotations__ = {'DOMAIN_MODULES': 'frozenset[str]', 'OUTSIDE_DOMAIN': 'dict[str, str]', 'UNREVIEWED': 'frozenset[str]', '_campaign_locks': 'dict[str, _ProcessScopedLock]', '_world_actor_locks': 'dict[str, _ProcessScopedLock]', '_image_collection_locks': 'dict[str, _ProcessScopedLock]', '_image_collection_job_locks': 'dict[tuple[str, str], _ProcessScopedLock]', '_image_object_locks': 'dict[tuple[str, int], _ProcessScopedLock]', '_image_ingest_gc_locks': 'dict[str, _ProcessScopedLock]', '_image_stripe_locks': 'dict[tuple[str, str, int], StripeLock]', '_world_lifecycle_locks': 'dict[tuple[str, str], _WorldLifecycleLock]'}
