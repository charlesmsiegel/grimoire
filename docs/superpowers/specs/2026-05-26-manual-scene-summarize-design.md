# Manual Scene Summarization

## Problem

Scenes imported from external sources (or scenes where the running summary cadence was disabled) have no summary. The scene suggestion engine relies on `recent_summaries` from closed scenes, so these empty-summary scenes produce impoverished suggestion prompts. There is currently no way to trigger summarization on demand.

## Solution

Add a `POST /{campaign_id}/scenes/{scene_id}/summarize` endpoint that generates a summary for any scene (open or closed). The summarizer adapts its strategy based on the post volume relative to the model's context window.

## Behavior

### Endpoint: `POST /{campaign_id}/scenes/{scene_id}/summarize?force=false`

- **Open scene, no existing `running_summary`**: generate and store `running_summary` + `key_beats`.
- **Open scene, existing `running_summary`**: regenerate (overwrite) without requiring `force`.
- **Closed scene, no existing `final_summary`**: generate and store `final_summary` + `key_beats`.
- **Closed scene, existing `final_summary`, `force=false`**: return existing summary without re-running the LLM.
- **Closed scene, existing `final_summary`, `force=true`**: regenerate and overwrite.
- **Scene with zero posts**: return empty summary, no LLM call.

Response: `{"summary": "...", "key_beats": ["...", ...]}`.

### Adaptive windowing

The summarizer estimates total tokens for all posts and compares against the model's context window (obtained via `gateway.resolve_route` + `provider.list_models`).

- **Fits** (total post tokens < context_window / 2): single-pass final summarizer (existing `make_default_final_summarizer` logic).
- **Doesn't fit**: multi-pass windowed approach:
  1. Sort posts by `order_in_scene`.
  2. Walk through posts in windows sized to fit half the context window.
  3. For each window, call the running summarizer with the accumulated summary so far + the window's posts.
  4. After all windows, call the final summarizer with the accumulated `running_summary` and the **last window** of posts (so key beats come from the full arc, not just the last chunk).

The cheap token estimator (`len(text) // 4`) is sufficient for this sizing decision. If model info is unavailable (no `context_window` on `ModelInfo`, or value is 0), fall back to a conservative default of 100,000 tokens.

## Changes

### `backend/src/grimoire/scenes/default_summarizers.py`

New factory: `make_adaptive_summarizer(gateway, *, task, max_tokens, model, max_key_beats) -> AdaptiveSummarizer`.

Returns a callable with signature:
```python
async def(scene: Scene, posts: list[Post]) -> tuple[str, list[str]]
```

Internally:
1. Concatenates all post bodies, estimates tokens via `len(text) // 4`.
2. Resolves model context window: `route = gateway.resolve_route(task)`, then `model_info = await gateway.get_model_info(route.provider_id, route.model)`.
3. If tokens fit: delegates to the single-pass final summarizer logic (inline, same prompts as existing `make_default_final_summarizer`).
4. If tokens don't fit: windows posts, calls running summarizer prompt per window, then final summarizer prompt on the accumulated summary + last window.

### `backend/src/grimoire/llm_gateway/gateway.py`

Expose a public `get_model_info(provider_id, model) -> ModelInfo | None` method — thin wrapper around the existing private `_get_pricing`.

### `backend/src/grimoire/scenes/manager.py`

New method:
```python
async def generate_summary(
    self, scene_id: str, *, force: bool = False
) -> tuple[str, list[str]]:
```

- Acquires the scene lock.
- If closed scene has `final_summary` and not `force`: returns existing values.
- Loads all posts via `get_posts`.
- If no posts: returns `("", [])`.
- Calls the adaptive summarizer (stored as a new `_adaptive_summarizer` field, set via `set_adaptive_summarizer`).
- Falls back to `_final_summary` if no adaptive summarizer is wired.
- Stores result in `running_summary` (open) or `final_summary` (closed), plus `key_beats`.
- Writes sidecar.

New field + setter: `_adaptive_summarizer` / `set_adaptive_summarizer()`, following the existing `_summarizer` / `set_summarizer()` pattern.

### `backend/src/grimoire/bootstrap.py`

Wire `make_adaptive_summarizer` into the container alongside the existing summarizers:
```python
container.scenes.set_adaptive_summarizer(
    make_adaptive_summarizer(llm_gateway, ...)
)
```

### `backend/src/grimoire/api/campaigns/scenes.py`

New route:
```python
@router.post("/{campaign_id}/scenes/{scene_id}/summarize")
async def summarize_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    force: bool = False,
) -> dict[str, Any]:
```

Calls `scenes.generate_summary(scene_id, force=force)`, returns the result.

## Tests

### `backend/tests/scenes/test_default_summarizers.py`

- `test_adaptive_summarizer_single_pass`: short post list, verify single LLM call with final summarizer prompt.
- `test_adaptive_summarizer_windowed`: posts that exceed half the context window, verify multiple LLM calls (running + final).
- `test_adaptive_summarizer_no_posts`: returns empty summary.
- `test_adaptive_summarizer_fallback_context_window`: when model info returns `context_window=0`, falls back to 100k default.

### `backend/tests/scenes/test_manager.py`

- `test_generate_summary_open_scene`: open scene gets `running_summary` updated.
- `test_generate_summary_closed_scene`: closed scene gets `final_summary` updated.
- `test_generate_summary_closed_no_force_returns_existing`: closed scene with existing summary returns it without LLM call.
- `test_generate_summary_closed_force_regenerates`: with `force=True`, overwrites existing summary.
- `test_generate_summary_no_posts`: returns empty.

## Non-goals

- Changing the existing `close_scene` flow (it already works correctly).
- Changing the running summary cadence or the `update_running_summary` method.
- Token-exact counting (the `len // 4` heuristic is fine for a sizing decision).
