# Absorb: extract new characters, locations, and lore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the "End scene" (absorb) LLM pass so it also proposes brand-new characters, locations, and lore entries found in the transcript, reviewable/editable in the existing staged-edit panel, with creation-on-approval, auto-cast, auto-link, and a persisted Stable Diffusion prompt.

**Architecture:** Three new keys (`new_characters`, `new_locations`, `new_lore`) added to the single absorb JSON schema; parsed the same tolerant way as existing lists; materialized into staged edits of three new `kind`s (deduped against existing records); applied by creating the real record and, for characters/locations, doing one side effect (cast into the scene / link as its location). The frontend's existing generic edit-row renderer and save/apply plumbing are reused untouched except for a few new conditionally-rendered fields per row.

**Tech Stack:** FastAPI + Jinja2 templates (backend), Vite/React + TypeScript (frontend), pytest, vitest.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests from `frontend/`: `npx vitest run`. Run type-check from `frontend/`: `npx tsc -b`. (Must run from `frontend/`, not the repo root, or `vitest.config.ts` is skipped and `globals` is disabled, failing every mock-based test.)
- New character/location/lore records are created in the **campaign's copy-on-write root** (`campaigns.campaign_root(cid)`), never the master world — matching every other absorb-applied edit.
- No new Pydantic model changes are needed anywhere in `routes.py` — `ChronicleSave.edits: list[dict]` and `EntityCreate`/`EntityUpdate` are untouched; the absorb pipeline calls `store.entities`/`store.characters` functions directly.
- `sd_prompt` is plain text with no image-generation side effect anywhere in this plan.

---

### Task 1: `sd_prompt` field on generic entities

**Files:**
- Modify: `backend/src/grimoire/store/entities.py:70-100` (`create_entity`, `update_entity`)
- Test: `backend/tests/test_entities_store.py`

**Interfaces:**
- Produces: `entities.create_entity(root, kind, name, body="", keys="", owners="", sd_prompt="") -> str` and `entities.update_entity(root, kind, eid, name=None, body=None, keys=None, owners=None, sd_prompt=None) -> None`. `sd_prompt` is stored/read as a plain frontmatter meta key, omitted from meta when empty (mirrors `keys`/`owners` exactly).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_entities_store.py` (after `test_owners_round_trip`, before `test_owners_absent_when_empty` — anywhere at top level is fine, but keep it near the other round-trip tests):

```python
def test_sd_prompt_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "locations", "The Crypt", "cold", sd_prompt="a dark crypt")
    got = entities.read_entity(tmp_path, "locations", eid)
    assert got["meta"]["sd_prompt"] == "a dark crypt"
    entities.update_entity(tmp_path, "locations", eid, sd_prompt="an even darker crypt")
    assert entities.read_entity(tmp_path, "locations", eid)["meta"]["sd_prompt"] == "an even darker crypt"
    # entities without sd_prompt read as empty string, and it's omitted from meta (mirrors keys/owners)
    e2 = entities.create_entity(tmp_path, "locations", "No Prompt")
    assert entities.read_entity(tmp_path, "locations", e2)["meta"].get("sd_prompt", "") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py::test_sd_prompt_round_trip -v`
Expected: FAIL with `TypeError: create_entity() got an unexpected keyword argument 'sd_prompt'`

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/entities.py`, change:

```python
def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")
```

to:

```python
def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "",
                  sd_prompt: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    if sd_prompt:
        meta["sd_prompt"] = sd_prompt
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
    sd_prompt: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    if sd_prompt is not None:
        meta["sd_prompt"] = sd_prompt
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py -v`
Expected: all PASS (existing tests still pass; new one passes)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/tests/test_entities_store.py
git commit -m "feat(entities): add sd_prompt field to generic entities"
```

---

### Task 2: Absorb prompt + `parse_output` for new_characters/new_locations/new_lore

**Files:**
- Modify: `templates/absorb/system.j2`
- Modify: `backend/src/grimoire/store/absorb.py:17-99` (`_int05`, add `_truthy`, `parse_output`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `absorb.parse_output(text)` return dict gains three keys: `new_characters: list[{"name","description","sd_prompt"}]`, `new_locations: list[{"name","body","keys","sd_prompt","current_setting": bool}]`, `new_lore: list[{"name","body","keys"}]`. Later tasks (`materialize`) consume these exact shapes.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py` (near the other `parse_output` tests, e.g. after `test_parse_output_relationship_and_bond_lists`):

```python
def test_parse_output_new_entities():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "new_characters": [{"name": "Old Bram", "description": "W++ block", "sd_prompt": "an old man"}],'
            ' "new_locations": [{"name": "The Crypt", "body": "cold", "keys": "crypt",'
            '   "sd_prompt": "a dark crypt", "current_setting": true}],'
            ' "new_lore": [{"name": "Salt Pact", "body": "an old pact", "keys": "pact"}]}')
    out = absorb.parse_output(text)
    assert out["new_characters"] == [{"name": "Old Bram", "description": "W++ block", "sd_prompt": "an old man"}]
    assert out["new_locations"] == [{"name": "The Crypt", "body": "cold", "keys": "crypt",
                                     "sd_prompt": "a dark crypt", "current_setting": True}]
    assert out["new_lore"] == [{"name": "Salt Pact", "body": "an old pact", "keys": "pact"}]


def test_parse_output_new_locations_current_setting_defaults_false():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "new_locations": [{"name": "The Crypt", "body": "cold", "keys": ""}]}')
    out = absorb.parse_output(text)
    assert out["new_locations"] == [
        {"name": "The Crypt", "body": "cold", "keys": "", "sd_prompt": "", "current_setting": False}]
```

Update the existing `test_parse_output_tolerates_garbage` (it asserts an exact dict, which must now include the three new keys):

```python
def test_parse_output_tolerates_garbage():
    assert absorb.parse_output("no json") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
        "new_characters": [], "new_locations": [], "new_lore": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_parse_output_new_entities backend/tests/test_absorb_store.py::test_parse_output_new_locations_current_setting_defaults_false backend/tests/test_absorb_store.py::test_parse_output_tolerates_garbage -v`
Expected: FAIL — `KeyError: 'new_characters'` (or dicts not equal, missing keys)

- [ ] **Step 3: Update the prompt template**

Replace the entire contents of `templates/absorb/system.j2` with:

```
You are absorbing a completed role-play scene into a campaign chronicle and evolving its records. Read the transcript and reply with ONLY a JSON object, no prose around it, with keys: "one_line" (a one-sentence summary), "summary" (one self-contained paragraph), "keywords" (list of significant nouns/concepts, lowercase), "timeline_events" (list of {"date","text"} for concrete datable HAPPENINGS; [] if none), "character_state_edits" (list of {"id","current_state","knows","suspects"} — for each present character whose standing snapshot changed, the FULL rewritten snapshot: "current_state" is their standing condition, "knows" is what they now hold as certain, "suspects" is what they believe but have not confirmed; drop what is no longer true, standing facts only (not a running log). Use "" for a field that does not apply), "lore_edits" (list of {"id","append"} — a paragraph to add to a lore/location entry), "authored_edits" (list of {"id","field","text"} — ONLY when a character's core card field (description/personality/scenario) fundamentally and durably changed; rare), "relationship_deltas" (list of {"from","to","trust","affection","tension","note"} — for each directed pair whose feelings changed, the FULL updated values; use the "<kind>:<id>" tokens from the context block; trust/affection/tension are 0-5), "bond_changes" (list of {"a","b","type"} — a shared relationship type for a pair), "plot_movements" (list of {"id","title","status","beat"} — for each plot thread this scene moved: the thread id from the context block to advance or close it, or a NEW thread (omit "id", give a "title"); "status" is one of open/advanced/closed; "beat" is one sentence on how this scene moved it; only threads that actually moved), "new_characters" (list of {"name","description","sd_prompt"} — ONLY for a person who is named and speaks or acts in the scene but has NO existing character record, i.e. is not already one of the ids in the "Present:" context line; "description" must be a complete W++ block, e.g. [character("Name") { Species("...") Age("...") Occupation("...") Personality("...") Appearance("...") }], covering everything the scene reveals; "sd_prompt" is a comma-separated Stable Diffusion prompt describing their appearance; propose one only when the scene gives enough material for a real entry, not for every passing name), "new_locations" (list of {"name","body","keys","sd_prompt","current_setting"} — for a named place mentioned that has no existing location record; "body" is a short descriptive paragraph; "keys" is a comma-separated list of words that should trigger this entry in future scenes; "sd_prompt" is a suggested Stable Diffusion prompt for the location; "current_setting" is true for at most the ONE new location that is where this scene's action actually took place, false otherwise), and "new_lore" (list of {"name","body","keys"} — for a new faction, item, piece of history, or concept mentioned that isn't a person or a place; same "keys" convention as above). Write in third person, past tense. Use the ids given in the context block.
```

- [ ] **Step 4: Update `absorb.py`**

Add a `_truthy` helper right after `_int05` in `backend/src/grimoire/store/absorb.py`:

```python
def _int05(v) -> int:
    try:
        return max(0, min(5, int(v)))
    except (ValueError, TypeError):
        return 0


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")
```

In `parse_output`, add the three extractions right before the `return` statement, and add the three keys to the returned dict:

```python
    new_characters = _list("new_characters", ("name", "description", "sd_prompt"))

    new_locations = []
    for e in obj.get("new_locations", []):
        if not isinstance(e, dict):
            continue
        new_locations.append({
            "name": _str(e, "name"), "body": _str(e, "body"), "keys": _str(e, "keys"),
            "sd_prompt": _str(e, "sd_prompt"), "current_setting": _truthy(e.get("current_setting")),
        })

    new_lore = _list("new_lore", ("name", "body", "keys"))

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _list("timeline_events", ("date", "text")),
        "character_state_edits": cs_edits,
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
        "relationship_deltas": rel_deltas,
        "bond_changes": _list("bond_changes", ("a", "b", "type")),
        "plot_movements": plot_moves,
        "new_characters": new_characters,
        "new_locations": new_locations,
        "new_lore": new_lore,
    }
```

(This replaces the existing `return { ... "plot_movements": plot_moves, }` block — same dict, three keys added at the end, plus the three new local variables computed just above it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add templates/absorb/system.j2 backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): prompt + parse new_characters/new_locations/new_lore"
```

---

### Task 3: `materialize` staged edits for new characters/locations/lore

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (imports; `materialize`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `parsed["new_characters"]`, `parsed["new_locations"]`, `parsed["new_lore"]` (Task 2's shapes).
- Produces: `materialize()` output list gains rows of `kind` `"new_character"` / `"new_location"` / `"new_lore"`, each:
  ```python
  {"id": f"{prefix}:{candidate_id}", "kind": prefix,
   "target": {"kind": <"characters"|"locations"|"lore">, "id": ""},
   "label": <str>, "field": <"description"|"body">,
   "before": "", "after": <str>, "authored": False,
   "payload": {"name": str, ...}}
  ```
  where `prefix` is `"new_character"`, `"new_location"`, or `"new_lore"`. `target.id` is always `""` (the record doesn't exist yet). Task 4 (`apply_edits`) consumes this exact shape.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py` (after `test_materialize_tolerates_garbled_plot`):

```python
def test_materialize_new_character_creates_staged_edit(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [
        {"name": "Old Bram", "description": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]",
         "sd_prompt": "an old innkeeper, weathered face"}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    e = edits["new_character:old-bram"]
    assert e["kind"] == "new_character" and e["target"] == {"kind": "characters", "id": ""}
    assert e["label"] == "New character — Old Bram" and e["field"] == "description"
    assert e["before"] == "" and "Old Bram" in e["after"] and e["authored"] is False
    assert e["payload"] == {"name": "Old Bram", "sd_prompt": "an old innkeeper, weathered face"}


def test_materialize_new_character_drops_existing_name_collision(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [{"name": "seraphine", "description": "x", "sd_prompt": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_new_character_drops_blank_name_or_description(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [
        {"name": "", "description": "x", "sd_prompt": ""},
        {"name": "Nobody", "description": "", "sd_prompt": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_new_locations_and_lore(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Old Dock")
    sid = scenes.create_scene(cid, "S")
    parsed = {
        "new_locations": [
            {"name": "Old Dock", "body": "dup", "keys": "", "sd_prompt": "", "current_setting": False},
            {"name": "The Crypt", "body": "A cold crypt.", "keys": "crypt", "sd_prompt": "a dark crypt",
             "current_setting": True},
        ],
        "new_lore": [{"name": "Salt Pact", "body": "An old pact.", "keys": "pact"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    assert "new_location:old-dock" not in edits          # dedup: name collides with existing entity
    loc = edits["new_location:the-crypt"]
    assert loc["kind"] == "new_location" and loc["target"] == {"kind": "locations", "id": ""}
    assert loc["field"] == "body" and loc["after"] == "A cold crypt." and loc["authored"] is False
    assert loc["payload"] == {"name": "The Crypt", "keys": "crypt", "sd_prompt": "a dark crypt",
                              "current_setting": True}
    lore = edits["new_lore:salt-pact"]
    assert lore["kind"] == "new_lore" and lore["payload"] == {"name": "Salt Pact", "keys": "pact"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -k new_character_creates_staged or new_character_drops or new_locations_and_lore -v`
Expected: FAIL — `KeyError` (edits dict missing the expected ids; materialize doesn't emit them yet)

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/absorb.py`, add `scenes` to the module import:

```python
from . import (appearances, campaigns, changes, characters, chronicle, entities, pcs,
               playstate, plot, relationships, scenes)
```

In `materialize`, insert this block right before the final `return out` (after the existing `plot_movements` loop):

```python
    existing_char_names = {c["name"].strip().lower() for c in characters.list_characters(croot)}
    for e in parsed.get("new_characters", []):
        name = (e.get("name", "") or "").strip()
        description = (e.get("description", "") or "").strip()
        if not name or not description:
            continue
        if name.lower() in existing_char_names:
            continue
        candidate_id = slugify(name)
        try:
            characters.read_character(croot, candidate_id)
            continue  # id already taken -- treat as the same character
        except characters.CharacterNotFound:
            pass
        out.append({"id": f"new_character:{candidate_id}", "kind": "new_character",
                    "target": {"kind": "characters", "id": ""},
                    "label": f"New character — {name}", "field": "description",
                    "before": "", "after": description, "authored": False,
                    "payload": {"name": name, "sd_prompt": e.get("sd_prompt", "")}})

    for kind, parsed_key, prefix, label_noun in (
        ("locations", "new_locations", "new_location", "location"),
        ("lore", "new_lore", "new_lore", "lore entry"),
    ):
        existing_names = {ent["name"].strip().lower() for ent in entities.list_entities(croot, kind)}
        for e in parsed.get(parsed_key, []):
            name = (e.get("name", "") or "").strip()
            body = (e.get("body", "") or "").strip()
            if not name or not body:
                continue
            if name.lower() in existing_names:
                continue
            candidate_id = slugify(name)
            try:
                entities.read_entity(croot, kind, candidate_id)
                continue
            except entities.EntityNotFound:
                pass
            payload = {"name": name, "keys": e.get("keys", "")}
            if kind == "locations":
                payload["sd_prompt"] = e.get("sd_prompt", "")
                payload["current_setting"] = e.get("current_setting", False)
            out.append({"id": f"{prefix}:{candidate_id}", "kind": prefix,
                        "target": {"kind": kind, "id": ""},
                        "label": f"New {label_noun} — {name}", "field": "body",
                        "before": "", "after": body, "authored": False,
                        "payload": payload})

    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): materialize staged edits for new characters/locations/lore"
```

---

### Task 4: `apply_edits` creates the records (+ auto-cast, auto-link)

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (`_BROWSABLE_KINDS`, `apply_edits`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: staged edits of `kind` `"new_character"`/`"new_location"`/`"new_lore"` from Task 3, applied via the existing `apply_edits(cid, edits, sid=None)`.
- Produces: for `new_character`, a real character created in the campaign root with `data["description"]` and `data["extensions"]["sd_prompt"]` set, cast as an NPC into scene `sid` (when `sid` is given). For `new_location`, a real location entity with `keys`/`sd_prompt`, auto-linked as the scene's location only when `payload["current_setting"]` is true AND the scene has no location history yet. For `new_lore`, a real lore entity. All three now count as "browsable" for `changes.json`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py` (after `test_apply_edits_authored_rejects_non_card_field`):

```python
def test_apply_edits_new_character_creates_and_casts_npc(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied = absorb.apply_edits(cid, [
        {"id": "new_character:old-bram", "kind": "new_character",
         "target": {"kind": "characters", "id": ""}, "field": "description",
         "after": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]",
         "payload": {"name": "Old Bram", "sd_prompt": "an old innkeeper"}}], sid)
    assert applied == ["new_character:old-bram"]
    new_char = next(c for c in characters.list_characters(croot) if c["name"] == "Old Bram")
    card = characters.read_card(croot, new_char["id"], "default")
    assert card["data"]["description"] == "[character(\"Old Bram\") { Occupation(\"innkeep\") }]"
    assert card["data"]["extensions"]["sd_prompt"] == "an old innkeeper"
    assert appearances.is_appeared(cid, "characters", new_char["id"])


def test_apply_edits_new_character_without_sid_skips_casting(monkeypatch, tmp_path):
    from grimoire.store import characters
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    applied = absorb.apply_edits(cid, [
        {"id": "new_character:old-bram", "kind": "new_character",
         "target": {"kind": "characters", "id": ""}, "field": "description", "after": "x",
         "payload": {"name": "Old Bram", "sd_prompt": ""}}])  # no sid
    assert applied == ["new_character:old-bram"]
    assert any(c["name"] == "Old Bram" for c in characters.list_characters(croot))


def test_apply_edits_new_location_auto_links_empty_scene(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied = absorb.apply_edits(cid, [
        {"id": "new_location:the-crypt", "kind": "new_location",
         "target": {"kind": "locations", "id": ""}, "field": "body", "after": "A cold crypt.",
         "payload": {"name": "The Crypt", "keys": "crypt", "sd_prompt": "a dark crypt",
                     "current_setting": True}}], sid)
    assert applied == ["new_location:the-crypt"]
    got = entities.read_entity(croot, "locations", "the-crypt")
    assert got["meta"]["sd_prompt"] == "a dark crypt" and got["meta"]["keys"] == "crypt"
    assert scenes.get_location_history(cid, sid) == ["the-crypt"]


def test_apply_edits_new_location_leaves_existing_location_alone(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    entities.create_entity(croot, "locations", "Old Dock")
    scenes.set_location(cid, sid, "old-dock")
    absorb.apply_edits(cid, [
        {"id": "new_location:the-crypt", "kind": "new_location",
         "target": {"kind": "locations", "id": ""}, "field": "body", "after": "A cold crypt.",
         "payload": {"name": "The Crypt", "keys": "", "sd_prompt": "", "current_setting": True}}], sid)
    assert scenes.get_location_history(cid, sid) == ["old-dock"]  # untouched


def test_apply_edits_new_lore_creates_entity(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied = absorb.apply_edits(cid, [
        {"id": "new_lore:salt-pact", "kind": "new_lore",
         "target": {"kind": "lore", "id": ""}, "field": "body", "after": "An old pact.",
         "payload": {"name": "Salt Pact", "keys": "pact"}}], sid)
    assert applied == ["new_lore:salt-pact"]
    got = entities.read_entity(croot, "lore", "salt-pact")
    assert got["body"].strip() == "An old pact." and got["meta"]["keys"] == "pact"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -k new_character_creates or new_character_without_sid or new_location_auto_links or new_location_leaves or new_lore_creates -v`
Expected: FAIL — `applied == []` (the `else: continue` branch silently drops the unknown `kind`)

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/absorb.py`, extend `_BROWSABLE_KINDS`:

```python
_BROWSABLE_KINDS = ("character_state", "lore", "authored", "new_character", "new_location", "new_lore")
```

In `apply_edits`, add three `elif` branches before the existing `else: continue`, and rebind `target` to the newly-created record's real id so the `recorded`/changes.json block below (which reads `target["kind"]`/`target["id"]`) reflects it:

```python
            elif kind == "new_character":
                p = e["payload"]
                card = characters.blank_card(p["name"])
                card["data"]["description"] = after
                card["data"]["extensions"]["sd_prompt"] = p.get("sd_prompt", "")
                new_cid, new_vid = characters.create_character(croot, p["name"], "default", card)
                if sid:
                    appearances.appear(cid, sid, "characters", new_cid, new_vid, "npc")
                target = {"kind": "characters", "id": new_cid}
            elif kind == "new_location":
                p = e["payload"]
                new_eid = entities.create_entity(croot, "locations", p["name"], after,
                                                 p.get("keys", ""), sd_prompt=p.get("sd_prompt", ""))
                if sid and p.get("current_setting") and not scenes.get_location_history(cid, sid):
                    scenes.set_location(cid, sid, new_eid)
                target = {"kind": "locations", "id": new_eid}
            elif kind == "new_lore":
                p = e["payload"]
                new_eid = entities.create_entity(croot, "lore", p["name"], after, p.get("keys", ""))
                target = {"kind": "lore", "id": new_eid}
```

(Insert these directly after the existing `elif kind == "plot":` branch and before `else: continue`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -v`
Expected: all PASS

Then run the full backend suite to confirm nothing else broke:

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): apply new characters/locations/lore (create, cast, auto-link)"
```

---

### Task 5: Frontend API types

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces: `StagedEdit["kind"]` gains `"new_character" | "new_location" | "new_lore"`. `CardData` gains `extensions?: { sd_prompt?: string; [k: string]: unknown }`. `EntityDetail["meta"]` gains `sd_prompt?: string`. Task 6/7/8 read these.

No test for this task alone — it's pure type additions, verified by `tsc -b` in Task 6's step, since TypeScript won't compile Task 6's usages without them. (Right-sizing per the plan skill: this task's only "test" is the compiler, which Task 6 exercises — but we still verify it compiles standalone first so a broken type change doesn't get masked by Task 6's own additions.)

- [ ] **Step 1: Make the type changes**

In `frontend/src/api/client.ts`, change:

```typescript
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
};
```

to:

```typescript
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond" | "plot"
    | "new_character" | "new_location" | "new_lore";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
};
```

Change:

```typescript
export type CardData = {
  name: string;
  description?: string;
  personality?: string;
  scenario?: string;
  first_mes?: string;
  mes_example?: string;
  system_prompt?: string;
  post_history_instructions?: string;
  alternate_greetings?: string[];
  creator?: string;
  creator_notes?: string;
  tags?: string[];
  character_book?: { entries?: unknown[] };
  [k: string]: unknown;
};
```

to (add the `extensions` line):

```typescript
export type CardData = {
  name: string;
  description?: string;
  personality?: string;
  scenario?: string;
  first_mes?: string;
  mes_example?: string;
  system_prompt?: string;
  post_history_instructions?: string;
  alternate_greetings?: string[];
  creator?: string;
  creator_notes?: string;
  tags?: string[];
  character_book?: { entries?: unknown[] };
  extensions?: { sd_prompt?: string; [k: string]: unknown };
  [k: string]: unknown;
};
```

Change:

```typescript
export type EntityDetail = { meta: { id: string; name: string; keys?: string; owners?: string }; body: string };
```

to:

```typescript
export type EntityDetail = {
  meta: { id: string; name: string; keys?: string; owners?: string; sd_prompt?: string };
  body: string;
};
```

- [ ] **Step 2: Verify it compiles**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors (these are additive/optional fields; nothing currently reads them)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(client): types for new_character/new_location/new_lore staged edits"
```

---

### Task 6: CampaignView review panel — new record proposals

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (edit-row renderer, ~lines 377-404)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `StagedEdit` (Task 5), `absorb.location: string` (already on `SceneAbsorb`, unchanged) used to decide whether to show the "this is where the scene happened" checkbox.
- Produces: nothing new consumed elsewhere — this is the leaf UI.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/routes/CampaignView.test.tsx` (after `test("plot rows are editable and sent with payload on save", ...)`):

```typescript
test("new_character proposal renders editable name/description/sd_prompt and saves them", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    edits: [{ id: "new_character:old-bram", kind: "new_character",
      target: { kind: "characters", id: "" }, label: "New character — Old Bram",
      field: "description", before: "", after: "[character(\"Old Bram\") {}]", authored: false,
      payload: { name: "Old Bram", sd_prompt: "an old innkeeper" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const nameInput = await screen.findByLabelText("Name New character — Old Bram");
  expect((nameInput as HTMLInputElement).value).toBe("Old Bram");
  const desc = await screen.findByLabelText("After New character — Old Bram");
  expect((desc as HTMLTextAreaElement).value).toBe("[character(\"Old Bram\") {}]");
  const prompt = await screen.findByLabelText("Suggested image prompt New character — Old Bram");
  expect((prompt as HTMLInputElement).value).toBe("an old innkeeper");
  fireEvent.change(nameInput, { target: { value: "Old Man Bram" } });
  fireEvent.change(prompt, { target: { value: "a grizzled innkeeper" } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "new_character:old-bram",
      payload: { name: "Old Man Bram", sd_prompt: "a grizzled innkeeper" } })] })));
});

test("new_location shows the setting checkbox only when the scene has no location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const setting = await screen.findByLabelText("This is where the scene happened New location — The Crypt");
  fireEvent.click(setting);
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      payload: expect.objectContaining({ current_setting: true }) })] })));
});

test("new_location hides the setting checkbox when the scene already has a location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "Old Dock", date: "",
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("After New location — The Crypt");
  expect(screen.queryByLabelText("This is where the scene happened New location — The Crypt")).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: FAIL — the new labels (`Name ...`, `Suggested image prompt ...`, `This is where the scene happened ...`) don't exist yet

- [ ] **Step 3: Implement**

In `frontend/src/routes/CampaignView.tsx`, replace the edit-row rendering block:

```typescript
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
                    {e.kind === "relationship" || e.kind === "bond" ? (
                      <div className="absorb-diff">
                        {e.before && <span className="absorb-before">{e.before}</span>}
                        <span className="absorb-after">{e.after}</span>
                      </div>
                    ) : (
                      <>
                        {e.before && <div className="absorb-before">{e.before}</div>}
                        <textarea aria-label={`After ${e.label}`} rows={2} value={e.after}
                                  onChange={(ev) => setEditRows((rows) => rows.map((r, j) =>
                                    j === i ? { ...r, after: ev.target.value } : r))} />
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
```

with:

```typescript
            {editRows.length > 0 && (
              <div className="absorb-edits">
                <h5>Proposed changes</h5>
                {editRows.map((e, i) => {
                  const isNewRecord = e.kind === "new_character" || e.kind === "new_location" || e.kind === "new_lore";
                  const setPayload = (patch: Record<string, unknown>) =>
                    setEditRows((rows) => rows.map((r, j) =>
                      j === i ? { ...r, payload: { ...r.payload, ...patch } } : r));
                  return (
                    <div className={"absorb-edit" + (e.authored ? " authored" : "")} key={e.id}>
                      <label>
                        <input type="checkbox" aria-label={`Approve ${e.label}`} checked={e.approved}
                               onChange={() => setEditRows((rows) => rows.map((r, j) =>
                                 j === i ? { ...r, approved: !r.approved } : r))} />
                        {e.label}{e.authored ? " · card edit" : ""}
                      </label>
                      {isNewRecord && (
                        <input aria-label={`Name ${e.label}`} value={(e.payload?.name as string) ?? ""}
                               onChange={(ev) => setPayload({ name: ev.target.value })} />
                      )}
                      {e.kind === "relationship" || e.kind === "bond" ? (
                        <div className="absorb-diff">
                          {e.before && <span className="absorb-before">{e.before}</span>}
                          <span className="absorb-after">{e.after}</span>
                        </div>
                      ) : (
                        <>
                          {e.before && <div className="absorb-before">{e.before}</div>}
                          <textarea aria-label={`After ${e.label}`} rows={2} value={e.after}
                                    onChange={(ev) => setEditRows((rows) => rows.map((r, j) =>
                                      j === i ? { ...r, after: ev.target.value } : r))} />
                        </>
                      )}
                      {(e.kind === "new_character" || e.kind === "new_location") && (
                        <input aria-label={`Suggested image prompt ${e.label}`}
                               placeholder="Suggested image prompt"
                               value={(e.payload?.sd_prompt as string) ?? ""}
                               onChange={(ev) => setPayload({ sd_prompt: ev.target.value })} />
                      )}
                      {e.kind === "new_location" && !absorb?.location && (
                        <label>
                          <input type="checkbox" aria-label={`This is where the scene happened ${e.label}`}
                                 checked={!!e.payload?.current_setting}
                                 onChange={(ev) => setPayload({ current_setting: ev.target.checked })} />
                          This is where the scene happened
                        </label>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: all PASS

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(campaign): review panel for new character/location/lore proposals"
```

---

### Task 7: CharacterEditor "Image prompt" side-section

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx` (detail view, after the chub-source block, ~line 933)
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `card.data.extensions?.sd_prompt` (Task 5's `CardData.extensions`).

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/CharacterEditor.test.tsx` (after `test("detail view shows the character tagline", ...)`):

```typescript
test("detail view shows the suggested image prompt when set", async () => {
  (api.readCharacter as any).mockResolvedValue({
    ...DETAIL,
    versions: [{ id: "default", name: "default",
      card: { ...CARD, data: { ...CARD.data, extensions: { sd_prompt: "an old innkeeper, weathered face" } } },
      images: ["avatar"] }],
  });
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Image prompt");
  expect(screen.getByText("an old innkeeper, weathered face")).toBeInTheDocument();
});

test("detail view omits the image prompt section when unset", async () => {
  render(<CharacterEditor scope={{ kind: "world", id: "w" }} wid="w" />); // DETAIL's CARD has extensions: {}
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByText("Images"); // wait for the detail view to settle
  expect(screen.queryByText("Image prompt")).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx -t "image prompt"`
Expected: FAIL — `screen.findByText("Image prompt")` never resolves

- [ ] **Step 3: Implement**

In `frontend/src/components/CharacterEditor.tsx`, insert a new side-section right after the `chub-source-block` closes and before the `Images` `detail-field` (i.e. right after the `</div>}` that closes `{worldScope && <div className="chub-source-block">...}` around line 933):

```typescript
            {(card.data.extensions?.sd_prompt) && (
              <div className="side-section">
                <h4>Image prompt</h4>
                <div className="field-hint">{card.data.extensions.sd_prompt}</div>
              </div>
            )}

            <div className="detail-field">
              <div className="section-label">Images</div>
```

(The second line shown, `<div className="detail-field">`, is the existing line right after the insertion point — shown only to anchor exactly where the new block goes; don't duplicate it.)

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(character): show suggested image prompt in the detail sidebar"
```

---

### Task 8: EntityEditor "Image prompt" side-section

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx` (state, `select`/`resetForm`, detail sidebar)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `EntityDetail.meta.sd_prompt` (Task 5).

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/EntityEditor.test.tsx` (after the test that checks keys render in the sidebar — the one asserting `within(side).getByText("pact")`):

```typescript
test("detail sidebar shows the suggested image prompt when set", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "the-crypt", name: "The Crypt" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "the-crypt", name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt, torchlight" },
    body: "cold" });
  const { container } = render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("The Crypt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Image prompt")).toBeInTheDocument();
  expect(within(side).getByText("a dark crypt, torchlight")).toBeInTheDocument();
});

test("detail sidebar omits the image prompt section when unset", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  // default readEntity mock (from beforeEach) has no sd_prompt
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).queryByText("Image prompt")).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx -t "image prompt"`
Expected: FAIL — `within(side).getByText("Image prompt")` throws (not found)

- [ ] **Step 3: Implement**

In `frontend/src/components/EntityEditor.tsx`, add a new state variable next to `owners`:

```typescript
  const [owners, setOwners] = useState<string[]>([]);          // selected owner refs (lore only)
  const [sdPrompt, setSdPrompt] = useState("");                 // suggested SD prompt, absorb-set only
```

In `resetForm()`, add a reset line next to the existing `setOwners([])`:

```typescript
  function resetForm() {
    setEditing(null);
    setName("");
    setBody("");
    setKeys("");
    setOwners([]); // manual "+ New" / post-save: always world-level, never a stale nav owner
    setSdPrompt("");
    setImages([]);
    setMode("edit"); // a brand-new entry goes straight to the form
  }
```

In `select()`, add a set line next to the existing `setKeys`/`setOwners`:

```typescript
  async function select(id: string) {
    setError(null);
    const e = await api.readEntity(scope, kind, id);
    setEditing(id);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
    setOwners((e.meta.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean));
    setSdPrompt(e.meta.sd_prompt ?? "");
    setMode("view");
    reloadImages(id);
  }
```

In the detail sidebar JSX, add a new `side-section` right after the "Keys" section and before the `kind === "lore"` Owners section:

```typescript
              <div className="side-section">
                <h4>Keys</h4>
                {keyList.length > 0
                  ? <div className="chips">{keyList.map((k) => <span key={k} className="chip on">{k}</span>)}</div>
                  : <div className="field-hint">always-on</div>}
              </div>
              {sdPrompt && (
                <div className="side-section">
                  <h4>Image prompt</h4>
                  <div className="field-hint">{sdPrompt}</div>
                </div>
              )}
              {kind === "lore" && (
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx`
Expected: all PASS

- [ ] **Step 5: Run the full frontend suite and type-check**

Run (from `frontend/`): `npx vitest run`
Expected: all PASS

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx
git commit -m "feat(entity): show suggested image prompt in the detail sidebar"
```

---

### Task 9: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS

- [ ] **Step 2: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run`
Expected: all PASS

- [ ] **Step 3: Type-check the frontend**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 4: Manually sanity-check one end-to-end flow**

Use the `verify` skill or a manual local run: start a campaign scene, write dialogue introducing a brand-new named NPC and a brand-new named place, click "End scene", confirm the review panel shows editable "New character — …" and "New location — …" rows with Name/description-or-body/image-prompt fields, approve them, click "Save summary", and confirm the new character appears in the campaign's character list (cast into that scene) and the new location appears in the campaign's locations list (and, if marked as the scene's setting, is now that scene's recorded location).

No commit for this task — it's verification, not a code change.
