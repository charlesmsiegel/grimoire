# Offscreen Scenes (PC-less) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scenes without the player character, driven in director mode (ephemeral steering notes / Continue), with full offscreen-greeting support and a mode-first chooser.

**Architecture:** An explicit `pcless: "true"` frontmatter flag on scenes and greetings. The chat route never persists the user turn in a pcless scene — the note rides one LLM call. The context builder adds an "Offscreen scene" system section plus the campaign PC(s) as a not-present reference. Frontend specializes the chooser, composer, cast panel, inspector, and greeting editor.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend). Spec: `docs/superpowers/specs/2026-07-05-offscreen-scenes-design.md`.

## Global Constraints

- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (run from repo root).
- Frontend tests: `npx vitest run` and `npx tsc -b`, **run from `frontend/`** (never `npx --prefix`).
- Frontmatter values are scalar strings: boolean flags serialize as `"true"` (or the key is absent/empty). Absent ⇒ false; no migration for existing files.
- The scene body script format (`**Speaker:** content`) is unchanged.
- Backend route tests go in `backend/tests/test_routes.py` and reuse its `client` fixture, `_world`/`_campaign`/`_empty_scene`/`_cast_pc` helpers, and the `FakeOpenRouter`/`CapturingOpenRouter` fakes.
- Execution happens in a worktree under `.worktrees/` at the repo root (user preference).
- Commit after every task.

---

### Task 1: Scene `pcless` flag (store + routes)

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (`create_scene` ~line 112, `list_scenes` ~line 128)
- Modify: `backend/src/grimoire/routes.py` (`NewScene` ~line 144, `post_scene` ~line 1313)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: existing `scenes.create_scene(cid, title)`, `parse_frontmatter_head`.
- Produces: `scenes.create_scene(cid: str, title: str, pcless: bool = False) -> str`; `scenes.is_pcless(cid: str, sid: str) -> bool` (missing scene ⇒ False); `list_scenes` dicts gain `"pcless": bool`; `POST /campaigns/{cid}/scenes` accepts `{"title": ..., "pcless": true}`.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_routes.py`)

```python
# ---- offscreen (pcless) scenes ----
def test_scene_pcless_flag_roundtrip(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    normal = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    listing = {s["id"]: s["pcless"] for s in client.get(f"/api/campaigns/{cid}/scenes").json()}
    assert listing[sid] is True and listing[normal] is False
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["meta"]["pcless"] == "true"
    assert store.scenes.is_pcless(cid, sid) is True
    assert store.scenes.is_pcless(cid, normal) is False
    assert store.scenes.is_pcless(cid, "missing") is False
```

- [ ] **Step 2: Run it — expect FAIL** (`pcless` KeyError / TypeError)

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_scene_pcless_flag_roundtrip -q`

- [ ] **Step 3: Implement**

In `scenes.py`, change `create_scene`'s signature and meta line:

```python
def create_scene(cid: str, title: str, pcless: bool = False) -> str:
    ...
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    if pcless:
        meta["pcless"] = "true"
```

In `list_scenes`, add to the appended dict:

```python
                "pcless": meta.get("pcless") == "true",
```

Add after `list_scenes`:

```python
def is_pcless(cid: str, sid: str) -> bool:
    """A scene deliberately without a player character (director-driven)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return False
    return parse_frontmatter_head(p).get("pcless") == "true"
```

In `routes.py`:

```python
class NewScene(BaseModel):
    title: str | None = None
    pcless: bool = False
```

```python
@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene):
    title = body.title or "New scene"
    try:
        return {"id": store.scenes.create_scene(cid, title, pcless=body.pcless)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
```

- [ ] **Step 4: Run test — expect PASS**, then run the whole backend suite (`... -m pytest backend -q`).

- [ ] **Step 5: Commit** — `feat(backend): scenes carry an explicit pcless flag`

---

### Task 2: Reject player seating in a pcless scene

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`_seat_cast_member` ~line 1736)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.scenes.is_pcless` (Task 1).
- Produces: `POST .../cast` (and `/cast/batch`) → 400 for any `role=player` seat in a pcless scene.

- [ ] **Step 1: Failing test**

```python
def test_offscreen_scene_rejects_player_seating(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    pid = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara"}).json()["pc"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": pid}).status_code == 400
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Desmond"})
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond", "role": "player"}).status_code == 400
    # NPCs still seat fine
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond"}).status_code == 200
```

- [ ] **Step 2: Run — expect FAIL** (200 where 400 expected).

- [ ] **Step 3: Implement** — in `_seat_cast_member`, right after `role` is validated:

```python
    if role == "player" and store.scenes.is_pcless(cid, sid):
        raise HTTPException(status_code=400, detail="cannot seat a player in an offscreen scene")
```

- [ ] **Step 4: Run test + full backend suite — expect PASS.**

- [ ] **Step 5: Commit** — `feat(backend): pcless scenes refuse player seating`

---

### Task 3: Offscreen context assembly

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (`scene_substitutions` ~line 42, `OPENER_INSTRUCTION` ~line 121, `build_opener_messages` ~line 129, `_assemble` ~line 316)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `scenes.is_pcless` (Task 1); `appearances.roster`; `scene["meta"].get("pcless")` from `read_scene`.
- Produces: `context.build_director_messages(cid: str, sid: str, note: str) -> list[dict]`; system sections `"Offscreen scene"` and `"Absent player characters"` in pcless scenes; `{{user}}` in pcless scenes substitutes to campaign-level player names; pcless openers use `OFFSCREEN_OPENER_INSTRUCTION`.

- [ ] **Step 1: Failing tests**

```python
def test_offscreen_context_has_director_section_and_absent_pc(client):
    wid, cid = _campaign(client)
    # the PC enters the campaign roster by being seated in a *different* scene
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    _cast_pc(client, wid, cid, other, name="Elara Vane")
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    sections = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()["sections"]
    labels = {s["label"]: s["text"] for s in sections}
    assert "director's notes" in labels["Offscreen scene"]
    assert "Elara Vane" in labels["Offscreen scene"]           # named as not-present
    assert "Elara Vane" in labels["Absent player characters"]  # persona as reference
    normal = {s["label"] for s in
              client.get(f"/api/campaigns/{cid}/scenes/{other}/context").json()["sections"]}
    assert "Offscreen scene" not in normal


def test_offscreen_opener_uses_third_person_instruction(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "the cult meets"}) as r:
        r.read()
    assert "offscreen scene" in cap.messages[0]["content"].lower()
    assert "No player character is present" in cap.messages[0]["content"]
```

- [ ] **Step 2: Run — expect FAIL** (KeyError "Offscreen scene").

- [ ] **Step 3: Implement** in `context.py`.

Add after `_char_player_block`:

```python
def _campaign_player_refs(cid: str, croot) -> tuple[list[str], list[str]]:
    """(persona blocks, names) of every campaign-level player actor, seated in
    the scene or not — the offscreen reference cast."""
    blocks: list[str] = []
    names: list[str] = []
    for a in appearances.roster(cid):
        if a["role"] != "player":
            continue
        try:
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], a["version"])
                blocks.append(_pc_persona_block(p))
                names.append(p.get("name", a["id"]))
            else:
                data = characters.read_card(croot, a["id"], a["version"])["data"]
                blocks.append(_char_player_block(data))
                names.append(data.get("name", a["id"]))
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            continue
    return blocks, names


def _offscreen_instruction(ref_names: list[str]) -> str:
    text = (
        "This is an offscreen scene: no player character is present. The user's "
        "messages are out-of-scene director's notes — follow their steering, never "
        "acknowledge them in the fiction, and never address the director. Write "
        "only the NPCs and the world."
    )
    if ref_names:
        text += (
            " The player character(s) " + ", ".join(ref_names) + " are known to "
            "the world but NOT present: they may be discussed or referenced, but "
            "must never appear, speak, or act in this scene."
        )
    return text
```

In `scene_substitutions`, before the `return`:

```python
    if not player_names and scenes.is_pcless(cid, sid):
        player_names = _campaign_player_refs(cid, croot)[1]
```

In `_assemble`, replace the `subs = ...` line (after `npc_names`):

```python
    pcless = scene["meta"].get("pcless") == "true"
    ref_blocks: list[str] = []
    ref_names: list[str] = []
    if pcless:
        ref_blocks, ref_names = _campaign_player_refs(cid, croot)
    subs = {"{{user}}": ", ".join(player_names or ref_names), "{{char}}": ", ".join(npc_names)}
```

After the `add("Player personas", ...)` line:

```python
    if pcless:
        add("Offscreen scene", _offscreen_instruction(ref_names))
        add("Absent player characters", "\n\n".join(b for b in ref_blocks if b))
```

Add next to `OPENER_INSTRUCTION`:

```python
OFFSCREEN_OPENER_INSTRUCTION = (
    "Write the opening narration for a new offscreen scene based on the prompt below. "
    "Set the scene vividly in the third person. No player character is present; write "
    "only the NPCs and the world."
)
```

In `build_opener_messages`, replace the `system_text` assembly:

```python
    instruction = (OFFSCREEN_OPENER_INSTRUCTION if scenes.is_pcless(cid, sid)
                   else OPENER_INSTRUCTION)
    sections = "\n\n".join(t for _, t in a["system"]).strip()
    system_text = (instruction + "\n\n" + sections).strip() if sections else instruction
```

Add after `build_messages`:

```python
def build_director_messages(cid: str, sid: str, note: str) -> list[dict]:
    """One offscreen director turn: full system + history, then the note as the
    final user message. The note rides only this call — never persisted."""
    a = _assemble(cid, sid)
    messages: list[dict] = []
    system_text = "\n\n".join(t for _, t in a["system"]).strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages += a["history"]
    messages.append({"role": "user", "content": note})
    if a["post_history"]:
        messages.append({"role": "system", "content": a["post_history"]})
    return messages
```

- [ ] **Step 4: Run tests + full backend suite — expect PASS.**

- [ ] **Step 5: Commit** — `feat(backend): offscreen context — director section, absent-PC reference, opener variant`

---

### Task 4: Director chat turn (never-stored notes)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`ChatTurn` ~line 160, `post_chat` ~line 1406)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `context.build_director_messages`, `scenes.is_pcless`.
- Produces: pcless `POST .../chat` streams without persisting a user turn; empty `content` ⇒ `"Continue the scene."`; normal scenes now 400 on blank content.

- [ ] **Step 1: Failing tests**

```python
def test_offscreen_chat_never_persists_the_director_note(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouter(["**Grimoire:** The cult convenes."])
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "the guard grows suspicious"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs and all(m["role"] == "assistant" for m in msgs)
    assert "guard grows suspicious" not in json.dumps(msgs)


def test_offscreen_chat_empty_note_sends_continue(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": ""}) as r:
        r.read()
    assert [m for m in cap.messages if m["role"] == "user"] == \
        [{"role": "user", "content": "Continue the scene."}]


def test_chat_rejects_blank_content_in_a_normal_scene(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "k"})
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": " "}).status_code == 400
```

- [ ] **Step 2: Run — expect FAIL** (user message persisted / no 400).

- [ ] **Step 3: Implement**

```python
class ChatTurn(BaseModel):
    content: str = ""
```

```python
@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    if store.scenes.is_pcless(cid, sid):
        # director turn: the note steers this one generation and is never stored
        note = turn.content.strip() or "Continue the scene."
        messages = store.context.build_director_messages(cid, sid, note)
        return _chat_stream(cid, sid, messages, cfg, client)
    if not turn.content.strip():
        raise HTTPException(status_code=400, detail="empty message")
    names = store.appearances.player_names(cid, sid)
    speaker = names[0] if len(names) == 1 else None
    if speaker:
        store.scenes.stamp_user_speaker(cid, sid, speaker)
    store.scenes.append_message(cid, sid, "user", turn.content, speaker=speaker)
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)
```

- [ ] **Step 4: Run tests + full backend suite — expect PASS** (watch for existing chat tests sending non-blank content; they should be unaffected).

- [ ] **Step 5: Commit** — `feat(backend): pcless chat turns are ephemeral director notes`

---

### Task 5: Greeting `pcless` flag

**Files:**
- Modify: `backend/src/grimoire/store/greetings.py` (`_meta_dict` ~line 45, `create_greeting` ~line 78, `update_greeting` ~line 107, `availability` ~line 200)
- Modify: `backend/src/grimoire/routes.py` (`GreetingCreate` ~line 193, `GreetingUpdate` ~line 213, `post_world_greeting` ~line 824, `put_world_greeting` ~line 857, `post_campaign_greeting` ~line 1916, `put_campaign_greeting` ~line 1938)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: greeting meta dicts gain `"pcless": bool`; `create_greeting(..., pcless: bool = False)`; `update_greeting(..., pcless: bool | None = None)`; `availability()` output rows gain `"pcless": bool`; both greeting create/update routes accept `pcless`.

- [ ] **Step 1: Failing test**

```python
def test_greeting_pcless_roundtrip_and_availability(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver,
        "body": "The cult meets.", "pcless": True}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/greetings/{g}").json()["meta"]["pcless"] is True
    client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"pcless": False})
    assert client.get(f"/api/campaigns/{cid}/greetings/{g}").json()["meta"]["pcless"] is False
    client.put(f"/api/campaigns/{cid}/greetings/{g}", json={"pcless": True})
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert [a["pcless"] for a in avail if a["id"] == g] == [True]
```

- [ ] **Step 2: Run — expect FAIL** (`pcless` missing from meta).

- [ ] **Step 3: Implement**

`greetings.py` — `_meta_dict` gains a key:

```python
        "pcless": meta.get("pcless") == "true",
```

`create_greeting` — add `pcless: bool = False` to the signature (after `present`) and to `meta`:

```python
    meta = {"name": name, "character": character, "version": version,
            "present": ",".join(present or []),
            "requires_tags": ",".join(requires_tags or []),
            "predecessor_join": predecessor_join,
            "pcless": "true" if pcless else ""}
```

`update_greeting` — add `pcless: bool | None = None` to the signature and:

```python
    if pcless is not None:
        meta["pcless"] = "true" if pcless else ""
```

`availability` — the output row gains `"pcless": g["pcless"]`:

```python
        out.append({"id": gid, "name": g["name"], "available": not reasons,
                    "reasons": reasons, "pcless": g["pcless"]})
```

`routes.py` — `GreetingCreate` gains `pcless: bool = False`; `GreetingUpdate` gains `pcless: bool | None = None`. Pass them through in all four routes, e.g.:

```python
    gid = store.greetings.create_greeting(root, body.name, body.character, body.version,
                                          body.body, body.requires_tags,
                                          body.predecessor_join, present=body.present,
                                          pcless=body.pcless)
```

```python
        store.greetings.update_greeting(root, gid, name=body.name, body=body.body,
                                        requires_tags=body.requires_tags,
                                        predecessor_join=body.predecessor_join,
                                        present=body.present, pcless=body.pcless)
```

(Apply identically to the world-scope create/update routes.)

- [ ] **Step 4: Run test + full backend suite — expect PASS.**

- [ ] **Step 5: Commit** — `feat(backend): greetings carry a pcless flag through meta and availability`

---

### Task 6: Offscreen start-from-greeting

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (add `set_pcless`)
- Modify: `backend/src/grimoire/store/playing.py` (`start_from_greeting` ~line 98)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: greeting `meta["pcless"]` (Task 5), `scene_substitutions` pcless fallback (Task 3), `appearances.players_in_scene`.
- Produces: `scenes.set_pcless(cid, sid) -> None` (raises `SceneNotFound`); starting a pcless greeting stamps the scene pcless and substitutes `{{user}}` to the campaign PC name; mismatches raise `PlayError` (409 at the route).

- [ ] **Step 1: Failing tests**

```python
def test_offscreen_greeting_stamps_scene_and_substitutes_pc_name(client):
    wid, cid = _campaign(client)
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    _cast_pc(client, wid, cid, other, name="Elara Vane")
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver,
        "body": "While {{user}} sleeps, the cult convenes.", "pcless": True}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 200
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["meta"]["pcless"] == "true"          # plain scene got flagged
    assert "While Elara Vane sleeps" in scene["messages"][0]["content"]


def test_pc_greeting_cannot_start_an_offscreen_scene(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Meet", "character": "vex", "version": ver, "body": "Hi {{user}}."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Cabal", "pcless": True}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 409


def test_offscreen_greeting_rejects_a_scene_with_players(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    _cast_pc(client, wid, cid, sid)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Cabal", "character": "vex", "version": ver, "body": "x",
        "pcless": True}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 409
```

- [ ] **Step 2: Run — expect FAIL** (meta lacks pcless / 200 where 409 expected).

- [ ] **Step 3: Implement**

`scenes.py`, after `stamp_greeting`:

```python
def set_pcless(cid: str, sid: str) -> None:
    """Flag a scene as deliberately player-less (an offscreen greeting stamps it)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["pcless"] = "true"
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

`playing.py` — `start_from_greeting` gains the guards and stamp (full function):

```python
def start_from_greeting(cid: str, sid: str, gid: str) -> None:
    croot = campaigns.campaign_root(cid)
    g = greetings.read_greeting(croot, gid)["meta"]   # raises GreetingNotFound
    scene = scenes.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    scene_pcless = scene["meta"].get("pcless") == "true"
    if scene_pcless and not g["pcless"]:
        raise PlayError("an offscreen scene must start from an offscreen greeting")
    if g["pcless"] and appearances.players_in_scene(cid, sid):
        raise PlayError("an offscreen greeting cannot start a scene with players seated")
    if not {a["id"]: a["available"] for a in available_greetings(cid)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    # Cast everyone present at the opener. A locked version always wins; otherwise
    # the primary uses the greeting's version and co-present characters their default.
    for actor in dict.fromkeys([g["character"], *g["present"]]):
        version = appearances.locked_version(cid, "characters", actor)
        if version is None:
            version = g["version"] if actor == g["character"] else \
                characters.read_character(croot, actor)["meta"]["default_version"]
        appearances.appear(cid, sid, "characters", actor, version, "npc")
    if g["pcless"] and not scene_pcless:
        scenes.set_pcless(cid, sid)  # before substitution: {{user}} needs the pcless fallback
    _mark_played(cid, gid)
    scenes.stamp_greeting(cid, sid, gid)
    text = context._substitute(greetings.read_greeting(croot, gid)["body"],
                               context.scene_substitutions(cid, sid))
    scenes.append_message(cid, sid, "assistant", text)
```

- [ ] **Step 4: Run tests + full backend suite — expect PASS.**

- [ ] **Step 5: Commit** — `feat(backend): offscreen greetings stamp and guard their scenes`

---

### Task 7: Offscreen scene suggestions

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py` (`build_snapshot` ~line 74, `INSTRUCTION` ~line 160, `greeting_candidates` ~line 141, `build_prompt` ~line 199, `parse_output` ~line 234)
- Modify: `backend/src/grimoire/routes.py` (`post_scene_suggestions` ~line 1342)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: availability rows' `pcless` (Task 5).
- Produces: `POST /campaigns/{cid}/scene-suggestions?offscreen=true`; `build_snapshot(cid, offscreen=False)`, `greeting_candidates(cid, after=None, pcless=False)`, `build_prompt(snapshot, greeting_candidates=None, offscreen=False)`, `parse_output(text, cid, offscreen=False)`.

- [ ] **Step 1: Failing tests** (also add the fake once, near `CapturingOpenRouter`)

```python
class FakeCompleter:
    def __init__(self, text):
        self.text = text

    async def complete(self, messages, model, key):
        self.messages = messages
        return self.text
```

```python
def test_offscreen_suggestions_filter_player_cast(client):
    wid, cid = _campaign(client)
    other = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Tavern"}).json()["id"]
    pid = _cast_pc(client, wid, cid, other, name="Elara Vane")
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    client.put("/api/config", json={"openrouter_key": "k"})
    fake = FakeCompleter(json.dumps({"suggestions": [{
        "title": "Plot", "premise": "The cult schemes.",
        "cast": ["characters:vex", f"pcs:{pid}"], "location": ""}]}))
    client.app.dependency_overrides[routes.get_openrouter] = lambda: fake
    out = client.post(f"/api/campaigns/{cid}/scene-suggestions?offscreen=true").json()
    assert out["suggestions"][0]["cast"] == [{"kind": "characters", "id": "vex", "name": "Vex"}]
    assert "offscreen" in fake.messages[0]["content"].lower()
    assert f"pcs:{pid}" not in fake.messages[1]["content"]  # players withheld from the cast list


def test_offscreen_suggestions_rank_only_offscreen_greetings(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    for name in ("Alpha", "Beta", "Gamma"):
        client.post(f"/api/campaigns/{cid}/greetings", json={
            "name": name, "character": "vex", "version": ver, "body": "x", "pcless": True})
    client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "Normal", "character": "vex", "version": ver, "body": "y"})
    client.put("/api/config", json={"openrouter_key": "k"})
    fake = FakeCompleter(json.dumps({"suggestions": [], "greeting_picks": ["alpha", "beta"]}))
    client.app.dependency_overrides[routes.get_openrouter] = lambda: fake
    out = client.post(f"/api/campaigns/{cid}/scene-suggestions?offscreen=true").json()
    assert "Available greetings" in fake.messages[1]["content"]
    assert "Normal" not in fake.messages[1]["content"]
    assert out["greeting_picks"] == ["alpha", "beta"]
```

- [ ] **Step 2: Run — expect FAIL** (unexpected `offscreen` param has no effect; PC token passes through).

- [ ] **Step 3: Implement**

`suggest.py`:

```python
OFFSCREEN_INSTRUCTION = (
    "You help a game master write OFFSCREEN scenes for a role-play campaign — scenes "
    "that happen away from the player character, showing what NPCs do, plan, and want "
    "when the player is not there. Given the current situation below, propose 3-4 "
    "DISTINCT offscreen scene openings that each advance an open plot thread, reveal "
    "an NPC's motivations, or land on an upcoming date or birthday. Never include the "
    'player character in the cast. Reply with ONLY a JSON object with key "suggestions": '
    'a list of {"title" (a short label), "premise" (2-3 sentences the GM can open on), '
    '"cast" (list of "<kind>:<id>" tokens chosen ONLY from the available cast below), '
    '"location" (one location id from the available locations, or "")}. Use only the '
    "ids given; do not invent ids."
)
```

`build_snapshot(cid: str, offscreen: bool = False)` — wrap the player-append loop:

```python
    if not offscreen:
        for a in roster:
            if a["role"] != "player":
                continue
            ...existing body unchanged...
```

`greeting_candidates(cid: str, after: str | None = None, pcless: bool = False)` — filter:

```python
    avail = [g for g in playing.available_greetings(cid, after)
             if g["available"] and g.get("pcless", False) == pcless]
```

`build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None, offscreen: bool = False)`:

```python
    instruction = OFFSCREEN_INSTRUCTION if offscreen else INSTRUCTION
    content = _render_snapshot(snapshot)
    if greeting_candidates:
        instruction += RANK_INSTRUCTION
        ...
```

`parse_output(text: str, cid: str, offscreen: bool = False)` — `_valid_token` becomes:

```python
    def _valid_token(tok: str) -> bool:
        kind, _, aid = tok.partition(":")
        if kind == "characters" and aid in char_ids:
            return True
        return not offscreen and tok in player_tokens
```

`routes.py` — `post_scene_suggestions` signature gains `offscreen: bool = False` and threads it:

```python
    candidates = store.suggest.greeting_candidates(cid, after, pcless=offscreen)
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid, offscreen=offscreen),
                                          candidates, offscreen=offscreen)
    ...
    for s in store.suggest.parse_output(text, cid, offscreen=offscreen):
```

- [ ] **Step 4: Run tests + full backend suite — expect PASS.**

- [ ] **Step 5: Commit** — `feat(backend): offscreen scene suggestions and greeting ranking`

---

### Task 8: Frontend API client types

**Files:**
- Modify: `frontend/src/api/client.ts` (`SceneMeta` ~line 73, `Greeting` ~line 131, `GreetingDraft` ~line 143, `Availability` ~line 152, `createScene` ~line 290, `updateGreeting` ~line 441, `sceneSuggestions` ~line 503)

**Interfaces:**
- Produces: `SceneMeta.pcless?: boolean`; `Availability.pcless?: boolean`; `Greeting.pcless: boolean`; `GreetingDraft.pcless?: boolean`; `api.createScene(cid, title?, pcless?)`; `api.sceneSuggestions(cid, after?, offscreen?)`; `updateGreeting` patch accepts `pcless?: boolean`.

- [ ] **Step 1: Implement** (types-only change; the existing suite is the safety net)

```ts
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string; pcless?: boolean };
```

`Greeting` gains `pcless: boolean;` — `GreetingDraft` gains `pcless?: boolean;` — `Availability` gains `pcless?: boolean;` — `updateGreeting`'s patch type gains `pcless?: boolean`.

```ts
  createScene: (cid: string, title?: string, pcless?: boolean) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`, { title, pcless }),
```

```ts
  sceneSuggestions: (cid: string, after?: string, offscreen?: boolean) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    if (offscreen) params.set("offscreen", "true");
    const qs = params.toString();
    return request<{ suggestions: SceneSuggestion[]; greeting_picks?: string[] }>(
      "POST", `/api/campaigns/${cid}/scene-suggestions${qs ? `?${qs}` : ""}`);
  },
```

- [ ] **Step 2: Verify** — from `frontend/`: `npx tsc -b` then `npx vitest run`. Expect clean.

- [ ] **Step 3: Commit** — `feat(frontend): API types for pcless scenes, greetings, offscreen suggestions`

---

### Task 9: NewSceneChooser mode step

**Files:**
- Modify: `frontend/src/components/NewSceneChooser.tsx`
- Test: `frontend/src/components/NewSceneChooser.test.tsx`

**Interfaces:**
- Consumes: `api.createScene(cid, undefined, boolean)`, `api.sceneSuggestions(cid, after, boolean)`, `Availability.pcless` (Task 8).
- Produces: chooser opens on a mode step (`"With your PC"` / `"Offscreen (NPCs only)"`); all fetches deferred until a mode is picked; every create carries the mode's pcless.

- [ ] **Step 1: Failing tests.** First update the harness: `renderChooser` picks the PC mode so existing tests keep their flow, and the changed call shapes are asserted.

```tsx
async function renderChooser(props: Partial<{ afterSid: string | null; keySet: boolean;
                                              onClose: () => void; onCreated: (sid: string, p?: string) => void }> = {}) {
  render(<NewSceneChooser cid="c" afterSid={props.afterSid !== undefined ? props.afterSid : "s1"}
                          keySet={props.keySet ?? true}
                          onClose={props.onClose ?? (() => {})}
                          onCreated={props.onCreated ?? (() => {})} />);
  fireEvent.click(await screen.findByText("With your PC"));
}
```

Then in every existing test change `renderChooser(...)` to `await renderChooser(...)`, and update the call-shape assertions:
- `expect(api.availableGreetings).toHaveBeenCalledWith("c", "s1")` — unchanged.
- `expect(api.createScene).toHaveBeenCalledWith("c")` → `("c", undefined, false)`.
- `expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1")` → `("c", "s1", false)`.

New tests:

```tsx
test("mode step gates all fetches and offscreen filters to pcless greetings", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: false },
    { id: "cabal", name: "The Cabal", available: true, reasons: [], unlocked: false, pcless: true },
  ]);
  render(<NewSceneChooser cid="c" afterSid="s1" keySet={true}
                          onClose={() => {}} onCreated={() => {}} />);
  expect(api.availableGreetings).not.toHaveBeenCalled();
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByText(/offscreen \(npcs only\)/i));
  await screen.findByText("The Cabal");
  expect(screen.queryByText("Reckoning")).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", true);
});

test("offscreen manual create flags the scene pcless", async () => {
  const onCreated = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" keySet={true}
                          onClose={() => {}} onCreated={onCreated} />);
  fireEvent.click(await screen.findByText(/offscreen \(npcs only\)/i));
  fireEvent.click(await screen.findByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  expect(api.createScene).toHaveBeenCalledWith("c", undefined, true);
});

test("pc mode hides pcless greetings", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: false },
    { id: "cabal", name: "The Cabal", available: true, reasons: [], unlocked: false, pcless: true },
  ]);
  await renderChooser();
  await screen.findByText("Reckoning");
  expect(screen.queryByText("The Cabal")).toBeNull();
});
```

- [ ] **Step 2: Run — expect FAIL** (`With your PC` not found).

Run (from `frontend/`): `npx vitest run src/components/NewSceneChooser.test.tsx`

- [ ] **Step 3: Implement** in `NewSceneChooser.tsx`:

```tsx
const [mode, setMode] = useState<"pc" | "offscreen" | null>(null);
```

Both fetch effects gate on mode and re-key on it:

```tsx
useEffect(() => {
  if (!mode) return;
  api.availableGreetings(cid, afterSid ?? undefined)
    .then((all) => setGreetings(all.filter(
      (g) => g.available && !!g.pcless === (mode === "offscreen"))))
    .catch((err) => { setGreetings([]); setError(errMsg(err)); });
}, [cid, afterSid, mode]);

useEffect(() => {
  if (!keySet || !mode) return;
  api.sceneSuggestions(cid, afterSid ?? undefined, mode === "offscreen")
    .then((r) => { setSuggestions(r.suggestions); setPicks(r.greeting_picks ?? []); })
    .catch((err) => { setSuggestions([]); setPicks([]); setError(errMsg(err)); });
}, [cid, afterSid, keySet, mode]);
```

`create()` uses the mode:

```tsx
      const { id } = await api.createScene(cid, undefined, mode === "offscreen");
```

Render — inside `.chooser`, after the `<h3>`/error banner, wrap the body:

```tsx
        {mode === null ? (
          <>
            <div className="role">What kind of scene?</div>
            <button className="chooser-card" onClick={() => setMode("pc")}>
              <span className="chooser-card-title">With your PC</span>
              <span className="chooser-card-premise">Your player character takes part.</span>
            </button>
            <button className="chooser-card" onClick={() => setMode("offscreen")}>
              <span className="chooser-card-title">Offscreen (NPCs only)</span>
              <span className="chooser-card-premise">
                What happens away from your PC — NPC plans, motivations, and events you don't witness.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={onClose}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            {/* existing greeting/generated sections and form-actions, unchanged */}
          </>
        )}
```

- [ ] **Step 4: Run the file's tests, then the full suite + `npx tsc -b` — expect PASS.**

- [ ] **Step 5: Commit** — `feat(frontend): new-scene chooser picks PC vs offscreen mode first`

---

### Task 10: CastPanel hides player seating

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx`
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Produces: `CastPanel` accepts optional `pcless?: boolean`; when set, the actor-kind select and role select are gone and every add is `characters`/`npc`.

- [ ] **Step 1: Failing test** (follow the file's existing mock setup / beforeEach):

```tsx
test("offscreen scene hides PC and player seating", async () => {
  render(<CastPanel cid="c" sid="s" keySet={true} onSeeded={() => {}} pcless />);
  await screen.findByText(/add to scene/i);
  expect(screen.queryByLabelText("Actor kind")).toBeNull();
  expect(screen.queryByLabelText("Role")).toBeNull();
});
```

- [ ] **Step 2: Run — expect FAIL** (TS: unknown prop / selects present).

- [ ] **Step 3: Implement** — add `pcless` to the props:

```tsx
export function CastPanel({
  cid, sid, keySet, onSeeded, onSceneRenamed, initialPrompt, pcless,
}: {
  cid: string;
  sid: string;
  keySet: boolean;
  onSeeded: () => void;
  onSceneRenamed?: (id: string) => void;
  initialPrompt?: string;
  pcless?: boolean;
}) {
```

Wrap the kind select in `{!pcless && (...)}`, change the role select condition to `{kind === "characters" && !pcless && (...)}`, and in `add()`:

```tsx
      await api.addToCast(cid, sid, {
        kind, id: actorId,
        role: pcless ? "npc" : kind === "pcs" ? "player" : role,
      });
```

- [ ] **Step 4: Run file tests + full suite + tsc — expect PASS.**

- [ ] **Step 5: Commit** — `feat(frontend): cast panel hides player seating in offscreen scenes`

---

### Task 11: SceneInspector offscreen badge

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx`
- Test: `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Produces: `SceneInspector` accepts optional `pcless?: boolean` and renders an "Offscreen scene" side-section when set.

- [ ] **Step 1: Failing test** (follow the file's existing render helper/mocks):

```tsx
test("offscreen scene shows the offscreen side-section", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByText("Offscreen scene");
  expect(screen.getByText(/no player character/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — add `pcless?: boolean` to the props type and destructure it; render as the first side-section inside `<aside className="inspector">`:

```tsx
      {pcless && (
        <div className="side-section">
          <h4>Offscreen scene</h4>
          <div className="field-hint">No player character — you direct the NPCs.</div>
        </div>
      )}
```

- [ ] **Step 4: Run file tests + full suite + tsc — expect PASS.**

- [ ] **Step 5: Commit** — `feat(frontend): inspector badges offscreen scenes`

---

### Task 12: CampaignView director composer

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css` (transient-note style)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `SceneMeta.pcless` (Task 8), `CastPanel.pcless` (Task 10), `SceneInspector.pcless` (Task 11), `api.chat` accepting `""`.
- Produces: pcless scenes get the director composer ("Direct the scene (optional)…", **Continue ▶** on empty input), a transient 🎬 note while streaming, an "Offscreen" chip by the title, an "Offscreen" subtitle on the rail row, and `pcless` passed to CastPanel/SceneInspector.

- [ ] **Step 1: Failing tests** (in `CampaignView.test.tsx`):

```tsx
const OFFSCREEN_SCENE = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", pcless: true }];

test("offscreen scene: director composer, Continue button, title chip", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  await screen.findByPlaceholderText(/direct the scene/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  // one "Offscreen" chip by the title + one subtitle on the rail row
  expect(screen.getAllByText("Offscreen")).toHaveLength(2);
});

test("offscreen scene: empty Continue sends an empty note", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function)));
});

test("offscreen scene: typed note shows transiently, never lands in messages", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  let release: () => void = () => {};
  (api.chat as any).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the guard grows suspicious" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/🎬 the guard grows suspicious/);
  release();
  await waitFor(() => expect(screen.queryByText(/🎬/)).toBeNull());
});

test("normal scene keeps the plain composer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByPlaceholderText(/speak your intent/i);
  expect(screen.queryByRole("button", { name: /continue/i })).toBeNull();
});
```

- [ ] **Step 2: Run — expect FAIL** (placeholder not found).

- [ ] **Step 3: Implement** in `CampaignView.tsx`.

State + memo (near `playerName`):

```tsx
  const [directorNote, setDirectorNote] = useState<string | null>(null);
  // offscreen scenes take director notes instead of PC dialogue
  const activePcless = useMemo(
    () => scenes.find((s) => s.id === activeId)?.pcless ?? false,
    [scenes, activeId]);
```

Replace `send()`:

```tsx
  async function send() {
    if (busy) return;
    const content = input.trim();
    if (!content && !activePcless) return;
    let id = activeId;
    if (!id) {
      if (!content) return;
      id = (await api.createScene(cid)).id;
      setScenes(await api.listScenes(cid));
      setActiveId(id);
    }
    setInput("");
    if (activePcless) {
      // the note steers one generation and is never stored — show it transiently
      setDirectorNote(content || null);
      try {
        await runStream(id, (onEvent) => api.chat(cid, id!, content, onEvent));
      } finally {
        setDirectorNote(null);
      }
      return;
    }
    setMessages((m) => [...m, { role: "user", content }]);
    await runStream(id, (onEvent) => api.chat(cid, id!, content, onEvent));
  }
```

Title chip:

```tsx
        {activeId && (
          <h2 className="scene-title">
            {scenes.find((s) => s.id === activeId)?.title ?? ""}
            {activePcless && <span className="chip on offscreen-badge">Offscreen</span>}
          </h2>
        )}
```

Transient note — inside the `.stream` div, immediately before the `{streaming && (` block:

```tsx
          {directorNote && busy && (
            <div className="run director-note">
              <div className="msg assistant">
                <span className="msg-gutter" />
                <div className="msg-body">🎬 {directorNote}</div>
              </div>
            </div>
          )}
```

Composer:

```tsx
          <textarea
            rows={3}
            placeholder={activePcless ? "Direct the scene (optional)…" : "Speak your intent…"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : activePcless && !input.trim() ? "Continue ▶" : "Send ▸"}
          </button>
```

Rail badge — the scene rail's `EditableRow` gains a subtitle (the component already supports it):

```tsx
            <EditableRow
              key={s.id}
              label={s.title}
              prefix={String(scenes.length - i).padStart(2, "0")}
              subtitle={s.pcless ? "Offscreen" : undefined}
              active={s.id === activeId}
              onSelect={() => selectScene(s.id)}
              onRename={(title) => renameScene(s.id, title)}
              onDelete={() => deleteScene(s)}
            />
```

Prop wiring: `<CastPanel ... pcless={activePcless} />` and `<SceneInspector ... pcless={activePcless} />`.

`frontend/src/index.css` — append:

```css
.director-note .msg-body { opacity: 0.65; font-style: italic; }
.offscreen-badge { margin-left: 0.5em; }
```

- [ ] **Step 4: Run file tests + full suite + tsc — expect PASS.**

- [ ] **Step 5: Commit** — `feat(frontend): director composer for offscreen scenes`

---

### Task 13: GreetingEditor offscreen toggle

**Files:**
- Modify: `frontend/src/components/GreetingEditor.tsx` (`BLANK` ~line 8, `select` ~line 60, `save` ~line 78, view sidebar ~line 219, form ~line 298)
- Test: `frontend/src/components/GreetingEditor.test.tsx`

**Interfaces:**
- Consumes: `Greeting.pcless` / `GreetingDraft.pcless` (Task 8), backend greeting `pcless` (Task 5).
- Produces: an "Offscreen (no PC)" chip toggle in the form; an "Offscreen" side-section chip in the read-only view; create/update send `pcless`.

- [ ] **Step 1: Failing tests** (follow the file's existing mocks; the read-greeting mock's `meta` gains `pcless: true` for the view test):

```tsx
test("view shows the Offscreen chip for a pcless greeting", async () => {
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Cabal", character: "vex", version: "v1", present: [],
            requires_tags: [], predecessor_join: "all", pcless: true },
    body: "The cult meets.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  // render the editor and click the greeting row per this file's existing pattern
  ...
  await screen.findByText("NPC-only opener");
});

test("the form's Offscreen toggle is sent on save", async () => {
  // open the existing greeting, click Edit, toggle, save — per this file's pattern
  ...
  fireEvent.click(screen.getByRole("button", { name: /offscreen \(no pc\)/i }));
  fireEvent.click(screen.getByRole("button", { name: /save greeting/i }));
  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalledWith(
    expect.anything(), "g1", expect.objectContaining({ pcless: true })));
});
```

(The `...` lines are this file's existing render/select boilerplate — copy the setup used by its current view/edit tests verbatim; only the assertions above are new.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```tsx
const BLANK = { name: "", character: "", version: "", body: "", present: [] as string[],
                requires_tags: [] as string[], predecessor_join: "all" as "all" | "any",
                pcless: false };
```

`select()` — add `pcless: g.meta.pcless ?? false,` to the `setForm` object.

`save()` — the update call gains `pcless: form.pcless` (create already spreads `{ ...form }`):

```tsx
        await api.updateGreeting(scope, id, {
          name: form.name, body: form.body, present: form.present,
          requires_tags: form.requires_tags, predecessor_join: form.predecessor_join,
          pcless: form.pcless,
        });
```

Form — after the "Greeting text" Field:

```tsx
          <Field label="Offscreen"
                 hint="an NPC-only opener — no player character; {{user}} becomes your PC's name">
            <div className="chips">
              <button className={"chip" + (form.pcless ? " on" : "")}
                      onClick={() => setForm({ ...form, pcless: !form.pcless })}>
                Offscreen (no PC)
              </button>
            </div>
          </Field>
```

View sidebar — after the Edit button's `.form-actions`:

```tsx
              {form.pcless && (
                <div className="side-section">
                  <h4>Offscreen</h4>
                  <span className="chip on">NPC-only opener</span>
                </div>
              )}
```

- [ ] **Step 4: Run file tests + full suite + tsc — expect PASS.**

- [ ] **Step 5: Commit** — `feat(frontend): greeting editor authors offscreen greetings`

---

### Task 14: Full verification

- [ ] **Step 1:** `backend/.venv/Scripts/python.exe -m pytest backend -q` — all green.
- [ ] **Step 2:** From `frontend/`: `npx vitest run` and `npx tsc -b` — all green.
- [ ] **Step 3:** Run the `verify` skill: drive the real app — create an offscreen scene from the chooser, seat NPCs, Continue twice, confirm the transcript stays assistant-only and the context inspector shows the Offscreen section.
- [ ] **Step 4:** Commit any fixes; the branch is ready for `superpowers:finishing-a-development-branch`.
