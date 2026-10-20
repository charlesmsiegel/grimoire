"""Move a store from the legacy settings layout to format 2 (spec 11.2).

Global, once, idempotent and resumable. `ensure` runs the steps in order:
`llm_connections.ensure_migrated()` (today's format-1 seeding, not a migration
write), then

1. **backup** -- `backups.create_backup(prefix=SAFETY_PREFIX)`, which
   retention never prunes. If it fails nothing is written, the status says
   why, and the next start retries. Taken once per root: a run resuming after
   a later step failed reuses the archive the status note names while it is
   still there, so retries do not pile up never-pruned copies of the settings;
2. **providers** -- each connection gains `preset` and `billing`, both
   rev-neutral, so catalogs, verified tests and vector caches survive. A
   connection that already has a preset is skipped: a no-op update would
   still rewrite it;
3. **model facts** -- each connection's legacy `vision`, `prefill` and
   `post_process`, where they differ from the defaults, become the facts of its
   model (an unset Claude model is `opus`; any other empty model is keyed "").
   What is copied is recorded beside it (`facts.adopt_legacy`), so a resumed
   run takes back a copy the record no longer says before copying afresh.
   Copied in the `llm_connections.LOCK` hold that writes the marker
   (`_switch`), so a legacy edit made before it is not switched past;
4. *(derived reasoning presets: not the migration's.)* They are
   retirement's, persisted after the marker by `retire` (slice I, rulings 4
   and 6, below); the migration persists the planner's `mapped` only, never a
   repoint, so every preset key it writes names a preset file that exists
   (N2);
5. **roles**, 6. **routes**, 7. **split routes**, 9. **marker** -- ONE
   `config.md` write, the last, derived from a read taken inside the
   `config_lock` hold that writes it: the planner's mapping of the legacy
   settings (`legacy_plan.global_plan(...).mapped`, never its repoints),
   persisted **verbatim** (padded ids, dangling references and
   `PRESET_CLEAR` exactly as it gives them; the split routes are already in
   it, read from their parents) with two enrichments -- an unset Claude model
   is written as `opus` wherever it is a selection's model, and the Embedding
   role is set only when the legacy configuration actually embeds
   (`embed_space.resolve`'s answer over the run's strict lookup,
   `legacy_plan.legacy_embeds`; ruling 5) -- plus `inference_format: "2"`.

   Read late because the app serves at format 1 all through the backup, and a
   legacy edit made meanwhile must not be reverted by a snapshot from before
   it. Written whole because `write_config` merges: EVERY global key the
   migration owns (`OWNED_GLOBAL_KEYS`) is written, "" where the translation
   leaves it unset, so a format-2 key already in the file -- an interrupted
   run of an earlier build, a hand edit -- is overwritten with what the
   legacy settings say now rather than surviving because nothing named it.
   The legacy routes' `preset_<k>` keys are the same keys in both layouts and
   are left as they are.

8. **campaigns** -- `campaign(cid)` for each unmarked one, under its own
   `campaign_lock_nowait(cid)`, one at a time; a busy one is skipped and the
   next `ensure` finishes it (it resolves through the planner, in memory,
   meanwhile).
   AFTER the marker, not before it: a campaign's routes are translated from
   the connections they name, and before the marker a connection's legacy
   `model` can still be edited -- by this server or another -- so a campaign
   migrated then kept the model the edit replaced, for good. Once the store
   is at format 2 those fields are frozen (refused on write), so what a
   campaign is translated from cannot move under its step, and an unmarked
   campaign reads the same mapping, in memory, until it is reached.

10. **retirement** (`_retire`, slice I ruling 6) -- after the campaigns, in
   the same run, and on every `ensure` of a current store that
   `retire.left()` says has anything left: the `pre-retirement-` archive when
   the pass deletes or replaces a stored value (skipped when this run CREATED
   the `pre-inference-` one, reused when an earlier pass's is still there),
   then `config.md`'s derived presets, repoint, legacy-key deletion and
   retirement marker in one write, then each campaign likewise under its own
   no-wait hold (an unmarked one migrated in that same write). It never moves
   `status().state`; what it has left is `Status.retirement`.

Every derived value is deterministic, so two devices migrating one synced
store write the same bytes. The migration itself leaves the legacy keys and
fields in place, frozen, for older builds; retirement then removes the keys
(and, in Task 6b, the connection fields).

**What `ensure` holds.** The migration lock (`locks.inference_migration_lock`,
a process lock plus a proclock), TRIED and never waited on: a second `ensure`
returns the current status at once. It takes no run exclusion -- that is a fact
about one app's run registry, and `main.start` holds it (CLAUDE.md). It
resolves the store root inside that hold (`PUT /config/data-dir` holds the
same lock across a move, so the root cannot change between the two) and checks
it before each step and each item, so a data-dir switch mid-run writes no
marker into either tree: the run belongs to the root it started on. The same
checks read the caller's `stop` flag, which lifespan shutdown sets; a stopped
run is `pending` and resumes on the next `ensure`.

**Failure policy.** A failed backup, an I/O error on the final global write,
a connection file that exists but cannot be read, a `config.md` that holds no
record (zero bytes, or unfenced: `RecordUnreadableError`) and a model-facts
file that cannot be read (`facts.FactsUnreadableError`) fail the migration (no
marker; the next start retries). None of them is "nothing there": every step
persists what it reads, so a read a sync client blocked would otherwise be
written down for good -- an empty model with no preset, an empty layout
stamped current, a facts file holding only the copy. An item that cannot be
migrated -- an unreadable `campaign.md` (undecodable, or holding no record;
it is never rewritten), one naming an unreadable connection, a connection or
model-facts write that fails or is refused, a busy campaign -- is skipped, its
reason recorded in `skipped`, and the run goes on. A newer build's marker,
whenever it is seen -- at the start, in the switch's hold, or under a
campaign's hold while the loop runs -- ends the run as `newer` and nothing
more is marked.

**Status.** `done`, `pending` and `newer` are derived from the store on every
call: the global marker, and whether any readable campaign is unmarked (an
unreadable one is reported in `skipped`, also derived, and does not hold the
store at `pending` -- nothing a re-run does can read it). Only `running`, a
failure reason, the last run's skips and the safety archive's name are
remembered, keyed by root, in `<home>/.cache/inference-migration.json` --
derived data, outside every backup -- beside retirement's archive name and
why its last pass stopped short (`retire_safety`, `retire_failed`).
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import dataclasses
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from ... import (
    atomic,
    backups,
    config,
    frontmatter,
    llm_connections,
    locks,
    paths,
    proclock,
    revision,
    sampler_presets,
)
from ... import inference_keys as keys
from ... import inference_retired as retired
from ...campaigns import paths as campaign_paths
from ...campaigns import read as campaign_read
from .. import facts, legacy_plan, providers, retire
from ._contracts import MOVED as MOVED
from ._contracts import OWNED_GLOBAL_KEYS as OWNED_GLOBAL_KEYS
from ._contracts import STATES as STATES
from ._contracts import STOPPED as STOPPED
from ._contracts import Status as Status
from ._contracts import _no_retirement as _no_retirement
from ._execution import _UNREADABLE as _UNREADABLE
from ._execution import _archive as _archive
from ._execution import _before_campaigns as _before_campaigns
from ._execution import _campaign_step as _campaign_step
from ._execution import _facts as _facts
from ._execution import _idle as _idle
from ._execution import _lookup as _lookup
from ._execution import _providers as _providers
from ._execution import _retire as _retire
from ._execution import _retire_archive as _retire_archive
from ._execution import _retire_unit as _retire_unit
from ._execution import _retire_units as _retire_units
from ._execution import _Run as _Run
from ._execution import _run as _run
from ._execution import _steps as _steps
from ._execution import _stranded_notes as _stranded_notes
from ._execution import _strip_unit as _strip_unit
from ._execution import _switch as _switch
from ._execution import campaign as campaign
from ._execution import campaign_fields as campaign_fields
from ._execution import connection_reader as connection_reader
from ._execution import ensure as ensure
from ._execution import global_fields as global_fields
from ._execution import held_still as held_still
from ._execution import try_ensure as try_ensure
from ._tracking import RecordUnreadableError as RecordUnreadableError
from ._tracking import _active as _active
from ._tracking import _active_guard as _active_guard
from ._tracking import _activity as _activity
from ._tracking import _campaign_marks as _campaign_marks
from ._tracking import _config_exists as _config_exists
from ._tracking import _config_record as _config_record
from ._tracking import _is_running as _is_running
from ._tracking import _kept_retire_safety as _kept_retire_safety
from ._tracking import _kept_safety as _kept_safety
from ._tracking import _merged as _merged
from ._tracking import _note_retire_failed as _note_retire_failed
from ._tracking import _note_text as _note_text
from ._tracking import _owns_progress as _owns_progress
from ._tracking import _progress as _progress
from ._tracking import _progress_lease as _progress_lease
from ._tracking import _queued as _queued
from ._tracking import _recall as _recall
from ._tracking import _remember as _remember
from ._tracking import _retirement as _retirement
from ._tracking import _running_on as _running_on
from ._tracking import _status_path as _status_path
from ._tracking import _unreadable_skips as _unreadable_skips
from ._tracking import dequeue as dequeue
from ._tracking import log as log
from ._tracking import queue as queue
from ._tracking import status as status

# Original star-import surface, captured before extraction.
__all__ = [
    'MOVED',
    'OWNED_GLOBAL_KEYS',
    'STATES',
    'STOPPED',
    'Iterator',
    'Path',
    'RecordUnreadableError',
    'Status',
    'annotations',
    'atomic',
    'backups',
    'campaign',
    'campaign_fields',
    'campaign_paths',
    'campaign_read',
    'config',
    'connection_reader',
    'contextmanager',
    'dataclasses',
    'dequeue',
    'ensure',
    'facts',
    'frontmatter',
    'global_fields',
    'held_still',
    'json',
    'keys',
    'legacy_plan',
    'llm_connections',
    'locks',
    'log',
    'logging',
    'os',
    'paths',
    'proclock',
    'providers',
    'queue',
    'retire',
    'retired',
    'revision',
    'sampler_presets',
    'status',
    'suppress',
    'threading',
    'time',
    'try_ensure',
    'uuid',
]

# Preserve the original deferred module metadata without redeclaring exports.
__annotations__ = {'STATES': 'tuple[str, ...]', 'OWNED_GLOBAL_KEYS': 'tuple[str, ...]', '_active': 'dict[str, dict | None]', '_queued': 'set[str]'}
