# Campaign Record Changes (Phase 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the GM browse the campaign's world records (characters/lore/locations) that play has changed, and see a highlighted line diff of each record's previous version → current version (the delta from the last scene absorb that touched it).

**Architecture:** The write-back choke point `absorb.apply_edits` records the `before`/`after` of every applied *browsable* edit into a rolling `changes.json` (latest change per record). A new `GET …/changes` endpoint resolves record names + scene labels and computes a stdlib-`difflib` line diff per changed field. A read-only `ChangesPanel`, revealed by a tab in `CampaignView`, renders the grouped list + per-field diffs.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest + `@testing-library/react` (frontend). Diff via Python stdlib `difflib` — no new dependency.

## Global Constraints

- Store rooted at `campaigns.campaign_root(cid)`; tests isolate via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Tolerant reads everywhere: a missing/garbled JSON file yields empty, never a 500 (mirrors `plot`/`relationships`).
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Frontend (run **from** `frontend/`): `npx vitest run` and `npx tsc -b`.
- New JSON files are written `json.dumps(data, indent=2, sort_keys=True) + "\n"` (house convention, see `plot._write`).
- Only **applied** (approved) edits are recorded. Browsable kinds: `character_state` & `authored` → `characters/{id}`; `lore` → `{target.kind}/{id}` (`lore` or `locations`). `relationship`/`bond`/`plot` are never recorded.
- The `StagedEdit` shape (fixed): `{id, kind, target:{kind,id}, label, field, before, after, authored, payload?}`.

---

### Task 1: `changes.line_diff` — the pure diff unit

**Files:**
- Create: `backend/src/grimoire/store/changes.py`
- Test: `backend/tests/test_changes_store.py`

**Interfaces:**
- Produces: `line_diff(before: str, after: str) -> list[dict]` — a flat list of `{"op": "equal"|"insert"|"delete", "text": str}`, one per line, in diff order (deletes before inserts within a replaced span).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_changes_store.py
from grimoire.store import changes


def test_line_diff_insert_only():
    d = changes.line_diff("a", "a\nb")
    assert d == [{"op": "equal", "text": "a"}, {"op": "insert", "text": "b"}]


def test_line_diff_delete_only():
    d = changes.line_diff("a\nb", "a")
    assert d == [{"op": "equal", "text": "a"}, {"op": "delete", "text": "b"}]


def test_line_diff_replace_emits_delete_then_insert():
    d = changes.line_diff("a\nold", "a\nnew")
    assert d == [{"op": "equal", "text": "a"},
                 {"op": "delete", "text": "old"},
                 {"op": "insert", "text": "new"}]


def test_line_diff_identical_all_equal():
    assert changes.line_diff("a\nb", "a\nb") == [
        {"op": "equal", "text": "a"}, {"op": "equal", "text": "b"}]


def test_line_diff_empty_sides():
    assert changes.line_diff("", "") == []
    assert changes.line_diff("", "x") == [{"op": "insert", "text": "x"}]
    assert changes.line_diff("x", "") == [{"op": "delete", "text": "x"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.changes'`.

- [ ] **Step 3: Write the module + `line_diff`**

```python
# backend/src/grimoire/store/changes.py
"""Per-campaign record-change log: the latest write-back delta (previous -> current)
for each browsable record (characters/lore/locations). Stored at <campaign>/changes.json,
keyed by "<kind>/<id>". Pure JSON IO + a stdlib line diff. Written by absorb.apply_edits,
read by the GET /changes route.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import campaigns


def line_diff(before: str, after: str) -> list[dict]:
    """Tagged per-line diff of two text blobs. A `replace` span emits its deletes then
    its inserts, so the frontend can render removed-then-added lines."""
    a, b = before.splitlines(), after.splitlines()
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if tag == "equal":
            out += [{"op": "equal", "text": t} for t in a[i1:i2]]
        elif tag == "delete":
            out += [{"op": "delete", "text": t} for t in a[i1:i2]]
        elif tag == "insert":
            out += [{"op": "insert", "text": t} for t in b[j1:j2]]
        else:  # replace
            out += [{"op": "delete", "text": t} for t in a[i1:i2]]
            out += [{"op": "insert", "text": t} for t in b[j1:j2]]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/changes.py backend/tests/test_changes_store.py
git commit -m "feat(changes): line_diff — stdlib difflib per-line tagged diff"
```

---

### Task 2: `changes.read` / `changes.record` + store export

**Files:**
- Modify: `backend/src/grimoire/store/changes.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_changes_store.py`

**Interfaces:**
- Consumes: `campaigns.campaign_root(cid)`.
- Produces:
  - `read(cid: str) -> dict` — the `changes.json` map (`{}` on missing/garbled).
  - `record(cid: str, sid: str, changes: dict[str, list[dict]]) -> None` — upsert each `ref -> fields` as `{"scene": sid, "fields": fields}`, replacing any prior entry. No-op on empty `changes`.
  - `store.changes` is importable.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_changes_store.py
from grimoire.store import worlds, campaigns


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_record_and_read_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fields = [{"field": "body", "label": "Harbor — locations", "before": "old", "after": "new"}]
    changes.record(cid, "s1", {"locations/harbor": fields})
    assert changes.read(cid) == {"locations/harbor": {"scene": "s1", "fields": fields}}


def test_record_replaces_prior_entry(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {"lore/pact": [{"field": "body", "label": "L", "before": "a", "after": "b"}]})
    changes.record(cid, "s2", {"lore/pact": [{"field": "body", "label": "L", "before": "b", "after": "c"}]})
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s2" and entry["fields"][0]["before"] == "b"


def test_record_empty_is_noop(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {})
    assert changes.read(cid) == {}


def test_read_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "changes.json").write_text("{not json", encoding="utf-8")
    assert changes.read(cid) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.changes' has no attribute 'read'`.

- [ ] **Step 3: Add `_path`, `read`, `record`**

```python
# append to backend/src/grimoire/store/changes.py
def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "changes.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def record(cid: str, sid: str, changes: dict[str, list[dict]]) -> None:
    """Upsert the touched records, replacing any prior entry (rolling: only the latest
    write-back per record is kept). No-op when nothing was recorded."""
    if not changes:
        return
    data = read(cid)
    for ref, fields in changes.items():
        data[ref] = {"scene": sid, "fields": fields}
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

Then export `changes` from the store package. In `backend/src/grimoire/store/__init__.py`, add `changes` to the alphabetical import tuple (line ~5–9, between `campaigns` and `characters`):

```python
from . import (
    absorb, appearances, assets, briefs, campaigns, cards, changes, characters, chronicle,
    chub, context, entities, fetch, greetings, localize, lorebook, pcs, playing, playstate,
    plot, relationships, scenes, suggest, sync, tags, worlds,
)
```

and add `"changes",` to the `__all__` list (next to `"chronicle",`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/changes.py backend/src/grimoire/store/__init__.py backend/tests/test_changes_store.py
git commit -m "feat(changes): read/record rolling changes.json + store export"
```

---

### Task 3: `apply_edits` records applied browsable edits

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (import `changes`; `apply_edits` at 301–335)
- Modify: `backend/src/grimoire/routes.py` (`put_chronicle` at ~1194)
- Test: `backend/tests/test_changes_store.py`

**Interfaces:**
- Consumes: `changes.record`, existing `StagedEdit` dicts.
- Produces: `apply_edits(cid, edits, sid=None) -> list[str]` — unchanged return (applied ids); when `sid` is set, applied browsable edits are captured into `changes.json`. `sid=None` skips recording.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_changes_store.py
from grimoire.store import absorb, entities, scenes


def _lore_edit(before, after):
    return {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": before, "after": after,
            "authored": False}


def test_apply_records_lore_edit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nnew line")], "s1")
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s1"
    assert entry["fields"] == [{"field": "body", "label": "The Pact — lore",
                                "before": "old body", "after": "old body\n\nnew line"}]


def test_apply_accumulates_multiple_fields_per_record(monkeypatch, tmp_path):
    from grimoire.store import characters, playstate
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card("Mara")
    card["data"]["personality"] = "aloof"
    ch = characters.create_character(croot, "Mara", "main", card)[0]
    playstate.write_state(croot, ch, "calm")
    sid = scenes.create_scene(cid, "S")
    from grimoire.store import appearances
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    cs = {"id": f"character_state:{ch}", "kind": "character_state",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — current state",
          "field": "current_state", "before": "calm", "after": "shaken", "authored": False}
    au = {"id": f"authored:{ch}:personality", "kind": "authored",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — personality (card edit)",
          "field": "personality", "before": "aloof", "after": "warmer", "authored": True}
    absorb.apply_edits(cid, [cs, au], sid)
    fields = changes.read(cid)[f"characters/{ch}"]["fields"]
    assert {f["field"] for f in fields} == {"current_state", "personality"}


def test_apply_skips_non_browsable_kinds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot_edit = {"id": "plot:the-map", "kind": "plot", "target": {"kind": "plot", "id": "the-map"},
                 "label": "The map — advanced", "field": "beat", "before": "", "after": "It moved.",
                 "authored": False,
                 "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s1"}}
    absorb.apply_edits(cid, [plot_edit], "s1")
    assert changes.read(cid) == {}


def test_apply_without_sid_records_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nx")])
    assert changes.read(cid) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py -q`
Expected: FAIL — `test_apply_records_lore_edit` gets `KeyError: 'lore/pact'` (nothing recorded yet).

- [ ] **Step 3: Add the recording hook to `apply_edits`**

In `backend/src/grimoire/store/absorb.py`, add `changes` to the store import (line 11–12 tuple):

```python
from . import (
    appearances, campaigns, changes, characters, chronicle, entities, pcs, playstate,
    plot, relationships)
```

Replace `apply_edits` (lines 301–335) with:

```python
_BROWSABLE_KINDS = ("character_state", "lore", "authored")


def apply_edits(cid: str, edits: list[dict], sid: str | None = None) -> list[str]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: a missing or
    broken target is skipped. Returns the ids actually applied. When `sid` is given, the
    before/after of each applied *browsable* edit (characters/lore/locations) is captured
    into changes.json (the latest write-back delta per record)."""
    croot = campaigns.campaign_root(cid)
    applied: list[str] = []
    recorded: dict[str, list[dict]] = {}
    for e in edits:
        try:
            kind, target, after = e["kind"], e["target"], e.get("after", "")
            if kind == "character_state":
                playstate.write_state(croot, target["id"], after)
            elif kind == "lore":
                entities.update_entity(croot, target["kind"], target["id"], body=after)
            elif kind == "authored":
                if e["field"] not in _CARD_FIELDS:
                    continue  # re-guard: PUT edits are client-supplied, not re-materialized
                vid = appearances.locked_version(cid, "characters", target["id"])
                card = characters.read_card(croot, target["id"], vid)
                card["data"][e["field"]] = after
                characters.update_version(croot, target["id"], vid, card)
            elif kind == "relationship":
                p = e["payload"]
                relationships.set_feeling(cid, p["from"], p["to"], p["trust"], p["affection"],
                                          p["tension"], p.get("note", ""))
            elif kind == "bond":
                p = e["payload"]
                relationships.set_bond(cid, p["a"], p["b"], p["type"])
            elif kind == "plot":
                p = e["payload"]
                plot.set_movement(cid, p["id"], p["title"], p["status"], after, p["scene"])
            else:
                continue
            applied.append(e["id"])
            if sid and kind in _BROWSABLE_KINDS:
                ref = f"{target['kind']}/{target['id']}"
                recorded.setdefault(ref, []).append(
                    {"field": e.get("field", ""), "label": e.get("label", ""),
                     "before": e.get("before", ""), "after": after})
        except Exception:  # noqa: BLE001 — best-effort per edit
            continue
    if sid:
        changes.record(cid, sid, recorded)
    return applied
```

- [ ] **Step 4: Pass `sid` from the route**

In `backend/src/grimoire/routes.py`, `put_chronicle` (~line 1194), change:

```python
    applied = store.absorb.apply_edits(cid, body.edits, sid)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_changes_store.py backend/tests/test_absorb_store.py -q`
Expected: PASS (existing absorb tests still green — they call `apply_edits` without `sid`, which now records nothing).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/absorb.py backend/src/grimoire/routes.py backend/tests/test_changes_store.py
git commit -m "feat(changes): capture applied browsable edits in apply_edits"
```

---

### Task 4: `GET /campaigns/{cid}/changes` endpoint

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add route + `_record_name` helper, before the generic `/{kind}` routes at line 1387)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `changes.read`, `changes.line_diff`, `scenes.list_scenes`, `chronicle.read_chronicle`, `characters.read_character`, `entities.read_entity`, `entities.ENTITY_KINDS`.
- Produces: `GET /campaigns/{cid}/changes` → `list[{ref:{kind,id}, name, scene:{id,title,date}, fields:[{field,label,diff:[{op,text}]}]}]`, sorted by `(kind, name)`. `404` for unknown campaign. Records whose entity no longer exists are dropped.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_routes.py
def _apply_lore_change(client, cid):
    croot = store.campaigns.campaign_root(cid)
    store.entities.create_entity(croot, "lore", "Pact", body="old body")
    edit = {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": "old body",
            "after": "old body\nnew line", "authored": False}
    store.absorb.apply_edits(cid, [edit], "s1")


def test_get_changes_returns_name_scene_and_diff(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)  # records under scene "s1" (never created -> title falls back)
    out = client.get(f"/api/campaigns/{cid}/changes").json()
    assert len(out) == 1
    rec = out[0]
    assert rec["ref"] == {"kind": "lore", "id": "pact"} and rec["name"] == "The Pact"
    assert rec["scene"]["id"] == "s1"  # deleted/unknown scene -> title falls back to id
    ops = [d["op"] for d in rec["fields"][0]["diff"]]
    assert "insert" in ops


def test_get_changes_empty_and_not_shadowed_by_kind_route(client):
    _, cid = _campaign(client)
    res = client.get(f"/api/campaigns/{cid}/changes")
    assert res.status_code == 200 and res.json() == []  # not routed to the generic /{kind}


def test_get_changes_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/changes").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k changes`
Expected: FAIL — the empty-campaign case returns `404`/`unknown kind` (currently routed to the generic `/{kind}` handler), and the diff case `KeyError`.

- [ ] **Step 3: Add the route + helper**

In `backend/src/grimoire/routes.py`, immediately **after** `put_chronicle` (i.e. after line ~1195, well before the generic `@router.get("/campaigns/{cid}/{kind}")` at 1387):

```python
def _record_name(croot, kind: str, eid: str) -> str | None:
    """Display name for a campaign record, or None if it no longer exists."""
    try:
        if kind == "characters":
            return store.characters.read_character(croot, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.entities.read_entity(croot, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound):
        return None
    return None


@router.get("/campaigns/{cid}/changes")
def get_changes(cid: str):
    croot = _campaign_root_or_404(cid)
    data = store.changes.read(cid)
    scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    out: list[dict] = []
    for ref, entry in data.items():
        kind, _, eid = ref.partition("/")
        name = _record_name(croot, kind, eid)
        if name is None:
            continue  # record deleted since the change was captured
        sid = entry.get("scene", "")
        s, c = scenes_by_id.get(sid, {}), chron.get(sid, {})
        fields = [{"field": f.get("field", ""), "label": f.get("label", ""),
                   "diff": store.changes.line_diff(f.get("before", ""), f.get("after", ""))}
                  for f in entry.get("fields", [])]
        out.append({"ref": {"kind": kind, "id": eid}, "name": name,
                    "scene": {"id": sid, "title": s.get("title", sid), "date": c.get("date", "")},
                    "fields": fields})
    out.sort(key=lambda r: (r["ref"]["kind"], r["name"]))
    return out
```

Note the comment block at line 969/998 explains why "incoming"/"scenes" are declared before the generic `/{kind}` routes — `changes` follows the same rule (it is above line 1387, so no extra guard is needed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k changes`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(changes): GET /campaigns/{cid}/changes (name+scene+per-field diff)"
```

---

### Task 5: Frontend API — `campaignChanges` + types

**Files:**
- Modify: `frontend/src/api/client.ts` (types near line 148–167; `api` object near line 226)
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
  export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
  export type RecordChange = {
    ref: { kind: string; id: string }; name: string;
    scene: { id: string; title: string; date: string };
    fields: FieldDiff[];
  };
  ```
  `api.campaignChanges(cid: string) => Promise<RecordChange[]>`.

- [ ] **Step 1: Write the failing test**

```ts
// append to frontend/src/api/client.test.ts
test("campaignChanges GETs the campaign changes endpoint", async () => {
  const rows = [{ ref: { kind: "lore", id: "pact" }, name: "The Pact",
    scene: { id: "s1", title: "S", date: "" },
    fields: [{ field: "body", label: "The Pact — lore",
      diff: [{ op: "insert", text: "new" }] }] }];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(rows), { status: 200 }));
  const out = await api.campaignChanges("c1");
  expect(fetchMock).toHaveBeenCalledWith("/api/campaigns/c1/changes", expect.objectContaining({ method: "GET" }));
  expect(out).toEqual(rows);
});
```

(If `client.test.ts` does not already `import { api } from "./client"` / `import { describe, test, expect, vi } from "vitest"`, match the existing imports at the top of that file.)

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/api/client.test.ts`
Expected: FAIL — `api.campaignChanges is not a function`.

- [ ] **Step 3: Add the types + method**

In `frontend/src/api/client.ts`, after the `ChronicleEntry` type (line ~167):

```ts
export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
export type RecordChange = {
  ref: { kind: string; id: string }; name: string;
  scene: { id: string; title: string; date: string };
  fields: FieldDiff[];
};
```

In the `api` object, in the `// campaigns` group (after `deleteCampaign`, line ~223):

```ts
  campaignChanges: (cid: string) =>
    request<RecordChange[]>("GET", `/api/campaigns/${cid}/changes`),
```

- [ ] **Step 4: Run test + typecheck**

Run (from `frontend/`): `npx vitest run src/api/client.test.ts && npx tsc -b`
Expected: PASS + clean typecheck.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(changes): api.campaignChanges + RecordChange/FieldDiff/DiffLine types"
```

---

### Task 6: `ChangesPanel` component + diff CSS

**Files:**
- Create: `frontend/src/components/ChangesPanel.tsx`
- Create: `frontend/src/components/ChangesPanel.test.tsx`
- Modify: `frontend/src/index.css` (diff line classes)

**Interfaces:**
- Consumes: `api.campaignChanges`, `RecordChange`.
- Produces: `export function ChangesPanel({ cid }: { cid: string })` — a read-only list/detail. List rows grouped by kind (Characters / Lore / Locations); selecting a record shows its field diffs. Empty state when there are no changes.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/ChangesPanel.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { ChangesPanel } from "./ChangesPanel";

vi.mock("../api/client", () => ({ api: { campaignChanges: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => vi.clearAllMocks());

const HARBOR = {
  ref: { kind: "locations", id: "harbor" }, name: "Harbor",
  scene: { id: "s1", title: "The blockade", date: "12 Harvestmoon" },
  fields: [{ field: "body", label: "Harbor — locations",
    diff: [{ op: "equal", text: "A busy port town." },
           { op: "insert", text: "Now blockaded." }] }],
};

test("lists changed records and shows a field diff on select", async () => {
  (api.campaignChanges as any).mockResolvedValue([HARBOR]);
  render(<ChangesPanel cid="c1" />);
  fireEvent.click(await screen.findByRole("button", { name: /Harbor/ }));
  expect(screen.getByText("Now blockaded.")).toBeInTheDocument();
  expect(screen.getByText("Now blockaded.").className).toContain("diff-insert");
  expect(screen.getByText("A busy port town.").className).toContain("diff-equal");
});

test("shows an empty state when nothing has changed", async () => {
  (api.campaignChanges as any).mockResolvedValue([]);
  render(<ChangesPanel cid="c1" />);
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ChangesPanel.test.tsx`
Expected: FAIL — cannot resolve `./ChangesPanel`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/components/ChangesPanel.tsx
import { useEffect, useState } from "react";
import { api, type RecordChange } from "../api/client";

const GROUPS: { kind: string; label: string }[] = [
  { kind: "characters", label: "Characters" },
  { kind: "lore", label: "Lore" },
  { kind: "locations", label: "Locations" },
];

export function ChangesPanel({ cid }: { cid: string }) {
  const [rows, setRows] = useState<RecordChange[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    api.campaignChanges(cid).then(setRows).catch(() => setRows([]));
  }, [cid]);

  if (rows === null) return <div className="changes-panel">Loading…</div>;
  if (rows.length === 0)
    return <div className="changes-panel"><p className="field-hint">No record changes yet.</p></div>;

  const active = rows.find((r) => `${r.ref.kind}/${r.ref.id}` === sel) ?? null;

  return (
    <div className="changes-panel editor">
      <div className="editor-list">
        {GROUPS.map((g) => {
          const group = rows.filter((r) => r.ref.kind === g.kind);
          if (!group.length) return null;
          return (
            <div key={g.kind} className="side-section">
              <h4>{g.label}</h4>
              {group.map((r) => {
                const key = `${r.ref.kind}/${r.ref.id}`;
                return (
                  <button key={key} className={"row" + (key === sel ? " active" : "")}
                          onClick={() => setSel(key)}>
                    {r.name}
                    <span className="field-hint"> · changed in {r.scene.title}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="editor-body">
        {active ? (
          <div className="detail-view">
            <h3>{active.name}</h3>
            {active.fields.map((f) => (
              <div key={f.field} className="side-section">
                <h4>{f.label}</h4>
                <pre className="record-diff">
                  {f.diff.map((d, i) => (
                    <div key={i} className={"diff-line diff-" + d.op}>{d.text}</div>
                  ))}
                </pre>
              </div>
            ))}
          </div>
        ) : (
          <p className="field-hint">Select a record to see what changed.</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add diff CSS**

Append to `frontend/src/index.css`:

```css
.record-diff { white-space: pre-wrap; font-family: var(--mono, monospace); margin: 0; }
.diff-line { padding: 0 0.35rem; }
.diff-insert { background: rgba(60, 160, 90, 0.18); }
.diff-delete { background: rgba(200, 70, 70, 0.18); text-decoration: line-through; }
.diff-equal { opacity: 0.7; }
```

- [ ] **Step 5: Run tests + typecheck**

Run (from `frontend/`): `npx vitest run src/components/ChangesPanel.test.tsx && npx tsc -b`
Expected: PASS + clean typecheck.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ChangesPanel.tsx frontend/src/components/ChangesPanel.test.tsx frontend/src/index.css
git commit -m "feat(changes): ChangesPanel read-only list/detail + diff CSS"
```

---

### Task 7: Wire the Changes tab into `CampaignView`

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (import + state near line 21–37; `.campaign-header` at 190–196; render toggle after the header)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `ChangesPanel`.
- Produces: a "Changes" toggle button in the campaign header that reveals `<ChangesPanel cid={cid} />` in the main column (and hides it again).

- [ ] **Step 1: Write the failing test**

```tsx
// append to frontend/src/routes/CampaignView.test.tsx
test("Changes tab reveals the changes panel", async () => {
  (api.campaignChanges as any) = vi.fn().mockResolvedValue([]);
  renderView(); // use this file's existing render helper
  fireEvent.click(await screen.findByRole("button", { name: /^Changes$/ }));
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});
```

(Match this file's existing render helper name and its `vi.mock("../api/client", …)` block — add `campaignChanges: vi.fn()` to that mock's `api` object so the import resolves.)

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx -t "Changes tab"`
Expected: FAIL — no `Changes` button.

- [ ] **Step 3: Add the import, state, toggle, and render**

In `frontend/src/routes/CampaignView.tsx`:

Add the import (after the `CastPanel` import, line 8):

```tsx
import { ChangesPanel } from "../components/ChangesPanel";
```

Add state (with the other `useState` calls, ~line 33):

```tsx
  const [showChanges, setShowChanges] = useState(false);
```

In the `.campaign-header` (after the End-scene button, before `</div>` at line 196), add:

```tsx
          <button className="subtle" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
```

Immediately after the `</div>` that closes `.campaign-header` (line 196), render the panel when toggled:

```tsx
        {showChanges && <ChangesPanel cid={cid} />}
```

- [ ] **Step 4: Run test + typecheck**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b`
Expected: PASS + clean typecheck.

- [ ] **Step 5: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(changes): CampaignView Changes tab reveals the panel"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` — all green.
- [ ] Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b` — all green, clean types.
- [ ] Manual smoke: end a scene with an approved lore/location or character-state edit, open the campaign's **Changes** tab, confirm the record appears under its group and its field shows a red/green line diff.
