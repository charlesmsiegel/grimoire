# Scene Knowledge (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track per-NPC standing knowledge (`knows`/`suspects`) so the scene-absorb extraction proposes it, the review approves it, and the context injects it — riding the existing `character_state` machinery.

**Architecture:** `state.md` grows two optional `## `-headed prose sections beside `current_state`. `playstate` parses/composes the headed body; `absorb` extracts/materializes the three fields as one composed blob on the existing `character_state` StagedEdit (no new kind, no payload); `context` renders `Knows:`/`Suspects:` under the existing `# Character state` section. Snapshot semantics; NPC-only; fully back-compatible with Phase-2 bare-body `state.md`.

**Tech Stack:** FastAPI backend (Python, pytest). No frontend code change.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-scene-knowledge-design.md`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Fields are prose strings; missing ⇒ `""` (never `None`). Trust nothing from the LLM — coerce to stripped `str`.
- Store IO stays tolerant (omit-never-crash) exactly as Phases 1–3.
- Recognized `state.md` headers, matched case-insensitively on the whole line: `## Current state`, `## Knows`, `## Suspects`. A body with none is `current_state` wholesale (back-compat).
- `compose_body` returns **bare `current_state` prose (no header)** when both `knows` and `suspects` are empty; otherwise the headed blob (omitting empty knowledge sections).
- NPC-only means `a["role"] == "npc" and a["kind"] == "characters"`.

---

### Task 1: `playstate` — parse/compose/write the three-field body

**Files:**
- Modify: `backend/src/grimoire/store/playstate.py`
- Test: `backend/tests/test_playstate_store.py`

**Interfaces:**
- Consumes: `frontmatter.parse_frontmatter`, `frontmatter.dump_frontmatter`, `paths.now_iso` (already imported).
- Produces:
  - `read_state(root, cid) -> {"current_state": str, "knows": str, "suspects": str, "updated": str} | None`
  - `compose_body(current_state: str, knows: str, suspects: str) -> str`
  - `write_state(root: Path, cid: str, body: str) -> None` (writes the body verbatim; parameter renamed from `current_state`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_playstate_store.py`:

```python
from grimoire.store import playstate


def test_compose_body_bare_when_no_knowledge():
    assert playstate.compose_body("Just hurt.", "", "") == "Just hurt."


def test_compose_body_headed_when_knowledge_present():
    body = playstate.compose_body("Hurt.", "The map is fake.", "")
    assert "## Current state\nHurt." in body
    assert "## Knows\nThe map is fake." in body
    assert "## Suspects" not in body  # empty section omitted


def test_read_parses_three_sections(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine",
                          "## Current state\nHurt.\n\n## Knows\nThe map is fake.\n\n## Suspects\nElara lies.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Hurt."
    assert st["knows"] == "The map is fake."
    assert st["suspects"] == "Elara lies."


def test_read_unheaded_body_is_current_state(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "Wounded; travels with the party.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Wounded; travels with the party."
    assert st["knows"] == "" and st["suspects"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playstate_store.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.playstate' has no attribute 'compose_body'` and `KeyError: 'knows'`.

- [ ] **Step 3: Implement parse/compose/write**

Replace the body of `backend/src/grimoire/store/playstate.py` below the imports with:

```python
_SECTIONS = (("current_state", "current state"), ("knows", "knows"), ("suspects", "suspects"))
_HEADERS = {label: field for field, label in _SECTIONS}


def state_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "state.md"


def _parse_body(body: str) -> dict:
    fields = {"current_state": "", "knows": "", "suspects": ""}
    cur, buf = None, []
    saw_header = False

    def flush():
        if cur and buf:
            fields[cur] = "\n".join(buf).strip()

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
            flush()
            cur, buf, saw_header = _HEADERS[stripped[3:].strip().lower()], [], True
            continue
        buf.append(line)
    flush()
    if not saw_header:  # legacy Phase-2 body: whole thing is current_state
        fields["current_state"] = body.strip()
    return fields


def compose_body(current_state: str, knows: str, suspects: str) -> str:
    current_state, knows, suspects = current_state.strip(), knows.strip(), suspects.strip()
    if not knows and not suspects:
        return current_state
    parts = []
    for label, value in (("Current state", current_state), ("Knows", knows), ("Suspects", suspects)):
        if value:
            parts.append(f"## {label}\n{value}")
    return "\n\n".join(parts)


def read_state(root: Path, cid: str) -> dict | None:
    p = state_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {**_parse_body(body), "updated": meta.get("updated", "")}


def write_state(root: Path, cid: str, body: str) -> None:
    p = state_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"),
                 encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playstate_store.py -q`
Expected: PASS (new tests + the pre-existing `test_write_then_read_roundtrip` / `test_write_replaces_snapshot`, which pass bare bodies and still read back as `current_state`).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playstate.py backend/tests/test_playstate_store.py
git commit -m "feat(playstate): headed knows/suspects sections on state.md (back-compatible)"
```

---

### Task 2: `absorb.parse_output` — parse `knows`/`suspects`

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py:92` (the `character_state_edits` line in `parse_output`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_output(text)["character_state_edits"]` entries are now `{"id", "current_state", "knows", "suspects"}` (each a stripped `str`, `""` when absent).

- [ ] **Step 1: Update the failing test + add a knowledge test**

In `backend/tests/test_absorb_store.py`, change the assertion in `test_parse_output_extracts_summary_and_edit_lists` from:

```python
    assert out["character_state_edits"] == [{"id": "seraphine", "current_state": "hurt"}]
```

to:

```python
    assert out["character_state_edits"] == [
        {"id": "seraphine", "current_state": "hurt", "knows": "", "suspects": ""}]
```

Then add a new test:

```python
def test_parse_output_extracts_knowledge_fields():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [{"id": "seraphine", "current_state": "hurt",'
            '   "knows": "map is fake", "suspects": "elara lies"}],'
            ' "lore_edits": [], "authored_edits": []}')
    out = absorb.parse_output(text)
    assert out["character_state_edits"] == [
        {"id": "seraphine", "current_state": "hurt", "knows": "map is fake", "suspects": "elara lies"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "parse_output"`
Expected: FAIL — the entries lack `knows`/`suspects`.

- [ ] **Step 3: Extend the field list**

In `backend/src/grimoire/store/absorb.py`, in `parse_output`, change:

```python
        "character_state_edits": _list("character_state_edits", ("id", "current_state")),
```

to:

```python
        "character_state_edits": _list("character_state_edits",
                                       ("id", "current_state", "knows", "suspects")),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "parse_output"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): parse knows/suspects on character_state_edits"
```

---

### Task 3: `absorb.state_snapshot` + `EXTRACT_INSTRUCTION` — feed current knowledge to the model

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (`state_snapshot`, lines ~265–279; `EXTRACT_INSTRUCTION`, lines ~13–31)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `playstate.read_state` (now three-field).
- Produces: `state_snapshot(cid, sid) -> dict[str, str]` — display-name → a readable one-line-ish snapshot: `current_state` with ` Knows: … Suspects: …` appended when non-empty. `build_prompt` is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_absorb_store.py`:

```python
def test_state_snapshot_includes_knowledge(monkeypatch, tmp_path):
    from grimoire.store import appearances, playstate, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Hurt.", "map is fake", "elara lies"))
    snap = absorb.state_snapshot(cid, sid)
    assert "Hurt." in snap["Seraphine"]
    assert "Knows: map is fake" in snap["Seraphine"]
    assert "Suspects: elara lies" in snap["Seraphine"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_state_snapshot_includes_knowledge -q`
Expected: FAIL — `assert "Knows: map is fake" in "Hurt."` (snapshot only carries `current_state`).

- [ ] **Step 3: Render knowledge in `state_snapshot`**

In `backend/src/grimoire/store/absorb.py`, replace the `state_snapshot` function body's per-NPC value assignment. Change:

```python
        st = playstate.read_state(croot, a["id"])
        if st and st["current_state"]:
            try:
                name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = st["current_state"]
    return out
```

to:

```python
        st = playstate.read_state(croot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            try:
                name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = _snapshot_line(st)
    return out


def _snapshot_line(st: dict) -> str:
    parts = [st["current_state"].strip()]
    if st["knows"].strip():
        parts.append(f"Knows: {st['knows'].strip()}")
    if st["suspects"].strip():
        parts.append(f"Suspects: {st['suspects'].strip()}")
    return " ".join(p for p in parts if p)
```

- [ ] **Step 4: Update `EXTRACT_INSTRUCTION`**

In `backend/src/grimoire/store/absorb.py`, change the `character_state_edits` clause in `EXTRACT_INSTRUCTION` from:

```python
    '"character_state_edits" (list of {"id","current_state"} — for each present character '
    "whose standing condition changed, the FULL rewritten snapshot of who they are now, "
    "dropping what is no longer true; standing conditions only, not events), "
```

to:

```python
    '"character_state_edits" (list of {"id","current_state","knows","suspects"} — for each '
    "present character whose standing snapshot changed, the FULL rewritten snapshot: "
    '"current_state" is their standing condition, "knows" is what they now hold as certain, '
    '"suspects" is what they believe but have not confirmed; drop what is no longer true, '
    'standing facts only (not a running log). Use "" for a field that does not apply), '
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "state_snapshot or build_prompt"`
Expected: PASS (the pre-existing `test_build_prompt_includes_facts_transcript_and_state` still passes — `build_prompt` and its `name -> str` input are unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): feed current knows/suspects into the extraction prompt"
```

---

### Task 4: `absorb.materialize` — composed blob + no-op guard on the `character_state` row

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py:140-154` (the `character_state_edits` loop in `materialize`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `playstate.read_state`, `playstate.compose_body`.
- Produces: the `character_state` StagedEdit's `before`/`after` are `compose_body(...)` blobs; a row is dropped when `before == after`. Shape/kind/`field`/`authored` unchanged; no `payload`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_absorb_store.py`:

```python
def test_materialize_composes_knowledge_blob(monkeypatch, tmp_path):
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wary of the party.")
    parsed = {"character_state_edits": [
        {"id": ch, "current_state": "Travels with them.", "knows": "map is fake", "suspects": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["kind"] == "character_state" and cs["authored"] is False and "payload" not in cs
    assert cs["before"] == "Wary of the party."  # bare (no prior knowledge)
    assert "## Current state\nTravels with them." in cs["after"]
    assert "## Knows\nmap is fake" in cs["after"]
    assert "## Suspects" not in cs["after"]


def test_materialize_drops_noop_state(monkeypatch, tmp_path):
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Unchanged.")
    parsed = {"character_state_edits": [
        {"id": ch, "current_state": "Unchanged.", "knows": "", "suspects": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "materialize_composes_knowledge or materialize_drops_noop"`
Expected: FAIL — `after` is the bare `current_state` (no `## Knows`), and the no-op row is still emitted.

- [ ] **Step 3: Compose the blob + guard no-ops**

In `backend/src/grimoire/store/absorb.py`, replace the `character_state_edits` loop in `materialize`:

```python
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
```

with:

```python
    for e in parsed.get("character_state_edits", []):
        char_id = e.get("id", "")
        after = playstate.compose_body(e.get("current_state", ""), e.get("knows", ""),
                                       e.get("suspects", ""))
        if not char_id or not after:
            continue
        try:
            characters.read_character(croot, char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        before = playstate.compose_body(st["current_state"], st["knows"], st["suspects"]) if st else ""
        if before == after:
            continue
        out.append({"id": f"character_state:{char_id}", "kind": "character_state",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — current state",
                    "field": "current_state",
                    "before": before, "after": after, "authored": False})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "materialize"`
Expected: PASS — including the pre-existing `test_materialize_builds_before_after` (bare in, bare out: `before == "Wary of the party."`, `after == "Now travels with them."`) and `test_materialize_skips_unknown_targets`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat(absorb): materialize character_state as a composed knows/suspects blob"
```

---

### Task 5: `context._character_state` — render `Knows:`/`Suspects:`

**Files:**
- Modify: `backend/src/grimoire/store/context.py:225-240` (`_character_state`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `playstate.read_state` (three-field).
- Produces: the `# Character state` section renders each present NPC's `current_state`, then an indented `  Knows: …` and/or `  Suspects: …` line when non-empty. NPC-only filter and tolerant `try/except` unchanged.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_context.py`:

```python
def test_character_state_renders_knowledge(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main", characters.blank_card("Seraphine"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Hurt.", "map is fake", "elara lies"))
    section = dict(context._assemble(cid, sid)["system"])["Character state"]
    assert "Seraphine: Hurt." in section
    assert "Knows: map is fake" in section
    assert "Suspects: elara lies" in section
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py::test_character_state_renders_knowledge -q`
Expected: FAIL — the section renders only `Seraphine: Hurt.` (no `Knows:`/`Suspects:`).

- [ ] **Step 3: Render knowledge lines**

In `backend/src/grimoire/store/context.py`, replace the inner loop body of `_character_state`. Change:

```python
            st = playstate.read_state(croot, a["id"])
            if st and st["current_state"]:
                try:
                    name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
                except characters.CharacterNotFound:
                    name = a["id"]
                lines.append(f"{name}: {st['current_state']}")
        return "# Character state\n" + "\n".join(lines) if lines else ""
```

to:

```python
            st = playstate.read_state(croot, a["id"])
            if st and (st["current_state"] or st["knows"] or st["suspects"]):
                try:
                    name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
                except characters.CharacterNotFound:
                    name = a["id"]
                lines.append(f"{name}: {st['current_state']}".rstrip())
                if st["knows"].strip():
                    lines.append(f"  Knows: {st['knows'].strip()}")
                if st["suspects"].strip():
                    lines.append(f"  Suspects: {st['suspects'].strip()}")
        return "# Character state\n" + "\n".join(lines) if lines else ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k "character_state"`
Expected: PASS — including the pre-existing `test_character_state_section_injected` (bare body → `Seraphine: Wounded; travels with the party.`) and `test_character_state_absent_when_none`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat(context): render knows/suspects under # Character state"
```

---

### Task 6: Full backend suite + frontend guard

**Files:**
- Test: `frontend/src/components/CampaignView.test.tsx` (or the existing absorb-review test file — locate the one that renders a `character_state` review row)

**Interfaces:** none — behavior-only assertion; no product code changes.

- [ ] **Step 1: Run the whole backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, count ≥ the 426 baseline plus the ~9 new tests. If any Phase-1/2/3 test regressed, fix the cause before proceeding (do not weaken assertions).

- [ ] **Step 2: Locate the existing character_state review-row test**

Run: `grep -rn "character_state" frontend/src` (via the repo search tool).
Identify the vitest that renders the review checklist with a `character_state` StagedEdit and asserts its `after` textarea. This is the file to extend.

- [ ] **Step 3: Add the multi-section render assertion**

In that test file, add a test that builds a `character_state` StagedEdit whose `after` contains the headed blob and asserts the textarea shows it. Mirror the existing review-row test's setup (props/mocks); the only new content is the multi-line `after` and the assertion. Concretely, the StagedEdit fixture uses:

```ts
{ id: "character_state:seraphine", kind: "character_state",
  target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
  field: "current_state", authored: false,
  before: "Wary.",
  after: "## Current state\nHurt.\n\n## Knows\nmap is fake" }
```

and asserts (matching the existing row's query style) that the rendered `after` textarea's value contains `"## Knows"` and `"map is fake"`.

- [ ] **Step 4: Run the frontend checks**

Run (from `frontend/`): `npx vitest run` and `npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "test(review): character_state row renders multi-section knowledge body"
```

---

## Self-Review

**Spec coverage:**
- Storage (headed `state.md`, `read_state`/`compose_body`/`write_state`, back-compat) → Task 1. ✓
- Extraction `EXTRACT_INSTRUCTION` + `parse_output` knowledge fields → Tasks 2, 3. ✓
- `state_snapshot` feeds current knowledge, `build_prompt` unchanged → Task 3. ✓
- `materialize` composed blob + no-op guard, no new kind/payload → Task 4. ✓
- Apply unchanged (`write_state` stores `after` verbatim) → exercised by the pre-existing `test_apply_edits_writes_each_kind`, which still passes (bare body); no task needed. ✓
- `# Character state` injection renders knowledge, NPC-only, tolerant → Task 5. ✓
- Frontend no code change + guard test → Task 6. ✓
- NPC-only scope → enforced by the existing cast filters in `state_snapshot`/`_character_state` (unchanged), and knowledge only reaches NPCs. ✓

**Placeholder scan:** every code step shows full before/after content; Task 6 Step 2/3 reference an existing test whose exact path is resolved at execution (the one deliberate lookup, with the search command given). No TBD/TODO. ✓

**Type consistency:** `read_state` returns `{current_state, knows, suspects, updated}` everywhere consumed (Tasks 3, 4, 5); `compose_body(current_state, knows, suspects)` signature identical in Tasks 1, 3, 4; `write_state(root, cid, body)` matches its sole caller `apply_edits` (unchanged, passes `after`). `state_snapshot -> dict[str, str]` keeps `build_prompt` untouched. ✓

## Notes for the executor

- `apply_edits` needs **no change**: it already calls `playstate.write_state(croot, target["id"], after)`, and `after` is now the composed blob, stored verbatim. The `write_state` parameter rename (`current_state` → `body`) is signature-compatible at every call site.
- Do not add a `payload` to the `character_state` edit — knowledge rides the editable `after` text by design.
- Keep assertions strict. If a pre-existing test fails, the design intends it to still pass on a bare body; a failure means an implementation slip, not a test to relax.
