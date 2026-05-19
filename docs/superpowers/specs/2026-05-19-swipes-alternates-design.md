## Swipes / Alternates — Design

> **Status:** Design ready for implementation plan. Foundation feature: `retcon` (replayed turns produce alternates) and `auxiliary-tasks` (rewrite_post acceptance becomes a new alternate) both depend on it.

**Source idea:** `specs/new/swipes-alternates.md`
**Module:** `backend/src/grimoire/orchestrator/`, `backend/src/grimoire/scenes/`, `backend/src/grimoire/state_store/`
**Migration:** `024_delta_sets_and_alternates.sql` (paired with transient-state's migration; numbering picked at plan time)

## Purpose

Replace the current "regenerate = delete + replace" semantics with a multi-alternate model. Each post may have a list of `Alternate`s; one is `primary`. The scene's rendered prose `.md` reflects only primaries; swipes (chevron-cycle on the latest model post) rewind one delta set and apply another atomically.

This is the spec that introduces `delta_set_id` as a first-class state-store concept (per Theme A decision), which `retcon` and `auxiliary-tasks` both depend on for clean rewind/apply transactions.

## Scope (what changes)

- **State Store:** add `delta_set_id` column to `deltas` table; add `apply_delta_set` / `rewind_delta_set` atomic helpers; expose `current_delta_set_for(post_id)`.
- **Scene Manager:** per-post `alternates: list[Alternate]` and `primary_alternate_id` in the scene YAML sidecar; the `.md` file rebuilt from primaries on every `primary_switched` (sync, transactional).
- **Orchestrator:** replace `regenerate_last` (`backend/src/grimoire/orchestrator/service.py:304–338`) with `regenerate_post` (creates an alternate, does *not* auto-promote); add `switch_primary_alternate`, `pin_alternate`, `unpin_alternate`, `delete_alternate`.
- **API:** new routes under `/campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/...` and WebSocket events `alternate_added`, `primary_switched`.
- **Frontend:** chevron control + indicator on the latest model post; mid-history posts show chevrons disabled with tooltip pointing at retcon/fork.

Out of scope: cross-alternate side-by-side diff UI (data model supports it; v2 polish). Time-travel queries over non-primary alternates in observability (the audit log captures them; the observability viewer adapter is a separate small task tracked in `observability-COMPLETED.md`).

## The latest-post constraint

Switching alternates on a mid-history post would silently invalidate subsequent posts that were authored against the old primary's state. Mid-history changes are routed through retcon (deliberate edit + cascade) or fork (alternative timeline as separate campaign). The frontend disables chevrons on mid-history posts; the backend **also** rejects swipe API calls on non-latest posts with `400 ALTERNATE_LATEST_POST_ONLY`. Belt and suspenders, because direct API access bypasses UI controls.

The "latest post" check uses `Scene.last_model_post_id` from the sidecar; the constraint applies only to *model* author kinds (user posts don't have alternates).

## Data model

### `Alternate`

```python
@dataclass
class Alternate:
    id: str                          # e.g., "a_9012"
    post_id: str
    text: str
    delta_set_id: str                # FK to deltas table
    author_kind: AuthorKind          # user | model | aux
    model: Optional[str]             # model id when LLM-generated
    prompt_hash: Optional[str]       # deduplication key, see below
    steering_hint: Optional[str]     # nullable user instruction
    created_at: datetime
    tokens: Optional[int]
    pinned: bool = False
    is_primary: bool = False
```

`prompt_hash` is the SHA-256 of the assembled prompt messages (already computed for caching; see `AssembledPrompt.messages_hash` in `backend/src/grimoire/types/context.py`). Its purpose is **dedup detection**, not cache reuse: when a regenerate uses the same prompt (no steering, no model override), the orchestrator surfaces a "this matches an existing alternate" warning but still produces a fresh sample (the model output isn't deterministic across calls). The hash also powers the audit summary line.

### Scene YAML sidecar shape

```yaml
posts:
  - id: p_4710
    order_in_scene: 12
    turn_id: t_4710
    author_kind: model
    primary_alternate_id: a_9012
    alternates:
      - id: a_9011
        delta_set_id: ds_18021
        text: "...original prose..."
        author_kind: model
        model: claude-opus-4-7
        prompt_hash: "ab12..."
        tokens: 420
        pinned: false
        is_primary: false
      - id: a_9012
        delta_set_id: ds_18077
        text: "...regen with steering..."
        ...
        is_primary: true
```

User posts continue to live as a single primary with an `alternates` list of length 1 — keeps the schema uniform. The `.md` rendering walks posts in order and emits each primary's text under `## Post N — <author>` (current format).

## State Store: `delta_set_id` as first-class

Migration adds a column to `deltas`:

```sql
ALTER TABLE deltas ADD COLUMN delta_set_id TEXT;
CREATE INDEX ix_deltas_set ON deltas(campaign_id, delta_set_id);
```

A delta set is "all deltas produced by one Extractor run on one post text," roughly. The Orchestrator creates `delta_set_id = ds_<uuid>` at the start of extraction, threads it into `apply_delta`/`reverse_delta` calls already in `state_store/store.py:1249` and `:1308`. Backfilling is unnecessary: existing pre-migration deltas have `delta_set_id IS NULL`, which the new helpers treat as "ungrouped"; they're operationally fine — only future alternates create grouped sets.

New `StateStore` methods (additions to `backend/src/grimoire/state_store/store.py`):

```python
async def apply_delta_set(
    self,
    deltas: list[StateDelta],
    *,
    delta_set_id: str,
    campaign_id: str,
    branch_id: str,
    turn_id: str,
    source: str,
) -> list[DeltaRecord]: ...

async def rewind_delta_set(
    self,
    delta_set_id: str,
    *,
    campaign_id: str,
    branch_id: str,
) -> list[DeltaRecord]:                     # LIFO reversal, transactional
    ...

async def swap_delta_set(
    self,
    *,
    rewind_set_id: str,
    apply_deltas: list[StateDelta],
    apply_set_id: str,
    campaign_id: str,
    branch_id: str,
    turn_id: str,
    source: str,
) -> SwapResult: ...                        # combined atomic rewind+apply
```

`swap_delta_set` is the heart of `switch_primary_alternate`: SQLite `BEGIN IMMEDIATE` wraps both phases. On apply failure, the transaction rolls back; the prior primary's deltas are still in place because the rewind never committed. The Orchestrator surfaces a structured error; the alternate remains in the list, but unselected.

This same primitive is what `retcon` uses for replay (each replayed post is its own swap) and what `auxiliary-tasks.rewrite_post` uses on accept.

## Operations

### `regenerate_post(post_id, *, steering_hint=None, model_override=None)`

Replaces `regenerate_last`. Behavior:

1. Validate the post is the latest model post on its scene (`Scene.last_model_post_id == post_id`); else `400 ALTERNATE_LATEST_POST_ONLY`.
2. Resolve current primary alternate; capture its `delta_set_id` (for the optional accept-flow later — does *not* rewind yet).
3. Run a normal canonical turn pipeline against the **same player input** (read from the scene at `turn.player_input`). Streams to the frontend.
4. Extractor produces a new delta set with a fresh `delta_set_id`. Deltas are applied to a **shadow** branch state? **No** — easier path: deltas are applied immediately under the new `delta_set_id`, but the new alternate is **not** marked primary. The state store sees both delta sets layered; the "current truth" is whichever is primary, and reads honor that.
5. New `Alternate` written to the sidecar (`alternates.append(...)`, `is_primary=False`).
6. Emit `alternate_added` WS event.
7. Return `RegenerateResult(post_id, new_alternate_id)`.

The "apply but not primary" choice is what makes `switch_primary_alternate` cheap (swap precomputed sets). The cost is read-side discipline: every state-store read filters `WHERE delta_set_id IN (primary sets for this branch's primary alternates)`. Bookkeeping lives in a `current_alternate_delta_sets(branch_id)` materialized view rebuilt on every primary switch.

Alternative: skip step 4 (don't pre-apply), and run extraction lazily on switch. Cleaner read path but per-switch latency goes up by an Extractor call. **Recommended path is pre-apply** because it keeps the existing apply-then-extract flow intact; the materialized view is a small addition.

### `switch_primary_alternate(post_id, alternate_id)`

1. Validate latest-post.
2. Look up current primary's `delta_set_id` and the target's `delta_set_id`.
3. `state_store.swap_delta_set(rewind=current, apply=target_deltas, apply_set_id=target_delta_set_id)`.
4. Rewrite `scene.md` from primaries (sync — sidecar update + .md rewrite inside the same transaction-ish boundary; if .md write fails, alternate switch is reverted).
5. Update `current_alternate_delta_sets` materialized view.
6. Emit `primary_switched(from_alt_id, to_alt_id)` WS event.
7. Run Continuity contradiction-check on the new primary's deltas (already part of `_apply_routing` flow).

Audit log entry: `[switch] campaign=... post=p_4710 from=a_9011 to=a_9012 (rewind 14 / apply 17 deltas)`.

### Pinning, deletion, retention

- `pin_alternate(post_id, alternate_id, pinned: bool)` — sets the flag in the sidecar; pinned alternates count toward `max_alternates_per_post` but never auto-evict.
- `delete_alternate(post_id, alternate_id)` — guards against deleting primary (must switch first). Drops the row + reverses its delta set (the deltas were pre-applied at regenerate-time). Audit retained per the retention window.
- `auto_purge_older_than_days` defaults to `null` (never). Production default is **30 days** in `swipes.yaml` shipped with new campaigns. The vacuum sweep is the same daemon that handles transient-state retention.

`max_alternates_per_post` (default 5): on new generation, if the count of non-pinned alternates is at the cap, evict the oldest non-pinned. Eviction happens **after** the new alternate is added (so the cap can never temporarily hold the new one out). Pinned do not block adds: 5 pinned + 1 new = 6 total, no eviction; warn the user about the cap-busting scenario.

## Cross-spec hooks

- **`retcon` (replay variant):** each replayed turn calls `regenerate_post`-ish path on the replayed post id (the post id is preserved; only the alternate changes). The retcon orchestration tags each new alternate with a shared `replay_batch_id` in `prompt_hash` metadata — sidecar `extra` field — so audit can group them.
- **`auxiliary-tasks.rewrite_post`:** the auxiliary result accept-flow calls `switch_primary_alternate` after creating the new alternate from the auxiliary output's text + freshly-extracted delta set. The auxiliary spec depends on this primitive existing.
- **`fork`:** on fork, all alternates are copied; `delta_set_id` is rewritten to new IDs and the sidecar's `delta_set_id` references are remapped via the same lookup table the rest of the fork uses for delta id translation.

## REST and WebSocket surface

```
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/regenerate     # body: {steering_hint?, model_override?}
GET    /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/primary
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/pin     # body: {pinned: bool}
DELETE /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}
```

WS events emitted on the campaign channel:

```json
{ "type": "alternate_added",   "post_id": "...", "alternate_id": "..." }
{ "type": "primary_switched",  "post_id": "...", "from": "a_...", "to": "a_..." }
{ "type": "alternate_pinned",  "post_id": "...", "alternate_id": "...", "pinned": true }
{ "type": "alternate_deleted", "post_id": "...", "alternate_id": "..." }
```

## Frontend

Chevron strip under the latest model post (`frontend/src/routes/campaign/PostItem.tsx` — extend `ApiPost` types with `alternates` + `primary_alternate_id` + `is_latest_model_post`):

```
[GM] winifred catches your hand as you pour...
                                       ◀ 2 of 3 ▶ 📌 🔄 ✏️
```

Mid-history posts show the strip but chevrons are disabled with a tooltip:
> "Switching alternates is only available on the latest post. To revise an earlier post, use Retcon. To explore a different timeline, use Fork."

The 🔄 button opens the regenerate dialog (same-prompt / with-steering / model-override). The ✏️ opens `auxiliary-tasks.rewrite_post` (the auxiliary-tasks spec owns the dialog).

Streaming: `alternate_added` arrives during regenerate streaming; the frontend stores the partial text against `alternate_id` and reveals the chevron count once stream completes.

## Configuration

```yaml
swipes:
  max_alternates_per_post: 5
  purge_on_scene_close:
    enabled: false
    keep_primary: true
    keep_pinned: true
  auto_purge_older_than_days: 30          # null = never; default 30
  preapply_deltas: true                    # see "regenerate_post" design
```

## Audit, observability, performance

- `[swipe] campaign=... post=p_4710 added_alt=a_9012 (regenerate, prompt_hash=ab12...)`
- `[switch] campaign=... post=p_4710 from=a_9011 to=a_9012 (rewind 14 / apply 17)`

Performance targets:
- Regenerate latest: one normal-turn cost (no change vs. current `regenerate_last`).
- Switch primary: < 100 ms p95 (typical 5–20 deltas per set, single SQLite transaction).
- Pin/unpin: < 30 ms.

## Failure handling

| Failure | Behavior |
|---|---|
| Regenerate mid-stream error | Pre-applied deltas (if any committed before failure) rolled back via `delta_set_id` rewind; alternate not added; surface error |
| Switch primary apply fails | Transaction rolls back; prior primary intact; alternate stays in list, surface error |
| Scene .md rewrite fails after sidecar commit | Retry once; if still fails, mark sidecar with `pending_md_rewrite=true` and a background worker repairs (existing pattern used in scene-manager) |
| `current_alternate_delta_sets` materialized view drift | Daily integrity check warns; one-shot rebuild endpoint |
| Concurrent regenerate on same post | Second call gets `409 ALTERNATE_INFLIGHT`; UI prevents this client-side already |

## Test wiring

`backend/tests/orchestrator/test_alternates.py` (new):
- regenerate adds alternate, doesn't promote
- switch_primary rewinds + applies + rewrites .md
- pin/unpin
- delete alternate (non-primary only)
- max_alternates eviction (with pinned)
- latest-post-only enforcement at API layer
- swap failure → rollback to prior primary

`backend/tests/state_store/test_delta_sets.py`:
- `apply_delta_set` round-trip
- `rewind_delta_set` LIFO correctness
- `swap_delta_set` atomicity (kill mid-apply via fixture; verify state)

## Wiring touchpoints

- Migration `024_delta_sets_and_alternates.sql` adds the column + index; updates the `deltas` insert path in `state_store/store.py`.
- `scenes/storage.py`: sidecar reader/writer extended for `alternates` + `primary_alternate_id`.
- `scenes/manager.py`: `.md` rebuild from primaries (currently `write_body()` rewrites prose).
- `orchestrator/service.py`: replaces `regenerate_last` lines 304–338; adds new methods.
- `api/campaigns.py`: new routes (lines after the existing `regenerate` route at 670).
- `frontend/src/routes/campaign/PostItem.tsx` + `frontend/src/api/campaign.ts`: chevron strip + API client methods.
