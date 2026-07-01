# Scene Plot Threads (Phase 5a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track per-campaign plot threads so the scene-absorb extraction proposes movements (open/advance/close with a beat), the review approves them (beat editable), and the context injects open threads — riding the existing absorb pipeline.

**Architecture:** A new `plot.json` store (`store/plot.py`) holds threads `{title, status, beats:[{scene,text}], last_scene}`. `absorb` extracts `plot_movements`, materializes each as a new `plot` StagedEdit (editable beat `after` + structured `payload`), and `apply_edits` writes them via `plot.set_movement`. `context` injects a `# Plot threads` section. One-line frontend type change plus a vitest.

**Tech Stack:** FastAPI backend (Python, pytest), Vite/React frontend (vitest/tsc).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-scene-plot-threads-design.md`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q` (from repo root) — or an absolute test path.
- Frontend runs **from** `frontend/`: `npx vitest run` and `npx tsc -b`.
- Statuses are exactly `("open", "advanced", "closed")`, exported as `plot.STATUSES`.
- LLM output is untrusted: coerce to stripped `str`; an invalid/missing `status` ⇒ `"open"`.
- Store IO stays tolerant (omit-never-crash) exactly as Phases 1–4.
- `plot.json` is pure JSON (`indent=2, sort_keys=True`); `plot` imports only `campaigns`/`paths`.
- Beats **accrete** (append-only per thread); `status` is replaced. Only open/advanced threads are injected.

---

### Task 1: `store/plot.py` — the plot-thread store

**Files:**
- Create: `backend/src/grimoire/store/plot.py`
- Test: `backend/tests/test_plot_store.py`

**Interfaces:**
- Consumes: `campaigns.campaign_root`.
- Produces:
  - `STATUSES = ("open", "advanced", "closed")`
  - `read(cid) -> dict` (missing ⇒ `{}`)
  - `get(cid, pid) -> dict | None`
  - `set_movement(cid, pid, title, status, beat_text, scene) -> None`
  - `open_threads(cid) -> list[dict]` — each `{"id","title","status","latest_beat"}`, closed excluded, sorted by `(last_scene, id)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_plot_store.py`:

```python
from grimoire.store import campaigns, plot, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert plot.read(cid) == {}


def test_set_movement_creates_and_appends_beats(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "the-map", "The forged map", "open", "Elara got the map.", "s10")
    plot.set_movement(cid, "the-map", "", "advanced", "It's a forgery.", "s12")
    t = plot.get(cid, "the-map")
    assert t["title"] == "The forged map"      # preserved when passed blank
    assert t["status"] == "advanced"
    assert [b["text"] for b in t["beats"]] == ["Elara got the map.", "It's a forgery."]
    assert [b["scene"] for b in t["beats"]] == ["s10", "s12"]
    assert t["last_scene"] == "s12"


def test_set_movement_empty_beat_does_not_append(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "the-map", "The map", "open", "First.", "s1")
    plot.set_movement(cid, "the-map", "The map", "closed", "", "s2")  # no beat
    t = plot.get(cid, "the-map")
    assert t["status"] == "closed" and t["last_scene"] == "s2"
    assert len(t["beats"]) == 1


def test_open_threads_excludes_closed_and_sorts(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "b", "Bee", "open", "beat b", "s2")
    plot.set_movement(cid, "a", "Ay", "advanced", "beat a", "s1")
    plot.set_movement(cid, "z", "Zed", "closed", "done", "s3")
    got = plot.open_threads(cid)
    assert [t["id"] for t in got] == ["a", "b"]  # closed 'z' gone; sorted by last_scene
    assert got[0] == {"id": "a", "title": "Ay", "status": "advanced", "latest_beat": "beat a"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_plot_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.plot'`.

- [ ] **Step 3: Implement `store/plot.py`**

Create `backend/src/grimoire/store/plot.py`:

```python
"""Per-campaign plot threads: open/advanced/closed narrative threads, each with an
ordered list of dated beats. Stored at <campaign>/plot.json. Pure JSON IO, mirrors
relationships.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns

STATUSES = ("open", "advanced", "closed")


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "plot.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get(cid: str, pid: str) -> dict | None:
    return read(cid).get(pid)


def set_movement(cid: str, pid: str, title: str, status: str, beat_text: str, scene: str) -> None:
    data = read(cid)
    thread = data.get(pid) or {"title": "", "status": "open", "beats": [], "last_scene": ""}
    if title.strip():
        thread["title"] = title.strip()
    if not thread.get("title"):
        thread["title"] = pid
    if status in STATUSES:
        thread["status"] = status
    if beat_text.strip():
        thread.setdefault("beats", []).append({"scene": scene, "text": beat_text.strip()})
    thread["last_scene"] = scene
    data[pid] = thread
    _write(cid, data)


def open_threads(cid: str) -> list[dict]:
    items = [(pid, t) for pid, t in read(cid).items() if t.get("status") != "closed"]
    items.sort(key=lambda kt: (kt[1].get("last_scene", ""), kt[0]))
    out = []
    for pid, t in items:
        beats = t.get("beats") or []
        out.append({"id": pid, "title": t.get("title", pid), "status": t.get("status", "open"),
                    "latest_beat": beats[-1]["text"] if beats else ""})
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_plot_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/plot.py backend/tests/test_plot_store.py
git commit -m "feat(plot): plot.json thread store (set_movement/open_threads)"
```

---

### Task 2: `absorb` — parse `plot_movements`, feed the snapshot, extend the instruction

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (imports; `EXTRACT_INSTRUCTION`; `build_prompt`; `parse_output`; new `plot_snapshot`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `plot.STATUSES`, `plot.open_threads`, `paths.slugify`.
- Produces:
  - `parse_output(text)["plot_movements"]` → `list of {"id","title","status","beat"}` (strings stripped; `status` ∈ STATUSES, default `"open"`).
  - `plot_snapshot(cid, sid) -> str` (rendered open/advanced threads; `""` on garble).
  - `build_prompt(transcript, facts, state_snapshot=None, rel_snapshot=None, plot_snapshot=None)`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py`:

```python
def test_parse_output_plot_movements():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "plot_movements": [{"id": "the-map", "title": "The map", "status": "advanced",'
            '   "beat": "It is a forgery."},'
            '  {"title": "New thread", "status": "bogus", "beat": "starts"}]}')
    out = absorb.parse_output(text)
    assert out["plot_movements"] == [
        {"id": "the-map", "title": "The map", "status": "advanced", "beat": "It is a forgery."},
        {"id": "", "title": "New thread", "status": "open", "beat": "starts"}]  # bad status -> open


def test_plot_snapshot_renders_open_threads(monkeypatch, tmp_path):
    from grimoire.store import plot, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", "s12")
    plot.set_movement(cid, "done", "Done thread", "closed", "resolved", "s5")
    snap = absorb.plot_snapshot(cid, sid)
    assert "the-map" in snap and "The map" in snap and "It is a forgery." in snap
    assert "Done thread" not in snap  # closed excluded


def test_plot_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")
    assert absorb.plot_snapshot(cid, sid) == ""
```

Also extend the garbage-tolerance assertion in `test_parse_output_tolerates_garbage` — add `"plot_movements": []` to the expected dict:

```python
def test_parse_output_tolerates_garbage():
    assert absorb.parse_output("no json") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "plot_ or tolerates_garbage"`
Expected: FAIL — `plot_movements`/`plot_snapshot` absent.

- [ ] **Step 3: Add imports**

In `backend/src/grimoire/store/absorb.py`, change the import line:

```python
from . import appearances, campaigns, characters, chronicle, entities, pcs, playstate, relationships
```

to:

```python
from . import (appearances, campaigns, characters, chronicle, entities, pcs, playstate,
               plot, relationships)
from .paths import slugify
```

- [ ] **Step 4: Extend `EXTRACT_INSTRUCTION`**

In `EXTRACT_INSTRUCTION`, immediately before the final `"Write in third person, past tense…"` sentence, insert:

```python
    'and "plot_movements" (list of {"id","title","status","beat"} — for each plot thread '
    "this scene moved: the thread id from the context block to advance or close it, or a "
    'NEW thread (omit "id", give a "title"); "status" is one of open/advanced/closed; '
    '"beat" is one sentence on how this scene moved it; only threads that actually moved). '
```

- [ ] **Step 5: Add the `plot_snapshot` prompt block to `build_prompt`**

Change the signature and body of `build_prompt`. Replace:

```python
def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None) -> list[dict]:
```

with:

```python
def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None, plot_snapshot: str | None = None) -> list[dict]:
```

and immediately after the existing `if rel_snapshot:` block (before `prefix = ...`), add:

```python
    if plot_snapshot:
        head.append("Current plot threads:\n" + plot_snapshot)
```

- [ ] **Step 6: Parse `plot_movements`**

In `parse_output`, immediately before the `return {` statement, add:

```python
    plot_moves = []
    for e in obj.get("plot_movements", []):
        if isinstance(e, dict):
            status = str(e.get("status", "")).strip().lower()
            plot_moves.append({"id": str(e.get("id", "")).strip(),
                               "title": str(e.get("title", "")).strip(),
                               "status": status if status in plot.STATUSES else "open",
                               "beat": str(e.get("beat", "")).strip()})
```

and add this key to the returned dict (after `"bond_changes": ...`):

```python
        "plot_movements": plot_moves,
```

- [ ] **Step 7: Add the `plot_snapshot` helper**

At the end of `backend/src/grimoire/store/absorb.py`, add:

```python
def plot_snapshot(cid: str, sid: str) -> str:
    """Rendered open/advanced plot threads (id + title + status + latest beat) — feeds the
    prompt so the model advances the right thread. Tolerant of a garbled plot.json."""
    try:
        lines = []
        for t in plot.open_threads(cid):
            head = f"{t['id']}: {t['title']} ({t['status']})"
            lines.append(f"{head} — {t['latest_beat']}" if t["latest_beat"] else head)
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — garbled plot.json: omit, don't crash the extraction
        return ""
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "plot_ or tolerates_garbage or build_prompt"`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): parse plot_movements + feed plot snapshot to the prompt"
```

---

### Task 3: `absorb.materialize` — the `plot` StagedEdit kind

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (`materialize`, after the `bond_changes` loop)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `plot.get`, `slugify`.
- Produces: `plot` StagedEdits: `{id: "plot:<pid>", kind: "plot", target:{kind:"plot",id:pid}, label, field:"beat", before, after:<beat>, authored:false, payload:{id,title,status,scene}}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py`:

```python
def test_materialize_plot_new_and_advance(monkeypatch, tmp_path):
    from grimoire.store import plot, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    plot.set_movement(cid, "the-map", "The map", "open", "Elara got it.", "s10")
    parsed = {"plot_movements": [
        {"id": "the-map", "title": "The map", "status": "advanced", "beat": "It is a forgery."},
        {"id": "", "title": "The Duke's debt", "status": "open", "beat": "A creditor asked after Doran."},
        {"id": "", "title": "", "status": "open", "beat": "no id no title"},   # dropped
        {"id": "x", "title": "X", "status": "open", "beat": ""}]}               # empty beat dropped
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    adv = edits["plot:the-map"]
    assert adv["kind"] == "plot" and adv["field"] == "beat" and adv["authored"] is False
    assert adv["before"].startswith("open — Elara got it.")
    assert adv["after"] == "It is a forgery."
    assert adv["payload"] == {"id": "the-map", "title": "The map", "status": "advanced", "scene": sid}
    new = edits["plot:the-dukes-debt"]  # slugified from the title
    assert new["before"] == "" and new["payload"]["title"] == "The Duke's debt"
    assert "plot:x" not in edits and not any(k == "plot:" for k in edits)  # both dropped
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_materialize_plot_new_and_advance -q`
Expected: FAIL — no `plot:*` edits produced.

- [ ] **Step 3: Add the `plot` loop to `materialize`**

In `materialize`, immediately before the final `return out`, add:

```python
    for e in parsed.get("plot_movements", []):
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        mid = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        status = e.get("status", "open")
        cur = plot.get(cid, mid) if mid else None
        if cur:
            pid = mid
            beats = cur.get("beats") or []
            before = (f"{cur.get('status', 'open')} — {beats[-1]['text']}"
                      if beats else cur.get("status", "open"))
        else:
            pid = mid or slugify(title)
            before = ""
        if not pid:
            continue
        disp_title = title or (cur.get("title") if cur else pid)
        out.append({"id": f"plot:{pid}", "kind": "plot",
                    "target": {"kind": "plot", "id": pid},
                    "label": f"{disp_title} — {status}",
                    "field": "beat", "before": before, "after": beat, "authored": False,
                    "payload": {"id": pid, "title": disp_title, "status": status, "scene": sid}})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_materialize_plot_new_and_advance -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): materialize plot movements as an editable-beat plot edit"
```

---

### Task 4: `apply_edits` `plot` branch + wire `plot_snapshot` into the route

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (`apply_edits`)
- Modify: `backend/src/grimoire/routes.py:1127-1129` (the `build_prompt` call in `post_absorb`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `plot.set_movement`.
- Produces: applying a `plot` edit writes `plot.json`; `post_absorb` feeds `plot_snapshot`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_absorb_store.py`:

```python
def test_apply_edits_writes_plot(monkeypatch, tmp_path):
    from grimoire.store import plot
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "plot:the-map", "kind": "plot",
         "target": {"kind": "plot", "id": "the-map"}, "field": "beat",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s12"}}])
    assert applied == ["plot:the-map"]
    t = plot.get(cid, "the-map")
    assert t["status"] == "advanced" and t["last_scene"] == "s12"
    assert t["beats"][-1] == {"scene": "s12", "text": "It is a forgery."}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_apply_edits_writes_plot -q`
Expected: FAIL — the `plot` kind falls through `apply_edits` (`applied == []`).

- [ ] **Step 3: Add the `plot` branch to `apply_edits`**

In `apply_edits`, add a branch before the final `else: continue`:

```python
            elif kind == "plot":
                p = e["payload"]
                plot.set_movement(cid, p["id"], p["title"], p["status"], after, p["scene"])
```

(`after` is already bound at the top of the loop: `after = e.get("after", "")`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_apply_edits_writes_plot -q`
Expected: PASS.

- [ ] **Step 5: Wire `plot_snapshot` into `post_absorb`**

In `backend/src/grimoire/routes.py`, change the `build_prompt` call in `post_absorb`:

```python
    messages = store.absorb.build_prompt(
        store.chronicle.transcript_text(scene["messages"]), facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid))
```

to:

```python
    messages = store.absorb.build_prompt(
        store.chronicle.transcript_text(scene["messages"]), facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid),
        store.absorb.plot_snapshot(cid, sid))
```

- [ ] **Step 6: Run the absorb suite + a quick route import check**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q`
Expected: PASS (all absorb tests).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/routes.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): apply plot edits + feed plot snapshot from post_absorb"
```

---

### Task 5: `context._plot_threads` — the `# Plot threads` section

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (imports; a new `_plot_threads`; one `add(...)` in `_assemble`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `plot.open_threads`.
- Produces: the `# Plot threads` system section (label `"Plot threads"`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_context.py`:

```python
def test_plot_threads_section_injected(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, plot, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", "s12")
    plot.set_movement(cid, "done", "Done", "closed", "resolved", "s5")
    section = dict(context._assemble(cid, sid)["system"])["Plot threads"]
    assert "The map (advanced): It is a forgery." in section
    assert "Done" not in section  # closed excluded


def test_plot_threads_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    assert "Plot threads" not in [l for l, _ in context._assemble(cid, sid)["system"]]


def test_plot_threads_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")
    context._assemble(cid, sid)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k plot_threads`
Expected: FAIL — no `Plot threads` section.

- [ ] **Step 3: Import `plot` in context**

In `backend/src/grimoire/store/context.py`, add `plot` to the `from . import (...)` block (keep alphabetical): change

```python
from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               config, entities, pcs, playstate, relationships, scenes, worlds)
```

to:

```python
from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               config, entities, pcs, playstate, plot, relationships, scenes, worlds)
```

- [ ] **Step 4: Add `_plot_threads` and inject it**

In `backend/src/grimoire/store/context.py`, add this function just above `def _story_so_far(`:

```python
def _plot_threads(cid: str) -> str:
    try:
        lines = []
        for t in plot.open_threads(cid):
            head = f"{t['title']} ({t['status']})"
            lines.append(f"{head}: {t['latest_beat']}" if t["latest_beat"] else head)
        return "# Plot threads\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — garbled plot.json: omit, don't crash the context build
        return ""
```

Then in `_assemble`, immediately after the `add("Story so far", _story_so_far(cid))` line, add:

```python
    add("Plot threads", _plot_threads(cid))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k plot_threads`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat(context): inject # Plot threads (open/advanced) section"
```

---

### Task 6: Frontend `plot` kind + full-suite verification

**Files:**
- Modify: `frontend/src/api/client.ts` (the `StagedEdit` `kind` union)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:** none new — `plot` reuses the existing editable-row render + payload-forwarding save.

- [ ] **Step 1: Run the whole backend suite**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, count ≥ the 441 baseline plus the ~11 new tests. Fix any regression's cause (do not weaken assertions).

- [ ] **Step 2: Add `"plot"` to the `StagedEdit` kind union**

In `frontend/src/api/client.ts`, change:

```ts
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond";
```

to:

```ts
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot";
```

- [ ] **Step 3: Write the failing test**

In `frontend/src/routes/CampaignView.test.tsx`, add (mirror the existing `character_state` review-row test structure):

```ts
test("plot rows are editable and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    edits: [{ id: "plot:the-map", kind: "plot",
      target: { kind: "plot", id: "the-map" }, label: "The map — advanced",
      field: "beat", before: "open — Elara got it.", after: "It is a forgery.",
      authored: false, payload: { id: "the-map", title: "The map", status: "advanced", scene: "s1" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After The map — advanced");
  expect((ta as HTMLTextAreaElement).value).toBe("It is a forgery.");
  fireEvent.change(ta, { target: { value: "It is a clever forgery." } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "plot:the-map", after: "It is a clever forgery.",
        payload: expect.objectContaining({ status: "advanced" }) })]) })));
});
```

- [ ] **Step 4: Run the frontend checks**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx` then `npx tsc -b`
Expected: PASS; `tsc` exits 0.

- [ ] **Step 5: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run`
Expected: PASS (158 baseline + 1).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(review): plot StagedEdit kind — editable beat row with payload"
```

---

## Self-Review

**Spec coverage:**
- `plot.json` store + `set_movement`/`open_threads` → Task 1. ✓
- Extraction `plot_movements` + `plot_snapshot` + `build_prompt` param + `EXTRACT_INSTRUCTION` → Task 2. ✓
- `plot` StagedEdit (editable beat + payload, slug/resolve, drops) → Task 3. ✓
- Apply `plot` branch + `post_absorb` wiring → Task 4. ✓
- `# Plot threads` injection (open/advanced, tolerant) → Task 5. ✓
- Frontend kind union + editable-row/payload vitest → Task 6. ✓
- Absolute status, fed current threads (snapshot) → Task 2 (`plot_snapshot`) + Task 3 (status from parsed movement). ✓
- New-thread ids slugified → Task 3 (`slugify(title)`). ✓

**Placeholder scan:** every code step shows full content; no TBD/TODO; the only lookup-free reference is the existing `character_state` row test that Task 6 mirrors (its structure is shown inline). ✓

**Type consistency:** `set_movement(cid, pid, title, status, beat_text, scene)` identical in Tasks 1/4; `open_threads` item shape `{id,title,status,latest_beat}` identical in Tasks 1/2/5; `plot.STATUSES` defined in Task 1, used in Task 2; `plot_snapshot(cid, sid)` defined in Task 2, called in Task 4; the `plot` StagedEdit `payload {id,title,status,scene}` produced in Task 3 and consumed in Task 4. ✓

## Notes for the executor

- No new endpoint and (beyond the one-line union) no new frontend component: `plot` reuses the editable-textarea row (any kind except `relationship`/`bond`) and the payload-forwarding save.
- `apply_edits` binds `after = e.get("after", "")` at the top of its loop; the `plot` branch uses it directly — do not re-read.
- Keep assertions strict; a pre-existing failure means an implementation slip, not a test to relax. The only pre-existing test that must change is `test_parse_output_tolerates_garbage` (gains `"plot_movements": []`).
