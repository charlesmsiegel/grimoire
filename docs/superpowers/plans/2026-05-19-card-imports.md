# Character Card Imports Implementation Plan

> **Implementation status (as of 2026-05-19): NOT STARTED**
> - `grep -r card_import backend/src/grimoire` returns no matches.
> - All 5 branches (A macros, B lore schema, C lore algorithm, D ingest pipeline, E REST + frontend) are net-new work.
> - **Downstream dependency:** `lore-reclassification` needs Task E2 extended for `lore_overrides`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-card-imports-design.md`. Macro expansion, character_book→lore, alternate_greetings→library greetings, richer LoreEntry, system_prompt routing, per-import markdown report.

**Architecture:** Five branches.

- **A** `feature/imports-A-macros` — `grimoire.characters.macros` module + late-stage `_resolve_runtime_macros` pass.
- **B** `feature/imports-B-lore-schema` — extended `LoreEntry` + `LorePosition` + `SelectiveLogic`; tier routing helper.
- **C** `feature/imports-C-lore-algorithm` — full `WorldService.lore_for_post` rewrite.
- **D** `feature/imports-D-ingest-pipeline` — write greetings + lore in `import_character_card`; report generator.
- **E** `feature/imports-E-rest-frontend` — preview/commit endpoints + import dialog.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Pydantic v2; React/TS frontend.

---

## Conventions

Standard. No cross-spec deps — fully self-contained.

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/imports-A-macros          -b feature/imports-A-macros          main
git worktree add .worktrees/imports-B-lore-schema     -b feature/imports-B-lore-schema     main
git worktree add .worktrees/imports-C-lore-algorithm  -b feature/imports-C-lore-algorithm  main
git worktree add .worktrees/imports-D-ingest-pipeline -b feature/imports-D-ingest-pipeline main
git worktree add .worktrees/imports-E-rest-frontend   -b feature/imports-E-rest-frontend   main
```

---

# Branch A — Macros

### Task A1: `expand_macros` core

**Files:**
- Create: `backend/src/grimoire/characters/macros.py`.
- Test: `backend/tests/characters/test_macros.py`.

- [ ] **Step 1: Failing tests** (per spec macro table)

```python
from grimoire.characters.macros import expand_macros


def test_char_replaced_with_card_name():
    text, warnings = expand_macros(
        "{{char}} smiled.", char_name="Beatrice",
        card_asset_id="cv1", field_name="description",
    )
    assert text == "Beatrice smiled."
    assert warnings == []


def test_user_preserved_at_ingest():
    text, _ = expand_macros(
        "{{char}} addressed {{user}}.", char_name="Beatrice",
        card_asset_id="cv1", field_name="description",
    )
    assert "{{user}}" in text


def test_random_seeded_deterministic():
    t1, _ = expand_macros("{{random:a,b,c}}", char_name="X",
                          card_asset_id="cid", field_name="f")
    t2, _ = expand_macros("{{random:a,b,c}}", char_name="X",
                          card_asset_id="cid", field_name="f")
    assert t1 == t2


def test_random_alternate_separator():
    text, _ = expand_macros("{{random:a::b::c}}", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert text in {"a", "b", "c"}


def test_roll_NdM_sum():
    text, _ = expand_macros("{{roll:2d6}}", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert 2 <= int(text) <= 12


def test_newline_expands_to_newline_char():
    text, _ = expand_macros("a{{newline}}b", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert text == "a\nb"


def test_trim_consumes_one_whitespace_each_side():
    text, _ = expand_macros("a {{trim}} b", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert text == "ab"


def test_trim_chains():
    text, _ = expand_macros("a {{trim}}{{trim}} b", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert text == "ab"


def test_comment_stripped():
    text, _ = expand_macros("foo{{// hidden}} bar", char_name="X",
                            card_asset_id="cid", field_name="f")
    assert text == "foo bar"


def test_unknown_macro_passthrough_with_warning():
    text, warnings = expand_macros("foo {{calendar}} bar", char_name="X",
                                   card_asset_id="cid", field_name="f")
    assert text == "foo {{calendar}} bar"
    assert any("calendar" in w for w in warnings)


def test_nested_macros_disallowed_with_warning():
    text, warnings = expand_macros("{{random:{{char}},other}}",
                                   char_name="X",
                                   card_asset_id="cid", field_name="f")
    # Outer left literal because nested
    assert any("nest" in w.lower() for w in warnings)


def test_macro_position_distinct_seeds():
    # Two random macros in the same field get different positions/seeds
    text, _ = expand_macros("{{random:a,b}}{{random:a,b}}",
                            char_name="X", card_asset_id="cid", field_name="f")
    # Determinism guaranteed; the two picks may or may not differ but
    # different positions mean different RNG state.
    text2, _ = expand_macros("{{random:a,b}}{{random:a,b}}",
                             char_name="X", card_asset_id="cid", field_name="f")
    assert text == text2
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/characters/macros.py
from __future__ import annotations

import hashlib
import random
import re
from typing import Iterable


_MACRO_PATTERN = re.compile(r"\{\{([^{}]+?)\}\}")


def expand_macros(
    text: str,
    *,
    char_name: str,
    card_asset_id: str,
    field_name: str,
    keep_user: bool = True,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    macro_index = 0
    out = []
    pos = 0
    for m in _MACRO_PATTERN.finditer(text):
        out.append(text[pos:m.start()])
        body = m.group(1).strip()
        replacement, warn = _expand_one(
            body, char_name=char_name, card_asset_id=card_asset_id,
            field_name=field_name, macro_index=macro_index,
            keep_user=keep_user,
        )
        if warn:
            warnings.append(warn)
        out.append(replacement)
        pos = m.end()
        macro_index += 1
    out.append(text[pos:])
    result = "".join(out)
    result = _apply_trim(result)
    return result, warnings


def _seed(card_asset_id: str, field_name: str, macro_index: int) -> int:
    digest = hashlib.sha256(
        f"{card_asset_id}::{field_name}::{macro_index}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _expand_one(
    body: str, *, char_name, card_asset_id, field_name, macro_index, keep_user,
) -> tuple[str, str | None]:
    # Nested check
    if "{{" in body or "}}" in body:
        return f"{{{{{body}}}}}", f"nested macro left literal: {body!r} in {field_name}"

    if body.lower() == "char":
        return char_name, None
    if body.lower() == "user":
        return "{{user}}" if keep_user else "the player", None
    if body.lower() == "newline":
        return "\n", None
    if body == "trim":
        return "\x00TRIM\x00", None         # sentinel; _apply_trim handles
    if body.startswith("//"):
        return "", None

    if body.lower().startswith("random:") or body.lower().startswith("pick:"):
        opts = body.split(":", 1)[1]
        choices = opts.split("::") if "::" in opts else opts.split(",")
        rng = random.Random(_seed(card_asset_id, field_name, macro_index))
        return rng.choice([c.strip() for c in choices]), None

    if body.lower().startswith("roll:"):
        spec = body.split(":", 1)[1].strip()
        m = re.match(r"^(\d+)d(\d+)$", spec)
        if not m:
            return f"{{{{{body}}}}}", f"bad roll spec: {spec!r}"
        n, sides = int(m.group(1)), int(m.group(2))
        rng = random.Random(_seed(card_asset_id, field_name, macro_index))
        total = sum(rng.randint(1, sides) for _ in range(n))
        return str(total), None

    return f"{{{{{body}}}}}", f"unknown macro: {body!r} in {field_name}"


_TRIM_PATTERN = re.compile(r" ?\x00TRIM\x00 ?")


def _apply_trim(text: str) -> str:
    # Repeatedly apply to handle chained trims
    while True:
        new = _TRIM_PATTERN.sub("", text)
        if new == text:
            return new
        text = new
```

- [ ] **Step 3: Tests PASS, commit.**

### Task A2: Late-stage `{{user}}` substitution

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:build` — call `_resolve_runtime_macros` at end.
- Test: `backend/tests/context/test_runtime_macros.py`.

- [ ] **Step 1: Failing test**

```python
async def test_user_macro_substituted_with_active_pc_name(builder, seeded_state):
    # A greeting with {{user}} loaded into context
    prompt = await builder.build(... )
    blob = "\n".join(m.content for m in prompt.messages)
    assert "{{user}}" not in blob
    assert seeded_state.active_pc_name in blob


async def test_user_macro_substituted_with_the_player_when_no_pc(builder, seeded_state_no_pc):
    prompt = await builder.build(...)
    blob = "\n".join(m.content for m in prompt.messages)
    assert "the player" in blob


async def test_resolve_runtime_macros_idempotent(builder, ...):
    # Calling twice yields the same output (after first replaces, second is no-op)
    ...
```

- [ ] **Step 2: Implement** — small post-pass at the end of `build()`:

```python
def _resolve_runtime_macros(messages: list[Message], active_pc_name: str | None) -> list[Message]:
    pc_name = active_pc_name or "the player"
    out = []
    for m in messages:
        out.append(replace(m, content=m.content.replace("{{user}}", pc_name)))
    return out
```

- [ ] **Step 3: Tests PASS + commit + merge A.**

---

# Branch B — `LoreEntry` schema + position routing

### Task B1: Extend `LoreEntry`

**Files:**
- Modify: `backend/src/grimoire/types/world.py:LoreEntry`.
- Test: `backend/tests/types/test_lore_entry.py`.

- [ ] **Step 1: Failing tests for backwards-compat defaults + enums**

```python
def test_legacy_lore_yaml_parses_with_defaults():
    yaml = """
    id: lore-1
    title: The Tremere
    body: Vampires of secrecy
    tags: [vampire, faction]
    keywords: [Tremere]
    related_factions: [tremere]
    secrecy: open
    """
    entry = LoreEntry.model_validate(yaml.safe_load(yaml))
    assert entry.enabled is True
    assert entry.position == LorePosition.AFTER_CAST
    assert entry.priority == 100
    assert entry.probability == 100
    assert entry.scan_depth is None


def test_full_shape_roundtrips():
    entry = LoreEntry(
        id="x", title="X", body="b", keywords=["x"],
        secondary_keys=["y"], selective_logic=SelectiveLogic.AND_ALL,
        constant=True, enabled=False, case_sensitive=True,
        match_whole_words=True, priority=42, probability=30,
        position=LorePosition.AT_DEPTH, at_depth=2, scan_depth=10,
        comment="test", import_source=ImportSource(
            kind="sillytavern_character_book", card_asset_id="cv1", source_index=3,
        ),
    )
    yaml = entry.model_dump()
    assert LoreEntry.model_validate(yaml) == entry
```

- [ ] **Step 2: Implement** new fields + enums (per spec).

- [ ] **Step 3: Commit + merge B.**

---

# Branch C — `lore_for_post` algorithm rewrite

### Task C1: Full algorithm

**Files:**
- Modify: `backend/src/grimoire/world/service.py:lore_for_post` (line 647).
- Test: `backend/tests/world/test_lore_for_post.py`.

- [ ] **Step 1: Failing tests** covering every algorithm branch (per spec):

```python
async def test_enabled_false_skipped(world, library):
    await library.create_entity("lore", {"id": "x", ..., "enabled": False})
    hits = await world.lore_for_post(setting_id, scene, turn_id="t_1")
    assert not any(h.id == "x" for h in hits)


async def test_constant_fires_unconditionally(world, library):
    ...


async def test_primary_key_match_case_insensitive_substring(...):
    ...


async def test_primary_key_match_case_sensitive(...):
    ...


async def test_primary_key_match_whole_words(...):
    ...


async def test_secondary_keys_and_any(...):
    ...


async def test_secondary_keys_and_all(...):
    ...


async def test_secondary_keys_not_any(...):
    ...


async def test_secondary_keys_not_all(...):
    ...


async def test_empty_secondary_keys_treated_as_no_requirement(...):
    ...


async def test_probability_deterministic_on_entry_id_turn_id(...):
    # Same (entry_id, turn_id) → same dice roll
    h1 = await world.lore_for_post(..., turn_id="t_1")
    h2 = await world.lore_for_post(..., turn_id="t_1")
    assert {x.id for x in h1} == {x.id for x in h2}


async def test_scan_depth_limits_haystack(...):
    # scan_depth=2 + keyword appearing in 3rd-from-last post → not matched
    ...


async def test_scan_depth_zero_scans_nothing_constant_only(...):
    ...


async def test_priority_sort_and_max_results(...):
    ...
```

- [ ] **Step 2: Implement** the full algorithm (per spec):

```python
async def lore_for_post(
    self,
    setting_id: str,
    scene: Scene,
    *,
    turn_id: str | None = None,
    max_results: int | None = None,
) -> list[LoreEntry]:
    cfg = self.config.lore
    turn_id = turn_id or scene.current_turn_id
    all_lore = await self.library.list_in_setting(setting_id, "lore")
    hits: list[tuple[int, LoreEntry]] = []

    for entry in all_lore:
        if not entry.enabled:
            continue
        if entry.constant:
            hits.append((entry.priority, entry))
            continue
        haystack = self._build_haystack(scene, scan_depth=entry.scan_depth)
        if not self._primary_keyword_match(entry, haystack):
            continue
        if entry.secondary_keys and not self._evaluate_selective_logic(entry, haystack):
            continue
        if not self._probability_check(entry, turn_id):
            continue
        hits.append((entry.priority, entry))

    hits.sort(key=lambda x: (-x[0], x[1].id))
    if max_results is None:
        max_results = cfg.max_lore_in_archive
    return [e for _, e in hits[:max_results]]


def _primary_keyword_match(self, entry, haystack):
    if not entry.keywords:
        return False
    if entry.case_sensitive:
        text = haystack
    else:
        text = haystack.lower()
    for kw in entry.keywords:
        needle = kw if entry.case_sensitive else kw.lower()
        if entry.match_whole_words:
            pattern = rf"\b{re.escape(needle)}\b"
            if re.search(pattern, text):
                return True
        else:
            if needle in text:
                return True
    return False


def _evaluate_selective_logic(self, entry, haystack):
    text = haystack if entry.case_sensitive else haystack.lower()
    matches = []
    for k in entry.secondary_keys:
        needle = k if entry.case_sensitive else k.lower()
        if entry.match_whole_words:
            matches.append(bool(re.search(rf"\b{re.escape(needle)}\b", text)))
        else:
            matches.append(needle in text)
    if entry.selective_logic == SelectiveLogic.AND_ANY:
        return any(matches)
    if entry.selective_logic == SelectiveLogic.AND_ALL:
        return all(matches)
    if entry.selective_logic == SelectiveLogic.NOT_ANY:
        return not any(matches)
    if entry.selective_logic == SelectiveLogic.NOT_ALL:
        return not all(matches)
    return True


def _probability_check(self, entry, turn_id):
    if entry.probability >= 100:
        return True
    if entry.probability <= 0:
        return False
    digest = hashlib.sha256(f"{entry.id}::{turn_id}".encode()).digest()
    roll = int.from_bytes(digest[:4], "big") % 100
    return roll < entry.probability


def _build_haystack(self, scene, *, scan_depth):
    posts = scene.posts
    if scan_depth is not None and scan_depth >= 0:
        posts = posts[-scan_depth:] if scan_depth > 0 else []
    return "\n".join(p.body for p in posts)
```

- [ ] **Step 3: Tests PASS + commit + merge C.**

### Task C2: Context Builder position routing

**Files:**
- Modify: `backend/src/grimoire/context/builder.py` — `_route_lore_to_tier` helper.
- Test: `backend/tests/context/test_lore_routing.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_before_cast_lands_in_spotlight(...):
async def test_after_cast_lands_in_background(...):
async def test_at_depth_lands_in_lock_in_recent_posts(...):
async def test_archive_lands_in_archive(...):
```

- [ ] **Step 2: Implement** helper, wire into the existing lore-collection step in `builder.py`.

- [ ] **Step 3: Tests PASS + commit.**

---

# Branch D — Ingest pipeline writes greetings + lore

### Task D1: `import_character_card` greetings + lore writes

**Files:**
- Modify: `backend/src/grimoire/characters/service.py:import_character_card` (line 1324).
- Modify: `backend/src/grimoire/characters/ingest.py` — populate `IngestedLoreEntry` list.
- Modify: `backend/src/grimoire/types/characters.py:IngestOptions` — new toggles.
- Test: `backend/tests/characters/test_ingest_v2.py` (extend) and `test_import_card_writes.py` (new).

- [ ] **Step 1: Failing tests**

```python
async def test_character_book_entries_become_lore_files(service, library, ...):
    card = make_v2_card_with_character_book(entries=[
        {"keys": ["Tremere"], "content": "Vampires of secrecy."},
        {"keys": ["Camarilla"], "content": "Ruling sect."},
    ])
    result, _ = await service.import_character_card(card, target_setting_id="by-night")
    assert any(id.endswith("--tremere.md") for id in result.created if "lore" in id)
    assert any(id.endswith("--camarilla.md") for id in result.created if "lore" in id)


async def test_alternate_greetings_become_greeting_files(...):
    ...


async def test_first_mes_becomes_default_greeting(...):
    ...


async def test_macro_pass_applied_to_lore_body(...):
    # character_book entry body has {{char}}; resolves to character name
    ...


async def test_collision_suffix_2_3(...):
    # Pre-existing lore file with same slug → suffix -2
    ...


async def test_character_write_failure_aborts(...):
    # Mock LibraryService.create_entity to raise → no greetings/lore written
    ...


async def test_greeting_failure_non_fatal(...):
    # One greeting write fails → ImportResult.errors contains it; rest written
    ...


async def test_system_prompt_routed_to_campaign_addendum(...):
    # Not into character body
    ...
```

- [ ] **Step 2: Implement** — extend `_finalize_import` to:
  1. Write character (existing).
  2. For each `alternate_greetings[i]` and `first_mes`, write greeting library entry.
  3. For each `character_book.entries[i]`, write lore library entry.
  4. Apply macro pass to all text fields.
  5. Collision suffix logic.
  6. Build markdown report; write to `data/library/imports/<timestamp>-<char_slug>.md`.

- [ ] **Step 3: Tests PASS + commit + merge D.**

### Task D2: Avatar metadata stripping

**Files:**
- Modify: `backend/src/grimoire/characters/ingest.py:_strip_png_metadata` (new).

- [ ] **Step 1: Test**

```python
def test_strip_avatar_keeps_chara_chunk_drops_others():
    png_bytes = make_png_with_chunks(["tEXt:chara", "tEXt:Software", "iCCP:something"])
    stripped = strip_avatar_metadata(png_bytes)
    chunks = list(iter_png_chunks(stripped))
    assert "chara" in [k for kind, k in chunks if kind == "tEXt"]
    assert "Software" not in [k for kind, k in chunks if kind == "tEXt"]
    assert all(kind != "iCCP" for kind, _ in chunks)
```

- [ ] **Step 2: Implement.** Apply unconditionally before persisting the avatar.

- [ ] **Step 3: Commit.**

---

# Branch E — REST + frontend preview/commit

### Task E1: Preview/commit/report routes

**Files:**
- Modify: `backend/src/grimoire/api/library.py` or new `api/imports.py`.
- Test: `backend/tests/api/test_imports_routes.py`.

Routes:
```
POST   /library/settings/{sid}/imports/sillytavern               # multipart bytes; runs parse-only
POST   /library/settings/{sid}/imports/sillytavern/commit        # body: {preview_id, options}
GET    /library/imports
GET    /library/imports/{report_id}
```

The preview path stores parsed `IngestedCharacterCard` in an in-memory dict keyed by `preview_id` with a TTL (15 min). Commit reads the preview, runs `_finalize_import` and writes the report.

- [ ] **Step 1: Failing tests + implement + commit.**

### Task E2: Import dialog

**Files:**
- Create: `frontend/src/routes/library/Imports/ImportDialog.tsx`.
- Create: `frontend/src/api/imports.ts`.

UI: file picker → POST preview → render character preview + greetings list + lore list + warnings → user toggles `IngestOptions` checkboxes → POST commit → show report link.

- [ ] **Step end: Tests + commit + merge E.**

---

# Integration check

- [ ] **Step end1: Full suite.**
- [ ] **Step end2: Smoke** — import a real SillyTavern card; verify character + N greetings + M lore files materialize; verify macros expanded; verify `{{user}}` still literal until runtime.
- [ ] **Step end3: COMPLETED doc + delete design.**
