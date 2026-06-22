# Context Builder (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble each chat/retry turn into a SillyTavern-faithful prompt that injects the campaign's player personas (`{{user}}`), in-scene NPC cards (`{{char}}`), card fields, and keyword-activated world-info (lore + locations) — behind a swappable retrieval seam.

**Architecture:** A new isolated `store/context.py` owns the whole assembler: `build_messages(cid, sid)` reads the campaign store and returns the OpenRouter `messages` list. World-info selection goes through one `activate(entries, recent_text)` function (v1 keyword; the only swap surface). Entities gain an optional `keys` field; config gains `context_scan_depth`. The chat/retry routes change only how `messages` is built.

**Tech Stack:** Python 3, FastAPI, pytest + `fastapi.testclient.TestClient`. Pure stdlib (`re`). Frontend out of scope.

## Global Constraints

- **SillyTavern-faithful order** in the single system message: `system_prompt`(s) → NPC
  `description`/`personality`/`scenario` block(s) → player persona block(s) → `mes_example`(s) →
  activated world-info. Then chat history. Then a **final** system message with
  `post_history_instructions`(s).
- **Actors from `appearances.scene_cast(cid, sid)`** (`[{kind,id,role}]`): `role=="player"` →
  persona block (`{{user}}`); `role=="npc"` (always kind `characters`) → NPC card block
  (`{{char}}`). Read each at `appearances.locked_version(cid, kind, id)` from the **campaign** root.
- **Multi-NPC:** inject all NPC cards in cast order; `{{char}}` → comma-joined NPC names,
  `{{user}}` → comma-joined player names.
- **Token substitution** of literal `{{user}}`/`{{char}}` (case-insensitive) across **every**
  message content, but **only when the replacement is non-empty** (so a player-less scene leaves
  `{{user}}` untouched and a bare scene is byte-identical to today).
- **Empty context** (no NPC cards, no players, no activated/always-on world-info) ⇒ **no system
  message**; history is sent as today.
- **World-info = campaign `lore` + `locations` entries.** An entry activates if it has **no keys**
  (always-on) **or** any key matches as a **whole word, case-insensitive** in the last
  `context_scan_depth` messages. `keys` is comma-joined frontmatter; default depth `8`.
- **`first_mes`/`alternate_greetings` are NOT injected** (those belong to greetings/scene-start).
- The chat SSE/persistence/error contract and the `409` missing-key behavior are unchanged.
- Store modules in `backend/src/grimoire/store/`, routes in `routes.py`, tests in
  `backend/tests/`. Run tests from `backend/` with `.venv/Scripts/python.exe -m pytest`.

---

### Task 1: Optional `keys` on entities + route passthrough

**Files:**
- Modify: `backend/src/grimoire/store/entities.py` (`create_entity`, `update_entity`)
- Modify: `backend/src/grimoire/routes.py` (`EntityCreate`, `EntityUpdate`, `_entity_create`, `_entity_update`)
- Test: `backend/tests/test_entities_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  ```python
  create_entity(root, kind, name, body="", keys="") -> str
  update_entity(root, kind, eid, name=None, body=None, keys=None) -> None
  ```
  `read_entity`/`list_entities` already surface `meta["keys"]` (absent ⇒ "").

- [ ] **Step 1: Write the failing store test**

Add to `backend/tests/test_entities_store.py`:

```python
def test_keys_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "the pact", keys="pact, salt")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["keys"] == "pact, salt"
    # update can change keys without touching the body
    entities.update_entity(tmp_path, "lore", eid, keys="pact")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["keys"] == "pact"
    assert got["body"].strip() == "the pact"
    # entities without keys read as empty string
    e2 = entities.create_entity(tmp_path, "lore", "No Keys", "x")
    assert entities.read_entity(tmp_path, "lore", e2)["meta"].get("keys", "") == ""
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_entities_store.py::test_keys_round_trip -v`
Expected: FAIL (`create_entity` got an unexpected keyword `keys`).

- [ ] **Step 3: Implement in `entities.py`**

Replace `create_entity` and `update_entity`:

```python
def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None,
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
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")
```

- [ ] **Step 4: Run the store test to pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_entities_store.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing route test**

Add to `backend/tests/test_routes.py`:

```python
def test_entity_keys_via_routes(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "p", "keys": "pact"}).json()["id"]
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact"
    client.put(f"/api/worlds/{wid}/lore/{eid}", json={"keys": "pact, salt"})
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact, salt"
```

- [ ] **Step 6: Pass `keys` through the routes**

In `routes.py`, extend the models and helpers:

```python
class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
```

```python
def _entity_create(root, kind: str, body: EntityCreate):
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body, keys=body.keys)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}
```

- [ ] **Step 7: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS (111 prior + 2 new).

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/src/grimoire/routes.py backend/tests/test_entities_store.py backend/tests/test_routes.py
git commit -m "feat(store): optional keys on entities (world-info triggers)"
```

---

### Task 2: `context_scan_depth` config

**Files:**
- Modify: `backend/src/grimoire/store/config.py`
- Test: `backend/tests/test_config_store.py`

**Interfaces:**
- Produces: `read_config()` returns a `context_scan_depth` string (default `"8"`); writable via
  `write_config(context_scan_depth=...)`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_config_store.py`:

```python
def test_context_scan_depth_default_and_write(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config
    assert config.read_config()["context_scan_depth"] == "8"
    config.write_config(context_scan_depth="5")
    assert config.read_config()["context_scan_depth"] == "5"
```

(If `test_config_store.py` already sets `GRIMOIRE_HOME` via a fixture, follow that file's existing
setup instead of `monkeypatch.setenv` — match the established pattern.)

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_config_store.py::test_context_scan_depth_default_and_write -v`
Expected: FAIL (`KeyError: 'context_scan_depth'`).

- [ ] **Step 3: Implement in `config.py`**

```python
DEFAULT_SCAN_DEPTH = "8"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth")
```

In `read_config`, add the key to both the defaults-write branch and the returned dict:

```python
        defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME,
                    "context_scan_depth": DEFAULT_SCAN_DEPTH}
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "openrouter_key": meta.get("openrouter_key", ""),
        "model": meta.get("model", DEFAULT_MODEL),
        "theme": meta.get("theme", DEFAULT_THEME),
        "context_scan_depth": meta.get("context_scan_depth", DEFAULT_SCAN_DEPTH),
    }
```

- [ ] **Step 4: Run to pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_config_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/config.py backend/tests/test_config_store.py
git commit -m "feat(store): context_scan_depth config (default 8)"
```

---

### Task 3: `activate()` — keyword world-info strategy

**Files:**
- Create: `backend/src/grimoire/store/context.py` (this task adds only `activate`)
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `context`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Produces:
  ```python
  activate(entries, recent_text) -> list[dict]
  # entries: [{"name": str, "body": str, "keys": [str, ...]}]
  # an entry is selected if keys is empty (always-on) OR any key whole-word-matches recent_text
  ```

> Note: the spec sketched `activate(entries, recent_text, depth)`; `depth` is applied upstream
> when `build_messages` slices `recent_text` to the last N messages, so `activate` itself takes
> only the already-sliced text. The seam (one function) is unchanged.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_context.py`:

```python
from grimoire.store import context


def test_activate_keyword_and_always_on():
    entries = [
        {"name": "Salt Pact", "body": "the pact", "keys": ["pact", "salt"]},
        {"name": "Constant", "body": "always", "keys": []},
        {"name": "Hidden", "body": "secret", "keys": ["dragon"]},
    ]
    out = context.activate(entries, "We spoke of the Pact at dawn.")
    names = [e["name"] for e in out]
    assert "Salt Pact" in names      # 'Pact' whole-word, case-insensitive
    assert "Constant" in names       # keyless -> always-on
    assert "Hidden" not in names     # 'dragon' absent


def test_activate_whole_word_only():
    entries = [{"name": "Pac", "body": "x", "keys": ["pac"]}]
    # 'pact' must NOT trigger key 'pac' (whole-word match)
    assert context.activate(entries, "the pact") == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py -v`
Expected: FAIL (`ModuleNotFoundError: grimoire.store.context`).

- [ ] **Step 3: Implement `activate` in `context.py`**

Create `backend/src/grimoire/store/context.py`:

```python
"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later.
"""

from __future__ import annotations

import re


def activate(entries: list[dict], recent_text: str) -> list[dict]:
    """Select world-info entries: keyless = always-on; else any key whole-word (ci) in recent_text."""
    out: list[dict] = []
    for e in entries:
        keys = e.get("keys") or []
        if not keys:
            out.append(e)
            continue
        if any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys):
            out.append(e)
    return out
```

- [ ] **Step 4: Re-export + run**

In `store/__init__.py`: add `context` to the `from . import …` line and `"context"` to `__all__`.

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/src/grimoire/store/__init__.py backend/tests/test_context.py
git commit -m "feat(store): world-info activate() keyword strategy"
```

---

### Task 4: `build_messages` — the ST-faithful assembler

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (add `build_messages` + helpers)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `activate` (Task 3); `scenes.read_scene`, `appearances.scene_cast`/`locked_version`,
  `characters.read_card`, `pcs.read_persona`, `entities.list_entities`/`read_entity`,
  `campaigns.campaign_root`, `config.read_config`.
- Produces: `build_messages(cid, sid) -> list[dict]` (the full OpenRouter messages list).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_context.py`:

```python
import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, context, entities, pcs, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return wid, cid, sid


def _npc_card(name, **fields):
    card = characters.blank_card(name)
    card["data"].update(fields)
    return card


def test_single_npc_block_order(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="keeper", personality="cold", scenario="docks"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    msgs = context.build_messages(cid, sid)
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert sys.index("keeper") < sys.index("cold") < sys.index("docks")
    assert msgs[-1] == {"role": "user", "content": "hello"}


def test_multi_npc_char_token_joined(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", _npc_card("Seraphine", description="A"))
    characters.create_character(wroot, "Drowned King", "default", _npc_card("Drowned King", description="B"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    ap.appear(cid, sid, "characters", "drowned-king", "default", "npc")
    scenes.append_message(cid, sid, "user", "{{char}} arrives")
    msgs = context.build_messages(cid, sid)
    assert "A" in msgs[0]["content"] and "B" in msgs[0]["content"]
    # scene_cast sorts by (kind, id): 'drowned-king' precedes 'seraphine'
    assert msgs[-1]["content"] == "Drowned King, Seraphine arrives"


def test_player_persona_and_user_token(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    pcs.create_pc(worlds.world_root(wid), "Elara", [],
                  persona={"name": "Elara", "pronouns": "she/her", "summary": "scholar", "description": "A wanderer."})
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    scenes.append_message(cid, sid, "user", "I am {{user}}")
    msgs = context.build_messages(cid, sid)
    assert "A wanderer." in msgs[0]["content"]
    assert msgs[-1]["content"] == "I am Elara"


def test_post_history_is_last(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="d", post_history_instructions="STAY IN CHARACTER"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    assert msgs[-1] == {"role": "system", "content": "STAY IN CHARACTER"}
    assert msgs[0]["role"] == "system" and "d" in msgs[0]["content"]


def test_worldinfo_keyword_depth(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Salt Pact", "the pact lore", keys="pact")
    # 'pact' is far back; with depth shrunk it should fall outside the scan window
    for i in range(10):
        scenes.append_message(cid, sid, "user", "we mentioned the pact" if i == 0 else f"filler {i}")
    from grimoire.store import config
    config.write_config(context_scan_depth="3")
    # the only 'pact' is message 0; with depth 3 the scan sees only the last 3 fillers.
    # no cast, no always-on entry, key outside depth -> empty context -> no system message.
    sys_msgs = [m for m in context.build_messages(cid, sid) if m["role"] == "system"]
    assert sys_msgs == []


def test_worldinfo_always_on_and_in_depth(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Const", "always lore", keys="")
    entities.create_entity(croot, "lore", "Salt", "pact lore", keys="pact")
    scenes.append_message(cid, sid, "user", "the pact matters")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "always lore" in sys and "pact lore" in sys


def test_empty_context_is_raw_history(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "plain message")
    assert context.build_messages(cid, sid) == [{"role": "user", "content": "plain message"}]
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: FAIL (`build_messages` not defined).

- [ ] **Step 3: Implement `build_messages` + helpers**

Append to `backend/src/grimoire/store/context.py` (and add the imports at the top of the file):

```python
from . import appearances, campaigns, characters, config, entities, pcs, scenes


def _substitute(text: str, subs: dict[str, str]) -> str:
    for token, value in subs.items():
        if value:  # only replace when non-empty, so player-less scenes keep {{user}} literal
            text = re.sub(re.escape(token), value, text, flags=re.IGNORECASE)
    return text


def _npc_block(data: dict) -> str:
    parts = [data.get(f, "").strip() for f in ("description", "personality", "scenario")]
    return "\n".join(p for p in parts if p)


def _pc_persona_block(p: dict) -> str:
    head = ", ".join(x for x in (p.get("name", ""), p.get("pronouns", "")) if x)
    body = "\n".join(x for x in (p.get("summary", ""), p.get("description", "")) if x)
    return "\n".join(x for x in (head, body) if x).strip()


def _char_player_block(data: dict) -> str:
    body = "\n".join(data.get(f, "").strip() for f in ("description", "personality") if data.get(f, "").strip())
    return "\n".join(x for x in (data.get("name", ""), body) if x).strip()


def _world_info(croot, recent_text: str) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            entries.append({"name": e["meta"].get("name", meta["id"]), "body": e["body"].strip(), "keys": keys})
    selected = activate(entries, recent_text)
    return "\n\n".join(e["body"] for e in selected if e["body"])


def build_messages(cid: str, sid: str) -> list[dict]:
    scene = scenes.read_scene(cid, sid)
    history = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
    croot = campaigns.campaign_root(cid)
    cast = appearances.scene_cast(cid, sid)

    npc_cards: list[dict] = []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            npc_cards.append(characters.read_card(croot, a["id"], vid)["data"])
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue

    player_blocks: list[str] = []
    player_names: list[str] = []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = appearances.locked_version(cid, a["kind"], a["id"])
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], vid)
                player_blocks.append(_pc_persona_block(p))
                player_names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(croot, a["id"], vid)["data"]
                player_blocks.append(_char_player_block(data))
                player_names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound, characters.CharacterNotFound, characters.VersionNotFound):
            continue

    npc_names = [d.get("name", "") for d in npc_cards if d.get("name")]
    subs = {"{{user}}": ", ".join(player_names), "{{char}}": ", ".join(npc_names)}

    try:
        depth = int(config.read_config().get("context_scan_depth", "8"))
    except (ValueError, TypeError):
        depth = 8
    recent_text = "\n".join(m["content"] for m in history[-depth:])

    parts: list[str] = []
    parts += [d.get("system_prompt", "").strip() for d in npc_cards if d.get("system_prompt", "").strip()]
    parts += [b for b in (_npc_block(d) for d in npc_cards) if b]
    parts += [b for b in player_blocks if b]
    parts += [d.get("mes_example", "").strip() for d in npc_cards if d.get("mes_example", "").strip()]
    wi = _world_info(croot, recent_text)
    if wi:
        parts.append(wi)
    system_text = "\n\n".join(parts).strip()
    post_history = "\n\n".join(
        d.get("post_history_instructions", "").strip() for d in npc_cards
        if d.get("post_history_instructions", "").strip()
    ).strip()

    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": _substitute(system_text, subs)})
    messages += [{"role": m["role"], "content": _substitute(m["content"], subs)} for m in history]
    if post_history:
        messages.append({"role": "system", "content": _substitute(post_history, subs)})
    return messages
```

- [ ] **Step 4: Run the context tests**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_context.py -q`
Expected: PASS (all build_messages + activate tests).

- [ ] **Step 5: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat(store): build_messages ST-faithful prompt assembly"
```

---

### Task 5: Wire chat/retry through the context builder

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`post_chat`, `post_retry`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.context.build_messages(cid, sid)`.

- [ ] **Step 1: Write the failing route test**

The existing `FakeOpenRouter` (top of `test_routes.py`) ignores the messages it's handed. Add a
capturing variant and a test:

```python
class CapturingOpenRouter:
    def __init__(self):
        self.messages = None

    async def stream(self, messages, model, key):
        self.messages = messages
        for d in ["ok"]:
            yield d


def test_chat_injects_system_message(client, monkeypatch):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "the drowned keeper", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})

    cap = CapturingOpenRouter()
    from grimoire import routes
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap

    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hello"}) as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[0]["role"] == "system"
    assert "the drowned keeper" in cap.messages[0]["content"]
    assert cap.messages[-1] == {"role": "user", "content": "hello"}
```

(If `client` is a plain `TestClient`, `client.app` is the app; if the fixture exposes the app
differently, use that — match the fixture. The fixture already overrides `get_openrouter` once;
re-overriding for this test is fine.)

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_routes.py::test_chat_injects_system_message -v`
Expected: FAIL (current chat sends only the raw user turn — no system message captured).

- [ ] **Step 3: Rewire `post_chat` and `post_retry`**

```python
@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    store.scenes.append_message(cid, sid, "user", turn.content)
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if not scene["messages"]:
        raise HTTPException(status_code=400, detail="nothing to retry")
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)
```

- [ ] **Step 4: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS. The existing chat/retry tests (`test_chat_*`, `test_retry_*`) still pass: with no
cast and no world-info those scenes have empty context, so `build_messages` returns exactly the
raw history they already assert.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(api): chat/retry assemble context via build_messages"
```

---

## Self-Review notes (coverage)

- Spec §"Schema additions" (`keys` on lore/locations; `context_scan_depth` default 8) → Tasks 1, 2.
- Spec §"Assembly" (ST order, multi-NPC, substitution non-empty-only, empty-context) → Task 4
  (`build_messages`, tests `test_single_npc_block_order`, `test_multi_npc_char_token_joined`,
  `test_player_persona_and_user_token`, `test_post_history_is_last`, `test_empty_context_is_raw_history`).
- Spec §"Activation" (pluggable seam, keyword whole-word, keyless always-on, scan depth) → Tasks 3
  (`activate`) + 4 (`_world_info` slices `recent_text` by depth; tests `test_worldinfo_*`).
- Spec §"Route wiring" (chat appends then builds; retry builds, keeps 400; SSE unchanged) → Task 5.
- Spec §"Error handling" (missing card/persona skipped; character-as-player via persona block;
  always-on lore with no cast) → Task 4 (try/except around reads; player branch; `_world_info`
  independent of cast).
- Spec §"Non-goals" (no import, no greetings, no budget, no template) → nothing built for them.
- Documented deviation: `activate(entries, recent_text)` drops the spec's illustrative `depth`
  param — `depth` is applied in `build_messages` when slicing `recent_text`; the one-function seam
  is preserved.
- `first_mes`/`alternate_greetings` excluded by omission (the assembler only reads
  description/personality/scenario/system_prompt/mes_example/post_history_instructions).
```
