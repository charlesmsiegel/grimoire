# Greetings & Plot Maps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class world greetings, a plot map (directed `leads_to` edges + mutual exclusion + AND/OR predecessor join), tag gating against a campaign's player PCs, start-a-scene-from-greeting, and an ephemeral world-informed opener generator.

**Architecture:** A new `greetings.py` owns world greeting CRUD, the `plotmap.json` sidecar, import-from-card, and a pure `availability()`. A new `playing.py` owns campaign play-state (`played.json`, `available_greetings`, `start_from_greeting`). `context.py` gains `scene_substitutions()` (shared name→token map) and `build_opener_messages()`. Routes follow the literal-before-generic ordering convention.

**Tech Stack:** Python 3, FastAPI, pytest. Markdown + JSON file store under `~/.grimoire`. No new dependencies.

## Global Constraints

- Run tests from `backend/` with `.venv/Scripts/python.exe -m pytest -q`. Suite is green at **129**; keep it green every task.
- Frontmatter writer is **string-scalar only** — nested data (plot edges, played set) lives in JSON sidecars, never frontmatter lists.
- IDs are `slugify` + `uniquify`, no date prefix.
- Store modules: one responsibility each; re-export in `store/__init__.py`; routes in `routes.py` with **dedicated literal routes declared BEFORE the generic `/{kind}` catch-alls**.
- Tests use a temp `GRIMOIRE_HOME` (monkeypatch env) and the existing fake-OpenRouter pattern in `tests/test_routes.py`.
- Commit message footer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Do not push, open PRs, or touch `main`.

## File Structure

- Create `backend/src/grimoire/store/greetings.py` — world greeting CRUD, plotmap IO, import-from-card, pure `availability()`.
- Create `backend/src/grimoire/store/playing.py` — campaign played-set, `player_tags`, `available_greetings`, `start_from_greeting`.
- Modify `backend/src/grimoire/store/context.py` — add `scene_substitutions()` + `build_opener_messages()`.
- Modify `backend/src/grimoire/store/__init__.py` — register new modules + exceptions.
- Modify `backend/src/grimoire/routes.py` — world greeting routes, campaign play routes, ephemeral opener stream.
- Create `backend/tests/test_greetings_store.py`, `backend/tests/test_playing_store.py`.
- Modify `backend/tests/test_context.py`, `backend/tests/test_routes.py`.

---

### Task 1: `greetings.py` — greeting CRUD, plotmap, import-from-card

**Files:**
- Create: `backend/src/grimoire/store/greetings.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_greetings_store.py`

**Interfaces:**
- Consumes: `characters.read_card(root, cid, vid)`, `frontmatter.dump_frontmatter/parse_frontmatter`, `paths.slugify/uniquify`.
- Produces:
  - `list_greetings(root) -> [{id,name,character,version,requires_tags:[...],predecessor_join}]`
  - `read_greeting(root, gid) -> {"meta": {...}, "body": str}` (raises `GreetingNotFound`)
  - `create_greeting(root, name, character, version, body="", requires_tags=None, predecessor_join="all") -> gid`
  - `update_greeting(root, gid, *, name=None, body=None, requires_tags=None, predecessor_join=None)`
  - `delete_greeting(root, gid)` (prunes plotmap node + inbound refs)
  - `read_plotmap(root) -> {gid: {"leads_to":[...], "excludes":[...]}}`
  - `set_edges(root, gid, leads_to=None, excludes=None)`
  - `import_from_character(root, char_id, vid) -> [gid, …]`
  - `availability(world_root, plotmap, played, player_tags) -> [{id,name,available,reasons}]`
  - `GreetingNotFound`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_greetings_store.py
from pathlib import Path

import pytest

from grimoire.store import characters, greetings


def _world(tmp_path) -> Path:
    (tmp_path / "greetings").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_create_read_list_roundtrip(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "Rescued at Sea", "seraphine", "default",
                                    body="You wake on the deck.",
                                    requires_tags=["sailor"], predecessor_join="any")
    g = greetings.read_greeting(root, gid)
    assert g["meta"]["character"] == "seraphine"
    assert g["meta"]["version"] == "default"
    assert g["meta"]["requires_tags"] == ["sailor"]
    assert g["meta"]["predecessor_join"] == "any"
    assert g["body"].strip() == "You wake on the deck."
    assert [x["id"] for x in greetings.list_greetings(root)] == [gid]


def test_update_and_missing(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "G", "c", "v")
    greetings.update_greeting(root, gid, body="new text", requires_tags=["a", "b"])
    g = greetings.read_greeting(root, gid)
    assert g["body"].strip() == "new text"
    assert g["meta"]["requires_tags"] == ["a", "b"]
    with pytest.raises(greetings.GreetingNotFound):
        greetings.read_greeting(root, "nope")


def test_plotmap_edges_and_delete_prunes(tmp_path):
    root = _world(tmp_path)
    a = greetings.create_greeting(root, "A", "c", "v")
    b = greetings.create_greeting(root, "B", "c", "v")
    greetings.set_edges(root, a, leads_to=[b], excludes=[b])
    assert greetings.read_plotmap(root)[a] == {"leads_to": [b], "excludes": [b]}
    greetings.delete_greeting(root, b)
    pm = greetings.read_plotmap(root)
    assert b not in pm
    assert pm[a]["leads_to"] == [] and pm[a]["excludes"] == []


def test_import_from_character(tmp_path):
    root = _world(tmp_path)
    card = characters.blank_card("Seraphine")
    card["data"].update(first_mes="Hello there.",
                         alternate_greetings=["Alt one.", "  ", "Alt two."])
    characters.create_character(root, "Seraphine", "default", card)
    gids = greetings.import_from_character(root, "seraphine", "default")
    # first_mes + 2 non-blank alternates (the blank alt is skipped)
    assert len(gids) == 3
    bodies = sorted(greetings.read_greeting(root, g)["body"].strip() for g in gids)
    assert bodies == ["Alt one.", "Alt two.", "Hello there."]
    assert all(greetings.read_greeting(root, g)["meta"]["character"] == "seraphine" for g in gids)


def test_import_empty_card_returns_empty(tmp_path):
    root = _world(tmp_path)
    characters.create_character(root, "Blank", "default", characters.blank_card("Blank"))
    assert greetings.import_from_character(root, "blank", "default") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_greetings_store.py -q`
Expected: FAIL (`ModuleNotFoundError: greetings` / `AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/grimoire/store/greetings.py
"""World greeting objects + the plot map.

A greeting is a markdown file under <world>/greetings/<gid>.md that references a
character + version and carries scalar gating attributes. The directed plot-map
edges (leads_to / excludes) are nested data, so they live in <world>/plotmap.json
keyed by greeting id.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import characters
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class GreetingNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _greetings_dir(root: Path) -> Path:
    return root / "greetings"


def _greeting_path(root: Path, gid: str) -> Path:
    return _greetings_dir(root) / f"{gid}.md"


def _plotmap_path(root: Path) -> Path:
    return root / "plotmap.json"


def _tags_list(s: str) -> list[str]:
    return [t for t in s.split(",") if t]


def _meta_dict(gid: str, meta: dict) -> dict:
    return {
        "id": gid,
        "name": meta.get("name", gid),
        "character": meta.get("character", ""),
        "version": meta.get("version", ""),
        "requires_tags": _tags_list(meta.get("requires_tags", "")),
        "predecessor_join": meta.get("predecessor_join", "all"),
    }


def create_greeting(root: Path, name: str, character: str, version: str, body: str = "",
                    requires_tags: list[str] | None = None, predecessor_join: str = "all") -> str:
    _greetings_dir(root).mkdir(parents=True, exist_ok=True)
    gid = uniquify(slugify(name), lambda c: _greeting_path(root, c).exists())
    meta = {"name": name, "character": character, "version": version,
            "requires_tags": ",".join(requires_tags or []), "predecessor_join": predecessor_join}
    _greeting_path(root, gid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return gid


def read_greeting(root: Path, gid: str) -> dict:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(gid, meta), "body": body}


def list_greetings(root: Path) -> list[dict]:
    d = _greetings_dir(root)
    if not d.exists():
        return []
    return [read_greeting(root, p.stem)["meta"] for p in sorted(d.glob("*.md"))]


def update_greeting(root: Path, gid: str, *, name: str | None = None, body: str | None = None,
                    requires_tags: list[str] | None = None, predecessor_join: str | None = None) -> None:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if requires_tags is not None:
        meta["requires_tags"] = ",".join(requires_tags)
    if predecessor_join is not None:
        meta["predecessor_join"] = predecessor_join
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def read_plotmap(root: Path) -> dict:
    p = _plotmap_path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_plotmap(root: Path, data: dict) -> None:
    _plotmap_path(root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def edges_of(plotmap: dict, gid: str) -> dict:
    e = plotmap.get(gid) or {}
    return {"leads_to": e.get("leads_to", []), "excludes": e.get("excludes", [])}


def set_edges(root: Path, gid: str, leads_to: list[str] | None = None,
              excludes: list[str] | None = None) -> None:
    data = read_plotmap(root)
    cur = edges_of(data, gid)
    if leads_to is not None:
        cur["leads_to"] = list(leads_to)
    if excludes is not None:
        cur["excludes"] = list(excludes)
    data[gid] = cur
    _write_plotmap(root, data)


def delete_greeting(root: Path, gid: str) -> None:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    p.unlink()
    data = read_plotmap(root)
    changed = data.pop(gid, None) is not None
    for e in data.values():
        for key in ("leads_to", "excludes"):
            if gid in e.get(key, []):
                e[key] = [x for x in e[key] if x != gid]
                changed = True
    if changed:
        _write_plotmap(root, data)


def import_from_character(root: Path, char_id: str, vid: str) -> list[str]:
    data = characters.read_card(root, char_id, vid).get("data", {})
    cname = data.get("name", char_id)
    items: list[tuple[str, str]] = []
    first = data.get("first_mes", "")
    if isinstance(first, str) and first.strip():
        items.append((cname, first))
    for i, alt in enumerate(data.get("alternate_greetings", []) or [], start=1):
        if isinstance(alt, str) and alt.strip():
            items.append((f"{cname} (alt {i})", alt))
    return [create_greeting(root, name, char_id, vid, body) for name, body in items]


def availability(world_root: Path, plotmap: dict, played, player_tags) -> list[dict]:
    """Pure: which greetings are startable given the played set + player tags."""
    played = set(played)
    player_tags = set(player_tags)
    items = list_greetings(world_root)
    preds: dict[str, set] = {g["id"]: set() for g in items}
    for src, e in plotmap.items():
        for tgt in e.get("leads_to", []):
            if tgt in preds:
                preds[tgt].add(src)
    out: list[dict] = []
    for g in items:
        gid = g["id"]
        reasons: list[str] = []
        p = preds[gid]
        if p:
            if g["predecessor_join"] == "any":
                if not (p & played):
                    reasons.append("predecessors not played (any)")
            elif not (p <= played):
                reasons.append("predecessors not played (all)")
        excluded = ({x for x in played if gid in edges_of(plotmap, x)["excludes"]}
                    or set(edges_of(plotmap, gid)["excludes"]) & played)
        if excluded:
            reasons.append("excluded by a played greeting")
        if not (set(g["requires_tags"]) <= player_tags):
            reasons.append("missing required tags")
        out.append({"id": gid, "name": g["name"], "available": not reasons, "reasons": reasons})
    return out
```

Then register in `backend/src/grimoire/store/__init__.py`: add `greetings` to the `from . import …` line, add `from .greetings import GreetingNotFound`, and add `"greetings"` and `"GreetingNotFound"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_greetings_store.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Run full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q` (expect 134 passed).
```bash
git add backend/src/grimoire/store/greetings.py backend/src/grimoire/store/__init__.py backend/tests/test_greetings_store.py
git commit -m "feat: world greetings + plot map (greetings.py)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: pure `availability` coverage + `playing.py` (played set, player tags, available_greetings)

**Files:**
- Create: `backend/src/grimoire/store/playing.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_greetings_store.py` (availability cases), `backend/tests/test_playing_store.py`

**Interfaces:**
- Consumes: `greetings.availability/read_plotmap`, `appearances.roster`, `pcs.read_pc`, `campaigns.*`, `worlds.world_root`.
- Produces:
  - `read_played(cid) -> set[str]`
  - `player_tags(cid) -> set[str]`
  - `available_greetings(cid) -> [{id,name,available,reasons}]`
  - `PlayError`
  - (`start_from_greeting` lands in Task 3.)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_greetings_store.py`:

```python
def test_availability_gate_join_exclusion_tags(tmp_path):
    root = _world(tmp_path)
    a = greetings.create_greeting(root, "A", "c", "v")
    b = greetings.create_greeting(root, "B", "c", "v", predecessor_join="all")
    c = greetings.create_greeting(root, "C", "c", "v", predecessor_join="any")
    greetings.set_edges(root, a, leads_to=[b, c])
    a2 = greetings.create_greeting(root, "A2", "c", "v")
    greetings.set_edges(root, a2, leads_to=[b])  # b now has preds {a, a2}
    locked = greetings.create_greeting(root, "Locked", "c", "v", requires_tags=["vip"])
    excl = greetings.create_greeting(root, "Excl", "c", "v")
    greetings.set_edges(root, a, excludes=[excl])

    pm = greetings.read_plotmap(root)
    # nothing played, no tags
    avail = {x["id"]: x["available"] for x in greetings.availability(root, pm, set(), set())}
    assert avail[a] is True            # no predecessors
    assert avail[b] is False           # all-join, no preds played
    assert avail[c] is False           # any-join, no preds played
    assert avail[locked] is False      # missing tag
    assert avail[excl] is True         # excluder not played yet

    avail = {x["id"]: x["available"] for x in greetings.availability(root, pm, {a}, {"vip"})}
    assert avail[c] is True            # any-join satisfied by a
    assert avail[b] is False           # all-join still needs a2
    assert avail[locked] is True       # tag now present
    assert avail[excl] is False        # a played -> excl excluded (symmetric)

    avail = {x["id"]: x["available"] for x in greetings.availability(root, pm, {a, a2}, set())}
    assert avail[b] is True            # all preds played
```

Create `backend/tests/test_playing_store.py`:

```python
from grimoire.store import appearances as ap
from grimoire.store import campaigns, greetings, pcs, playing, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return wid, cid, sid


def test_played_roundtrip(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    assert playing.read_played(cid) == set()
    playing._mark_played(cid, "g1")
    playing._mark_played(cid, "g1")  # idempotent
    playing._mark_played(cid, "g2")
    assert playing.read_played(cid) == {"g1", "g2"}


def test_player_tags_unions_player_pcs_only(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    from grimoire.store import tags
    for t in ("student", "sailor"):
        tags.add_tag(wroot, t)
    pcs.create_pc(wroot, "Elara", ["student"])
    pcs.create_pc(wroot, "Bryn", ["sailor"])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    ap.appear(cid, sid, "pcs", "bryn", "default", "player")
    assert playing.player_tags(cid) == {"student", "sailor"}


def test_available_greetings_end_to_end(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    from grimoire.store import tags
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "c", "v", requires_tags=["vip"])
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is False
    pcs.create_pc(wroot, "Elara", ["vip"])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_greetings_store.py -q`
Expected: FAIL (`ModuleNotFoundError: playing`; availability test passes already if Task 1 correct — that's fine, it documents behavior).

- [ ] **Step 3: Write `playing.py`**

```python
# backend/src/grimoire/store/playing.py
"""Campaign play-state: the played-greeting set, availability bound to a
campaign, and starting a scene from a greeting."""

from __future__ import annotations

import json
from pathlib import Path

from . import appearances, campaigns, context, greetings, pcs, scenes, worlds


class PlayError(Exception):
    pass


def _world_root(cid: str) -> Path:
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


def _played_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "played.json"


def read_played(cid: str) -> set[str]:
    p = _played_path(cid)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text(encoding="utf-8")))


def _mark_played(cid: str, gid: str) -> None:
    played = read_played(cid)
    played.add(gid)
    _played_path(cid).write_text(json.dumps(sorted(played), indent=2) + "\n", encoding="utf-8")


def player_tags(cid: str) -> set[str]:
    croot = campaigns.campaign_root(cid)
    out: set[str] = set()
    for a in appearances.roster(cid):
        if a["role"] == "player" and a["kind"] == "pcs":
            try:
                out |= set(pcs.read_pc(croot, a["id"])["meta"]["tags"])
            except pcs.PCNotFound:
                continue
    return out


def available_greetings(cid: str) -> list[dict]:
    wroot = _world_root(cid)
    return greetings.availability(wroot, greetings.read_plotmap(wroot),
                                  read_played(cid), player_tags(cid))


def start_from_greeting(cid: str, sid: str, gid: str) -> None:
    wroot = _world_root(cid)
    g = greetings.read_greeting(wroot, gid)["meta"]   # raises GreetingNotFound
    scene = scenes.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    if not {a["id"]: a["available"] for a in available_greetings(cid)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    appearances.appear(cid, sid, "characters", g["character"], g["version"], "npc")
    _mark_played(cid, gid)
    text = context._substitute(greetings.read_greeting(wroot, gid)["body"],
                               context.scene_substitutions(cid, sid))
    scenes.append_message(cid, sid, "assistant", text)
```

Register `playing` + `PlayError` in `store/__init__.py` (`from . import …`, `from .playing import PlayError`, add both to `__all__`).

> Note: `start_from_greeting` is exercised in Task 3 (it depends on `context.scene_substitutions`, added there). It is written now so `playing.py` is whole; its test lands in Task 3.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_greetings_store.py -q`
Expected: PASS (note: `start_from_greeting` not yet tested).

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
```bash
git add backend/src/grimoire/store/playing.py backend/src/grimoire/store/__init__.py backend/tests/test_playing_store.py backend/tests/test_greetings_store.py
git commit -m "feat: campaign play-state + greeting availability (playing.py)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `context.scene_substitutions` + `start_from_greeting` behavior + routes

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_playing_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `appearances.scene_cast/locked_version`, `characters.read_card`, `pcs.read_persona`, `playing.start_from_greeting`, `greetings.*`.
- Produces:
  - `context.scene_substitutions(cid, sid) -> {"{{user}}": str, "{{char}}": str}`
  - Routes: `GET/POST /worlds/{wid}/greetings`, `GET/PUT/DELETE /worlds/{wid}/greetings/{gid}`, `PUT /worlds/{wid}/greetings/{gid}/edges`, `POST /worlds/{wid}/greetings/import`, `GET /campaigns/{cid}/greetings/available`, `POST /campaigns/{cid}/scenes/{sid}/start-from-greeting`.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_playing_store.py`:

```python
def test_start_from_greeting_seeds_appears_marks(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    from grimoire.store import characters
    card = characters.blank_card("Seraphine")
    card["data"].update(description="keeper")
    characters.create_character(wroot, "Seraphine", "default", card)
    pcs.create_pc(wroot, "Elara", [])
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  body="{{char}} greets {{user}}.")
    playing.start_from_greeting(cid, sid, g)
    scene = scenes.read_scene(cid, sid)
    assert scene["messages"][0]["role"] == "assistant"
    assert scene["messages"][0]["content"] == "Seraphine greets Elara."   # tokens substituted
    assert g in playing.read_played(cid)
    assert ap.is_appeared(cid, "characters", "seraphine")
    # second start on a now-nonempty scene -> PlayError
    import pytest
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)


def test_start_unavailable_raises(monkeypatch, tmp_path):
    import pytest
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    from grimoire.store import characters, tags
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "s", "default", requires_tags=["vip"])
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)
```

Append to `backend/tests/test_routes.py`:

```python
def test_greeting_crud_import_edges_and_start(client):
    wid = _world(client)
    # a character to attach greetings to
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    # import from the character's blank first_mes -> none; set a first_mes via a new version
    got = client.get(f"/api/worlds/{wid}/characters/seraphine").json()
    card = got["versions"][0]["card"]
    card["data"]["first_mes"] = "You meet Seraphine."
    client.put(f"/api/worlds/{wid}/characters/seraphine/versions/default", json={"card": card})
    imported = client.post(f"/api/worlds/{wid}/greetings/import",
                           json={"character": "seraphine", "version": "default"}).json()["greetings"]
    assert len(imported) == 1
    # explicit create + edges
    g2 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Reckoning", "character": "seraphine", "version": "default",
                           "body": "It ends here."}).json()["id"]
    client.put(f"/api/worlds/{wid}/greetings/{imported[0]}/edges", json={"leads_to": [g2]})
    assert client.get(f"/api/worlds/{wid}/greetings/{imported[0]}").json()["meta"]["character"] == "seraphine"
    assert [x["id"] for x in client.get(f"/api/worlds/{wid}/greetings").json()] == sorted([imported[0], g2])

    # campaign: start a scene from the first greeting
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert {x["id"]: x["available"] for x in avail}[imported[0]] is True
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": imported[0]})
    assert r.status_code == 200
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["messages"][0]["content"] == "You meet Seraphine."
    # starting again on a non-empty scene -> 409
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g2}).status_code == 409


def test_start_from_greeting_unknown_404(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": "nope"}).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_routes.py -q`
Expected: FAIL (`scene_substitutions` missing; routes 404/405).

- [ ] **Step 3a: Add `scene_substitutions` to `context.py`**

Insert after `_substitute` (keep existing `build_messages` unchanged):

```python
def scene_substitutions(cid: str, sid: str) -> dict[str, str]:
    """Token map for a scene's current cast: {{user}} -> player names, {{char}} -> NPC names."""
    croot = campaigns.campaign_root(cid)
    npc_names: list[str] = []
    player_names: list[str] = []
    for a in appearances.scene_cast(cid, sid):
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["role"] == "npc":
                npc_names.append(characters.read_card(croot, a["id"], vid)["data"].get("name", ""))
            elif a["kind"] == "pcs":
                player_names.append(pcs.read_persona(croot, a["id"], vid).get("name", a["id"]))
            else:
                player_names.append(characters.read_card(croot, a["id"], vid)["data"].get("name", a["id"]))
        except (characters.CharacterNotFound, characters.VersionNotFound,
                pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
    return {"{{user}}": ", ".join(n for n in player_names if n),
            "{{char}}": ", ".join(n for n in npc_names if n)}
```

- [ ] **Step 3b: Add routes to `routes.py`**

Add request models near the other `BaseModel`s:

```python
class GreetingCreate(BaseModel):
    name: str
    character: str
    version: str
    body: str = ""
    requires_tags: list[str] = []
    predecessor_join: str = "all"


class GreetingUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    requires_tags: list[str] | None = None
    predecessor_join: str | None = None


class Edges(BaseModel):
    leads_to: list[str] | None = None
    excludes: list[str] | None = None


class ImportGreetings(BaseModel):
    character: str
    version: str


class StartFromGreeting(BaseModel):
    greeting: str


class Opener(BaseModel):
    prompt: str
```

Add world greeting routes **before** the generic `@router.get("/worlds/{wid}/{kind}")` block (e.g. right after `get_character_export`). Note `/greetings/import` is declared before `/greetings/{gid}` (same shape — order matters):

```python
# ---- world greetings (declared before the generic /{kind} routes) ----
@router.get("/worlds/{wid}/greetings")
def get_world_greetings(wid: str):
    return store.greetings.list_greetings(_world_root_or_404(wid))


@router.post("/worlds/{wid}/greetings")
def post_world_greeting(wid: str, body: GreetingCreate):
    gid = store.greetings.create_greeting(_world_root_or_404(wid), body.name, body.character,
                                          body.version, body.body, body.requires_tags,
                                          body.predecessor_join)
    return {"id": gid}


@router.post("/worlds/{wid}/greetings/import")
def post_world_greetings_import(wid: str, body: ImportGreetings):
    root = _world_root_or_404(wid)
    try:
        gids = store.greetings.import_from_character(root, body.character, body.version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"greetings": gids}


@router.get("/worlds/{wid}/greetings/{gid}")
def get_world_greeting(wid: str, gid: str):
    try:
        return store.greetings.read_greeting(_world_root_or_404(wid), gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")


@router.put("/worlds/{wid}/greetings/{gid}")
def put_world_greeting(wid: str, gid: str, body: GreetingUpdate):
    try:
        store.greetings.update_greeting(_world_root_or_404(wid), gid, name=body.name,
                                        body=body.body, requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}


@router.put("/worlds/{wid}/greetings/{gid}/edges")
def put_world_greeting_edges(wid: str, gid: str, body: Edges):
    root = _world_root_or_404(wid)
    try:
        store.greetings.read_greeting(root, gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    store.greetings.set_edges(root, gid, body.leads_to, body.excludes)
    return {"ok": True}


@router.delete("/worlds/{wid}/greetings/{gid}")
def delete_world_greeting(wid: str, gid: str):
    try:
        store.greetings.delete_greeting(_world_root_or_404(wid), gid)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    return {"ok": True}
```

Add campaign play routes **before** the generic `@router.get("/campaigns/{cid}/{kind}")` block (e.g. right after `post_dismiss`):

```python
# ---- campaign greetings / play (declared before the generic /{kind} routes) ----
@router.get("/campaigns/{cid}/greetings/available")
def get_available_greetings(cid: str):
    _campaign_root_or_404(cid)
    return store.playing.available_greetings(cid)


@router.post("/campaigns/{cid}/scenes/{sid}/start-from-greeting")
def post_start_from_greeting(cid: str, sid: str, body: StartFromGreeting):
    _require_scene(cid, sid)
    try:
        store.playing.start_from_greeting(cid, sid, body.greeting)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
```bash
git add backend/src/grimoire/store/context.py backend/src/grimoire/routes.py backend/tests/test_playing_store.py backend/tests/test_routes.py
git commit -m "feat: start-from-greeting route + scene_substitutions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `build_opener_messages` + ephemeral opener SSE route

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_context.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `context._world_info/_pc_persona_block/_char_player_block/_substitute/scene_substitutions`, `appearances.scene_cast/locked_version`.
- Produces: `context.build_opener_messages(cid, sid, prompt) -> list[dict]`; `POST /campaigns/{cid}/scenes/{sid}/opener` (SSE, ephemeral).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_context.py`:

```python
def test_build_opener_messages(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    pcs.create_pc(worlds.world_root(wid), "Elara", [],
                  persona={"name": "Elara", "pronouns": "", "summary": "", "description": "a scholar"})
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    entities.create_entity(croot, "lore", "Always", "ambient lore", keys="")
    entities.create_entity(croot, "lore", "Salt", "salt lore", keys="salt")
    msgs = context.build_opener_messages(cid, sid, "A storm over the salt marshes for {{user}}.")
    assert msgs[0]["role"] == "system" and msgs[-1]["role"] == "user"
    sys = msgs[0]["content"]
    assert "a scholar" in sys          # player persona present
    assert "ambient lore" in sys       # always-on lore present
    assert "salt lore" in sys          # 'salt' activated by the prompt text
    assert "{{user}}" not in sys       # substituted
    assert msgs[-1]["content"] == "A storm over the salt marshes for Elara."
```

Append to `backend/tests/test_routes.py`:

```python
def test_opener_streams_without_persisting(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "Begin in a tavern."}) as r:
        body = "".join(r.iter_text())
    assert "Hel" in body and "lo" in body and '"done": true' in body
    # ephemeral: the scene is untouched
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []


def test_opener_requires_key(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "x"}).status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py tests/test_routes.py -q`
Expected: FAIL (`build_opener_messages` missing; opener route 404).

- [ ] **Step 3a: Add `build_opener_messages` to `context.py`**

```python
OPENER_INSTRUCTION = (
    "Write the opening narration for a new scene based on the prompt below. "
    "Set the scene vividly in the second person. Do not speak or act for the player."
)


def build_opener_messages(cid: str, sid: str, prompt: str) -> list[dict]:
    """A world-informed, character-less opener: instruction + player personas + activated
    world-info (driven by the prompt). Ephemeral — the caller does not persist the result."""
    croot = campaigns.campaign_root(cid)
    subs = scene_substitutions(cid, sid)
    player_blocks: list[str] = []
    for a in appearances.scene_cast(cid, sid):
        if a["role"] != "player":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                player_blocks.append(_pc_persona_block(pcs.read_persona(croot, a["id"], vid)))
            else:
                player_blocks.append(_char_player_block(characters.read_card(croot, a["id"], vid)["data"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    parts = [OPENER_INSTRUCTION] + [b for b in player_blocks if b]
    wi = _world_info(croot, prompt)
    if wi:
        parts.append(wi)
    system_text = _substitute("\n\n".join(parts), subs)
    return [{"role": "system", "content": system_text},
            {"role": "user", "content": _substitute(prompt, subs)}]
```

- [ ] **Step 3b: Add the ephemeral opener route + stream helper to `routes.py`**

Add a non-persisting stream helper near `_chat_stream`:

```python
def _ephemeral_stream(messages: list[dict], cfg: dict, client: OpenRouterClient):
    async def event_stream():
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

Add the route next to `post_start_from_greeting`:

```python
@router.post("/campaigns/{cid}/scenes/{sid}/opener")
def post_opener(cid: str, sid: str, body: Opener, client: OpenRouterClient = Depends(get_openrouter)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    messages = store.context.build_opener_messages(cid, sid, body.prompt)
    return _ephemeral_stream(messages, cfg, client)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
```bash
git add backend/src/grimoire/store/context.py backend/src/grimoire/routes.py backend/tests/test_context.py backend/tests/test_routes.py
git commit -m "feat: ephemeral world-informed opener generator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final steps (after Task 4)

- [ ] Whole-branch read-only review: dispatch a general-purpose agent over `git diff context-builder...HEAD`; fix Critical/Important findings.
- [ ] Update `.superpowers/sdd/progress.md` ledger.
- [ ] `finishing-a-development-branch` → squash the whole branch to a single commit (`git reset --soft context-builder` then one commit), keep the branch.

## Self-Review

**Spec coverage:** greeting objects + import (Task 1) ✓; plot edges leads_to/excludes + AND/OR join + mutual exclusion (Task 1 storage, Task 2 availability) ✓; tag gating vs player PCs (Task 2) ✓; played set (Task 2) ✓; start-from-greeting seeds first post + appear() + substitution (Task 3) ✓; generate-opener ephemeral with world context (Task 4) ✓; routes literal-before-generic (Tasks 3–4) ✓; greetings don't sync (no sync.py change) ✓.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `read_greeting` returns `{"meta": {...}, "body"}`; `start_from_greeting` reads `["meta"]["character"/"version"]` ✓. `availability` returns `{id,name,available,reasons}`; `available_greetings` passes it through; tests key on `available` ✓. `scene_substitutions` returns the `{{user}}/{{char}}` dict consumed by `_substitute` ✓.
