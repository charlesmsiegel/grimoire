## Fork — Design

> **Status:** Design ready for implementation plan. Independent of other `specs/new/` features (the auto-fork nudge from `retcon-design.md` defers to fork's API but neither blocks the other). Note: this spec is **campaign-level fork**; the existing `orchestrator.fork` (branch-level fork) is a separate, complementary mechanism that stays.

**Source idea:** `specs/new/fork.md`
**Module:** `backend/src/grimoire/orchestrator/`, `backend/src/grimoire/state_store/`, `backend/src/grimoire/api/campaigns.py`

## Purpose

Branch a whole campaign at a chosen turn. Both the original and the fork are real campaigns with full state, developed independently. The current branch-level fork (`orchestrator/service.py:474`) creates an alternate branch *within* the same campaign and works well for "what-if" exploration that converges back. Campaign-level fork is for **divergence**: a whole new campaign id, separate directory, independent event streams. Forks appear in the campaign list under their parent.

## Scope (what changes)

- **Two fork modes:** "from current" (copy all state up to now) and "from earlier turn" (state up to a chosen post).
- **Bulk-copy machinery** in State Store for ~14 campaign-scoped SQLite tables (the relevant set enumerated below).
- **Image handling:** hardlink by default, deep-copy fallback. Per Theme C, **probe then auto-fallback silently** on Windows / cross-device.
- **Provenance fields** on `campaigns` table: `forked_from_campaign_id`, `forked_at_post_id`.
- **REST endpoints:** create fork, lineage tree.
- **Frontend:** fork dialog + campaign-list tree rendering.
- **Audit log + WebSocket events.**
- **Concurrency:** fork-during-streaming queued; concurrent forks allowed.

The existing branch-level fork (`fork_branch` in `state_store/store.py:1172`) is untouched.

## Two fork modes

### Fork from current

"I want to try a different direction from here." Behavior:

1. Pick a new campaign id; create `data/campaigns/<new-id>/` directory.
2. Copy every campaign-scoped narrative file (scenes, sheets, overrides, emergent content, sidecars).
3. Library refs inherited unchanged (same pinned versions or `track_latest`).
4. SQLite rows tagged with the original `campaign_id` duplicated with the new `campaign_id` — across all campaign-scoped tables (full list below).
5. Images: hardlink by default; deep-copy fallback (cross-device / Windows lacking privilege).
6. Provenance: `campaigns.forked_from_campaign_id = original`, `campaigns.forked_at_post_id = original.latest_post_id`.
7. Both campaigns independent from this moment on.

### Fork from earlier turn

Same as fork from current, but state is materialized via **audit-log replay**:

1. Pick the cutoff `post_id` in the original.
2. Create the new campaign directory.
3. Copy scene files up to and including the cutoff post's scene (truncating the cutoff scene at the cutoff post).
4. Replay deltas from the original's audit log (`deltas` table) up to the cutoff turn, applying them into the new campaign's fresh state-store rows.
5. Provenance same as above; `forked_at_post_id` is the cutoff.

Replay correctness (the open question on idempotence): not all deltas are pure idempotent applies — `apply_delta` writes `after` snapshots which are absolute, so re-applying produces deterministic state. The risk is in deltas whose `after` is a transient derivative (e.g., a counter expressed as `value + 1` stored as the post-increment absolute). The codebase already encodes deltas as absolutes (verified in `state_store/store.py`), so replay equivalence holds.

**Safety net:** after replay, run a hash check on a fingerprint of the original's state at the cutoff (computed at fork time via `state_store.fingerprint_at_turn(turn_id)`). Compare to the replayed campaign's fingerprint. Mismatch → fall back to **copy-and-truncate** (bulk-copy current rows where `turn_no <= cutoff`); audit logs the degradation. The audit-corruption fallback path (the open question) is this same mechanism.

## Campaign-scoped tables (the bulk-copy set)

Enumerated from migrations:

| Table | Migration | Filter |
|---|---|---|
| `campaigns` | 002 | row insert (new campaign) |
| `campaign_setting_refs` | 002 | inherit pinned versions |
| `campaign_pcs` | 002, 020 | copy roster; `last_played_at` copies the original's value |
| `scenes` | 004 | `campaign_id=:orig AND opened_at_turn_no <= :cutoff` |
| `posts` | 004 | `campaign_id=:orig AND turn_no <= :cutoff` |
| `facts` | 005 | `campaign_id=:orig AND established_in_post.turn_no <= :cutoff` |
| `commitments` | 005 | same |
| `relationships` | 005 | same (with history JSON pruned) |
| `knowledge_state` | 005 | same |
| `calendar` | 005 | same |
| `character_state` | 003 | same |
| `location_state` | 003 | same |
| `faction_state` | 003 | same |
| `images` (metadata) | 006 | bulk copy + file hardlink/copy |
| `deltas` | 007 | `campaign_id=:orig AND turn_no <= :cutoff` (history retained) |
| `review_queue` | 007 | open items only (pending) |
| `embeddings` | 007 | matching campaign_id; entries pinned to extant facts |
| `turn_audits` | 008 | `campaign_id=:orig AND turn_no <= :cutoff` |
| `cost_records` | 008 | skip (per-account billing record stays with original) |
| `llm_requests` | 008 | skip (observability; large) |
| `contradiction_reports` | 009 | matching campaign + post-id |
| `scheduled_events` | 010 | open items only |
| `campaign_mechanics_history` | 023 | full copy (history of mechanics changes) |

**Not copied:** `cost_records`, `llm_requests` (transient observability), `imagegen_jobs` (transient queue). These remain with the original.

Implementation lives in `state_store/fork.py` (new module): one `bulk_copy(original_campaign_id, new_campaign_id, cutoff_turn_no=None)` function that runs the INSERT..SELECT statements inside a single transaction. With `cutoff_turn_no=None`, the filter degenerates to "all rows."

**FK integrity:** the table list above is ordered such that referenced tables come before referencing ones (e.g., `scenes` before `posts`). All inserts happen in one transaction; failure rolls back the whole fork.

## Image handling

Spec: hardlink default, deep-copy fallback on failure. Per Theme C, probe + auto-fallback silently.

```python
async def fork_image_files(original_dir: Path, new_dir: Path) -> ImageHandlingResult:
    sentinel_src = original_dir / "images" / ".sentinel"
    sentinel_src.parent.mkdir(parents=True, exist_ok=True)
    sentinel_src.touch(exist_ok=True)
    sentinel_dst = new_dir / "images" / ".sentinel"
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "images").mkdir(exist_ok=True)

    handling: ImageHandling
    try:
        os.link(sentinel_src, sentinel_dst)
        handling = ImageHandling.HARDLINK
    except (OSError, PermissionError):
        handling = ImageHandling.DEEP_COPY
    sentinel_dst.unlink(missing_ok=True)

    for src in (original_dir / "images").rglob("*"):
        if src.is_dir() or src.name == ".sentinel":
            continue
        dst = new_dir / src.relative_to(original_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if handling == ImageHandling.HARDLINK:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
                handling = ImageHandling.DEEP_COPY     # downgrade entire run
        else:
            shutil.copy2(src, dst)

    return ImageHandlingResult(handling=handling, ...)
```

The sentinel probe avoids attempting `os.link` for every file on a volume that can't support it. On success, every subsequent file gets hardlinked; if any individual link fails mid-run (cross-device, exotic FS error), the rest of the run deep-copies. The returned `handling` reflects what actually happened; logged in the audit line. **The fork dialog does not surface the choice** (per the theme decision) — the user sees what they get.

`copy2` preserves mtime/permissions, matching hardlink behavior as closely as possible.

## Provenance fields

Migration adds:

```sql
ALTER TABLE campaigns ADD COLUMN forked_from_campaign_id TEXT;
ALTER TABLE campaigns ADD COLUMN forked_at_post_id TEXT;
ALTER TABLE campaigns ADD COLUMN forked_at_turn_no INTEGER;
ALTER TABLE campaigns ADD COLUMN forked_image_handling TEXT;        -- hardlink | deep_copy | mixed
CREATE INDEX ix_campaigns_forked_from ON campaigns(forked_from_campaign_id) WHERE forked_from_campaign_id IS NOT NULL;
```

Lineage tree query:
```sql
WITH RECURSIVE descendants(id, depth) AS (
    SELECT id, 0 FROM campaigns WHERE id = ?
    UNION ALL
    SELECT c.id, descendants.depth + 1
    FROM campaigns c JOIN descendants ON c.forked_from_campaign_id = descendants.id
)
SELECT * FROM descendants ORDER BY depth, id;
```

## Backend surface

```python
async def fork_campaign(
    self,
    campaign_id: str,
    *,
    new_campaign_id: str,
    new_name: str,
    fork_at_post_id: str | None = None,
    description: str | None = None,
    make_active: bool = False,
) -> ForkCampaignResult: ...


@dataclass
class ForkCampaignResult:
    new_campaign: Campaign
    image_handling: ImageHandling
    deltas_replayed: int            # 0 for "from current"
    fingerprint_match: bool         # for fork-from-earlier; True for "from current"
    degraded: bool                  # True if fingerprint mismatch + fallback used
    queued: bool                    # True if fork was queued (mid-stream)
```

REST:
```
POST   /campaigns/{id}/forks            # body: {new_campaign_id, new_name, fork_at_post_id?, description?, make_active?}
GET    /campaigns/{id}/lineage          # parent + descendants tree
GET    /campaigns/{id}/lineage/ancestors   # straight line up
```

WebSocket events:
```json
{ "type": "campaign_fork_queued",   "source": "...", "new": "..." }
{ "type": "campaign_forked",         "source": "...", "new": "...", "at_post": "...", "image_handling": "hardlink" }
{ "type": "campaign_fork_failed",    "source": "...", "new": "...", "error": "..." }
```

## Concurrency

**Fork mid-flight (during canonical turn streaming):** the fork is **queued** — `Orchestrator._pending_fork_queue: deque[ForkRequest]`. The queue persists across server restart via a `pending_forks` SQLite table (so a restart mid-fork doesn't lose user intent). On stream completion, the next pending fork runs. The queue is processed serially per campaign (multiple campaigns can have pending forks in parallel — no contention).

**Two forks open concurrently** (on different campaigns): allowed; each runs in its own transaction.

**Two forks of the same campaign in flight:** also allowed (they read from the source independently); the resulting forks are independent. If both fork from the same `forked_at_post_id`, that's fine — two divergent branches of exploration.

## Campaign id collisions

The new campaign id must be unique. The frontend auto-suggests `<original-id>-fork-<n>` but **requires the user to type a descriptive name** before allowing submit. The suggestion is a placeholder, not a default. Backend validates uniqueness and returns `409 CAMPAIGN_ID_EXISTS`. Rationale (research surfaced this): "by-night-london-fork-1" is a bad name; better to nudge for descriptiveness.

If the user changes the *name* of a campaign later, the id stays — folder rename is **not** supported in v1 (the directory `data/campaigns/<id>` is identity, not display). The display name lives in `campaigns.name`.

## Frontend

Fork dialog:

```
┌─ Fork campaign ─────────────────────────────────────────┐
│  Source: by-night-london (currently at scene 47, p_4710) │
│                                                           │
│  Fork from:                                               │
│    ◉ Current state                                        │
│    ◯ Earlier post: [search/select]                        │
│                                                           │
│  New campaign name:    [_________________]   (required)   │
│  New campaign id:      [auto-suggested]                   │
│                                                           │
│  Describe the divergence (optional):                      │
│    [____________________________________________]         │
│                                                           │
│  ☐ Make this the active campaign after forking            │
│                                                           │
│            [Fork]   [Cancel]                              │
└──────────────────────────────────────────────────────────┘
```

Note: the **Image handling** row is gone — per Theme C, the system probes and auto-falls-back; user isn't asked.

Campaign list with indented children:

```
Campaigns
├── by-night-london
│   ├── by-night-london-divergent-negotiation  (forked at p_4710)
│   └── what-if-julian-died                      (forked at p_3104)
└── a-saga-in-iberia
```

Lineage is computed via `GET /campaigns/{id}/lineage` and rendered via a flat indented list.

## Configuration

```yaml
fork:
  image_handling: probe                   # probe | hardlink_only | deep_copy_only
  fingerprint_check: true                 # safety check for fork-from-earlier
  fingerprint_fallback: copy_and_truncate # copy_and_truncate | fail
  pending_fork_persistence: true          # survive restart
  pending_fork_queue_max: 32
```

## Audit and observability

```
[fork-queued]   source=by-night-london new=by-night-london-divergent at_post=p_4710 by=user
[fork-started]  source=by-night-london new=by-night-london-divergent fork_at_post=p_4710
[fork-images]   source=by-night-london new=by-night-london-divergent handling=hardlink files=124
[fork-replay]   source=by-night-london new=by-night-london-divergent deltas_replayed=842 fingerprint=match
[fork-complete] source=by-night-london new=by-night-london-divergent duration_s=1.84
```

Time-travel queries in observability gain awareness of fork events as first-class — when a campaign's id matches a fork descendant, the observability viewer offers a "show original timeline" toggle.

## Performance

- Fork from current: SQLite bulk-copy (12 tables) + file hardlinks → < 1s for a typical campaign (10,000 posts / 50 MB images, hardlinked).
- Fork from earlier turn: audit-log replay → 1–5 s for a long campaign (replay-bound on number of deltas).
- Lineage tree query: < 50 ms (small recursive CTE).

## Failure handling

| Failure | Behavior |
|---|---|
| New campaign id collides | 409; UI re-prompts |
| Hardlinks fail mid-run | Continue with deep-copy; `image_handling=mixed` in audit |
| Fingerprint mismatch on fork-from-earlier | Fall back to copy-and-truncate; `degraded=true`; audit logged |
| Bulk-copy partial failure | Entire transaction rolls back; new campaign directory deleted; surface error |
| Fork during canonical turn streaming | Queued; `campaign_fork_queued` WS event; processed after turn completes |
| Server restart mid-fork | Queue restored from `pending_forks` table; partial fork files cleaned on startup |
| Audit-log corruption (missing deltas) | Detected by gaps in `turn_no` sequence; fall back to copy-and-truncate; audit warns "degraded" |
| Empty source campaign | Fork allowed (creates a clone with no content); useful for templating |

## Test wiring

`backend/tests/orchestrator/test_fork_campaign.py` (new):
- Fork from current: bulk-copy round-trip; fork's state matches original at fork moment.
- Fork from earlier turn: replay correctness on a fixture audit log.
- Fingerprint mismatch fixture (manually corrupted delta) → fallback to copy-and-truncate.
- Image handling probe: simulate `os.link` raising `OSError` → deep-copy path.
- Fork during streaming: turn finishes first; fork applies after.

`backend/tests/state_store/test_fork.py`:
- `bulk_copy` independent table-by-table tests.
- FK integrity validation post-copy.
- `fingerprint_at_turn` deterministic.

`backend/tests/api/test_fork_routes.py`:
- All endpoints round-trip.
- Lineage tree shape on a 3-deep nested chain.
- 409 on id collision.

## Wiring touchpoints

- `backend/src/grimoire/state_store/fork.py` (new): `bulk_copy`, `replay_to_turn`, `fingerprint_at_turn`.
- `backend/src/grimoire/state_store/store.py`: registers `pending_forks` table; surface for fork queue.
- `backend/src/grimoire/orchestrator/service.py`: new `fork_campaign` method; queueing logic; calls into `state_store/fork.py`.
- `backend/src/grimoire/orchestrator/fork_images.py` (new): the probe + copy machinery.
- `backend/src/grimoire/api/campaigns.py:710`: replace branch-level wrapper with the campaign-level method when called with `fork_at_post_id` argument; old branch-level fork stays as a separate route `/campaigns/{id}/branches`.
- Migration adds provenance columns on `campaigns` + `pending_forks` table.
- `frontend/src/routes/campaigns/ForkDialog.tsx` (new).
- `frontend/src/routes/campaigns/CampaignList.tsx`: tree rendering using lineage endpoint.
- `frontend/src/api/campaigns.ts`: fork + lineage clients.

## Out of scope (v1)

- Fork merge-back (UI for re-applying selected fork turns onto parent). Data model preserves enough provenance to support this in v2.
- Inter-campaign event correlation (events on the original don't replay into the fork after fork point).
- Cross-machine hardlinks (would need filesystem awareness; deep-copy is the safe path).
- Fork undo (delete the fork's directory + SQLite rows; v2).
- Selective copy (e.g., "fork everything except commitments"). Always full state copy.
