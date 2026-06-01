# Post cascade-delete and cost-display relocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-post delete that truncates the scene from that post onward and reverts the derived state (with straddling-turn deltas sent to the review queue), and move the per-turn cost display from the generated post to the user post that triggered the turn.

**Architecture:** Feature 1 adds a suffix-truncation method to the Scene Manager, a coordinating `delete_post_cascade` on the Orchestrator that reuses the existing delta-reversal (`_reverse_turn_deltas`) and review-queue (`queue_for_review`) machinery, and a `DELETE` REST route. The frontend gets a delete button with an inline confirmation, refreshing the scene on success. Feature 2 is frontend-only: the Scene Pane maps each user post to the turn id of the model posts that follow it, and the Post Item renders the cost there.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic (backend), pytest-asyncio; TypeScript / React / Vitest (frontend).

---

## File Structure

**Backend**
- `backend/src/grimoire/orchestrator/errors.py` — add `SceneClosedError`.
- `backend/src/grimoire/types/orchestrator.py` — add `CascadeDeleteResult`.
- `backend/src/grimoire/scenes/manager.py` — add `truncate_scene_from`.
- `backend/src/grimoire/orchestrator/service.py` — add `_reverse_and_requeue_turn` + `delete_post_cascade`.
- `backend/src/grimoire/api/alternates.py` — add `DELETE .../posts/{post_id}` route.
- Tests: `backend/tests/scenes/test_manager.py`, `backend/tests/orchestrator/test_service.py`, `backend/tests/api/test_alternate_routes.py`.

**Frontend**
- `frontend/src/api/campaign/api.ts` — add `deletePost`.
- `frontend/src/routes/campaign/PostItem.tsx` — delete button + inline confirm; cost prop.
- `frontend/src/routes/campaign/ScenePane.tsx` — cost map, subsequent-count, thread props.
- `frontend/src/routes/campaign/PlayView.tsx` — pass `refresh` to ScenePane.
- Tests: `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`, `frontend/src/routes/campaign/__tests__/ScenePane.test.tsx`.

---

## Task 1: `SceneClosedError` exception

**Files:**
- Modify: `backend/src/grimoire/orchestrator/errors.py`

- [ ] **Step 1: Add the exception class**

Add after `NoTurnsToUndoError` (around line 39):

```python
class SceneClosedError(OrchestratorError):
    """Raised when a mutating op (e.g. cascade delete) targets a closed scene."""

    http_status = 409

    def __init__(self, scene_id: str) -> None:
        super().__init__(f"scene {scene_id!r} is closed")
        self.scene_id = scene_id
```

- [ ] **Step 2: Export it**

In the `__all__` list at the bottom, add `"SceneClosedError",` (keep alphabetical order — between `RetconInFlightError` and `TurnAlreadyInProgressError`).

- [ ] **Step 3: Verify import resolves**

Run: `cd backend && uv run python -c "from grimoire.orchestrator.errors import SceneClosedError; print(SceneClosedError('s1').http_status)"`
Expected: prints `409`

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/orchestrator/errors.py
git commit -m "feat(orchestrator): add SceneClosedError"
```

---

## Task 2: `CascadeDeleteResult` model

**Files:**
- Modify: `backend/src/grimoire/types/orchestrator.py`

- [ ] **Step 1: Add the result model**

Add after `UndoResult` (around line 149):

```python
class CascadeDeleteResult(BaseModel):
    deleted_post_ids: list[str] = Field(default_factory=list)
    reversed_turn_ids: list[TurnId] = Field(default_factory=list)
    requeued_review_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

`BaseModel`, `Field`, and `TurnId` are already imported in this file (used by `UndoResult`).

- [ ] **Step 2: Verify import resolves**

Run: `cd backend && uv run python -c "from grimoire.types.orchestrator import CascadeDeleteResult; print(CascadeDeleteResult().model_dump())"`
Expected: prints `{'deleted_post_ids': [], 'reversed_turn_ids': [], 'requeued_review_ids': [], 'warnings': []}`

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/types/orchestrator.py
git commit -m "feat(orchestrator): add CascadeDeleteResult model"
```

---

## Task 3: `truncate_scene_from` in the Scene Manager

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Test: `backend/tests/scenes/test_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/scenes/test_manager.py`. First add `POST_DELETED` to the existing import from `grimoire.scenes` (top of file), then append this test:

```python
async def test_truncate_scene_from_removes_suffix(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(4):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    third = (await manager.get_posts(scene.id))[2]  # order_in_scene == 3
    removed = await manager.truncate_scene_from(third.id, source="cascade_delete")

    posts = await manager.get_posts(scene.id)
    assert [p.body for p in posts] == ["line 0", "line 1"]
    assert [p.order_in_scene for p in posts] == [1, 2]
    assert {p.body for p in removed} == {"line 2", "line 3"}
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.post_count == 2
    assert sum(1 for e in bus.events if e.type == POST_DELETED) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_manager.py::test_truncate_scene_from_removes_suffix -v`
Expected: FAIL with `AttributeError: 'SceneManager' object has no attribute 'truncate_scene_from'`

- [ ] **Step 3: Implement `truncate_scene_from`**

Add immediately after `delete_post` (after line ~1251, before `_find_post`) in `backend/src/grimoire/scenes/manager.py`:

```python
    async def truncate_scene_from(self, post_id: str, source: str) -> list[Post]:
        """Delete ``post_id`` and every post after it in the same scene.

        Suffix sibling of :meth:`delete_post`: because the removed posts are a
        contiguous tail, no order-shifting is needed. Rewrites the ``.md`` and
        sidecar, updates counts, drops the removed records, emits one
        ``POST_DELETED`` per removed post, and returns the removed posts.
        """
        scene, target = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            posts = await self.get_posts(scene.id)
            cut = target.order_in_scene
            kept = [p for p in posts if p.order_in_scene < cut]
            removed = [p for p in posts if p.order_in_scene >= cut]
            md_path, _ = self._scene_file_paths(scene)
            write_body(
                md_path,
                kept,
                heading_pattern=self.config.files.post_heading_pattern,
            )
            scene.post_count = len(kept)
            if scene.last_advance_at_post > scene.post_count:
                scene.last_advance_at_post = scene.post_count
            self._hydrate_records(scene)
            records = self._records_for(scene.id)
            self._post_records[scene.id] = {
                key: rec for key, rec in records.items() if int(key) < cut
            }
            self._write_sidecar(scene)
            self._known_body_hashes[scene.id] = content_hash(
                md_path.read_text(encoding="utf-8")
            )
            for removed_post in removed:
                await self._emit(
                    POST_DELETED,
                    scene,
                    order=removed_post.order_in_scene,
                    source=source,
                    post_id=removed_post.id,
                )
        return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_manager.py::test_truncate_scene_from_removes_suffix -v`
Expected: PASS

- [ ] **Step 5: Run the broader scene-manager suite to confirm no regressions**

Run: `cd backend && uv run pytest tests/scenes/test_manager.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/tests/scenes/test_manager.py
git commit -m "feat(scenes): add truncate_scene_from suffix delete"
```

---

## Task 4: `delete_post_cascade` on the Orchestrator

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Test: `backend/tests/orchestrator/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/orchestrator/test_service.py`. At the top, extend the imports:

```python
from grimoire.orchestrator import (
    NoTurnsToUndoError,
    OrchestratorConfig,
    OrchestratorService,
    UnknownCampaignError,
    UnknownPCError,
)
from grimoire.orchestrator.errors import SceneClosedError
from grimoire.scenes import AuthorKind, new_post
```

(If `new_post` / `AuthorKind` are already imported, don't duplicate.) Then append:

```python
# --------------------------------------------------------------------------- #
# Cascade delete
# --------------------------------------------------------------------------- #


def _seed_applied(fake_store, *, campaign_id, turn_id, target_id):
    """Record a fake applied delta for ``turn_id`` so the cascade can reverse it."""
    from grimoire.types.state import DeltaKind, StateDelta

    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope="campaign-sqlite",
        target_id=target_id,
        target_table="facts",
        after={"text": target_id},
        confidence=0.95,
        source="extractor",
    )
    did = f"d_seed_{target_id}"
    fake_store.applied.append(
        {"id": did, "delta": delta, "source": "extractor",
         "turn_id": turn_id, "campaign_id": campaign_id}
    )
    return did


async def test_cascade_delete_reverses_fully_contained_turns(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair",
                            body="hi", is_player=True))
    m1 = new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1")
    m2 = new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T2")
    await scene_manager.append_post(scene.id, m1)
    await scene_manager.append_post(scene.id, m2)
    d1 = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")
    d2 = _seed_applied(fake_store, campaign_id="c1", turn_id="T2", target_id="fact-2")

    orch = _build_orch(
        scene_manager=scene_manager, event_bus=event_bus, fake_store=fake_store,
        fake_gateway=fake_gateway, fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    remaining = await scene_manager.get_posts(scene.id)
    assert [p.body for p in remaining] == ["hi"]
    assert set(fake_store.reversed_ids) == {d1, d2}
    assert set(result.reversed_turn_ids) == {"T1", "T2"}
    assert result.requeued_review_ids == []
    assert len(result.deleted_post_ids) == 2


async def test_cascade_delete_requeues_straddling_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair",
                            body="hi", is_player=True))
    # Split turn T1 produces two model posts; T2 a third.
    await scene_manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"))
    await scene_manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T1"))
    await scene_manager.append_post(
        scene.id, new_post(author_kind=AuthorKind.NARRATOR, body="m3", is_player=False, turn_id="T2"))
    d1 = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")
    d2 = _seed_applied(fake_store, campaign_id="c1", turn_id="T2", target_id="fact-2")

    review_events: list = []
    event_bus.subscribe("review_item_added", review_events.append)

    orch = _build_orch(
        scene_manager=scene_manager, event_bus=event_bus, fake_store=fake_store,
        fake_gateway=fake_gateway, fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m2")  # mid-split → T1 straddles
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    remaining = await scene_manager.get_posts(scene.id)
    assert [p.body for p in remaining] == ["hi", "m1"]
    # Both turns reversed; only the straddling turn (T1) is re-queued.
    assert set(fake_store.reversed_ids) == {d1, d2}
    assert len(fake_store.reviewed) == 1
    assert fake_store.reviewed[0]["source"] == "cascade_delete"
    assert len(result.requeued_review_ids) == 1
    assert len(review_events) == 1


async def test_cascade_delete_rejects_closed_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    post = new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1")
    await scene_manager.append_post(scene.id, post)
    appended = (await scene_manager.get_posts(scene.id))[0]
    await scene_manager.close_scene(scene.id, closed_at_turn="T1")

    orch = _build_orch(
        scene_manager=scene_manager, event_bus=event_bus, fake_store=fake_store,
        fake_gateway=fake_gateway, fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    with pytest.raises(SceneClosedError):
        await orch.delete_post_cascade("c1", scene.id, appended.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/orchestrator/test_service.py -k cascade_delete -v`
Expected: FAIL with `AttributeError: 'OrchestratorService' object has no attribute 'delete_post_cascade'`

- [ ] **Step 3: Add the `CascadeDeleteResult` / `SceneClosedError` imports**

In `backend/src/grimoire/orchestrator/service.py`, add `SceneClosedError` to the existing import from `.errors` (the block that imports `NoTurnsToUndoError`), and add `CascadeDeleteResult` to the existing import from `grimoire.types.orchestrator` (the block that imports `UndoResult`).

- [ ] **Step 4: Implement the reverse-and-requeue helper**

Add immediately after `_reverse_turn_deltas` (around line 1827) in `service.py`:

```python
    async def _reverse_and_requeue_turn(
        self, campaign_id: CampaignId, turn_id: TurnId
    ) -> tuple[list[str], list[str]]:
        """Reverse a turn's deltas, then re-queue each for human review.

        Used for turns that straddle a cascade-delete cut: the deltas can no
        longer be auto-trusted, so they are reversed and re-queued. Approve
        (existing review flow) re-applies; reject leaves them reversed.
        """
        log = await self._store.get_delta_log(
            campaign_id=campaign_id, turn_id=turn_id, include_reversed=False
        )
        reversed_ids: list[str] = []
        review_ids: list[str] = []
        for record in reversed(log):
            try:
                await self._store.reverse_delta(record.id)
                reversed_ids.append(record.id)
            except Exception as exc:
                logger.warning("reverse_delta(%s) failed: %s", record.id, exc)
                continue
            try:
                review_id = await self._store.queue_for_review(
                    delta=record,
                    source="cascade_delete",
                    campaign_id=campaign_id,
                )
            except Exception as exc:
                logger.warning("queue_for_review(%s) failed: %s", record.id, exc)
                continue
            if review_id:
                review_ids.append(str(review_id))
                await self._bus.emit(
                    Event(
                        type=events.REVIEW_ITEM_ADDED,
                        payload={
                            "campaign_id": campaign_id,
                            "review_id": review_id,
                            "turn_id": turn_id,
                        },
                    )
                )
        return reversed_ids, review_ids
```

- [ ] **Step 5: Implement `delete_post_cascade`**

Add directly after `undo_turn` (after line ~656, before the Retcon section comment) in `service.py`:

```python
    async def delete_post_cascade(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId,
        post_id: PostId,
    ) -> CascadeDeleteResult:
        """Delete ``post_id`` and every later post in the scene, reverting state.

        Fully-contained turns (all posts at/after the cut) have their deltas
        reversed. Straddling turns (split-mode turns with posts on both sides of
        the cut) are reversed and re-queued for human review. Rejected on a
        closed scene.
        """
        await self._require_campaign(campaign_id)
        scene = await self._scenes.get_scene(scene_id)
        if scene.campaign_id != campaign_id:
            raise OrchestratorError(
                f"scene {scene_id!r} does not belong to campaign {campaign_id!r}"
            )
        if scene.closed:
            raise SceneClosedError(scene_id)

        posts = await self._scenes.get_posts(scene_id)
        target = next((p for p in posts if p.id == post_id), None)
        if target is None:
            raise KeyError(f"post {post_id!r} not found in scene {scene_id!r}")
        cut = target.order_in_scene

        min_order: dict[str, int] = {}
        max_order: dict[str, int] = {}
        for p in posts:
            tid = p.turn_id
            if not tid:
                continue
            min_order[tid] = min(min_order.get(tid, p.order_in_scene), p.order_in_scene)
            max_order[tid] = max(max_order.get(tid, p.order_in_scene), p.order_in_scene)

        deleted_post_ids = [p.id for p in posts if p.order_in_scene >= cut]
        fully_contained = [t for t, lo in min_order.items() if lo >= cut]
        straddling = [
            t for t, lo in min_order.items() if lo < cut <= max_order[t]
        ]

        reversed_turn_ids: list[TurnId] = []
        requeued_review_ids: list[str] = []
        warnings: list[str] = []

        for tid in sorted(fully_contained, key=lambda t: min_order[t], reverse=True):
            try:
                ids = await self._reverse_turn_deltas(campaign_id, tid)
            except Exception as exc:
                warnings.append(f"failed to reverse turn {tid}: {exc}")
                continue
            if ids:
                reversed_turn_ids.append(tid)
            await self._bus.emit(
                Event(
                    type=events.TURN_UNDONE,
                    payload={
                        "campaign_id": campaign_id,
                        "turn_id": tid,
                        "reversed_deltas": ids,
                    },
                )
            )

        for tid in sorted(straddling, key=lambda t: min_order[t], reverse=True):
            try:
                ids, review_ids = await self._reverse_and_requeue_turn(campaign_id, tid)
            except Exception as exc:
                warnings.append(f"failed to reverse straddling turn {tid}: {exc}")
                continue
            if ids:
                reversed_turn_ids.append(tid)
            requeued_review_ids.extend(review_ids)
            await self._bus.emit(
                Event(
                    type=events.TURN_UNDONE,
                    payload={
                        "campaign_id": campaign_id,
                        "turn_id": tid,
                        "reversed_deltas": ids,
                    },
                )
            )

        await self._scenes.truncate_scene_from(post_id, source="cascade_delete")

        state = self._state_for(campaign_id)
        top = await self._recent_turn_ids(campaign_id, 1)
        state.last_turn_id = top[0] if top else None

        return CascadeDeleteResult(
            deleted_post_ids=deleted_post_ids,
            reversed_turn_ids=reversed_turn_ids,
            requeued_review_ids=requeued_review_ids,
            warnings=warnings,
        )
```

- [ ] **Step 6: Run the cascade tests to verify they pass**

Run: `cd backend && uv run pytest tests/orchestrator/test_service.py -k cascade_delete -v`
Expected: all three PASS

- [ ] **Step 7: Run the full orchestrator service suite for regressions**

Run: `cd backend && uv run pytest tests/orchestrator/test_service.py -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/tests/orchestrator/test_service.py
git commit -m "feat(orchestrator): add delete_post_cascade with state reversal"
```

---

## Task 5: `DELETE` REST route for a post

**Files:**
- Modify: `backend/src/grimoire/api/alternates.py`
- Test: `backend/tests/api/test_alternate_routes.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_alternate_routes.py`, extend `FakeOrchestrator` with a `delete_post_cascade` method (add inside the class, after `delete_alternate`):

```python
    async def delete_post_cascade(
        self, campaign_id: str, scene_id: str, post_id: str
    ) -> Any:
        self.calls.append(("cascade_delete", campaign_id, scene_id, post_id))

        @dataclass
        class _Result:
            deleted_post_ids: list
            reversed_turn_ids: list
            requeued_review_ids: list
            warnings: list

        return _Result(
            deleted_post_ids=[post_id], reversed_turn_ids=["T1"],
            requeued_review_ids=[], warnings=[],
        )
```

Then append these tests at the end of the file:

```python
def test_delete_post_route_calls_orchestrator(wire, client) -> None:
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_post_ids"] == ["p1"]
    assert body["reversed_turn_ids"] == ["T1"]
    assert wire.orchestrator.calls[0] == ("cascade_delete", "c1", "s1", "p1")


def test_delete_post_rejects_wrong_campaign(wire, client) -> None:
    response = client.delete("/api/campaigns/other/scenes/s1/posts/p1")
    assert response.status_code == 404


def test_delete_post_rejects_unknown_post(wire, client) -> None:
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p_nope")
    assert response.status_code == 404


def test_delete_post_closed_scene_is_409(wire, client) -> None:
    from grimoire.orchestrator.errors import SceneClosedError

    async def boom(**_kw):
        raise SceneClosedError("s1")

    wire.orchestrator.delete_post_cascade = boom  # type: ignore[method-assign]
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1")
    assert response.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/api/test_alternate_routes.py -k delete_post -v`
Expected: FAIL — the route returns 405 (Method Not Allowed) / 404 because no DELETE handler is registered for that path.

- [ ] **Step 3: Implement the route**

In `backend/src/grimoire/api/alternates.py`, add after `edit_post_body` (after line 162, before the `delete_alternate` route):

```python
@router.delete("/{campaign_id}/scenes/{scene_id}/posts/{post_id}")
async def delete_post(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    orchestrator: OrchestratorDep,
    scenes: ScenesDep,
) -> Any:
    """Delete a post and every post after it in the scene, reverting state.

    See ``docs/superpowers/specs/2026-05-31-post-delete-and-cost-display-design.md``.
    """
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        result = await orchestrator.delete_post_cascade(
            campaign_id=campaign_id, scene_id=scene_id, post_id=post_id
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
```

The `boom(**_kw)` fake in the test is called with keyword args, matching the `campaign_id=..., scene_id=..., post_id=...` call here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/api/test_alternate_routes.py -k delete_post -v`
Expected: all four PASS

- [ ] **Step 5: Lint + format the backend changes**

Run: `cd backend && uv run ruff check && uv run ruff format`
Expected: no errors; formatter may reformat — re-stage if so.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/api/alternates.py backend/tests/api/test_alternate_routes.py
git commit -m "feat(api): add DELETE post route for cascade delete"
```

---

## Task 6: Frontend API client `deletePost`

**Files:**
- Modify: `frontend/src/api/campaign/api.ts`

- [ ] **Step 1: Add the client method**

In `frontend/src/api/campaign/api.ts`, add right after the `deleteAlternate` method (around line 151):

```typescript
  deletePost: (campaignId: string, sceneId: string, postId: string) =>
    api.delete<{
      deleted_post_ids: string[];
      reversed_turn_ids: string[];
      requeued_review_ids: string[];
      warnings: string[];
    }>(`/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}`),
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/campaign/api.ts
git commit -m "feat(frontend): add deletePost API client"
```

---

## Task 7: PostItem delete button + inline confirm

**Files:**
- Modify: `frontend/src/routes/campaign/PostItem.tsx`
- Test: `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`:

```typescript
describe("PostItem delete", () => {
  beforeEach(() => mockIntersectionObserver());

  it("shows a delete button when the scene is open", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Delete post" })).toBeInTheDocument();
  });

  it("hides the delete button when the scene is closed", () => {
    render(
      <PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" sceneClosed />,
    );
    expect(screen.queryByRole("button", { name: "Delete post" })).toBeNull();
  });

  it("confirms then calls deletePost and onDeleted", async () => {
    const spy = vi.spyOn(campaignApi, "deletePost").mockResolvedValue({
      deleted_post_ids: ["p1"],
      reversed_turn_ids: [],
      requeued_review_ids: [],
      warnings: [],
    });
    const onDeleted = vi.fn();
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        campaignId="c1"
        subsequentCount={2}
        onDeleted={onDeleted}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete post" }));
    expect(screen.getByText(/2 following posts/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1"));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it("cancel closes the confirm without calling deletePost", () => {
    const spy = vi.spyOn(campaignApi, "deletePost");
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Delete post" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel delete" }));
    expect(screen.queryByRole("button", { name: "Confirm delete" })).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && pnpm test -- PostItem`
Expected: FAIL — no "Delete post" button exists yet.

- [ ] **Step 3: Add the new props**

In `frontend/src/routes/campaign/PostItem.tsx`, extend the `Props` interface (after `expressionsEnabledCharacters`):

```typescript
  /** Number of posts after this one in the scene (drives the confirm copy). */
  subsequentCount?: number;
  /** When true, the scene is closed — deletion is not offered. */
  sceneClosed?: boolean;
  /** Called after a successful delete so the caller can refresh the scene. */
  onDeleted?: () => void;
  /** Turn id whose cost should render on this (user) post; see Task 9. */
  costTurnId?: string;
```

And destructure them in the function signature:

```typescript
export function PostItem({
  post,
  pcs,
  images,
  isLatestModelPost = false,
  campaignId,
  presentCharacterRefs = [],
  expressionsEnabledCharacters,
  subsequentCount,
  sceneClosed = false,
  onDeleted,
  costTurnId,
}: Props) {
```

- [ ] **Step 4: Add delete state + handler**

After the `const [bodyOverride, setBodyOverride] = useState<string | null>(null);` line (~line 90), add:

```typescript
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const canDelete = !!campaignId && !sceneClosed;
```

After the `saveEdit` function (~line 121), add:

```typescript
  async function doDelete() {
    if (!campaignId) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await campaignApi.deletePost(campaignId, post.scene_id, post.id);
      onDeleted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleteBusy(false);
    }
  }
```

(No `finally` reset: on success the parent refresh unmounts this item; on error we reset and keep the confirm open.)

- [ ] **Step 5: Add the delete button**

In the `post-actions post-actions-icons` block, add as the last button (after the translate button, before the closing `</div>` at ~line 342):

```tsx
          {canDelete && (
            <button
              type="button"
              className="post-icon-btn post-delete"
              aria-label="Delete post"
              title="Delete"
              disabled={deleteBusy}
              onClick={() => setConfirmingDelete(true)}
            >
              🗑
            </button>
          )}
```

- [ ] **Step 6: Add the inline confirm strip**

Add immediately after the `post-actions` closing block (after the `)}` that ends the `campaignId && editDraft === null && (...)` action row, ~line 343):

```tsx
      {confirmingDelete && campaignId && (
        <div className="post-delete-confirm" role="alertdialog" aria-label="Confirm delete">
          <p className="post-delete-warning">
            Delete this post
            {subsequentCount && subsequentCount > 0
              ? ` and the ${subsequentCount} following ${
                  subsequentCount === 1 ? "post" : "posts"
                } in this scene`
              : ""}
            ? Facts and changes derived from {subsequentCount ? "them" : "it"} will be
            reverted. This cannot be undone.
          </p>
          <div className="post-delete-actions">
            <button
              type="button"
              className="post-delete-confirm-btn"
              aria-label="Confirm delete"
              disabled={deleteBusy}
              onClick={() => void doDelete()}
            >
              {deleteBusy ? "Deleting..." : "Delete"}
            </button>
            <button
              type="button"
              aria-label="Cancel delete"
              disabled={deleteBusy}
              onClick={() => setConfirmingDelete(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
```

- [ ] **Step 7: Run the delete tests to verify they pass**

Run: `cd frontend && pnpm test -- PostItem`
Expected: the new `PostItem delete` tests PASS. (Some existing `PostItem cost` tests will be updated in Task 9 — they may still pass here since cost rendering is unchanged so far.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/campaign/PostItem.tsx frontend/src/routes/campaign/__tests__/PostItem.test.tsx
git commit -m "feat(frontend): add per-post delete button with inline confirm"
```

---

## Task 8: Thread refresh, subsequent-count, and scene-closed through ScenePane and PlayView

**Files:**
- Modify: `frontend/src/routes/campaign/ScenePane.tsx`
- Modify: `frontend/src/routes/campaign/PlayView.tsx`
- Test: `frontend/src/routes/campaign/__tests__/ScenePane.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/campaign/__tests__/ScenePane.test.tsx`:

```typescript
describe("ScenePane delete wiring", () => {
  it("passes a delete button and forwards onPostDeleted", async () => {
    const { campaignApi } = await import("../../../api/campaign");
    const spy = vi.spyOn(campaignApi, "deletePost").mockResolvedValue({
      deleted_post_ids: ["p1"],
      reversed_turn_ids: [],
      requeued_review_ids: [],
      warnings: [],
    });
    const onPostDeleted = vi.fn();
    const scene = {
      id: "s1",
      campaign_id: "c1",
      post_count: 1,
      closed: false,
      present_character_refs: [],
    } as unknown as Parameters<typeof ScenePane>[0]["scene"];
    renderPane({
      posts: [{ ...makePost("p1"), is_player: true, author_kind: "pc" }],
      campaignId: "c1",
      scene,
      onPostDeleted,
    });
    const { fireEvent, waitFor } = await import("@testing-library/react");
    fireEvent.click(screen.getByRole("button", { name: "Delete post" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1"));
    await waitFor(() => expect(onPostDeleted).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && pnpm test -- ScenePane`
Expected: FAIL — `ScenePane` has no `onPostDeleted` prop and doesn't forward delete wiring.

- [ ] **Step 3: Add the `onPostDeleted` prop to ScenePane**

In `frontend/src/routes/campaign/ScenePane.tsx`, add to the `Props` interface (after `expressionsEnabledCharacters`):

```typescript
  onPostDeleted?: () => void;
```

Destructure it in the component signature (after `expressionsEnabledCharacters,`):

```typescript
  onPostDeleted,
```

- [ ] **Step 4: Pass delete-related props to PostItem**

Replace the `<PostItem ... />` block (lines ~97-108) with:

```tsx
      {posts.map((post) => (
        <PostItem
          key={post.id}
          post={post}
          pcs={pcs}
          images={byPost[post.id] ?? []}
          isLatestModelPost={post.id === latestModelPostId}
          campaignId={campaignId}
          presentCharacterRefs={scene?.present_character_refs ?? []}
          expressionsEnabledCharacters={expressionsEnabledCharacters}
          subsequentCount={
            scene ? Math.max(0, scene.post_count - post.order_in_scene - 1) : undefined
          }
          sceneClosed={scene?.closed ?? false}
          onDeleted={onPostDeleted}
        />
      ))}
```

- [ ] **Step 5: Pass `refresh` from PlayView**

In `frontend/src/routes/campaign/PlayView.tsx`, find the `<ScenePane ... />` usage (around line 175) and add the prop:

```tsx
              onPostDeleted={play.refresh}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && pnpm test -- ScenePane`
Expected: the new wiring test PASSES (plus existing ScenePane tests).

- [ ] **Step 7: Typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/campaign/ScenePane.tsx frontend/src/routes/campaign/PlayView.tsx frontend/src/routes/campaign/__tests__/ScenePane.test.tsx
git commit -m "feat(frontend): wire post delete refresh through ScenePane and PlayView"
```

---

## Task 9: Move cost display to the user post (Feature 2)

**Files:**
- Modify: `frontend/src/routes/campaign/ScenePane.tsx`
- Modify: `frontend/src/routes/campaign/PostItem.tsx`
- Test: `frontend/src/routes/campaign/__tests__/ScenePane.test.tsx`, `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`

- [ ] **Step 1: Update PostItem cost tests to reflect the new behavior**

In `frontend/src/routes/campaign/__tests__/PostItem.test.tsx`, replace the entire `describe("PostItem cost in header", ...)` block with:

```typescript
describe("PostItem cost in header", () => {
  it("displays cost on a post given a costTurnId when visible", async () => {
    mockIntersectionObserver();
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.012, input_tokens: 800, output_tokens: 350, call_count: 1 },
      { task: "extraction", total_usd: 0.001, input_tokens: 400, output_tokens: 50, call_count: 1 },
    ]);
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" costTurnId="t1" />);
    triggerIntersection();
    await waitFor(() => expect(screen.getByLabelText("Turn cost")).toHaveTextContent("$0.0130"));
  });

  it("does not show cost when no costTurnId is provided", () => {
    mockIntersectionObserver();
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.queryByLabelText("Turn cost")).toBeNull();
  });

  it("does not fetch cost until element is visible", () => {
    mockIntersectionObserver();
    const spy = vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" costTurnId="t1" />);
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not render a clickable cost button", () => {
    mockIntersectionObserver();
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" costTurnId="t1" />);
    expect(screen.queryByRole("button", { name: /cost/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run the cost tests to verify they fail**

Run: `cd frontend && pnpm test -- PostItem`
Expected: FAIL — `PostItem` still keys cost off `showCost`/`post.turn_id`, ignoring `costTurnId`.

- [ ] **Step 3: Update PostItem to render cost from `costTurnId`**

In `frontend/src/routes/campaign/PostItem.tsx`, delete the now-unused `showCost` line:

```typescript
  const showCost = isModelPost && !!post.turn_id;
```

In the header, replace:

```tsx
        {showCost && <CostLabel turnId={post.turn_id} />}
```

with:

```tsx
        {costTurnId && <CostLabel turnId={costTurnId} />}
```

(`isModelPost` stays — it is still used by `canContinue`.) `costTurnId` was already added to `Props` in Task 7.

- [ ] **Step 4: Run the PostItem cost tests to verify they pass**

Run: `cd frontend && pnpm test -- PostItem`
Expected: all PostItem tests PASS

- [ ] **Step 5: Write the ScenePane cost-map test**

Append to `frontend/src/routes/campaign/__tests__/ScenePane.test.tsx`:

```typescript
describe("ScenePane cost attribution", () => {
  it("renders the turn cost once, on the user post, for a split-into-two turn", async () => {
    const { observabilityApi } = await import("../../../api/observability");
    const spy = vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.02, input_tokens: 100, output_tokens: 100, call_count: 1 },
    ]);
    // Real IntersectionObserver mock that fires immediately so CostLabel fetches.
    vi.stubGlobal(
      "IntersectionObserver",
      vi.fn((cb: (entries: Array<{ isIntersecting: boolean }>) => void) => {
        setTimeout(() => cb([{ isIntersecting: true }]), 0);
        return { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() };
      }),
    );
    const user = { ...makePost("u1"), is_player: true, author_kind: "pc" as const, turn_id: "tu" };
    const m1 = { ...makePost("m1"), turn_id: "T1" };
    const m2 = { ...makePost("m2"), turn_id: "T1" };
    renderPane({ posts: [user, m1, m2], campaignId: "c1" });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(screen.getAllByLabelText("Turn cost")).toHaveLength(1));
    expect(spy).toHaveBeenCalledWith("T1");
  });
});
```

- [ ] **Step 6: Run the ScenePane cost test to verify it fails**

Run: `cd frontend && pnpm test -- ScenePane`
Expected: FAIL — ScenePane does not yet compute or pass `costTurnId`.

- [ ] **Step 7: Compute the cost map in ScenePane**

In `frontend/src/routes/campaign/ScenePane.tsx`, add right after the `latestModelPostId` loop (after line ~90, before `return (`):

```typescript
  // Attribute each turn's cost to the user post that triggered it: the first
  // model post after a user post carries the turn id whose cost we display.
  // This shows cost once even when one LLM call is split into several posts.
  const costTurnByPost: Record<string, string> = {};
  let lastUserPostId: string | null = null;
  for (const p of posts) {
    if (p.is_player) {
      lastUserPostId = p.id;
    } else if (lastUserPostId && !(lastUserPostId in costTurnByPost) && p.turn_id) {
      costTurnByPost[lastUserPostId] = p.turn_id;
    }
  }
```

Then add the prop to the `<PostItem ... />` block (alongside the props added in Task 8):

```tsx
          costTurnId={costTurnByPost[post.id]}
```

- [ ] **Step 8: Run the ScenePane cost test to verify it passes**

Run: `cd frontend && pnpm test -- ScenePane`
Expected: all ScenePane tests PASS

- [ ] **Step 9: Typecheck, lint, format**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format`
Expected: no errors; re-stage any formatter changes.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/routes/campaign/ScenePane.tsx frontend/src/routes/campaign/PostItem.tsx frontend/src/routes/campaign/__tests__/ScenePane.test.tsx frontend/src/routes/campaign/__tests__/PostItem.test.tsx
git commit -m "feat(frontend): show turn cost on the triggering user post"
```

---

## Task 10: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Backend tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass (perf benchmarks excluded by default).

- [ ] **Step 2: Backend lint + format check**

Run: `cd backend && uv run ruff check && uv run ruff format --check`
Expected: clean.

- [ ] **Step 3: Frontend tests + typecheck + lint + format check**

Run: `cd frontend && pnpm test && pnpm typecheck && pnpm lint && pnpm format:check`
Expected: all pass / clean.

- [ ] **Step 4: Commit any residual formatting**

```bash
git add -A
git commit -m "chore: formatting after post-delete and cost-display work" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Source of truth:** `docs/superpowers/specs/2026-05-31-post-delete-and-cost-display-design.md`.
- **Straddling vs fully-contained turns:** a turn is *fully contained* when its lowest-order post is at/after the cut (reverse outright); *straddling* when it has posts on both sides of the cut (reverse + re-queue for review). Only turns whose ids appear in the delta log produce any reversal — player/direction posts carry throwaway turn ids and are no-ops.
- **CSS:** `post-delete`, `post-delete-confirm`, `post-delete-warning`, `post-delete-actions`, `post-delete-confirm-btn` are new classes. Styling is optional for functionality; mirror the existing `post-edit-form` / `post-icon-btn` styles in the scene stylesheet if you want visual parity (search for `.post-edit-form` to find the file).
- **The review queue UI** already exists (HUD "Review queue" widget + `review_item_added` WS event); re-queued straddling deltas will surface there with no extra frontend work.
