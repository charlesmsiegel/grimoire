# Scene Chronicle & Recap Spine (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a scene ends, one backend LLM call produces a scene summary + timeline, the user reviews it, and it is stored in an append-only campaign chronicle that the context builder injects into every future scene as a `# Story so far` block.

**Architecture:** A new `store/chronicle.py` owns `chronicle.json` (per-campaign, keyed by scene id) and `timeline.md`, plus the extraction prompt/parse (the LLM call stays in the route layer, exactly like `store/briefs.py`). Two routes: `POST …/absorb` runs the extraction and returns an unsaved **preview**; `PUT …/chronicle` persists the (possibly edited) record. `context._assemble` gains a deterministic, always-on, recency-bounded `# Story so far` section. The frontend adds an **End scene** button that opens a small review panel, and a **Story so far** panel in the inspector.

**Tech Stack:** FastAPI + Pydantic (backend, pytest), Vite/React + TypeScript (frontend, vitest + @testing-library/react). Store is markdown/JSON files under `~/.grimoire`.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`. Every store test starts from that.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Run a single test: append `backend/tests/test_x.py::test_name`.
- Run frontend checks **from `frontend/`**: `npx vitest run` and `npx tsc -b`. (Running vitest from the repo root skips `frontend/vitest.config.ts` and breaks every mock-based test.)
- No new import cycles. `chronicle.py` may import `campaigns`/`paths` at module load; it imports `appearances`/`entities`/`scenes` **inside functions** only. `context.py` may import `chronicle` at module load (chronicle does not import context).
- Store conventions: flat scalars in frontmatter, nested data in a JSON sidecar, prose in markdown. JSON sidecars are written `json.dumps(data, indent=2, sort_keys=True) + "\n"`.
- The LLM client is `OpenRouterClient`; `await client.complete(messages, model, key)` returns the full text. Missing key raises `OpenRouterError("missing_key", …)`; routes guard with `_require_key(cfg)` first.
- Naming: scene records are keyed by scene id (`sid`). Chronicle record shape is fixed (see Task 1) — reuse the exact keys everywhere.

---

## File Structure

**Backend**
- Create `backend/src/grimoire/store/chronicle.py` — chronicle.json + timeline.md IO, deterministic `scene_facts`, `transcript_text`, and the extraction `build_prompt`/`parse_output`.
- Modify `backend/src/grimoire/store/__init__.py` — export `chronicle`.
- Modify `backend/src/grimoire/store/scenes.py` — add `mark_absorbed`.
- Modify `backend/src/grimoire/store/config.py` — add `recap_depth`.
- Modify `backend/src/grimoire/store/context.py` — add the `# Story so far` section.
- Modify `backend/src/grimoire/routes.py` — `GET …/chronicle`, `POST …/scenes/{sid}/absorb`, `PUT …/scenes/{sid}/chronicle` + `ChronicleSave` model.
- Tests: create `backend/tests/test_chronicle_store.py`; extend `test_scene_store.py`, `test_config_store.py`, `test_context.py`, `test_routes.py`.

**Frontend**
- Modify `frontend/src/api/client.ts` — types + `absorbScene`, `saveChronicle`, `getChronicle`.
- Modify `frontend/src/routes/CampaignView.tsx` — End scene button + review panel.
- Modify `frontend/src/components/SceneInspector.tsx` — Story so far panel.
- Modify `frontend/src/index.css` — panel styling.
- Tests: extend `frontend/src/routes/CampaignView.test.tsx`, `frontend/src/components/SceneInspector.test.tsx`.

---

## Task 1: Chronicle store — IO, facts, transcript

**Files:**
- Create: `backend/src/grimoire/store/chronicle.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_chronicle_store.py`

**Interfaces:**
- Consumes: `campaigns.campaign_root`, `paths.now_iso`; inside functions `appearances.scene_cast`, `scenes.get_location_history`, `scenes.get_time_history`, `scenes.ROLE_TO_LABEL`, `entities.read_entity`.
- Produces:
  - `read_chronicle(cid: str) -> dict` — full map keyed by scene id; missing file ⇒ `{}`.
  - `absorb(cid: str, record: dict) -> dict` — insert/replace by `record["id"]`, stamp `absorbed`, write; returns the stored record.
  - `recent(cid: str, n: int) -> list[dict]` — the `n` records with the highest ids (chronological-ish), ascending; `n <= 0` ⇒ `[]`.
  - `append_timeline(cid: str, events: list[dict]) -> None` — append `{"date","text"}` lines; empty ⇒ no-op.
  - `scene_facts(cid: str, sid: str) -> dict` — `{"cast": [ "<kind>/<id>", … ], "location": str, "date": str}`.
  - `transcript_text(messages: list[dict]) -> str` — labeled transcript string.

- [ ] **Step 1: Write the failing IO tests**

Create `backend/tests/test_chronicle_store.py`:

```python
import pytest

from grimoire.store import campaigns, chronicle, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert chronicle.read_chronicle(cid) == {}


def test_absorb_stores_and_stamps(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rec = chronicle.absorb(cid, {"id": "2026-01-01-a", "one_line": "They met.",
                                 "summary": "A met B.", "keywords": ["salt"]})
    assert rec["one_line"] == "They met."
    assert rec["absorbed"]  # timestamp added
    assert chronicle.read_chronicle(cid)["2026-01-01-a"]["summary"] == "A met B."


def test_absorb_replaces_by_id(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "s1", "one_line": "v1", "summary": "", "keywords": []})
    chronicle.absorb(cid, {"id": "s1", "one_line": "v2", "summary": "", "keywords": []})
    data = chronicle.read_chronicle(cid)
    assert len(data) == 1 and data["s1"]["one_line"] == "v2"


def test_recent_orders_by_id_and_bounds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "2026-01-02-b", "one_line": "second", "summary": "", "keywords": []})
    chronicle.absorb(cid, {"id": "2026-01-01-a", "one_line": "first", "summary": "", "keywords": []})
    assert [r["one_line"] for r in chronicle.recent(cid, 5)] == ["first", "second"]
    assert [r["one_line"] for r in chronicle.recent(cid, 1)] == ["second"]
    assert chronicle.recent(cid, 0) == []


def test_append_timeline_writes_lines(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.append_timeline(cid, [{"date": "2026-01-01", "text": "The gate opened."}])
    chronicle.append_timeline(cid, [{"date": "2026-01-02", "text": "It closed."}])
    body = (campaigns.campaign_root(cid) / "timeline.md").read_text(encoding="utf-8")
    assert "The gate opened." in body and "It closed." in body
    chronicle.append_timeline(cid, [])  # no-op, no crash


def test_transcript_text_labels_roles():
    text = chronicle.transcript_text([{"role": "user", "content": "hi"},
                                      {"role": "assistant", "content": "hello"}])
    assert "**You:** hi" in text and "**Grimoire:** hello" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chronicle_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.chronicle'`.

- [ ] **Step 3: Create `chronicle.py`**

```python
"""Per-campaign play chronicle: an append-only fact record of absorbed scenes plus a
running timeline. The recap read-forward reads from here.

<campaign>/chronicle.json — keyed by scene id:
  {"<sid>": {"id","one_line","summary","keywords":[...],"cast":[...],
             "location","date","absorbed"}}
<campaign>/timeline.md — append-only dated lines.

Pure file IO + the extraction prompt/parse (the LLM call lives in the route layer,
mirroring briefs.py). No module-load import of scenes/appearances/entities (cycle-free).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns
from .paths import now_iso


def _chronicle_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "chronicle.json"


def _timeline_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "timeline.md"


def read_chronicle(cid: str) -> dict:
    p = _chronicle_path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def absorb(cid: str, record: dict) -> dict:
    """Insert or replace the record keyed by record['id']; stamp absorption time."""
    data = read_chronicle(cid)
    stored = {**record, "absorbed": now_iso()}
    data[record["id"]] = stored
    _chronicle_path(cid).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stored


def recent(cid: str, n: int) -> list[dict]:
    """The n highest-id (chronological-ish) records, ascending. n <= 0 -> []."""
    if n <= 0:
        return []
    data = read_chronicle(cid)
    return sorted(data.values(), key=lambda r: r["id"])[-n:]


def append_timeline(cid: str, events: list[dict]) -> None:
    if not events:
        return
    p = _timeline_path(cid)
    existing = p.read_text(encoding="utf-8") if p.exists() else "# Timeline\n"
    lines = [f"- **{e.get('date', '')}** {e.get('text', '').strip()}".rstrip()
             for e in events]
    p.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def scene_facts(cid: str, sid: str) -> dict:
    """Deterministic facts the LLM should not have to infer: present cast refs, the
    current location's display name, and the current native datetime."""
    from . import appearances, entities, scenes
    cast = [f"{a['kind']}/{a['id']}" for a in appearances.scene_cast(cid, sid)]
    loc_hist = scenes.get_location_history(cid, sid)
    location = ""
    if loc_hist:
        try:
            location = entities.read_entity(
                campaigns.campaign_root(cid), "locations", loc_hist[-1]
            )["meta"].get("name", loc_hist[-1])
        except entities.EntityNotFound:
            location = loc_hist[-1]
    time_hist = scenes.get_time_history(cid, sid)
    return {"cast": cast, "location": location, "date": time_hist[-1] if time_hist else ""}


def transcript_text(messages: list[dict]) -> str:
    from .scenes import ROLE_TO_LABEL
    return "\n\n".join(
        f"**{ROLE_TO_LABEL.get(m['role'], m['role'])}:** {m['content']}" for m in messages)
```

- [ ] **Step 4: Export `chronicle` from the store barrel**

In `backend/src/grimoire/store/__init__.py`, add `chronicle` to the alphabetical `from . import (...)` block (between `campaigns,` and `context,` — the block already reads `appearances, assets, briefs, campaigns, cards, characters, chub, context,`; insert `chronicle,` after `characters, chub,` so it reads `... characters, chronicle, chub, context,`). Then add `"chronicle",` to the `__all__` list (near `"campaigns",`).

```python
from . import (
    appearances, assets, briefs, campaigns, cards, characters, chronicle, chub,
    context, entities, fetch, greetings, localize, lorebook, pcs, playing, scenes,
    sync, tags, worlds,
)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chronicle_store.py -q`
Expected: PASS (6 passed). `scene_facts` is covered indirectly by the route test in Task 6; the store tests above cover IO + transcript.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/chronicle.py backend/src/grimoire/store/__init__.py backend/tests/test_chronicle_store.py
git commit -m "feat: chronicle store (scene fact record + timeline)"
```

---

## Task 2: Chronicle extraction — prompt + tolerant JSON parse

**Files:**
- Modify: `backend/src/grimoire/store/chronicle.py`
- Test: `backend/tests/test_chronicle_store.py`

**Interfaces:**
- Produces:
  - `build_prompt(transcript: str, facts: dict) -> list[dict]` — system+user messages for `client.complete`.
  - `parse_output(text: str) -> dict` — tolerant parse to `{"one_line","summary","keywords":[...],"timeline_events":[{"date","text"}, …]}`; garbled input ⇒ empty fields, never raises.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_chronicle_store.py`:

```python
def test_build_prompt_includes_facts_and_transcript():
    msgs = chronicle.build_prompt("**You:** hi", {"location": "The Crypt",
                                                  "date": "2026-01-01", "cast": ["characters/seraphine"]})
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The Crypt" in user and "2026-01-01" in user and "seraphine" in user and "**You:** hi" in user


def test_parse_output_extracts_json():
    text = ('Sure!\n```json\n{"one_line": "They met.", "summary": "A met B by the sea.",'
            ' "keywords": ["sea", ""], "timeline_events": [{"date": "2026-01-01", "text": "Met."}]}\n```')
    out = chronicle.parse_output(text)
    assert out == {"one_line": "They met.", "summary": "A met B by the sea.",
                   "keywords": ["sea"],
                   "timeline_events": [{"date": "2026-01-01", "text": "Met."}]}


def test_parse_output_tolerates_garbage():
    assert chronicle.parse_output("no json here") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chronicle_store.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.chronicle' has no attribute 'build_prompt'`.

- [ ] **Step 3: Add the extraction helpers to `chronicle.py`**

Add near the top (after the imports) and body of `chronicle.py`:

```python
EXTRACT_INSTRUCTION = (
    "You are absorbing a completed role-play scene into a campaign chronicle. "
    "Read the transcript and reply with ONLY a JSON object, no prose around it, with keys: "
    '"one_line" (a single-sentence summary of the scene), '
    '"summary" (one self-contained paragraph, readable without the transcript), '
    '"keywords" (a list of significant nouns/concepts, lowercase), and '
    '"timeline_events" (a list of {"date","text"} for concrete datable happenings; '
    "empty list if none). Write in third person, past tense."
)


def build_prompt(transcript: str, facts: dict) -> list[dict]:
    head = []
    if facts.get("location"):
        head.append(f"Location: {facts['location']}")
    if facts.get("date"):
        head.append(f"Date: {facts['date']}")
    if facts.get("cast"):
        head.append("Present: " + ", ".join(facts["cast"]))
    prefix = ("\n".join(head) + "\n\n") if head else ""
    return [{"role": "system", "content": EXTRACT_INSTRUCTION},
            {"role": "user", "content": prefix + transcript}]


def parse_output(text: str) -> dict:
    """Pull the JSON object out of a model reply (tolerant of code fences / prose)."""
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    events = [
        {"date": str(e.get("date", "")).strip(), "text": str(e.get("text", "")).strip()}
        for e in obj.get("timeline_events", []) if isinstance(e, dict)
    ]
    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": events,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chronicle_store.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/chronicle.py backend/tests/test_chronicle_store.py
git commit -m "feat: chronicle scene-extraction prompt + tolerant JSON parse"
```

---

## Task 3: Mark a scene absorbed (scene frontmatter)

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py`
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Produces: `scenes.mark_absorbed(cid: str, sid: str, one_line: str, summary: str) -> None` — writes `one_line`, `summary`, and `done: "true"` into scene frontmatter; bumps `updated`. Missing scene ⇒ `SceneNotFound`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scene_store.py`:

```python
def test_mark_absorbed_writes_frontmatter(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Ending")
    scenes.mark_absorbed(cid, sid, "They parted.", "A and B parted at dawn.")
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["one_line"] == "They parted."
    assert meta["summary"] == "A and B parted at dawn."
    assert meta["done"] == "true"


def test_mark_absorbed_missing_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.mark_absorbed(cid, "nope", "x", "y")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k mark_absorbed`
Expected: FAIL — `AttributeError: module 'grimoire.store.scenes' has no attribute 'mark_absorbed'`.

- [ ] **Step 3: Add `mark_absorbed` to `scenes.py`**

Add after `edit_message` (near the other frontmatter writers):

```python
def mark_absorbed(cid: str, sid: str, one_line: str, summary: str) -> None:
    """Record a scene's absorbed summary into its frontmatter and flag it done."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["one_line"] = one_line
    meta["summary"] = summary
    meta["done"] = "true"
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k mark_absorbed`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat: scenes.mark_absorbed writes summary + done flag"
```

---

## Task 4: `recap_depth` config scalar

**Files:**
- Modify: `backend/src/grimoire/store/config.py`
- Test: `backend/tests/test_config_store.py`

**Interfaces:**
- Produces: `read_config()["recap_depth"]` defaults to `"5"`; round-trips through `write_config(recap_depth=...)`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config_store.py`:

```python
def test_recap_depth_defaults_and_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config
    assert config.read_config()["recap_depth"] == "5"
    config.write_config(recap_depth="3")
    assert config.read_config()["recap_depth"] == "3"
```

(If `test_config_store.py` imports `config` at the top already, drop the inline import.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_config_store.py -q -k recap_depth`
Expected: FAIL — `KeyError: 'recap_depth'`.

- [ ] **Step 3: Add the key to `config.py`**

Add a default constant and wire it into `_CONFIG_KEYS` and the `defaults` dict:

```python
DEFAULT_RECAP_DEPTH = "5"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth",
                "system_prompt", "quote_color", "recap_depth")
```

And in `read_config`, add `"recap_depth": DEFAULT_RECAP_DEPTH` to the `defaults` dict:

```python
    defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "",
                "quote_color": "off", "recap_depth": DEFAULT_RECAP_DEPTH}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_config_store.py -q -k recap_depth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/config.py backend/tests/test_config_store.py
git commit -m "feat: recap_depth config scalar (default 5)"
```

---

## Task 5: `# Story so far` context section

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `chronicle.recent`, `config.read_config`.
- Produces: a labeled system section `("Story so far", "# Story so far\n- …")` inside `_assemble`, so it appears in both `build_messages` and `context_sections`. Always-on; bounded to the last `recap_depth` chronicle entries; omitted when there are none or depth is 0.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py` (this file already builds a campaign + scene; mirror its existing helpers — if it has a `_campaign`/`_scene` helper, reuse it; otherwise this self-contained test works):

```python
def test_story_so_far_section_is_injected(monkeypatch, tmp_path):
    from grimoire.store import campaigns, chronicle, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    chronicle.absorb(cid, {"id": "2026-01-01-past", "one_line": "They first met.",
                           "summary": "A met B.", "keywords": []})
    labels = [label for label, _ in context._assemble(cid, sid)["system"]]
    assert "Story so far" in labels
    text = dict(context._assemble(cid, sid)["system"])["Story so far"]
    assert "They first met." in text and text.startswith("# Story so far")


def test_story_so_far_absent_when_empty(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Story so far" not in [label for label, _ in context._assemble(cid, sid)["system"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k story_so_far`
Expected: FAIL — `assert 'Story so far' in [...]` fails (section not present).

- [ ] **Step 3: Add `chronicle` to context imports and the section**

In `context.py`, add `chronicle` to the top import line:

```python
from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               config, entities, pcs, scenes, worlds)
```

Add a helper (near `_today_block`):

```python
def _story_so_far(cid: str) -> str:
    try:
        depth = max(int(config.read_config().get("recap_depth", "5")), 0)
    except (ValueError, TypeError):
        depth = 5
    lines = []
    for r in chronicle.recent(cid, depth):
        s = (r.get("one_line") or r.get("summary") or "").strip()
        if s:
            lines.append(f"- {s}")
    return "# Story so far\n" + "\n".join(lines) if lines else ""
```

In `_assemble`, add the section call immediately after the `add("Message examples", …)` line and before `add("Today", …)`:

```python
    add("Story so far", _story_so_far(cid))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k story_so_far`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full context suite (guard the characterization test)**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS. If a `build_messages` byte-for-byte characterization test exists and now fails, that is expected — the section legitimately adds `# Story so far` to the system prompt; update that test's expected string to include the new block (only when the scene has chronicle entries; empty campaigns are unaffected).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: inject always-on # Story so far recap into scene context"
```

---

## Task 6: Routes — absorb (preview), save chronicle, list chronicle

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.chronicle.*`, `store.scenes.mark_absorbed`, `_require_scene`, `_campaign_root_or_404`, `_require_key`, `get_openrouter`, `OpenRouterError`.
- Produces three endpoints:
  - `GET /api/campaigns/{cid}/chronicle` → `list[dict]` (recent 50, ascending).
  - `POST /api/campaigns/{cid}/scenes/{sid}/absorb` → preview `{one_line, summary, keywords, timeline_events, cast, location, date}` (not persisted); `400` if the scene has no messages; `409`→`502` on `OpenRouterError`; missing key surfaces as `OpenRouterError` from `complete`.
  - `PUT /api/campaigns/{cid}/scenes/{sid}/chronicle` body `{one_line, summary, keywords, timeline_events}` → persisted record.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py` (reuse the module's `FakeOpenRouterComplete`, `_world`, `_campaign` helpers, and the module-level `store` imported at the top of the file). Seed a message directly via `store.scenes.append_message`:

```python
def test_absorb_returns_preview_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered the crypt.")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"one_line": "They entered.", "summary": "The party entered the crypt.",'
        ' "keywords": ["crypt"], "timeline_events": []}')
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["one_line"] == "They entered." and body["keywords"] == ["crypt"]
    # not persisted yet
    assert client.get(f"/api/campaigns/{cid}/chronicle").json() == []


def test_absorb_empty_scene_is_400(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 400


def test_save_chronicle_persists_and_lists(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": "They entered.", "summary": "In the crypt.",
                         "keywords": ["crypt"],
                         "timeline_events": [{"date": "2026-01-01", "text": "Entered."}]})
    assert r.status_code == 200 and r.json()["one_line"] == "They entered."
    listed = client.get(f"/api/campaigns/{cid}/chronicle").json()
    assert len(listed) == 1 and listed[0]["summary"] == "In the crypt."
    assert store.scenes.read_scene(cid, sid)["meta"]["done"] == "true"  # scene marked done
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "absorb or chronicle"`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Add the `ChronicleSave` model**

Near the other request models in `routes.py` (e.g. after `class RenameScene(BaseModel):`):

```python
class ChronicleSave(BaseModel):
    one_line: str = ""
    summary: str = ""
    keywords: list[str] = []
    timeline_events: list[dict] = []
```

- [ ] **Step 4: Add the three routes**

Place them near the other scene routes (after `post_retry`), so they precede any generic `/{kind}` catch-alls:

```python
@router.get("/campaigns/{cid}/chronicle")
def get_chronicle(cid: str):
    _campaign_root_or_404(cid)
    return store.chronicle.recent(cid, 50)


@router.post("/campaigns/{cid}/scenes/{sid}/absorb")
async def post_absorb(cid: str, sid: str,
                      client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to absorb")
    facts = store.chronicle.scene_facts(cid, sid)
    messages = store.chronicle.build_prompt(
        store.chronicle.transcript_text(scene["messages"]), facts)
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    return {**store.chronicle.parse_output(text), **facts}


@router.put("/campaigns/{cid}/scenes/{sid}/chronicle")
def put_chronicle(cid: str, sid: str, body: ChronicleSave):
    _require_scene(cid, sid)
    facts = store.chronicle.scene_facts(cid, sid)
    record = store.chronicle.absorb(cid, {
        "id": sid, "one_line": body.one_line, "summary": body.summary,
        "keywords": body.keywords, **facts})
    store.chronicle.append_timeline(cid, body.timeline_events)
    store.scenes.mark_absorbed(cid, sid, body.one_line, body.summary)
    return record
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "absorb or chronicle"`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the whole backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: absorb/save/list chronicle routes"
```

---

## Task 7: Frontend API client — types + methods

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: (covered via the component tests in Tasks 8–9)

**Interfaces:**
- Produces:
  - `type TimelineEvent = { date: string; text: string }`
  - `type SceneAbsorb = { one_line: string; summary: string; keywords: string[]; timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string }`
  - `type ChronicleEntry = { id: string; one_line: string; summary: string; keywords: string[]; cast: string[]; location: string; date: string; absorbed: string }`
  - `api.absorbScene(cid, sid) -> Promise<SceneAbsorb>`
  - `api.saveChronicle(cid, sid, body) -> Promise<ChronicleEntry>`
  - `api.getChronicle(cid) -> Promise<ChronicleEntry[]>`

- [ ] **Step 1: Add the types**

Near the other exported types in `client.ts` (e.g. after `SceneContext`):

```ts
export type TimelineEvent = { date: string; text: string };
export type SceneAbsorb = {
  one_line: string; summary: string; keywords: string[];
  timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string;
};
export type ChronicleEntry = {
  id: string; one_line: string; summary: string; keywords: string[];
  cast: string[]; location: string; date: string; absorbed: string;
};
```

- [ ] **Step 2: Add the methods**

Inside the `export const api = { … }` object (near `editMessage`):

```ts
  absorbScene: (cid: string, sid: string) =>
    request<SceneAbsorb>("POST", `/api/campaigns/${cid}/scenes/${sid}/absorb`),
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[]; timeline_events: TimelineEvent[] }) =>
    request<ChronicleEntry>("PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: PASS (no errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: chronicle API client types + methods"
```

---

## Task 8: End scene button + review panel (CampaignView)

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `api.absorbScene`, `api.saveChronicle`, `type SceneAbsorb`.
- Produces: an **End scene** button in the header that fetches a preview and shows an editable review panel; **Save** persists via `saveChronicle` and bumps the inspector `ctxKey`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/CampaignView.test.tsx` (add `absorbScene`, `saveChronicle`, `getChronicle` to the `vi.mock("../api/client")` object and to `beforeEach` defaults first):

In the `vi.mock("../api/client", …)` `api` object, add:
```ts
    absorbScene: vi.fn(), saveChronicle: vi.fn(), getChronicle: vi.fn(),
```
In `beforeEach`, add:
```ts
  (api.absorbScene as any).mockResolvedValue({
    one_line: "They met.", summary: "A met B.", keywords: ["salt"],
    timeline_events: [], cast: [], location: "", date: "" });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t" });
  (api.getChronicle as any).mockResolvedValue([]);
```
Then add the test:

```ts
test("End scene fetches a preview, edits, and saves the chronicle", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi"); // scene loaded → activeId set → button enabled
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const summary = await screen.findByLabelText("Scene summary");
  expect((summary as HTMLTextAreaElement).value).toContain("A met B.");
  fireEvent.change(summary, { target: { value: "Edited summary." } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ summary: "Edited summary.", one_line: "They met." })));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx -t "End scene"`
Expected: FAIL — no "End scene" button found.

- [ ] **Step 3: Add state + handlers to `CampaignView`**

Add imports at the top (extend the existing `../api/client` import):
```ts
import { api, type SceneMeta, type Message, type SceneAbsorb } from "../api/client";
```

Add state (next to the other `useState` calls):
```ts
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  const [absorbing, setAbsorbing] = useState(false);
```

Add handlers (near `retry`):
```ts
  async function endScene() {
    if (!activeId || absorbing) return;
    setAbsorbing(true);
    setError(null);
    try {
      setAbsorb(await api.absorbScene(cid, activeId));
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setAbsorbing(false);
    }
  }

  async function saveAbsorb() {
    if (!absorb || !activeId) return;
    await api.saveChronicle(cid, activeId, {
      one_line: absorb.one_line, summary: absorb.summary,
      keywords: absorb.keywords, timeline_events: absorb.timeline_events });
    setAbsorb(null);
    setCtxKey((n) => n + 1);
  }
```

- [ ] **Step 4: Add the button and the review panel to the JSX**

Replace the header line:
```tsx
        <div className="campaign-header">{name}</div>
```
with:
```tsx
        <div className="campaign-header">
          <span>{name}</span>
          <button className="end-scene" onClick={endScene}
                  disabled={!activeId || absorbing || busy}>
            {absorbing ? "Ending…" : "End scene"}
          </button>
        </div>
        {absorb && (
          <div className="absorb-panel">
            <h4>Review scene summary</h4>
            <label className="field-hint" htmlFor="absorb-oneline">One line</label>
            <input id="absorb-oneline" aria-label="Scene one-line" value={absorb.one_line}
                   onChange={(e) => setAbsorb({ ...absorb, one_line: e.target.value })} />
            <label className="field-hint" htmlFor="absorb-summary">Summary</label>
            <textarea id="absorb-summary" aria-label="Scene summary" rows={5} value={absorb.summary}
                      onChange={(e) => setAbsorb({ ...absorb, summary: e.target.value })} />
            {absorb.timeline_events.length > 0 && (
              <ul className="absorb-timeline">
                {absorb.timeline_events.map((t, i) => (
                  <li key={i}><strong>{t.date}</strong> {t.text}</li>
                ))}
              </ul>
            )}
            <div className="form-actions">
              <button className="subtle" onClick={() => setAbsorb(null)}>Cancel</button>
              <button className="primary" onClick={saveAbsorb}>Save summary</button>
            </div>
          </div>
        )}
```

- [ ] **Step 5: Add minimal styling**

Append to `frontend/src/index.css`:

```css
.campaign-header { display: flex; align-items: center; justify-content: space-between; }
.campaign-header .end-scene { font-size: 0.85rem; }
.absorb-panel { border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem;
  margin: 0.5rem 0; background: var(--surface); }
.absorb-panel input, .absorb-panel textarea { width: 100%; margin-bottom: 0.5rem; }
.absorb-timeline { margin: 0.25rem 0 0.5rem; padding-left: 1rem; font-size: 0.85rem; }
```

(If `--surface`/`--border`/`--accent` are not the theme's variable names, use whatever the file already uses for panels — grep `index.css` for an existing `.side-section` or `.banner` and match it.)

- [ ] **Step 6: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: PASS.

- [ ] **Step 7: Typecheck and commit**

Run (from `frontend/`): `npx tsc -b`
Expected: PASS.

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/index.css frontend/src/routes/CampaignView.test.tsx
git commit -m "feat: End scene review panel that saves the chronicle"
```

---

## Task 9: Story-so-far panel in the inspector

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx`
- Test: `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Consumes: `api.getChronicle`, `type ChronicleEntry`.
- Produces: a read-only "Story so far" `.side-section` listing recent chronicle one-liners (most recent first), refetched on `refreshKey`.

- [ ] **Step 1: Write the failing test**

In `SceneInspector.test.tsx`, add `getChronicle: vi.fn()` to the `vi.mock("../api/client")` `api` object, and in `beforeEach` add:
```ts
  (api.getChronicle as any).mockResolvedValue([
    { id: "s0", one_line: "They first met.", summary: "", keywords: [],
      cast: [], location: "", date: "", absorbed: "t" }]);
```
Then add the test:

```ts
test("shows the story-so-far recap", async () => {
  renderInspector();
  await screen.findByText("Story so far");
  await screen.findByText("They first met.");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx -t "story-so-far"`
Expected: FAIL — "Story so far" not found.

- [ ] **Step 3: Add the recap fetch + panel to `SceneInspector`**

Extend the client import:
```ts
import { api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry } from "../api/client";
```

Add state (with the other `useState` calls):
```ts
  const [recap, setRecap] = useState<ChronicleEntry[]>([]);
```

In the `useEffect` keyed on `[cid, sid, refreshKey]`, add:
```ts
    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
```

Add the panel as the first `.side-section` inside the `<aside>` (before "Active characters"), rendering only when non-empty:
```tsx
      {recap.length > 0 && (
        <div className="side-section">
          <h4>Story so far</h4>
          {[...recap].reverse().map((r) => (
            <div className="field-hint" key={r.id}>{r.one_line || r.summary}</div>
          ))}
        </div>
      )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the full frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: PASS (all green). If any other test now renders `SceneInspector` without a `getChronicle` mock and errors, add `getChronicle: vi.fn()` + a `mockResolvedValue([])` default to that test's client mock (only `CampaignView.test.tsx` embeds it, and Task 8 already added the mock there).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx
git commit -m "feat: story-so-far recap panel in the scene inspector"
```

---

## Final verification

- [ ] **Backend:** `backend/.venv/Scripts/python.exe -m pytest backend -q` → all pass.
- [ ] **Frontend (from `frontend/`):** `npx vitest run` → all pass; `npx tsc -b` → no errors.

---

## Self-Review notes (spec coverage)

- **Chronicle (fact record, never compacted):** Tasks 1–2, 6 — `chronicle.json` append/replace, no compaction path exists.
- **Single deterministic-primed extraction call:** Task 2 (`build_prompt` seeds cast/location/date) + Task 6 (`post_absorb` calls `complete` once).
- **Auto-then-review (summary only, this phase):** Task 6 splits preview (`POST absorb`, not persisted) from persist (`PUT chronicle`); Task 8 is the review UI.
- **Always-on, recency-bounded `# Story so far`:** Task 5 (`recap_depth`-bounded, omitted when empty) + Task 4 (config scalar).
- **Timeline:** Tasks 1 (`append_timeline`) + 6 (persisted on save).
- **Scene "done" marker + summary mirror:** Task 3.
- **Read-forward surfaced to the user:** Task 9 (inspector recap) — the context injection (Task 5) is what the model sees.
- **Out of Phase 1 (deferred to later phases, correctly absent here):** character/lore state edits, relationships, knowledge, plot threads, suggested-next-scenes, campaign-vs-base diff view.
