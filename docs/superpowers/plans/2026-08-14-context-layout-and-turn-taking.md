# Prompt layout and active-speaker turn-taking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issue #29's two remaining implementable layers — a user-editable
prompt section layout, and a derived active-speaker nomination for group scenes
— both behind config toggles that ship off.

**Architecture:** `context/assemble._SECTIONS` becomes a *catalog* with stable
ids. A new `context/layout.py` owns `<home>/prompt_layout.json` and a pure merge
of stored overrides onto that catalog. A new `context/speaker.py` derives, from
the transcript and the present cast, which NPC should lead the turn, and renders
one new `SPOTLIGHT` section. Neither module imports `assemble`, so the module
graph stays acyclic.

**Tech Stack:** Python 3 / FastAPI / pytest (`backend/`), Jinja2 templates
(`templates/`), React + Vite + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-08-14-context-layout-and-turn-taking-design.md`

## Global Constraints

- **Both features ship off.** `prompt_layout_enabled` and `speaker_turn_taking`
  default to `"off"`. With both off the assembled prompt must be **byte-identical**
  to today's. This is the claim Task 8 asserts directly.
- **pydantic v1/v2-agnostic** — plain `BaseModel` fields only; dump via
  `routes.common._dump`. No `model_dump()`, `Field`, validators, `ConfigDict`
  (enforced by `test_pydantic_guard.py`).
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`), and
  filesystem access through the `store.paths` resolvers (`test_paths_guard.py`).
- **Imports at module scope, module graph acyclic** (`test_import_guard.py`).
  Inside `store/`, a cross-package import binds a *submodule*: `from ..scenes import
  serialize as scenes_serialize`, then `scenes_serialize.match_name(...)`. Never
  `from ..scenes.serialize import match_name`.
- **No real world/campaign/character names in fixtures.** Reuse the codebase's
  placeholders: Seraphine, Mara, Winifred, Realm, Saltmarch.
- **Tier is never user-editable**, and neither are `pcless_only` / `opener_only` /
  `except_opener`. See the spec's "Tier is not user-editable".
- **Relabelling renames the inspector row only** — templates emit their own
  `# Heading`. The UI must say so.
- Gate command: `make check`. Backend only: `make check-py`.

---

### Task 1: Stable ids on the section catalog

`Section` has no identity independent of its label, and the layout keys off
identity. This task adds it and changes nothing else — the prompt is untouched.

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py:250-315` (`Section`, `_SECTIONS`, `_render_sections`)
- Modify: `backend/src/grimoire/store/context/assemble.py:516-519` (`_breakdown` rows)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Produces: `Section` NamedTuple gains a leading `id: str` field. Every
  `_SECTIONS` entry carries a unique snake_case id. `_render_sections` returns
  dicts with an added `"id"` key; `pack.pack` passes it through; `_breakdown`
  rows carry `"id"`.

- [ ] **Step 1: Write the failing test**

```python
def test_section_ids_are_unique_and_reach_the_breakdown(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store.context import assemble
    ids = [s.id for s in assemble._SECTIONS]
    assert len(ids) == len(set(ids)), "section ids must be unique"
    assert all(i and i.replace("_", "").isalnum() for i in ids)
```

Then, in the existing scene fixture used by `test_context.py`, assert every
breakdown row carries a non-empty `id`:

```python
def test_breakdown_rows_carry_ids(scene_store):
    cid, sid = scene_store
    rows = store.context.context_sections(cid, sid)
    assert rows and all(r["id"] for r in rows)
```

Reuse whatever fixture `test_context.py` already uses to build a campaign and
scene; do not invent a second one.

- [ ] **Step 2: Run it and watch it fail**

Run: `make check-py PYTEST_ARGS="backend/tests/test_context.py -k section_ids or breakdown_rows_carry_ids"`
Expected: FAIL — `Section` has no attribute `id`.

- [ ] **Step 3: Add the field and the ids**

`Section` gains `id: str` as its **first** field, so every entry reads
`Section("world_info", "World info", "scene/sections/world_info.j2", pack.SPOTLIGHT)`.
Ids, in catalog order:

`opener_instruction, global_system_prompt, prose_style, natural_prose,
card_system_prompts, character_descriptions, character_state, transient_state,
relationships, player_personas, offscreen_scene, absent_players,
message_examples, story_so_far, archive, plot_threads, commitments, today,
weather, current_setting, world_info, recalled_lore, group_state,
mechanics_rules, mechanics_sheets, off_scene_cast, mechanics_response_format,
response_format, transient_tracker, response_budget`

In `_render_sections`, add `"id": section.id` to the emitted dict. In
`_breakdown`, add `"id": s["id"]` to the section rows, and give the
`Conversation history`, `Post-history instructions` and appended rows synthetic
ids (`"history"`, `"post_history"`, and `f"appended_{n}"`) so **every** row has
one — `ContextBreakdown` keys on it in Task 7 and a missing key would collide
across two appended blocks.

Check `pack.pack` in `context/pack.py`: it must carry unknown keys through
rather than rebuilding section dicts field-by-field. If it rebuilds, add `id`
there too.

- [ ] **Step 4: Run the tests**

Run: `make check-py`
Expected: PASS. `verify_templates.py` is unaffected — it never reads `_SECTIONS`.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Give every prompt section an id its label cannot supply"
```

---

### Task 2: `context/layout.py` — the merge, pure and tested

The merge rules are the whole feature. Build and test them with no I/O, then add
the file underneath in Task 3.

**Files:**
- Create: `backend/src/grimoire/store/context/layout.py`
- Test: `backend/tests/test_context_layout.py`

**Interfaces:**
- Consumes: `Section` from Task 1 (duck-typed — `layout.py` must NOT import
  `assemble`; it takes the catalog as an argument and calls `._replace(label=…)`).
- Produces:
  - `MAX_LABEL: int = 60`
  - `merge(catalog: list, stored: list[dict]) -> list` — pure.
  - `sanitize(entries) -> list[dict]` — shape-check for the write path.

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace

from grimoire.store.context import layout


def _s(sid, label="L"):
    """A catalog stand-in: only `id` and `label` matter to the merge, and
    `_replace` is what a NamedTuple Section gives it."""
    class Sec(SimpleNamespace):
        def _replace(self, **kw):
            return Sec(**{**self.__dict__, **kw})
    return Sec(id=sid, label=label)


CATALOG = [_s("a"), _s("b"), _s("c"), _s("d")]


def test_empty_layout_is_the_catalog_unchanged():
    assert [s.id for s in layout.merge(CATALOG, [])] == ["a", "b", "c", "d"]


def test_stored_order_wins():
    stored = [{"id": "d"}, {"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["d", "c", "b", "a"]


def test_disabled_section_is_omitted():
    stored = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "c"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "c", "d"]


def test_unknown_id_is_ignored():
    stored = [{"id": "a"}, {"id": "gone"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]


def test_new_catalog_section_lands_after_its_neighbour_not_at_the_end():
    """The upgrade rule. The saved layout predates "c"; "c" must arrive between
    its catalog neighbours, not be appended below everything."""
    stored = [{"id": "d"}, {"id": "a"}, {"id": "b"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["d", "a", "b", "c"]
    stored = [{"id": "a"}, {"id": "b"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]


def test_new_section_with_no_present_predecessor_goes_to_the_front():
    stored = [{"id": "b"}, {"id": "c"}, {"id": "d"}]
    catalog = [_s("new"), *CATALOG]
    assert [s.id for s in layout.merge(catalog, stored)][0] == "new"


def test_two_new_sections_keep_catalog_order_between_themselves():
    catalog = [_s("a"), _s("x"), _s("y"), _s("b")]
    stored = [{"id": "a"}, {"id": "b"}]
    assert [s.id for s in layout.merge(catalog, stored)] == ["a", "x", "y", "b"]


def test_a_new_section_after_a_disabled_one_falls_back_further_up():
    """"c" is new; its neighbour "b" is disabled, so it cannot anchor to it."""
    stored = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "c", "d"]


def test_label_override_applies_and_is_capped():
    stored = [{"id": "a", "label": "  Mine  "}, {"id": "b", "label": "x" * 200}]
    out = {s.id: s.label for s in layout.merge(CATALOG, stored)}
    assert out["a"] == "Mine"
    assert len(out["b"]) == layout.MAX_LABEL


def test_blank_or_non_string_label_falls_back_to_the_catalog():
    stored = [{"id": "a", "label": "   "}, {"id": "b", "label": 7}]
    out = {s.id: s.label for s in layout.merge(CATALOG, stored)}
    assert out["a"] == "L" and out["b"] == "L"


def test_duplicate_ids_keep_only_the_first():
    """The second "b" is dropped, label and all. "a" then has no surviving
    predecessor, so the insert rule puts it at the front."""
    stored = [{"id": "b"}, {"id": "b", "label": "second"}]
    out = layout.merge(CATALOG, stored)
    assert [s.id for s in out] == ["a", "b", "c", "d"]
    assert {s.id: s.label for s in out}["b"] == "L"


def test_malformed_entries_are_skipped_individually():
    stored = ["not a dict", {"no_id": 1}, {"id": 5}, {"id": "b"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]
```

Both of those last two land on plain catalog order, and that is the rule
working, not a weak assertion: with `out = [b]`, `a` has no surviving
predecessor so it goes to the front, then `c` anchors after `b` and `d` after
`c`. `test_stored_order_wins` is what proves the merge can reorder at all.

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_layout.py -v`
Expected: FAIL — no module named `layout`.

- [ ] **Step 3: Implement the merge**

```python
"""The user's prompt layout: which sections render, in what order, under what
inspector label (#29).

The catalog is `context.assemble._SECTIONS` and stays there. This module holds
the stored OVERRIDES and the merge, and it takes the catalog as an argument
rather than importing it -- `assemble` imports this, so reaching back would
close a cycle the import guard exists to catch.

What is overridable, and what is not
------------------------------------
Order, presence and the inspector's label. Not the tier, and not the three
render selectors.

The tier is `pack.py`'s business: it documents at length why `RECALLED` sits
below `ARCHIVE` -- semantic recall promises to be purely additive, and within a
tier the largest section is dropped first, so sharing a tier let recalled lore
evict the Earlier-scenes section. A user-editable tier is a control whose only
function is to break that promise. Prompt order and drop order are two axes and
only the first is a preference.

`except_opener` is the same kind of thing: the opener is streamed unpersisted
into a box the reader adopts by hand, so the machine-readable tracker block
must not render into it, and there is no reply afterwards to strip it from.

The label is the INSPECTOR's row name and never reaches the model -- each
section template emits its own `# Heading`. Editing the text a section sends is
already possible and always was: `prompts.py` loads `templates/` from disk with
auto-reload precisely so prompts are editable without touching code.

The upgrade rule
----------------
A layout saved today does not know about a section a later version ships.
Appending the strangers would put a new Response format block below everything
-- the one place it must not be -- so a catalog section the layout never
mentioned is inserted after its nearest preceding catalog neighbour that
survived the merge, at the front when it has none. The mirror rule is that an
id the catalog no longer has is ignored, which retires a removed section with
no migration.
"""

from __future__ import annotations

import json

from .. import atomic, config, locks
from ..paths import ensure_home, home

#: Longest stored label. A row name, not prose -- and the cap is also what stops
#: a hand-edited file from putting a paragraph in the inspector's rail.
MAX_LABEL = 60


def _clean_label(entry: dict, default: str) -> str:
    label = entry.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()[:MAX_LABEL]
    return default


def merge(catalog: list, stored: list) -> list:
    """The catalog, reordered / filtered / relabelled by `stored`. Pure.

    Defensive throughout: `stored` comes off disk and may be anything. A
    malformed entry is skipped on its own and a malformed file simply merges as
    empty, because the alternative -- raising -- takes scene generation down
    over a preference.
    """
    by_id = {s.id: s for s in catalog}
    order = [s.id for s in catalog]
    out: list = []
    seen: set[str] = set()
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or sid not in by_id or sid in seen:
            continue
        seen.add(sid)          # marked seen even when disabled: an explicit
        if entry.get("enabled") is False:   # "off" is an answer, so the
            continue                        # insert-missing pass must not undo it
        out.append(by_id[sid]._replace(label=_clean_label(entry, by_id[sid].label)))

    # Catalog sections the layout never mentioned, in catalog order so two
    # consecutive newcomers keep their relative order (each is findable as the
    # next one's predecessor once inserted).
    for sid in [s for s in order if s not in seen]:
        idx = order.index(sid)
        pos = 0
        for prev in reversed(order[:idx]):
            at = next((n for n, s in enumerate(out) if s.id == prev), None)
            if at is not None:
                pos = at + 1
                break
        out.insert(pos, by_id[sid])
    return out


def sanitize(entries) -> list[dict]:
    """The write path's shape check: `[{"id", "label", "enabled"}]`, ids unique.

    Ids are NOT checked against the catalog. An id this version does not know
    is kept on write and ignored on read, so saving a layout from a build that
    is one version behind cannot silently delete the newer build's sections
    from the file.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            continue
        seen.add(sid)
        label = entry.get("label")
        out.append({"id": sid,
                    "label": label.strip()[:MAX_LABEL] if isinstance(label, str) else "",
                    "enabled": entry.get("enabled") is not False})
    return out
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_layout.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "The prompt layout's merge: order, presence, label — and the upgrade rule"
```

---

### Task 3: The layout file, the toggle, and `apply`

**Files:**
- Modify: `backend/src/grimoire/store/context/layout.py`
- Modify: `backend/src/grimoire/store/config.py:110-155` (`_CONFIG_KEYS`, defaults)
- Test: `backend/tests/test_context_layout.py`

**Interfaces:**
- Produces:
  - `config.DEFAULT_PROMPT_LAYOUT_ENABLED = "off"`, key `prompt_layout_enabled`
  - `layout.enabled() -> bool`
  - `layout.read_layout() -> list[dict]` (never raises)
  - `layout.write_layout(entries) -> list[dict]`
  - `layout.apply(catalog) -> list`

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from grimoire.store import config
from grimoire.store.context import layout


@pytest.fixture
def store_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_apply_is_the_catalog_while_the_toggle_is_off(store_home):
    layout.write_layout([{"id": "d"}, {"id": "a"}])
    assert [s.id for s in layout.apply(CATALOG)] == ["a", "b", "c", "d"]


def test_apply_honours_the_layout_once_the_toggle_is_on(store_home):
    layout.write_layout([{"id": "d"}, {"id": "a"}])
    config.write_config(prompt_layout_enabled="on")
    assert [s.id for s in layout.apply(CATALOG)][:2] == ["d", "a"]


def test_the_toggle_bypasses_without_deleting(store_home):
    layout.write_layout([{"id": "d"}])
    config.write_config(prompt_layout_enabled="on")
    config.write_config(prompt_layout_enabled="off")
    assert layout.read_layout() == [{"id": "d", "label": "", "enabled": True}]


def test_a_truncated_file_reads_as_no_layout(store_home):
    (store_home / "prompt_layout.json").write_text('{"sections": [', encoding="utf-8")
    assert layout.read_layout() == []
    config.write_config(prompt_layout_enabled="on")
    assert [s.id for s in layout.apply(CATALOG)] == ["a", "b", "c", "d"]


def test_a_wrong_shaped_file_reads_as_no_layout(store_home):
    (store_home / "prompt_layout.json").write_text('["a", "b"]', encoding="utf-8")
    assert layout.read_layout() == []


def test_a_missing_file_reads_as_no_layout(store_home):
    assert layout.read_layout() == []


def test_write_round_trips_through_sanitize(store_home):
    layout.write_layout([{"id": "a", "label": "  Mine  ", "enabled": False},
                         "junk", {"id": "a"}])
    assert layout.read_layout() == [{"id": "a", "label": "Mine", "enabled": False}]
```

`CATALOG` is the same stand-in list from Task 2 — lift it into a module-level
helper both test groups share.

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_layout.py -v`
Expected: FAIL — `layout` has no attribute `apply`.

- [ ] **Step 3: Implement**

Add to `config.py`: `DEFAULT_PROMPT_LAYOUT_ENABLED = "off"` and
`DEFAULT_SPEAKER_TURN_TAKING = "off"` beside the other feature defaults (both are
added now so Task 5 does not have to touch `_CONFIG_KEYS` again), both keys
appended to `_CONFIG_KEYS` and to `read_config`'s `defaults` dict. Note the
comment already on `_LENGTH_KEYS`: a key omitted from `_CONFIG_KEYS` is silently
dropped by `read_config`, so both must be listed in both places.

Add to `layout.py`:

```python
def _path():
    return home() / "prompt_layout.json"


def enabled() -> bool:
    """Whether the stored layout is applied. Off is the default and off is
    byte-identical: `apply` hands back the catalog untouched, so an install that
    never opens the editor sends what it always sent. Turning it off KEEPS the
    file -- it is a bypass, so a reader can A/B their ordering against the
    default without rebuilding it."""
    return config.read_config().get("prompt_layout_enabled") == "on"


def read_layout() -> list[dict]:
    """The stored entries, sanitized. Never raises.

    Every failure -- no file, a truncated one, a hand-edit that made it a list
    of strings, an unreadable one -- is the same answer: no layout, which means
    the catalog. A preference must not be able to take a scene's generation
    down.
    """
    ensure_home()
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    sections = raw.get("sections")
    return sanitize(sections) if isinstance(sections, list) else []


def write_layout(entries) -> list[dict]:
    """Replace the stored layout. Returns what was stored.

    Under `config_lock` and through `atomic`: the file is rewritten whole, so
    two unlocked read-modify-writes lose one of them, and it is the same
    global-settings lock domain `config.md` sits in.
    """
    clean = sanitize(entries)
    ensure_home()
    with locks.config_lock():
        atomic.write_text(_path(), json.dumps({"sections": clean}, indent=2) + "\n")
    return clean


def apply(catalog: list) -> list:
    """The section list to render: the catalog, or the merge when the toggle is on."""
    return merge(catalog, read_layout()) if enabled() else list(catalog)
```

- [ ] **Step 4: Run the tests**

Run: `make check-py`
Expected: PASS. If `test_atomic_guard.py` or `test_paths_guard.py` complains,
the fix is to use the resolvers — not a marker.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "prompt_layout.json, and a toggle that bypasses rather than deletes"
```

---

### Task 4: `_render_sections` applies the layout

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py:30-31` (import), `:323-344` (`_render_sections`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `layout.apply` from Task 3.

- [ ] **Step 1: Write the failing test**

Against the existing `test_context.py` campaign/scene fixture:

```python
def test_layout_reorders_and_drops_sections_in_the_real_prompt(scene_store):
    cid, sid = scene_store
    before = [r["id"] for r in store.context.context_sections(cid, sid)]
    assert "world_info" in before and "response_budget" in before

    store.context.layout.write_layout(
        [{"id": "response_budget"}, {"id": "world_info", "enabled": False}])
    store.config.write_config(prompt_layout_enabled="on")

    after = [r["id"] for r in store.context.context_sections(cid, sid)]
    assert after[0] == "response_budget"
    assert "world_info" not in after
```

The fixture must produce a scene where both of those sections are non-empty —
`response_budget` always renders, and `world_info` needs one activated lore
entry. Follow whatever `test_context.py` already does to activate world info.

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — the order and the presence are unchanged.

- [ ] **Step 3: Implement**

In `assemble.py`, add `layout` to the `from . import (…)` line, and change
`_render_sections`'s loop header from `for section in _SECTIONS:` to
`for section in layout.apply(_SECTIONS):`.

Extend `_SECTIONS`'s docstring comment: the list is the **catalog**, and
`layout.apply` is what the render actually walks.

Export `layout` from `context/__init__.py` beside the other submodules so
`store.context.layout` resolves.

- [ ] **Step 4: Run the tests**

Run: `make check-py`
Expected: PASS, including the frozen-campaign sweep — with the toggle off the
merge never runs.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Render the layout, not the catalog"
```

---

### Task 5: `context/speaker.py` — the nomination

**Files:**
- Create: `backend/src/grimoire/store/context/speaker.py`
- Test: `backend/tests/test_context_speaker.py`

**Interfaces:**
- Produces: `nominate(npc_names: list[str], history: list[dict]) -> dict | None`
  returning `{"lead": str, "quiet": list[str], "silent_for": int, "spoken": bool,
  "reason": "addressed" | "rotation"}`.
- Consumes: `scenes_serialize.match_name`, `scenes_serialize.SYNTHETIC_SPEAKERS`.

History entries are transcript messages: `{"role": "user"|"assistant",
"speaker": str|None, "content": str}`.

- [ ] **Step 1: Write the failing tests**

```python
from grimoire.store.context import speaker

NPCS = ["Seraphine Vale", "Mara Quist", "Winifred Ash"]


def _npc(name, text="..."):
    return {"role": "assistant", "speaker": name, "content": text}


def _player(text):
    return {"role": "user", "speaker": None, "content": text}


def test_no_section_below_two_npcs():
    assert speaker.nominate([], []) is None
    assert speaker.nominate(["Seraphine Vale"], [_player("hello")]) is None


def test_direct_address_by_full_name_wins():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist"),
            _player("Winifred Ash, what did you see?")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash" and out["reason"] == "addressed"


def test_direct_address_by_unique_first_name_wins():
    hist = [_npc("Winifred Ash"), _player("Mara, hold the door.")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist" and out["reason"] == "addressed"


def test_two_npcs_named_in_one_post_is_not_a_direct_address():
    hist = [_npc("Winifred Ash"), _player("Mara and Seraphine, both of you.")]
    out = speaker.nominate(NPCS, hist)
    assert out["reason"] == "rotation"


def test_an_ambiguous_first_name_addresses_nobody():
    npcs = ["Winifred Ash", "Winifred Vale", "Mara Quist"]
    hist = [_npc("Mara Quist"), _player("Winifred, answer me.")]
    out = speaker.nominate(npcs, hist)
    assert out["reason"] == "rotation"


def test_a_first_name_that_is_another_actors_full_name_addresses_nobody():
    npcs = ["Mara Quist", "Mara", "Winifred Ash"]
    hist = [_npc("Winifred Ash"), _player("Mara, wait.")]
    assert speaker.nominate(npcs, hist)["reason"] == "rotation"


def test_only_the_last_player_post_is_read_for_address():
    hist = [_player("Winifred Ash, wait."), _npc("Winifred Ash"),
            _player("What now?")]
    assert speaker.nominate(NPCS, hist)["reason"] == "rotation"


def test_a_name_inside_a_longer_word_is_not_a_mention():
    hist = [_npc("Winifred Ash"), _player("The maraud was loud.")]
    assert speaker.nominate(NPCS, hist)["reason"] == "rotation"


def test_never_spoken_leads_over_merely_quiet():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash" and out["spoken"] is False


def test_least_recently_spoken_leads():
    hist = [_npc("Winifred Ash"), _npc("Seraphine Vale"), _npc("Mara Quist")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash"
    assert out["spoken"] is True and out["silent_for"] == 2


def test_equal_silence_breaks_toward_the_fewest_blocks():
    """Both last spoke the same distance back; Mara has said less overall."""
    hist = [_npc("Seraphine Vale"), _npc("Seraphine Vale"),
            _npc("Mara Quist"), _npc("Seraphine Vale")]
    out = speaker.nominate(["Seraphine Vale", "Mara Quist"], hist)
    assert out["lead"] == "Mara Quist"


def test_a_full_tie_breaks_toward_cast_order():
    hist = []
    assert speaker.nominate(NPCS, hist)["lead"] == "Seraphine Vale"


def test_quiet_lists_the_others_and_never_the_lead():
    out = speaker.nominate(NPCS, [_npc("Seraphine Vale")])
    assert out["lead"] not in out["quiet"]
    assert sorted(out["quiet"] + [out["lead"]]) == sorted(NPCS)


def test_partial_speaker_labels_canonicalize_to_the_cast_name():
    """The transcript stamps "Winifred" — that is still Winifred Ash speaking,
    and counting it as a stranger would nominate her as never-spoken."""
    hist = [_npc("Winifred"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist"


def test_synthetic_speakers_are_not_npc_turns():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist")]
    for synthetic in scenes_serialize.SYNTHETIC_SPEAKERS:
        hist.append({"role": "assistant", "speaker": synthetic, "content": "x"})
    assert speaker.nominate(NPCS, hist)["lead"] == "Winifred Ash"


def test_blank_and_non_string_names_are_dropped():
    assert speaker.nominate(["Seraphine Vale", "  ", None], []) is None
```

Import `scenes_serialize` in the test as
`from grimoire.store.scenes import serialize as scenes_serialize`.

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_speaker.py -v`
Expected: FAIL — no module named `speaker`.

- [ ] **Step 3: Implement**

```python
"""Who leads this turn (#29's group active-speaker layer).

With several NPCs cast, every card is concatenated into Character descriptions
and the model picks a speaker implicitly. In a two-hander that is fine; with
four in a room it produces the failure every group scene has -- one character
monologues for three turns while the others stand silently.

DERIVED, NEVER STORED. A rotation counter would be a second source of truth
about who has spoken, free to disagree with the transcript the moment a post is
undone, a turn regenerated, or the scene file hand-edited -- and `store/scenes`
serializes its whole mutator surface precisely to keep the transcript
authoritative. Deriving costs one pass over history already in memory, cannot
drift, and makes regenerate reproduce the same nomination rather than advancing
a rotation the reader never saw.

Pure: no I/O, no store reads. The caller passes the present NPC names (the CARD
names, which is what the model is holding and what the transcript stamps) and
the projected history.
"""

from __future__ import annotations

import re

from ..scenes import serialize as scenes_serialize


def _first_token(name: str) -> str:
    return name.split()[0] if name.split() else ""


def _address_labels(names: list[str]) -> dict[str, str]:
    """label -> the one name it can mean. A label shared by two present actors
    is absent, so it addresses nobody.

    Same rule `_voice_notes` applies for the same reason: an instruction pointed
    at the wrong character is worse than no instruction. Full names and first
    names go into one namespace, so a first name that is another actor's whole
    name is ambiguous too.
    """
    counts: dict[str, list[str]] = {}
    for name in names:
        for label in {name, _first_token(name)}:
            if label:
                counts.setdefault(label.casefold(), []).append(name)
    return {label: owners[0] for label, owners in counts.items() if len(owners) == 1}


def _mentioned(text: str, names: list[str]) -> str | None:
    """The one present NPC the text names, or None for nobody and None for more
    than one. Whole-word only: "the maraud was loud" does not summon Mara."""
    labels = _address_labels(names)
    hits = {owner for label, owner in labels.items()
            if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.IGNORECASE)}
    return hits.pop() if len(hits) == 1 else None


def nominate(npc_names: list[str], history: list[dict]) -> dict | None:
    """Who should lead this turn, or None for "say nothing".

    None below two present NPCs: turn-taking is a group problem, and "Seraphine
    leads this turn" in a two-hander is tokens spent telling the model what the
    cast list already said.
    """
    names = [n.strip() for n in npc_names
             if isinstance(n, str) and n.strip()]
    names = list(dict.fromkeys(names))
    if len(names) < 2:
        return None

    blocks = [m for m in history
              if m.get("role") == "assistant"
              and m.get("speaker") not in scenes_serialize.SYNTHETIC_SPEAKERS
              and isinstance(m.get("speaker"), str)]
    #: distance from the end, in model blocks, of each NPC's last block; and how
    #: many blocks each has taken. A label the transcript stamped short
    #: ("Winifred") canonicalizes to the cast name, exactly as drift measurement
    #: does -- counting it as a stranger would nominate a talkative character as
    #: never having spoken.
    last: dict[str, int] = {}
    spoken: dict[str, int] = {}
    for pos, m in enumerate(blocks):
        who = scenes_serialize.match_name(m["speaker"], names) or m["speaker"]
        if who not in names:
            continue
        last[who] = len(blocks) - 1 - pos
        spoken[who] = spoken.get(who, 0) + 1

    #: Never spoken sorts ahead of everyone who has: one past the longest
    #: possible silence.
    def silence(name: str) -> int:
        return last[name] + 1 if name in last else len(blocks) + 1

    ranked = sorted(names,
                    key=lambda n: (-silence(n), spoken.get(n, 0), names.index(n)))
    lead = ranked[0]

    reason = "rotation"
    last_post = next((m for m in reversed(history) if m.get("role") == "user"), None)
    if last_post and isinstance(last_post.get("content"), str):
        addressed = _mentioned(last_post["content"], names)
        if addressed:
            lead, reason = addressed, "addressed"

    return {"lead": lead, "reason": reason,
            "spoken": lead in last,
            "silent_for": last.get(lead, 0),
            "quiet": [n for n in ranked if n != lead]}
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_speaker.py -v`
Expected: PASS. If `test_least_recently_spoken_leads` disagrees about
`silent_for`, settle the unit first — it is "model blocks since that character
last spoke" — and make test and code agree on it rather than tuning one.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Nominate a lead from the transcript, not from a counter"
```

---

### Task 6: The section, wired and toggled

**Files:**
- Create: `templates/scene/sections/active_speaker.j2`
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_assemble` data, `_SECTIONS`)
- Modify: `backend/src/grimoire/store/config.py` (`speaker_turn_taking` accessor)
- Modify: `scripts/verify_templates.py:597-643` (the `gather()` render mirror)
- Modify: `templates/README.md` (the section's variables)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `speaker.nominate` (Task 5), `layout.apply` (Task 4).
- Produces: `data["speaker"]`, section id `active_speaker`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_speaker_section_appears_only_when_turned_on(group_scene):
    cid, sid = group_scene          # a scene with three NPCs cast
    assert "active_speaker" not in [r["id"] for r in store.context.context_sections(cid, sid)]

    store.config.write_config(speaker_turn_taking="on")
    rows = {r["id"]: r for r in store.context.context_sections(cid, sid)}
    assert "active_speaker" in rows
    assert rows["active_speaker"]["tier"] == store.context.pack.SPOTLIGHT
    assert "leads this turn" in rows["active_speaker"]["text"]


def test_no_speaker_section_in_a_two_hander(single_npc_scene):
    cid, sid = single_npc_scene
    store.config.write_config(speaker_turn_taking="on")
    assert "active_speaker" not in [r["id"] for r in store.context.context_sections(cid, sid)]
```

Build `group_scene` by casting three NPCs into the existing test campaign
fixture. Use Seraphine / Mara / Winifred.

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — no such section.

- [ ] **Step 3: Implement**

`config.py`:

```python
def speaker_turn_taking() -> bool:
    """Whether the active-speaker section renders (#29). Off by default: it adds
    tokens to every group turn, so it may not arrive by upgrade."""
    return read_config().get("speaker_turn_taking") == "on"
```

`assemble.py` — add `speaker` to the `from . import (…)` line, and in `_assemble`'s
`data` dict, beside the other cast-derived keys:

```python
        # Derived on every pass, never stored -- see speaker.py. Off by default,
        # and `None` renders no section at all.
        "speaker": (speaker.nominate(npc_names, history)
                    if config.speaker_turn_taking() else None),
```

Use `history` (the raw scene messages, which carry `speaker`), **not**
`sub_history` — `_project_history` is built later and reshapes the entries.
Confirm `history`'s dicts still carry `speaker`; they are `dict(m)` copies of
`scene["messages"]`, so they do.

Add the catalog entry immediately after `transient_state`:

```python
    # Beside the state sections and at their tier: who is live right now. It
    # sits after Transient state so the model reads what each character is
    # feeling before it reads which of them should carry the turn.
    Section("active_speaker", "Active speaker", "scene/sections/active_speaker.j2",
            pack.SPOTLIGHT),
```

`templates/scene/sections/active_speaker.j2`:

```jinja
{#- Who carries this turn in a group scene (#29). Renders nothing unless
    `speaker` is set — the layer is off by default, and it stays quiet below two
    present NPCs. Vars: speaker (None | {lead, quiet, reason, spoken,
    silent_for}). -#}
{%- if speaker %}# Active speaker
{{ speaker.lead }} leads this turn{% if speaker.reason == "addressed" %} — the last post spoke to them directly{% elif not speaker.spoken %} — they have not spoken yet in this scene{% elif speaker.silent_for %} — they have been silent for {{ speaker.silent_for }} posts{% endif %}.
{% if speaker.quiet %}{{ speaker.quiet | join(", ") }} {{ "is" if speaker.quiet | length == 1 else "are" }} present: let them react briefly or not at all. Do not give every character a turn.{% endif %}{% endif -%}
```

`scripts/verify_templates.py` — add `"scene/sections/active_speaker.j2"` to the
`names` list right after `transient_state.j2` (line ~615), and add a `speaker`
key to the mirror's data. The mirror is a hand-written copy of the order, so it
must reflect the catalog; add both the `None` case and a populated one if the
harness's fixture supports it, otherwise `None` is enough — the toggle is off in
that throwaway store.

`templates/README.md` — document `active_speaker.j2` and its one variable
alongside the other section templates.

- [ ] **Step 4: Run the gate**

Run: `make check-py && python scripts/verify_templates.py`
Expected: PASS. A `StrictUndefined` error means `speaker` is missing from a
render path — the verifier's mirror, most likely.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "The active-speaker section, off unless asked for"
```

---

### Task 7: Routes, API client, and the Prompt layout panel

**Files:**
- Modify: `backend/src/grimoire/routes/config.py` (two routes, `_public_config`)
- Modify: `backend/src/grimoire/routes/models.py` (`ConfigUpdate`, `PromptLayoutUpdate`)
- Create: `frontend/src/components/PromptLayoutEditor.tsx`
- Create: `frontend/src/components/PromptLayoutEditor.test.tsx`
- Modify: `frontend/src/api/client.ts` (`Config` type, `getPromptLayout`, `savePromptLayout`)
- Modify: `frontend/src/routes/ConfigView.tsx` (`DRAFT_FIELDS`, `SECTIONS`, the panel)
- Modify: `frontend/src/components/ContextBreakdown.tsx:34` (key on `id`)
- Test: `backend/tests/test_routes_config.py` (or wherever config routes are tested)

**Interfaces:**
- `GET /api/prompt-layout` → `{"enabled": bool, "sections": [{"id", "label",
  "default_label", "tier", "enabled"}]}` — the **merged** view, so the panel shows
  new sections in place.
- `PUT /api/prompt-layout` body `{"sections": [{"id", "label", "enabled"}]}` →
  the same shape as GET.

- [ ] **Step 1: Write the failing backend test**

```python
def test_prompt_layout_round_trips(client):
    got = client.get("/api/prompt-layout").json()
    assert got["enabled"] is False
    ids = [s["id"] for s in got["sections"]]
    assert ids[0] == "opener_instruction" and "active_speaker" in ids
    assert all(s["default_label"] and s["tier"] for s in got["sections"])

    body = {"sections": [{"id": "world_info", "label": "Lore", "enabled": True},
                         {"id": "weather", "label": "", "enabled": False}]}
    saved = client.put("/api/prompt-layout", json=body).json()
    assert saved["sections"][0]["id"] == "world_info"
    assert [s for s in saved["sections"] if s["id"] == "world_info"][0]["label"] == "Lore"
    assert [s for s in saved["sections"] if s["id"] == "weather"][0]["enabled"] is False


def test_the_two_toggles_are_public_config(client):
    cfg = client.get("/api/config").json()
    assert cfg["prompt_layout_enabled"] == "off"
    assert cfg["speaker_turn_taking"] == "off"
    client.put("/api/config", json={"speaker_turn_taking": "on"})
    assert client.get("/api/config").json()["speaker_turn_taking"] == "on"
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — 404 on `/api/prompt-layout`.

- [ ] **Step 3: Implement the backend**

`models.py`:

```python
class PromptLayoutSection(BaseModel):
    id: str
    label: str = ""
    enabled: bool = True


class PromptLayoutUpdate(BaseModel):
    sections: list[PromptLayoutSection] = []
```

`ConfigUpdate` gains `prompt_layout_enabled: str | None = None` and
`speaker_turn_taking: str | None = None`. `_public_config` gains both keys with
their `store.config.DEFAULT_*` fallbacks.

`routes/config.py`:

```python
def _layout_view() -> dict:
    """The merged layout: every catalog section, in the order it will render,
    with the label it will render under and the label it would render under
    unedited. Merged rather than raw, so a section this version added shows up
    in the editor at its author's position instead of being invisible until the
    reader saves."""
    stored = store.context.layout.read_layout()
    by_id = {e["id"]: e for e in stored}
    merged = store.context.layout.merge(store.context.assemble._SECTIONS, stored)
    shown = [{"id": s.id, "label": s.label,
              "default_label": _default_labels()[s.id], "tier": s.tier,
              "enabled": True} for s in merged]
    # merge() drops the disabled ones; the editor has to show them, switched off,
    # or there would be no way back on.
    ...
```

Take care here: `merge` omits disabled sections, but the **editor must list
them** — otherwise a section switched off can never be switched back on. Build
the view as: merged (enabled) sections in merged order, then each disabled
stored id re-inserted at the position it occupies in the *stored* list, marked
`enabled: false`. Write a small helper in `layout.py` for this —
`describe(catalog, stored) -> list[dict]` — and unit-test it in
`test_context_layout.py`:

```python
def test_describe_keeps_disabled_sections_visible_and_in_place():
    stored = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "c"}]
    rows = layout.describe(CATALOG, stored)
    assert [r["id"] for r in rows] == ["a", "b", "c", "d"]
    assert [r["enabled"] for r in rows] == [True, False, True, True]
    assert rows[1]["default_label"] == "L"
```

`describe` belongs in `layout.py`, not the route: it is the same merge rule with
one more column, and putting it in the route would make the editor's ordering a
second implementation of the render's.

Routes:

```python
@router.get("/prompt-layout")
def get_prompt_layout():
    return {"enabled": store.context.layout.enabled(),
            "sections": store.context.layout.describe(
                store.context.assemble._SECTIONS, store.context.layout.read_layout())}


@router.put("/prompt-layout")
def put_prompt_layout(body: PromptLayoutUpdate):
    stored = store.context.layout.write_layout([_dump(s) for s in body.sections])
    return {"enabled": store.context.layout.enabled(),
            "sections": store.context.layout.describe(
                store.context.assemble._SECTIONS, stored)}
```

`_dump` is `routes.common._dump` — required by the pydantic guard.

`assemble._SECTIONS` is underscored and read from a route. Either promote it to
`assemble.SECTIONS` (preferred — it is now a public catalog other modules
legitimately read) and keep `_SECTIONS` as an alias-free rename, updating
`verify_templates.py`'s comment that references the name, or expose
`assemble.catalog()`. Pick the rename; update every reference.

- [ ] **Step 4: Run the backend tests**

Run: `make check-py`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend test**

`PromptLayoutEditor.test.tsx` — mock `api`, render, and assert:
- rows render in server order with their labels;
- **↓ on the first row** swaps it with the second and marks the panel dirty;
- unchecking a row's checkbox and saving sends `enabled: false` for that id;
- typing a label and saving sends it;
- **Reset** sends an empty `sections` list;
- the panel renders the caption explaining that a label renames the inspector
  row and not the prompt heading (assert the copy, so the honesty survives a
  refactor).

Follow the existing mocking style in `ConfigView.test.tsx`. Run vitest **from**
`frontend/` — `npx --prefix frontend vitest run` skips
`frontend/vitest.config.ts` and disables `globals`.

- [ ] **Step 6: Implement the frontend**

`client.ts` — `Config` gains `prompt_layout_enabled` and `speaker_turn_taking`;
add `PromptLayoutSection` type and `getPromptLayout()` / `savePromptLayout(sections)`.

`PromptLayoutEditor.tsx` — its own component; `ConfigView.tsx` is already 900
lines. Local draft state, ↑/↓ buttons (not drag-and-drop: no new dependency, and
keyboard-reachable), a checkbox, a text input placeholdered with
`default_label`, Save and Reset. Rows keyed on `id`.

`ConfigView.tsx` — `DRAFT_FIELDS` gains both toggle keys; a new
`{ id: "layout", group: "What the model sees", label: "Prompt layout", fields:
["prompt_layout_enabled"] }` section renders the toggle plus
`<PromptLayoutEditor />`; `speaker_turn_taking` joins the existing `context`
section's fields with a caption naming the cost ("adds a short section to every
group-scene turn").

`ContextBreakdown.tsx:34` — `key={s.label}` → `key={s.id}`; add `id: string` to
the row type.

- [ ] **Step 7: Run the gate**

Run: `make check`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "The layout editor, and an inspector that keys on ids"
```

---

### Task 8: The byte-identity claim, asserted

The whole "ships disabled" argument rests on this, and nothing so far proves it.

**Files:**
- Test: `backend/tests/test_context.py`

- [ ] **Step 1: Write the test**

```python
def test_both_layers_off_change_nothing(group_scene):
    """The claim the defaults rest on: an install that never opens the new UI
    sends the bytes it always sent."""
    cid, sid = group_scene
    before = store.context.build_messages(cid, sid)

    # a layout is stored but not enabled; the speaker layer is off
    store.context.layout.write_layout(
        [{"id": "response_budget"}, {"id": "world_info", "enabled": False}])

    assert store.context.build_messages(cid, sid) == before
```

`build_messages` expands `{{random}}`/`{{roll}}` at render time, so if the
fixture's content uses those macros this comparison is flaky — check the fixture
and, if it does, assert against a scene whose text has none.

- [ ] **Step 2: Run it**

Run: `make check-py`
Expected: PASS. A failure here means a default is not actually inert.

- [ ] **Step 3: Run the whole gate, including the Android dependency set**

Run: `make check`
Expected: PASS — `check-pydantic1` in particular, since Task 7 added a nested
`BaseModel`.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "Assert the claim the defaults rest on"
```

---

### Task 9: Review to convergence

- [ ] **Step 1: Run `/brutal-review` against the full diff**
- [ ] **Step 2: Fix what it finds, or write down why not**
- [ ] **Step 3: Re-run. Repeat until a pass surfaces nothing new.**
- [ ] **Step 4: `make check`**
- [ ] **Step 5: Update `TODO.md` §Backend and issue #29's acceptance criteria**
- [ ] **Step 6: Push and open the PR**, saying plainly that CLAUDE.md's Codex
      gates could not run in this environment and `/brutal-review` stood in.

## Self-Review

**Spec coverage.** Relabel-is-presentation → Task 7 asserts the caption copy.
Tier not editable → enforced structurally (`merge` only ever `_replace`s
`label`); no task exposes it. Insert-at-neighbour → Task 2. Both toggles off,
byte-identical → Task 8. Derived-not-stored → Task 5 (no store import in
`speaker.py`). Nomination order → Task 5. `id` on breakdown rows → Tasks 1 and 7.
Defensive reads → Task 3. Review gates → Task 9.

**Type consistency.** `nominate` returns the same five keys in Task 5's tests,
Task 6's template and Task 6's assertions. `merge(catalog, stored)` and
`describe(catalog, stored)` take the same two arguments in Tasks 2, 3 and 7.
`Section.id` is added in Task 1 and consumed in Tasks 2, 4, 6, 7.

**Placeholder scan:** clean — every code step carries the code, and the two
places that say "follow what the fixture already does" name the fixture's file.
