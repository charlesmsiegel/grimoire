# Swipes / Alternates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-swipes-alternates-design.md`. Foundation for `retcon` (replay → alternates) and `auxiliary-tasks.rewrite_post` (accept → switch_primary). Introduces `delta_set_id` as first-class state-store concept.

**Architecture:** Six sequential branches.

- **A** `feature/swipes-A-delta-sets` — migration adds `delta_set_id` column; `apply_delta_set` / `rewind_delta_set` / `swap_delta_set` on `StateStore`.
- **B** `feature/swipes-B-sidecar` — scene sidecar reader/writer learns `alternates` and `primary_alternate_id`; `.md` rebuild from primaries.
- **C** `feature/swipes-C-regen` — `Orchestrator.regenerate_post` (pre-applies deltas, no auto-primary).
- **D** `feature/swipes-D-switch` — `switch_primary_alternate` using `swap_delta_set`; latest-post enforcement.
- **E** `feature/swipes-E-pin-purge` — pin/unpin/delete, retention vacuum.
- **F** `feature/swipes-F-rest-ws-frontend` — REST routes, WS events, chevron UI.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest + pytest-asyncio, Pydantic v2, React (TS) frontend. Test runner: `pytest backend/tests/orchestrator -v` + `pytest backend/tests/state_store -v`.

---

## Conventions

- **Test runner:** `pytest backend/tests/<module> -v`. Async by default.
- **Lint:** `ruff check`, `ruff format` before every commit.
- **Worktrees** under `.worktrees/`; rebase-merge to main.
- **Commit footer:** `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- **Latest migration:** 023 (or whatever transient-state has bumped it to). This plan claims the next free number — `024_delta_sets_and_alternates.sql` — but if transient-state lands first, renumber accordingly. (Coordination between plan authors: check `backend/src/grimoire/storage/migrations/` before starting.)

---

## Branch setup

- [ ] **Step S1: Create worktrees**

```powershell
git worktree add .worktrees/swipes-A-delta-sets       -b feature/swipes-A-delta-sets       main
git worktree add .worktrees/swipes-B-sidecar          -b feature/swipes-B-sidecar          main
git worktree add .worktrees/swipes-C-regen            -b feature/swipes-C-regen            main
git worktree add .worktrees/swipes-D-switch           -b feature/swipes-D-switch           main
git worktree add .worktrees/swipes-E-pin-purge        -b feature/swipes-E-pin-purge        main
git worktree add .worktrees/swipes-F-rest-ws-frontend -b feature/swipes-F-rest-ws-frontend main
```

Rebase each onto main as upstream branches merge.

---

# Branch A — delta_set_id + State Store helpers

### Task A1: Migration

**Files:**
- Create: `backend/src/grimoire/storage/migrations/024_delta_sets_and_alternates.sql`

- [ ] **Step 1: SQL**

```sql
-- backend/src/grimoire/storage/migrations/024_delta_sets_and_alternates.sql
ALTER TABLE deltas ADD COLUMN delta_set_id TEXT;
CREATE INDEX ix_deltas_set ON deltas(campaign_id, delta_set_id);

-- Materialized view of which delta sets are currently the primary for each post.
CREATE TABLE current_alternate_delta_sets (
    campaign_id     TEXT NOT NULL,
    branch_id       TEXT NOT NULL,
    post_id         TEXT NOT NULL,
    delta_set_id    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id, post_id)
);

CREATE INDEX ix_current_alt_sets_campaign
    ON current_alternate_delta_sets(campaign_id, branch_id);
```

- [ ] **Step 2: Verify migration applies**

```powershell
pytest backend/tests/test_storage.py -v -k migrations
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add backend/src/grimoire/storage/migrations/024_delta_sets_and_alternates.sql
git commit -m @'
feat(swipes): migration 024 - delta_set_id column + materialized view

Adds delta_set_id to deltas (nullable for back-compat with existing
ungrouped rows). New current_alternate_delta_sets table tracks which
delta set is the current primary per post; rebuilt on every
switch_primary_alternate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@
```

### Task A2: StateStore methods

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py` — add `apply_delta_set`, `rewind_delta_set`, `swap_delta_set`, `current_delta_set_for`.
- Modify: `backend/src/grimoire/state_store/delta_log.py` — thread `delta_set_id` through `DeltaRecord` + insert path.
- Test: `backend/tests/state_store/test_delta_sets.py` (new).

- [ ] **Step 1: Failing test**

```python
# backend/tests/state_store/test_delta_sets.py
"""delta_set_id first-class on StateStore (spec 2026-05-19-swipes-alternates)."""

from __future__ import annotations

import pytest

from grimoire.state_store import StateStore
from grimoire.types.state import DeltaKind, StateDelta


async def test_apply_delta_set_tags_every_delta(store: StateStore, seeded_campaign):
    delta = StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope="character_state",
        target_id="char_x",
        after={"mood": "guarded"},
        confidence=1.0,
        source="test",
    )
    records = await store.apply_delta_set(
        deltas=[delta],
        delta_set_id="ds_abc",
        campaign_id=seeded_campaign,
        branch_id=f"{seeded_campaign}:main",
        turn_id="t_1",
        source="orchestrator:regenerate",
    )
    assert len(records) == 1
    assert records[0].delta_set_id == "ds_abc"


async def test_rewind_delta_set_lifo(store, seeded_campaign):
    deltas = [
        StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, target_scope="character_state",
                   target_id="char_x", after={"mood": "a"}, confidence=1.0, source="t"),
        StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, target_scope="character_state",
                   target_id="char_x", after={"mood": "b"}, confidence=1.0, source="t"),
    ]
    await store.apply_delta_set(deltas=deltas, delta_set_id="ds_1",
                                campaign_id=seeded_campaign,
                                branch_id=f"{seeded_campaign}:main",
                                turn_id="t_1", source="test")
    reversed_records = await store.rewind_delta_set(
        delta_set_id="ds_1",
        campaign_id=seeded_campaign,
        branch_id=f"{seeded_campaign}:main",
    )
    # LIFO order: last applied is first reversed
    assert reversed_records[0].after == {"mood": "b"}
    assert reversed_records[1].after == {"mood": "a"}


async def test_swap_delta_set_atomic_rollback_on_apply_failure(store, seeded_campaign):
    original = StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, target_scope="character_state",
                          target_id="char_x", after={"mood": "calm"}, confidence=1.0, source="t")
    await store.apply_delta_set(deltas=[original], delta_set_id="ds_orig",
                                campaign_id=seeded_campaign,
                                branch_id=f"{seeded_campaign}:main",
                                turn_id="t_1", source="test")
    # Construct a delta that will violate a unique constraint or similar
    bad_delta = StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, target_scope="character_state",
                           target_id="char_x", after=None, confidence=1.0, source="t")  # None after raises
    with pytest.raises(Exception):
        await store.swap_delta_set(
            rewind_set_id="ds_orig",
            apply_deltas=[bad_delta],
            apply_set_id="ds_new",
            campaign_id=seeded_campaign,
            branch_id=f"{seeded_campaign}:main",
            turn_id="t_1",
            source="test",
        )
    # Original delta still in effect; ds_orig still tagged
    current = await store.current_delta_set_for(
        post_id=None,  # query by set_id
        campaign_id=seeded_campaign,
        branch_id=f"{seeded_campaign}:main",
        set_id="ds_orig",
    )
    assert current is not None      # un-rewound


async def test_current_delta_set_for_post(store, seeded_campaign):
    deltas = [StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, target_scope="character_state",
                         target_id="char_x", after={"mood": "x"}, confidence=1.0, source="t")]
    await store.apply_delta_set(deltas=deltas, delta_set_id="ds_p",
                                campaign_id=seeded_campaign,
                                branch_id=f"{seeded_campaign}:main",
                                turn_id="t_1", source="test")
    await store.set_current_alternate_delta_set(
        campaign_id=seeded_campaign,
        branch_id=f"{seeded_campaign}:main",
        post_id="p_1",
        delta_set_id="ds_p",
    )
    assert (await store.current_delta_set_for(
        post_id="p_1",
        campaign_id=seeded_campaign,
        branch_id=f"{seeded_campaign}:main",
    )) == "ds_p"
```

- [ ] **Step 2: Run, expect FAIL**

```powershell
pytest backend/tests/state_store/test_delta_sets.py -v
```

Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement helpers**

```python
# backend/src/grimoire/state_store/store.py — add methods

async def apply_delta_set(
    self,
    *,
    deltas: list[StateDelta],
    delta_set_id: str,
    campaign_id: str,
    branch_id: str,
    turn_id: str,
    source: str,
) -> list[DeltaRecord]:
    """Apply all deltas atomically, tagging each with delta_set_id."""
    records: list[DeltaRecord] = []
    async with self.db.connect_write() as conn:
        async with conn.transaction():
            for d in deltas:
                rec = await self._apply_delta_inner(
                    conn, d,
                    campaign_id=campaign_id, branch_id=branch_id,
                    turn_id=turn_id, source=source,
                    delta_set_id=delta_set_id,
                )
                records.append(rec)
    return records


async def rewind_delta_set(
    self,
    delta_set_id: str,
    *,
    campaign_id: str,
    branch_id: str,
) -> list[DeltaRecord]:
    """LIFO reverse every delta with this delta_set_id."""
    async with self.db.connect_write() as conn:
        async with conn.transaction():
            rows = await conn.execute_fetchall(
                "SELECT * FROM deltas WHERE campaign_id=? AND branch_id=? "
                "AND delta_set_id=? AND reversed_at IS NULL "
                "ORDER BY applied_at DESC",
                (campaign_id, branch_id, delta_set_id),
            )
            reversed_records = []
            for r in rows:
                rec = await self._reverse_delta_row(conn, r)
                reversed_records.append(rec)
    return reversed_records


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
) -> SwapResult:
    """Atomic rewind + apply. Rolls back both on apply failure."""
    async with self.db.connect_write() as conn:
        async with conn.transaction():
            rewound = []
            for r in await conn.execute_fetchall(
                "SELECT * FROM deltas WHERE campaign_id=? AND branch_id=? "
                "AND delta_set_id=? AND reversed_at IS NULL "
                "ORDER BY applied_at DESC",
                (campaign_id, branch_id, rewind_set_id),
            ):
                rewound.append(await self._reverse_delta_row(conn, r))
            applied = []
            for d in apply_deltas:
                applied.append(await self._apply_delta_inner(
                    conn, d,
                    campaign_id=campaign_id, branch_id=branch_id,
                    turn_id=turn_id, source=source,
                    delta_set_id=apply_set_id,
                ))
    return SwapResult(rewound=rewound, applied=applied)


async def set_current_alternate_delta_set(
    self,
    *,
    campaign_id: str,
    branch_id: str,
    post_id: str,
    delta_set_id: str,
) -> None:
    async with self.db.connect_write() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO current_alternate_delta_sets "
            "(campaign_id, branch_id, post_id, delta_set_id, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (campaign_id, branch_id, post_id, delta_set_id),
        )


async def current_delta_set_for(
    self,
    *,
    post_id: str | None,
    campaign_id: str,
    branch_id: str,
    set_id: str | None = None,
) -> str | None:
    async with self.db.connect_read() as conn:
        if post_id is not None:
            r = await conn.execute_fetchone(
                "SELECT delta_set_id FROM current_alternate_delta_sets "
                "WHERE campaign_id=? AND branch_id=? AND post_id=?",
                (campaign_id, branch_id, post_id),
            )
            return r["delta_set_id"] if r else None
        # Lookup by set_id existence check
        r = await conn.execute_fetchone(
            "SELECT delta_set_id FROM current_alternate_delta_sets "
            "WHERE campaign_id=? AND branch_id=? AND delta_set_id=?",
            (campaign_id, branch_id, set_id),
        )
        return r["delta_set_id"] if r else None
```

`SwapResult` is a small dataclass:

```python
@dataclass(frozen=True, slots=True)
class SwapResult:
    rewound: list[DeltaRecord]
    applied: list[DeltaRecord]
```

`_apply_delta_inner` is the existing `apply_delta` body refactored to take `delta_set_id`. Update the `deltas` INSERT to set the column.

- [ ] **Step 4: Run tests, expect PASS**

```powershell
pytest backend/tests/state_store/test_delta_sets.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit + merge A**

```powershell
ruff check backend/src/grimoire/state_store backend/tests/state_store
git add -A
git commit -m @'
feat(swipes): delta_set_id first-class on StateStore

apply_delta_set/rewind_delta_set/swap_delta_set atomic helpers;
current_alternate_delta_sets materialized view; backwards-compatible
with legacy ungrouped deltas (NULL delta_set_id).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
'@

git checkout main
git rebase feature/swipes-A-delta-sets
```

---

# Branch B — Scene sidecar `alternates`

### Task B1: Sidecar schema + read/write

**Files:**
- Modify: `backend/src/grimoire/scenes/storage.py` — extend YAML reader/writer.
- Modify: `backend/src/grimoire/scenes/types.py` — `Alternate` dataclass + `Post.alternates` + `Post.primary_alternate_id`.
- Test: `backend/tests/scenes/test_alternates_sidecar.py`.

- [ ] **Step 1: Failing test**

```python
# backend/tests/scenes/test_alternates_sidecar.py
async def test_round_trip_alternates(scene_storage, tmp_path):
    scene = make_scene_with_alternates(post_id="p_4710", alternates=[
        Alternate(id="a_9011", post_id="p_4710", text="A", delta_set_id="ds_1",
                  author_kind="model", model="opus", prompt_hash="h1",
                  tokens=420, pinned=False, is_primary=False, created_at=...),
        Alternate(id="a_9012", post_id="p_4710", text="B", delta_set_id="ds_2",
                  author_kind="model", model="opus", prompt_hash="h2",
                  tokens=420, pinned=True, is_primary=True, created_at=...),
    ], primary="a_9012")
    await scene_storage.write_sidecar(scene)
    loaded = await scene_storage.read_sidecar(scene.id)
    assert loaded.posts[0].primary_alternate_id == "a_9012"
    assert {a.id for a in loaded.posts[0].alternates} == {"a_9011", "a_9012"}


async def test_user_post_has_single_implicit_alternate(scene_storage, tmp_path):
    # User posts get one implicit alternate; sidecar may omit or include it.
    ...
```

- [ ] **Step 2: Implement YAML shape**

In `scenes/storage.py`, extend the `posts:` block writer:

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
        text: "..."
        author_kind: model
        model: claude-opus-4-7
        prompt_hash: "ab12"
        tokens: 420
        pinned: false
        is_primary: false
        created_at: 2026-05-19T14:00:00Z
      - id: a_9012
        ...
```

`scenes/types.py` adds:

```python
@dataclass(frozen=True, slots=True)
class Alternate:
    id: str
    post_id: str
    text: str
    delta_set_id: str
    author_kind: AuthorKind
    model: str | None
    prompt_hash: str | None
    steering_hint: str | None
    created_at: datetime
    tokens: int | None
    pinned: bool = False
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class Post:
    # existing fields ...
    alternates: list[Alternate] = field(default_factory=list)
    primary_alternate_id: str | None = None
```

- [ ] **Step 3: Tests PASS**

```powershell
pytest backend/tests/scenes/test_alternates_sidecar.py -v
```

- [ ] **Step 4: Commit B1**

### Task B2: `.md` rebuild from primaries

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py` — `rebuild_md_from_primaries(scene_id)`.
- Test: `backend/tests/scenes/test_md_rebuild.py`.

- [ ] **Step 1: Failing test**

```python
async def test_md_rebuild_uses_only_primary_alternate_text(manager, store):
    # Set up scene with one post, two alternates, primary=second
    ...
    await manager.rebuild_md_from_primaries("s_1")
    md = (await manager.read_md("s_1"))
    assert "B alternate body" in md
    assert "A alternate body" not in md


async def test_user_posts_render_as_themselves(manager, store):
    # User posts don't have a meaningful alternates list; render their text directly
    ...
```

- [ ] **Step 2: Implement** — read sidecar, walk posts in order, for each pick `primary_alternate_id` text (or fall back to first alternate, or post.body for user posts that haven't been alternate-tracked), reformat to `## Post N — Author\n\n<text>\n` blocks.

- [ ] **Step 3: Commit + merge B.**

---

# Branch C — Orchestrator `regenerate_post`

### Task C1: Replace `regenerate_last` with `regenerate_post`

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py:304–338` — refactor `regenerate_last` → `regenerate_post`.
- Test: `backend/tests/orchestrator/test_regenerate_post.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_regenerate_pre_applies_deltas_and_creates_alternate(
    orchestrator, store, scene_manager, seeded_state,
):
    post_id = seeded_state.last_model_post_id
    result = await orchestrator.regenerate_post(
        campaign_id=seeded_state.campaign_id,
        post_id=post_id,
    )
    assert result.new_alternate_id is not None
    # Alternate exists in sidecar
    scene = await scene_manager.get_scene_containing(post_id)
    post = next(p for p in scene.posts if p.id == post_id)
    assert any(a.id == result.new_alternate_id for a in post.alternates)
    # And primary not auto-promoted
    assert post.primary_alternate_id != result.new_alternate_id
    # Deltas pre-applied with the new delta_set_id
    new_ds = next(a.delta_set_id for a in post.alternates if a.id == result.new_alternate_id)
    assert await store.current_delta_set_for(
        post_id=None,
        campaign_id=seeded_state.campaign_id,
        branch_id=f"{seeded_state.campaign_id}:main",
        set_id=new_ds,
    ) == new_ds


async def test_regenerate_rejects_non_latest_post(orchestrator, seeded_state):
    earlier_post = seeded_state.posts[-3].id     # not the latest
    with pytest.raises(LatestPostOnly):
        await orchestrator.regenerate_post(
            campaign_id=seeded_state.campaign_id,
            post_id=earlier_post,
        )


async def test_regenerate_with_steering_hint_threads_through(...):
    # The steering_hint reaches the assembled prompt
    ...


async def test_regenerate_failure_rolls_back_pre_applied_deltas(...):
    # If extraction fails post-stream, rewind the new delta set; alternate not added.
    ...
```

- [ ] **Step 2: Implementation outline**

```python
# backend/src/grimoire/orchestrator/service.py
@dataclass(frozen=True, slots=True)
class RegenerateResult:
    post_id: str
    new_alternate_id: str
    delta_set_id: str


async def regenerate_post(
    self,
    *,
    campaign_id: str,
    post_id: str,
    steering_hint: str | None = None,
    model_override: str | None = None,
) -> RegenerateResult:
    # 1. Validate latest-post
    scene = await self.scenes.get_scene_containing(post_id)
    if scene.last_model_post_id != post_id:
        raise LatestPostOnly(post_id)

    # 2. Re-run canonical turn against the same player_input (from scene.turn.player_input)
    turn = await self.scenes.get_turn_for_post(post_id)
    new_alt_id = f"a_{uuid7()}"
    new_ds_id = f"ds_{uuid7()}"

    try:
        # 3. Build context; stream LLM; collect text
        text, prompt_hash, tokens, model_used = await self._run_canonical_generation(
            campaign_id=campaign_id,
            player_input=turn.player_input,
            steering_hint=steering_hint,
            model_override=model_override,
            post_id=post_id,
        )
        # 4. Run extractor
        deltas = await self.extractor.extract(text, scene, campaign_id, ...).deltas
        # 5. Pre-apply with new delta_set_id
        await self.store.apply_delta_set(
            deltas=deltas, delta_set_id=new_ds_id,
            campaign_id=campaign_id, branch_id=f"{campaign_id}:main",
            turn_id=turn.id, source="orchestrator:regenerate",
        )
        # 6. Append alternate to sidecar (not primary)
        await self.scenes.append_alternate(post_id, Alternate(
            id=new_alt_id, post_id=post_id, text=text,
            delta_set_id=new_ds_id, author_kind="model",
            model=model_used, prompt_hash=prompt_hash,
            steering_hint=steering_hint, tokens=tokens,
            pinned=False, is_primary=False, created_at=_now(),
        ))
        # 7. Emit WS event
        await self.events.emit_alternate_added(campaign_id, post_id, new_alt_id)
        return RegenerateResult(post_id=post_id, new_alternate_id=new_alt_id, delta_set_id=new_ds_id)
    except Exception:
        # Rollback any pre-applied deltas
        await self.store.rewind_delta_set(
            new_ds_id, campaign_id=campaign_id, branch_id=f"{campaign_id}:main",
        )
        raise
```

The existing `regenerate_last` callers (API route) update to call `regenerate_post(post_id=latest_post_id)`.

- [ ] **Step 3-N: Tests PASS, commit, merge C.**

---

# Branch D — `switch_primary_alternate`

### Task D1: Atomic swap + .md rebuild

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py` — add `switch_primary_alternate`.
- Test: `backend/tests/orchestrator/test_switch_primary.py`.

- [ ] **Step 1: Failing test**

```python
async def test_switch_primary_swaps_delta_set_and_rewrites_md(
    orchestrator, scene_manager, store, seeded_state_with_two_alternates,
):
    post_id, alt_a, alt_b = seeded_state_with_two_alternates
    result = await orchestrator.switch_primary_alternate(
        campaign_id=seeded_state_with_two_alternates.campaign_id,
        post_id=post_id, alternate_id=alt_b,
    )
    scene = await scene_manager.get_scene_containing(post_id)
    post = next(p for p in scene.posts if p.id == post_id)
    assert post.primary_alternate_id == alt_b
    md = await scene_manager.read_md(scene.id)
    assert get_alternate(post, alt_b).text in md
    assert get_alternate(post, alt_a).text not in md


async def test_switch_atomic_on_apply_failure(...):
    # swap_delta_set raises; prior primary unchanged
    ...


async def test_switch_rejects_non_latest_post(...):
    # latest-post-only enforced at the orchestrator API boundary
    ...
```

- [ ] **Step 2: Implementation**

```python
async def switch_primary_alternate(
    self,
    *,
    campaign_id: str,
    post_id: str,
    alternate_id: str,
) -> SwitchResult:
    scene = await self.scenes.get_scene_containing(post_id)
    if scene.last_model_post_id != post_id:
        raise LatestPostOnly(post_id)
    post = next(p for p in scene.posts if p.id == post_id)
    target = next((a for a in post.alternates if a.id == alternate_id), None)
    if target is None:
        raise AlternateNotFound(alternate_id)
    if post.primary_alternate_id == alternate_id:
        return SwitchResult(unchanged=True)
    current = next(a for a in post.alternates if a.id == post.primary_alternate_id)
    # Atomic rewind current + apply target's deltas
    # target's deltas are already in DB tagged with target.delta_set_id, but
    # rewound/applied tracking uses the materialized view + swap helper.
    await self.store.swap_delta_set(
        rewind_set_id=current.delta_set_id,
        apply_deltas=[],   # already applied; swap re-marks active set
        apply_set_id=target.delta_set_id,
        campaign_id=campaign_id,
        branch_id=f"{campaign_id}:main",
        turn_id=scene.last_turn_id,
        source="orchestrator:switch-primary",
    )
    # Update sidecar primary pointer
    await self.scenes.set_primary_alternate(post_id, alternate_id)
    # Rebuild .md
    await self.scenes.rebuild_md_from_primaries(scene.id)
    # Update materialized view
    await self.store.set_current_alternate_delta_set(
        campaign_id=campaign_id, branch_id=f"{campaign_id}:main",
        post_id=post_id, delta_set_id=target.delta_set_id,
    )
    # Continuity contradiction check on the new primary's deltas
    await self.continuity.check_for_post(post_id)
    # Emit WS
    await self.events.emit_primary_switched(campaign_id, post_id, current.id, alternate_id)
    return SwitchResult(unchanged=False, from_alt=current.id, to_alt=alternate_id)
```

The "swap with empty apply_deltas" is the trick: both delta sets are already in the database (one currently rewound, one currently applied). The swap helper re-marks the current materialized-view target.

To make swap_delta_set work with "already-applied deltas" semantics, modify it to handle `apply_deltas=[]` specially: if the apply set has rows in the deltas table already, just clear their `reversed_at` and update the materialized view.

This swap with re-application of an existing set needs a second helper:

```python
async def re_activate_delta_set(
    self,
    *,
    set_id: str,
    campaign_id: str,
    branch_id: str,
) -> int:
    async with self.db.connect_write() as conn:
        result = await conn.execute(
            "UPDATE deltas SET reversed_at=NULL "
            "WHERE campaign_id=? AND branch_id=? AND delta_set_id=? AND reversed_at IS NOT NULL",
            (campaign_id, branch_id, set_id),
        )
    return result.rowcount
```

Then `switch_primary_alternate` calls rewind on the old, re-activate on the new — both inside a single transaction.

- [ ] **Step 3-N: Tests PASS, commit, merge D.**

---

# Branch E — Pin/unpin/delete + retention

### Task E1: Pin / unpin / delete

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py` — add `pin_alternate`, `unpin_alternate`, `delete_alternate`.
- Test: `backend/tests/orchestrator/test_alternate_lifecycle.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_pin_alternate_sets_flag(...):
async def test_delete_primary_rejected(...):
async def test_delete_non_primary_rewinds_its_delta_set(...):
async def test_max_alternates_evicts_oldest_non_pinned_on_regen(...):
```

- [ ] **Step 2: Implementation**

```python
async def pin_alternate(self, *, post_id, alternate_id, pinned: bool):
    await self.scenes.update_alternate(post_id, alternate_id, pinned=pinned)
    await self.events.emit_alternate_pinned(... pinned ...)


async def delete_alternate(self, *, post_id, alternate_id):
    scene = await self.scenes.get_scene_containing(post_id)
    post = next(p for p in scene.posts if p.id == post_id)
    if post.primary_alternate_id == alternate_id:
        raise CannotDeletePrimary()
    target = next(a for a in post.alternates if a.id == alternate_id)
    # Rewind its delta set (cleanup pre-applied state)
    await self.store.rewind_delta_set(
        target.delta_set_id,
        campaign_id=scene.campaign_id, branch_id=f"{scene.campaign_id}:main",
    )
    await self.scenes.remove_alternate(post_id, alternate_id)
    await self.events.emit_alternate_deleted(...)
```

Eviction policy in `regenerate_post` after `append_alternate`:

```python
# After append:
non_pinned = [a for a in post.alternates if not a.pinned and a.id != post.primary_alternate_id]
if len(non_pinned) > self.config.swipes.max_alternates_per_post:
    oldest = min(non_pinned, key=lambda a: a.created_at)
    await self.delete_alternate(post_id=post_id, alternate_id=oldest.id)
```

- [ ] **Step 3: Vacuum** — config `auto_purge_older_than_days: 30` (per spec default). A background coroutine in `orchestrator` (or the existing observability daemon) walks alternates older than the threshold (non-primary, non-pinned) and `delete_alternate`s them. Test exercises the sweep on a fixture with backdated alternates.

- [ ] **Step 4: Commit + merge E.**

---

# Branch F — REST + WS + Frontend

### Task F1: REST routes

**Files:**
- Create: `backend/src/grimoire/api/alternates.py`.
- Modify: `backend/src/grimoire/main.py` to mount the subrouter.
- Test: `backend/tests/api/test_alternate_routes.py`.

Routes:
```
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/regenerate
GET    /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/primary
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/pin    body: {pinned: bool}
DELETE /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}
```

- [ ] **Step 1: Failing test per route** — happy-path round-trips, 404 on missing alternate, 400 on latest-post-only violation.

- [ ] **Step 2-N: Implement + commit.**

### Task F2: WS events

Modify `backend/src/grimoire/api/stream.py:_FORWARDED_EVENTS` — add `alternate_added`, `primary_switched`, `alternate_pinned`, `alternate_deleted`. Test: existing WS broadcasting test pattern; assert events forwarded to client.

### Task F3: Frontend chevrons

**Files:**
- Modify: `frontend/src/api/campaign.ts` — extend `ApiPost` with `alternates`, `primary_alternate_id`, `is_latest_model_post`. Add client methods.
- Modify: `frontend/src/routes/campaign/PostItem.tsx` — chevron strip.
- Test: `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`.

Chevron strip UI:

```tsx
{post.alternates.length > 1 && (
  <div className="chevron-strip">
    <button disabled={!post.is_latest_model_post || cursor === 0} onClick={() => prev()}>◀</button>
    <span>{cursor + 1} of {post.alternates.length}</span>
    <button disabled={!post.is_latest_model_post || cursor === post.alternates.length - 1} onClick={() => next()}>▶</button>
    <button title={post.alternates[cursor].pinned ? "Unpin" : "Pin"} onClick={() => togglePin()}>📌</button>
    <button onClick={() => regenerate()}>🔄</button>
    <button onClick={() => openRewriteDialog()}>✏️</button>
  </div>
)}

{!post.is_latest_model_post && post.alternates.length > 1 && (
  <Tooltip>Switching alternates is only available on the latest post. To revise an earlier post, use Retcon. For a different timeline, use Fork.</Tooltip>
)}
```

`prev`/`next` call `POST /alternates/{id}/primary`. `regenerate` posts to `/regenerate`. `togglePin` calls `/pin`.

The rewrite dialog (`openRewriteDialog`) is owned by `auxiliary-tasks` and is wired in that plan; here we just dispatch the open intent.

- [ ] **Step 1: Failing component test** — render with `alternates.length === 2`; assert chevrons render; click prev → `primary` API called with prior alt's id.

- [ ] **Step 2-N: Implement + commit + merge F.**

---

# Integration check

- [ ] **Step F-end1: Full suite passes**

```powershell
pytest backend/tests -v --ignore=backend/tests/perf
npm test --prefix frontend
```

- [ ] **Step F-end2: Replace `regenerate_last` callers** — confirm no remaining references with `rg 'regenerate_last' backend/`. Should return nothing after this plan lands.

- [ ] **Step F-end3: Update memory + COMPLETED doc** — `docs/superpowers/specs/2026-05-19-swipes-alternates-COMPLETED.md` documenting deltas vs the design spec.
