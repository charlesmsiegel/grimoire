# Campaign log ingestion skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable project skill (`.claude/skills/ingest-campaign-log/`) plus a small
Python helper script that turns one already-rewritten scene (transcript + metadata) into a real
scene in a grimoire campaign, running it through grimoire's own absorb pipeline so state,
relationships, and plot accumulate exactly as they would during live play.

**Architecture:** `backend/scripts/ingest_scene.py` is a thin, testable wrapper over existing
`grimoire.store` modules (`campaigns`, `characters`, `entities`, `scenes`, `appearances`,
`chronicle`, `absorb`) plus the real `OpenRouterClient` — no new backend business logic. It reads
one JSON scene descriptor per invocation and is idempotent per scene via a manifest file, so a
multi-scene, multi-session ingestion run can be resumed. `.claude/skills/ingest-campaign-log/SKILL.md`
documents the workflow: how an agent should read a raw log, rewrite it into grimoire's
`**Speaker:**` marker grammar and judge scene boundaries by hand, then drive this script one scene
at a time.

**Tech Stack:** Python 3 (backend/.venv), pytest, existing `grimoire.store` package.

## Global Constraints

- No changes to `grimoire.store` or `grimoire.routes` — this consumes existing store functions
  as-is. If a needed function doesn't exist, that's a signal the plan's understanding of the store
  is wrong; stop and re-check rather than adding new store code.
- Run backend tests with `backend/.venv/Scripts/python.exe -m pytest backend -q` from the repo
  root (per `CLAUDE.md`).
- New characters/locations created by this tool are always **campaign-local**
  (`characters.create_character(campaign_root, ...)` / `entities.create_entity(campaign_root,
  "locations", ...)`) — never written to a World root.
- Every materialized absorb edit is auto-applied; there is no review/approval step in this tool.
- This plan does **not** run the tool against the real "Silver Oath" logs — that's the next,
  separate step once the skill exists and is tested.

---

### Task 1: Campaign setup + ingest manifest

**Files:**
- Create: `backend/scripts/ingest_scene.py`
- Create: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Produces: `ensure_campaign(name: str, world_id: str) -> str` (campaign id — finds an existing
  campaign with this name+world, else creates one). `load_manifest(cid: str) -> dict`,
  `save_manifest(cid: str, data: dict) -> None` (manifest lives at
  `<campaign_root>/ingest_manifest.json`, keyed by an arbitrary scene `key` string the caller
  supplies, e.g. `"file1-scene03"`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ingest_scene.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_scene  # noqa: E402
from grimoire.store import campaigns, worlds  # noqa: E402


def _world(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("ashgrove")


def test_ensure_campaign_creates_once(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid1 = ingest_scene.ensure_campaign("Silver Oath", wid)
    cid2 = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert cid1 == cid2
    assert campaigns.read_campaign(cid1)["meta"]["world"] == wid


def test_manifest_round_trips(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert ingest_scene.load_manifest(cid) == {}
    ingest_scene.save_manifest(cid, {"file1-scene01": {"status": "done", "sid": "001--x"}})
    assert ingest_scene.load_manifest(cid) == {"file1-scene01": {"status": "done", "sid": "001--x"}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingest_scene'` (the file doesn't exist yet).

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/scripts/ingest_scene.py
"""Ingest one rewritten campaign-log scene into a grimoire campaign, running it through
the real absorb pipeline. Built for the ingest-campaign-log skill — see
.claude/skills/ingest-campaign-log/SKILL.md for the end-to-end workflow this drives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import campaigns  # noqa: E402


def ensure_campaign(name: str, world_id: str) -> str:
    for c in campaigns.list_campaigns():
        if c["name"] == name and c["world"] == world_id:
            return c["id"]
    return campaigns.create_campaign(name, world_id)


def _manifest_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "ingest_manifest.json"


def load_manifest(cid: str) -> dict:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(cid: str, data: dict) -> None:
    _manifest_path(cid).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(scripts): campaign setup + manifest for log ingestion"
```

---

### Task 2: Campaign-local character/location creation

**Files:**
- Modify: `backend/scripts/ingest_scene.py`
- Modify: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Consumes: nothing new from Task 1.
- Produces: `ensure_character(croot: Path, spec: dict) -> str` (spec has `"name"`, optional
  `"personality"`/`"description"`; returns the character id, idempotent by
  `slugify(spec["name"])`). `ensure_location(croot: Path, spec: dict) -> str` (spec has `"name"`,
  optional `"notes"`; same idempotency). `resolve_version(croot: Path, kind: str, actor_id: str) ->
  str` (`kind` is `"characters"` or `"pcs"`; returns that actor's default version id).

- [ ] **Step 1: Write the failing tests**

```python
def test_ensure_character_creates_once(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    aid1 = ingest_scene.ensure_character(croot, {"name": "cassian", "personality": "wary, precise"})
    aid2 = ingest_scene.ensure_character(croot, {"name": "cassian"})
    assert aid1 == aid2 == "cassian"
    vid = ingest_scene.resolve_version(croot, "characters", aid1)
    from grimoire.store import characters
    assert characters.read_card(croot, aid1, vid)["data"]["personality"] == "wary, precise"


def test_ensure_location_creates_once(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    eid1 = ingest_scene.ensure_location(croot, {"name": "Thornfield Manor", "notes": "Seat of corvin."})
    eid2 = ingest_scene.ensure_location(croot, {"name": "Thornfield Manor"})
    assert eid1 == eid2 == "thornfield-manor"


def test_resolve_version_for_pc(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, pcs, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    wroot = worlds_store.world_root(wid)
    pcs.create_pc(wroot, "julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    vid = ingest_scene.resolve_version(croot, "pcs", "julian")
    assert vid == "default"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: FAIL — `AttributeError: module 'ingest_scene' has no attribute 'ensure_character'` (and
similarly for `ensure_location`/`resolve_version`).

- [ ] **Step 3: Write the minimal implementation**

```python
# add to backend/scripts/ingest_scene.py, alongside the existing imports:
from pathlib import Path

from grimoire.store import characters, entities  # noqa: E402
from grimoire.store.paths import slugify  # noqa: E402


def ensure_character(croot: Path, spec: dict) -> str:
    target = slugify(spec["name"])
    if target in characters.character_refs(croot):
        return target
    card = characters.blank_card(spec["name"])
    card["data"]["personality"] = spec.get("personality", "")
    card["data"]["description"] = spec.get("description", "")
    cid, _ = characters.create_character(croot, spec["name"], "main", card)
    return cid


def ensure_location(croot: Path, spec: dict) -> str:
    target = slugify(spec["name"])
    existing = {e["id"] for e in entities.list_entities(croot, "locations")}
    if target in existing:
        return target
    return entities.create_entity(croot, "locations", spec["name"], body=spec.get("notes", ""))


def resolve_version(croot: Path, kind: str, actor_id: str) -> str:
    if kind == "pcs":
        from grimoire.store import pcs
        return pcs.read_pc(croot, actor_id)["meta"]["default_version"]
    return characters.read_character(croot, actor_id)["meta"]["default_version"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(scripts): campaign-local character/location creation for log ingestion"
```

---

### Task 3: Scene assembly (transcript, cast, location, date)

**Files:**
- Modify: `backend/scripts/ingest_scene.py`
- Modify: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Consumes: `ensure_character`, `ensure_location`, `resolve_version` (Task 2).
- Produces: `build_scene(cid: str, scene: dict) -> str` (returns the final scene id — may differ
  from the id `scenes.create_scene` returned, since setting the first date renames the file to
  embed it). `scene` dict shape:
  ```python
  {
      "title": "The Reckoning",
      "date": "1818-05-15",              # optional; native calendar string
      "location": "winterbourne-manor",       # optional; existing or just-created location id
      "new_characters": [{"name": "cassian", "personality": "..."}],   # optional
      "new_locations": [{"name": "Thornfield Manor", "notes": "..."}], # optional
      "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "estra-hamilton"}],
      "turns": [
          {"role": "user", "speaker": "julian", "content": "..."},
          {"role": "assistant", "speaker": None, "content": "..."},
          {"role": "assistant", "speaker": "Estra", "content": "..."},
      ],
  }
  ```

- [ ] **Step 1: Write the failing test**

```python
def test_build_scene_writes_transcript_cast_location_date(monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns as campaigns_store, scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    wroot = worlds_store.world_root(wid)
    from grimoire.store import pcs
    pcs.create_pc(wroot, "julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)

    scene = {
        "title": "The Reckoning",
        "date": "1818-05-15",
        "new_locations": [{"name": "winterbourne Manor", "notes": "Family seat."}],
        "location": "winterbourne-manor",
        "new_characters": [{"name": "marisol", "personality": "cruel, controlled"}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "marisol"}],
        "turns": [
            {"role": "assistant", "speaker": None, "content": "*The study is silent.*"},
            {"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""},
            {"role": "user", "speaker": "julian", "content": "\"I have.\""},
        ],
    }
    sid = ingest_scene.build_scene(cid, scene)

    read = scenes.read_scene(cid, sid)
    assert [m["content"] for m in read["messages"]] == [
        "*The study is silent.*", "\"You've grown bold.\"", "\"I have.\""]
    assert read["messages"][1]["speaker"] == "marisol"
    assert read["messages"][2]["role"] == "user"
    assert "1818-05-15" in sid  # first date-set stamps the filename
    cast = {(a["kind"], a["id"]) for a in appearances.scene_cast(cid, sid)}
    assert cast == {("pcs", "julian"), ("characters", "marisol")}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py::test_build_scene_writes_transcript_cast_location_date -v`
Expected: FAIL — `AttributeError: module 'ingest_scene' has no attribute 'build_scene'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add to backend/scripts/ingest_scene.py
from grimoire.store import appearances, scenes  # noqa: E402


def build_scene(cid: str, scene: dict) -> str:
    croot = campaigns.campaign_root(cid)
    for spec in scene.get("new_characters", []):
        ensure_character(croot, spec)
    for spec in scene.get("new_locations", []):
        ensure_location(croot, spec)

    sid = scenes.create_scene(cid, scene["title"])
    if scene.get("date"):
        sid = scenes.set_datetime(cid, sid, scene["date"])["id"]
    if scene.get("location"):
        scenes.set_location(cid, sid, scene["location"])
    for turn in scene["turns"]:
        scenes.append_message(cid, sid, turn["role"], turn["content"], speaker=turn.get("speaker"))
    for actor in scene["characters"]:
        kind, aid = actor["kind"], actor["id"]
        vid = resolve_version(croot, kind, aid)
        appearances.appear(cid, sid, kind, aid, vid, "player" if kind == "pcs" else "npc")
    return sid
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(scripts): assemble a scene (transcript, cast, location, date) for log ingestion"
```

---

### Task 4: Absorb + apply against a fake LLM client

**Files:**
- Modify: `backend/scripts/ingest_scene.py`
- Modify: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Consumes: `build_scene` (Task 3).
- Produces: `async def run_absorb(cid: str, sid: str, client, cfg: dict) -> dict` (returns
  `{"parsed": {...}, "edits": [...]}`, mirroring `POST .../absorb`'s response shape minus the
  dossier-refresh side effect, which this tool intentionally skips — dossiers are a nice-to-have
  the live UI generates, not something bulk import needs). `apply_scene(cid: str, sid: str, parsed:
  dict, edits: list[dict]) -> list[str]` (applies every edit and records chronicle, mirroring `PUT
  .../chronicle`; returns the applied edit ids). `client` is any object with an async
  `complete(messages, model, key) -> str` method — the real `OpenRouterClient` in production, a
  fake in tests.

- [ ] **Step 1: Write the failing test**

```python
import asyncio
import json as json_module


class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def complete(self, messages, model, key):
        self.calls.append((messages, model, key))
        return self.text


def test_run_absorb_and_apply_scene(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, playstate, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})

    scene = {
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    sid = ingest_scene.build_scene(cid, scene)

    fake_text = json_module.dumps({
        "one_line": "marisol needles julian.",
        "summary": "A tense study confrontation.",
        "keywords": ["study", "confrontation"],
        "timeline_events": [{"date": "1818-05-15", "text": "julian confronts marisol."}],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    result = asyncio.run(ingest_scene.run_absorb(cid, sid, client, {"model": "test/model", "openrouter_key": "k"}))
    assert result["parsed"]["one_line"] == "marisol needles julian."
    assert any(e["kind"] == "character_state" for e in result["edits"])

    applied = ingest_scene.apply_scene(cid, sid, result["parsed"], result["edits"])
    assert applied
    st = playstate.read_state(croot, "marisol")
    assert "wary of julian" in st["current_state"]
    assert client.calls[0][1] == "test/model" and client.calls[0][2] == "k"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py::test_run_absorb_and_apply_scene -v`
Expected: FAIL — `AttributeError: module 'ingest_scene' has no attribute 'run_absorb'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add to backend/scripts/ingest_scene.py
from grimoire.store import absorb, chronicle  # noqa: E402


async def run_absorb(cid: str, sid: str, client, cfg: dict) -> dict:
    scene = scenes.read_scene(cid, sid)
    facts = chronicle.scene_facts(cid, sid)
    transcript = chronicle.transcript_text(scene["messages"])
    messages = absorb.build_prompt(
        transcript, facts, absorb.state_snapshot(cid, sid),
        absorb.relationships_snapshot(cid, sid), absorb.plot_snapshot(cid))
    text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    parsed = absorb.parse_output(text)
    edits = absorb.materialize(cid, sid, parsed)
    return {"parsed": parsed, "edits": edits}


def apply_scene(cid: str, sid: str, parsed: dict, edits: list[dict]) -> list[str]:
    facts = chronicle.scene_facts(cid, sid)
    chronicle.absorb(cid, {"id": sid, "one_line": parsed["one_line"], "summary": parsed["summary"],
                           "keywords": parsed["keywords"], **facts})
    chronicle.append_timeline(cid, parsed["timeline_events"])
    scenes.mark_absorbed(cid, sid, parsed["one_line"], parsed["summary"])
    return absorb.apply_edits(cid, edits, sid)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(scripts): run the real absorb pipeline and apply edits for log ingestion"
```

---

### Task 5: Orchestration, CLI, and a two-scene sequential end-to-end test

**Files:**
- Modify: `backend/scripts/ingest_scene.py`
- Modify: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: `async def ingest_one_scene(cid: str, scene: dict, client, cfg: dict) -> dict` (checks
  the manifest for `scene["key"]`; if already `"done"`, returns
  `{"key", "status": "skipped", ...previous record}` without touching the campaign; otherwise runs
  `build_scene` → `run_absorb` → `apply_scene`, records the manifest, and returns
  `{"key", "status": "done", "sid", "one_line", "applied"}`). `main() -> int` — CLI entry point
  with `setup` / `ingest` / `status` subcommands.

- [ ] **Step 1: Write the failing tests**

```python
def test_ingest_one_scene_is_resumable(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})

    scene = {
        "key": "file1-scene01",
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    fake_text = json_module.dumps({
        "one_line": "marisol needles julian.", "summary": "s", "keywords": [],
        "timeline_events": [], "character_state_edits": [], "lore_edits": [],
        "authored_edits": [], "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    cfg = {"model": "test/model", "openrouter_key": "k"}

    first = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, cfg))
    assert first["status"] == "done"
    assert len(client.calls) == 1

    second = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, cfg))
    assert second["status"] == "skipped"
    assert second["sid"] == first["sid"]
    assert len(client.calls) == 1  # no second LLM call


def test_two_scenes_accumulate_state_in_order(monkeypatch, tmp_path):
    """Scene 2's snapshot must see scene 1's applied character-state edit."""
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})
    cfg = {"model": "test/model", "openrouter_key": "k"}

    scene1 = {
        "key": "file1-scene01", "title": "Scene One",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    text1 = json_module.dumps({
        "one_line": "a", "summary": "a", "keywords": [], "timeline_events": [],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene1, FakeClient(text1), cfg))

    captured = {}
    real_snapshot = ingest_scene.absorb.state_snapshot

    def spying_snapshot(cid_, sid_):
        snap = real_snapshot(cid_, sid_)
        captured.update(snap)
        return snap

    monkeypatch.setattr(ingest_scene.absorb, "state_snapshot", spying_snapshot)

    scene2 = {
        "key": "file1-scene02", "title": "Scene Two",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"Still bold, I see.\""}],
    }
    text2 = json_module.dumps({
        "one_line": "b", "summary": "b", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene2, FakeClient(text2), cfg))

    assert any("wary of julian" in v for v in captured.values())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: FAIL — `AttributeError: module 'ingest_scene' has no attribute 'ingest_one_scene'`

- [ ] **Step 3: Write the minimal implementation**

```python
# add to backend/scripts/ingest_scene.py
import argparse
import asyncio

from grimoire.openrouter import OpenRouterClient  # noqa: E402
from grimoire.store import read_config  # noqa: E402


async def ingest_one_scene(cid: str, scene: dict, client, cfg: dict) -> dict:
    manifest = load_manifest(cid)
    key = scene["key"]
    if manifest.get(key, {}).get("status") == "done":
        return {"key": key, "status": "skipped", **manifest[key]}
    sid = build_scene(cid, scene)
    result = await run_absorb(cid, sid, client, cfg)
    applied = apply_scene(cid, sid, result["parsed"], result["edits"])
    manifest[key] = {"status": "done", "sid": sid, "one_line": result["parsed"]["one_line"],
                     "applied": applied}
    save_manifest(cid, manifest)
    return {"key": key, "status": "done", **manifest[key]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a rewritten campaign-log scene into a grimoire campaign.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="create (or find) the target campaign")
    p_setup.add_argument("--world", required=True)
    p_setup.add_argument("--name", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest one scene JSON file")
    p_ingest.add_argument("--campaign", required=True)
    p_ingest.add_argument("--input", required=True, type=Path)

    p_status = sub.add_parser("status", help="print the ingest manifest")
    p_status.add_argument("--campaign", required=True)

    args = ap.parse_args()
    if args.cmd == "setup":
        print(ensure_campaign(args.name, args.world))
        return 0
    if args.cmd == "status":
        print(json.dumps(load_manifest(args.campaign), indent=2, sort_keys=True))
        return 0

    scene = json.loads(args.input.read_text(encoding="utf-8"))
    cfg = read_config()
    if not cfg["openrouter_key"]:
        print("error: OpenRouter key not configured (set it in grimoire's Configuration page)",
              file=sys.stderr)
        return 1
    client = OpenRouterClient()
    result = asyncio.run(ingest_one_scene(args.campaign, scene, client, cfg))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ingest_scene.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all tests pass (existing suite + the 9 new ones)

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(scripts): resumable per-scene ingestion orchestration + CLI"
```

---

### Task 6: The `ingest-campaign-log` skill

**Files:**
- Create: `.claude/skills/ingest-campaign-log/SKILL.md`

**Interfaces:**
- Consumes: `backend/scripts/ingest_scene.py`'s three CLI subcommands (`setup`, `ingest`, `status`)
  and the scene JSON schema from Task 3.
- Produces: nothing further consumes this — it's the terminal deliverable.

- [ ] **Step 1: Write the skill**

```markdown
---
name: ingest-campaign-log
description: Use when turning a raw Claude.ai RP transcript export (and, optionally, an old campaign-skill archive with hand-authored state) into a real grimoire Campaign.
---

# Ingesting a campaign log into grimoire

Turns raw session transcripts into a real grimoire Campaign by driving the app's own absorb
pipeline scene by scene, so character state, relationships, and plot threads accumulate exactly
as they would during live play. Scene segmentation and speaker attribution is **your** judgment
call while reading — this skill does not delegate that to another LLM call.

## Inputs

- A directory of raw logs (Claude.ai exports: `## User` / `## Claude` turns).
- Optionally, an old campaign-skill `.skill` archive (a zip) — unzip it and read `SKILL.md` +
  `state/active_characters/*.md` for roster/premise context and, later, for verification. Never
  copy its `state/` prose directly into grimoire — every fact must come from re-running absorb on
  the actual transcript.
- A target grimoire World id, and a campaign name.

## Workflow

1. **Setup:**
   `backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py setup --world <world-id> --name "<Campaign Name>"`
   Prints the campaign id — use it for every following step.

2. **Per source file, in order** (state is cumulative — files and scenes within them must be
   ingested in story order, never in parallel):

   a. Read forward through the raw transcript. An explicit `<!-- new scene -->` HTML comment (or
      similar authorial marker) is a hard scene break. Otherwise, judge breaks yourself on
      location change, a hard time skip, or a POV shift — the same signals a human GM would use.
      Strip anything that isn't in-fiction content: session-start briefings ("read the project
      documents..."), and session-end commands (`/updateskill`, skill-creator invocations).

   b. Rewrite each turn into grimoire's marker grammar as you go: `**Speaker:** content`, blank
      line between messages. The raw `## Claude` turn mixes narration with every present NPC's
      dialogue — split out `**<Name>:**` for any line that's unambiguously one character acting
      or speaking; leave true omniscient narration under `**Grimoire:**` (speaker `None` in the
      JSON below). The raw `## User` turn is the player character's first-person lines — tag it
      `role: "user", speaker: "<PC name>"`.

   c. For each finished scene, note which characters/locations it needs. Anything not already in
      the World or a prior scene's `new_characters`/`new_locations` goes in this scene's own
      `new_characters`/`new_locations` list — never invent an id yourself, the tool derives one
      from the name (`slugify`).

   d. Write the scene to a JSON file and ingest it:
      ```json
      {
        "key": "file1-scene03",
        "title": "The Reckoning",
        "date": "1818-05-15",
        "location": "winterbourne-manor",
        "new_characters": [{"name": "cassian", "personality": "wary, precise"}],
        "new_locations": [{"name": "Thornfield Manor", "notes": "Seat of corvin."}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "cassian"}],
        "turns": [
          {"role": "assistant", "speaker": null, "content": "*The study is silent.*"},
          {"role": "assistant", "speaker": "cassian", "content": "\"I didn't ask to come here.\""},
          {"role": "user", "speaker": "julian", "content": "\"Neither did I, once.\""}
        ]
      }
      ```
      ```bash
      backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py ingest --campaign <cid> --input scene.json
      ```
      This creates the scene, seats the cast, sets location/date, runs the real absorb LLM call
      against grimoire's configured OpenRouter key/model, and auto-applies every edit it proposes.
      It's a real API call and real spend — there is no dry-run mode.

   e. `key` must be unique and stable across a whole ingestion run (e.g. `"<logfile>-scene<NN>"`).
      Re-running `ingest` with the same `--campaign` and the same `key` is a no-op if that scene
      already completed — check with:
      `backend/.venv/Scripts/python.exe backend/scripts/ingest_scene.py status --campaign <cid>`
      A failed or interrupted run resumes cleanly: fix the problem and re-issue `ingest` for the
      scene that failed and everything after it.

3. **After each source file finishes**, if an old skill archive was provided, compare the
   resulting campaign's state/relationships/plot against its hand-authored `state/` files (prose
   cross-reference — this is a judgment call about whether the story came out right, not a
   mechanical diff) and report anything that looks meaningfully off before moving to the next file.

## Common mistakes

- Treating one raw log file as one scene, or one "session." These exports are Claude.ai
  conversation continuations, not clean session/scene boundaries — always read for actual scene
  breaks.
- Feeding a whole `## Claude` turn through as a single `**Grimoire:**` block when it clearly
  contains one or more NPCs' distinct dialogue — split it, per Workflow step 2b.
- Inventing a character/location id instead of using the exact `slugify` of the `name` you gave it
  in `new_characters`/`new_locations` (lowercase, hyphenated) — `resolve_version` and later scenes'
  `characters` references will silently fail to line up otherwise.
- Running scenes out of order, or re-running an already-`"done"` key expecting it to refresh —
  `ingest_one_scene` treats "done" as final; delete the manifest entry first if a scene genuinely
  needs redoing (this also un-applies nothing — you'd be re-applying on top of the old state).
```

- [ ] **Step 2: Verify frontmatter and structure**

Confirm the file has exactly the `name`/`description` frontmatter fields, `name` uses only
lowercase letters/hyphens, and `description` starts with "Use when" and states triggering
conditions only (no workflow summary) — check by re-reading the written file.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/ingest-campaign-log/SKILL.md
git commit -m "docs(skills): add ingest-campaign-log skill for importing RP session logs"
```

---

## After this plan

The skill and its helper script are built and tested against synthetic scenes, but never run
against the real "Silver Oath" logs. That first real invocation — reading
`Manor Vows 1.md`, rewriting it into scenes, and ingesting them one at a time — is a separate,
follow-up piece of work once this plan is done.
