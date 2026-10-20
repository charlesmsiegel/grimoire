# Storage contracts

[Documentation](../README.md) · [Data and backups](../guides/data.md)

This page describes persistence boundaries, not a promise that a collection of
ordinary files behaves like a transactional database. The owning modules linked
below contain the detailed operation contracts.

## Library layout

The active root comes from [store.paths](../../backend/src/grimoire/store/paths.py).
Its main areas are:

| Area | Contents |
|---|---|
| `config.md`, `llm_connections/`, `sampler_presets/` | Global settings and provider/model configuration |
| `worlds/` | Shared setting records and reusable cast |
| `campaigns/` | Campaign metadata, scenes and campaign-specific state |
| `assets/image-store/` | Shared image blobs and object metadata |
| `modules/`, `calendars/` | User mechanics packs and calendar providers |
| `usage/`, `logs/` | Monthly append-only accounting and diagnostic records |
| `.cache/` | Rebuildable derived data and device-specific cache files |

The bootstrap pointer lives outside this tree. Source templates are resolved
separately by [prompts.py](../../backend/src/grimoire/prompts.py), including the
`GRIMOIRE_TEMPLATES` override. APK assets and machine-local lock state do not
become library content merely because the app uses them.

## World and campaign overlays

[overlay/](../../backend/src/grimoire/store/overlay/) owns campaign-over-world
resolution. A campaign's materialized record wins; otherwise an inherited world
record is read live. A tombstone hides an inherited record. A detached record
shares no inheritance relationship with a world record that later reuses its ID.

Characters and PCs materialize as whole record directories for metadata and
versions. Sidecars and image placements have their own overlay rules. A read
sweep can share an overlay `View`, but must not keep it across mutations.
`sync.md` records comparison bases for diverged records; it is not a second
authoritative world.

## Atomic record writes

[atomic.py](../../backend/src/grimoire/store/atomic.py) writes through temporary
descriptors beside the target and publishes complete replacements. A reader sees
the old or new record, rather than a target truncated halfway through a write.
The module also owns streaming writes and protected append operations.

This does not promise durability of the final rename across sudden power loss.
It does not make a set of separate files one transaction or prevent a sync client
from creating conflict copies. Linked record files are unsupported; replacement
acts on directory entries rather than following a leaf link as an ordinary
overwrite might.

## Campaign locks

[locks/](../../backend/src/grimoire/store/locks/) defines the campaign write
domain. Writers that read, validate and publish related campaign state serialize
on `campaign_lock(cid)`. A caller holding several campaign locks must use
`hold_all`, which orders them consistently. Other lock domains protect global
configuration, shared image operations and narrower sidecars.

A lock protects the span in which it is held. Separate locked writes do not
make an unlocked read or a larger multi-step workflow transactional. Best-effort
reads and strict mutators have different contention behavior. Inspect the chosen
lock helper before depending on a snapshot or exception type.

Run finalizers also fence writes on scene identity. A reusable filename or scene
slug alone is insufficient: a deleted scene can be replaced while an older run
is still alive. [Scene identity](../../backend/src/grimoire/store/scenes/identity.py)
and [run persistence](../../backend/src/grimoire/routes/streaming/) own that fence.

## Multiple processes and sync

[proclock.py](../../backend/src/grimoire/store/proclock.py) adds advisory operating
system locks for cooperating processes of the same user on one machine. Its lock
files live in machine-local state outside the synced library. It does not provide
locking across devices or across arbitrary external editors.

Configuration writes share the global configuration lock, including provider
records, model facts and settings changes that must compare and publish together.
Campaign mutation coverage is declared in `DOMAIN_MODULES`, `OUTSIDE_DOMAIN` and
the remaining `UNREVIEWED` entries in `locks.py`; do not infer universal coverage
from the existence of a campaign lock.

Filesystem signatures let readers notice ordinary external writes. These are
not a distributed coherence protocol. Generic stat-based memos can miss an
in-place rewrite that restores the same metadata; readers using stronger stamps
or actual content keys have different boundaries. See
[statcache.py](../../backend/src/grimoire/store/statcache.py).

## Shared images

[image_store.py](../../backend/src/grimoire/store/image_store.py) keeps encoded
bytes in content-addressed blobs and picture identity in object sidecars.
[image_refs.py](../../backend/src/grimoire/store/image_refs.py) records placements
under record asset directories. Reusing or copying a placement need not duplicate
the blob. A legacy image not yet migrated still follows its compatibility path.

### Shared metadata

Descriptions and subjects belong to the shared image object. Their writes must
respect the object locks and stale-save checks. A placement rename/removal and
a shared-caption update are different operations; an old save must not overwrite
a newer placement or metadata revision. Use the APIs in
[image_descriptions.py](../../backend/src/grimoire/store/image_descriptions.py)
and [assets/](../../backend/src/grimoire/store/assets/), not direct sidecar edits.

Migration and collection run against a pinned library root. Collection requires
a scan token, rechecks reachability, protects recent/unarrived data, and uses a
device-local recycle outbox before disposal. Unreadable roots fail closed.
Maintenance exclusion prevents conflicting tree operations, and cancellation
does not abandon a thread still writing. Read
[image_gc/](../../backend/src/grimoire/store/image_gc/),
[image_migration/](../../backend/src/grimoire/store/image_migration/) and
[maintenance routes](../../backend/src/grimoire/routes/maintenance.py).

## Model settings migration

[inference/migrate/](../../backend/src/grimoire/store/inference/migrate/) moves
legacy settings to the current roles/routes format, with protected settings
archives before destructive changes. Current safety archives are partial and
ordinary backup retention leaves them alone. The marker, individual campaign
migrations and retirement can be at different stages during a resumable upgrade.

[inference/retire.py](../../backend/src/grimoire/store/inference/retire.py) removes
legacy settings only through its recorded retirement process. Unreadable or
newer formats are not interpreted as empty settings to overwrite. Settings
routes refuse writes while the layout is unavailable or belongs to a newer
build. Do not delete markers or retirement records to force a save.

## Compiled caches

[compiled/](../../backend/src/grimoire/store/compiled/) owns an optional
SQLite memo. Callers supply live record keys or paths; persisted cache membership
does not define the live inventory. Derived answers are bound to their content
keys and runtime/build identity. Unknown, foreign, busy or unusable storage costs
a miss rather than permission to replace a library record.

The [build fingerprint](../../backend/src/grimoire/compiled_build.py) includes
runtime source, data and templates. It excludes README files and `docs/`
directories, so documentation changes do not invalidate caches. Markdown used
as runtime style or mechanics data remains an input.

### Overview projections

[overview/](../../backend/src/grimoire/store/overview/) supplies read projections
for world/campaign lists, the shell and to-do views. These are presentation
accelerators, not inputs that authorize a mutation or decide scene state.
Metadata stamps and projection keys govern reuse; actual write decisions still
read the underlying authoritative records. An overview is not a transactionally
consistent view of unrelated files being changed concurrently.

## Direct-edit synchronization

A direct edit bypasses the API's validation. Individual readers differ in their
tolerance for malformed Markdown, frontmatter and JSON; there is no universal
read-time schema check. For example, [cards.card_data](../../backend/src/grimoire/store/cards.py)
protects roster reads from a non-object card body without making every downstream
consumer tolerant of that same record. Preserve valid fields and references.

[cache_sync/](../../backend/src/grimoire/store/cache_sync/) warms previously
materialized representations after an external edit. It validates supplied
paths, handles intentional deletions separately, bumps affected campaign tokens
and runs local/network hooks. It does not make an invalid direct edit safe.
Skipping the warm-up can cost later recomputation; cache files remain derived.

Use the [store-editing procedure](../../.agents/skills/grimoire-store-editing/SKILL.md)
for authorized outside-app changes and the [cache CLI](../../backend/src/grimoire/cache.py)
for current arguments. Backups and readable exports have different scopes; see
[Data and backups](../guides/data.md).
