# Scene Relationships (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At End scene, the extraction also proposes updated directed feelings and symmetric bonds among the present cast; the user approves each as a read-only before→after row; approved ones are written to `relationships.json` and injected as a `# Relationships` context block.

**Architecture:** A new `store/relationships.py` owns `relationships.json` (directed `feelings` + canonical `bonds`) and a present-cast renderer. `absorb.py` grows a relationships snapshot into the prompt, two parsed lists, two `materialize` kinds (carrying a structured `payload`), and two `apply_edits` branches. `context._assemble` injects `# Relationships`. The review checklist renders relationship/bond rows read-only (approve-only); text rows stay editable.

**Tech Stack:** FastAPI + Pydantic (pytest), Vite/React + TS (vitest). Store = markdown/JSON under `~/.grimoire`.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` (absolute path `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` if the shell sits in `frontend/`).
- Run frontend **from `frontend/`**: `npx vitest run`, `npx tsc -b`.
- Actor tokens are `"<kind>:<id>"` (`characters:seraphine`, `pcs:elara`). Feeling key `"{from}->{to}"`; bond key = sorted `"{lo}|{hi}"` (canonical, so both orderings collapse to one).
- Metrics `trust`/`affection`/`tension` are ints clamped to **0–5**; `note`/`type` are strings.
- Feelings are directed/asymmetric (no enforced symmetry); bonds symmetric-by-construction.
- Relationship metrics are proposed as **absolute** new values (snapshot), fed the current values; a no-op (after == before) is dropped at materialize.
- StagedEdit shape is reused; relationship/bond rows add `payload` (structured) and are **not** text-editable in review (approve/reject only). `payload` shape: feeling `{from,to,trust,affection,tension,note}`; bond `{a,b,type}`.
- `apply_edits` is best-effort per edit (skip malformed/missing).
- No import cycles: `relationships` imports `campaigns`/`characters`/`pcs`/`paths`; `absorb`/`context` import `relationships` at module load.
- Injection is tolerant of a garbled `relationships.json` (omit, never crash).

## File Structure

- Create `backend/src/grimoire/store/relationships.py`.
- Modify `backend/src/grimoire/store/__init__.py` (export `relationships`).
- Modify `backend/src/grimoire/store/absorb.py` (snapshot, parse, materialize, apply).
- Modify `backend/src/grimoire/store/context.py` (`# Relationships`).
- Modify `backend/src/grimoire/routes.py` (`post_absorb` passes the rel snapshot to `build_prompt`).
- Modify `frontend/src/api/client.ts` (`StagedEdit.payload?`).
- Modify `frontend/src/routes/CampaignView.tsx` (read-only structured rows).
- Tests: create `test_relationships_store.py`; modify `test_absorb_store.py`, `test_context.py`, `test_routes.py`, `CampaignView.test.tsx`.

---

## Task 1: `relationships.py` store + renderer

**Files:**
- Create: `backend/src/grimoire/store/relationships.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_relationships_store.py`

**Interfaces:**
- Produces:
  - `read(cid) -> {"feelings": dict, "bonds": dict}` (missing ⇒ empty).
  - `feeling_key(a, b) -> str`; `bond_key(a, b) -> str` (canonical).
  - `get_feeling(cid, a, b) -> dict | None`; `get_bond(cid, a, b) -> dict | None`.
  - `set_feeling(cid, a, b, trust, affection, tension, note) -> None`.
  - `set_bond(cid, a, b, type, since_scene="") -> None` (preserves existing `since_scene`).
  - `actor_name(croot, token) -> str`.
  - `render_present(cid, tokens, name_of) -> list[str]` — directed feelings among tokens, then bonds; each a readable line.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_relationships_store.py`:

```python
from grimoire.store import campaigns, relationships, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert relationships.read(cid) == {"feelings": {}, "bonds": {}}


def test_feeling_directed_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "grateful")
    assert relationships.get_feeling(cid, "characters:a", "characters:b") == {
        "trust": 4, "affection": 3, "tension": 1, "note": "grateful"}
    assert relationships.get_feeling(cid, "characters:b", "characters:a") is None  # asymmetric


def test_bond_key_is_canonical(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_bond(cid, "characters:b", "characters:a", "allies", since_scene="s1")
    assert relationships.get_bond(cid, "characters:a", "characters:b")["type"] == "allies"
    relationships.set_bond(cid, "characters:a", "characters:b", "rivals")  # reorder, no since
    data = relationships.read(cid)
    assert list(data["bonds"]) == ["characters:a|characters:b"]  # single canonical key
    assert data["bonds"]["characters:a|characters:b"] == {"type": "rivals", "since_scene": "s1"}


def test_render_present_lists_feelings_and_bonds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "warm")
    relationships.set_bond(cid, "characters:a", "characters:b", "allies")
    lines = relationships.render_present(cid, ["characters:a", "characters:b"], lambda t: t.split(":")[1].title())
    assert "A → B: trust 4, affection 3, tension 1 (warm)" in lines
    assert "A & B: allies" in lines
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relationships_store.py -q`
Expected: FAIL — `cannot import name 'relationships'`.

- [ ] **Step 3: Create `relationships.py`**

```python
"""Per-campaign relationships: directed feelings (asymmetric) + symmetric bonds among
cast actors. Actor tokens are "<kind>:<id>". Stored at <campaign>/relationships.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, characters, pcs


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "relationships.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {"feelings": {}, "bonds": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("feelings", {})
    data.setdefault("bonds", {})
    return data


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def feeling_key(a: str, b: str) -> str:
    return f"{a}->{b}"


def bond_key(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


def get_feeling(cid: str, a: str, b: str) -> dict | None:
    return read(cid)["feelings"].get(feeling_key(a, b))


def get_bond(cid: str, a: str, b: str) -> dict | None:
    return read(cid)["bonds"].get(bond_key(a, b))


def set_feeling(cid: str, a: str, b: str, trust: int, affection: int, tension: int, note: str) -> None:
    data = read(cid)
    data["feelings"][feeling_key(a, b)] = {"trust": trust, "affection": affection,
                                           "tension": tension, "note": note}
    _write(cid, data)


def set_bond(cid: str, a: str, b: str, type: str, since_scene: str = "") -> None:
    data = read(cid)
    key = bond_key(a, b)
    existing = data["bonds"].get(key, {})
    data["bonds"][key] = {"type": type, "since_scene": since_scene or existing.get("since_scene", "")}
    _write(cid, data)


def actor_name(croot, token: str) -> str:
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            return pcs.read_pc(croot, aid)["meta"]["name"]
        return characters.read_character(croot, aid)["meta"].get("name", aid)
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return aid


def _render_feeling(f: dict) -> str:
    note = f" ({f['note']})" if f.get("note") else ""
    return f"trust {f['trust']}, affection {f['affection']}, tension {f['tension']}{note}"


def render_present(cid: str, tokens: list[str], name_of) -> list[str]:
    data = read(cid)
    lines: list[str] = []
    for a in tokens:
        for b in tokens:
            if a == b:
                continue
            f = data["feelings"].get(feeling_key(a, b))
            if f:
                lines.append(f"{name_of(a)} → {name_of(b)}: {_render_feeling(f)}")
    seen: set[str] = set()
    for a in tokens:
        for b in tokens:
            if a >= b:
                continue
            key = bond_key(a, b)
            if key in seen:
                continue
            bd = data["bonds"].get(key)
            if bd:
                seen.add(key)
                lines.append(f"{name_of(a)} & {name_of(b)}: {bd['type']}")
    return lines
```

- [ ] **Step 4: Export from the barrel**

In `__init__.py`, add `relationships` to the `from . import (...)` block (alphabetical: after `pcs, playing, playstate,` → before `scenes,`) and `"relationships",` to `__all__`.

- [ ] **Step 5: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relationships_store.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/relationships.py backend/src/grimoire/store/__init__.py backend/tests/test_relationships_store.py
git commit -m "feat: relationships store (directed feelings + canonical bonds)"
```

---

## Task 2: absorb — snapshot + parse the two lists

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Produces:
  - `parse_output` gains `relationship_deltas: [{from,to,trust,affection,tension,note}]` (ints 0–5) and `bond_changes: [{a,b,type}]`.
  - `relationships_snapshot(cid, sid) -> str` — rendered present-cast feelings/bonds block.
  - `build_prompt(transcript, facts, state_snapshot=None, rel_snapshot=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_absorb_store.py`:

```python
def test_parse_output_relationship_and_bond_lists():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "relationship_deltas": [{"from": "characters:a", "to": "characters:b",'
            '   "trust": 9, "affection": 2, "tension": 1, "note": "warm"}],'
            ' "bond_changes": [{"a": "characters:a", "b": "characters:b", "type": "allies"}]}')
    out = absorb.parse_output(text)
    assert out["relationship_deltas"] == [{"from": "characters:a", "to": "characters:b",
                                           "trust": 5, "affection": 2, "tension": 1, "note": "warm"}]  # 9 clamped to 5
    assert out["bond_changes"] == [{"a": "characters:a", "b": "characters:b", "type": "allies"}]


def test_relationships_snapshot_renders_present(monkeypatch, tmp_path):
    from grimoire.store import appearances, relationships, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = _char(croot, "Ann")
    b = _char(croot, "Bo")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 4, 3, 1, "warm")
    snap = absorb.relationships_snapshot(cid, sid)
    assert "Ann → Bo: trust 4" in snap
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "relationship or snapshot"`
Expected: FAIL (`relationship_deltas` missing / no `relationships_snapshot`).

- [ ] **Step 3: Extend `parse_output` and add the snapshot**

In `absorb.py`, add `relationships` to the imports:
`from . import appearances, campaigns, characters, chronicle, entities, playstate, relationships`

Add an int-clamp helper and extend `parse_output`'s returned dict:

```python
def _int05(v) -> int:
    try:
        return max(0, min(5, int(v)))
    except (ValueError, TypeError):
        return 0
```

In `parse_output`, before the `return`, build the relationship list, and add both keys to the returned dict:

```python
    rel_deltas = []
    for e in obj.get("relationship_deltas", []):
        if isinstance(e, dict):
            rel_deltas.append({"from": str(e.get("from", "")).strip(), "to": str(e.get("to", "")).strip(),
                               "trust": _int05(e.get("trust")), "affection": _int05(e.get("affection")),
                               "tension": _int05(e.get("tension")), "note": str(e.get("note", "")).strip()})
```

Add to the returned dict:
```python
        "relationship_deltas": rel_deltas,
        "bond_changes": _list("bond_changes", ("a", "b", "type")),
```

Add the snapshot + name helper (reuse `relationships.actor_name`):

```python
def relationships_snapshot(cid: str, sid: str) -> str:
    croot = campaigns.campaign_root(cid)
    tokens = [f"{a['kind']}:{a['id']}" for a in appearances.scene_cast(cid, sid)]
    lines = relationships.render_present(cid, tokens, lambda t: relationships.actor_name(croot, t))
    return "\n".join(lines)
```

Extend `build_prompt` to accept and include the relationships snapshot:

```python
def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None) -> list[dict]:
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
    if rel_snapshot:
        head.append("Current relationships:\n" + rel_snapshot)
    prefix = ("\n".join(head) + "\n\n") if head else ""
    return [{"role": "system", "content": EXTRACT_INSTRUCTION},
            {"role": "user", "content": prefix + transcript}]
```

Extend `EXTRACT_INSTRUCTION` to request the two lists (append before the final sentence):

```python
    '"relationship_deltas" (list of {"from","to","trust","affection","tension","note"} — '
    "for each directed pair whose feelings changed, the FULL updated values; use the "
    '"<kind>:<id>" tokens from the context block; trust/affection/tension are 0-5), '
    '"bond_changes" (list of {"a","b","type"} — a shared relationship type for a pair). '
```

- [ ] **Step 4: Pass the snapshot from the route**

In `routes.py` `post_absorb`, extend the `build_prompt` call:

```python
    messages = store.absorb.build_prompt(
        store.chronicle.transcript_text(scene["messages"]), facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid))
```

- [ ] **Step 5: Run tests + full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (existing absorb/route tests still green — new keys are additive; the summary preview shape is unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/routes.py backend/tests/test_absorb_store.py
git commit -m "feat: absorb parses relationship/bond lists + primes prompt with rel snapshot"
```

---

## Task 3: materialize relationship + bond StagedEdits

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Produces: `materialize` emits `relationship`/`bond` StagedEdits with readable
  `before`/`after` and a structured `payload`; asymmetry preserved; no-ops dropped;
  unknown actors dropped.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_absorb_store.py`:

```python
def test_materialize_relationship_and_bond(monkeypatch, tmp_path):
    from grimoire.store import appearances, relationships, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = _char(croot, "Ann")
    b = _char(croot, "Bo")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 1, 1, 3, "wary")
    relationships.set_feeling(cid, f"characters:{b}", f"characters:{a}", 2, 2, 2, "keep")
    parsed = {
        "relationship_deltas": [
            {"from": f"characters:{a}", "to": f"characters:{b}", "trust": 4, "affection": 3, "tension": 1, "note": "warm"},
            {"from": f"characters:{b}", "to": f"characters:{a}", "trust": 2, "affection": 2, "tension": 2, "note": "keep"},  # no-op vs stored -> dropped
            {"from": "characters:ghost", "to": f"characters:{b}", "trust": 5, "affection": 0, "tension": 0, "note": ""}],   # unknown -> dropped
        "bond_changes": [{"a": f"characters:{a}", "b": f"characters:{b}", "type": "allies"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    rel = edits[f"feeling:characters:{a}->characters:{b}"]
    assert rel["kind"] == "relationship" and rel["before"].startswith("trust 1, affection 1, tension 3") \
        and rel["after"].startswith("trust 4, affection 3, tension 1") and rel["payload"]["trust"] == 4
    assert f"feeling:characters:{b}->characters:{a}" not in edits  # no-op dropped
    assert not any(k.startswith("feeling:characters:ghost") for k in edits)  # unknown dropped
    bond = edits[f"bond:characters:{a}|characters:{b}"]
    assert bond["kind"] == "bond" and bond["after"] == "allies" and bond["payload"]["type"] == "allies"
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "materialize_relationship"`
Expected: FAIL.

- [ ] **Step 3: Extend `materialize`**

Add helpers near `materialize` in `absorb.py`:

```python
def _actor_exists(croot, token: str) -> bool:
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(croot, aid)
        elif kind == "characters":
            characters.read_character(croot, aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False
```

Add `pcs` to the `absorb.py` imports (join the existing `from . import ...` line).

At the end of `materialize` (before `return out`), append:

```python
    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(croot, frm) or not _actor_exists(croot, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        out.append({"id": f"feeling:{relationships.feeling_key(frm, to)}", "kind": "relationship",
                    "target": {"kind": "relationships", "id": relationships.feeling_key(frm, to)},
                    "label": f"{relationships.actor_name(croot, frm)} → {relationships.actor_name(croot, to)}",
                    "field": "feeling", "before": before, "after": after, "authored": False,
                    "payload": payload})

    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(croot, a_tok) or not _actor_exists(croot, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        out.append({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                    "target": {"kind": "relationships", "id": relationships.bond_key(a_tok, b_tok)},
                    "label": f"{relationships.actor_name(croot, a_tok)} & {relationships.actor_name(croot, b_tok)}",
                    "field": "bond", "before": before, "after": typ, "authored": False,
                    "payload": {"a": a_tok, "b": b_tok, "type": typ}})
```

Note `relationships._render_feeling` accepts the payload dict (has trust/affection/tension/note keys).

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "materialize"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat: materialize relationship/bond StagedEdits (payload + no-op drop)"
```

---

## Task 4: apply relationship + bond edits

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Produces: `apply_edits` handles `relationship`/`bond` by writing `relationships.json` from `payload`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_absorb_store.py`:

```python
def test_apply_edits_writes_relationships(monkeypatch, tmp_path):
    from grimoire.store import relationships
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "feeling:characters:a->characters:b", "kind": "relationship",
         "target": {"kind": "relationships", "id": "characters:a->characters:b"}, "field": "feeling",
         "after": "…", "payload": {"from": "characters:a", "to": "characters:b",
                                    "trust": 4, "affection": 3, "tension": 1, "note": "warm"}},
        {"id": "bond:characters:a|characters:b", "kind": "bond",
         "target": {"kind": "relationships", "id": "characters:a|characters:b"}, "field": "bond",
         "after": "allies", "payload": {"a": "characters:a", "b": "characters:b", "type": "allies"}}])
    assert set(applied) == {"feeling:characters:a->characters:b", "bond:characters:a|characters:b"}
    assert relationships.get_feeling(cid, "characters:a", "characters:b")["trust"] == 4
    assert relationships.get_bond(cid, "characters:a", "characters:b")["type"] == "allies"
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q -k "writes_relationships"`
Expected: FAIL (kinds not handled ⇒ skipped ⇒ `applied` empty).

- [ ] **Step 3: Add the two branches to `apply_edits`**

In `apply_edits`, before the `else: continue`:

```python
            elif kind == "relationship":
                p = e["payload"]
                relationships.set_feeling(cid, p["from"], p["to"], p["trust"], p["affection"],
                                          p["tension"], p.get("note", ""))
            elif kind == "bond":
                p = e["payload"]
                relationships.set_bond(cid, p["a"], p["b"], p["type"])
```

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/tests/test_absorb_store.py
git commit -m "feat: apply relationship/bond edits to relationships.json"
```

---

## Task 5: `# Relationships` context injection

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `relationships.render_present`, `relationships.actor_name`, `cast`.
- Produces: an always-on `# Relationships` section among present cast; omitted when empty; tolerant.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_context.py`:

```python
def test_relationships_section_injected(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                relationships, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    a = characters.create_character(croot, "Ann", "main", characters.blank_card("Ann"))[0]
    b = characters.create_character(croot, "Bo", "main", characters.blank_card("Bo"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 4, 3, 1, "warm")
    system = dict(context._assemble(cid, sid)["system"])
    assert "Relationships" in system
    assert "Ann → Bo: trust 4, affection 3, tension 1 (warm)" in system["Relationships"]


def test_relationships_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    assert "Relationships" not in [l for l, _ in context._assemble(cid, sid)["system"]]
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k relationships`
Expected: FAIL (section absent).

- [ ] **Step 3: Implement in `context.py`**

Add `relationships` to the top import block.

Add a helper near `_character_state`:

```python
def _relationships(cid: str, croot, cast) -> str:
    try:
        tokens = [f"{a['kind']}:{a['id']}" for a in cast]
        lines = relationships.render_present(cid, tokens, lambda t: relationships.actor_name(croot, t))
        return "# Relationships\n" + "\n".join(lines) if lines else ""
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return ""
```

In `_assemble`, add the section right after `add("Character state", …)`:

```python
    add("Relationships", _relationships(cid, croot, cast))
```

- [ ] **Step 4: Run to verify it passes + full context suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: inject # Relationships (present-cast feelings + bonds) into context"
```

---

## Task 6: Frontend — payload type + read-only structured rows

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `StagedEdit`, `editRows`.
- Produces: `StagedEdit.payload?`; relationship/bond rows render read-only (no textarea) with an approve checkbox and a before→after line; Save still sends approved rows (payload included).

- [ ] **Step 1: Write the failing test**

In `CampaignView.test.tsx`, extend the `absorbScene` mock in `beforeEach` to add a relationship edit alongside the existing one:

```ts
      { id: "feeling:characters:a->characters:b", kind: "relationship",
        target: { kind: "relationships", id: "characters:a->characters:b" }, label: "Ann → Bo",
        field: "feeling", before: "trust 1, affection 1, tension 3", after: "trust 4, affection 3, tension 1",
        authored: false, payload: { from: "characters:a", to: "characters:b", trust: 4, affection: 3, tension: 1, note: "" } }
```

(Append it to the existing `edits: [...]` array.)

Then the test:

```ts
test("relationship rows are read-only and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Ann → Bo");
  // no editable textarea for the relationship row
  expect(screen.queryByLabelText("After Ann → Bo")).toBeNull();
  expect(screen.getByText(/trust 4, affection 3, tension 1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "feeling:characters:a->characters:b",
        payload: expect.objectContaining({ trust: 4 }) })]) })));
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx -t "read-only"`
Expected: FAIL (relationship row currently renders an editable textarea / label present).

- [ ] **Step 3: Add `payload` to the type**

In `client.ts`, extend `StagedEdit`:

```ts
export type StagedEdit = {
  id: string; kind: "character_state" | "lore" | "authored" | "relationship" | "bond";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
};
```

- [ ] **Step 4: Branch the row rendering in `CampaignView`**

Replace the single edit-row body (the `{e.before && …}` + `<textarea …>` block) with a kind-branch: structured rows (`relationship`/`bond`) render read-only; others keep the editable textarea:

```tsx
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
```

(The checkbox `<label>` above this block is unchanged and already carries `aria-label={`Approve ${e.label}`}`.)

- [ ] **Step 5: Style the read-only diff**

Append to `frontend/src/index.css`:

```css
.absorb-diff { font-size: 13px; margin: 2px 0; }
.absorb-diff .absorb-after { color: var(--fg); }
.absorb-diff .absorb-before { margin-right: 6px; }
```

- [ ] **Step 6: Run tests + typecheck**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx` then `npx tsc -b`
Expected: PASS; tsc clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignView.tsx frontend/src/index.css frontend/src/routes/CampaignView.test.tsx
git commit -m "feat: read-only relationship/bond rows in the review checklist"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all pass.
- [ ] Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b` → all pass.

---

## Self-Review notes (spec coverage)

- **relationships.json (directed feelings + canonical bonds):** Task 1.
- **Extraction snapshot + parsed lists, ints 0–5, tokens:** Task 2.
- **Materialize two kinds + payload; asymmetry; no-op & unknown drop:** Task 3.
- **Apply writes relationships.json:** Task 4.
- **`# Relationships` injection, tolerant:** Task 5.
- **Frontend read-only structured rows + payload on save:** Task 6.
- **Deferred (absent):** knowledge, plot, PC-state/voice_drift, campaign-vs-base, inline metric editing.
