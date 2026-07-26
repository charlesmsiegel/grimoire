# Response Presets, Part 1: Budget + Drift Counterweight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every scene turn a resolved length budget, and push back on reply-length drift with a measured corrective injected right before generation.

**Architecture:** Two new store modules (`lengths.py` — four built-in numeric presets; `response_presets.py` — saveable records pairing a style with a length, plus a per-field resolution cascade over turn → scene → campaign → global) feed a new system-prompt section. Two transcript-integrity changes (a reserved speaker for synthetic messages, and a persisted per-generation turn boundary) make the transcript measurable; `length_drift.py` then measures the last three turns and renders a corrective into the post-history system message when replies have actually drifted over budget.

**Tech Stack:** Python 3 / FastAPI backend, Jinja2 templates (`StrictUndefined`), pytest. No new dependencies.

**Source spec:** `docs/superpowers/specs/2026-07-26-response-presets-design.md`

**Scope:** This plan covers the spec's Build Order stages 1 and 2 only — the plumbing and the counterweight. Together they deliver working drift control using default budgets, with no UI. Stages 3–5 (scope settings + `/response` API, preset management view, composer turn-override chip) are a separate plan; nothing here blocks on them.

## Global Constraints

- **Privacy — invented names only.** This repo is public and the data store is private. Never use a real world/campaign/character name in a test fixture, template, doc, or commit message. Reuse existing placeholders: Seraphine, Mara, Winifred, Realm, Saltmarch.
- **pydantic v1/v2-agnostic.** Plain `BaseModel` fields only. No `model_dump()`, `Field`, validators, or `ConfigDict`. Dump via `routes._dump`.
- **Android-safe filesystem access.** Built-in data files resolve through `prompts.templates_dir()`; user data through `store.paths.home()`. Never assume a repo checkout layout.
- **No new dependencies.** Base `pyproject.toml` deps must stay Android-installable.
- **Jinja runs with `StrictUndefined`.** Every variable a template references must always be present. A missing var is a hard render failure mid-scene, so `resolve()` must always return a complete dict.
- **Every new store module must be registered in `backend/src/grimoire/store/__init__.py`** — add it to the `from . import (...)` tuple *and* to `__all__`. The package does not auto-discover modules, so `from grimoire.store import <new>` raises `ImportError` until you do. This applies to all three modules created by this plan.
- **Backend test command:** `backend/.venv/Scripts/python.exe -m pytest backend -q`
- **Backend store tests isolate via** `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`; tests touching built-in data files also set `GRIMOIRE_TEMPLATES`.

---

## File Structure

**Create:**
- `backend/src/grimoire/store/lengths.py` — the four built-in length presets and knob coercion. Pure constants + helpers, no I/O.
- `backend/src/grimoire/store/response_presets.py` — response-preset records (built-in + user), what a record *supplies*, and the per-field `resolve()` cascade.
- `backend/src/grimoire/store/length_drift.py` — pure measurement over a transcript. No I/O.
- `templates/response_presets/{terse,brisk,standard,cinematic}.md` — the four shipped built-ins.
- `templates/scene/sections/response_budget.j2` — the static budget system-prompt section.
- `templates/scene/length_correction.j2` — the adaptive corrective.
- `backend/tests/test_lengths.py`, `test_response_presets.py`, `test_length_drift.py`

**Modify:**
- `backend/src/grimoire/store/scenes.py` — add `TRANSITION_SPEAKER`, `append_reply()`, `turn_sizes` bookkeeping.
- `backend/src/grimoire/store/appearances.py:208,237` — tag join/leave messages.
- `backend/src/grimoire/store/playing.py:131` — greeting goes through `append_reply`.
- `backend/src/grimoire/routes.py:2110` — `_persist_reply` goes through `append_reply`.
- `backend/src/grimoire/store/styles.py` — add public `exists()`.
- `backend/src/grimoire/store/context.py` — resolve the budget, measure drift, feed both into the prompt.
- `templates/scene/system.j2`, `templates/scene/post_history.j2`
- `backend/tests/test_scene_store.py`, `test_context.py`

---

## Task 1: Length presets

**Files:**
- Create: `backend/src/grimoire/store/lengths.py`
- Test: `backend/tests/test_lengths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `KNOBS: tuple[str, ...]`, `PRESETS: dict[str, dict[str, int]]`, `DEFAULT: str`, `get(preset_id: str) -> dict[str, int] | None`, `coerce(value) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_lengths.py`:

```python
from grimoire.store import lengths


def test_four_presets_with_all_knobs():
    assert set(lengths.PRESETS) == {"terse", "brisk", "standard", "cinematic"}
    for name, preset in lengths.PRESETS.items():
        assert set(preset) == set(lengths.KNOBS), name
        assert all(isinstance(v, int) and v > 0 for v in preset.values()), name


def test_blocks_leave_room_for_narration():
    # narration is a block but is not a speaker, so every preset must allow
    # at least one block beyond its speaker cap
    for name, preset in lengths.PRESETS.items():
        assert preset["blocks"] > preset["speakers"], name


def test_presets_increase_monotonically():
    order = ["terse", "brisk", "standard", "cinematic"]
    for knob in lengths.KNOBS:
        values = [lengths.PRESETS[p][knob] for p in order]
        assert values == sorted(values), knob


def test_get_returns_a_copy():
    got = lengths.get("terse")
    got["reply_words"] = 99999
    assert lengths.PRESETS["terse"]["reply_words"] != 99999


def test_get_unknown_is_none():
    assert lengths.get("nonesuch") is None
    assert lengths.get("") is None


def test_default_is_standard():
    assert lengths.DEFAULT == "standard"
    assert lengths.get(lengths.DEFAULT) is not None


def test_coerce_accepts_positive_ints_only():
    assert lengths.coerce("300") == 300
    assert lengths.coerce(300) == 300
    assert lengths.coerce("0") is None
    assert lengths.coerce("-5") is None
    assert lengths.coerce("many") is None
    assert lengths.coerce("") is None
    assert lengths.coerce(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_lengths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.lengths'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/lengths.py`:

```python
"""The built-in length vocabulary: four named bundles of the five response knobs.

Constants, not files — these are the primitive presets a response preset names,
and tuning happens by overriding individual knobs at a scope rather than by
editing a preset. See docs/superpowers/specs/2026-07-26-response-presets-design.md.
"""

from __future__ import annotations

# reply_words         target TOTAL words in one reply, narration included
# blocks              max blocks in one reply, narration included
# paragraphs          max paragraphs in any single block
# speakers            max distinct speaking characters (narration excluded)
# blocks_per_speaker  max blocks any one character may take (1 == no repeats)
KNOBS = ("reply_words", "blocks", "paragraphs", "speakers", "blocks_per_speaker")

# `blocks` is deliberately > `speakers` in every preset: narration occupies a
# block but is not a speaker, so it always needs room.
PRESETS: dict[str, dict[str, int]] = {
    "terse":     {"reply_words": 150, "blocks": 3, "paragraphs": 1,
                  "speakers": 2, "blocks_per_speaker": 1},
    "brisk":     {"reply_words": 300, "blocks": 4, "paragraphs": 2,
                  "speakers": 3, "blocks_per_speaker": 1},
    "standard":  {"reply_words": 550, "blocks": 5, "paragraphs": 2,
                  "speakers": 4, "blocks_per_speaker": 2},
    "cinematic": {"reply_words": 900, "blocks": 7, "paragraphs": 3,
                  "speakers": 5, "blocks_per_speaker": 2},
}

DEFAULT = "standard"


def get(preset_id: str) -> dict[str, int] | None:
    """The knob values for `preset_id`, or None if it names nothing. Callers get
    a fresh dict — resolution mutates its working copy."""
    preset = PRESETS.get(preset_id)
    return dict(preset) if preset else None


def coerce(value) -> int | None:
    """A knob value as a positive int, or None if it isn't one. Frontmatter is
    all strings, and a malformed knob must degrade to 'unset' rather than raise
    mid-scene."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_lengths.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/lengths.py backend/tests/test_lengths.py
git commit -m "feat(lengths): built-in length presets and knob coercion"
```

---

## Task 2: Response preset records

**Files:**
- Create: `backend/src/grimoire/store/response_presets.py`
- Create: `templates/response_presets/terse.md`, `brisk.md`, `standard.md`, `cinematic.md`
- Test: `backend/tests/test_response_presets.py`

**Interfaces:**
- Consumes: `lengths.KNOBS`, `lengths.get`, `lengths.coerce` (Task 1).
- Produces: `PresetNotFound`, `list_presets() -> list[dict]`, `read_preset(pid) -> dict`, `is_built_in(pid) -> bool`, `supplies(meta) -> dict | None`. `supplies` returns the fields a record specifies (keys drawn from `lengths.KNOBS` plus `"style_id"`), or `None` when the record is invalid. (Preset *writing* — create/update/delete and the built-in-immutable error — belongs to stage 3's CRUD routes and is deliberately not built here.)

The one rule this task implements: **a preset supplies exactly the fields it specifies.** An unspecified field is not defaulted — the preset has no opinion and resolution (Task 3) walks past it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_response_presets.py`:

```python
import pytest

from grimoire.store import response_presets as rp


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path / "templates"))


def _write(dir_path, pid, **fields):
    dir_path.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    (dir_path / f"{pid}.md").write_text(f"---\n{body}---\n", encoding="utf-8")


def test_named_form_supplies_all_five_knobs(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "slow-burn",
           name="Slow Burn", style_id="gothic-horror", length_preset="cinematic")
    supplied = rp.supplies(rp.read_preset("slow-burn")["meta"])
    assert supplied["style_id"] == "gothic-horror"
    assert supplied["reply_words"] == 900
    assert supplied["blocks_per_speaker"] == 2


def test_explicit_form_supplies_only_the_knobs_present(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "clipped",
           name="Clipped", reply_words="220", speakers="2")
    supplied = rp.supplies(rp.read_preset("clipped")["meta"])
    assert supplied == {"reply_words": 220, "speakers": 2}
    # the unnamed knobs are NOT defaulted — they keep resolving outward
    assert "blocks" not in supplied


def test_neither_form_is_a_valid_style_only_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "just-gothic",
           name="Just Gothic", style_id="gothic-horror")
    supplied = rp.supplies(rp.read_preset("just-gothic")["meta"])
    assert supplied == {"style_id": "gothic-horror"}


def test_unknown_length_preset_invalidates_the_whole_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", style_id="gothic-horror",
           length_preset="nonesuch", reply_words="220")
    # invalid: supplies NOTHING, not even its style, and the ignored explicit
    # value must never spring to life because the name was mistyped
    assert rp.supplies(rp.read_preset("broken")["meta"]) is None


def test_named_form_ignores_explicit_keys_entirely(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "both",
           name="Both", length_preset="terse", reply_words="9999")
    assert rp.supplies(rp.read_preset("both")["meta"])["reply_words"] == 150


def test_malformed_knob_is_absent_not_defaulted(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "sloppy",
           name="Sloppy", reply_words="lots", speakers="3")
    supplied = rp.supplies(rp.read_preset("sloppy")["meta"])
    assert supplied == {"speakers": 3}


def test_style_none_sentinel_clears(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "bare",
           name="Bare", style_id="none", length_preset="terse")
    assert rp.supplies(rp.read_preset("bare")["meta"])["style_id"] == ""


def test_empty_style_is_no_opinion(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "home" / "response_presets", "lengthy",
           name="Lengthy", style_id="", length_preset="terse")
    assert "style_id" not in rp.supplies(rp.read_preset("lengthy")["meta"])


def test_list_merges_builtin_and_custom(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "brisk",
           name="Brisk", length_preset="brisk")
    _write(tmp_path / "home" / "response_presets", "mine", name="Mine")
    items = {p["id"]: p for p in rp.list_presets()}
    assert items["brisk"]["built_in"] is True
    assert items["mine"]["built_in"] is False


def test_read_missing_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(rp.PresetNotFound):
        rp.read_preset("nonesuch")


def test_shipped_builtins_are_length_only(monkeypatch, tmp_path):
    # against the REAL templates/ dir: the four shipped presets must not
    # disturb styles, so none of them may specify style_id
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    ids = {p["id"] for p in rp.list_presets() if p["built_in"]}
    assert ids == {"terse", "brisk", "standard", "cinematic"}
    for pid in ids:
        supplied = rp.supplies(rp.read_preset(pid)["meta"])
        assert "style_id" not in supplied, pid
        assert supplied["reply_words"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.response_presets'`

- [ ] **Step 3: Create the four built-in preset files**

`templates/response_presets/terse.md`:

```
---
name: Terse
description: Quick exchanges. Two voices, one short block each.
length_preset: terse
---
```

`templates/response_presets/brisk.md`:

```
---
name: Brisk
description: Snappy back-and-forth without feeling clipped.
length_preset: brisk
---
```

`templates/response_presets/standard.md`:

```
---
name: Standard
description: The default shape — room for a scene beat and a few voices.
length_preset: standard
---
```

`templates/response_presets/cinematic.md`:

```
---
name: Cinematic
description: Long, unhurried set pieces with a full cast.
length_preset: cinematic
---
```

- [ ] **Step 4: Write the implementation**

Create `backend/src/grimoire/store/response_presets.py`:

```python
"""Response presets: saveable records pairing a prose style with a length budget.

Built-ins ship under templates/response_presets/ (resolved via
prompts.templates_dir(), so the Android build's GRIMOIRE_TEMPLATES indirection
works unchanged); user-authored ones live in <GRIMOIRE_HOME>/response_presets/
and are the only editable kind. Mirrors the split in store/styles.py.

The governing rule, which every function here serves: A PRESET SUPPLIES EXACTLY
THE FIELDS IT SPECIFIES. A field it does not specify is not defaulted — the
preset has no opinion and resolution walks past it to the next scope. Defaulting
an unspecified field is what makes a length choice silently clobber a style.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from . import lengths
from .frontmatter import parse_frontmatter
from .paths import home, natural_key

_STYLE_CLEAR = "none"


class PresetNotFound(Exception):
    pass


def _safe(pid: str) -> bool:
    return pid not in ("", ".", "..") and "/" not in pid and "\\" not in pid


def _builtin_dir() -> Path:
    return prompts.templates_dir() / "response_presets"


def _custom_dir() -> Path:
    return home() / "response_presets"


def _find_path(pid: str) -> tuple[Path, bool] | None:
    if not _safe(pid):
        return None
    p = _custom_dir() / f"{pid}.md"
    if p.exists():
        return p, False
    p = _builtin_dir() / f"{pid}.md"
    if p.exists():
        return p, True
    return None


def _meta_dict(pid: str, meta: dict, built_in: bool) -> dict:
    return {"id": pid, "name": meta.get("name", pid),
            "description": meta.get("description", ""),
            "style_id": meta.get("style_id", ""),
            "length_preset": meta.get("length_preset", ""),
            **{k: meta.get(k, "") for k in lengths.KNOBS},
            "built_in": built_in}


def _list_dir(directory: Path, built_in: bool) -> list[dict]:
    out: list[dict] = []
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.md")):
        try:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue  # a broken file is skipped, not fatal — as in styles.py
        out.append(_meta_dict(p.stem, meta, built_in))
    return out


def list_presets() -> list[dict]:
    items = _list_dir(_builtin_dir(), built_in=True) + _list_dir(_custom_dir(), built_in=False)
    items.sort(key=lambda m: natural_key(m["name"]))
    return items


def is_built_in(pid: str) -> bool:
    found = _find_path(pid)
    return found is not None and found[1]


def read_preset(pid: str) -> dict:
    found = _find_path(pid)
    if found is None:
        raise PresetNotFound(pid)
    p, built_in = found
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(pid, meta, built_in)}


def supplies(meta: dict) -> dict | None:
    """The fields this record specifies, or None if the record is invalid.

    Keys are drawn from lengths.KNOBS plus "style_id". A key's ABSENCE means
    "no opinion", which is materially different from a falsy value: a supplied
    style_id of "" is an explicit clear (the `none` sentinel).
    """
    named = (meta.get("length_preset") or "").strip()
    out: dict = {}

    if named:
        knobs = lengths.get(named)
        if knobs is None:
            return None  # invalid record: supplies nothing, not even its style
        out.update(knobs)  # named form ignores explicit keys unconditionally
    else:
        for knob in lengths.KNOBS:
            value = lengths.coerce(meta.get(knob, ""))
            if value is not None:
                out[knob] = value

    style = (meta.get("style_id") or "").strip()
    if style == _STYLE_CLEAR:
        out["style_id"] = ""
    elif style:
        out["style_id"] = style
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q`
Expected: PASS — 11 passed

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/response_presets.py backend/tests/test_response_presets.py templates/response_presets/
git commit -m "feat(response-presets): preset records and supplies() semantics"
```

---

## Task 3: The per-field resolution cascade

**Files:**
- Modify: `backend/src/grimoire/store/response_presets.py`
- Modify: `backend/src/grimoire/store/styles.py` (add `exists()`)
- Test: `backend/tests/test_response_presets.py`

**Interfaces:**
- Consumes: `supplies()`, `read_preset()` (Task 2); `lengths.PRESETS`, `lengths.coerce`, `lengths.DEFAULT` (Task 1).
- Produces: `resolve(*, turn=None, scene_meta=None, campaign_meta=None, config=None) -> dict` returning every key in `lengths.KNOBS` plus `"style_id"` plus `"provenance"`. `styles.exists(sid) -> bool`.

Each of the six fields resolves **independently**. For one field, walk turn → scene → campaign → global and take the first value found; within a scope, a loose override beats that scope's own preset. This is what stops a length choice from wiping a style, in every cascade ordering.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_response_presets.py`:

```python
def _scope(preset="", style="", **knobs):
    meta = {}
    if preset:
        meta["response_preset"] = preset
    if style:
        meta["style_id"] = style
    for k, v in knobs.items():
        meta[f"length_{k}"] = str(v)
    return meta


def test_no_settings_anywhere_falls_back_to_standard(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve()
    assert got["reply_words"] == 550
    assert got["style_id"] == ""
    assert got["provenance"]["reply_words"]["scope"] == "default"


def test_narrower_preset_wins_for_length(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     campaign_meta=_scope(preset="cinematic"))
    assert got["reply_words"] == 150
    assert got["provenance"]["reply_words"]["scope"] == "scene"


def test_length_only_preset_does_not_wipe_a_broader_LOOSE_style(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 150


def test_length_only_preset_does_not_wipe_a_broader_PRESET_style(tmp_path, monkeypatch):
    """The case an earlier draft of the design got wrong."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "home" / "response_presets", "slow-burn",
           name="Slow Burn", style_id="gothic-horror", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="terse"),
                     config=_scope(preset="slow-burn"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 150


def test_global_default_style_id_spelling(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    got = rp.resolve(config={"default_style_id": "gothic-horror"})
    assert got["style_id"] == "gothic-horror"


def test_loose_override_beats_its_own_scopes_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(campaign_meta=_scope(preset="cinematic", speakers=3))
    assert got["speakers"] == 3
    assert got["reply_words"] == 900


def test_stale_broad_override_cannot_haunt_a_narrow_preset(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="cinematic"),
                     config=_scope(reply_words=90))
    assert got["reply_words"] == 900


def test_style_only_preset_leaves_length_resolving_outward(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "home" / "response_presets", "just-gothic",
           name="Just Gothic", style_id="gothic-horror")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(scene_meta=_scope(preset="just-gothic"),
                     campaign_meta=_scope(preset="cinematic"))
    assert got["style_id"] == "gothic-horror"
    assert got["reply_words"] == 900


def test_none_sentinel_clears_a_broader_style(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    _write(tmp_path / "home" / "response_presets", "bare",
           name="Bare", style_id="none", length_preset="terse")
    got = rp.resolve(scene_meta=_scope(preset="bare"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == ""


def test_unknown_style_id_continues_outward(tmp_path, monkeypatch):
    """styles.resolve_style skips unresolvable ids so a stale reference never
    breaks generation — the new cascade must not regress that."""
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "styles", "gothic-horror", name="Gothic Horror")
    got = rp.resolve(scene_meta=_scope(style="deleted-style"),
                     campaign_meta=_scope(style="gothic-horror"))
    assert got["style_id"] == "gothic-horror"


def test_missing_or_invalid_preset_is_skipped(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    _write(tmp_path / "home" / "response_presets", "broken",
           name="Broken", length_preset="nonesuch")
    assert rp.resolve(scene_meta=_scope(preset="ghost"),
                      campaign_meta=_scope(preset="cinematic"))["reply_words"] == 900
    assert rp.resolve(scene_meta=_scope(preset="broken"),
                      campaign_meta=_scope(preset="cinematic"))["reply_words"] == 900


def test_turn_scope_is_narrowest(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write(tmp_path / "templates" / "response_presets", "terse",
           name="Terse", length_preset="terse")
    _write(tmp_path / "templates" / "response_presets", "cinematic",
           name="Cinematic", length_preset="cinematic")
    got = rp.resolve(turn=_scope(preset="terse"), scene_meta=_scope(preset="cinematic"))
    assert got["reply_words"] == 150


def test_malformed_scope_override_is_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve(scene_meta={"length_reply_words": "-4"})
    assert got["reply_words"] == 550


def test_result_is_always_complete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    got = rp.resolve(scene_meta=_scope(preset="ghost"))
    for knob in rp.lengths.KNOBS:
        assert isinstance(got[knob], int) and got[knob] > 0
    assert isinstance(got["style_id"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.response_presets' has no attribute 'resolve'`

- [ ] **Step 3: Add `styles.exists()`**

In `backend/src/grimoire/store/styles.py`, directly after `is_built_in`:

```python
def exists(sid: str) -> bool:
    """Whether `sid` names a readable style. Resolution treats an id that
    doesn't as 'no opinion' and keeps walking outward, matching
    resolve_style's long-standing skip-and-fall-back behaviour."""
    return _find_path(sid) is not None
```

- [ ] **Step 4: Write the resolution implementation**

Append to `backend/src/grimoire/store/response_presets.py`:

```python
# Scope names, narrowest first — the order every field is walked in.
_SCOPES = ("turn", "scene", "campaign", "global")

# The loose-override frontmatter key for a field at a given scope. style_id is
# spelled `default_style_id` globally and keeps that spelling: renaming it would
# break every existing install's global style for no functional gain.
def _override_key(field: str, scope: str) -> str:
    if field == "style_id":
        return "default_style_id" if scope == "global" else "style_id"
    return f"length_{field}"


def _supplied_by_preset(meta: dict) -> dict:
    """What the preset named by this scope supplies. A scope naming a missing
    or invalid preset supplies nothing and the walk continues."""
    pid = (meta.get("response_preset") or "").strip()
    if not pid:
        return {}
    try:
        record = read_preset(pid)
    except PresetNotFound:
        return {}
    return supplies(record["meta"]) or {}


def _override(meta: dict, field: str, scope: str):
    raw = (meta.get(_override_key(field, scope)) or "").strip()
    if not raw:
        return None
    if field == "style_id":
        return "" if raw == _STYLE_CLEAR else raw
    return lengths.coerce(raw)


def resolve(*, turn: dict | None = None, scene_meta: dict | None = None,
            campaign_meta: dict | None = None, config: dict | None = None) -> dict:
    """The per-field cascade over turn -> scene -> campaign -> global.

    EACH FIELD RESOLVES INDEPENDENTLY. For one field, walk the scopes and take
    the first value found; within a scope, a loose override beats that scope's
    own preset. There is no single "base preset" — that formulation is what
    made a narrower length-only preset wipe a broader style.
    """
    from . import styles  # local: styles imports nothing from here, but keep it lazy

    scoped = {"turn": turn or {}, "scene": scene_meta or {},
              "campaign": campaign_meta or {}, "global": config or {}}
    presets = {name: _supplied_by_preset(meta) for name, meta in scoped.items()}

    out: dict = {}
    provenance: dict = {}
    for field in ("style_id",) + lengths.KNOBS:
        for scope in _SCOPES:
            meta = scoped[scope]
            for source, value in (("override", _override(meta, field, scope)),
                                  ("preset", presets[scope].get(field))):
                if value is None:
                    continue
                # An id naming a style that doesn't exist is no opinion, not a
                # clear — the walk continues. "" IS a clear and stops the walk.
                if field == "style_id" and value and not styles.exists(value):
                    continue
                out[field] = value
                provenance[field] = {"scope": scope, "source": source}
                break
            if field in out:
                break
        if field not in out:
            out[field] = "" if field == "style_id" else lengths.PRESETS[lengths.DEFAULT][field]
            provenance[field] = {"scope": "default", "source": "default"}

    out["provenance"] = provenance
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_response_presets.py -q`
Expected: PASS — 25 passed

- [ ] **Step 6: Run the full backend suite to check nothing regressed**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — no failures

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/response_presets.py backend/src/grimoire/store/styles.py backend/tests/test_response_presets.py
git commit -m "feat(response-presets): per-field resolution cascade"
```

---

## Task 4: The static budget prompt section

**Files:**
- Create: `templates/scene/sections/response_budget.j2`
- Modify: `templates/scene/system.j2`
- Modify: `backend/src/grimoire/store/context.py` (`_assemble`, `_SECTIONS`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `response_presets.resolve` (Task 3).
- Produces: template var `budget` (a dict with the five knobs) present in `_assemble`'s data for every scene turn. `_assemble`'s returned `data` also carries `style_id` resolution through the new cascade instead of `styles.resolve_style`.

This task also switches style resolution onto the new cascade. The migration is designed to be a no-op, so the test asserts exactly that.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`. It already has a `_campaign(monkeypatch, tmp_path)` helper returning `(cid, sid)` and already imports `campaigns`, `entities`, and `scenes` — use them rather than adding new ones.

**Do not write style fixtures into `prompts.templates_dir()`.** `_campaign` sets only `GRIMOIRE_HOME`, so the templates dir is the *real repo* `templates/` and a test writing there would pollute the working tree. User styles live under `GRIMOIRE_HOME/styles`, which is `tmp_path` and safely isolated.

```python
def _user_style(tmp_path, sid, name, body):
    d = tmp_path / "styles"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.md").write_text(f"---\nname: {name}\n---\n\n{body}", encoding="utf-8")


def test_budget_section_renders_with_resolved_numbers(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    text = context.build_messages(cid, sid)[0]["content"]
    assert "# Response budget" in text
    assert "550 words" in text                         # standard fallback
    assert "at most 5 blocks" in text


def test_budget_follows_the_scene_override(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "terse")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "150 words" in text
    assert "do not return to a character you have already written" in text


def test_repeats_allowed_wording(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "cinematic")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "No character takes more than 2 blocks." in text


def test_legacy_style_id_still_resolves_identically(monkeypatch, tmp_path):
    """Migration is a no-op: a store with only the legacy style_id keys must
    resolve the same style it does today, now through the new cascade."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _user_style(tmp_path, "gothic-horror", "Gothic Horror", "Atmosphere first.")
    campaigns.set_campaign_style(cid, "gothic-horror")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Atmosphere first." in text


def test_stale_scene_style_falls_back_to_campaign(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    _user_style(tmp_path, "gothic-horror", "Gothic Horror", "Atmosphere first.")
    campaigns.set_campaign_style(cid, "gothic-horror")
    scenes.set_style(cid, sid, "deleted-style")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Atmosphere first." in text


def test_budget_section_appears_in_the_token_breakdown(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    labels = [s["label"] for s in context.context_sections(cid, sid)]
    assert "Response budget" in labels
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k budget`
Expected: FAIL — `AttributeError: module 'grimoire.store.scenes' has no attribute 'set_response_preset'`

- [ ] **Step 3: Add the scene-scope setter**

In `backend/src/grimoire/store/scenes.py`, directly after `set_style`:

```python
def set_response_preset(cid: str, sid: str, preset_id: str) -> None:
    """The scene-scope response preset. Loose per-knob overrides use the
    `length_*` frontmatter keys and are written by the same routes."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["response_preset"] = preset_id
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 4: Create the budget section template**

Create `templates/scene/sections/response_budget.j2`:

```
{#- The resolved length budget, rendered right after the reply-format rule.
    Word counts are TARGETS ("about N") — a hard cap makes models truncate
    mid-thought, where a target makes them compose shorter. The structural
    limits are caps, because they are.
    Vars: budget ({reply_words, blocks, paragraphs, speakers,
                   blocks_per_speaker}). -#}
# Response budget
This whole reply runs about {{ budget.reply_words }} words across at most {{ budget.blocks }} blocks, narration (**Grimoire:**) included — roughly {{ budget.reply_words // budget.blocks }} words per block. No block exceeds {{ budget.paragraphs }} paragraph{{ "" if budget.paragraphs == 1 else "s" }}.
At most {{ budget.speakers }} characters act or speak. {% if budget.blocks_per_speaker == 1 %}Give each exactly one block — do not return to a character you have already written.{% else %}No character takes more than {{ budget.blocks_per_speaker }} blocks.{% endif %}
```

- [ ] **Step 5: Include the section in the system message**

In `templates/scene/system.j2`, immediately **after** the `response_format.j2` block, add:

```
{%- set s -%}{%- include "scene/sections/response_budget.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

- [ ] **Step 6: Wire resolution into `_assemble`**

In `backend/src/grimoire/store/context.py`, add `response_presets` to the store imports. Then replace the `resolved_style = styles.resolve_style(...)` call with:

```python
    budget = response_presets.resolve(scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except styles.StyleNotFound:
        resolved_style = None  # belt and braces: resolve() already skips unknown ids
```

and add to the `data` dict:

```python
        "budget": {k: budget[k] for k in lengths.KNOBS},
```

(import `lengths` alongside `response_presets`).

Then register the section in `_SECTIONS`, immediately after the `("Response format", ...)` entry:

```python
    ("Response budget", "scene/sections/response_budget.j2", False),
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS

- [ ] **Step 8: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — no failures

- [ ] **Step 9: Commit**

```bash
git add templates/scene/sections/response_budget.j2 templates/scene/system.j2 backend/src/grimoire/store/context.py backend/src/grimoire/store/scenes.py backend/tests/test_context.py
git commit -m "feat(context): render the resolved response budget into the system prompt"
```

**Stage 1 is complete here.** Every scene turn now carries a budget, and styles resolve through the new cascade with identical results.

---

## Task 5: Tag synthetic messages with a reserved speaker

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (add `TRANSITION_SPEAKER`, use it in `set_location`, `set_datetime`)
- Modify: `backend/src/grimoire/store/appearances.py:208,237`
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Produces: `scenes.TRANSITION_SPEAKER: str`, and all four synthetic-message sites tagging with it.

Grimoire appends assistant-role messages that no model wrote. Untagged, they inflate a turn's block and word counts, and one sitting between two replies merges them into a single run — a budget-compliant reply followed by a location change would measure as an over-cap turn and fire a false correction.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scene_store.py`:

```python
def test_transition_messages_carry_the_reserved_speaker(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Moves")
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Wet rope and tar.")
    entities.create_entity(croot, "locations", "The Long Stair", "Down and down.")
    scenes.set_location(cid, sid, "saltmarch-docks")     # first is silent
    scenes.set_location(cid, sid, "the-long-stair")      # this one appends
    messages = scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["speaker"] == scenes.TRANSITION_SPEAKER
    assert "The Long Stair" in messages[-1]["content"]


def test_transition_speaker_cannot_collide_with_a_real_name():
    # U+2063-prefixed, exactly like ROLL_SPEAKER, so a character actually
    # called "Scene" round-trips as plain "Scene"
    assert scenes.TRANSITION_SPEAKER.startswith("⁣")
    assert scenes.TRANSITION_SPEAKER != scenes.ROLL_SPEAKER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k transition`
Expected: FAIL — `AttributeError: module 'grimoire.store.scenes' has no attribute 'TRANSITION_SPEAKER'`

- [ ] **Step 3: Add the constant**

In `backend/src/grimoire/store/scenes.py`, directly after the `ROLL_SPEAKER` definition:

```python
# Scene transitions (location change, time advance, cast join/leave) are
# appended as assistant-role messages so they render inline, but no model wrote
# them. Tagged with this speaker so drift measurement can treat them as turn
# SEPARATORS rather than counting them as model prose — untagged, a transition
# between two replies merges them into one apparently-oversized turn. Same
# U+2063 prefix as ROLL_SPEAKER, for the same anti-collision reason.
TRANSITION_SPEAKER = "⁣Scene"
```

- [ ] **Step 4: Tag the four call sites**

In `backend/src/grimoire/store/scenes.py`, `set_location`:

```python
        append_message(cid, sid, "assistant", f"*The scene moves to {name}.*",
                       speaker=TRANSITION_SPEAKER)
```

In `backend/src/grimoire/store/scenes.py`, `set_datetime`:

```python
        append_message(cid, sid, "assistant", f"*Time passes. It is now {friendly}.*",
                       speaker=TRANSITION_SPEAKER)
```

In `backend/src/grimoire/store/appearances.py` (line ~208 and ~237):

```python
        scenes.append_message(cid, scene_id, "assistant", f"*{name} joins the scene.*",
                              speaker=scenes.TRANSITION_SPEAKER)
```

```python
        scenes.append_message(cid, scene_id, "assistant", f"*{name} leaves the scene.*",
                              speaker=scenes.TRANSITION_SPEAKER)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py backend/tests/test_appearances_store.py -q`
Expected: PASS

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. If any test asserts on the old untagged transition text, update it to expect the speaker — the rendered content is unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/store/appearances.py backend/tests/test_scene_store.py
git commit -m "feat(scenes): tag synthetic transition messages with a reserved speaker"
```

---

## Task 6: Persist per-generation turn boundaries

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (`append_reply`, `turn_sizes` bookkeeping, `remove_trailing_assistant_run`)
- Modify: `backend/src/grimoire/routes.py:2110` (`_persist_reply`)
- Modify: `backend/src/grimoire/store/playing.py:131`
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Produces: `scenes.append_reply(cid, sid, segments: list[dict]) -> None` and `scenes.get_turn_sizes(cid, sid) -> list[int]`. `segments` are `{"speaker": str | None, "content": str}` dicts, exactly `split_reply`'s output shape.

Turn boundaries **cannot be inferred from message role**: `post_chat` builds director notes and empty sends as ephemeral user messages that are never persisted, so consecutive director generations — the normal way an offscreen scene is played — leave no user message between them. Role-based segmentation would merge an entire offscreen scene into one turn.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scene_store.py`:

```python
def test_append_reply_records_a_turn_boundary(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Turns")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "The door opens."},
                                   {"speaker": "Mara", "content": "You're late."}])
    scenes.append_reply(cid, sid, [{"speaker": "Winifred", "content": "I walked."}])
    assert scenes.get_turn_sizes(cid, sid) == [2, 1]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 3


def test_consecutive_generations_stay_separate_without_user_messages(monkeypatch, tmp_path):
    """Offscreen/director play persists no user turn between generations."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Offscreen")
    for _ in range(3):
        scenes.append_reply(cid, sid, [{"speaker": None, "content": "Time grinds on."}])
    assert scenes.get_turn_sizes(cid, sid) == [1, 1, 1]


def test_turn_sizes_survive_a_message_edit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Edits")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Short."}])
    scenes.edit_message(cid, sid, 0, "Much, much longer now.")
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_remove_trailing_assistant_run_pops_the_boundary(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Reroll")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "First try."},
                                   {"speaker": None, "content": "She waits."}])
    scenes.remove_trailing_assistant_run(cid, sid)
    assert scenes.get_turn_sizes(cid, sid) == []


def test_no_turn_sizes_on_a_legacy_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Legacy")
    scenes.append_message(cid, sid, "assistant", "Written before turn tracking.")
    assert scenes.get_turn_sizes(cid, sid) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k turn`
Expected: FAIL — `AttributeError: module 'grimoire.store.scenes' has no attribute 'append_reply'`

- [ ] **Step 3: Implement `append_reply` and the accessors**

In `backend/src/grimoire/store/scenes.py`, after `append_message`:

```python
def get_turn_sizes(cid: str, sid: str) -> list[int]:
    """Block counts for each model generation, oldest first. Empty on a scene
    written before turn tracking — such scenes are simply not measured, which
    is a far better failure than confident wrong numbers."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    raw = parse_frontmatter_head(p).get("turn_sizes", "")
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


def _write_turn_sizes(p: Path, sizes: list[int]) -> None:
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["turn_sizes"] = ",".join(str(n) for n in sizes)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def append_reply(cid: str, sid: str, segments: list[dict]) -> None:
    """Persist ONE model generation as per-speaker posts, recording its block
    count as a turn boundary.

    The single entry point for model output, because boundaries can't be
    recovered later: ephemeral director notes and empty sends are never
    persisted, so consecutive generations leave no user message between them.
    Counts rather than indices — a message EDIT leaves counts untouched, where
    indices would need rewriting on every edit.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    kept = [s for s in segments if s["content"].strip()]
    if not kept:
        return
    for seg in kept:
        append_message(cid, sid, "assistant", seg["content"], speaker=seg.get("speaker"))
    _write_turn_sizes(p, get_turn_sizes(cid, sid) + [len(kept)])
```

- [ ] **Step 4: Pop the boundary when a run is removed**

In `remove_trailing_assistant_run`, after the existing message-truncation write, add:

```python
    sizes = get_turn_sizes(cid, sid)
    if sizes:
        _write_turn_sizes(p, sizes[:-1])
```

- [ ] **Step 5: Route model output through `append_reply`**

In `backend/src/grimoire/routes.py`, `_persist_reply`:

```python
def _persist_reply(cid: str, sid: str, text: str) -> None:
    """Split one model reply into per-speaker posts and append them (#744).
    Macros are expanded before persisting (#137): {{roll}}/{{random}} must be
    resolved once, not re-rolled on every future context build that re-reads
    this now-historical message. Goes through append_reply so the generation
    records its own turn boundary for drift measurement."""
    players = frozenset(store.appearances.player_names(cid, sid))
    subs = store.context.scene_substitutions(cid, sid)
    segments = [{"speaker": seg["speaker"],
                 "content": store.context.expand_macros(seg["content"], subs, cid, sid)}
                for seg in store.scenes.split_reply(text, players)]
    store.scenes.append_reply(cid, sid, segments)
```

In `backend/src/grimoire/store/playing.py`, replace the greeting append:

```python
    scenes.append_reply(cid, sid, [{"speaker": None, "content": text}])
```

(The greeting is authored rather than generated, but it is the strongest length anchor the model has at the start of a scene and it *will* be matched — so it records a turn like any other.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py backend/tests/test_playing_store.py backend/tests/test_routes.py -q`
Expected: PASS

- [ ] **Step 7: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — no failures

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/store/playing.py backend/src/grimoire/routes.py backend/tests/test_scene_store.py
git commit -m "feat(scenes): record a persisted turn boundary per model generation"
```

---

## Task 7: Measure the transcript

**Files:**
- Create: `backend/src/grimoire/store/length_drift.py`
- Test: `backend/tests/test_length_drift.py`

**Interfaces:**
- Consumes: `scenes.ROLL_SPEAKER`, `scenes.TRANSITION_SPEAKER`, `scenes.match_name` (Tasks 5–6).
- Produces: `segment(messages, turn_sizes) -> list[list[dict]]`, `measure(messages, turn_sizes, cast_names, budget, window=3) -> dict | None`. `measure` returns `None` when there is nothing to measure; otherwise `{"totals": [int], "max_ratio": float, "tier": "" | "trim" | "cut", "blocks": bool, "paragraphs": bool, "speakers": bool, "blocks_per_speaker": bool}`.

Two rules this task encodes, both of which a naive implementation gets wrong:
- **Every signal is "any turn in the window violated"**, including the word signal, which uses the window **maximum**, not the mean. A mean oscillates: at a 100-word budget, 130/130/130 corrects at 1.30×, one compliant turn clears it at 1.20×, and the next 150-word turn re-triggers at 1.27×.
- **Speaker labels are not identities.** `split_reply` preserves whatever the model wrote, so `Winifred` and `Winifred Vance` would count as two speakers — inflating the speaker count into a false violation while letting the same character slip under `blocks_per_speaker`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_length_drift.py`:

```python
from grimoire.store import length_drift, scenes

BUDGET = {"reply_words": 100, "blocks": 3, "paragraphs": 2,
          "speakers": 2, "blocks_per_speaker": 1}
CAST = ["Winifred Vance", "Mara"]


def _msg(speaker, words, paragraphs=1):
    para = " ".join(["word"] * max(words // paragraphs, 1))
    return {"role": "assistant", "speaker": speaker,
            "content": "\n\n".join([para] * paragraphs)}


def _turn(*messages):
    return list(messages)


def test_returns_none_without_turn_sizes():
    msgs = [_msg("Mara", 500)]
    assert length_drift.measure(msgs, [], CAST, BUDGET) is None


def test_returns_none_when_sizes_do_not_match_the_transcript():
    # a hand-edited file: fail safe rather than measure garbage
    msgs = [_msg("Mara", 50)]
    assert length_drift.measure(msgs, [5], CAST, BUDGET) is None


def test_compliant_replies_produce_no_tier():
    msgs = [_msg("Mara", 40), _msg(None, 40)]
    got = length_drift.measure(msgs, [2], CAST, BUDGET)
    assert got["tier"] == ""
    assert got["blocks"] is False


def test_tiers_come_from_the_worst_turn_not_the_mean():
    msgs = [_msg("Mara", 130), _msg("Mara", 130), _msg("Mara", 100)]
    got = length_drift.measure(msgs, [1, 1, 1], CAST, BUDGET)
    assert got["tier"] == "trim"
    assert round(got["max_ratio"], 2) == 1.30


def test_no_oscillation_regression():
    """130/130/130 -> 130/130/100 -> 130/100/150 must stay ON throughout.
    Under a mean-driven signal the middle window clears at 1.20x."""
    for totals in ([130, 130, 130], [130, 130, 100], [130, 100, 150]):
        msgs = [_msg("Mara", n) for n in totals]
        got = length_drift.measure(msgs, [1, 1, 1], CAST, BUDGET)
        assert got["tier"] != "", totals


def test_clears_only_after_three_compliant_turns():
    msgs = [_msg("Mara", 300), _msg("Mara", 50), _msg("Mara", 50), _msg("Mara", 50)]
    got = length_drift.measure(msgs, [1, 1, 1, 1], CAST, BUDGET)
    assert got["tier"] == ""      # the 300 has rolled out of the 3-turn window


def test_cut_tier_above_175_percent():
    msgs = [_msg("Mara", 200)]
    assert length_drift.measure(msgs, [1], CAST, BUDGET)["tier"] == "cut"


def test_splitting_across_more_blocks_does_not_evade_the_budget():
    """The regression test for the per-block-budget loophole: six compliant
    blocks still bust a total-words budget."""
    msgs = [_msg("Mara", 40) for _ in range(6)]
    got = length_drift.measure(msgs, [6], CAST, BUDGET)
    assert got["tier"] == "cut"
    assert got["blocks"] is True


def test_speaker_aliases_count_as_one_character():
    msgs = [_msg("Winifred", 20), _msg("Winifred Vance", 20)]
    got = length_drift.measure(msgs, [2], CAST, BUDGET)
    assert got["speakers"] is False            # one character, cap is 2
    assert got["blocks_per_speaker"] is True   # ...who took two blocks, cap is 1


def test_unresolvable_label_counts_as_itself():
    msgs = [_msg("A Stranger", 20), _msg("Mara", 20), _msg("Winifred Vance", 20)]
    got = length_drift.measure(msgs, [3], CAST, BUDGET)
    assert got["speakers"] is True             # three distinct, cap is 2


def test_narration_is_not_a_speaker():
    msgs = [_msg(None, 20), _msg(None, 20), _msg("Mara", 20)]
    got = length_drift.measure(msgs, [3], CAST, BUDGET)
    assert got["speakers"] is False
    assert got["blocks"] is False              # 3 blocks, cap is 3


def test_paragraph_cap_uses_the_longest_block():
    msgs = [_msg("Mara", 30, paragraphs=3)]
    assert length_drift.measure(msgs, [1], CAST, BUDGET)["paragraphs"] is True


def test_reserved_speakers_are_separators_not_blocks():
    msgs = [_msg("Mara", 40),
            {"role": "assistant", "speaker": scenes.TRANSITION_SPEAKER,
             "content": "*Time passes. It is now dusk.*"},
            _msg("Mara", 40)]
    got = length_drift.measure(msgs, [1, 1], CAST, BUDGET)
    assert got["totals"] == [40, 40]     # NOT one merged 80-word turn
    assert got["blocks"] is False


def test_roll_fences_are_stripped_from_word_counts():
    msgs = [{"role": "assistant", "speaker": "Mara",
             "content": "She throws.\n\n```roll\n" + "x " * 200 + "\n```"}]
    got = length_drift.measure(msgs, [1], CAST, BUDGET)
    assert got["totals"] == [2]
    assert got["tier"] == ""


def test_user_messages_are_not_part_of_turns():
    msgs = [{"role": "user", "speaker": "You", "content": "w " * 500},
            _msg("Mara", 40)]
    got = length_drift.measure(msgs, [1], CAST, BUDGET)
    assert got["totals"] == [40]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_length_drift.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.length_drift'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/length_drift.py`:

```python
"""Measure reply-length drift over the recent transcript. Pure: no I/O.

The counterweight to length drift needs to know what actually happened, not
what was asked for. This module turns the stored transcript into per-turn
metrics and the boolean signals the corrective template renders from.
"""

from __future__ import annotations

import re

from . import scenes

WINDOW = 3          # turns measured; a constant, deliberately not a setting
_TRIM = 1.25        # below this, nothing renders
_CUT = 1.75

_ROLL_FENCE = re.compile(r"```[ \t]*roll\b.*?(?:```|\Z)", re.IGNORECASE | re.DOTALL)
_RESERVED = (scenes.ROLL_SPEAKER, scenes.TRANSITION_SPEAKER)


def _is_model_block(m: dict) -> bool:
    return m.get("role") == "assistant" and m.get("speaker") not in _RESERVED


def segment(messages: list[dict], turn_sizes: list[int]) -> list[list[dict]]:
    """Partition model-generated blocks into turns using the recorded sizes.

    Returns [] when the sizes don't account for exactly the model blocks present
    — a hand-edited scene file. Measuring a transcript we can't explain would
    produce confident wrong numbers; silence is the better failure.
    """
    blocks = [m for m in messages if _is_model_block(m)]
    if not turn_sizes or sum(turn_sizes) != len(blocks):
        return []
    turns, at = [], 0
    for size in turn_sizes:
        turns.append(blocks[at:at + size])
        at += size
    return turns


def _words(content: str) -> int:
    return len(_ROLL_FENCE.sub(" ", content).split())


def _paragraphs(content: str) -> int:
    return max(len([p for p in content.split("\n\n") if p.strip()]), 1)


def _identity(speaker, cast_names) -> str:
    """Canonicalize a label to a cast member. split_reply preserves whatever the
    model wrote, so one character can appear as 'Winifred' and 'Winifred Vance';
    counting raw strings inflates the speaker count into a false violation while
    letting that same character slip under blocks_per_speaker."""
    return scenes.match_name(speaker, cast_names) or speaker


def measure(messages: list[dict], turn_sizes: list[int], cast_names,
            budget: dict, window: int = WINDOW) -> dict | None:
    """Per-turn metrics plus the render signals, or None if nothing to measure.

    EVERY signal is "any turn in the window violated it" — including the word
    signal, which uses the window MAXIMUM. A mean oscillates: at a 100-word
    budget, 130/130/130 corrects at 1.30x, one compliant turn clears it at
    1.20x, and the next 150-word turn re-triggers at 1.27x. The maximum makes
    the rule monotone in the window's contents, so it cannot flicker, and makes
    "clears only after 3 compliant turns" true rather than merely claimed.
    """
    turns = segment(messages, turn_sizes)[-window:]
    if not turns:
        return None

    totals, ratios = [], []
    over_blocks = over_paras = over_speakers = over_repeats = False
    for turn in turns:
        total = sum(_words(m["content"]) for m in turn)
        totals.append(total)
        ratios.append(total / budget["reply_words"])
        over_blocks = over_blocks or len(turn) > budget["blocks"]
        over_paras = over_paras or any(_paragraphs(m["content"]) > budget["paragraphs"]
                                       for m in turn)
        counts: dict[str, int] = {}
        for m in turn:
            if m.get("speaker") is None:
                continue  # narration occupies a block but is not a character
            who = _identity(m["speaker"], cast_names)
            counts[who] = counts.get(who, 0) + 1
        over_speakers = over_speakers or len(counts) > budget["speakers"]
        over_repeats = over_repeats or any(n > budget["blocks_per_speaker"]
                                           for n in counts.values())

    peak = max(ratios)
    return {"totals": totals, "max_ratio": peak,
            "tier": "cut" if peak >= _CUT else ("trim" if peak >= _TRIM else ""),
            "blocks": over_blocks, "paragraphs": over_paras,
            "speakers": over_speakers, "blocks_per_speaker": over_repeats}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_length_drift.py -q`
Expected: PASS — 15 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/length_drift.py backend/tests/test_length_drift.py
git commit -m "feat(length-drift): measure per-turn reply length against the budget"
```

---

## Task 8: Render the corrective into the post-history message

**Files:**
- Create: `templates/scene/length_correction.j2`
- Modify: `templates/scene/post_history.j2`
- Modify: `backend/src/grimoire/store/context.py` (`_assemble`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `length_drift.measure` (Task 7), `scenes.get_turn_sizes` (Task 6), `response_presets.resolve` (Task 3).
- Produces: the post-history system message carrying the corrective whenever drift is measured, including when the NPC cards contribute no post-history instructions.

The corrective goes in the post-history system message because it is the **last message before generation** — the closest available counterweight to a transcript full of increasingly long replies.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def _bloat(cid, sid, turns, words):
    for _ in range(turns):
        scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "w " * words}])


def test_no_corrective_on_a_fresh_scene(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    messages = context.build_messages(cid, sid)
    assert not any("run long" in m["content"] for m in messages)


def test_no_corrective_while_replies_are_compliant(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "cinematic")   # 900-word budget
    _bloat(cid, sid, turns=3, words=200)
    messages = context.build_messages(cid, sid)
    assert not any("run long" in m["content"] for m in messages)


def test_corrective_lands_in_the_last_message(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "terse")       # 150-word budget
    _bloat(cid, sid, turns=3, words=600)
    messages = context.build_messages(cid, sid)
    last = messages[-1]
    assert last["role"] == "system"
    assert "run long" in last["content"]
    assert "Cut hard" in last["content"]
    assert "150 words total" in last["content"]


def test_trim_tier_wording(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "terse")       # 150 -> trim band is 188..262
    _bloat(cid, sid, turns=3, words=220)
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "Trim toward the budget" in text
    assert "Cut hard" not in text


def test_structural_lines_appear_only_when_violated(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "terse")       # speakers 2, repeats 1
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Short."},
                                   {"speaker": "Mara", "content": "Again."}])
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "give each character at most 1" in text
    assert "speaking characters" not in text     # the speaker cap was NOT broken
    assert "run long" not in text                # nor the word budget


def test_transition_between_replies_does_not_fire_a_false_block_violation(monkeypatch, tmp_path):
    """A budget-compliant reply followed by a scene transition must not
    measure as one over-cap turn."""
    from grimoire.store import entities
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "brisk")       # blocks cap 4
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Wet rope.")
    entities.create_entity(croot, "locations", "The Long Stair", "Down.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One."},
                                   {"speaker": None, "content": "Two."},
                                   {"speaker": "Winifred", "content": "Three."},
                                   {"speaker": None, "content": "Four."}])
    scenes.set_location(cid, sid, "saltmarch-docks")
    scenes.set_location(cid, sid, "the-long-stair")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Five."}])
    messages = context.build_messages(cid, sid)
    assert not any("blocks" in m["content"] and "keep this one" in m["content"]
                   for m in messages)


def test_corrective_rides_alone_when_cards_have_no_post_history(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response_preset(cid, sid, "terse")
    _bloat(cid, sid, turns=3, words=600)
    messages = context.build_messages(cid, sid)
    assert messages[-1]["role"] == "system"
    assert "run long" in messages[-1]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k corrective`
Expected: FAIL — the post-history message carries no corrective text

- [ ] **Step 3: Create the corrective template**

Create `templates/scene/length_correction.j2`:

```
{#- The adaptive counterweight, appended to the post-history system message —
    the last message before generation, and so the closest available push-back
    against a transcript full of increasingly long replies.

    Renders only when a rule was measurably violated; each rule contributes its
    own lines independently. Vars: drift (length_drift.measure output),
    budget (the resolved knobs). -#}
{%- set lines = [] -%}
{%- if drift.tier -%}
{%- set totals = drift.totals | join(", ") -%}
{%- set _ = lines.append(
  "Your recent replies have run long: the last " ~ drift.totals | length ~
  " turns ran " ~ totals ~ " words against a budget of " ~ budget.reply_words ~
  " — up to " ~ "%.1f" | format(drift.max_ratio) ~ "× over. " ~
  ("Cut hard; this" if drift.tier == "cut" else "Trim toward the budget; this") ~
  " reply must land near " ~ budget.reply_words ~
  " words total. Trim description first, then dialogue tags.") -%}
{%- endif -%}
{%- if drift.blocks -%}
{%- set _ = lines.append(
  "Recent replies have exceeded " ~ budget.blocks ~
  " blocks; keep this one to at most " ~ budget.blocks ~ ", narration included.") -%}
{%- endif -%}
{%- if drift.speakers -%}
{%- set _ = lines.append(
  "Recent replies have exceeded " ~ budget.speakers ~
  " speaking characters; keep this one to at most " ~ budget.speakers ~ ".") -%}
{%- endif -%}
{%- if drift.blocks_per_speaker -%}
{%- set _ = lines.append(
  "A character has taken more than " ~ budget.blocks_per_speaker ~
  " block(s) in a reply; give each character at most " ~
  budget.blocks_per_speaker ~ ".") -%}
{%- endif -%}
{%- if drift.paragraphs -%}
{%- set _ = lines.append(
  "A block has run past " ~ budget.paragraphs ~
  " paragraph(s); keep every block to at most " ~ budget.paragraphs ~ ".") -%}
{%- endif -%}
{{ lines | join("\n") }}
```

- [ ] **Step 4: Join the corrective into the post-history message**

Replace `templates/scene/post_history.j2` with:

```
{#- The trailing system message: the NPC cards' post-history instructions and
    the adaptive length corrective, blank-line separated. Sent last, right
    before generation; omitted entirely when both are empty.
    Vars: npc_cards, length_correction (str, possibly empty). -#}
{%- set blocks = [] -%}
{%- for d in npc_cards -%}
{%- if d.get("post_history_instructions", "").strip() -%}{%- set _ = blocks.append(d.get("post_history_instructions", "").strip()) -%}{%- endif -%}
{%- endfor -%}
{%- if length_correction.strip() -%}{%- set _ = blocks.append(length_correction.strip()) -%}{%- endif -%}
{{ blocks | join("\n\n") }}
```

- [ ] **Step 5: Wire measurement into `_assemble`**

In `backend/src/grimoire/store/context.py`, add `length_drift` to the store imports. In `_assemble`, after the `budget` resolution added in Task 4:

```python
    # npc_names + player_names, NOT `cast` — scene_cast entries carry role/kind/id
    # with no name, so reading a name off them yields "" and silently disables
    # speaker canonicalization.
    cast_names = npc_names + player_names
    drift = length_drift.measure(history, scenes.get_turn_sizes(cid, sid),
                                 cast_names, budget)
    length_correction = (prompts.render("scene/length_correction.j2",
                                        drift=drift, budget=budget) if drift else "")
```

Place this after `npc_names` and `player_names` are built (both exist well before the style resolution you edited in Task 4).

and change the post-history render to pass it:

```python
    post_history = prompts.render("scene/post_history.j2", npc_cards=npc_cards,
                                  length_correction=length_correction)
```

`build_messages` already emits the post-history system message whenever the string is non-empty, so a scene whose cards carry no instructions still receives the corrective — no change needed there.

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS

- [ ] **Step 7: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — no failures

- [ ] **Step 8: Commit**

```bash
git add templates/scene/length_correction.j2 templates/scene/post_history.j2 backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat(context): push back on measured length drift from the post-history slot"
```

**Stage 2 is complete here.** Drift control is live end to end on default budgets.

---

## Task 9: Document the new templates

**Files:**
- Modify: `templates/README.md`

**Interfaces:** none — documentation only.

`templates/README.md` documents each template's variables, and two new templates plus a changed one need entries or the next person editing prompts is guessing.

- [ ] **Step 1: Add the entries**

In `templates/README.md`, following the existing format for scene templates, document:

- `scene/sections/response_budget.j2` — vars: `budget` (`{reply_words, blocks, paragraphs, speakers, blocks_per_speaker}`), the knobs resolved by `response_presets.resolve` over turn → scene → campaign → global.
- `scene/length_correction.j2` — vars: `drift` (output of `length_drift.measure`), `budget`. Rendered only when a rule was measurably violated; joined into the post-history system message.
- `post_history.j2` — note the added `length_correction` var.

- [ ] **Step 2: Verify templates still render**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add templates/README.md
git commit -m "docs(templates): document the response budget and length corrective"
```

---

## Verification

Before considering this plan complete, confirm all of the following and paste the actual output:

- [ ] `backend/.venv/Scripts/python.exe -m pytest backend -q` — full suite passes
- [ ] The four shipped built-in presets load against the real `templates/` dir (covered by `test_shipped_builtins_are_length_only`)
- [ ] A manual smoke check via the `verify` skill: start a scene, send several turns, and confirm the budget section appears in the scene inspector's token breakdown and that a deliberately long stretch produces a corrective

## What this plan does NOT cover

Stages 3–5 of the spec, to be planned separately once this lands:

- Scope settings UI and the `/api/campaigns/{cid}/response` endpoints (plus retiring the `/style` endpoints)
- `ResponsePresetPicker`, `ResponsePresetsView`, **Save as preset…**, preset CRUD routes, and `/usage` delete-impact preview
- The composer one-shot turn-override chip and the `response` field on the chat send payload

Until those land, budgets resolve from defaults and the scene-level `response_preset` / `length_*` frontmatter keys, which can be set by hand or via `scenes.set_response_preset`.
