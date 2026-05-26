# Manual Scene Summarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `POST /{campaign_id}/scenes/{scene_id}/summarize` endpoint that generates summaries on demand, with adaptive windowing for long scenes.

**Architecture:** New `make_adaptive_summarizer` factory in `default_summarizers.py` handles both single-pass and windowed summarization based on token count vs. model context window. `SceneManager` gains a `generate_summary()` method and `_adaptive_summarizer` seam. The API gets one new route.

**Tech Stack:** Python, FastAPI, existing LLM Gateway infrastructure.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/src/grimoire/llm_gateway/gateway.py` | Modify | Expose `get_model_info()` public method |
| `backend/src/grimoire/scenes/default_summarizers.py` | Modify | Add `make_adaptive_summarizer` factory |
| `backend/src/grimoire/scenes/manager.py` | Modify | Add `_adaptive_summarizer` seam + `generate_summary()` method |
| `backend/src/grimoire/bootstrap.py` | Modify | Wire adaptive summarizer |
| `backend/src/grimoire/api/campaigns/scenes.py` | Modify | Add `/summarize` endpoint |
| `backend/tests/scenes/test_default_summarizers.py` | Modify | Tests for adaptive summarizer |
| `backend/tests/scenes/test_manager.py` | Modify | Tests for `generate_summary()` |

---

### Task 1: Expose `get_model_info` on the gateway

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py:181-206`

- [ ] **Step 1: Add `get_model_info` public method**

Add this method directly after `_get_pricing` (after line 206) in `LLMGatewayService`:

```python
    async def get_model_info(self, provider_id: str, model: str) -> "ModelInfo | None":
        """Public accessor for model metadata (context window, pricing, etc.)."""
        return await self._get_pricing(provider_id, model)
```

- [ ] **Step 2: Run existing tests to verify no breakage**

Run: `pytest backend/tests/llm_gateway/ -x -q`
Expected: all pass, no regressions.

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py
git commit -m "feat: expose get_model_info on LLMGatewayService"
```

---

### Task 2: Add `make_adaptive_summarizer` factory

**Files:**
- Modify: `backend/src/grimoire/scenes/default_summarizers.py`
- Modify: `backend/tests/scenes/test_default_summarizers.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/scenes/test_default_summarizers.py`:

```python
from grimoire.scenes.default_summarizers import make_adaptive_summarizer


class _AdaptiveGateway:
    """Fake gateway that also supports resolve_route and get_model_info."""

    def __init__(self, response_text: str, context_window: int = 200_000) -> None:
        self._text = response_text
        self._context_window = context_window
        self.calls: list[tuple[str, Any]] = []
        self.raise_on_call = False

    async def complete(self, task, request, campaign_id=None, *, turn_id=None):
        self.calls.append((task, request))
        if self.raise_on_call:
            raise RuntimeError("provider unavailable")
        return _FakeResponse(self._text)

    def resolve_route(self, task, campaign_id=None):
        class _Route:
            provider_id = "fake"
            model = "fake-model"
        return _Route()

    async def get_model_info(self, provider_id, model):
        from grimoire.types.llm import ModelInfo
        return ModelInfo(
            id=model, name=model, context_window=self._context_window,
        )


async def test_adaptive_summarizer_single_pass() -> None:
    gateway = _AdaptiveGateway(
        '{"summary": "All resolved.", "key_beats": ["Beat one"]}',
        context_window=200_000,
    )
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    posts = [_post(1, "short post"), _post(2, "another post")]
    summary, beats = await summarize(scene, posts)
    assert summary == "All resolved."
    assert beats == ["Beat one"]
    assert len(gateway.calls) == 1  # single LLM call


async def test_adaptive_summarizer_windowed() -> None:
    call_count = 0
    responses = [
        "Rolling summary of window 1.",
        '{"summary": "Final summary.", "key_beats": ["Beat A", "Beat B"]}',
    ]

    class _MultiGateway(_AdaptiveGateway):
        async def complete(self, task, request, campaign_id=None, *, turn_id=None):
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            self.calls.append((task, request))
            return _FakeResponse(responses[idx])

    # context_window=200 tokens -> 800 chars. Each post ~100 chars -> 2 posts
    # fit in half the window. 4 posts total -> needs 2 windows + final.
    gateway = _MultiGateway("ignored", context_window=200)
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    long_body = "x" * 100
    posts = [_post(i, long_body) for i in range(1, 5)]
    summary, beats = await summarize(scene, posts)
    assert summary == "Final summary."
    assert beats == ["Beat A", "Beat B"]
    assert call_count >= 2  # at least one rolling + one final


async def test_adaptive_summarizer_no_posts() -> None:
    gateway = _AdaptiveGateway('{"summary": "ignored", "key_beats": []}')
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    summary, beats = await summarize(scene, [])
    assert summary == "Things have happened."  # falls back to running_summary
    assert beats == []
    assert len(gateway.calls) == 0  # no LLM calls


async def test_adaptive_summarizer_fallback_context_window() -> None:
    gateway = _AdaptiveGateway(
        '{"summary": "Summarized.", "key_beats": []}',
        context_window=0,
    )
    summarize = make_adaptive_summarizer(gateway)
    scene = _scene()
    posts = [_post(1, "hello")]
    summary, beats = await summarize(scene, posts)
    assert summary == "Summarized."
    assert len(gateway.calls) == 1  # fell back to 100k, small post fits in single pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/scenes/test_default_summarizers.py -k adaptive -v`
Expected: FAIL — `make_adaptive_summarizer` does not exist.

- [ ] **Step 3: Implement `make_adaptive_summarizer`**

Add to `backend/src/grimoire/scenes/default_summarizers.py`, after `_trivial_summary` (after line 184):

```python
_FALLBACK_CONTEXT_WINDOW = 100_000


class _AdaptiveGateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> object: ...

    def resolve_route(self, task: str, campaign_id: str | None = None) -> object: ...

    async def get_model_info(self, provider_id: str, model: str) -> object | None: ...


def make_adaptive_summarizer(
    gateway: _AdaptiveGateway,
    *,
    task: str = _DEFAULT_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
    max_key_beats: int = 5,
):
    """Build a summarizer that adapts between single-pass and windowed mode.

    When the total post tokens fit in half the model's context window, uses
    the single-pass final summarizer. Otherwise, processes posts in windows
    using a rolling summary, then produces the final summary from the
    accumulated context.
    """

    async def _get_context_window() -> int:
        try:
            route = gateway.resolve_route(task)
            info = await gateway.get_model_info(route.provider_id, route.model)
            if info is not None:
                cw = getattr(info, "context_window", 0) or 0
                if cw > 0:
                    return cw
        except Exception:
            pass
        return _FALLBACK_CONTEXT_WINDOW

    async def _rolling_pass(previous: str | None, posts: list[Post]) -> str:
        if not posts:
            return previous or ""
        system = (
            "You are a tight-prose scene summarizer for a tabletop RPG companion. "
            "Maintain a rolling summary that captures the most important narrative "
            "developments so far. Aim for 3-5 short sentences. No bullet lists."
        )
        previous_block = (previous or "(no prior summary)").strip()
        user = (
            f"Previous running summary:\n{previous_block}\n\n"
            f"Recent posts:\n{_post_window(posts, n=len(posts))}\n\n"
            "Return only the updated running summary."
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.4,
        )
        try:
            response = await gateway.complete(task, request)
        except Exception as exc:
            logger.warning("adaptive rolling summary LLM call failed: %s", exc)
            return previous or ""
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return previous or ""
        return text.strip()

    async def _final_pass(scene: Scene, posts: list[Post], running: str | None) -> tuple[str, list[str]]:
        if not posts:
            return running or scene.running_summary or "", []
        system = (
            "You are a scene close-out summarizer for a tabletop RPG companion. "
            "Given the full post history, return a short final summary plus a "
            f"list of {max_key_beats} or fewer key beats that drove the scene. "
            "Respond with a JSON object ONLY, no prose, no markdown fences."
        )
        running_block = (running or scene.running_summary or "(none)").strip()
        user = (
            f"Scene title: {scene.title or scene.slug}\n"
            f"Running summary so far: {running_block}\n\n"
            f"Full scene posts:\n{_post_window(posts, n=len(posts))}\n\n"
            'Return JSON of the form: {"summary": "...", "key_beats": ["...", "..."]}'
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request)
        except Exception as exc:
            logger.warning("adaptive final summary LLM call failed: %s", exc)
            return _trivial_summary(scene, posts), []
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return _trivial_summary(scene, posts), []
        parsed = _extract_json(text)
        if parsed is None:
            return text.strip(), []
        summary = str(parsed.get("summary") or "").strip()
        beats_raw = parsed.get("key_beats") or []
        beats = [str(b).strip() for b in beats_raw if isinstance(b, (str, int))]
        beats = [b for b in beats if b][:max_key_beats]
        if not summary:
            summary = _trivial_summary(scene, posts)
        return summary, beats

    async def _adaptive(scene: Scene, posts: list[Post]) -> tuple[str, list[str]]:
        if not posts:
            return scene.running_summary or "", []

        context_window = await _get_context_window()
        total_chars = sum(len(p.body) for p in posts)
        total_tokens_est = total_chars // 4
        budget = context_window // 2

        if total_tokens_est <= budget:
            return await _final_pass(scene, posts, scene.running_summary)

        window_chars = budget * 4
        running = scene.running_summary
        windows: list[list[Post]] = []
        current_window: list[Post] = []
        current_chars = 0
        for p in posts:
            if current_chars + len(p.body) > window_chars and current_window:
                windows.append(current_window)
                current_window = []
                current_chars = 0
            current_window.append(p)
            current_chars += len(p.body)
        if current_window:
            windows.append(current_window)

        for window in windows[:-1]:
            running = await _rolling_pass(running, window)

        return await _final_pass(scene, windows[-1], running)

    return _adaptive
```

Update the `__all__` list at the bottom of the file:

```python
__all__ = [
    "make_adaptive_summarizer",
    "make_default_final_summarizer",
    "make_default_running_summarizer",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/scenes/test_default_summarizers.py -v`
Expected: all tests pass, including the 4 new adaptive tests.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/default_summarizers.py backend/tests/scenes/test_default_summarizers.py
git commit -m "feat: add adaptive summarizer with context-window-based windowing"
```

---

### Task 3: Add `generate_summary()` to `SceneManager`

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py:70-71` (type alias), `135-194` (init/setters), `589-599` (after `_final_summary`)
- Modify: `backend/tests/scenes/test_manager.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/scenes/test_manager.py`:

```python
async def test_generate_summary_open_scene(tmp_path: Path) -> None:
    called = {}

    async def fake_adaptive(scene, posts):
        called["scene_id"] = scene.id
        called["post_count"] = len(posts)
        return "Generated summary.", ["Beat 1"]

    manager, _ = _manager(tmp_path)
    manager.set_adaptive_summarizer(fake_adaptive)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="opening", is_player=False),
    )
    summary, beats = await manager.generate_summary(scene.id)
    assert summary == "Generated summary."
    assert beats == ["Beat 1"]
    assert called["post_count"] == 1
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.running_summary == "Generated summary."
    assert refreshed.key_beats == ["Beat 1"]


async def test_generate_summary_closed_scene(tmp_path: Path) -> None:
    async def fake_adaptive(scene, posts):
        return "Final.", ["Beat A"]

    manager, _ = _manager(tmp_path)
    manager.set_adaptive_summarizer(fake_adaptive)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="line", is_player=False),
    )
    await manager.close_scene(scene.id, closed_at_turn="t1")
    summary, beats = await manager.generate_summary(scene.id, force=True)
    assert summary == "Final."
    assert beats == ["Beat A"]
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.final_summary == "Final."
    assert refreshed.key_beats == ["Beat A"]


async def test_generate_summary_closed_no_force_returns_existing(tmp_path: Path) -> None:
    called = False

    async def fake_adaptive(scene, posts):
        nonlocal called
        called = True
        return "Should not be used.", []

    manager, _ = _manager(tmp_path)
    manager.set_adaptive_summarizer(fake_adaptive)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="line", is_player=False),
    )
    # Close scene — without a final_summarizer, it gets a trivial summary
    await manager.close_scene(scene.id, closed_at_turn="t1")
    refreshed = await manager.get_scene(scene.id)
    existing_summary = refreshed.final_summary

    summary, beats = await manager.generate_summary(scene.id)
    assert summary == existing_summary
    assert called is False  # adaptive summarizer was NOT invoked


async def test_generate_summary_closed_force_regenerates(tmp_path: Path) -> None:
    called = False

    async def fake_adaptive(scene, posts):
        nonlocal called
        called = True
        return "Regenerated.", ["New beat"]

    manager, _ = _manager(tmp_path)
    manager.set_adaptive_summarizer(fake_adaptive)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="line", is_player=False),
    )
    await manager.close_scene(scene.id, closed_at_turn="t1")
    summary, beats = await manager.generate_summary(scene.id, force=True)
    assert summary == "Regenerated."
    assert beats == ["New beat"]
    assert called is True


async def test_generate_summary_no_posts(tmp_path: Path) -> None:
    called = False

    async def fake_adaptive(scene, posts):
        nonlocal called
        called = True
        return "Should not reach.", []

    manager, _ = _manager(tmp_path)
    manager.set_adaptive_summarizer(fake_adaptive)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    summary, beats = await manager.generate_summary(scene.id)
    assert summary == ""
    assert beats == []
    assert called is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/scenes/test_manager.py -k generate_summary -v`
Expected: FAIL — `set_adaptive_summarizer` / `generate_summary` do not exist.

- [ ] **Step 3: Add the type alias, field, setter, and method**

In `backend/src/grimoire/scenes/manager.py`:

**After line 71** (after the `FinalSummarizer` alias), add:

```python
AdaptiveSummarizer = Callable[[Scene, list[Post]], Awaitable[tuple[str, list[str]]]]
```

**In `__init__`** (after line 154, `self._final_summarizer = final_summarizer`), add:

```python
        self._adaptive_summarizer: AdaptiveSummarizer | None = None
```

**After `set_final_summarizer`** (after line 190), add:

```python
    def set_adaptive_summarizer(self, summarizer: AdaptiveSummarizer | None) -> None:
        self._adaptive_summarizer = summarizer
```

**After `_final_summary`** (after line 599), add:

```python
    async def generate_summary(
        self, scene_id: str, *, force: bool = False,
    ) -> tuple[str, list[str]]:
        """Generate a summary on demand for any scene (open or closed)."""
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)

            if scene.closed and scene.final_summary and not force:
                return scene.final_summary, list(scene.key_beats)

            posts = await self.get_posts(scene_id)
            if not posts:
                return "", []

            if self._adaptive_summarizer is not None:
                summary, key_beats = await self._adaptive_summarizer(scene, posts)
            else:
                summary, key_beats = await self._final_summary(scene, posts)

            if scene.closed:
                scene.final_summary = summary
            else:
                scene.running_summary = summary
            scene.key_beats = list(key_beats)
            self._write_sidecar(scene)
            return summary, key_beats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/scenes/test_manager.py -k generate_summary -v`
Expected: all 5 new tests pass.

- [ ] **Step 5: Run full manager test suite**

Run: `pytest backend/tests/scenes/test_manager.py -v`
Expected: all pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/tests/scenes/test_manager.py
git commit -m "feat: add generate_summary() to SceneManager with adaptive summarizer seam"
```

---

### Task 4: Wire the adaptive summarizer in bootstrap

**Files:**
- Modify: `backend/src/grimoire/bootstrap.py:292-390`

- [ ] **Step 1: Add import and wiring**

In `backend/src/grimoire/bootstrap.py`:

**At line 292-295**, update the import block to include `make_adaptive_summarizer`:

```python
    from grimoire.scenes.default_summarizers import (
        make_adaptive_summarizer,
        make_default_final_summarizer,
        make_default_running_summarizer,
    )
```

**After the `set_final_summarizer` block** (after line 390), add:

```python
    if getattr(container.scenes, "_adaptive_summarizer", None) is None:
        container.scenes.set_adaptive_summarizer(
            make_adaptive_summarizer(
                llm_gateway,
                max_tokens=container.scenes.config.running_summary.max_tokens,
                model=container.scenes.config.running_summary.model or "default",
            )
        )
```

- [ ] **Step 2: Run existing tests to verify no breakage**

Run: `pytest backend/tests/ -x -q --timeout=30`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/bootstrap.py
git commit -m "feat: wire adaptive summarizer into bootstrap"
```

---

### Task 5: Add the API endpoint

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/scenes.py:197-209`

- [ ] **Step 1: Add the endpoint**

In `backend/src/grimoire/api/campaigns/scenes.py`, **before the `end_scene` endpoint** (before line 197), add:

```python
@router.post("/{campaign_id}/scenes/{scene_id}/summarize")
async def summarize_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    force: bool = False,
) -> Any:
    try:
        await _require_scene_owned(scenes, campaign_id, scene_id)
        summary, key_beats = await scenes.generate_summary(scene_id, force=force)
        return {"summary": summary, "key_beats": key_beats}
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
```

- [ ] **Step 2: Run existing tests to verify no breakage**

Run: `pytest backend/tests/ -x -q --timeout=30`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend/src/grimoire/api/campaigns/scenes.py
git commit -m "feat: add POST /scenes/{scene_id}/summarize endpoint"
```

---

### Task 6: Verify end-to-end and clean up

- [ ] **Step 1: Run the full test suite**

Run: `pytest backend/tests/ -v --timeout=30`
Expected: all pass.

- [ ] **Step 2: Run ruff formatting**

Run: `ruff format backend/src/grimoire/scenes/default_summarizers.py backend/src/grimoire/scenes/manager.py backend/src/grimoire/api/campaigns/scenes.py backend/src/grimoire/bootstrap.py backend/src/grimoire/llm_gateway/gateway.py backend/tests/scenes/test_default_summarizers.py backend/tests/scenes/test_manager.py`

- [ ] **Step 3: Run ruff linting**

Run: `ruff check backend/src/grimoire/scenes/default_summarizers.py backend/src/grimoire/scenes/manager.py backend/src/grimoire/api/campaigns/scenes.py backend/src/grimoire/bootstrap.py backend/src/grimoire/llm_gateway/gateway.py backend/tests/scenes/test_default_summarizers.py backend/tests/scenes/test_manager.py`
Expected: no errors.

- [ ] **Step 4: Final commit if formatting changed anything**

```bash
git add -u
git commit -m "style: format manual scene summarization files"
```
