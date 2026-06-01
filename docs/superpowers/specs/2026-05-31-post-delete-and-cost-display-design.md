# Post cascade-delete and cost-display relocation — design

Date: 2026-05-31
Branch: `ui-improvements`

Two unrelated UI changes in the campaign play view:

1. A **delete** button on every post that removes that post **and every post
   after it in the scene**, reverting the state extracted from the affected
   turns. Requires a confirmation step that spells this out.
2. **Move the cost display** from the generated (model) post to the user post
   that triggered the turn, so cost is shown exactly once even in
   "one LLM call, post-per-character" mode where a single call is split into
   multiple model posts.

These are independent and can land in either order.

---

## Feature 1 — Cascade post delete with state reversal

### Goal and rationale

Deleting a single post mid-scene leaves the prose after it referring to events
that no longer happened, and leaves behind facts/commitments/relationships/cast
changes/time advancement that were extracted from posts that no longer exist.
To keep the campaign consistent, deleting a post must also delete every post
that follows it in the scene and revert the state derived from the removed
turns.

### Scope

- Deletion is **scoped to the post's scene**. Later scenes are untouched.
- Deletion is **only allowed while the scene is open**. A closed/complete
  scene (`scene.closed`) rejects the operation. This keeps the final summary
  stable and avoids reverting state for a sealed scene.
- "Revert extracted state" reuses the existing delta-reversal machinery
  (`OrchestratorService._reverse_turn_deltas`, the same path `undo_turn` uses).
  It inherits that machinery's guarantees and limits.

### Turn classification relative to the cut

Let `cut = target_post.order_in_scene`. For each `turn_id` present in the
scene's posts, compute `min_order` and `max_order` over the posts carrying it.
A turn is one of:

- **Before** the cut (`max_order < cut`): untouched.
- **Fully contained** in the deleted suffix (`min_order >= cut`): its deltas are
  reversed outright, no review (same as `undo_turn`).
- **Straddling** the cut (`min_order < cut <= max_order`): some of its posts
  survive and some are deleted. This only arises in "post-per-character" split
  mode where several model posts share a `turn_id` and the cut falls between
  them. We cannot auto-decide whether its deltas still hold, so they are
  **reversed and then re-queued for human review** (see below).

(Player/direction posts carry throwaway `turn_id`s that never appear in the
delta log, so they never classify as fully-contained or straddling turns.)

### Backend

#### Scene Manager — `truncate_scene_from`

Add a suffix-truncation sibling to the existing `delete_post`. Where
`delete_post` removes one post and shifts the orders of subsequent posts down by
one, `truncate_scene_from` removes a contiguous tail.

```
async def truncate_scene_from(self, post_id: str, source: str) -> list[Post]:
```

Behavior, holding the scene lock:

1. Resolve `(scene, target_post)` via `_find_post`.
2. Let `cut = target_post.order_in_scene`. Keep posts with
   `order_in_scene < cut`; drop the rest. No order-shifting is needed because
   the removed posts are a suffix.
3. Rewrite the scene `.md` (`write_body`) with the kept posts.
4. Set `scene.post_count = len(kept)`; clamp
   `scene.last_advance_at_post` to `<= post_count`.
5. Drop post records for `order >= cut` from `self._post_records[scene.id]`;
   write the sidecar.
6. Refresh `self._known_body_hashes[scene.id]`.
7. Emit `POST_DELETED` for each removed post (order + post_id + source) so
   downstream indexers stay consistent, mirroring `delete_post`'s single emit.
8. Return the removed `Post` objects (each carries its `turn_id`) so the
   orchestrator can decide which turns to reverse.

#### Orchestrator — `delete_post_cascade`

```
async def delete_post_cascade(
    self, campaign_id: CampaignId, scene_id: SceneId, post_id: PostId
) -> CascadeDeleteResult:
```

1. `_require_campaign(campaign_id)`.
2. Resolve the scene and verify it belongs to `campaign_id`. **Reject if the
   scene is closed/complete** (`scene.closed`) with a clear error. Resolve the
   target post and its `cut = order_in_scene`.
3. Read the scene's posts and classify each turn relative to `cut` (see "Turn
   classification" above) into *before* / *fully contained* / *straddling*.
4. **Fully-contained turns** (process in reverse scene order so the most recent
   is undone first): call `_reverse_turn_deltas` and emit `TURN_UNDONE` with the
   reversed delta ids — identical to `undo_turn`. Collect warnings on failure
   without aborting the whole operation.
5. **Straddling turns**: reverse their deltas the same way, then **re-queue each
   reversed delta for human review** so the human can decide whether it should
   survive the partial deletion (see "Straddling turns" below). Emit
   `TURN_UNDONE` as well so derived UIs stay consistent.
6. Call `self._scenes.truncate_scene_from(post_id, source="cascade_delete")`.
7. Reset `state.last_turn_id` to the now-top turn
   (`_recent_turn_ids(campaign_id, 1)`), so a subsequent `regenerate_last`
   refers to the correct turn.
8. Return
   `CascadeDeleteResult(deleted_post_ids, reversed_turn_ids, requeued_review_ids, warnings)`.

#### Straddling turns — reverse then re-queue for review

For each straddling turn, after reversing its deltas:

- For each reversed delta (read via `get_delta_log(turn_id=...)`), call the
  existing `store.queue_for_review(delta=<delta-as-dict>, source="cascade_delete",
  campaign_id=...)`. This inserts a **fresh, unapplied copy** of the delta and a
  pending `review_queue` row, returning a `review_id`.
- Emit `REVIEW_ITEM_ADDED` per queued item, mirroring `delta_applier`'s existing
  emit (`{campaign_id, review_id, turn_id}`), so the frontend review queue and
  the "Review queue" HUD widget pick it up live.
- Resulting human semantics (the queue's existing behavior):
  **Approve** → re-applies the delta (it survives the deletion);
  **Reject** → leaves it reversed (it is removed).

This reuses the exact approve = apply / reject = discard semantics already in
`approve_review_item` / `reject_review_item`; no inverted review mode is
introduced.

`CascadeDeleteResult` is a small dataclass/Pydantic model alongside the other
orchestrator result types.

#### API — DELETE route

Add to `backend/src/grimoire/api/alternates.py` (it already injects both
`ScenesDep` and `OrchestratorDep`, and owns the
`/campaigns/{cid}/scenes/{sid}/posts/{pid}` route family):

```
@router.delete("/{campaign_id}/scenes/{scene_id}/posts/{post_id}")
async def delete_post(campaign_id, scene_id, post_id, orchestrator, scenes) -> Any:
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        result = await orchestrator.delete_post_cascade(
            campaign_id=campaign_id, scene_id=scene_id, post_id=post_id
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
```

Returns `{ deleted_post_ids, reversed_turn_ids, requeued_review_ids, warnings }`.
Rejecting a delete on a closed scene maps to a 4xx via `map_lookup_errors` (or
an explicit guard returning 409/400).

### Frontend

#### API client

`frontend/src/api/campaign/api.ts`:

```
deletePost: (campaignId: string, sceneId: string, postId: string) =>
  api.delete<{
    deleted_post_ids: string[];
    reversed_turn_ids: string[];
    requeued_review_ids: string[];
    warnings: string[];
  }>(
    `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}`,
  ),
```

#### PostItem — delete button + inline confirm

- Add a 🗑 delete icon button to the `post-actions` row of **every** post
  (gated on `campaignId` being present, like edit, **and on the scene being
  open** — hidden/disabled when `scene.closed`).
- Clicking it opens an **inline confirmation strip** (consistent with the
  existing guided-regenerate / translate / continue inline forms — no new modal
  dependency):

  > Delete this post and the **N** following posts in this scene? Facts and
  > changes derived from them will be reverted. This cannot be undone.
  > **[Delete] [Cancel]**

- `N` (the count of subsequent posts) is computed accurately and
  pagination-proof from scene metadata, not from the loaded-post window:
  `subsequentCount = scene.post_count - post.order_in_scene - 1`. PostItem
  receives `subsequentCount` as a prop from ScenePane.
- On confirm: call `campaignApi.deletePost`, then call a threaded `refresh()`
  to reload the scene's posts. Disable controls while busy; surface errors via
  the existing `post-error` slot.

#### Threading `refresh`

`play.refresh` already exists in `PlayView` and is passed to other children
(`CastChangePrompt`, scene creation). Thread it into `ScenePane` and on to
`PostItem` (e.g. an `onDeleted?: () => void` prop, or pass `refresh` directly).

### Known limitations

- Deletion is blocked on closed/complete scenes (by design), so the **final
  summary stays stable**. The **running summary** of an open scene is recomputed
  on the normal cadence after the truncation, so stale running-summary text is
  self-correcting — no special handling needed.
- State reversal is only as complete as the delta log: anything not recorded as
  a reversible delta is not undone (same constraint as `undo_turn`).
- Re-queued straddling-turn deltas inherit `approve_review_item`'s scope: it
  re-applies `campaign-sqlite`-scoped deltas (via `upsert_row`). File-scoped
  deltas, if any, are reversed but won't be auto-re-applied on approve — same
  limitation the existing review-approve path already has.
- Scene-post embeddings derived from the `.md` are rebuilt by the watcher when
  the file changes; no explicit embedding cleanup is performed here.

### Tests

- Unit (Scene Manager): `truncate_scene_from` removes the suffix, updates
  `post_count`/`last_advance_at_post`, drops the right records, leaves earlier
  posts untouched.
- Integration (Orchestrator): after a few turns, cascade-deleting a mid-scene
  post removes the suffix **and** reverses the fully-contained turns' deltas;
  a straddling (split-mode) turn has its deltas reversed **and** re-queued as
  pending review items (approve re-applies, reject leaves reversed);
  `last_turn_id` is reset; deleting in a **closed scene is rejected**.
- Scenario (API): `DELETE .../posts/{pid}` returns the summary and the scene
  reflects the truncation on subsequent fetch; closed-scene delete returns 4xx.
- Frontend: PostItem renders the delete button, shows the confirm strip with
  the correct count, calls `deletePost` + `refresh` on confirm, and does
  nothing on cancel.

---

## Feature 2 — Cost on the user post (frontend-only)

### Goal and rationale

Today `PostItem` shows the turn cost on each **model** post
(`showCost = isModelPost && !!post.turn_id`). In "one call, post-per-character"
mode a single LLM call is split into several model posts that all share the
same `turn_id`, so the same turn cost is rendered once per split post. Moving
the display to the **user post** that triggered the turn shows the cost exactly
once per turn.

The user/direction post does not itself carry the triggering turn's id (it gets
a throwaway uuid at append time), so the frontend derives the association by
position. No backend change.

### ScenePane — derive `userPostId → turnId`

Walk `posts` in scene order, tracking the most recent `is_player` post. The
**first** model post that follows it contributes its `turn_id` as that user
post's cost turn:

```
const costTurnByPost: Record<string, string> = {};
let lastUserPostId: string | null = null;
for (const p of posts) {
  if (p.is_player) {
    lastUserPostId = p.id;
  } else if (lastUserPostId && !(lastUserPostId in costTurnByPost) && p.turn_id) {
    // first model post after a user post → attribute its turn to that user post
    costTurnByPost[lastUserPostId] = p.turn_id;
  }
}
```

- A "model post" is `author_kind !== "pc" && !is_player` (the same predicate
  used for `latestModelPostId`).
- A "user post" is `is_player` (covers both PC posts and directions).
- Subsequent split posts of the same turn are ignored because the user post is
  already keyed → cost shows once.

Pass `costTurnId={costTurnByPost[post.id]}` to each `PostItem`.

### PostItem — render cost on the user post

- Remove the model-post cost path (`showCost = isModelPost && !!post.turn_id`).
- Add a `costTurnId?: string` prop. Render `<CostLabel turnId={costTurnId} />`
  in the header when `costTurnId` is present.
- `CostLabel` is unchanged — it already sums the turn's task costs via
  `observabilityApi.turnCosts`.

### Behavior notes

- A user post still awaiting a response (multi-PC scene, pre-advance) has no
  following model post yet → no cost shown until the turn runs. Correct.
- When multiple user posts precede a single turn (multi-PC advance), the turn's
  cost is attributed to the **immediately preceding** user post. This is a
  reasonable single-attribution and avoids double counting.

### Tests

- Frontend (ScenePane / PostItem): cost renders on the user post and not on the
  model post; a split-into-two-model-posts turn shows cost once; a user post
  with no following model post shows no cost.

---

## Out of scope

- Regenerating summaries after a cascade delete.
- Any backend change to how the triggering post's `turn_id` is assigned.
- A modal confirmation dialog (inline strip is used for UX consistency).
