# Scene Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the scene page into a play-and-inspect workspace: 3-column layout with a read-only inspector (cast, location, context-token breakdown), editable message cards, a global system prompt, and a quote-coloring toggle.

**Architecture:** Backend refactors `context.py` so the prompt is assembled from labeled sections (shared by `build_messages` and a new breakdown), adds tiktoken token counts (with a char/4 fallback), and adds endpoints for context, actor detail, and message editing. Frontend adds a `SceneInspector` + `RecordDrawer`, restyles the transcript into editable cards, and wires two new config settings.

**Tech Stack:** FastAPI + pytest; Vite/React + react-markdown/rehype + vitest.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; route tests use the `client` fixture.
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Run frontend tests: `npx --prefix frontend vitest run --root frontend` (the `--root frontend` is required so vitest loads `frontend/vite.config.ts`).
- Typecheck: from `frontend/`, `npx tsc -b`.
- Token counts are estimates labeled "est."; `count_tokens` falls back to `len(text)//4` if tiktoken can't load — tests must pass with either backend.
- Inspector is read-only; `CastPanel` (scene setup) stays in the center column.
- Config settings are global; `/api/config` must never leak `openrouter_key`.

---

## Phase 1 — Backend: config + context refactor

### Task 1: Config gains `system_prompt` and `quote_color`

**Files:**
- Modify: `backend/src/grimoire/store/config.py`
- Modify: `backend/src/grimoire/routes.py` (`ConfigUpdate`, `_public_config`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `read_config()` / `write_config()` handle `system_prompt` (default `""`) and `quote_color` (default `"off"`); `/api/config` returns both; `PUT /api/config` accepts both.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py`:

```python
def test_config_system_prompt_and_quote_color_roundtrip(client):
    client.put("/api/config", json={"system_prompt": "Never speak for the PC.", "quote_color": "on"})
    body = client.get("/api/config").json()
    assert body["system_prompt"] == "Never speak for the PC."
    assert body["quote_color"] == "on"
    assert "openrouter_key" not in body
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_config_system_prompt_and_quote_color_roundtrip -q`
Expected: FAIL — `KeyError: 'system_prompt'`.

- [ ] **Step 3: Update config store**

In `backend/src/grimoire/store/config.py`, replace lines 8-32 (the constants through `read_config`) with:

```python
DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "occult"
DEFAULT_SCAN_DEPTH = "8"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth", "system_prompt", "quote_color")


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off"}
    if not path.exists():
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {k: meta.get(k, default) for k, default in defaults.items()}
```

- [ ] **Step 4: Update the config route model + response**

In `backend/src/grimoire/routes.py`, replace the `ConfigUpdate` model (lines ~23-26):

```python
class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
```

and replace `_public_config` (line ~179-180):

```python
def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"]),
            "system_prompt": cfg.get("system_prompt", ""), "quote_color": cfg.get("quote_color", "off")}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "config"`
Expected: PASS (including the existing `test_config_never_leaks_key`).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/config.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: global system_prompt and quote_color config settings"
```

---

### Task 2: Context section refactor + token counting

**Files:**
- Modify: `backend/src/grimoire/store/context.py`
- Modify: `backend/pyproject.toml` (add `tiktoken`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `config.read_config()["system_prompt"]` (Task 1).
- Produces:
  - `context.context_sections(cid, sid) -> list[{"label": str, "text": str}]` — substituted, non-empty, payload order; global system prompt first.
  - `context.count_tokens(text: str) -> int` — tiktoken `cl100k_base`, fallback `len//4`, `0` for `""`.
  - `build_messages` output is unchanged when `system_prompt` is empty (the existing `test_context.py` suite is the characterization guard).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_context.py`:

```python
def test_context_sections_labels_and_global_prompt(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(system_prompt="Never speak for the PC.")
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="keeper"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    secs = context.context_sections(cid, sid)
    labels = [s["label"] for s in secs]
    assert labels[0] == "Global system prompt"
    assert secs[0]["text"] == "Never speak for the PC."
    assert "Character descriptions" in labels
    assert "Conversation history" in labels
    assert all(s["text"].strip() for s in secs)  # no empty sections


def test_count_tokens_positive_and_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert context.count_tokens("") == 0
    assert context.count_tokens("the drowned king waits") > 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k "context_sections or count_tokens"`
Expected: FAIL — `module 'grimoire.store.context' has no attribute 'context_sections'`.

- [ ] **Step 3: Refactor `context.py` to assemble labeled sections**

In `backend/src/grimoire/store/context.py`, replace the entire `build_messages` function (from `def build_messages(` to the end of the file) with:

```python
def _assemble(cid: str, sid: str) -> dict:
    """One pass producing substituted, labeled system sections + history + post-history.
    Shared by build_messages (joins sections into the system message) and
    context_sections (exposes them for the token breakdown)."""
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
        depth = max(int(config.read_config().get("context_scan_depth", "8")), 0)
    except (ValueError, TypeError):
        depth = 8
    recent_text = "\n".join(m["content"] for m in history[-depth:]) if depth else ""

    sys: list[tuple[str, str]] = []

    def add(label: str, text: str) -> None:
        text = text.strip()
        if text:
            sys.append((label, _substitute(text, subs)))

    add("Global system prompt", config.read_config().get("system_prompt", ""))
    add("System prompt", "\n\n".join(d.get("system_prompt", "").strip() for d in npc_cards if d.get("system_prompt", "").strip()))
    add("Character descriptions", "\n\n".join(b for b in (_npc_block(d) for d in npc_cards) if b))
    add("Player personas", "\n\n".join(b for b in player_blocks if b))
    add("Message examples", "\n\n".join(d.get("mes_example", "").strip() for d in npc_cards if d.get("mes_example", "").strip()))

    history_ids = scenes.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    exclude: frozenset = frozenset()
    if current_loc:
        try:
            loc_body = entities.read_entity(croot, "locations", current_loc)["body"].strip()
            exclude = frozenset({current_loc})
            add("Current setting", loc_body)
        except entities.EntityNotFound:
            pass
    add("World info", _world_info(croot, recent_text, exclude))
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))
    add("Off-scene cast", _cast_directory(croot, wroot, cid, sid))

    post_history = "\n\n".join(
        d.get("post_history_instructions", "").strip() for d in npc_cards
        if d.get("post_history_instructions", "").strip()
    ).strip()
    post_history = _substitute(post_history, subs) if post_history else ""

    sub_history = [{"role": m["role"], "content": _substitute(m["content"], subs)} for m in history]
    return {"system": sys, "history": sub_history, "post_history": post_history}


def build_messages(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    messages: list[dict] = []
    system_text = "\n\n".join(t for _, t in a["system"]).strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages


def context_sections(cid: str, sid: str) -> list[dict]:
    a = _assemble(cid, sid)
    out = [{"label": label, "text": text} for label, text in a["system"]]
    hist = "\n\n".join(m["content"] for m in a["history"])
    if hist:
        out.append({"label": "Conversation history", "text": hist})
    if a["post_history"]:
        out.append({"label": "Post-history instructions", "text": a["post_history"]})
    return out


@functools.lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:
        return len(text) // 4
```

- [ ] **Step 4: Add the `functools` import**

At the top of `backend/src/grimoire/store/context.py`, change:

```python
import re
```

to:

```python
import functools
import re
```

- [ ] **Step 5: Run the context suite (includes the unchanged-output characterization)**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS — all existing `build_messages` tests still pass (output unchanged) plus the two new ones.

- [ ] **Step 6: Add tiktoken to dependencies and install (best effort)**

In `backend/pyproject.toml`, add `"tiktoken>=0.7"` to the `dependencies` list (next to `httpx`).

Run: `backend/.venv/Scripts/python.exe -m pip install "tiktoken>=0.7"`
Expected: installs, or fails offline — either is fine (`count_tokens` falls back).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/pyproject.toml backend/tests/test_context.py
git commit -m "feat: labeled context sections and token counting"
```

---

## Phase 2 — Backend endpoints

### Task 3: Context breakdown endpoint

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.context.context_sections`, `store.context.count_tokens` (Task 2).
- Produces: `GET /api/campaigns/{cid}/scenes/{sid}/context` → `{"model": str, "total_tokens": int, "sections": [{"label","text","tokens"}]}`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py`:

```python
def test_scene_context_breakdown(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})  # adds history
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert body["model"]
    assert body["sections"], "expected at least one section"
    assert all(s["tokens"] >= 0 for s in body["sections"])
    assert body["total_tokens"] == sum(s["tokens"] for s in body["sections"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_scene_context_breakdown -q`
Expected: FAIL — 405/404 (route not defined).

- [ ] **Step 3: Add the route**

In `backend/src/grimoire/routes.py`, immediately after the `put_scene_location` function (the `PUT .../location` route), add:

```python
@router.get("/campaigns/{cid}/scenes/{sid}/context")
def get_scene_context(cid: str, sid: str):
    scene = _require_scene(cid, sid)
    sections = []
    total = 0
    for s in store.context.context_sections(cid, sid):
        tokens = store.context.count_tokens(s["text"])
        total += tokens
        sections.append({"label": s["label"], "text": s["text"], "tokens": tokens})
    return {"model": scene["meta"].get("model", ""), "total_tokens": total, "sections": sections}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_scene_context_breakdown -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: scene context breakdown endpoint"
```

---

### Task 4: Actor cast-detail endpoint

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (`cast_detail`)
- Modify: `backend/src/grimoire/routes.py` (route)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `scene_cast`, `locked_version`, `characters.read_card`, `pcs.read_persona`.
- Produces:
  - `appearances.cast_detail(cid, sid, kind, actor_id) -> {"kind","id","name","version","body"}`; raises `AppearError` if the actor isn't in the scene cast.
  - `GET /api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}` → that dict; 404 for unknown kind / not-in-cast / missing copy.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py`:

```python
def test_cast_detail_for_character_and_pc(client):
    wid, cid = _campaign(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.",
                     "personality": "cold", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "pcs", "id": "mara", "version": "default"})

    c = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/seraphine").json()
    assert c["name"] == "Seraphine" and "Drowned King" in c["body"] and "cold" in c["body"]
    p = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/pcs/mara").json()
    assert p["name"] == "Mara" and "On the run." in p["body"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/ghost").status_code == 404
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_cast_detail_for_character_and_pc -q`
Expected: FAIL — route not defined.

- [ ] **Step 3: Add the store helper**

In `backend/src/grimoire/store/appearances.py`, add at the end of the file:

```python
def cast_detail(cid: str, sid: str, kind: str, actor_id: str) -> dict:
    """Read-only display info for an actor in a scene, from the campaign copy."""
    if not any(a["kind"] == kind and a["id"] == actor_id for a in scene_cast(cid, sid)):
        raise AppearError(f"{kind}/{actor_id} is not in scene {sid}")
    croot = campaigns.campaign_root(cid)
    vid = locked_version(cid, kind, actor_id)
    if kind == "characters":
        data = characters.read_card(croot, actor_id, vid)["data"]
        labelled = [("Description", "description"), ("Personality", "personality"), ("Scenario", "scenario")]
        body = "\n\n".join(f"**{lbl}**\n{data.get(f, '').strip()}"
                           for lbl, f in labelled if data.get(f, "").strip())
        name = data.get("name", actor_id)
    else:
        p = pcs.read_persona(croot, actor_id, vid)
        body = "\n\n".join(x for x in (p.get("summary", "").strip(), p.get("description", "").strip()) if x)
        name = p.get("name", actor_id)
    return {"kind": kind, "id": actor_id, "name": name, "version": vid, "body": body}
```

- [ ] **Step 4: Add the route**

In `backend/src/grimoire/routes.py`, immediately after the `get_scene_context` route (Task 3), add:

```python
@router.get("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def get_cast_detail(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        return store.appearances.cast_detail(cid, sid, kind, id)
    except (store.appearances.AppearError, store.characters.CharacterNotFound,
            store.characters.VersionNotFound, store.pcs.PCNotFound, store.pcs.PCVersionNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
```

- [ ] **Step 5: Run it to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_cast_detail_for_character_and_pc -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: scene cast-detail endpoint"
```

---

### Task 5: Edit a message

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (`_serialize_messages`, `edit_message`)
- Modify: `backend/src/grimoire/routes.py` (model + route)
- Test: `backend/tests/test_scene_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - `scenes.edit_message(cid, sid, index, content)` — replaces message `index`; raises `SceneNotFound` (bad scene) / `IndexError` (out of range).
  - `PUT /api/campaigns/{cid}/scenes/{sid}/messages/{index}` `{content}` → `{ok: True}`; 404 missing scene, 400 out-of-range.

- [ ] **Step 1: Write the failing store test**

Add to `backend/tests/test_scene_store.py`:

```python
def test_edit_message_roundtrip_and_bounds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "frist")
    scenes.append_message(cid, sid, "assistant", "She nods.")
    scenes.edit_message(cid, sid, 0, "first")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "She nods."}]
    with pytest.raises(IndexError):
        scenes.edit_message(cid, sid, 5, "x")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py::test_edit_message_roundtrip_and_bounds -q`
Expected: FAIL — `scenes` has no `edit_message`.

- [ ] **Step 3: Implement serialize + edit**

In `backend/src/grimoire/store/scenes.py`, add after `append_message`:

```python
def _serialize_messages(messages: list[dict]) -> str:
    body = ""
    for m in messages:
        block = f"**{ROLE_TO_LABEL[m['role']]}:** {m['content'].strip()}\n"
        body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    return body


def edit_message(cid: str, sid: str, index: int, content: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    messages = _parse_messages(p.read_text(encoding="utf-8").split("---", 2)[-1]) if False else read_scene(cid, sid)["messages"]
    if index < 0 or index >= len(messages):
        raise IndexError(index)
    messages[index]["content"] = content.strip()
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, _serialize_messages(messages)), encoding="utf-8")
```

(The `if False else` keeps a single clear source: `messages` comes from `read_scene`.)

- [ ] **Step 4: Run it to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: PASS (all).

- [ ] **Step 5: Write the failing route test**

Add to `backend/tests/test_routes.py`:

```python
def test_edit_message_route(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "helo"})
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0", json={"content": "hello"}).json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][0]["content"] == "hello"
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/9", json={"content": "x"}).status_code == 400
```

- [ ] **Step 6: Run it to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_edit_message_route -q`
Expected: FAIL — route not defined.

- [ ] **Step 7: Add the model + route**

In `backend/src/grimoire/routes.py`, add after the `SceneLocation` model:

```python
class EditMessage(BaseModel):
    content: str
```

and add after the `get_cast_detail` route (Task 4):

```python
@router.put("/campaigns/{cid}/scenes/{sid}/messages/{index}")
def put_scene_message(cid: str, sid: str, index: int, body: EditMessage):
    _require_scene(cid, sid)
    try:
        store.scenes.edit_message(cid, sid, index, body.content)
    except IndexError:
        raise HTTPException(status_code=400, detail="message index out of range")
    return {"ok": True}
```

- [ ] **Step 8: Run both new tests + full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no regressions).

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/routes.py backend/tests/test_scene_store.py backend/tests/test_routes.py
git commit -m "feat: edit-message store and route"
```

---

## Phase 3 — Frontend plumbing

### Task 6: API client + ConfigView settings

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/ConfigView.tsx`
- Test: `frontend/src/routes/ConfigView.test.tsx` (create)

**Interfaces:**
- Produces: `Config` gains `system_prompt`, `quote_color`; `api.getSceneContext`, `api.getCastDetail`, `api.editMessage`; types `ContextSection`, `SceneContext`, `CastDetail`. `ConfigView` saves both new settings.

- [ ] **Step 1: Add API types and methods**

In `frontend/src/api/client.ts`, replace the `Config` type:

```ts
export type Config = { model: string; theme: string; key_set: boolean; system_prompt: string; quote_color: string };
```

Add after the `SceneLocation` types:

```ts
export type ContextSection = { label: string; text: string; tokens: number };
export type SceneContext = { model: string; total_tokens: number; sections: ContextSection[] };
export type CastDetail = { kind: "characters" | "pcs"; id: string; name: string; version: string; body: string };
```

Change `putConfig` (the param type) to:

```ts
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string; system_prompt: string; quote_color: string }>) =>
    request<Config>("PUT", "/api/config", body),
```

Add after `setSceneLocation`:

```ts
  getSceneContext: (cid: string, sid: string) =>
    request<SceneContext>("GET", `/api/campaigns/${cid}/scenes/${sid}/context`),
  getCastDetail: (cid: string, sid: string, kind: string, id: string) =>
    request<CastDetail>("GET", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
  editMessage: (cid: string, sid: string, index: number, content: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/scenes/${sid}/messages/${index}`, { content }),
```

- [ ] **Step 2: Write the failing ConfigView test**

Create `frontend/src/routes/ConfigView.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  api: { getConfig: vi.fn(), putConfig: vi.fn() },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
vi.mock("./ModelCombobox", () => ({ default: () => <div /> }));
import { api } from "../api/client";

const cfg = { model: "m", theme: "occult", key_set: false, system_prompt: "", quote_color: "off" };
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
});

test("saves the system prompt", async () => {
  render(<ConfigView />);
  const ta = await screen.findByLabelText(/system prompt/i);
  fireEvent.change(ta, { target: { value: "Never speak for the PC." } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ system_prompt: "Never speak for the PC." })));
});

test("toggling quote color saves immediately", async () => {
  render(<ConfigView />);
  const cb = await screen.findByLabelText(/color quoted/i);
  fireEvent.click(cb);
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ quote_color: "on" }));
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npx --prefix frontend vitest run --root frontend src/routes/ConfigView.test.tsx`
Expected: FAIL — no system-prompt / quote-color controls.

- [ ] **Step 4: Update ConfigView**

In `frontend/src/routes/ConfigView.tsx`, add `systemPrompt` state and the two controls. Replace the component body so it reads (keep imports as-is):

```tsx
export default function ConfigView() {
  const { setTheme } = useTheme();
  const [config, setConfig] = useState<Config | null>(null);
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setModel(c.model);
      setSystemPrompt(c.system_prompt);
    });
  }, []);

  if (!config) return <div className="config">Loading…</div>;

  async function save(fields: Partial<{ model: string; theme: string; openrouter_key: string; system_prompt: string; quote_color: string }>) {
    const next = await api.putConfig(fields);
    setConfig(next);
    setKey("");
    setSaved(true);
    if (fields.theme) setTheme(fields.theme);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div className="config">
      <h2>Configuration</h2>

      <label>OpenRouter API key</label>
      <input
        type="password"
        placeholder={config.key_set ? "A key is set — type to replace" : "sk-or-…"}
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />

      <label>Model</label>
      <ModelCombobox value={model} onChange={setModel} />

      <label htmlFor="cfg-system-prompt">System prompt (sent with every scene)</label>
      <textarea
        id="cfg-system-prompt"
        rows={4}
        placeholder="e.g. Never speak or act for the player character."
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
      />

      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          aria-label="Color quoted dialogue"
          checked={config.quote_color === "on"}
          onChange={(e) => save({ quote_color: e.target.checked ? "on" : "off" })}
        />
        Color quoted dialogue
      </label>

      <label>Theme</label>
      <div className="theme-cards">
        {themeList.map((t) => (
          <div
            key={t.name}
            className={"theme-card" + (config.theme === t.name ? " active" : "")}
            onClick={() => save({ theme: t.name })}
          >
            {t.label}
          </div>
        ))}
      </div>

      <p style={{ marginTop: 24 }}>
        <button
          className="primary"
          onClick={() => save({ model, system_prompt: systemPrompt, ...(key ? { openrouter_key: key } : {}) })}
        >
          Save
        </button>
        {saved && <span style={{ marginLeft: 12, color: "var(--accent)" }}>Saved</span>}
      </p>
    </div>
  );
}
```

Add `.config textarea` styling in `frontend/src/index.css` next to the `.config input` rule:

```css
.config textarea { width: 100%; background: var(--surface); color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 8px; font-family: var(--font-body); resize: vertical; }
```

- [ ] **Step 5: Run it to verify it passes + typecheck**

Run: `npx --prefix frontend vitest run --root frontend src/routes/ConfigView.test.tsx`
Then from `frontend/`: `npx tsc -b`
Expected: PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/ConfigView.tsx frontend/src/routes/ConfigView.test.tsx frontend/src/index.css
git commit -m "feat: config client types + system-prompt and quote-color settings UI"
```

---

## Phase 4 — Frontend layout + inspector + drawer

### Task 7: RecordDrawer + SceneInspector

**Files:**
- Create: `frontend/src/components/RecordDrawer.tsx`
- Create: `frontend/src/components/SceneInspector.tsx`
- Create: `frontend/src/components/SceneInspector.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `api.getCast`, `api.getCampaign`, `api.listCharacters`, `api.listPCs`, `api.listCampaignPCs`, `api.getSceneLocation`, `api.getSceneContext`, `api.getCastDetail`, `api.readEntity`, `api.campaignImageUrl`; `fetchModels` from `../api/models`.
- Produces: `<SceneInspector cid sid refreshKey />` (default-exported? no — named) and `<RecordDrawer cid sid target onClose />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/SceneInspector.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneInspector } from "./SceneInspector";

vi.mock("../api/client", () => ({
  api: {
    getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
    listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
    getCastDetail: vi.fn(), readEntity: vi.fn(),
    campaignImageUrl: () => "/img",
  },
}));
vi.mock("../api/models", () => ({ fetchModels: vi.fn() }));
import { api } from "../api/client";
import { fetchModels } from "../api/models";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCast as any).mockResolvedValue([{ kind: "characters", id: "seraphine", role: "npc" }]);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c", world: "w" }, body: "" });
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: { id: "crypt", name: "The Crypt" }, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100,
    sections: [{ label: "World info", text: "lore text", tokens: 100 }],
  });
  (api.getCastDetail as any).mockResolvedValue({ kind: "characters", id: "seraphine", name: "Seraphine", version: "default", body: "keeper" });
  (fetchModels as any).mockResolvedValue([{ id: "m", name: "M", context: 1000, prompt: "0", completion: "0" }]);
});

function renderInspector() {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} />);
}

test("lists cast names and the location and a context section", async () => {
  renderInspector();
  await screen.findByText("Seraphine");
  await screen.findByText("The Crypt");
  await screen.findByText(/World info/);
});

test("clicking a cast row opens the drawer", async () => {
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /Seraphine/ }));
  await waitFor(() => expect(api.getCastDetail).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await screen.findByText("keeper");
});

test("context section expands to show the text", async () => {
  renderInspector();
  const summary = await screen.findByText(/World info/);
  fireEvent.click(summary);
  await screen.findByText("lore text");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx --prefix frontend vitest run --root frontend src/components/SceneInspector.test.tsx`
Expected: FAIL — module `./SceneInspector` does not exist.

- [ ] **Step 3: Create RecordDrawer**

Create `frontend/src/components/RecordDrawer.tsx`:

```tsx
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CastDetail } from "../api/client";

export type DrawerTarget =
  | { type: "actor"; kind: "characters" | "pcs"; id: string }
  | { type: "location"; id: string };

export function RecordDrawer({ cid, sid, target, onClose }:
  { cid: string; sid: string; target: DrawerTarget; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [avatar, setAvatar] = useState<string | null>(null);

  useEffect(() => {
    setAvatar(null);
    if (target.type === "actor") {
      api.getCastDetail(cid, sid, target.kind, target.id).then((d: CastDetail) => {
        setTitle(d.name);
        setBody(d.body);
        if (d.kind === "characters") setAvatar(api.campaignImageUrl(cid, d.id, d.version, "avatar"));
      });
    } else {
      api.readEntity({ kind: "campaign", id: cid }, "locations", target.id).then((e) => {
        setTitle(e.meta.name);
        setBody(e.body);
      });
    }
  }, [cid, sid, target]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        <h3>{title}</h3>
        {avatar && (
          <img className="drawer-avatar" alt={`${title} avatar`} src={avatar}
               onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
        )}
        <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown></div>
      </aside>
    </div>
  );
}
```

- [ ] **Step 4: Create SceneInspector**

Create `frontend/src/components/SceneInspector.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { api, type Actor, type SceneContext, type SceneLocation } from "../api/client";
import { fetchModels, type Model } from "../api/models";
import { RecordDrawer, type DrawerTarget } from "./RecordDrawer";

export function SceneInspector({ cid, sid, refreshKey }: { cid: string; sid: string; refreshKey: number }) {
  const [cast, setCast] = useState<Actor[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [ctx, setCtx] = useState<SceneContext | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      Promise.all([api.listCharacters(c.meta.world), api.listPCs(c.meta.world), api.listCampaignPCs(cid)])
        .then(([chars, worldPCs, localPCs]) => {
          const m: Record<string, string> = {};
          for (const x of chars) m[`characters/${x.id}`] = x.name;
          for (const x of [...worldPCs, ...localPCs]) m[`pcs/${x.id}`] = x.name;
          setNames(m);
        });
    });
    fetchModels().then(setModels).catch(() => setModels([]));
  }, [cid]);

  useEffect(() => {
    api.getCast(cid, sid).then(setCast).catch(() => setCast([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));
  }, [cid, sid, refreshKey]);

  const ctxLen = useMemo(
    () => models.find((m) => m.id === ctx?.model)?.context ?? 0,
    [models, ctx]);

  const pct = (t: number) => (ctxLen > 0 ? ` · ${Math.round((t / ctxLen) * 100)}%` : "");
  const nameOf = (a: Actor) => names[`${a.kind}/${a.id}`] ?? a.id;

  return (
    <aside className="inspector">
      <div className="side-section">
        <h4>Active characters</h4>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => (
          <button key={`${a.kind}/${a.id}`} className="inspector-row"
                  onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
            {nameOf(a)} <span className="role">{a.role}</span>
          </button>
        ))}
      </div>

      <div className="side-section">
        <h4>Location</h4>
        {setting?.current
          ? <button className="inspector-row" onClick={() => setDrawer({ type: "location", id: setting.current!.id })}>
              {setting.current.name}
            </button>
          : <div className="field-hint">No setting</div>}
      </div>

      <div className="side-section">
        <h4>Context {ctx ? `· ${ctx.total_tokens.toLocaleString()} tok${pct(ctx.total_tokens)}` : ""}</h4>
        <div className="field-hint">token estimates</div>
        {ctx?.sections.map((s) => (
          <details className="ctx-section" key={s.label}>
            <summary>{s.label} <span className="role">{s.tokens.toLocaleString()} tok{pct(s.tokens)}</span></summary>
            <pre className="ctx-text">{s.text}</pre>
          </details>
        ))}
      </div>

      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
}
```

- [ ] **Step 5: Add inspector + drawer CSS**

Append to `frontend/src/index.css`:

```css
/* ---- scene inspector + record drawer ---- */
.inspector { width: 300px; flex: none; border-left: 1px solid var(--muted); padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 18px; }
.inspector-row { display: block; width: 100%; text-align: left; background: transparent; color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 6px 8px; margin-bottom: 6px; cursor: pointer; }
.inspector-row:hover { border-color: var(--accent); color: var(--accent); }
.inspector-row .role { float: right; color: var(--muted); }
.ctx-section { border: 1px solid var(--muted); border-radius: var(--radius); margin-bottom: 6px; }
.ctx-section > summary { cursor: pointer; padding: 6px 8px; }
.ctx-section .role { color: var(--muted); }
.ctx-text { white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow-y: auto; margin: 0; padding: 8px; border-top: 1px solid var(--muted); font-size: 12px; }
.drawer-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; justify-content: flex-end; z-index: 20; }
.drawer { width: 360px; max-width: 90vw; background: var(--bg); border-left: 1px solid var(--muted); padding: 16px; overflow-y: auto; }
.drawer-close { float: right; background: transparent; border: none; color: var(--muted); cursor: pointer; font-size: 16px; }
.drawer-close:hover { color: var(--accent); }
.drawer-avatar { max-width: 160px; height: auto; display: block; border-radius: var(--radius); border: 1px solid var(--muted); margin: 8px 0; }
@media (max-width: 1100px) { .inspector { display: none; } }
```

- [ ] **Step 6: Run the test + typecheck**

Run: `npx --prefix frontend vitest run --root frontend src/components/SceneInspector.test.tsx`
Then from `frontend/`: `npx tsc -b`
Expected: PASS; tsc clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/RecordDrawer.tsx frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx frontend/src/index.css
git commit -m "feat: SceneInspector and RecordDrawer"
```

---

### Task 8: Mount the inspector (3-column layout)

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `<SceneInspector cid sid refreshKey />` (Task 7).
- Produces: a `ctxKey` counter bumped on scene select / send / retry; the inspector mounted as the right column when a scene is active.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/routes/CampaignView.test.tsx` (check its existing mock block; add `getSceneContext: vi.fn()`, `getCast: vi.fn()`, `getSceneLocation: vi.fn()`, `getCampaign: vi.fn()`, `listCharacters: vi.fn()`, `listPCs: vi.fn()`, `listCampaignPCs: vi.fn()` to the `api` mock if missing, and default them in `beforeEach`: `getSceneContext`→`{model:"m",total_tokens:0,sections:[]}`, `getCast`→`[]`, `getSceneLocation`→`{current:null,visited:[]}`, `getCampaign`→`{meta:{id:"c",world:"w"},body:""}`, the three list calls→`[]`; mock `../api/models` `fetchModels`→`[]`):

```tsx
test("renders the inspector for an active scene", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/c"]}>
    <Routes><Route path="/campaigns/:cid" element={<CampaignView keySet={true} />} /></Routes>
  </MemoryRouter>);
  await screen.findByText(/Active characters/i);
  await screen.findByText(/^Context/);
});
```

(Use the import style already present in that test file; add `Routes, Route` to the react-router-dom import if not present.)

- [ ] **Step 2: Run it to verify it fails**

Run: `npx --prefix frontend vitest run --root frontend src/routes/CampaignView.test.tsx`
Expected: FAIL — no "Active characters" text.

- [ ] **Step 3: Mount the inspector**

In `frontend/src/routes/CampaignView.tsx`, add the import:

```tsx
import { SceneInspector } from "../components/SceneInspector";
```

Add a `ctxKey` state next to the others:

```tsx
  const [ctxKey, setCtxKey] = useState(0);
```

Bump it after a scene is selected and after a stream completes. In `selectScene`, after `setStreaming("")`, add `setCtxKey((n) => n + 1);`. In `runStream`'s `finally` block, after `setBusy(false);`, add `setCtxKey((n) => n + 1);`.

Wrap the layout so the inspector is a third column. Replace the outer return's structure: change the closing of `<section className="main">…</section>` so it is followed by the inspector, both inside `.layout`:

Find the final lines:

```tsx
        </div>
      </section>
    </div>
  );
}
```

and replace with:

```tsx
        </div>
      </section>
      {activeId && <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey} />}
    </div>
  );
}
```

- [ ] **Step 4: Run it to verify it passes + typecheck**

Run: `npx --prefix frontend vitest run --root frontend src/routes/CampaignView.test.tsx`
Then from `frontend/`: `npx tsc -b`
Expected: PASS; tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat: mount SceneInspector as the scene-page right column"
```

---

## Phase 5 — Frontend transcript: cards, edit, quotes

### Task 9: Message cards + inline edit

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `api.editMessage` (Task 6).
- Produces: each message renders as `.msg-card.<role>`; an Edit action saves via `editMessage`, reloads the scene, and bumps `ctxKey`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/routes/CampaignView.test.tsx` (ensure `editMessage: vi.fn()` is in the api mock and `getScene` returns a message, e.g. default `getScene`→`{meta:{id:"s1",title:"S"},messages:[{role:"assistant",content:"hi"}]}` and `listScenes`→`[{id:"s1",title:"S",model:"",created:"",updated:""}]`):

```tsx
test("editing a message saves and reloads", async () => {
  (api.editMessage as any).mockResolvedValue({ ok: true });
  render(<MemoryRouter initialEntries={["/campaigns/c"]}>
    <Routes><Route path="/campaigns/:cid" element={<CampaignView keySet={true} />} /></Routes>
  </MemoryRouter>);
  await screen.findByText("hi");
  fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
  const ta = await screen.findByLabelText(/edit message/i);
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("c", "s1", 0, "hello"));
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx --prefix frontend vitest run --root frontend src/routes/CampaignView.test.tsx`
Expected: FAIL — no Edit button.

- [ ] **Step 3: Add edit state + card markup**

In `frontend/src/routes/CampaignView.tsx`, add edit state near the others:

```tsx
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
```

Add a save handler near `send`:

```tsx
  async function saveEdit() {
    if (!editing || !activeId) return;
    await api.editMessage(cid, activeId, editing.index, editing.text);
    setEditing(null);
    await selectScene(activeId);
  }
```

Replace the messages `.map` in the stream (the block rendering `messages.map((m, i) => ( <div className="msg" …`) with:

```tsx
          {messages.map((m, i) => (
            <div className={`msg-card ${m.role}`} key={i}>
              <div className="msg-card-head">
                <span className="role">{m.role === "user" ? "You" : "Grimoire"}</span>
                {editing?.index !== i && !busy && (
                  <button className="msg-edit" onClick={() => setEditing({ index: i, text: m.content })}>Edit</button>
                )}
              </div>
              {editing?.index === i ? (
                <div className="msg-edit-form">
                  <textarea aria-label="Edit message" rows={4} value={editing.text}
                            onChange={(e) => setEditing({ index: i, text: e.target.value })} />
                  <div className="form-actions">
                    <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                    <button className="primary" onClick={saveEdit}>Save</button>
                  </div>
                </div>
              ) : (
                <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown>
              )}
            </div>
          ))}
          {streaming && (
            <div className="msg-card assistant">
              <div className="msg-card-head"><span className="role">Grimoire</span></div>
              <Markdown remarkPlugins={[remarkGfm]}>{streaming}</Markdown>
              <span className="cursor" />
            </div>
          )}
```

- [ ] **Step 4: Add card CSS**

Append to `frontend/src/index.css`:

```css
/* ---- transcript message cards ---- */
.msg-card { border: 1px solid var(--muted); border-radius: var(--radius); padding: 10px 12px; margin-bottom: 12px; }
.msg-card.assistant { background: var(--surface); border-left: 2px solid var(--accent); }
.msg-card.user { background: transparent; }
.msg-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.msg-edit { background: transparent; border: none; color: var(--muted); cursor: pointer; font-size: 12px; }
.msg-edit:hover { color: var(--accent); }
.msg-edit-form textarea { width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--accent); border-radius: var(--radius); padding: 8px; font-family: var(--font-body); resize: vertical; }
```

- [ ] **Step 5: Run it to verify it passes + typecheck**

Run: `npx --prefix frontend vitest run --root frontend src/routes/CampaignView.test.tsx`
Then from `frontend/`: `npx tsc -b`
Expected: PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/index.css
git commit -m "feat: transcript message cards with inline edit"
```

---

### Task 10: Quote-coloring plugin + toggle

**Files:**
- Create: `frontend/src/markdown/quotePlugin.ts`
- Create: `frontend/src/markdown/quotePlugin.test.ts`
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `api.getConfig` (`quote_color`).
- Produces: a rehype plugin wrapping double-quoted runs in `span.quoted`; the `.stream` gets `color-quotes` when the setting is on.

- [ ] **Step 1: Write the failing plugin test**

Create `frontend/src/markdown/quotePlugin.test.ts`:

```ts
import { quotePlugin } from "./quotePlugin";

test("wraps double-quoted text in span.quoted", () => {
  const tree: any = { type: "root", children: [
    { type: "element", tagName: "p", children: [
      { type: "text", value: 'She said "hello there" softly.' },
    ] },
  ] };
  quotePlugin()(tree);
  const p = tree.children[0];
  const span = p.children.find((c: any) => c.type === "element" && c.tagName === "span");
  expect(span).toBeTruthy();
  expect(span.properties.className).toContain("quoted");
  expect(span.children[0].value).toBe('"hello there"');
});

test("leaves unquoted text untouched", () => {
  const tree: any = { type: "root", children: [
    { type: "element", tagName: "p", children: [{ type: "text", value: "no quotes here" }] },
  ] };
  quotePlugin()(tree);
  expect(tree.children[0].children).toEqual([{ type: "text", value: "no quotes here" }]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx --prefix frontend vitest run --root frontend src/markdown/quotePlugin.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the plugin**

Create `frontend/src/markdown/quotePlugin.ts`:

```ts
// rehype plugin: wrap double-quoted runs (straight or curly) in <span class="quoted">.
// Operates within a single text node; quotes spanning markdown nodes are left alone.
const QUOTE = /["“][^"”]*["”]/g;

function splitQuotes(value: string): any[] {
  const out: any[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  QUOTE.lastIndex = 0;
  while ((m = QUOTE.exec(value))) {
    if (m.index > last) out.push({ type: "text", value: value.slice(last, m.index) });
    out.push({
      type: "element", tagName: "span",
      properties: { className: ["quoted"] },
      children: [{ type: "text", value: m[0] }],
    });
    last = m.index + m[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: any): void {
  if (!node || !Array.isArray(node.children)) return;
  const next: any[] = [];
  for (const child of node.children) {
    if (child.type === "text" && QUOTE.test(child.value)) {
      next.push(...splitQuotes(child.value));
    } else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function quotePlugin() {
  return (tree: any) => walk(tree);
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npx --prefix frontend vitest run --root frontend src/markdown/quotePlugin.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire it into the transcript**

In `frontend/src/routes/CampaignView.tsx`:

Add imports:

```tsx
import { api, type SceneMeta, type Message } from "../api/client";
import { quotePlugin } from "../markdown/quotePlugin";
```

(The `api` import already exists — only add the `quotePlugin` import.)

Add state and load:

```tsx
  const [colorQuotes, setColorQuotes] = useState(false);
```

In the mount effect (the one keyed on `[cid]`), add:

```tsx
    api.getConfig().then((c) => setColorQuotes(c.quote_color === "on")).catch(() => {});
```

Add `color-quotes` to the stream container — change `<div className="stream" ref={streamRef}>` to:

```tsx
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}>
```

Add `rehypePlugins={[quotePlugin]}` to every `<Markdown>` in the stream (the message card render and the streaming render):

```tsx
                <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[quotePlugin]}>{m.content}</Markdown>
```
```tsx
              <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[quotePlugin]}>{streaming}</Markdown>
```

Add the import for `getConfig` usage is already covered by `api`. Append CSS to `frontend/src/index.css`:

```css
.color-quotes .quoted { color: var(--accent); }
```

- [ ] **Step 6: Full frontend suite + typecheck**

Run: `npx --prefix frontend vitest run --root frontend`
Then from `frontend/`: `npx tsc -b`
Expected: all pass; tsc clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/markdown/quotePlugin.ts frontend/src/markdown/quotePlugin.test.ts frontend/src/routes/CampaignView.tsx frontend/src/index.css
git commit -m "feat: quote-coloring rehype plugin and scene toggle"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` — all pass.
- [ ] Frontend: `npx --prefix frontend vitest run --root frontend` — all pass.
- [ ] Types: from `frontend/`, `npx tsc -b` — clean.
- [ ] Manual (optional): start the app, open a scene — inspector shows cast/location/context; click a cast row → drawer; edit a message; toggle quote color in Config and confirm dialogue colors.

## Self-review notes (coverage map)

- Spec A (3-col layout) → Task 8 + `.inspector` CSS (Task 7).
- Spec B (cast/location/drawer) → Tasks 4, 7.
- Spec C (context breakdown) → Tasks 2, 3, 7.
- Spec D (response cards) → Task 9.
- Spec E (edit posts) → Tasks 5, 9.
- Spec F (global system prompt) → Tasks 1, 2, 6.
- Spec G (quote-color toggle) → Tasks 1, 6, 10.
- Spec testing → each task's tests; final full-suite gate above.
