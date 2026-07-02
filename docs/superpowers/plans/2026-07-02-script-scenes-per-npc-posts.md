# Script Scenes + Per-NPC Posts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scene logs become a script (`**<Speaker>:** …` per message, role derived from cast, not stored); one LLM call per turn returns a script that is split into per-speaker posts (#744).

**Architecture:** `store/scenes.py` parses/serializes arbitrary speaker labels; `store/appearances.py` resolves player names so role can be derived (speaker ∈ players → user). Routes stamp the sole PC's name on user turns and split model replies into per-speaker messages. `store/context.py` re-embeds speaker labels when projecting the script into API conversation roles and instructs the model to answer in script format. The frontend renders the stored speaker with a render-time fallback to the sole player's name.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-02-script-scenes-per-npc-posts-design.md`

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (targeted: append `backend/tests/<file>.py -q`).
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend`).
- Marker grammar: `**<label>:**` at start of body or after a blank line; label 1–64 chars, no `*`, no newline. Reserved labels: `You` (user), `Grimoire` (assistant). Legacy parens sub-speaker (`**Grimoire (X):**`) recognized on read for reserved labels only, never written again.
- Exactly one LLM call per turn — splitting happens on the stored/streamed text, never via extra calls.
- Commit after each task on branch `script-scenes`.

---

### Task 1: appearances — actor names (`player_names`, `scene_cast` gains `name`)

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (`scene_cast` at ~line 167; add helpers near `players_in_scene` ~line 176)
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Produces: `appearances.player_names(cid: str, scene_id: str) -> list[str]` — display names of the scene's `role=player` cast, resolved from campaign copies; unresolvable actors skipped.
- Produces: `appearances.scene_cast(...)` entries gain `"name": str` (falls back to the actor id).

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_appearances_store.py` (match its existing imports; add any of `worlds, campaigns, scenes, pcs, characters, appearances` that are missing):

```python
def test_player_names_and_scene_cast_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(wroot, "Elara Vane", [])
    char_id, cvid = characters.create_character(wroot, "Seraphine Vale")
    appearances.appear(cid, sid, "pcs", pid, pvid, "player")
    appearances.appear(cid, sid, "characters", char_id, cvid, "npc")
    assert appearances.player_names(cid, sid) == ["Elara Vane"]
    cast = appearances.scene_cast(cid, sid)
    assert {a["id"]: a["name"] for a in cast} == {pid: "Elara Vane", char_id: "Seraphine Vale"}


def test_player_names_empty_when_no_players(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    assert appearances.player_names(cid, sid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'player_names'`

- [ ] **Step 3: Implement** — in `backend/src/grimoire/store/appearances.py`, add after `locked_version` (or near `players_in_scene`):

```python
def _actor_name(croot: Path, kind: str, actor_id: str, vid: str | None) -> str | None:
    """Display name from the campaign copy at the locked version; None if unreadable."""
    try:
        if kind == "pcs":
            return pcs.read_persona(croot, actor_id, vid).get("name") or actor_id
        return characters.read_card(croot, actor_id, vid)["data"].get("name") or actor_id
    except (pcs.PCNotFound, pcs.PCVersionNotFound,
            characters.CharacterNotFound, characters.VersionNotFound):
        return None


def player_names(cid: str, scene_id: str) -> list[str]:
    """Display names of the scene's role=player cast (PCs or characters cast as players)."""
    croot = campaigns.campaign_root(cid)
    out = []
    for a in players_in_scene(cid, scene_id):
        name = _actor_name(croot, a["kind"], a["id"], a["version"])
        if name:
            out.append(name)
    return out
```

and change `scene_cast` to include names:

```python
def scene_cast(cid: str, scene_id: str) -> list[dict]:
    croot = campaigns.campaign_root(cid)
    out = []
    for ref, r in record(cid).items():
        if scene_id in r["scenes"]:
            kind, actor_id = _split(ref)
            out.append({"kind": kind, "id": actor_id, "role": r["role"],
                        "name": _actor_name(croot, kind, actor_id, r["version"]) or actor_id})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))
```

(`characters.VersionNotFound` exists — same exceptions `context.py` already catches.)

- [ ] **Step 4: Run the file's tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (extra `name` key is additive; no caller does exact-dict equality on `scene_cast` — if one does, update it to include `name`).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(appearances): player_names helper; scene_cast entries carry display names"`

---

### Task 2: scenes — script marker grammar + derived roles

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py:13-21` (labels/regex), `:109-127` (`_parse_messages`, `read_scene`)
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `appearances.player_names(cid, sid)` (Task 1), imported **lazily inside `read_scene`** (mirrors `appearances.suggestions` lazily importing `scenes` — avoids a cycle).
- Produces: `read_scene` messages are `{"role": "user"|"assistant", "content": str, "speaker"?: str}` where role is **derived**: speaker `You`/stamped-player → user; everything else assistant. `ROLE_TO_LABEL` kept (chronicle imports it); `LABEL_TO_ROLE` deleted.
- Produces (module-internal, reused in Task 3): `_markers(body) -> list[re.Match]`, `_speaker_and_role(m, players) -> tuple[str | None, str]`, `_parse_messages(body, players)`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_scene_store.py` (extend the imports line to `from grimoire.store import appearances, campaigns, pcs, scenes, worlds`):

```python
def _campaign_with_pc(monkeypatch, tmp_path, pc_name="Elara Vane"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(worlds.world_root(wid), pc_name, [])
    appearances.appear(cid, sid, "pcs", pid, pvid, "player")
    return cid, sid


def test_script_labels_store_and_derive_roles(monkeypatch, tmp_path):
    cid, sid = _campaign_with_pc(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "I draw my blade.", speaker="Elara Vane")
    scenes.append_message(cid, sid, "assistant", '"You dare?"', speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "The hall falls silent.")
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**Elara Vane:** I draw my blade." in raw
    assert "**Seraphine Vale:**" in raw and "(Seraphine Vale)" not in raw
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", "Elara Vane"),
        ("assistant", "Seraphine Vale"),
        ("assistant", None),
    ]


def test_legacy_labels_and_parens_still_parse(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    p = campaigns.campaign_root(cid) / "scenes" / f"{sid}.md"
    meta_text = p.read_text(encoding="utf-8").split("---")[1]
    p.write_text("---" + meta_text + "---\n\n"
                 "**You:** hello\n\n"
                 "**Grimoire (Seraphine Vale):** she nods\n\n"
                 "**Grimoire:** rain falls\n", encoding="utf-8")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", None),
        ("assistant", "Seraphine Vale"),
        ("assistant", None),
    ]


def test_marker_requires_blank_line(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "line one\n**Aside:** same message")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "line one\n**Aside:** same message"


def test_unsafe_speaker_falls_back_to_reserved_label(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi", speaker="x" * 65)
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**You:** hi" in raw
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: FAIL — `**Elara Vane:**` not written (serializer still emits `**You (Elara Vane):**`), arbitrary labels unparsed.

- [ ] **Step 3: Implement** — in `backend/src/grimoire/store/scenes.py`, replace lines 13–21 with:

```python
# The body is a script: every message is `**<Speaker>:** content`. Role is not
# stored — a message is user-side iff its speaker is "You" or a role=player
# cast member's name (derived in read_scene). Reserved labels keep legacy
# files working; their parens sub-speaker form is read but never written.
RESERVED_LABELS = {"You": "user", "Grimoire": "assistant"}
ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
_SAFE_LABEL = re.compile(r"^[^*\n]{1,64}$")


def _label(role: str, speaker: str | None) -> str:
    if speaker and _SAFE_LABEL.match(speaker) and speaker not in RESERVED_LABELS:
        return speaker
    return ROLE_TO_LABEL[role]


def _markers(body: str) -> list[re.Match]:
    """Marker matches that actually start a message: at the top of the body or
    after a blank line (the serializer always writes blank lines between
    messages; this keeps bold-label lines inside a paragraph as content)."""
    return [m for m in _MARKER.finditer(body)
            if m.start() == 0 or body[max(0, m.start() - 2):m.start()] == "\n\n"]


def _speaker_and_role(m: re.Match, players: frozenset[str]) -> tuple[str | None, str]:
    base, sub = m.group(1), m.group(2)
    if base in RESERVED_LABELS:
        return sub, RESERVED_LABELS[base]
    speaker = f"{base} ({sub})" if sub else base
    return speaker, "user" if speaker in players else "assistant"
```

Replace `_parse_messages` and `read_scene` (lines 109–127) with:

```python
def _parse_messages(body: str, players: frozenset[str]) -> list[dict]:
    matches = _markers(body)
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        speaker, role = _speaker_and_role(m, players)
        msg = {"role": role, "content": body[start:end].strip()}
        if speaker:
            msg["speaker"] = speaker
        messages.append(msg)
    return messages


def read_scene(cid: str, sid: str) -> dict:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    from . import appearances  # lazy: appearances lazily imports scenes too
    players = frozenset(appearances.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": _parse_messages(body, players)}
```

Delete the old `LABEL_TO_ROLE` constant.

- [ ] **Step 4: Run the file's tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. (`test_append_and_parse_roundtrip`'s `**Not a real marker**` content has no `:**`, so it still doesn't split.)

- [ ] **Step 5: Commit** — `git commit -am "feat(scenes): script marker grammar; conversation role derived from cast"`

---

### Task 3: scenes — `stamp_user_speaker`, `split_reply`, `remove_trailing_assistant_run`

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (add after `_serialize_messages`; keep `remove_last_message` for now — routes still call it until Task 4)
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `_markers`, `_speaker_and_role`, `_serialize_messages`, `read_scene` (Task 2).
- Produces: `scenes.stamp_user_speaker(cid, sid, name) -> None` (backfills every speakerless user message; raises `SceneNotFound`); `scenes.split_reply(text, players: frozenset[str]) -> list[dict]` (`{"speaker": str | None, "content": str}` segments); `scenes.remove_trailing_assistant_run(cid, sid) -> None` (raises `IndexError` when the last message isn't assistant-side).

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_scene_store.py`:

```python
def test_stamp_user_speaker_backfills_only_bare_user_lines(monkeypatch, tmp_path):
    cid, sid = _campaign_with_pc(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "first, before the PC joined")
    scenes.append_message(cid, sid, "assistant", "noted")
    scenes.stamp_user_speaker(cid, sid, "Elara Vane")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", "Elara Vane"), ("assistant", None)]
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**Elara Vane:** first, before the PC joined" in raw


def test_split_reply_segments_and_guards():
    players = frozenset({"Elara Vane"})
    text = ("The rain hammers the roof.\n\n"
            '**Seraphine Vale:** "You dare?"\n\n'
            "**Grimoire:** Thunder rolls.\n\n"
            "**Elara Vane:** I would never—")
    assert scenes.split_reply(text, players) == [
        {"speaker": None, "content": "The rain hammers the roof."},
        {"speaker": "Seraphine Vale", "content": '"You dare?"'},
        {"speaker": None, "content": "Thunder rolls."},
        # a player-named block is never stored as the player: reassigned to the narrator
        {"speaker": None, "content": "I would never—"},
    ]


def test_split_reply_without_markers_is_one_narrator_post():
    assert scenes.split_reply("Just prose.", frozenset()) == [
        {"speaker": None, "content": "Just prose."}]
    assert scenes.split_reply("   ", frozenset()) == []


def test_remove_trailing_assistant_run(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    scenes.append_message(cid, sid, "assistant", "one", speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "two")
    scenes.remove_trailing_assistant_run(cid, sid)
    assert scenes.read_scene(cid, sid)["messages"] == [{"role": "user", "content": "hi"}]
    with pytest.raises(IndexError):
        scenes.remove_trailing_assistant_run(cid, sid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: FAIL — the three functions don't exist.

- [ ] **Step 3: Implement** — add to `backend/src/grimoire/store/scenes.py` after `_serialize_messages`:

```python
def stamp_user_speaker(cid: str, sid: str, name: str) -> None:
    """Backfill: give every speakerless user message the (sole) player's name."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    messages = read_scene(cid, sid)["messages"]
    stamped = False
    for m in messages:
        if m["role"] == "user" and not m.get("speaker"):
            m["speaker"] = name
            stamped = True
    if not stamped:
        return
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    p.write_text(dump_frontmatter(meta, _serialize_messages(messages)), encoding="utf-8")


def split_reply(text: str, players: frozenset[str]) -> list[dict]:
    """Split one model reply into per-speaker segments on the marker grammar.
    Unlabeled leading text, reserved labels, and player-named blocks (never
    store a forged player line) all go to the narrator (speaker None)."""
    text = text.strip()
    matches = _markers(text)
    segments: list[dict] = []

    def add(speaker: str | None, content: str) -> None:
        if content.strip():
            segments.append({"speaker": speaker, "content": content.strip()})

    add(None, text[:matches[0].start()] if matches else text)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        speaker, role = _speaker_and_role(m, players)
        add(None if role == "user" else speaker, text[m.end():end])
    return segments


def remove_trailing_assistant_run(cid: str, sid: str) -> None:
    """Drop the trailing run of assistant-side messages (one turn's output)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    messages = read_scene(cid, sid)["messages"]
    if not messages or messages[-1]["role"] != "assistant":
        raise IndexError("no trailing assistant reply")
    while messages and messages[-1]["role"] == "assistant":
        messages.pop()
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, _serialize_messages(messages)), encoding="utf-8")
```

- [ ] **Step 4: Run the full backend suite** — `backend/.venv/Scripts/python.exe -m pytest backend -q` — Expected: PASS

- [ ] **Step 5: Commit** — `git commit -am "feat(scenes): speaker backfill, reply splitting, trailing-run removal"`

---

### Task 4: routes — stamp user turns, split replies, run-aware regenerate

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`_chat_stream` ~1025, `post_chat` ~1151, `post_regenerate` ~1172, `post_first_post` ~1481)
- Modify: `backend/src/grimoire/store/scenes.py` (delete `remove_last_message`, now unused)
- Test: `backend/tests/test_routes.py`, `backend/tests/test_scene_store.py` (replace `remove_last_message` tests)

**Interfaces:**
- Consumes: `appearances.player_names`, `scenes.stamp_user_speaker`, `scenes.split_reply`, `scenes.remove_trailing_assistant_run`.
- Produces: `routes._persist_reply(cid, sid, text) -> None` (split + append per segment) used by `_chat_stream` (both success and partial-error paths) and `post_first_post`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_routes.py`:

```python
def _empty_scene(client, cid):
    return client.post(f"/api/campaigns/{cid}/scenes", json={}).json()["id"]


def _cast_pc(client, wid, cid, sid, name="Elara Vane"):
    pid = client.post(f"/api/worlds/{wid}/pcs", json={"name": name}).json()["pc"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                    json={"kind": "pcs", "id": pid, "role": "player"})
    assert r.status_code == 200
    return pid


def test_chat_with_sole_pc_stamps_speaker_and_backfills(client):
    wid, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "sent before the PC joined"}) as r:
        r.read()
    _cast_pc(client, wid, cid, sid)
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "I draw my blade."}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    assert len(users) == 2 and all(m["speaker"] == "Elara Vane" for m in users)


def test_chat_without_pc_stays_unstamped(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hello"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[0] == {"role": "user", "content": "hello"}


def test_reply_is_split_into_per_speaker_posts(client):
    wid, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    _cast_pc(client, wid, cid, sid)
    reply = ('**Seraphine Vale:** "You dare?"\n\n'
             "**Grimoire:** Thunder rolls.\n\n"
             "**Elara Vane:** forged player line")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouter([reply])
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "hi"}) as r:
        r.read()
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[1:] == [
        {"role": "assistant", "content": '"You dare?"', "speaker": "Seraphine Vale"},
        {"role": "assistant", "content": "Thunder rolls."},
        {"role": "assistant", "content": "forged player line"},
    ]


def test_regenerate_drops_the_whole_trailing_run(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "one", speaker="Seraphine Vale")
    store.scenes.append_message(cid, sid, "assistant", "two")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Hello"}]


def test_regenerate_multi_post_opening_returns_400(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    client.put("/api/config", json={"openrouter_key": "k"})
    store.scenes.append_message(cid, sid, "assistant", "opener part one")
    store.scenes.append_message(cid, sid, "assistant", "opener part two", speaker="Seraphine Vale")
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400


def test_first_post_splits_speakers(client):
    _, cid = _campaign(client)
    sid = _empty_scene(client, cid)
    text = "Mist rolls in.\n\n**Seraphine Vale:** Who goes there?"
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": text}).status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [
        {"role": "assistant", "content": "Mist rolls in."},
        {"role": "assistant", "content": "Who goes there?", "speaker": "Seraphine Vale"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: the new tests FAIL (no stamping, reply stored as one message, regenerate drops one message only).

- [ ] **Step 3: Implement** — in `backend/src/grimoire/routes.py`:

Add above `_chat_stream`:

```python
def _persist_reply(cid: str, sid: str, text: str) -> None:
    """Split one model reply into per-speaker posts and append them (#744)."""
    players = frozenset(store.appearances.player_names(cid, sid))
    for seg in store.scenes.split_reply(text, players):
        store.scenes.append_message(cid, sid, "assistant", seg["content"], speaker=seg["speaker"])
```

In `_chat_stream`, replace both `store.scenes.append_message(cid, sid, "assistant", "".join(parts))` calls with `_persist_reply(cid, sid, "".join(parts))`.

Replace the body of `post_chat` (after `_require_key(cfg)`):

```python
    names = store.appearances.player_names(cid, sid)
    speaker = names[0] if len(names) == 1 else None
    if speaker:
        store.scenes.stamp_user_speaker(cid, sid, speaker)
    store.scenes.append_message(cid, sid, "user", turn.content, speaker=speaker)
    messages = store.context.build_messages(cid, sid)
    return _chat_stream(cid, sid, messages, cfg, client)
```

In `post_regenerate`, replace

```python
    if msgs[-1]["role"] == "assistant":
        if len(msgs) == 1:
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        store.scenes.remove_last_message(cid, sid)
```

with

```python
    if msgs[-1]["role"] == "assistant":
        if all(m["role"] == "assistant" for m in msgs):
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        store.scenes.remove_trailing_assistant_run(cid, sid)
```

In `post_first_post` (~line 1488), replace `store.scenes.append_message(cid, sid, "assistant", body.text.strip())` with `_persist_reply(cid, sid, body.text)`.

Delete `remove_last_message` from `backend/src/grimoire/store/scenes.py` and delete `test_remove_last_message` / `test_remove_last_message_missing_scene_raises` from `backend/tests/test_scene_store.py` (superseded by `test_remove_trailing_assistant_run`).

- [ ] **Step 4: Run the full backend suite** — `backend/.venv/Scripts/python.exe -m pytest backend -q` — Expected: PASS (`test_regenerate_replaces_the_last_assistant_post` and `test_regenerate_sole_opening_post_returns_400` keep passing under the run-aware logic).

- [ ] **Step 5: Commit** — `git commit -am "feat(routes): stamp PC speaker on user turns; split replies into per-speaker posts (#744)"`

---

### Task 5: context + chronicle — script projection and Response format section

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (`_assemble` lines 298–299, 377–386)
- Modify: `backend/src/grimoire/store/chronicle.py:98-101` (`transcript_text`)
- Test: `backend/tests/test_context.py`, `backend/tests/test_chronicle_store.py`

**Interfaces:**
- Consumes: messages with derived roles + speakers (Task 2).
- Produces: `build_messages` history where every assistant message content starts with `**<Speaker or Grimoire>:** `, stamped user messages with `**<PC name>:** `, consecutive same-role messages merged with `\n\n`; a final system section labeled `Response format`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_context.py` (reuse that file's existing setup helpers/imports; the code below shows the store calls needed):

```python
def test_history_projection_labels_and_merges(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(worlds.world_root(wid), "Elara Vane", [])
    appearances.appear(cid, sid, "pcs", pid, pvid, "player")
    scenes.append_message(cid, sid, "user", "I enter.", speaker="Elara Vane")
    scenes.append_message(cid, sid, "assistant", '"You dare?"', speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "Thunder rolls.")
    hist = [m for m in context.build_messages(cid, sid) if m["role"] != "system"]
    assert hist == [
        {"role": "user", "content": "**Elara Vane:** I enter."},
        {"role": "assistant",
         "content": '**Seraphine Vale:** "You dare?"\n\n**Grimoire:** Thunder rolls.'},
    ]


def test_unstamped_user_lines_stay_bare(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "plain message")
    hist = [m for m in context.build_messages(cid, sid) if m["role"] != "system"]
    assert hist == [{"role": "user", "content": "plain message"}]


def test_response_format_section_lists_players(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(worlds.world_root(wid), "Elara Vane", [])
    appearances.appear(cid, sid, "pcs", pid, pvid, "player")
    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Write your reply as a script" in sections["Response format"]
    assert "Elara Vane" in sections["Response format"]
```

And append to `backend/tests/test_chronicle_store.py`:

```python
def test_transcript_text_prefers_speakers():
    text = chronicle.transcript_text([
        {"role": "user", "content": "hi", "speaker": "Elara Vane"},
        {"role": "assistant", "content": "yo"}])
    assert "**Elara Vane:** hi" in text and "**Grimoire:** yo" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py backend/tests/test_chronicle_store.py -q`
Expected: FAIL — no labels in history, no `Response format` section.

- [ ] **Step 3: Implement** — in `backend/src/grimoire/store/context.py`:

Add module-level helpers (near `_pc_persona_block`):

```python
def _script_line(m: dict) -> str:
    """A message as a script line. Assistant lines always carry their speaker
    label (Grimoire when unnamed) so attribution survives the round trip;
    user lines only when stamped (legacy bare lines stay bare)."""
    if m["role"] == "assistant":
        return f"**{m.get('speaker') or 'Grimoire'}:** {m['content']}"
    if m.get("speaker"):
        return f"**{m['speaker']}:** {m['content']}"
    return m["content"]


def _project_history(messages: list[dict]) -> list[dict]:
    """Script -> conversation roles; merge consecutive same-role messages so
    providers that expect strict alternation are happy."""
    out: list[dict] = []
    for m in messages:
        line = _script_line(m)
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    return out
```

In `_assemble`, change line 299 to keep speakers:

```python
    history = [dict(m) for m in scene["messages"]]
```

After the `add("Off-scene cast", ...)` line, add the format section:

```python
    fmt = ("Write your reply as a script. Each character who acts or speaks gets "
           "their own block starting with **<Name>:** on its own line, e.g. "
           "**Seraphine Vale:**. Use **Grimoire:** for narration, scene "
           "description, and any voice that isn't a named character.")
    if player_names:
        fmt += " Never write dialogue or actions for: " + ", ".join(player_names) + "."
    add("Response format", fmt)
```

Change the last line of `_assemble`:

```python
    sub_history = [{"role": m["role"], "content": _substitute(m["content"], subs)}
                   for m in _project_history(history)]
```

In `backend/src/grimoire/store/chronicle.py`, replace `transcript_text`:

```python
def transcript_text(messages: list[dict]) -> str:
    from .scenes import ROLE_TO_LABEL
    return "\n\n".join(
        f"**{m.get('speaker') or ROLE_TO_LABEL.get(m['role'], m['role'])}:** {m['content']}"
        for m in messages)
```

- [ ] **Step 4: Run the full backend suite and update the two known casualties**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`

The always-on `Response format` section breaks two assertions in `backend/tests/test_context.py`; update them:
- Line ~199 (`build_messages(cid, sid) == [{"role": "user", "content": "plain message"}]`): the empty-store scene now has one system message — change the assertion to filter it out or assert the system message contains `"Write your reply as a script"` plus the bare user message.
- Line ~292 (`[m for m in build_messages(...) if m["role"] == "system"] == []`): now exactly one system section — assert `"Response format"`/script text is the only system content instead.

If any other test asserts unlabeled assistant history content, prepend `**Grimoire:** ` to its expectation.
Expected after updates: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(context): script-format prompt section; history carries speakers, merges runs"`

---

### Task 6: frontend — speaker spines, player-name fallback, re-fetch after stream

**Files:**
- Modify: `frontend/src/api/client.ts:133` (`Actor` type)
- Modify: `frontend/src/routes/CampaignView.tsx` (state ~42, `selectScene` ~69, `runStream` ~110, `send`/`retry`/`reroll`, spine ~342, reroll condition ~345)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `GET …/cast` entries now include `name` (Task 1); messages carry `speaker` and derived `role`.
- Produces: spine label rule `m.speaker ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant)`.

- [ ] **Step 1: Update existing tests + write failing tests** in `frontend/src/routes/CampaignView.test.tsx`:

The stream now ends with a scene re-fetch, so two existing tests need their `getScene` mocks sequenced:

- **"an error shows a Retry button that retries the scene"**: before `renderCampaign()`, replace the default `getScene` mock with
  ```tsx
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hello" }] });
  ```
  (the server persisted the user turn even though the stream errored — the re-fetch returns it; the final `getAllByText("hello")` assertion still holds).
- **"Reroll on the last assistant post replaces it with a fresh reply"**: replace its `getScene` mock with
  ```tsx
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] });
  ```

New tests:

```tsx
test("an unstamped user line renders the sole player's name", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" },
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "I open the door." },
    { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
  ] });
  renderCampaign();
  await screen.findByText("Elara Vane");
  expect(screen.getByText("Seraphine Vale")).toBeInTheDocument();
});

test("a stored speaker beats the player-name fallback", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "spoken as someone else", speaker: "Old Name" }] });
  renderCampaign();
  await screen.findByText("Old Name");
  expect(screen.queryByText("Elara Vane")).toBeNull();
});

test("after a stream completes the scene is re-fetched", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hello" },
      { role: "assistant", content: "Thunder rolls." },
      { role: "assistant", content: "Who goes there?", speaker: "Seraphine Vale" },
    ] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ delta: "**Grimoire:** Thunder rolls." });
  });
  renderCampaign();
  await screen.findByText(/01 · Old/);
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await screen.findByText("Who goes there?");
  expect(api.getScene).toHaveBeenCalledTimes(2);
});

test("no Reroll when every message is assistant-side (multi-post opener)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "opener one" },
    { role: "assistant", content: "opener two", speaker: "Seraphine Vale" }] });
  renderCampaign();
  await screen.findByText("opener two");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify the new tests fail** — from `frontend/`: `npx vitest run src/routes/CampaignView.test.tsx` — Expected: the four new tests FAIL (no fallback, one `getScene` call, reroll shown on all-assistant scenes).

- [ ] **Step 3: Implement** — in `frontend/src/api/client.ts` line 133:

```ts
export type Actor = { kind: "characters" | "pcs"; id: string; role: "player" | "npc"; name: string };
```

In `frontend/src/routes/CampaignView.tsx`:

Add state next to `labels`:

```tsx
const [playerName, setPlayerName] = useState<string | null>(null);
```

In `selectScene`, after `setActiveId(id)`:

```tsx
    api.getCast(cid, id).then((cast) => {
      const players = cast.filter((a) => a.role === "player");
      setPlayerName(players.length === 1 ? players[0].name : null);
    }).catch(() => setPlayerName(null));
```

Rework `runStream` to take the scene id and re-fetch on completion (persisted split posts replace the local bubble; also covers partial-error persistence):

```tsx
  async function runStream(id: string, start: (onEvent: (e: ChatEvent) => void) => Promise<void>) {
    setBusy(true);
    setError(null);
    let acc = "";
    try {
      await start((e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
      await selectScene(id);
    }
  }
```

(`selectScene` already bumps `ctxKey`, so drop the old `setCtxKey` line and the `if (acc) setMessages(...)` append.) Update callers: `send()` → `runStream(id, (onEvent) => api.chat(cid, id!, content, onEvent))`; `retry()` → `runStream(activeId, (onEvent) => api.retry(cid, activeId, onEvent))`; `reroll()` similarly.

In `reroll()`, replace `setMessages((m) => m.slice(0, -1))` with a whole-run slice:

```tsx
    setMessages((m) => {
      let end = m.length;
      while (end > 0 && m[end - 1].role === "assistant") end--;
      return m.slice(0, end);
    });
```

Above the JSX `return`, compute the reroll condition (a trailing run that reaches index 0 is the opener — not rerollable):

```tsx
  const canReroll = messages.length > 0 &&
    messages[messages.length - 1].role === "assistant" &&
    messages.some((x) => x.role === "user");
```

In the message map, replace the spine label and both reroll conditions:

```tsx
<span className="spine">
  {m.speaker ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant)}
</span>
```

and change `m.role === "assistant" && i === messages.length - 1 && i > 0` (both the ↻ button and the popover render condition) to `i === messages.length - 1 && canReroll`.

- [ ] **Step 4: Run the frontend suite and typecheck** — from `frontend/`: `npx vitest run && npx tsc -b` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(transcript): PC-named spines with player fallback; re-fetch split posts after stream"`

---

### Task 7: full verification

- [ ] **Step 1:** `backend/.venv/Scripts/python.exe -m pytest backend -q` — Expected: all pass.
- [ ] **Step 2:** from `frontend/`: `npx vitest run && npx tsc -b` — Expected: all pass, no type errors.
- [ ] **Step 3:** Fix any stragglers (most likely: tests elsewhere asserting exact `scene_cast` dicts or unlabeled assistant history). Commit fixes: `git commit -am "test: align remaining assertions with script scenes"`.
