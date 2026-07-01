# Scene State Write-Back (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At End scene, the same one extraction call also proposes how present characters and touched lore evolved; the user reviews each as a before→after diff; approved edits are written into the campaign's copies.

**Architecture:** A new `store/absorb.py` owns the (now richer) single extraction — prompt, tolerant parse, diff **materialization** (JSON → before/after `StagedEdit`s), and **apply** (dispatch by kind). A new `store/playstate.py` owns per-character `state.md` (the `current_state` snapshot). `chronicle.py` reverts to chronicle+timeline IO. `POST …/absorb` returns the Phase-1 preview plus `edits`; `PUT …/chronicle` grows an `edits` field and applies the approved subset atomically. `context._assemble` injects a `# Character state` block for present NPCs.

**Tech Stack:** FastAPI + Pydantic (pytest), Vite/React + TS (vitest). Store = markdown/JSON under `~/.grimoire`.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` (single test: append `path::name`). The shell may sit in `frontend/`; use the absolute venv path `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` if a relative call fails.
- Run frontend **from `frontend/`**: `npx vitest run` and `npx tsc -b`.
- No new import cycles. `playstate`/`absorb` import `campaigns`/`characters`/`entities`/`appearances`/`chronicle` at module load; `context` imports `playstate` at module load (playstate does not import context).
- `current_state` is a **snapshot** (full rewrite each absorb), never a log. Discrete events belong to `timeline_events`, not `current_state`.
- Nothing is written by `POST …/absorb` (preview only). Only `PUT …/chronicle` writes.
- `StagedEdit` shape is fixed and reused verbatim backend→TS: `{id, kind, target:{kind,id}, label, field, before, after, authored}`. `kind` ∈ `"character_state" | "lore" | "authored"`.
- **authored_edits target character card fields only** (`description`/`personality`/`scenario`) — lore evolves via `lore_edits`. (Refinement of the spec: authored is characters-only.)
- Apply is best-effort per edit: a missing/broken target is skipped, never raises.

---

## File Structure

- Create `backend/src/grimoire/store/playstate.py` — `state.md` read/write.
- Create `backend/src/grimoire/store/absorb.py` — extraction prompt/parse (moved from chronicle + extended), `state_snapshot`, `materialize`, `apply_edits`.
- Modify `backend/src/grimoire/store/chronicle.py` — remove the extraction helpers (revert to IO).
- Modify `backend/src/grimoire/store/__init__.py` — export `absorb`, `playstate`.
- Modify `backend/src/grimoire/store/context.py` — `# Character state` section.
- Modify `backend/src/grimoire/routes.py` — rewire `post_absorb`; extend `ChronicleSave` + `put_chronicle`.
- Modify `frontend/src/api/client.ts` — `StagedEdit`, `SceneAbsorb.edits`, `saveChronicle` edits.
- Modify `frontend/src/routes/CampaignView.tsx` — edits checklist in the review panel.
- Tests: create `test_playstate_store.py`, `test_absorb_store.py`; modify `test_chronicle_store.py`, `test_context.py`, `test_routes.py`, `CampaignView.test.tsx`.

---

## Task 1: `playstate.py` — per-character state.md

**Files:**
- Create: `backend/src/grimoire/store/playstate.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_playstate_store.py`

**Interfaces:**
- Produces:
  - `state_path(root: Path, cid: str) -> Path` → `<root>/characters/<cid>/state.md`.
  - `read_state(root, cid) -> dict | None` → `{"current_state": str, "updated": str}` or `None` if absent.
  - `write_state(root, cid, current_state: str) -> None` — frontmatter `{updated}` + body = snapshot.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_playstate_store.py`:

```python
from grimoire.store import playstate, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.world_root(worlds.create_world("W"))


def test_read_missing_is_none(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert playstate.read_state(root, "seraphine") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "Wounded left arm; travels with the party.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Wounded left arm; travels with the party."
    assert st["updated"]


def test_write_replaces_snapshot(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "v1")
    playstate.write_state(root, "seraphine", "v2")
    assert playstate.read_state(root, "seraphine")["current_state"] == "v2"
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playstate_store.py -q`
Expected: FAIL — `cannot import name 'playstate'`.

- [ ] **Step 3: Create `playstate.py`**

```python
"""Per-character campaign play-state: a `current_state` snapshot stored beside the
character copy at <campaign-or-world root>/characters/<cid>/state.md. Snapshot only —
rewritten each absorb (discrete events live in the chronicle timeline). Mirrors briefs.py.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso


def state_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "state.md"


def read_state(root: Path, cid: str) -> dict | None:
    p = state_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"current_state": body.strip(), "updated": meta.get("updated", "")}


def write_state(root: Path, cid: str, current_state: str) -> None:
    p = state_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"updated": now_iso()}, current_state.strip() + "\n"),
                 encoding="utf-8")
```

- [ ] **Step 4: Export from the barrel**

In `backend/src/grimoire/store/__init__.py`, add `playstate` to the `from . import (...)` block (alphabetical: after `pcs,` → `pcs, playing, playstate, scenes,`) and add `"playstate",` to `__all__` (near `"playing"`).

- [ ] **Step 5: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playstate_store.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/playstate.py backend/src/grimoire/store/__init__.py backend/tests/test_playstate_store.py
git commit -m "feat: playstate store (per-character current_state snapshot)"
```

---

## Task 2: `absorb.py` — move + extend the extraction

Moves `EXTRACT_INSTRUCTION`/`build_prompt`/`parse_output` out of `chronicle.py` into `absorb.py`, extends the instruction+parse to the three edit lists, adds a `state_snapshot`, and rewires the route. The `POST …/absorb` response is unchanged this task (still summary+facts, no `edits` yet — that arrives in Task 3), so behavior is preserved.

**Files:**
- Create: `backend/src/grimoire/store/absorb.py`
- Modify: `backend/src/grimoire/store/chronicle.py` (remove the 3 helpers + `EXTRACT_INSTRUCTION`)
- Modify: `backend/src/grimoire/store/__init__.py` (export `absorb`)
- Modify: `backend/src/grimoire/routes.py` (`post_absorb` calls `absorb.*`, passes snapshot)
- Modify: `backend/tests/test_chronicle_store.py` (delete the 3 moved tests)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `chronicle.transcript_text`, `chronicle.scene_facts`, `playstate.read_state`, `appearances.scene_cast`, `characters.read_character`, `campaigns.campaign_root`.
- Produces:
  - `EXTRACT_INSTRUCTION: str`
  - `build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None) -> list[dict]`
  - `parse_output(text: str) -> dict` → `{one_line, summary, keywords, timeline_events, character_state_edits:[{id,current_state}], lore_edits:[{id,append}], authored_edits:[{id,field,text}]}`
  - `state_snapshot(cid: str, sid: str) -> dict` → `{character_name: current_state}` for present NPCs with state.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_absorb_store.py`:

```python
from grimoire.store import absorb, campaigns, playstate, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def test_build_prompt_includes_facts_transcript_and_state():
    msgs = absorb.build_prompt("**You:** hi",
                               {"location": "The Crypt", "date": "2026-01-01", "cast": ["characters/seraphine"]},
                               {"Seraphine": "Wary of the party."})
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The Crypt" in user and "seraphine" in user and "**You:** hi" in user
    assert "Seraphine" in user and "Wary of the party." in user


def test_parse_output_extracts_summary_and_edit_lists():
    text = ('```json\n{"one_line": "x", "summary": "y", "keywords": ["k"],'
            ' "timeline_events": [{"date": "d", "text": "t"}],'
            ' "character_state_edits": [{"id": "seraphine", "current_state": "hurt"}],'
            ' "lore_edits": [{"id": "salt-cathedral", "append": "now flooded"}],'
            ' "authored_edits": [{"id": "seraphine", "field": "personality", "text": "colder"}]}\n```')
    out = absorb.parse_output(text)
    assert out["one_line"] == "x" and out["timeline_events"] == [{"date": "d", "text": "t"}]
    assert out["character_state_edits"] == [{"id": "seraphine", "current_state": "hurt"}]
    assert out["lore_edits"] == [{"id": "salt-cathedral", "append": "now flooded"}]
    assert out["authored_edits"] == [{"id": "seraphine", "field": "personality", "text": "colder"}]


def test_parse_output_tolerates_garbage():
    assert absorb.parse_output("no json") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": []}
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q`
Expected: FAIL — `cannot import name 'absorb'`.

- [ ] **Step 3: Create `absorb.py`**

```python
"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer (mirrors briefs.py).
"""

from __future__ import annotations

import json

from . import appearances, campaigns, characters, chronicle, entities, playstate

EXTRACT_INSTRUCTION = (
    "You are absorbing a completed role-play scene into a campaign chronicle and "
    "evolving its records. Read the transcript and reply with ONLY a JSON object, no "
    "prose around it, with keys: "
    '"one_line" (a one-sentence summary), "summary" (one self-contained paragraph), '
    '"keywords" (list of significant nouns/concepts, lowercase), '
    '"timeline_events" (list of {"date","text"} for concrete datable HAPPENINGS; [] if none), '
    '"character_state_edits" (list of {"id","current_state"} — for each present character '
    "whose standing condition changed, the FULL rewritten snapshot of who they are now, "
    "dropping what is no longer true; standing conditions only, not events), "
    '"lore_edits" (list of {"id","append"} — a paragraph to add to a lore/location entry), '
    'and "authored_edits" (list of {"id","field","text"} — ONLY when a character\'s core '
    "card field (description/personality/scenario) fundamentally and durably changed; rare). "
    "Write in third person, past tense. Use the ids given in the context block."
)


def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None) -> list[dict]:
    head = []
    if facts.get("location"):
        head.append(f"Location: {facts['location']}")
    if facts.get("date"):
        head.append(f"Date: {facts['date']}")
    if facts.get("cast"):
        head.append("Present: " + ", ".join(facts["cast"]))
    if state_snapshot:
        head.append("Current character state:\n" +
                    "\n".join(f"- {name}: {s}" for name, s in state_snapshot.items()))
    prefix = ("\n".join(head) + "\n\n") if head else ""
    return [{"role": "system", "content": EXTRACT_INSTRUCTION},
            {"role": "user", "content": prefix + transcript}]


def _obj(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        obj = {}
    return obj if isinstance(obj, dict) else {}


def _events(obj: dict) -> list[dict]:
    return [{"date": str(e.get("date", "")).strip(), "text": str(e.get("text", "")).strip()}
            for e in obj.get("timeline_events", []) if isinstance(e, dict)]


def parse_output(text: str) -> dict:
    obj = _obj(text)

    def _list(key, fields):
        out = []
        for e in obj.get(key, []):
            if isinstance(e, dict):
                out.append({f: str(e.get(f, "")).strip() for f in fields})
        return out

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _events(obj),
        "character_state_edits": _list("character_state_edits", ("id", "current_state")),
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
    }


def state_snapshot(cid: str, sid: str) -> dict:
    """Present NPCs' existing current_state, keyed by display name (feeds the prompt)."""
    croot = campaigns.campaign_root(cid)
    out: dict[str, str] = {}
    for a in appearances.scene_cast(cid, sid):
        if a["role"] != "npc" or a["kind"] != "characters":
            continue
        st = playstate.read_state(croot, a["id"])
        if st and st["current_state"]:
            try:
                name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = st["current_state"]
    return out
```

- [ ] **Step 4: Remove the moved helpers from `chronicle.py`**

Delete `EXTRACT_INSTRUCTION`, `build_prompt`, and `parse_output` from `backend/src/grimoire/store/chronicle.py` (they now live in `absorb.py`). Leave `read_chronicle`/`absorb`/`recent`/`append_timeline`/`scene_facts`/`transcript_text` intact.

- [ ] **Step 5: Delete the 3 moved tests from `test_chronicle_store.py`**

Remove `test_build_prompt_includes_facts_and_transcript`, `test_parse_output_extracts_json`, and `test_parse_output_tolerates_garbage` from `backend/tests/test_chronicle_store.py` (their successors live in `test_absorb_store.py`).

- [ ] **Step 6: Export `absorb` from the barrel**

In `__init__.py`, add `absorb` to the `from . import (...)` block (alphabetical: first entry → `absorb, appearances, assets, …`) and `"absorb",` to `__all__`.

- [ ] **Step 7: Rewire `post_absorb` in `routes.py`**

Replace the body of `post_absorb` that builds the prompt/parse:

```python
    facts = store.chronicle.scene_facts(cid, sid)
    messages = store.absorb.build_prompt(
        store.chronicle.transcript_text(scene["messages"]), facts,
        store.absorb.state_snapshot(cid, sid))
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    parsed = store.absorb.parse_output(text)
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"], **facts}
```

- [ ] **Step 8: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (the moved tests now live in `test_absorb_store.py`; the route test still passes — same preview shape).

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/store/chronicle.py backend/src/grimoire/store/__init__.py backend/src/grimoire/routes.py backend/tests/test_absorb_store.py backend/tests/test_chronicle_store.py
git commit -m "refactor: move+extend extraction into absorb.py (edit lists + state snapshot)"
```

---

## Task 3: `materialize` — parsed edits → before/after StagedEdits

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Modify: `backend/src/grimoire/routes.py` (`post_absorb` returns `edits`)
- Test: `backend/tests/test_absorb_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `materialize(cid: str, sid: str, parsed: dict) -> list[dict]` → `StagedEdit`s:
  `{id, kind, target:{kind,id}, label, field, before, after, authored}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_absorb_store.py`:

```python
def _char(root, name):
    from grimoire.store import characters
    card = characters.blank_card(name)
    card["data"]["personality"] = "aloof"
    return characters.create_character(root, name, "main", card)[0]  # (cid, vid) -> cid


def test_materialize_builds_before_after(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    entities.create_entity(croot, "lore", "Salt Cathedral", body="A ruined cathedral.")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wary of the party.")
    parsed = {
        "character_state_edits": [{"id": ch, "current_state": "Now travels with them."}],
        "lore_edits": [{"id": "salt-cathedral", "append": "Now flooded."}],
        "authored_edits": [{"id": ch, "field": "personality", "text": "guardedly loyal"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["kind"] == "character_state" and cs["before"] == "Wary of the party." \
        and cs["after"] == "Now travels with them." and cs["authored"] is False
    lore = edits["lore:salt-cathedral"]
    assert lore["before"] == "A ruined cathedral." and lore["after"].endswith("Now flooded.")
    auth = edits[f"authored:{ch}:personality"]
    assert auth["authored"] is True and auth["before"] == "aloof" and auth["after"] == "guardedly loyal"


def test_materialize_skips_unknown_targets(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "S")
    parsed = {"character_state_edits": [{"id": "ghost", "current_state": "x"}],
              "lore_edits": [{"id": "nope", "append": "y"}],
              "authored_edits": [{"id": "ghost", "field": "personality", "text": "z"}]}
    assert absorb.materialize(cid, sid, parsed) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k materialize`
Expected: FAIL — `AttributeError: ... 'materialize'`.

- [ ] **Step 3: Add `materialize` + helpers to `absorb.py`**

```python
_CARD_FIELDS = ("description", "personality", "scenario")


def _char_name(croot, cid: str) -> str:
    try:
        return characters.read_character(croot, cid)["meta"].get("name", cid)
    except characters.CharacterNotFound:
        return cid


def _entity_kind(croot, eid: str) -> str | None:
    for kind in ("lore", "locations"):
        try:
            entities.read_entity(croot, kind, eid)
            return kind
        except entities.EntityNotFound:
            continue
    return None


def materialize(cid: str, sid: str, parsed: dict) -> list[dict]:
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []

    for e in parsed.get("character_state_edits", []):
        char_id, after = e.get("id", ""), (e.get("current_state", "") or "").strip()
        if not char_id or not after:
            continue
        try:
            characters.read_character(croot, char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        out.append({"id": f"character_state:{char_id}", "kind": "character_state",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — current state",
                    "field": "current_state",
                    "before": st["current_state"] if st else "", "after": after,
                    "authored": False})

    for e in parsed.get("lore_edits", []):
        eid, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not eid or not append:
            continue
        kind = _entity_kind(croot, eid)
        if not kind:
            continue
        before = entities.read_entity(croot, kind, eid)["body"].strip()
        after = (before + "\n\n" + append).strip()
        name = entities.read_entity(croot, kind, eid)["meta"].get("name", eid)
        out.append({"id": f"lore:{eid}", "kind": "lore", "target": {"kind": kind, "id": eid},
                    "label": f"{name} — {kind}", "field": "body",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            before = characters.read_card(croot, char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append({"id": f"authored:{char_id}:{field}", "kind": "authored",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — {field} (card edit)",
                    "field": field, "before": before, "after": text, "authored": True})

    return out
```

- [ ] **Step 4: Return `edits` from `post_absorb`**

In `routes.py` `post_absorb`, change the return to include materialized edits:

```python
    parsed = store.absorb.parse_output(text)
    edits = store.absorb.materialize(cid, sid, parsed)
    return {"one_line": parsed["one_line"], "summary": parsed["summary"],
            "keywords": parsed["keywords"], "timeline_events": parsed["timeline_events"],
            **facts, "edits": edits}
```

- [ ] **Step 5: Add a route test asserting absorb returns edits and persists nothing**

Append to `backend/tests/test_routes.py`:

```python
def test_absorb_returns_edits_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    croot = store.campaigns.campaign_root(cid)
    ch = store.characters.create_character(croot, "Seraphine", "main", store.characters.blank_card("Seraphine"))[0]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")  # campaign-local NPC
    store.scenes.append_message(cid, sid, "user", "We fought.")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],'
        f' "character_state_edits": [{{"id": "{ch}", "current_state": "hurt"}}],'
        ' "lore_edits": [], "authored_edits": []}')
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["edits"][0]["kind"] == "character_state" and body["edits"][0]["after"] == "hurt"
    assert store.playstate.read_state(croot, ch) is None  # not persisted
```

- [ ] **Step 6: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py backend/tests/test_routes.py -q -k "materialize or absorb"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/routes.py backend/tests/test_absorb_store.py backend/tests/test_routes.py
git commit -m "feat: materialize proposed edits into before/after StagedEdits"
```

---

## Task 4: `# Character state` context injection

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `playstate.read_state`, `characters.read_character`, the `cast` already computed in `_assemble`.
- Produces: an always-on `("Character state", "# Character state\n<Name>: <current_state>…")` section for present NPC characters that have state; omitted when none; tolerant of errors.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def test_character_state_section_injected(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card("Seraphine")
    ch = characters.create_character(croot, "Seraphine", "main", card)
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wounded; travels with the party.")
    system = dict(context._assemble(cid, sid)["system"])
    assert "Character state" in system
    assert "Seraphine: Wounded; travels with the party." in system["Character state"]


def test_character_state_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Character state" not in [l for l, _ in context._assemble(cid, sid)["system"]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k character_state`
Expected: FAIL (section absent).

- [ ] **Step 3: Implement in `context.py`**

Add `playstate` to the top import block (`from . import (appearances, briefs, calendars, campaigns, characters, chronicle, config, entities, pcs, playstate, scenes, worlds)`).

Add a helper near `_story_so_far`:

```python
def _character_state(croot, cast) -> str:
    try:
        lines = []
        for a in cast:
            if a["role"] != "npc" or a["kind"] != "characters":
                continue
            st = playstate.read_state(croot, a["id"])
            if st and st["current_state"]:
                try:
                    name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
                except characters.CharacterNotFound:
                    name = a["id"]
                lines.append(f"{name}: {st['current_state']}")
        return "# Character state\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return ""
```

In `_assemble`, add the section right after the `add("Character descriptions", …)` line:

```python
    add("Character state", _character_state(croot, cast))
```

- [ ] **Step 4: Run to verify it passes + full context suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS (existing tests unaffected — no present NPC has state in them).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: inject # Character state (present NPCs' current_state) into context"
```

---

## Task 5: `apply_edits` + PUT chronicle applies approved edits

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Modify: `backend/src/grimoire/routes.py` (`ChronicleSave.edits`, `put_chronicle`)
- Test: `backend/tests/test_absorb_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `apply_edits(cid: str, edits: list[dict]) -> list[str]` — applies each approved `StagedEdit` by kind; returns applied `id`s; best-effort (skips missing/broken targets).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_absorb_store.py`:

```python
def test_apply_edits_writes_each_kind(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    applied = absorb.apply_edits(cid, [
        {"id": f"character_state:{ch}", "kind": "character_state",
         "target": {"kind": "characters", "id": ch}, "field": "current_state", "after": "Loyal now."},
        {"id": "lore:salt-cathedral", "kind": "lore",
         "target": {"kind": "lore", "id": "salt-cathedral"}, "field": "body", "after": "Flooded."},
        {"id": f"authored:{ch}:personality", "kind": "authored",
         "target": {"kind": "characters", "id": ch}, "field": "personality", "after": "guarded"},
    ])
    assert set(applied) == {f"character_state:{ch}", "lore:salt-cathedral", f"authored:{ch}:personality"}
    assert playstate.read_state(croot, ch)["current_state"] == "Loyal now."
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."
    assert characters.read_card(croot, ch, "main")["data"]["personality"] == "guarded"


def test_apply_edits_skips_missing_target(monkeypatch, tmp_path):
    # lore/authored targets must exist; a missing one is skipped (best-effort).
    # (character_state writes unconditionally, so it is not a "missing target" case.)
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "lore:nope", "kind": "lore",
         "target": {"kind": "lore", "id": "nope"}, "field": "body", "after": "x"}])
    assert applied == []
```

This assumes `entities.update_entity` raises on a missing entity (it does — it requires the entity to exist). If it instead created one, the test would catch that regression.

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k apply_edits`
Expected: FAIL — no `apply_edits`.

- [ ] **Step 3: Add `apply_edits` to `absorb.py`**

```python
def apply_edits(cid: str, edits: list[dict]) -> list[str]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: a missing or
    broken target is skipped. Returns the ids actually applied."""
    croot = campaigns.campaign_root(cid)
    applied: list[str] = []
    for e in edits:
        try:
            kind, target, after = e["kind"], e["target"], e.get("after", "")
            if kind == "character_state":
                playstate.write_state(croot, target["id"], after)
            elif kind == "lore":
                entities.update_entity(croot, target["kind"], target["id"], body=after)
            elif kind == "authored":
                vid = appearances.locked_version(cid, "characters", target["id"])
                card = characters.read_card(croot, target["id"], vid)
                card["data"][e["field"]] = after
                characters.update_version(croot, target["id"], vid, card)
            else:
                continue
            applied.append(e["id"])
        except Exception:  # noqa: BLE001 — best-effort per edit
            continue
    return applied
```

- [ ] **Step 4: Extend `ChronicleSave` and `put_chronicle` in `routes.py`**

Add `edits: list[dict] = []` to `ChronicleSave`. In `put_chronicle`, after `mark_absorbed`, apply and report:

```python
    applied = store.absorb.apply_edits(cid, body.edits)
    return {**record, "applied": applied}
```

- [ ] **Step 5: Add a route test that the approved subset is written**

Append to `backend/tests/test_routes.py`:

```python
def test_put_chronicle_applies_approved_edits(client):
    _, cid = _campaign(client)
    wid = client.get(f"/api/campaigns/{cid}").json()["meta"]["world"]
    ch = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "version_name": "main"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
        "edits": [{"id": f"character_state:{ch}", "kind": "character_state",
                   "target": {"kind": "characters", "id": ch}, "field": "current_state", "after": "Loyal."}]})
    assert r.json()["applied"] == [f"character_state:{ch}"]
    assert store.playstate.read_state(store.campaigns.campaign_root(cid), ch)["current_state"] == "Loyal."
```

- [ ] **Step 6: Run tests + full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/routes.py backend/tests/test_absorb_store.py backend/tests/test_routes.py
git commit -m "feat: apply approved edits on chronicle save (state/lore/authored)"
```

---

## Task 6: Frontend API client — StagedEdit types + edits

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces:
  - `type StagedEdit = { id: string; kind: "character_state" | "lore" | "authored"; target: { kind: string; id: string }; label: string; field: string; before: string; after: string; authored: boolean }`
  - `SceneAbsorb` gains `edits: StagedEdit[]`.
  - `saveChronicle` body gains `edits: StagedEdit[]`.

- [ ] **Step 1: Add the type and extend `SceneAbsorb`**

After `SceneAbsorb`/`ChronicleEntry` in `client.ts`:

```ts
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
};
```

Add `edits: StagedEdit[];` to the `SceneAbsorb` type.

- [ ] **Step 2: Extend the `saveChronicle` body type**

```ts
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[] }) =>
    request<ChronicleEntry & { applied: string[] }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body),
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: FAIL — `CampaignView.tsx`'s `saveChronicle` call now lacks `edits` (fixed in Task 7). This is expected; proceed to Task 7 before committing. (Do not commit a red typecheck; Tasks 6+7 commit together.)

---

## Task 7: Review panel — edits checklist + Save wiring

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `type StagedEdit`, `absorb.edits`, `api.saveChronicle` (now requires `edits`).
- Produces: an edits checklist below the summary; Save sends approved rows with final `after`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/CampaignView.test.tsx`. First extend the `absorbScene` mock in `beforeEach` to include an edit:

```ts
  (api.absorbScene as any).mockResolvedValue({
    one_line: "They met.", summary: "A met B.", keywords: ["salt"], timeline_events: [],
    cast: [], location: "", date: "",
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] });
```

Then the test:

```ts
test("End scene review sends approved edits with the summary", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Seraphine — current state");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({
      edits: [expect.objectContaining({ id: "character_state:seraphine", after: "Loyal now." })] })));
});

test("unchecking an edit excludes it from the save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByLabelText("Approve Seraphine — current state"));
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [] })));
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx -t "approved edits"`
Expected: FAIL — no edit row rendered.

- [ ] **Step 3: Add edit-row state + rendering to `CampaignView`**

Extend the client import: `import { api, type SceneMeta, type Message, type SceneAbsorb, type StagedEdit } from "../api/client";`

Add state next to `absorb`:

```ts
  const [editRows, setEditRows] = useState<(StagedEdit & { approved: boolean })[]>([]);
```

In `endScene`, after `setAbsorb(a)` (rename the fetched value to `a`):

```ts
  async function endScene() {
    if (!activeId || absorbing) return;
    setAbsorbing(true);
    setError(null);
    try {
      const a = await api.absorbScene(cid, activeId);
      setAbsorb(a);
      setEditRows(a.edits.map((e) => ({ ...e, approved: true })));
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setAbsorbing(false);
    }
  }
```

Update `saveAbsorb` to send approved edits and clear rows:

```ts
  async function saveAbsorb() {
    if (!absorb || !activeId) return;
    await api.saveChronicle(cid, activeId, {
      one_line: absorb.one_line, summary: absorb.summary, keywords: absorb.keywords,
      timeline_events: absorb.timeline_events,
      edits: editRows.filter((e) => e.approved).map(({ approved, ...e }) => e) });
    setAbsorb(null);
    setEditRows([]);
    setCtxKey((n) => n + 1);
  }
```

In the review panel JSX, add the edits checklist between the timeline block and the `.form-actions`:

```tsx
            {editRows.length > 0 && (
              <div className="absorb-edits">
                <h5>Proposed changes</h5>
                {editRows.map((e, i) => (
                  <div className={"absorb-edit" + (e.authored ? " authored" : "")} key={e.id}>
                    <label>
                      <input type="checkbox" aria-label={`Approve ${e.label}`} checked={e.approved}
                             onChange={() => setEditRows((rows) => rows.map((r, j) =>
                               j === i ? { ...r, approved: !r.approved } : r))} />
                      {e.label}{e.authored ? " · card edit" : ""}
                    </label>
                    {e.before && <div className="absorb-before">{e.before}</div>}
                    <textarea aria-label={`After ${e.label}`} rows={2} value={e.after}
                              onChange={(ev) => setEditRows((rows) => rows.map((r, j) =>
                                j === i ? { ...r, after: ev.target.value } : r))} />
                  </div>
                ))}
              </div>
            )}
```

- [ ] **Step 4: Add styling**

Append to `frontend/src/index.css`:

```css
.absorb-edits { margin-top: 8px; border-top: 1px solid var(--muted); padding-top: 8px; }
.absorb-edits h5 { margin: 0 0 6px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.absorb-edit { margin-bottom: 8px; }
.absorb-edit.authored { border-left: 2px solid var(--accent); padding-left: 8px; }
.absorb-edit label { display: flex; align-items: center; gap: 6px; }
.absorb-before { color: var(--muted); font-size: 13px; text-decoration: line-through; margin: 2px 0; }
.absorb-edit textarea { width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 6px; font-family: var(--font-body); }
```

- [ ] **Step 5: Run the tests + typecheck**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx` then `npx tsc -b`
Expected: PASS; tsc clean (Task 6's `saveChronicle` edits requirement is now satisfied).

- [ ] **Step 6: Commit Tasks 6 + 7 together**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignView.tsx frontend/src/index.css frontend/src/routes/CampaignView.test.tsx
git commit -m "feat: review checklist for proposed state/lore/authored edits"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all pass.
- [ ] Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b` → all pass, no type errors.

---

## Self-Review notes (spec coverage)

- **Extraction grows, one call:** Tasks 2 (parse edit lists + state snapshot) + 3 (materialize) + route.
- **Snapshot semantics / events-to-timeline:** enforced by `EXTRACT_INSTRUCTION` wording (Task 2); `current_state` is fully replaced by `write_state` (Task 1).
- **Uniform before→after diffs; lore append pre-computed into after:** `materialize` (Task 3).
- **Atomic reviewed apply; nothing before approval:** `POST /absorb` returns edits without writing (Task 3 test asserts state.md still `None`); `PUT /chronicle` applies approved subset (Task 5).
- **Dedicated state (state.md); authored rare & flagged:** Tasks 1, 3 (`authored: true`), 7 (visual flag).
- **`# Character state` injection, tolerant:** Task 4.
- **Modules:** `absorb.py` (2,3,5), `playstate.py` (1); `chronicle.py` reverts (2).
- **Deferred correctly (not in plan):** knowledge, relationships, plot, PC-state, voice_drift, campaign-vs-base view, the timeline re-append Minor.
