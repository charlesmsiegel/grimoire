# Per-Character Posts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable per-character post output from the LLM so multi-character scenes produce individually-attributed posts rather than a single narrator wall-of-text.

**Architecture:** Three narrator response modes (`all_at_once`, `per_character`, `per_character_multi_call`) control output format. A new response-format prompt template instructs the LLM to use XML tags. A `ResponseSplitter` parses tagged output into segments. The orchestrator creates one post per segment (single-call) or enters a speaker loop (multi-call). The frontend gets a "Next" button for multi-call mode.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Jinja2, pytest-asyncio; TypeScript, React, Vitest

---

### Task 1: Add `per_character_multi_call` to narrator mode constants

**Files:**
- Modify: `backend/src/grimoire/scenes/narrator_mode.py:22-25`
- Modify: `backend/tests/scenes/test_narrator_mode.py`

- [ ] **Step 1: Write tests for the new mode constant**

Add to `backend/tests/scenes/test_narrator_mode.py`:

```python
from grimoire.scenes.narrator_mode import PER_CHARACTER_MULTI_CALL

def test_normalize_response_mode_accepts_multi_call() -> None:
    assert normalize_response_mode(PER_CHARACTER_MULTI_CALL) == PER_CHARACTER_MULTI_CALL


def test_campaign_response_mode_reads_multi_call() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": PER_CHARACTER_MULTI_CALL}})}
    assert campaign_response_mode(row) == PER_CHARACTER_MULTI_CALL


def test_effective_response_mode_scene_override_multi_call() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": ALL_AT_ONCE}})}
    assert (
        effective_response_mode(scene_override=PER_CHARACTER_MULTI_CALL, campaign_row=row)
        == PER_CHARACTER_MULTI_CALL
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/scenes/test_narrator_mode.py -v`
Expected: FAIL — `ImportError: cannot import name 'PER_CHARACTER_MULTI_CALL'`

- [ ] **Step 3: Add the constant**

In `backend/src/grimoire/scenes/narrator_mode.py`, update the constants block (lines 22-25):

```python
ALL_AT_ONCE = "all_at_once"
PER_CHARACTER = "per_character"
PER_CHARACTER_MULTI_CALL = "per_character_multi_call"
DEFAULT_RESPONSE_MODE = ALL_AT_ONCE
RESPONSE_MODES: tuple[str, ...] = (ALL_AT_ONCE, PER_CHARACTER, PER_CHARACTER_MULTI_CALL)
```

Update the module docstring (lines 1-15) to mention the third mode:

```python
"""Narrator response mode: campaign default + per-scene override.

Three values are supported:

- ``"all_at_once"`` — the narrator emits one combined response covering
  every present character in a single post.
- ``"per_character"`` — the narrator emits a single LLM response using
  XML character tags, parsed into separate per-character posts.
- ``"per_character_multi_call"`` — the orchestrator makes one LLM call
  per character in a speaker loop, with player interject control.
...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/scenes/test_narrator_mode.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/scenes/narrator_mode.py backend/tests/scenes/test_narrator_mode.py
git commit -m "feat(scenes): add per_character_multi_call narrator response mode"
```

---

### Task 2: Build the `ResponseSplitter`

**Files:**
- Create: `backend/src/grimoire/scenes/response_splitter.py`
- Create: `backend/tests/scenes/test_response_splitter.py`

- [ ] **Step 1: Write tests for the splitter**

Create `backend/tests/scenes/test_response_splitter.py`:

```python
"""Tests for :mod:`grimoire.scenes.response_splitter`."""

from __future__ import annotations

from grimoire.scenes.response_splitter import ResponseSegment, split_response


def test_single_narrator_tag() -> None:
    text = '<narrator>The wind howls.</narrator>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="The wind howls.")]


def test_single_character_tag() -> None:
    text = '<character ref="alice">Alice waves.</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="alice", body="Alice waves.")]


def test_mixed_tags_preserve_order() -> None:
    text = (
        '<narrator>Rain falls.</narrator>'
        '<character ref="alice">Alice shivers.</character>'
        '<character ref="bob">Bob opens an umbrella.</character>'
    )
    result = split_response(text)
    assert len(result) == 3
    assert result[0] == ResponseSegment(kind="narrator", ref=None, body="Rain falls.")
    assert result[1] == ResponseSegment(kind="character", ref="alice", body="Alice shivers.")
    assert result[2] == ResponseSegment(kind="character", ref="bob", body="Bob opens an umbrella.")


def test_text_outside_tags_becomes_narrator() -> None:
    text = 'Preamble text.<character ref="alice">Alice speaks.</character>Trailing text.'
    result = split_response(text)
    assert result[0] == ResponseSegment(kind="narrator", ref=None, body="Preamble text.")
    assert result[1] == ResponseSegment(kind="character", ref="alice", body="Alice speaks.")
    assert result[2] == ResponseSegment(kind="narrator", ref=None, body="Trailing text.")


def test_no_tags_returns_single_narrator() -> None:
    text = "Just plain prose with no tags at all."
    result = split_response(text)
    assert result == [
        ResponseSegment(kind="narrator", ref=None, body="Just plain prose with no tags at all.")
    ]


def test_empty_string_returns_empty_list() -> None:
    result = split_response("")
    assert result == []


def test_empty_tag_body_is_dropped() -> None:
    text = '<character ref="alice"></character><character ref="bob">Hello.</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="bob", body="Hello.")]


def test_adjacent_same_character_merged() -> None:
    text = (
        '<character ref="alice">First part.</character>'
        '<character ref="alice">Second part.</character>'
    )
    result = split_response(text)
    assert result == [
        ResponseSegment(kind="character", ref="alice", body="First part.\n\nSecond part.")
    ]


def test_adjacent_narrator_segments_merged() -> None:
    text = '<narrator>Part one.</narrator><narrator>Part two.</narrator>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="Part one.\n\nPart two.")]


def test_same_character_non_adjacent_not_merged() -> None:
    text = (
        '<character ref="alice">First.</character>'
        '<character ref="bob">Middle.</character>'
        '<character ref="alice">Second.</character>'
    )
    result = split_response(text)
    assert len(result) == 3
    assert result[0].ref == "alice"
    assert result[1].ref == "bob"
    assert result[2].ref == "alice"


def test_unclosed_last_tag() -> None:
    text = '<character ref="alice">She begins to speak but the response cuts'
    result = split_response(text)
    assert result == [
        ResponseSegment(
            kind="character",
            ref="alice",
            body="She begins to speak but the response cuts",
        )
    ]


def test_whitespace_only_body_is_dropped() -> None:
    text = '<character ref="alice">   </character><narrator>Scene.</narrator>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="Scene.")]


def test_multiline_body_preserved() -> None:
    body = "Line one.\n\nLine two.\nLine three."
    text = f'<character ref="alice">{body}</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="alice", body=body)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/scenes/test_response_splitter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the splitter**

Create `backend/src/grimoire/scenes/response_splitter.py`:

```python
"""Split LLM responses with XML character/narrator tags into segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_TAG_PATTERN = re.compile(
    r"<(character)\s+ref=\"([^\"]+)\">([\s\S]*?)(?:</character>)"
    r"|<(narrator)>([\s\S]*?)(?:</narrator>)"
    r"|<(character)\s+ref=\"([^\"]+)\">([\s\S]*?)$"
    r"|<(narrator)>([\s\S]*?)$",
)


@dataclass(frozen=True)
class ResponseSegment:
    kind: Literal["character", "narrator"]
    ref: str | None
    body: str


def split_response(text: str) -> list[ResponseSegment]:
    if not text:
        return []

    segments: list[ResponseSegment] = []
    last_end = 0

    for m in _TAG_PATTERN.finditer(text):
        # Text before this match becomes a narrator segment
        if m.start() > last_end:
            gap = text[last_end : m.start()].strip()
            if gap:
                segments.append(ResponseSegment(kind="narrator", ref=None, body=gap))

        # Determine which alternative matched
        if m.group(1):  # <character ref="...">...</character>
            segments.append(ResponseSegment(kind="character", ref=m.group(2), body=m.group(3)))
        elif m.group(4):  # <narrator>...</narrator>
            segments.append(ResponseSegment(kind="narrator", ref=None, body=m.group(5)))
        elif m.group(6):  # <character ref="...">... (unclosed, end of string)
            segments.append(ResponseSegment(kind="character", ref=m.group(7), body=m.group(8)))
        elif m.group(9):  # <narrator>... (unclosed, end of string)
            segments.append(ResponseSegment(kind="narrator", ref=None, body=m.group(10)))

        last_end = m.end()

    # Trailing text after all tags
    if last_end < len(text):
        trailing = text[last_end:].strip()
        if trailing:
            segments.append(ResponseSegment(kind="narrator", ref=None, body=trailing))

    # No tags found — entire text is a single narrator segment
    if not segments and text.strip():
        return [ResponseSegment(kind="narrator", ref=None, body=text.strip())]

    # Drop empty/whitespace-only bodies
    segments = [s for s in segments if s.body.strip()]

    # Merge adjacent segments with same kind+ref
    merged: list[ResponseSegment] = []
    for seg in segments:
        if merged and merged[-1].kind == seg.kind and merged[-1].ref == seg.ref:
            merged[-1] = ResponseSegment(
                kind=seg.kind,
                ref=seg.ref,
                body=merged[-1].body + "\n\n" + seg.body,
            )
        else:
            merged.append(seg)

    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/scenes/test_response_splitter.py -v`
Expected: all PASS

- [ ] **Step 5: Run ruff**

Run: `cd backend && uv run ruff check src/grimoire/scenes/response_splitter.py && uv run ruff format --check src/grimoire/scenes/response_splitter.py`
Expected: clean

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/scenes/response_splitter.py backend/tests/scenes/test_response_splitter.py
git commit -m "feat(scenes): add ResponseSplitter for XML-tagged per-character output"
```

---

### Task 3: Create the response format prompt templates

**Files:**
- Create: `backend/src/grimoire/templates/context_response_format/default.j2`
- Create: `backend/src/grimoire/templates/context_response_format/single_character.j2`
- Create: `backend/tests/templates/test_response_format_templates.py`

- [ ] **Step 1: Write tests for template rendering**

Create `backend/tests/templates/test_response_format_templates.py`:

```python
"""Tests for the response format prompt templates."""

from __future__ import annotations

from grimoire.templates import render


def test_default_response_format_renders_npc_list() -> None:
    npcs = [
        {"name": "Alice", "ref": "worlds/w/characters/alice"},
        {"name": "Bob", "ref": "worlds/w/characters/bob"},
    ]
    result = render("context_response_format", present_npcs=npcs)
    assert "Alice" in result
    assert "worlds/w/characters/alice" in result
    assert "Bob" in result
    assert '<character ref="' in result
    assert "<narrator>" in result


def test_default_response_format_with_single_npc() -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    result = render("context_response_format", present_npcs=npcs)
    assert "Alice" in result


def test_single_character_variant_renders_character() -> None:
    result = render(
        "context_response_format",
        variant="single_character",
        character_name="Alice",
        character_ref="worlds/w/characters/alice",
    )
    assert "Alice" in result
    assert "worlds/w/characters/alice" in result
    assert "only as Alice" in result or "as Alice" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/templates/test_response_format_templates.py -v`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound`

- [ ] **Step 3: Create the default (multi-character) template**

Create `backend/src/grimoire/templates/context_response_format/default.j2`:

```jinja2
{#-
  Response format instructions for per_character mode (single call).
  Injected after the lock-in block when narrator_response_mode is "per_character".

  Inputs:
    present_npcs    list[dict] — each has "name" and "ref" keys
-#}
# Response format

Structure your response as a sequence of character posts and optional narrator segments using XML tags. Each character post is wrapped in a `<character>` tag with the character's ref. Narrator prose (scene-setting, environmental description, transitions) uses a `<narrator>` tag.

Characters present in this scene:
{% for npc in present_npcs %}
- {{ npc.name }} (ref: `{{ npc.ref }}`)
{% endfor %}

Rules:
- Use `<character ref="...">` tags with the exact ref values listed above.
- A character may appear multiple times if the scene calls for it.
- Use `<narrator>` for environmental prose, transitions, or description not attributable to a character.
- Write the prose inside each tag as normal narrative — dialogue, action, internal thought.
- Do not nest tags or use any tags other than `<character>` and `<narrator>`.

Example:
<narrator>The gas lamps flicker as wind sweeps through the alley.</narrator>
<character ref="{{ present_npcs[0].ref }}">{{ present_npcs[0].name }} reacts to the scene.</character>
{%- if present_npcs | length > 1 %}
<character ref="{{ present_npcs[1].ref }}">{{ present_npcs[1].name }} responds.</character>
{%- endif %}
```

- [ ] **Step 4: Create the single-character variant (for multi-call mode)**

Create `backend/src/grimoire/templates/context_response_format/single_character.j2`:

```jinja2
{#-
  Response format instructions for per_character_multi_call mode.
  One LLM call per character — this template tells the model to write
  only as the specified character.

  Inputs:
    character_name  str
    character_ref   str
-#}
# Response format

Write your response only as {{ character_name }}. Stay in character — use their voice, mannerisms, and perspective. Include dialogue, actions, and internal thoughts as appropriate.

Do not write for other characters. Do not include scene-setting narration beyond what {{ character_name }} would directly perceive and react to.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/templates/test_response_format_templates.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/templates/context_response_format/ backend/tests/templates/test_response_format_templates.py
git commit -m "feat(templates): add response format templates for per-character modes"
```

---

### Task 4: Wire response format template into the assembler

**Files:**
- Modify: `backend/src/grimoire/context/types.py:54-71`
- Modify: `backend/src/grimoire/context/assembler.py:32-125`
- Modify: `backend/src/grimoire/context/builder.py:290-467`
- Create: `backend/tests/context/test_assembler_response_format.py`

- [ ] **Step 1: Write tests for response format injection**

Create `backend/tests/context/test_assembler_response_format.py`:

```python
"""Tests for response format message injection in PromptAssembler."""

from __future__ import annotations

import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext
from grimoire.scenes.narrator_mode import ALL_AT_ONCE, PER_CHARACTER, PER_CHARACTER_MULTI_CALL


def _make_ctx(**overrides) -> BuiltContext:
    defaults = dict(
        composition=None,
        style_text="",
        content_boundaries="",
        system_meta="",
        scene_header="Test scene",
        active_pc_card="Test PC",
        active_pc_name="Player",
        mechanics_block="",
        commitments_block="",
        voice_corrective="",
        narrator_response_mode=ALL_AT_ONCE,
        present_npcs=[],
    )
    defaults.update(overrides)
    return BuiltContext(**defaults)


@pytest.fixture
def assembler() -> PromptAssembler:
    return PromptAssembler(config=ContextBuilderConfig(), estimator=cheap_estimator())


@pytest.mark.asyncio
async def test_no_response_format_for_all_at_once(assembler: PromptAssembler) -> None:
    ctx = _make_ctx(narrator_response_mode=ALL_AT_ONCE)
    prompt = await assembler.assemble(ctx, "player input")
    for msg in prompt.messages:
        assert "Response format" not in msg.content


@pytest.mark.asyncio
async def test_response_format_injected_for_per_character(assembler: PromptAssembler) -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    ctx = _make_ctx(narrator_response_mode=PER_CHARACTER, present_npcs=npcs)
    prompt = await assembler.assemble(ctx, "player input")
    contents = [msg.content for msg in prompt.messages]
    response_fmt = [c for c in contents if "Response format" in c]
    assert len(response_fmt) == 1
    assert "Alice" in response_fmt[0]
    assert '<character ref="' in response_fmt[0]


@pytest.mark.asyncio
async def test_response_format_injected_for_multi_call(assembler: PromptAssembler) -> None:
    ctx = _make_ctx(
        narrator_response_mode=PER_CHARACTER_MULTI_CALL,
        present_npcs=[],
        multi_call_character_name="Alice",
        multi_call_character_ref="worlds/w/characters/alice",
    )
    prompt = await assembler.assemble(ctx, "player input")
    contents = [msg.content for msg in prompt.messages]
    response_fmt = [c for c in contents if "Response format" in c]
    assert len(response_fmt) == 1
    assert "Alice" in response_fmt[0]


@pytest.mark.asyncio
async def test_response_format_appears_after_lock_in(assembler: PromptAssembler) -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    ctx = _make_ctx(narrator_response_mode=PER_CHARACTER, present_npcs=npcs)
    prompt = await assembler.assemble(ctx, "player input")
    tiers = [msg.metadata.get("tier") for msg in prompt.messages]
    lock_in_idx = next(i for i, t in enumerate(tiers) if t == "lock-in")
    fmt_idx = next(i for i, t in enumerate(tiers) if t == "response-format")
    assert fmt_idx == lock_in_idx + 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/context/test_assembler_response_format.py -v`
Expected: FAIL — `BuiltContext` doesn't accept `narrator_response_mode` yet

- [ ] **Step 3: Add new fields to `BuiltContext`**

In `backend/src/grimoire/context/types.py`, add to the `BuiltContext` dataclass (after `extra` field, line 70):

```python
    narrator_response_mode: str = "all_at_once"
    present_npcs: list[dict] = field(default_factory=list)
    multi_call_character_name: str = ""
    multi_call_character_ref: str = ""
```

- [ ] **Step 4: Inject the response format message in the assembler**

In `backend/src/grimoire/context/assembler.py`, modify the `assemble` method. After the lock-in block message is appended (after line 59) and before the spotlight tier packing (line 61), add the response format injection:

```python
        response_fmt = self._response_format_block(ctx)
        if response_fmt:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=response_fmt,
                    metadata={"tier": "response-format"},
                )
            )
```

Add the helper method to the `PromptAssembler` class (after `_lock_in_block`, around line 185):

```python
    def _response_format_block(self, ctx: BuiltContext) -> str:
        from grimoire.scenes.narrator_mode import PER_CHARACTER, PER_CHARACTER_MULTI_CALL

        if ctx.narrator_response_mode == PER_CHARACTER:
            return render_template(
                "context_response_format",
                present_npcs=ctx.present_npcs,
            ).strip()
        if ctx.narrator_response_mode == PER_CHARACTER_MULTI_CALL:
            return render_template(
                "context_response_format",
                variant="single_character",
                character_name=ctx.multi_call_character_name,
                character_ref=ctx.multi_call_character_ref,
            ).strip()
        return ""
```

- [ ] **Step 5: Pass the new fields through the context builder**

In `backend/src/grimoire/context/builder.py`, in the `_build_context` method, before the `return BuiltContext(...)` block (around line 450):

1. Resolve the narrator response mode:

```python
        from grimoire.scenes.narrator_mode import effective_response_mode

        campaign_row = None
        if self._store is not None:
            try:
                campaign_row = await self._store.get_campaign(campaign_id)
            except Exception:
                pass
        narrator_mode = effective_response_mode(
            scene_override=getattr(scene, "narrator_response_mode", None),
            campaign_row=campaign_row,
        )
```

2. Build the NPC list from `present_character_refs`:

```python
        present_npcs: list[dict] = []
        if scene is not None:
            for ref in getattr(scene, "present_character_refs", []):
                name = ref.rsplit("/", 1)[-1].replace("-", " ").title()
                try:
                    entity = await self._library.get_entity(ref)
                    name = getattr(entity, "name", name)
                except Exception:
                    pass
                present_npcs.append({"name": name, "ref": ref})
```

3. Add to the `BuiltContext(...)` return:

```python
            narrator_response_mode=narrator_mode,
            present_npcs=present_npcs,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/context/test_assembler_response_format.py -v`
Expected: all PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `cd backend && uv run pytest tests/context/ -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```
git add backend/src/grimoire/context/types.py backend/src/grimoire/context/assembler.py backend/src/grimoire/context/builder.py backend/tests/context/test_assembler_response_format.py
git commit -m "feat(context): inject response format template based on narrator mode"
```

---

### Task 5: Orchestrator single-call post splitting

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py:960-970`
- Create: `backend/tests/orchestrator/test_post_splitting.py`

- [ ] **Step 1: Write tests for post splitting in the orchestrator**

Create `backend/tests/orchestrator/test_post_splitting.py`. This tests the `_create_response_posts` helper that will be extracted:

```python
"""Tests for per-character post splitting in the orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from grimoire.orchestrator.post_splitting import create_response_posts
from grimoire.scenes.narrator_mode import ALL_AT_ONCE, PER_CHARACTER
from grimoire.scenes.types import AuthorKind


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_all_at_once_creates_single_narrator_post() -> None:
    posts = create_response_posts(
        response_text="The narrator speaks for everyone.",
        narrator_mode=ALL_AT_ONCE,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 1
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "The narrator speaks for everyone."


def test_per_character_splits_tagged_response() -> None:
    response = (
        '<narrator>Rain falls.</narrator>'
        '<character ref="worlds/w/characters/alice">Alice shivers.</character>'
        '<character ref="worlds/w/characters/bob">Bob opens an umbrella.</character>'
    )
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 3
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "Rain falls."
    assert posts[1].author_kind == AuthorKind.NPC
    assert posts[1].author_npc_ref == "worlds/w/characters/alice"
    assert posts[1].body == "Alice shivers."
    assert posts[2].author_kind == AuthorKind.NPC
    assert posts[2].author_npc_ref == "worlds/w/characters/bob"
    assert posts[2].body == "Bob opens an umbrella."


def test_per_character_no_tags_degrades_to_narrator() -> None:
    posts = create_response_posts(
        response_text="Plain prose without any tags.",
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 1
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "Plain prose without any tags."


def test_all_posts_share_turn_id() -> None:
    response = (
        '<character ref="alice">Alice.</character>'
        '<character ref="bob">Bob.</character>'
    )
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-42",
        clock=_clock,
    )
    assert all(p.turn_id == "turn-42" for p in posts)


def test_all_posts_have_unique_ids() -> None:
    response = (
        '<character ref="alice">Alice.</character>'
        '<character ref="bob">Bob.</character>'
    )
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    ids = [p.id for p in posts]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/orchestrator/test_post_splitting.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the `post_splitting` module**

Create `backend/src/grimoire/orchestrator/post_splitting.py`:

```python
"""Create response posts from LLM output, respecting narrator response mode."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from grimoire.scenes.narrator_mode import ALL_AT_ONCE, PER_CHARACTER
from grimoire.scenes.response_splitter import split_response
from grimoire.scenes.types import AuthorKind, Post


def create_response_posts(
    *,
    response_text: str,
    narrator_mode: str,
    turn_id: str,
    clock: Callable[[], datetime],
) -> list[Post]:
    if narrator_mode != PER_CHARACTER:
        return [_make_post(AuthorKind.NARRATOR, response_text, None, turn_id, clock)]

    segments = split_response(response_text)
    if not segments:
        return [_make_post(AuthorKind.NARRATOR, response_text, None, turn_id, clock)]

    posts: list[Post] = []
    for seg in segments:
        if seg.kind == "character":
            posts.append(
                _make_post(AuthorKind.NPC, seg.body, seg.ref, turn_id, clock)
            )
        else:
            posts.append(
                _make_post(AuthorKind.NARRATOR, seg.body, None, turn_id, clock)
            )
    return posts


def _make_post(
    author_kind: AuthorKind,
    body: str,
    npc_ref: str | None,
    turn_id: str,
    clock: Callable[[], datetime],
) -> Post:
    return Post(
        id=str(uuid.uuid4()),
        scene_id="",
        order_in_scene=0,
        author_kind=author_kind,
        body=body,
        is_player=False,
        created_at=clock(),
        turn_id=turn_id,
        author_npc_ref=npc_ref,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/orchestrator/test_post_splitting.py -v`
Expected: all PASS

- [ ] **Step 5: Wire into orchestrator service**

In `backend/src/grimoire/orchestrator/service.py`, replace the single-post creation block (lines 961-970):

```python
        from grimoire.extractor.together import strip_tracker_block

        response_post = self._new_post(
            author_kind=SceneAuthorKind.NARRATOR,
            body=strip_tracker_block(response_text),
            is_player=False,
            turn_id=turn_id,
        )
        await self._scenes.append_post(scene_id, response_post)
        await self._emit_fragment(turn_id, campaign_id, scene_appended=True)
```

With:

```python
        from grimoire.extractor.together import strip_tracker_block
        from grimoire.orchestrator.post_splitting import create_response_posts
        from grimoire.scenes.narrator_mode import effective_response_mode

        cleaned_text = strip_tracker_block(response_text)
        narrator_mode = effective_response_mode(
            scene_override=scene_obj.narrator_response_mode,
            campaign_row=await self._store.get_campaign(campaign_id),
        )
        response_posts = create_response_posts(
            response_text=cleaned_text,
            narrator_mode=narrator_mode,
            turn_id=turn_id,
            clock=self._clock,
        )
        for rp in response_posts:
            await self._scenes.append_post(scene_id, rp)
        await self._emit_fragment(turn_id, campaign_id, scene_appended=True)
```

- [ ] **Step 6: Run existing orchestrator tests**

Run: `cd backend && uv run pytest tests/orchestrator/ -v`
Expected: all PASS (existing tests use `all_at_once` by default, behavior unchanged)

- [ ] **Step 7: Commit**

```
git add backend/src/grimoire/orchestrator/post_splitting.py backend/tests/orchestrator/test_post_splitting.py backend/src/grimoire/orchestrator/service.py
git commit -m "feat(orchestrator): split response into per-character posts in per_character mode"
```

---

### Task 6: Add speaker loop config and events

**Files:**
- Modify: `backend/src/grimoire/orchestrator/config.py:70-99`
- Modify: `backend/src/grimoire/events.py`

- [ ] **Step 1: Add `SpeakerLoopConfig` to orchestrator config**

In `backend/src/grimoire/orchestrator/config.py`, add a new dataclass before `OrchestratorConfig`:

```python
@dataclass
class SpeakerLoopConfig:
    timeout_seconds: float = 300.0
    speaker_select_max_tokens: int = 50
```

Add it to `OrchestratorConfig`:

```python
    speaker_loop: SpeakerLoopConfig = None  # type: ignore[assignment]
```

Add to `__post_init__`:

```python
        if self.speaker_loop is None:
            self.speaker_loop = SpeakerLoopConfig()
```

Add to `__all__`:

```python
    "SpeakerLoopConfig",
```

- [ ] **Step 2: Add speaker loop event constants**

In `backend/src/grimoire/events.py`, add under the "Turn lifecycle" section:

```python
SPEAKER_ROUND_WAITING = "speaker_round_waiting"
SPEAKER_ROUND_NEXT = "speaker_round_next"
```

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/orchestrator/config.py backend/src/grimoire/events.py
git commit -m "feat(orchestrator): add speaker loop config and event constants"
```

---

### Task 7: Speaker selection LLM call

**Files:**
- Create: `backend/src/grimoire/orchestrator/speaker_select.py`
- Create: `backend/tests/orchestrator/test_speaker_select.py`

- [ ] **Step 1: Write tests for speaker selection**

Create `backend/tests/orchestrator/test_speaker_select.py`:

```python
"""Tests for :mod:`grimoire.orchestrator.speaker_select`."""

from __future__ import annotations

import random

import pytest

from grimoire.orchestrator.speaker_select import (
    parse_speaker_ref,
    select_fallback_speaker,
)


def test_parse_speaker_ref_exact_match() -> None:
    present = ["worlds/w/characters/alice", "worlds/w/characters/bob"]
    assert parse_speaker_ref("worlds/w/characters/alice", present) == "worlds/w/characters/alice"


def test_parse_speaker_ref_trailing_whitespace() -> None:
    present = ["worlds/w/characters/alice"]
    assert parse_speaker_ref("  worlds/w/characters/alice  \n", present) == "worlds/w/characters/alice"


def test_parse_speaker_ref_unknown_returns_none() -> None:
    present = ["worlds/w/characters/alice"]
    assert parse_speaker_ref("worlds/w/characters/unknown", present) is None


def test_parse_speaker_ref_empty_returns_none() -> None:
    assert parse_speaker_ref("", ["worlds/w/characters/alice"]) is None


def test_fallback_speaker_picks_least_recent() -> None:
    present = ["alice", "bob", "charlie"]
    recent_speakers = ["charlie", "bob"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, recent_speakers, rng)
    assert result == "alice"


def test_fallback_speaker_all_spoken_picks_least_recent() -> None:
    present = ["alice", "bob"]
    recent_speakers = ["alice", "bob", "alice"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, recent_speakers, rng)
    assert result == "bob"


def test_fallback_speaker_none_spoken_picks_random() -> None:
    present = ["alice", "bob"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, [], rng)
    assert result in present


def test_fallback_speaker_single_npc() -> None:
    result = select_fallback_speaker(["alice"], [], random.Random(0))
    assert result == "alice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/orchestrator/test_speaker_select.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement speaker selection helpers**

Create `backend/src/grimoire/orchestrator/speaker_select.py`:

```python
"""Speaker selection for per_character_multi_call mode."""

from __future__ import annotations

import random


def parse_speaker_ref(raw: str, present_refs: list[str]) -> str | None:
    cleaned = raw.strip()
    if cleaned in present_refs:
        return cleaned
    return None


def select_fallback_speaker(
    present_refs: list[str],
    recent_speakers: list[str],
    rng: random.Random,
) -> str:
    if len(present_refs) == 1:
        return present_refs[0]

    # Find refs that haven't spoken at all
    spoken = set(recent_speakers)
    unspoken = [r for r in present_refs if r not in spoken]
    if unspoken:
        return rng.choice(unspoken)

    # All have spoken — pick whoever spoke least recently.
    # Walk recent_speakers from most recent, return the present ref
    # found last (= least recently spoken).
    last_index: dict[str, int] = {}
    for i, ref in enumerate(reversed(recent_speakers)):
        if ref in set(present_refs):
            last_index.setdefault(ref, i)
    if last_index:
        return max(last_index, key=lambda r: last_index[r])

    return rng.choice(present_refs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/orchestrator/test_speaker_select.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/orchestrator/speaker_select.py backend/tests/orchestrator/test_speaker_select.py
git commit -m "feat(orchestrator): add speaker selection helpers for multi-call mode"
```

---

### Task 8: Orchestrator speaker loop (multi-call mode)

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Create: `backend/tests/orchestrator/test_speaker_loop.py`

This is the largest task — it adds the speaker loop to the orchestrator.

- [ ] **Step 1: Write an integration test for the speaker loop**

Create `backend/tests/orchestrator/test_speaker_loop.py`. This test verifies the public API surface — `submit_post` in multi-call mode results in NPC posts and a waiting state, and `next_speaker` continues the loop:

```python
"""Integration tests for the per_character_multi_call speaker loop.

These tests verify the orchestrator's public API behavior. They use
minimal stubs for the collaborating services.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from grimoire.events import SPEAKER_ROUND_WAITING
from grimoire.orchestrator.service import OrchestratorService
from grimoire.scenes.narrator_mode import PER_CHARACTER_MULTI_CALL
from grimoire.scenes.types import AuthorKind


# This test requires a more complete stub setup that mirrors the
# orchestrator's full wiring. The implementation step will adapt
# existing test fixtures from the orchestrator test suite.
# Placeholder structure — implementation will fill in the stubs.

@pytest.mark.asyncio
async def test_multi_call_mode_emits_speaker_round_waiting() -> None:
    """After the first NPC responds in multi-call mode, the orchestrator
    should emit SPEAKER_ROUND_WAITING and pause for player input."""
    pytest.skip("Will be implemented alongside the orchestrator speaker loop wiring")
```

- [ ] **Step 2: Add `next_speaker` method and speaker loop to the orchestrator**

In `backend/src/grimoire/orchestrator/service.py`:

1. Add a `_speaker_loop_events` dict to `_CampaignTurnState`:

```python
@dataclass
class _CampaignTurnState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queued: int = 0
    active: _ActiveTurn | None = None
    last_turn_id: TurnId | None = None
    pending_pre_roll: _PendingPreRoll | None = None
    speaker_loop_event: asyncio.Event | None = None
```

2. Add a public `next_speaker` method to `OrchestratorService` (after the `advance` method):

```python
    async def next_speaker(self, campaign_id: CampaignId) -> None:
        state = self._campaign_state(campaign_id)
        if state.speaker_loop_event is not None:
            state.speaker_loop_event.set()
```

3. Add a `_run_speaker_loop` method that the turn body calls when the mode is `per_character_multi_call`. This method:
   - Gets present NPC refs from the scene
   - Loops: select speaker → build context → stream → create NPC post → emit `SPEAKER_ROUND_WAITING` → wait for event or timeout
   - Exits when the event is not set within timeout (player disconnected) or when a new player post arrives

The full implementation will be written by the implementing agent, following the patterns established by the existing `_continue_turn_after_pre_roll` method. Key integration points:

- After the player post is appended and the advance decision is made, if the mode is `per_character_multi_call`, call `_run_speaker_loop` instead of `_continue_turn_after_pre_roll`.
- The speaker loop calls `_stream_main_response` for each character turn.
- Between character turns, it emits `SPEAKER_ROUND_WAITING` via the event bus and `_push_to_ws`.
- It waits on `state.speaker_loop_event` with timeout from `self._config.speaker_loop.timeout_seconds`.

- [ ] **Step 3: Run existing orchestrator tests to check regressions**

Run: `cd backend && uv run pytest tests/orchestrator/ -v`
Expected: all PASS

- [ ] **Step 4: Flesh out and run the speaker loop integration test**

Update `backend/tests/orchestrator/test_speaker_loop.py` to use the real orchestrator with stubs and verify:
- In multi-call mode, after a player post, at least one NPC post is created
- `SPEAKER_ROUND_WAITING` event is emitted via WS push
- Calling `next_speaker` creates another NPC post

Run: `cd backend && uv run pytest tests/orchestrator/test_speaker_loop.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/orchestrator/service.py backend/tests/orchestrator/test_speaker_loop.py
git commit -m "feat(orchestrator): implement speaker loop for per_character_multi_call mode"
```

---

### Task 9: API endpoint for next-speaker

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/schemas.py`
- Modify: `backend/src/grimoire/api/campaigns/turns.py`
- Create: `backend/tests/api/campaigns/test_next_speaker.py`

- [ ] **Step 1: Write test for the endpoint**

Create `backend/tests/api/campaigns/test_next_speaker.py`:

```python
"""Tests for the next-speaker API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from grimoire.api.campaigns.turns import router


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    return AsyncMock()


def test_next_speaker_endpoint_calls_orchestrator(mock_orchestrator: AsyncMock) -> None:
    """The endpoint should forward to orchestrator.next_speaker."""
    # This test will use the app fixture from the existing test setup.
    pytest.skip("Will be implemented with the full app fixture")
```

- [ ] **Step 2: Add the schema and endpoint**

In `backend/src/grimoire/api/campaigns/schemas.py`, add:

```python
class NextSpeakerPayload(BaseModel):
    scene_id: str
```

In `backend/src/grimoire/api/campaigns/turns.py`, add the import and endpoint:

```python
from .schemas import (
    AdvanceTurnPayload,
    NextSpeakerPayload,
    ResolveProposalsPayload,
    ResolveSceneBreakPayload,
    SubmitTurnPayload,
    UndoPayload,
)

@router.post("/{campaign_id}/turns/next-speaker")
async def next_speaker(
    campaign_id: str,
    payload: NextSpeakerPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        await orchestrator.next_speaker(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"accepted": True}
```

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/campaigns/schemas.py backend/src/grimoire/api/campaigns/turns.py backend/tests/api/campaigns/test_next_speaker.py
git commit -m "feat(api): add POST /turns/next-speaker endpoint"
```

---

### Task 10: Frontend — playReducer and WebSocket handler

**Files:**
- Modify: `frontend/src/routes/campaign/playReducer.ts`
- Modify: `frontend/src/routes/campaign/usePlayStreamEvents.ts`

- [ ] **Step 1: Add speaker loop state and action to playReducer**

In `frontend/src/routes/campaign/playReducer.ts`:

Add to `PlayState` (after `advanceReason`):

```typescript
  nextSpeakerEnabled: boolean;
  speakerRoundActive: boolean;
```

Add to `PlayAction` union:

```typescript
  | { type: "set-next-speaker"; enabled: boolean }
  | { type: "set-speaker-round"; active: boolean }
```

Add to `initialPlayState`:

```typescript
  nextSpeakerEnabled: false,
  speakerRoundActive: false,
```

Add cases to `playReducer`:

```typescript
    case "set-next-speaker":
      return { ...state, nextSpeakerEnabled: action.enabled };
    case "set-speaker-round":
      return { ...state, speakerRoundActive: action.active };
```

- [ ] **Step 2: Handle `speaker_round_waiting` WebSocket event**

In `frontend/src/routes/campaign/usePlayStreamEvents.ts`:

Add `"speaker_round_waiting"` to the `STREAM_EVENT_TYPES` array.

Add a case to the `onEvent` switch:

```typescript
        case "speaker_round_waiting":
          dispatch({ type: "set-next-speaker", enabled: true });
          dispatch({ type: "set-speaker-round", active: true });
          return;
```

Also, in the `turn_complete` case, reset speaker round state:

```typescript
          dispatch({ type: "set-next-speaker", enabled: false });
          dispatch({ type: "set-speaker-round", active: false });
```

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && pnpm test`
Expected: all PASS

- [ ] **Step 4: Commit**

```
git add frontend/src/routes/campaign/playReducer.ts frontend/src/routes/campaign/usePlayStreamEvents.ts
git commit -m "feat(frontend): add speaker round state to playReducer and WS handler"
```

---

### Task 11: Frontend — "Next" button in InputArea

**Files:**
- Modify: `frontend/src/routes/campaign/InputArea.tsx`
- Modify: `frontend/src/api/campaign/api.ts`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add `nextSpeaker` API call**

In `frontend/src/api/campaign/api.ts`, add after the `advance` method:

```typescript
  nextSpeaker: (id: string, sceneId: string) =>
    api.post<{ accepted: boolean }>(`/api/campaigns/${enc(id)}/turns/next-speaker`, {
      scene_id: sceneId,
    }),
```

- [ ] **Step 2: Add props and button to InputArea**

In `frontend/src/routes/campaign/InputArea.tsx`:

Add to `Props`:

```typescript
  onNextSpeaker: () => Promise<void>;
  nextSpeakerEnabled: boolean;
  speakerRoundActive: boolean;
```

Add state and handler inside the component:

```typescript
  const [requestingNext, setRequestingNext] = useState(false);

  const nextSpeaker = useCallback(async () => {
    if (!nextSpeakerEnabled || requestingNext || busy) return;
    setRequestingNext(true);
    try {
      await onNextSpeaker();
    } finally {
      setRequestingNext(false);
    }
  }, [nextSpeakerEnabled, requestingNext, busy, onNextSpeaker]);
```

Add the button in the `input-actions` div, after the Advance button:

```tsx
        {speakerRoundActive && (
          <button
            type="button"
            onClick={nextSpeaker}
            disabled={!nextSpeakerEnabled || requestingNext || busy}
            className="input-next-speaker"
            title="Let the next character speak"
          >
            {requestingNext ? "Calling…" : "Next"}
          </button>
        )}
```

- [ ] **Step 3: Add CSS for the button**

In `frontend/src/index.css`, add near the `.input-advance` styles:

```css
.input-next-speaker {
  padding: 0.3rem 0.8rem;
  border-radius: 4px;
  cursor: pointer;
}
.input-next-speaker:not(:disabled):hover {
  opacity: 0.85;
}
.input-next-speaker:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
```

- [ ] **Step 4: Wire props from the parent component**

The parent component that renders `InputArea` needs to pass the new props. Find where `InputArea` is used (the Play page component) and add:

```typescript
onNextSpeaker={async () => {
  if (state.scene) {
    await campaignApi.nextSpeaker(campaignId, state.scene.id);
  }
}}
nextSpeakerEnabled={state.nextSpeakerEnabled}
speakerRoundActive={state.speakerRoundActive}
```

- [ ] **Step 5: Run frontend type checks and tests**

Run: `cd frontend && pnpm typecheck && pnpm test`
Expected: all PASS

- [ ] **Step 6: Commit**

```
git add frontend/src/routes/campaign/InputArea.tsx frontend/src/api/campaign/api.ts frontend/src/index.css
git commit -m "feat(frontend): add Next button for speaker loop in InputArea"
```

---

### Task 12: End-to-end smoke test and final checks

**Files:**
- No new files

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && uv run pytest -x -v`
Expected: all PASS

- [ ] **Step 2: Run ruff lint and format**

Run: `cd backend && uv run ruff check && uv run ruff format --check`
Expected: clean

- [ ] **Step 3: Run full frontend checks**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm test`
Expected: all PASS

- [ ] **Step 4: Manual smoke test**

Start the app and verify:
1. Default `all_at_once` mode works as before (no regression)
2. Setting a scene to `per_character` mode and submitting a post produces individually-tagged posts from the model, split into separate posts in the UI
3. The `per_character_multi_call` mode shows the "Next" button after each NPC post

Run: `scripts/run.sh`

- [ ] **Step 5: Final commit if any fixes needed**

```
git add -u
git commit -m "fix: address smoke test findings"
```
