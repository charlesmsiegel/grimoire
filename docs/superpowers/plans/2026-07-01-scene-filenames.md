# Scene Filenames `<number>--<date>--<slug>` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scene files become `<number>--<in-world-date>--<title-slug>.md` (date section omitted while undated), with campaign-wide width re-pads past 999 and a startup migration for legacy real-date filenames.

**Architecture:** A tiny id-grammar module (`scene_ids.py`) parses/formats the three `--`-separated sections. Each store that persists scene ids (appearances, chronicle, changes, plot) gains a bulk `repoint_scenes(cid, mapping)`; `scene_refs.repoint` fans out to all four. `scenes.py` renames files (create/title-rename/first-date-set/re-pad) and calls `scene_refs.repoint`; `migrations.py` converts legacy stores at FastAPI startup (lifespan hook). The set-datetime API response gains `id`; the frontend adopts a renamed id via a new `onSceneRenamed` callback.

**Tech Stack:** FastAPI + pytest (backend), React + vitest (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-01-scene-filenames-design.md`

## Global Constraints

- Sections separated by `--`; `slugify` collapses dash runs, so sections can never contain `--`.
- Dated id: `007--1023-05-12--the-ambush`. Undated id: `007--the-ambush` (no date section).
- Number width starts at 3 (`MIN_WIDTH = 3`), grows campaign-wide on overflow; numbers are never reused or renumbered (deletes leave gaps).
- The filename date is the **first** `time_history` entry's date part only (time carries a `:`, illegal on Windows), `slugify`d.
- Scene ids are persisted in exactly four stores: appearances (`scenes` lists), chronicle (keys + `id` field), changes (`scene` field), plot (`beats[].scene` + `last_scene`).
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`, isolate via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Frontend tests: run **from `frontend/`**: `npx vitest run` and `npx tsc -b`.
- Commit after every task.

---

### Task 1: Id grammar module `scene_ids.py`

**Files:**
- Create: `backend/src/grimoire/store/scene_ids.py`
- Test: `backend/tests/test_scene_ids.py`

**Interfaces:**
- Consumes: `slugify` from `grimoire.store.paths`.
- Produces (used by Tasks 2–6):
  - `MIN_WIDTH: int = 3`
  - `parse_sid(sid: str) -> dict | None` — `{"number": int, "width": int, "date_slug": str | None, "title_slug": str}`, or `None` for legacy/foreign ids.
  - `format_sid(number: int, width: int, date_slug: str | None, title_slug: str) -> str`
  - `date_slug_of(canonical: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_scene_ids.py
from grimoire.store.scene_ids import date_slug_of, format_sid, parse_sid


def test_parse_dated():
    assert parse_sid("007--1023-05-12--the-ambush") == {
        "number": 7, "width": 3, "date_slug": "1023-05-12", "title_slug": "the-ambush"}


def test_parse_undated():
    assert parse_sid("007--the-ambush") == {
        "number": 7, "width": 3, "date_slug": None, "title_slug": "the-ambush"}


def test_parse_uniquify_suffix_stays_in_title():
    assert parse_sid("007--the-ambush-2")["title_slug"] == "the-ambush-2"


def test_parse_rejects_legacy_and_garbage():
    assert parse_sid("2026-06-28-the-ambush") is None  # legacy real-date id
    assert parse_sid("nope") is None
    assert parse_sid("--x") is None            # empty number
    assert parse_sid("7--") is None            # empty title
    assert parse_sid("a--b--c--d") is None     # too many sections
    assert parse_sid("x7--slug") is None       # non-numeric number


def test_format_round_trips():
    assert format_sid(7, 3, None, "the-ambush") == "007--the-ambush"
    assert format_sid(7, 3, "1023-05-12", "the-ambush") == "007--1023-05-12--the-ambush"
    assert format_sid(1000, 4, None, "x") == "1000--x"
    for sid in ("007--the-ambush", "0042--1023-05-12--x"):
        p = parse_sid(sid)
        assert format_sid(p["number"], p["width"], p["date_slug"], p["title_slug"]) == sid


def test_date_slug_of_strips_time_and_slugifies():
    assert date_slug_of("2026-07-04T09:00") == "2026-07-04"
    assert date_slug_of("2026-07-04") == "2026-07-04"
    assert date_slug_of("12 Frostfall 892") == "12-frostfall-892"  # fantasy calendars
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_ids.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.scene_ids'`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/grimoire/store/scene_ids.py
"""Scene id grammar: <number>--<date-slug>--<title-slug>, date section optional.

The number comes first so lexicographic filename order equals play order
absolutely. Sections are separated by "--", which is unambiguous because
slugify collapses dash runs — no section can contain consecutive dashes.
"""

from __future__ import annotations

from .paths import slugify

MIN_WIDTH = 3


def parse_sid(sid: str) -> dict | None:
    """Split a scene id into its sections; None for ids outside the grammar
    (legacy real-date ids, foreign strings)."""
    parts = sid.split("--")
    if len(parts) == 2:
        num, date, title = parts[0], None, parts[1]
    elif len(parts) == 3:
        num, date, title = parts
    else:
        return None
    if not num.isdigit() or not title:
        return None
    return {"number": int(num), "width": len(num), "date_slug": date, "title_slug": title}


def format_sid(number: int, width: int, date_slug: str | None, title_slug: str) -> str:
    mid = f"{date_slug}--" if date_slug else ""
    return f"{number:0{width}d}--{mid}{title_slug}"


def date_slug_of(canonical: str) -> str:
    """Filename-safe slug of a canonical moment's date part. The time part is
    dropped — it contains a colon, illegal in Windows filenames."""
    return slugify(canonical.partition("T")[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_ids.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scene_ids.py backend/tests/test_scene_ids.py
git commit -m "feat(scenes): scene id grammar <number>--<date>--<slug>"
```

---

### Task 2: Bulk repointing — `repoint_scenes` per store + `scene_refs.repoint`

**Files:**
- Create: `backend/src/grimoire/store/scene_refs.py`
- Modify: `backend/src/grimoire/store/appearances.py:124-140` (replace `rename_scene` with `repoint_scenes`)
- Modify: `backend/src/grimoire/store/chronicle.py` (add `repoint_scenes`)
- Modify: `backend/src/grimoire/store/changes.py` (add `repoint_scenes`)
- Modify: `backend/src/grimoire/store/plot.py` (add `repoint_scenes`)
- Modify: `backend/tests/test_appearances_store.py:121-133` (call `repoint_scenes` instead of `rename_scene`)
- Test: `backend/tests/test_scene_refs.py`

**Interfaces:**
- Consumes: each store's existing `record`/`read`/`read_chronicle` + private `_write`/`_path` helpers (same module, so private use is fine).
- Produces (used by Tasks 3–6):
  - `scene_refs.repoint(cid: str, mapping: dict[str, str]) -> None` — repoints all four stores; ignores identity entries; no-op on empty mapping. **Does not rename files** — callers own the files.
  - `appearances.repoint_scenes(cid, mapping)`, `chronicle.repoint_scenes(cid, mapping)`, `changes.repoint_scenes(cid, mapping)`, `plot.repoint_scenes(cid, mapping)`.
- Removes: `appearances.rename_scene` (its only caller, `scenes.rename_scene`, switches to `scene_refs.repoint` in Task 5 — but the swap happens here so nothing dangles: see Step 3).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_scene_refs.py
import json

from grimoire.store import campaigns, changes, chronicle, plot, scene_refs, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def _seed_appearance(cid, sid):
    p = campaigns.campaign_root(cid) / "appearances.json"
    p.write_text(json.dumps({
        "characters/a": {"version": "default", "base": "", "scenes": [sid], "role": "npc"},
        "characters/b": {"version": "default", "base": "", "scenes": ["other"], "role": "npc"},
    }), encoding="utf-8")


def test_repoint_updates_all_four_stores(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    old = "001--s"
    _seed_appearance(cid, old)
    chronicle.absorb(cid, {"id": old, "one_line": "x", "summary": "", "keywords": []})
    changes.record(cid, old, {"characters/a": [{"op": "equal", "text": "hi"}]})
    plot.set_movement(cid, "heist", "The Heist", "open", "cased the vault", old)

    scene_refs.repoint(cid, {old: "001--2026-07-04--s"})

    from grimoire.store import appearances
    assert appearances.record(cid)["characters/a"]["scenes"] == ["001--2026-07-04--s"]
    assert appearances.record(cid)["characters/b"]["scenes"] == ["other"]  # untouched
    rec = chronicle.read_chronicle(cid)["001--2026-07-04--s"]
    assert rec["id"] == "001--2026-07-04--s"
    assert "001--s" not in chronicle.read_chronicle(cid)
    assert changes.read(cid)["characters/a"]["scene"] == "001--2026-07-04--s"
    thread = plot.read(cid)["heist"]
    assert thread["last_scene"] == "001--2026-07-04--s"
    assert thread["beats"][0]["scene"] == "001--2026-07-04--s"


def test_repoint_identity_and_empty_are_noops(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {})
    scene_refs.repoint(cid, {"a": "a"})  # must not create empty store files
    assert not (campaigns.campaign_root(cid) / "chronicle.json").exists()


def test_repoint_tolerates_missing_stores(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {"a": "b"})  # no store files exist — must not raise
```

Also update the two direct `appearances.rename_scene` unit tests in `backend/tests/test_appearances_store.py` (lines ~121–133) to the new bulk API:

```python
def test_repoint_scenes_only_touches_matching_id(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)  # keep the file's existing helper name/shape
    _seed(cid, {"characters/x": {"version": "v", "base": "", "scenes": ["a", "b"], "role": "npc"}})
    ap.repoint_scenes(cid, {"a": "z"})
    assert ap.record(cid)["characters/x"]["scenes"] == ["z", "b"]


def test_repoint_scenes_noop_when_id_unchanged(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _seed(cid, {"characters/x": {"version": "v", "base": "", "scenes": ["a"], "role": "npc"}})
    ap.repoint_scenes(cid, {"a": "a"})
    assert ap.record(cid)["characters/x"]["scenes"] == ["a"]
```

(Adapt to the file's actual seeding helpers — read the existing tests at lines 121–133 first; the end-to-end test `test_rename_scene_migrates_cast_end_to_end` stays as-is since it drives `scenes.rename_scene`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_refs.py -q`
Expected: FAIL — `ImportError: cannot import name 'scene_refs'`

- [ ] **Step 3: Write the implementation**

In `appearances.py`, replace `rename_scene` (lines 124–140) with:

```python
def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in every appearance's scenes list.

    Cast is keyed by actor here, not by scene, so a scene rename (which changes
    the sid) would otherwise orphan its cast under the old id."""
    data = record(cid)
    changed = False
    for rec in data.values():
        scenes_list = rec.get("scenes", [])
        if any(s in mapping for s in scenes_list):
            rec["scenes"] = [mapping.get(s, s) for s in scenes_list]
            changed = True
    if changed:
        _write(cid, data)
```

In `chronicle.py`, add after `absorb`:

```python
def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: rewrite record keys and their id fields."""
    data = read_chronicle(cid)
    if not any(k in mapping for k in data):
        return
    out = {}
    for k, rec in data.items():
        if rec.get("id") in mapping:
            rec = {**rec, "id": mapping[rec["id"]]}
        out[mapping.get(k, k)] = rec
    _chronicle_path(cid).write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

In `changes.py`, add after `record`:

```python
def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each record's scene field."""
    data = read(cid)
    hit = False
    for rec in data.values():
        if rec.get("scene") in mapping:
            rec["scene"] = mapping[rec["scene"]]
            hit = True
    if hit:
        _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

In `plot.py`, add after `set_movement`:

```python
def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in beats and last_scene markers."""
    data = read(cid)
    hit = False
    for thread in data.values():
        if thread.get("last_scene") in mapping:
            thread["last_scene"] = mapping[thread["last_scene"]]
            hit = True
        for beat in thread.get("beats", []):
            if beat.get("scene") in mapping:
                beat["scene"] = mapping[beat["scene"]]
                hit = True
    if hit:
        _write(cid, data)
```

Create `scene_refs.py`:

```python
# backend/src/grimoire/store/scene_refs.py
"""Bulk scene-id repointing across every store that persists scene ids.

A scene's id is its filename stem, so file renames (title renames, first-date
stamps, width re-pads, legacy migration) must be followed by every persisted
reference. Exactly four stores hold scene ids: appearances (per-actor scenes
lists), chronicle (record keys + id fields), changes (per-record scene field),
and plot (beats[].scene + last_scene). Callers rename the files themselves.
"""

from __future__ import annotations

from . import appearances, changes, chronicle, plot


def repoint(cid: str, mapping: dict[str, str]) -> None:
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    for mod in (appearances, chronicle, changes, plot):
        mod.repoint_scenes(cid, mapping)
```

In `scenes.py:103`, `rename_scene` still calls `appearances.rename_scene` — switch it now so nothing dangles (full rewrite comes in Task 5):

```python
    if new_sid != sid:
        p.rename(_scene_path(cid, new_sid))
        # a scene's id is its filename: carry every store's references across
        scene_refs.repoint(cid, {sid: new_sid})
```

with `scene_refs` added to the `from . import ...` line at `scenes.py:8` (keep `appearances` only if still referenced elsewhere in the module — it isn't, so drop it).

In `store/__init__.py`, add `scene_ids` and `scene_refs` to the `from . import (...)` block (line 5–9).

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (including the updated `test_appearances_store.py` and untouched end-to-end rename test)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store backend/tests/test_scene_refs.py backend/tests/test_appearances_store.py
git commit -m "feat(scenes): bulk scene-id repointing across all four referencing stores"
```

---

### Task 3: `create_scene` — numbered ids, width, and the re-pad

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:40-49` (`create_scene`) + new helpers
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `scene_ids.parse_sid/format_sid/MIN_WIDTH` (Task 1), `scene_refs.repoint` (Task 2).
- Produces:
  - `create_scene(cid, title) -> str` returning e.g. `"001--my-first-scene"`.
  - `repad(cid: str, width: int) -> None` — module-public; also used by migration (Task 6).

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_scene_store.py`)

```python
def test_create_assigns_padded_sequence_numbers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.create_scene(cid, "Alpha") == "001--alpha"
    assert scenes.create_scene(cid, "Beta") == "002--beta"


def test_numbering_skips_gaps_left_by_deletes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1 = scenes.create_scene(cid, "Alpha")
    scenes.create_scene(cid, "Beta")
    scenes.delete_scene(cid, s1)
    assert scenes.create_scene(cid, "Gamma") == "003--gamma"  # 001 is never reused


def test_repad_widens_every_scene_and_repoints(monkeypatch, tmp_path):
    from grimoire.store import chronicle
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "One")           # 001--one
    d = campaigns.campaign_root(cid) / "scenes"
    (d / f"{sid}.md").rename(d / "999--one.md")     # simulate a campaign at the width limit
    chronicle.absorb(cid, {"id": "999--one", "one_line": "x", "summary": "", "keywords": []})
    new = scenes.create_scene(cid, "Two")
    assert new == "1000--two"
    assert sorted(p.stem for p in d.glob("*.md")) == ["0999--one", "1000--two"]
    assert "0999--one" in chronicle.read_chronicle(cid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: the three new tests FAIL (`001--alpha != 2026-...-alpha`); pre-existing tests still pass (they never assert the prefix — only `.endswith(...)`).

- [ ] **Step 3: Write the implementation** (replace `create_scene`, scenes.py:40-49)

```python
def _numbering(cid: str) -> tuple[int, int]:
    """(next number, current pad width) from the files on disk — no stored
    counter. Width starts at MIN_WIDTH and follows the widest number present;
    legacy (unmigrated) stems don't parse and are ignored."""
    top, width = 0, scene_ids.MIN_WIDTH
    d = _scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            parsed = scene_ids.parse_sid(p.stem)
            if parsed:
                top = max(top, parsed["number"])
                width = max(width, parsed["width"])
    return top + 1, width


def repad(cid: str, width: int) -> None:
    """Re-pad every scene number to `width` digits (renames files, repoints all
    referencing stores). Keeps widths uniform so lexicographic order stays exact."""
    mapping = {}
    for p in _scenes_dir(cid).glob("*.md"):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed and parsed["width"] != width:
            mapping[p.stem] = scene_ids.format_sid(
                parsed["number"], width, parsed["date_slug"], parsed["title_slug"])
    for old, new in mapping.items():
        _scene_path(cid, old).rename(_scene_path(cid, new))
    scene_refs.repoint(cid, mapping)


def create_scene(cid: str, title: str) -> str:
    _require_campaign(cid)
    d = _scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    number, width = _numbering(cid)
    if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
        width = len(str(number))
        repad(cid, width)
    now = now_iso()
    base = scene_ids.format_sid(number, width, None, slugify(title))
    sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    _scene_path(cid, sid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return sid
```

Add `scene_ids` to the `from . import ...` line at `scenes.py:8`.

- [ ] **Step 4: Run the full backend suite** (create_scene is called everywhere)

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. If a test hard-codes the old real-date prefix, update it to the new grammar (search the failure output; `test_scene_store.py:15` and `:98` use `.endswith`, which still passes).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat(scenes): numbered scene ids with campaign-wide width re-pad"
```

---

### Task 4: First `set_datetime` stamps the date into the filename

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:224-244` (`set_datetime`)
- Test: `backend/tests/test_scene_store.py` (rewrite `test_set_datetime_first_silent_then_advance`, add cast-carry test)

**Interfaces:**
- Consumes: `scene_ids` (Task 1), `scene_refs.repoint` (Task 2).
- Produces: `set_datetime(cid, sid, native) -> dict` now returns `{"advanced": bool, "friendly": str, "id": str}` — `id` is the possibly-renamed scene id. The route (`routes.py:1377`, `{"ok": True, **result}`) passes it through automatically.

- [ ] **Step 1: Update/write the failing tests**

Rewrite `test_set_datetime_first_silent_then_advance` (test_scene_store.py:121-136):

```python
def test_set_datetime_first_silent_then_advance(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    # first set: silent, no transcript line — and the start date enters the filename
    res = scenes.set_datetime(cid, sid, "2026-06-29")
    assert res == {"advanced": False, "friendly": "29 June 2026", "id": "001--2026-06-29--s"}
    sid = res["id"]
    assert scenes.get_time_history(cid, sid) == ["2026-06-29"]
    assert scenes.read_scene(cid, sid)["messages"] == []
    # change: appends an italic transition line; filename keeps the start date
    res = scenes.set_datetime(cid, sid, "2026-07-04T09:00")
    assert res == {"advanced": True, "friendly": "4 July 2026", "id": sid}
    assert scenes.get_time_history(cid, sid) == ["2026-06-29", "2026-07-04T09:00"]
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*Time passes. It is now 4 July 2026.*"}]
    # re-set the same current: no-op
    assert scenes.set_datetime(cid, sid, "2026-07-04T09:00") == {
        "advanced": False, "friendly": "4 July 2026", "id": sid}
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1
```

Add:

```python
def test_first_datetime_rename_carries_references(monkeypatch, tmp_path):
    import json
    from grimoire.store import appearances
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "appearances.json").write_text(json.dumps(
        {"characters/a": {"version": "default", "base": "", "scenes": [sid], "role": "npc"}}),
        encoding="utf-8")
    new_sid = scenes.set_datetime(cid, sid, "2026-06-29")["id"]
    assert new_sid != sid
    assert appearances.record(cid)["characters/a"]["scenes"] == [new_sid]
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, sid)


def test_first_datetime_with_time_of_day_keeps_filename_windows_safe(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    new_sid = scenes.set_datetime(cid, sid, "2026-06-29T14:30")["id"]
    assert new_sid == "001--2026-06-29--s"  # time part (with its colon) never reaches the filename
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k datetime`
Expected: FAIL — result dicts lack `"id"`, filename not renamed.

- [ ] **Step 3: Write the implementation** (replace `set_datetime`, scenes.py:224-244)

```python
def set_datetime(cid: str, sid: str, native: str) -> dict:
    """Set the scene's current moment (in the primary calendar). The first set is
    silent and stamps the start date into the filename (the id changes); later
    changes append an assistant transition line. Returns {"advanced", "friendly",
    "id"} where id is the possibly-renamed scene id."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    provider = calendars.get_provider(cfg["primary"])
    canonical = calendars.normalize(provider, native)  # raises calendars.CalendarError
    friendly = calendars.friendly(provider, canonical)
    history = get_time_history(cid, sid)
    if history and history[-1] == canonical:
        return {"advanced": False, "friendly": friendly, "id": sid}
    advanced = bool(history)
    if advanced:
        append_message(cid, sid, "assistant", f"*Time passes. It is now {friendly}.*")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(canonical)
    meta["time_history"] = ",".join(history)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if not advanced:
        sid = _stamp_start_date(cid, sid, canonical)
    return {"advanced": advanced, "friendly": friendly, "id": sid}


def _stamp_start_date(cid: str, sid: str, canonical: str) -> str:
    """First date set: insert the date section into the filename. The start date
    is fixed — later advances never touch the name. Legacy ids are left alone."""
    parsed = scene_ids.parse_sid(sid)
    if parsed is None or parsed["date_slug"] is not None:
        return sid
    base = scene_ids.format_sid(parsed["number"], parsed["width"],
                                scene_ids.date_slug_of(canonical), parsed["title_slug"])
    new_sid = uniquify(base, lambda c: c != sid and _scene_path(cid, c).exists())
    _scene_path(cid, sid).rename(_scene_path(cid, new_sid))
    scene_refs.repoint(cid, {sid: new_sid})
    return new_sid
```

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. Watch for tests that call `set_datetime` and then keep using the old sid (grep failures for `set_datetime`) — update them to adopt `res["id"]`. Route-level datetime tests in `test_routes.py` asserting the exact response shape gain `"id"`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests
git commit -m "feat(scenes): first set_datetime stamps the start date into the filename"
```

---

### Task 5: `rename_scene` preserves number and date sections

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:88-104` (`rename_scene`)
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `scene_ids`, `scene_refs.repoint`.
- Produces: `rename_scene(cid, sid, title) -> str` — same signature; new ids keep `NNN--` (and `--date--`) verbatim; legacy ids keep the old created-date prefix scheme.

- [ ] **Step 1: Write the failing tests**

```python
def test_rename_preserves_number_and_date_sections(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Old")                      # 001--old
    assert scenes.rename_scene(cid, sid, "New Name") == "001--new-name"
    sid = scenes.set_datetime(cid, "001--new-name", "2026-06-29")["id"]  # 001--2026-06-29--new-name
    assert scenes.rename_scene(cid, sid, "Final") == "001--2026-06-29--final"


def test_rename_repoints_chronicle_changes_and_plot(monkeypatch, tmp_path):
    from grimoire.store import changes, chronicle, plot
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    chronicle.absorb(cid, {"id": sid, "one_line": "x", "summary": "", "keywords": []})
    changes.record(cid, sid, {"characters/a": [{"op": "equal", "text": "hi"}]})
    plot.set_movement(cid, "heist", "The Heist", "open", "beat", sid)
    new_sid = scenes.rename_scene(cid, sid, "Renamed")
    assert new_sid in chronicle.read_chronicle(cid)
    assert changes.read(cid)["characters/a"]["scene"] == new_sid
    assert plot.read(cid)["heist"]["last_scene"] == new_sid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k rename`
Expected: the two new tests FAIL (`rename_scene` rebuilds the id from `created`, producing a real-date prefix). (`test_rename_repoints_...` may partially pass if Task 2's repoint swap landed — the section-preservation test is the hard gate.)

- [ ] **Step 3: Write the implementation** (replace `rename_scene`, scenes.py:88-104)

```python
def rename_scene(cid: str, sid: str, title: str) -> str:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    parsed = scene_ids.parse_sid(sid)
    if parsed:  # keep number and date sections verbatim; only the title re-slugs
        base = scene_ids.format_sid(
            parsed["number"], parsed["width"], parsed["date_slug"], slugify(title))
    else:  # legacy (pre-migration) id: keep the old created-date prefix scheme
        base = f"{meta.get('created', now_iso())[:10]}-{slugify(title)}"
    new_sid = uniquify(base, lambda c: c != sid and _scene_path(cid, c).exists())
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if new_sid != sid:
        p.rename(_scene_path(cid, new_sid))
        # a scene's id is its filename: carry every store's references across
        scene_refs.repoint(cid, {sid: new_sid})
    return new_sid
```

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (existing rename tests assert `.endswith("shiny-new-name")` and id stability — both hold).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat(scenes): title rename preserves number and date sections"
```

---

### Task 6: Legacy migration at startup

**Files:**
- Create: `backend/src/grimoire/store/migrations.py`
- Modify: `backend/src/grimoire/main.py` (lifespan hook)
- Modify: `backend/src/grimoire/store/__init__.py` (add `migrations` to the import block)
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `campaigns.list_campaigns/campaign_root`, `scene_ids`, `scene_refs.repoint`, `scenes.repad` (Task 3), `parse_frontmatter`, `slugify`, `uniquify`.
- Produces: `migrations.migrate_scene_ids() -> None` — idempotent, called from the FastAPI lifespan (fires on real server start; `TestClient` without a `with` block never triggers it, so route tests are unaffected).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_migrations.py
import json

from grimoire.store import campaigns, chronicle, migrations, scenes, worlds
from grimoire.store.frontmatter import dump_frontmatter


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def _legacy_scene(cid, stem, title, created, time_history=""):
    d = campaigns.campaign_root(cid) / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"title": title, "model": "m", "created": created, "updated": created}
    if time_history:
        meta["time_history"] = time_history
    (d / f"{stem}.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def test_migrates_legacy_scenes_in_created_order(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _legacy_scene(cid, "2026-06-28-second", "Second", "2026-06-28T10:00:00Z")
    _legacy_scene(cid, "2026-06-27-first", "First", "2026-06-27T10:00:00Z",
                  time_history="1023-05-12,1023-05-13T09:00")
    chronicle.absorb(cid, {"id": "2026-06-27-first", "one_line": "x", "summary": "", "keywords": []})

    migrations.migrate_scene_ids()

    d = campaigns.campaign_root(cid) / "scenes"
    assert sorted(p.stem for p in d.glob("*.md")) == [
        "001--1023-05-12--first",   # dated: start date from time_history[0], time stripped
        "002--second",              # undated: no date section
    ]
    assert "001--1023-05-12--first" in chronicle.read_chronicle(cid)


def test_migration_is_idempotent_and_continues_numbering(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scenes.create_scene(cid, "Already New")   # 001--already-new
    _legacy_scene(cid, "2026-06-28-old", "Old", "2026-06-28T10:00:00Z")
    migrations.migrate_scene_ids()
    d = campaigns.campaign_root(cid) / "scenes"
    assert sorted(p.stem for p in d.glob("*.md")) == ["001--already-new", "002--old"]
    before = sorted(p.stem for p in d.glob("*.md"))
    migrations.migrate_scene_ids()            # second run: no changes
    assert sorted(p.stem for p in d.glob("*.md")) == before


def test_migration_handles_empty_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    migrations.migrate_scene_ids()  # no campaigns — must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_migrations.py -q`
Expected: FAIL — `ImportError: cannot import name 'migrations'`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/grimoire/store/migrations.py
"""One-time store migrations, run once per app startup. Each is idempotent."""

from __future__ import annotations

from . import campaigns, scene_ids, scene_refs, scenes
from .frontmatter import parse_frontmatter
from .paths import slugify, uniquify


def migrate_scene_ids() -> None:
    """Rename legacy real-date scene files (<real-date>-<slug>.md) into the
    <number>--<date>--<slug> grammar: number by created order (continuing after
    any already-migrated scenes), date section from the scene's first
    time_history entry, then repoint every persisted reference. New-grammar
    files never match the legacy test, so re-running is a no-op."""
    for c in campaigns.list_campaigns():
        _migrate_campaign(c["id"])


def _migrate_campaign(cid: str) -> None:
    d = campaigns.campaign_root(cid) / "scenes"
    if not d.exists():
        return
    legacy, top, width = [], 0, scene_ids.MIN_WIDTH
    for p in d.glob("*.md"):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed:
            top = max(top, parsed["number"])
            width = max(width, parsed["width"])
        else:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            legacy.append((meta.get("created", ""), p.stem, meta))
    if not legacy:
        return
    legacy.sort(key=lambda t: (t[0], t[1]))  # created, then stem — never compare the meta dicts
    width = max(width, len(str(top + len(legacy))))
    taken = {p.stem for p in d.glob("*.md")}
    mapping: dict[str, str] = {}
    for number, (_, stem, meta) in enumerate(legacy, start=top + 1):
        history = [x for x in meta.get("time_history", "").split(",") if x]
        date_slug = scene_ids.date_slug_of(history[0]) if history else None
        base = scene_ids.format_sid(number, width, date_slug, slugify(meta.get("title", stem)))
        new_sid = uniquify(base, lambda cand: cand in taken)
        taken.add(new_sid)
        mapping[stem] = new_sid
    for old, new in mapping.items():
        (d / f"{old}.md").rename(d / f"{new}.md")
    scene_refs.repoint(cid, mapping)
    scenes.repad(cid, width)  # widths must stay uniform if legacy count outgrew them
```

In `main.py`, add the lifespan hook:

```python
from contextlib import asynccontextmanager

from .store import migrations


@asynccontextmanager
async def _lifespan(app: FastAPI):
    migrations.migrate_scene_ids()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="grimoire", lifespan=_lifespan)
    ...
```

(Only the `FastAPI(...)` call and the two additions change; the rest of `create_app` stays.)

Add `migrations` to the `from . import (...)` block in `store/__init__.py`.

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire backend/tests/test_migrations.py
git commit -m "feat(scenes): migrate legacy real-date scene ids at startup"
```

---

### Task 7: Frontend adopts the renamed id after the first date set

**Files:**
- Modify: `frontend/src/api/client.ts:392-394` (`setSceneDatetime` response type)
- Modify: `frontend/src/components/SceneInspector.tsx:13-14,69-80`
- Modify: `frontend/src/components/CastPanel.tsx:15-23,102-113`
- Modify: `frontend/src/routes/CampaignView.tsx:267-274,319-322` (+ a `sceneRenamed` handler)
- Test: `frontend/src/components/SceneInspector.test.tsx`, `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Consumes: the backend's new `id` field in the set-datetime response (Task 4).
- Produces: `onSceneRenamed?: (id: string) => void` prop on both `SceneInspector` and `CastPanel`; `CampaignView` wires it to re-list scenes and select the new id.

- [ ] **Step 1: Update mocks and write the failing tests**

In both test files, give the `setSceneDatetime` mock the new field (SceneInspector.test.tsx:40, CastPanel.test.tsx:29): add `id: "s"` to the resolved value (the mocked sid is `"s"`, so `id: "s"` means "not renamed" for existing tests).

Add to `SceneInspector.test.tsx`:

```tsx
test("first date set renames the scene: adopts the new id via onSceneRenamed", async () => {
  (api.setSceneDatetime as any).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  const onRenamed = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0}
                         onSceneChanged={() => {}} onSceneRenamed={onRenamed} />);
  const input = await screen.findByLabelText("Scene date");
  fireEvent.change(input, { target: { value: "2026-07-04" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(onRenamed).toHaveBeenCalledWith("001--2026-07-04--s"));
});
```

Add the analogous test to `CastPanel.test.tsx` (same shape, using its existing `renderPanel` helper with an `onSceneRenamed` prop and its "Scene date" input + `/advance to|set date/i` button).

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx src/components/CastPanel.test.tsx`
Expected: the two new tests FAIL (prop doesn't exist, callback never fires).

- [ ] **Step 3: Write the implementation**

`client.ts:392-394`:

```ts
  setSceneDatetime: (cid: string, sid: string, datetime: string) =>
    request<{ ok: boolean; advanced: boolean; friendly: string; id: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/datetime`, { datetime }),
```

`SceneInspector.tsx` — signature (lines 13-14):

```tsx
export function SceneInspector({ cid, sid, refreshKey, onSceneChanged, onSceneRenamed }:
  { cid: string; sid: string; refreshKey: number; onSceneChanged: () => void;
    onSceneRenamed?: (id: string) => void }) {
```

`applyDatetime` (lines 69-80):

```tsx
  async function applyDatetime() {
    if (!dateInput) return;
    setError(null);
    try {
      const res = await api.setSceneDatetime(cid, sid, dateInput);
      setDateInput("");
      if (res.id !== sid) {
        // first date set renames the scene file — adopt the new id; the sid
        // prop change re-runs every load effect, so skip the stale reload
        onSceneRenamed?.(res.id);
        return;
      }
      await reloadWhen();
      onSceneChanged();  // surface the "Time passes…" transition line in the stream
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

`CastPanel.tsx` — add `onSceneRenamed?: (id: string) => void` to the props (destructure at line 16, type at lines 17-23) and apply the same `applyDatetime` change (lines 102-113, with `onSeeded()` in place of `onSceneChanged()`).

`CampaignView.tsx` — add next to `renameScene` (line 69):

```tsx
  async function sceneRenamed(id: string) {
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }
```

and pass `onSceneRenamed={sceneRenamed}` to `<CastPanel …>` (line 267) and `<SceneInspector …>` (line 320).

- [ ] **Step 4: Run the frontend suite and typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: PASS / no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(scenes): frontend adopts the renamed scene id after the first date set"
```

---

### Task 8: Full verification

- [ ] **Step 1: Backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, no warnings about deprecated `on_event` (we used lifespan).

- [ ] **Step 2: Frontend suite + typecheck** (from `frontend/`)

Run: `npx vitest run` and `npx tsc -b`
Expected: PASS / clean.

- [ ] **Step 3: Commit anything outstanding and hand off for review**

```bash
git status --short   # expect clean
```
