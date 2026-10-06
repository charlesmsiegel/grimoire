# Play controls VI — quick replies: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Composer buttons that send canned text, a director note, a saved roll, an auxiliary task or open the opener generator, from layered world + campaign sets edited with the list/detail pattern.

**Architecture:** Backend: a new `store/quick_replies.py` owns validation, lenient per-entry-salvaging reads, digest-guarded strict writes and the world→campaign layering; `routes/quick_replies.py` exposes the two sets and the effective set. Frontend: pure helpers (`components/quickReplies.ts`) decide each button's availability and the editor's set edits; `QuickReplyStrip` renders inside the composer; `CampaignView` gets a parameterised `sendText`, a parameterised `doRoll`, task runners sharing a busy flag with `SceneInspector`, and a controlled `CastPanel` opener; `QuickReplyEditor` is mounted in the world view's Writing group and the campaign hub's Settings column.

**Tech Stack:** FastAPI + pytest (`backend/`), React + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-quick-replies-design.md` (its "Gate resolutions" section is binding and overrides the earlier text).

## Global Constraints

- A quick reply: `{"id", "label", "kind", ...kind fields}`. Kinds `send` / `direct` (`text`, `mode`), `roll` (`notation`, `roll_label?`), `task` (`task`), `opener` (no fields). `mode` ∈ `send` | `insert`. `task` ∈ `rolling_summary` | `scene_break` | `next_scene`.
- `label` up to **40** characters (non-blank); `text` up to **2000** and must contain a non-space character; `notation` validated by `store.dice.parse` when saved (400 on a bad string). `roll_label` (plan ruling) collapsed to single spaces like the roll route does, up to **80**.
- Ids minted by the server (**uuid4 hex**) for any entry saved without one; ids unique within a set.
- Set file `{"version": 1, "replies": [...]}`, at most **50** entries; world `<world>/quick_replies.json`, campaign `<campaign>/quick_replies.json`.
- `plugin` is reserved: a PUT carrying it is refused **400 `plugin_api_unavailable`**. Any other rule violation is a 400 (never a pydantic 422): request models are loose (`replies: list[dict]`), every rule is checked in the store.
- Reads salvage per entry: a missing/garbled file is an empty set; an entry of an unknown kind (a future `plugin`, a newer build's kind) is kept verbatim on disk and preserved across a PUT, but never shown.
- Hide entry is `{"id", "hidden": true}` and nothing else (campaign set only).
- Effective set: the world's replies in order; a campaign reply with the same `id` **replaces** it in place; a campaign hide entry hides it; then the campaign's remaining replies in order. A campaign with no world has an empty world set.
- Every PUT carries `expect` (the digest the client read); a moved file is refused **409 `set_changed`**. Campaign writer holds `locks.campaign_lock(cid)` across the digest check and the write; world writer is atomic-only (no lock), as the tracker's world layer.
- Store writes through `atomic.write_text`; `store.quick_replies` goes in `locks.DOMAIN_MODULES`; inside `store/` bind submodules (`from .campaigns import read as campaigns_read`), imports at module scope (`test_import_guard.py`); paths built from `worlds_paths.world_root` / `campaigns_paths.campaign_root` (`test_paths_guard.py`).
- pydantic v1/v2-agnostic: plain `BaseModel` fields only, dump via `routes.common._dump`.
- Tasks call routes with `force=true` and `upto=<firstIndex + messages.length>` (absolute; ruling 1); disabled while `sceneLocked`, while no connection is `ready`, and while the same task runs from the strip **or** the inspector; outcome (`refreshed`/`asked` false, or an error) is a composer notice; success bumps `ctxKey`.
- `sendText(…, director)` never reads or clears `input`; on failure the canned text is not handed back and the mode is not flipped; it consumes `pendingResponse` exactly as Send does.
- Roll button guards verbatim: `!activeId || busy || sceneLocked || messages.length === 0 || rolling`, plus while `moduleBound` is still unknown (`null`). **Not** gated on a bound module.
- `send` reply in a PC-less scene: disabled with title `"This scene has no player character"`.
- Insert: empty composer → the text, and the composer mode follows the kind (`direct` → Direct, `send` → Speak); same-kind draft → appended after a blank line; different-kind draft → the box is kept and the text is parked in `parkedPrompts` behind the held-draft notice (ruling 6), returning when the box is cleared; refused with a composer notice only if something recovered is already parked for the scene. *Recorded resolution (final review): this replaces "refused, nothing written or parked" — the held-draft notice would be false if nothing were held.*
- **No hotkeys.** Every strip button carries its full text in `title`.
- The strip lives inside `.composer`, so it is hidden wherever the composer is (a done scene today, a closed branch scene from step 3).
- Placeholder names only in fixtures (Mara, Winifred, Seraphine, Realm, Saltmarch).
- Ratchet gates: if a change resolves or adds a ruff/mypy/eslint finding, run `make baseline` and commit the new `lint-baselines/*.json` with that change.
- Backend tests: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`. Frontend tests: `cd frontend && npx vitest run <files>` then `npm run typecheck`.

## Review Focus

1. A quick reply must never eat the player's composer draft: `sendText` neither reads nor clears `input`, and a failed quick send does not hand canned text back (Task 5 tests).
2. Two tabs doing Override/Hide read-modify-writes must not lose an update: stale `expect` → 409 `set_changed`, and the editor re-reads (Task 2, Task 3, Task 9 tests).
3. An older build's PUT must not erase a newer build's (or a future `plugin`) entry it cannot show (Task 2 test).
4. A saved roll in a freeform campaign must stay disabled while `moduleBound` is unknown, mid-turn, and on an empty scene — and must still be offered once `moduleBound === false` (Task 4 and Task 6 tests).
5. A task tapped on the strip while the inspector's own button runs it (or vice versa) must not fire twice (Task 6 test).

---

### Task 1: Entry validation and normalisation

**Files:**
- Create: `backend/src/grimoire/store/quick_replies.py`
- Test: `backend/tests/test_quick_replies.py` (new)

**Interfaces:**
- Produces constants: `VERSION = 1`, `KINDS = ("send", "direct", "roll", "task", "opener")`, `RESERVED_KINDS = ("plugin",)`, `TASKS = ("rolling_summary", "scene_break", "next_scene")`, `MODES = ("send", "insert")`, `MAX_LABEL = 40`, `MAX_TEXT = 2000`, `MAX_ROLL_LABEL = 80`, `MAX_REPLIES = 50`, `FILENAME = "quick_replies.json"`.
- Produces: `class QuickReplyError(ValueError)` with `__init__(self, message: str, code: str = "invalid_quick_reply")` and attribute `code`.
- Produces: `normalize(raw: object, *, campaign: bool) -> dict` — one entry: mints `uuid.uuid4().hex` when `id` is missing/empty; a given id must match `\A[0-9A-Za-z_-]{1,64}\Z`; a hide entry (`hidden` truthy) must be exactly `{"id", "hidden": True}` and is only legal when `campaign=True`; otherwise `label` stripped, non-blank, ≤ 40; `kind` in `KINDS` (`plugin` → `QuickReplyError(..., code="plugin_api_unavailable")`, anything else → "unknown kind"); output carries **only** the kind's fields, no `None` values: text kinds `text` (non-space, ≤ 2000, stored unstripped) + `mode` (default `"send"`); `roll` → `notation` stripped and `dice.parse`d (`DiceError` → `QuickReplyError`), `roll_label` = `" ".join(s.split())` (≤ 80) only when non-empty; `task` → `task` in `TASKS`.
- Produces: `validate_set(raw: object, *, campaign: bool) -> list[dict]` — `raw` must be a list; each entry through `normalize`; duplicate id → `QuickReplyError`; more than `MAX_REPLIES` → `QuickReplyError`.

- [ ] **Step 1: Write the failing tests** (pure, no store):

```python
from grimoire.store import quick_replies as qr

def test_send_is_normalised_to_its_fields_and_gets_an_id():
    out = qr.normalize({"label": " Look around ", "kind": "send", "text": "I take in the room.",
                        "notation": "2d6", "task": None}, campaign=False)
    assert set(out) == {"id", "label", "kind", "text", "mode"}
    assert out["label"] == "Look around" and out["mode"] == "send"
    assert re.fullmatch(r"[0-9a-f]{32}", out["id"])

def test_roll_label_is_absent_when_empty_and_collapsed_when_given():
    assert "roll_label" not in qr.normalize({"label": "Roll", "kind": "roll", "notation": "2d6+3"}, campaign=False)
    assert qr.normalize({"label": "Roll", "kind": "roll", "notation": "1d20",
                         "roll_label": "Perception\n\ncheck"}, campaign=False)["roll_label"] == "Perception check"

@pytest.mark.parametrize("bad", [
    {"label": "x" * 41, "kind": "send", "text": "hi"},
    {"label": "", "kind": "send", "text": "hi"},
    {"label": "Hi", "kind": "send", "text": "x" * 2001},
    {"label": "Hi", "kind": "direct", "text": "   "},
    {"label": "Hi", "kind": "send", "text": "hi", "mode": "later"},
    {"label": "Hi", "kind": "roll", "notation": "two dice"},
    {"label": "Hi", "kind": "task", "task": "absorb"},
    {"label": "Hi", "kind": "teleport"},
    {"id": "has space", "label": "Hi", "kind": "opener"},
])
def test_rule_violations_are_quick_reply_errors(bad):
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize(bad, campaign=False)
    assert exc.value.code == "invalid_quick_reply"

def test_plugin_is_refused_with_its_own_code():
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize({"label": "Run", "kind": "plugin", "command": "x"}, campaign=False)
    assert exc.value.code == "plugin_api_unavailable"

def test_hide_entries_are_exact_and_campaign_only():
    assert qr.normalize({"id": "abc", "hidden": True}, campaign=True) == {"id": "abc", "hidden": True}
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True, "label": "x"}, campaign=True)
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True}, campaign=False)

def test_set_rules():
    one = {"id": "a", "label": "A", "kind": "opener"}
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([one, dict(one)], campaign=False)          # duplicate id
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(51)], campaign=False)
    assert len(qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(50)], campaign=False)) == 50
```

- [ ] **Step 2: Run, verify FAIL** — `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_quick_replies.py` → `ImportError`.
- [ ] **Step 3: Implement** the constants, `QuickReplyError`, `normalize`, `validate_set` in `store/quick_replies.py` (module docstring: what a set is, the layering rule, why reads salvage and writes refuse, why the world writer takes no lock). Imports: `from . import atomic, dice, locks` plus (for Task 2) `from .campaigns import paths as campaigns_paths`, `from .campaigns import read as campaigns_read`, `from .worlds import paths as worlds_paths`.
- [ ] **Step 4: Run** the same command — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): validate and normalise quick reply entries`.

### Task 2: Sets on disk — lenient reads, guarded writes, layering

**Files:**
- Modify: `backend/src/grimoire/store/quick_replies.py`
- Modify: `backend/src/grimoire/store/locks.py` (add `"store.quick_replies"` to `DOMAIN_MODULES` with a comment: whole-file read-modify-write of a campaign file; the digest check and the write are one hold)
- Modify: `backend/src/grimoire/store/__init__.py` (import `quick_replies` in the submodule list and add `"quick_replies"` to `__all__`)
- Test: `backend/tests/test_quick_replies.py`

**Interfaces:**
- Consumes: Task 1's `normalize`, `validate_set`, `QuickReplyError`.
- Produces: `class SetChanged(Exception)`.
- Produces: `world_path(wid: str) -> Path` (`worlds_paths.world_root(wid) / FILENAME`), `campaign_path(cid: str) -> Path` (`campaigns_paths.campaign_root(cid) / FILENAME`).
- Produces: `digest(entries: list[dict]) -> str` — `sha256(json.dumps(entries, sort_keys=True))` hexdigest `[:16]`, over the **stored** entries in order (preserved ones included).
- Produces: `_load(path: Path, *, campaign: bool) -> list[dict]` — never raises; missing → `[]`; unreadable/not JSON/not an object/`version` ≠ 1/`replies` not a list → `[]` with a `log.warning`; per entry: a dict whose `kind` is a non-empty string outside `KINDS` (and not a hide) is kept **verbatim**; a known-kind or hide entry is kept if `normalize` accepts it **and it has a stored id** (no minting on read), else dropped with a warning; a later duplicate id is dropped.
- Produces: `is_shown(entry: dict) -> bool` — known kind or hide entry.
- Produces: `world_set(wid: str) -> dict` → `{"version": 1, "replies": [shown...], "digest": str}`; a `WorldNotFound` reads as empty.
- Produces: `campaign_set(cid: str) -> dict` → same shape (hide entries included in `replies`); raises `CampaignNotFound`.
- Produces: `effective(cid: str) -> list[dict]` — world from `campaigns_read.read_campaign(cid)["meta"].get("world") or ""` (empty → no world replies); layering per Global Constraints; hide entries never appear in the result.
- Produces: `write_world(wid: str, replies: object, expect: str) -> dict` — raises `WorldNotFound` when `not worlds_paths.world_exists(wid)`; `validate_set(campaign=False)`; `digest(_load(...)) != expect` → `SetChanged`; result = clean entries then the stored unshown entries (relative order kept); a clean id colliding with a preserved id, or a merged length > `MAX_REPLIES` → `QuickReplyError`; `atomic.write_text(path, json.dumps({"version": 1, "replies": merged}, indent=2, ensure_ascii=False) + "\n")`; returns `world_set(wid)`.
- Produces: `write_campaign(cid: str, replies: object, expect: str) -> dict` — same with `campaign=True`, the load/digest/merge/write inside `with locks.campaign_lock(cid):`; returns `campaign_set(cid)`.

- [ ] **Step 1: Write the failing tests** (fixture as `test_tracker_fields.py`'s `home`: `worlds.create_world("Realm")`, `campaigns.create_campaign("Saltmarch", wid)`):

```python
def test_sets_round_trip_and_mint_ids(home):
    wid, cid = home
    empty = qr.world_set(wid)
    assert empty["replies"] == [] and empty["version"] == 1
    saved = qr.write_world(wid, [{"label": "Look around", "kind": "send", "text": "I take in the room."}],
                           empty["digest"])
    assert saved["replies"][0]["text"] == "I take in the room." and len(saved["replies"][0]["id"]) == 32
    assert qr.world_set(wid) == saved

def test_effective_appends_replaces_in_place_and_hides(home):
    wid, cid = home
    w = qr.write_world(wid, [{"id": "a", "label": "A", "kind": "opener"},
                             {"id": "b", "label": "B", "kind": "send", "text": "b"},
                             {"id": "c", "label": "C", "kind": "send", "text": "c"}],
                       qr.world_set(wid)["digest"])
    qr.write_campaign(cid, [{"id": "b", "label": "B2", "kind": "direct", "text": "steer"},
                            {"id": "c", "hidden": True},
                            {"id": "d", "label": "D", "kind": "task", "task": "scene_break"}],
                      qr.campaign_set(cid)["digest"])
    assert [(r["id"], r["label"]) for r in qr.effective(cid)] == [("a", "A"), ("b", "B2"), ("d", "D")]

def test_campaign_with_no_world_has_only_its_own(home, monkeypatch): ...
    # create_campaign refuses a missing world, so blank the meta instead:
    # write a world reply and a campaign reply, then
    # monkeypatch.setattr(store.campaigns.read, "read_campaign", lambda c: {"meta": {"world": ""}})
    # (patching the submodule attribute is what the import guard's binding rule makes interceptable);
    # effective(cid) == the campaign's own replies only

def test_garbled_file_reads_empty_and_one_bad_entry_costs_itself(home, caplog):
    wid, _ = home
    qr.world_path(wid).write_text("{not json", encoding="utf-8")
    assert qr.world_set(wid)["replies"] == []
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [
        {"id": "a", "label": "x" * 99, "kind": "send", "text": "t"},
        {"id": "b", "label": "B", "kind": "opener"}]}), encoding="utf-8")
    assert [r["id"] for r in qr.world_set(wid)["replies"]] == ["b"]

def test_unknown_kind_is_hidden_but_survives_a_put(home):
    wid, _ = home
    future = {"id": "p", "label": "Ping", "kind": "plugin", "command": "/ping"}
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [
        future, {"id": "b", "label": "B", "kind": "opener"}]}), encoding="utf-8")
    seen = qr.world_set(wid)
    assert [r["id"] for r in seen["replies"]] == ["b"]
    qr.write_world(wid, [{"id": "c", "label": "C", "kind": "opener"}], seen["digest"])
    stored = json.loads(qr.world_path(wid).read_text(encoding="utf-8"))["replies"]
    assert stored == [{"id": "c", "label": "C", "kind": "opener"}, future]

def test_a_stale_expect_is_refused_and_writes_nothing(home):
    wid, cid = home
    first = qr.campaign_set(cid)["digest"]
    qr.write_campaign(cid, [{"id": "a", "label": "A", "kind": "opener"}], first)
    with pytest.raises(qr.SetChanged):
        qr.write_campaign(cid, [{"id": "z", "hidden": True}], first)
    assert [r["id"] for r in qr.campaign_set(cid)["replies"]] == ["a"]
    with pytest.raises(qr.SetChanged):
        qr.write_world(wid, [], "")                # an expect nobody read never matches

def test_world_writer_refuses_a_missing_world(home):
    with pytest.raises(store.worlds.WorldNotFound):
        qr.write_world("no-such-world", [], qr.digest([]))

def test_fork_and_bundle_carry_the_sets(home, tmp_path):
    # write both sets; child = fork.fork_campaign(cid, "Branch")["id"]; campaign_set(child)["replies"] equal;
    # world_bundle.write_bundle(wid, tmp_path / "b.zip"); new = world_bundle.import_bundle(tmp_path / "b.zip");
    # world_set(new)["replies"] equal
```

- [ ] **Step 2: Run, verify FAIL** — `AttributeError: ... 'world_set'`.
- [ ] **Step 3: Implement** the interfaces above; classify the module in `locks.DOMAIN_MODULES`; export from the facade.
- [ ] **Step 4: Run** `tests/test_quick_replies.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_import_guard.py tests/test_paths_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): layered world and campaign sets with digest-guarded writes`.

### Task 3: Routes

**Files:**
- Create: `backend/src/grimoire/routes/quick_replies.py`
- Modify: `backend/src/grimoire/routes/models.py` (add `QuickReplySetBody`)
- Modify: `backend/src/grimoire/routes/__init__.py` (import `quick_replies`, add it to the docstring table and to the `_compose` loop — anywhere before `entities`, e.g. after `tracker`)
- Test: `backend/tests/test_quick_replies.py`

**Interfaces:**
- Produces (model): `class QuickReplySetBody(BaseModel): replies: list[dict] = []; expect: str = ""`.
- Produces routes (all `def`):
  - `GET /worlds/{wid}/quick-replies` → `store.quick_replies.world_set(wid)`; 404 `"world not found"` unless `store.worlds.world_exists(wid)`.
  - `PUT /worlds/{wid}/quick-replies` (body `QuickReplySetBody`) → `write_world(wid, body.replies, body.expect)`.
  - `GET /campaigns/{cid}/quick-replies` → `{**campaign_set(cid), "inherited": world_set(<campaign's world>)["replies"]}`; `CampaignNotFound` → 404 `"campaign not found"`.
  - `PUT /campaigns/{cid}/quick-replies` → `write_campaign(...)`, answered as the GET above.
  - `GET /campaigns/{cid}/quick-replies/effective` → `{"replies": effective(cid)}`.
- Error mapping (helper `_refuse(exc)`): `QuickReplyError` → `HTTPException(400, detail={"kind": exc.code, "detail": str(exc)})`; `SetChanged` → `HTTPException(409, detail={"kind": "set_changed", "detail": "the set changed since it was read"})` (main.py's handler flattens a dict detail, so the client sees `ApiError.kind`).

- [ ] **Step 1: Write the failing tests** (use the `client` fixture from `conftest.py`):

```python
def test_routes_round_trip_and_layer(client):
    wid = store.worlds.create_world("Realm"); cid = store.campaigns.create_campaign("Saltmarch", wid)
    w = client.get(f"/api/worlds/{wid}/quick-replies").json()
    r = client.put(f"/api/worlds/{wid}/quick-replies", json={"expect": w["digest"], "replies": [
        {"id": "a", "label": "Look around", "kind": "send", "text": "I take in the room."}]})
    assert r.status_code == 200
    c = client.get(f"/api/campaigns/{cid}/quick-replies").json()
    assert c["replies"] == [] and [x["id"] for x in c["inherited"]] == ["a"]
    r = client.put(f"/api/campaigns/{cid}/quick-replies",
                   json={"expect": c["digest"], "replies": [{"id": "a", "hidden": True}]})
    assert r.status_code == 200 and r.json()["inherited"][0]["id"] == "a"
    assert client.get(f"/api/campaigns/{cid}/quick-replies/effective").json() == {"replies": []}

@pytest.mark.parametrize("entry,kind", [
    ({"label": "x" * 41, "kind": "send", "text": "t"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "roll", "notation": "2q6"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "nope"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "plugin"}, "plugin_api_unavailable"),
])
def test_bad_entries_are_400_not_422(client, entry, kind):
    # PUT to the world with the fresh digest; assert status 400 and r.json()["kind"] == kind

def test_stale_expect_is_409_set_changed(client):
    # two PUTs with the same digest; second → 409, json()["kind"] == "set_changed"

def test_unknown_ids_are_404(client):
    # GET/PUT /worlds/nope/quick-replies and /campaigns/nope/quick-replies(/effective) → 404
```

- [ ] **Step 2: Run, verify FAIL** — 404 on the new paths.
- [ ] **Step 3: Implement** the router (module docstring: the three surfaces and why the PUT is whole-set with `expect`).
- [ ] **Step 4: Run** `tests/test_quick_replies.py tests/test_route_order.py tests/test_pydantic_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): world, campaign and effective quick-reply routes`.

### Task 4: Client API, availability rules and the strip component

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Create: `frontend/src/components/quickReplies.ts`, `frontend/src/components/QuickReplyStrip.tsx`
- Modify: `frontend/src/index.css` (`.quick-replies`: one row, `overflow-x: auto`, `flex-wrap: nowrap`; `.quick-reply` buttons `flex: none`)
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`getEffectiveQuickReplies: vi.fn(), getQuickReplies: vi.fn(), setQuickReplies: vi.fn()`), `frontend/src/testkit/campaignHarness.tsx` (`installCampaignMocks`: `getEffectiveQuickReplies` → `{ replies: [] }`)
- Test: `frontend/src/components/quickReplies.test.ts`, `frontend/src/components/QuickReplyStrip.test.tsx` (new)

**Interfaces:**
- Produces (TS types): `QuickReplyKind = "send" | "direct" | "roll" | "task" | "opener"`; `QuickReplyTask = "rolling_summary" | "scene_break" | "next_scene"`; `QuickReplyMode = "send" | "insert"`; `QuickReply = { id: string; label: string; kind: QuickReplyKind; text?: string; mode?: QuickReplyMode; notation?: string; roll_label?: string; task?: QuickReplyTask }`; `QuickReplyHide = { id: string; hidden: true }`; `QuickReplyEntry = QuickReply | QuickReplyHide`; `QuickReplyDraft = Omit<QuickReply, "id"> & { id?: string }`; `QuickReplySet = { version: 1; replies: QuickReplyEntry[]; digest: string; inherited?: QuickReply[] }`; `QuickReplyScope = { kind: "world"; wid: string } | { kind: "campaign"; cid: string }`.
- Produces (client): `quickRepliesPath(scope)` (module function, like `trackerFieldsPath`); `api.getQuickReplies(scope: QuickReplyScope): Promise<QuickReplySet>`; `api.setQuickReplies(scope, replies: (QuickReplyEntry | QuickReplyDraft)[], expect: string): Promise<QuickReplySet>` (PUT `{replies, expect}`); `api.getEffectiveQuickReplies(cid: string): Promise<{ replies: QuickReply[] }>`.
- Produces (`components/quickReplies.ts`):
  - `isHide(e: QuickReplyEntry): e is QuickReplyHide`.
  - `quickReplyTitle(r: QuickReply): string` — the full text: `text` for send/direct; `notation` plus ` — ${roll_label}` when set; task → `"Refresh the scene summary now"` / `"Ask whether the scene should break now"` / `"Choose the next scene"`; opener → `"Open the opener generator"`.
  - `type QuickReplyContext = { busy: boolean; rolling: boolean; renaming: boolean; sceneLocked: boolean; posts: number; moduleKnown: boolean; pcless: boolean; ready: boolean; openerOffered: boolean; taskRunning: (t: "rolling_summary" | "scene_break") => boolean }`.
  - `quickReplyAvailability(r: QuickReply, ctx: QuickReplyContext): { shown: boolean; disabled: boolean; title: string }` — rules: `send` kind with `ctx.pcless` → disabled, title `"This scene has no player character"`; text kinds `mode: "insert"` → enabled; text kinds `mode: "send"` → disabled on `busy || rolling || renaming` (the Send button's guards); `roll` → disabled on `busy || sceneLocked || posts === 0 || rolling || !moduleKnown`; `rolling_summary`/`scene_break` → disabled on `posts === 0 || sceneLocked || !ready || taskRunning(task)`; `next_scene` → disabled on `posts === 0`; `opener` → `shown: ctx.openerOffered`, disabled on `!ready`; any other kind → `shown: false`. `title` is the reason when one is named above, else `quickReplyTitle(r)`.
- Produces: `QuickReplyStrip({ replies, ctx, onRun }: { replies: QuickReply[]; ctx: QuickReplyContext; onRun: (r: QuickReply) => void })` — `null` when no reply is `shown`; else `<div className="quick-replies" role="toolbar" aria-label="Quick replies">` with one `<button type="button" className="quick-reply">` per shown reply (text = `label`, `title`, `disabled`).

- [ ] **Step 1: Write the failing tests:**

```ts
// quickReplies.test.ts
const base: QuickReplyContext = { busy: false, rolling: false, renaming: false, sceneLocked: false,
  posts: 3, moduleKnown: true, pcless: false, ready: true, openerOffered: false, taskRunning: () => false };
const roll: QuickReply = { id: "r", label: "Roll", kind: "roll", notation: "2d6+3" };
test("send mirrors the Send button; insert is never disabled", () => {
  const send: QuickReply = { id: "s", label: "Look", kind: "send", text: "I take in the room.", mode: "send" };
  expect(quickReplyAvailability(send, { ...base, busy: true }).disabled).toBe(true);
  expect(quickReplyAvailability(send, { ...base, sceneLocked: true }).disabled).toBe(false);
  expect(quickReplyAvailability({ ...send, mode: "insert" }, { ...base, busy: true }).disabled).toBe(false);
  expect(quickReplyAvailability(send, base).title).toBe("I take in the room.");
});
test("a send reply in a PC-less scene names why", () => {
  const a = quickReplyAvailability({ id: "s", label: "L", kind: "send", text: "t", mode: "insert" }, { ...base, pcless: true });
  expect(a).toEqual({ shown: true, disabled: true, title: "This scene has no player character" });
  expect(quickReplyAvailability({ id: "d", label: "D", kind: "direct", text: "t", mode: "send" }, { ...base, pcless: true }).disabled).toBe(false);
});
test("roll takes the dice button's guards and waits for the module read", () => {
  for (const ctx of [{ busy: true }, { sceneLocked: true }, { posts: 0 }, { rolling: true }, { moduleKnown: false }])
    expect(quickReplyAvailability(roll, { ...base, ...ctx }).disabled).toBe(true);
  expect(quickReplyAvailability(roll, base).disabled).toBe(false);
});
test("tasks: running from either surface, locked, no connection, empty scene", () => { ... });
test("opener is absent unless the cast panel is offered", () => {
  const o: QuickReply = { id: "o", label: "Open", kind: "opener" };
  expect(quickReplyAvailability(o, base).shown).toBe(false);
  expect(quickReplyAvailability(o, { ...base, openerOffered: true, posts: 0, ready: false }))
    .toEqual({ shown: true, disabled: true, title: "Open the opener generator" });
});
test("an unknown kind is never shown", () => { ... kind: "plugin" as any → shown false });

// QuickReplyStrip.test.tsx
test("renders shown replies in order and nothing when none is shown", ...)  // toolbar "Quick replies"; null for []
test("a click runs the reply; a disabled button does nothing", ...)
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/components/quickReplies.test.ts src/components/QuickReplyStrip.test.tsx`.
- [ ] **Step 3: Implement** the types, client calls, helpers, strip, CSS and mock defaults.
- [ ] **Step 4: Run** the same + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): client api, availability rules and composer strip`.

### Task 5: Composer — the strip, `sendText`, direct and insert

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.quickReplies.test.tsx` (new; copy the `vi.mock` header of `CampaignView.swipes.test.tsx`, `beforeEach(installCampaignMocks)`)

**Interfaces:**
- Consumes: Task 4's `QuickReplyStrip`, `quickReplyAvailability`, `api.getEffectiveQuickReplies`.
- Produces (inside `CampaignView`):
  - state `quickSet: { cid: string; replies: QuickReply[] } | null`, read by an effect on `cid` (`getEffectiveQuickReplies(cid)`, a failure → `{ cid, replies: [] }`); only `quickSet.cid === cid` is rendered.
  - state `quickNotice: { cid: string; sid: string; text: string } | null`, rendered in `.composer-meta` as `<span className="composer-notice" role="status">` only when it matches `cid`/`activeId`; cleared at the start of every quick reply.
  - `async function sendText(id: string, content: string, director: boolean, recover: boolean): Promise<void>` — the body of today's `send()` from the `rerollToRetryRef.current = null` / `setProposalNow(null)` lines and from `const note = …` onward, with every `directing` replaced by `director` and every `recoverPrompt` callback / `recoverableText` argument passed only when `recover` (otherwise `undefined` / `""`). It never reads or writes `input`. `pendingResponse` handling unchanged (passed to `api.chat`, cleared on `landed`).
  - `send()` keeps its guard, the create-scene branch, `setInput("")` and `atBottomRef.current = true`, then `await sendText(id, content, directing, true)`.
  - `async function sendQuick(text: string, director: boolean)` — returns unless `activeId && !busy && !rolling && !renamesInFlight && text.trim()`; sets `atBottomRef.current = true`; `await sendText(activeId, text.trim(), director, false)`.
  - `function insertQuick(text: string, director: boolean)` — empty `input.trim()` → `setInput(text)` and `setDirectMode(director)`; `director === directing` → `setInput((cur) => `${cur.trimEnd()}\n\n${text}`)`; otherwise `setQuickNotice({ cid, sid: activeId, text: `${director ? "🎬 note" : "post"} not inserted · the box holds a ${directing ? "note" : "post"} — clear it first` })`.
  - `function runQuickReply(r: QuickReply)` — dispatch by kind (Tasks 6–7 fill `roll`, `task`, `opener`); text kinds → `r.mode === "insert" ? insertQuick(r.text!, r.kind === "direct") : void sendQuick(r.text!, r.kind === "direct")`.
  - The strip renders inside `.composer`, between `.composer-meta` and `.inputbar`, only when `activeId`, with `ctx` built from `busy`, `rolling`, `renamesInFlight > 0`, `sceneLocked`, `messages.length`, `moduleBound !== null`, `activePcless`, `ready` (Tasks 6–7 add `taskRunning`, `openerOffered`).

- [ ] **Step 1: Write the failing tests** (`getEffectiveQuickReplies` mocked per test; scene with posts `[{ role: "user", content: "hi" }, { role: "assistant", content: "a reply" }]`):

```ts
const LOOK = { id: "q1", label: "Look around", kind: "send", text: "I take in the room.", mode: "send" };
test("the strip shows the effective set in order, and is absent when empty", ...)
  // two replies → toolbar "Quick replies" buttons in order; default mock → queryByRole("toolbar") null
test("send posts the canned text and leaves the draft alone", async () => {
  (api.getEffectiveQuickReplies as any).mockResolvedValue({ replies: [LOOK] });
  renderCampaign(); await screen.findByText("a reply");
  fireEvent.change(screen.getByPlaceholderText("Speak your intent…"), { target: { value: "half a thought" } });
  fireEvent.click(screen.getByRole("button", { name: "Look around" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "I take in the room.",
    expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
  expect(screen.getByPlaceholderText("Speak your intent…")).toHaveValue("half a thought");
});
test("direct sends a director note without flipping the composer", ...)   // last chat arg true; Speak still aria-pressed
test("a failed quick send does not hand the canned text back", ...)       // api.chat rejects; textarea value stays ""
test("a quick send consumes the one-shot response targets", ...)          // type 300 in "Next reply words"; chat 5th arg objectContaining({ response_continuation_words: "300" })
test("insert fills an empty composer and switches its mode to the reply's", ...) // direct insert → value text, Direct aria-pressed, api.chat not called
test("insert appends to a same-kind draft after a blank line", ...)        // "draft" → "draft\n\nI take in the room."
test("insert refuses a different-kind draft with a notice", ...)           // Speak draft + direct insert → value unchanged, status /note not inserted/
test("a send reply is disabled in a PC-less scene", ...)                   // listScenes [{...ONE_SCENE[0], pcless: true}] → disabled, title "This scene has no player character"
test("the strip goes with the composer on a finished scene", ...)          // listScenes [{...ONE_SCENE[0], done: true}] → no toolbar
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/routes/CampaignView.quickReplies.test.tsx`.
- [ ] **Step 3: Implement** the interfaces above. No `useHotkeys` entry for any of it.
- [ ] **Step 4: Run** the new file plus `src/routes/CampaignView.test.tsx` (Send/Direct regressions) + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): composer strip with send, direct and insert`.

### Task 6: Composer — saved rolls, tasks, next scene

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/components/SceneInspector.tsx` (two props; its manual Refresh now / Ask now)
- Test: `frontend/src/routes/CampaignView.quickReplies.test.tsx`, `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Produces: `async function doRoll(notation: string, label: string | undefined): Promise<"skipped" | "ok" | { error: string }>` — guard `!activeId || busy || sceneLocked || rolling` → `"skipped"`; empty `notation.trim()` → `"skipped"`; takes `takeRollLatch(activeId)`, `api.roll(cid, activeId, notation, label)`, `selectScene` + `askAfterPost(activeId, seen)` as today; a rejection → `{ error: err.detail ?? String(err) }`; latch released in `finally`.
- Produces: `async function rollFromForm()` — the popover's Enter/Roll ▸ handler (replaces the three `doRoll()` call sites): returns without `rollForm`; `doRoll(rollForm.notation.trim(), rollForm.label.trim() || undefined)`; `"ok"` → `setRollForm(null)`; `{ error }` → `setRollForm({ ...rollForm, error })`.
- Strip `roll` → `doRoll(r.notation!, r.roll_label)`; `{ error }` → `quickNotice` `Roll failed: ${error}`.
- Produces: state `taskBusy: Record<string, true>` keyed `` `${origin}:${cid}/${sid}:${task}` `` with `origin` `"strip" | "inspector"`; `taskRunning(task)` true when either origin's key for the active scene is set; a memoized `stripTasks = { rolling_summary: boolean; scene_break: boolean }` (strip origin only) for the inspector; `onInspectorTaskBusy = useCallback((task, busy) => …)` keyed on the `cid`/`activeId` it was created for.
- Produces: `async function runQuickTask(task: QuickReplyTask)` — `next_scene` → `newScene()`; otherwise set the strip key, call `api.refreshRollingSummary(cid, sid, true, messages.length)` / `api.askSceneBreak(cid, sid, true, messages.length)`; `refreshed`/`asked` true → `setCtxKey((n) => n + 1)` (only if `activeIdRef.current === sid`); false → notice `"Summary already current — nothing new to fold"` / `"No scene-break question asked"`; rejection → notice `` `Summary failed: ${detail}` `` / `` `Scene-break check failed: ${detail}` ``; clear the key in `finally`.
- Produces (`SceneInspector` props): `stripTasks?: { rolling_summary?: boolean; scene_break?: boolean }` — Refresh now additionally disabled on `stripTasks?.rolling_summary`, Ask now on `stripTasks?.scene_break`; `onTaskBusy?: (task: "rolling_summary" | "scene_break", busy: boolean) => void` — called `true` where `refreshRolling` sets `rollingBusy` and where the Ask now handler sets `breakBusy`, `false` in their `finally`.

- [ ] **Step 1: Write the failing tests:**

```ts
const ROLL = { id: "q2", label: "Search", kind: "roll", notation: "1d20+2", roll_label: "Perception" };
test("roll calls the roll route with the saved notation, and is offered without a bound module", async () => {
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: "", source: "global" });
  (api.getEffectiveQuickReplies as any).mockResolvedValue({ replies: [ROLL] });
  (api.roll as any).mockResolvedValue({ ok: true, roll: { id: "r1" }, message: "🎲" });
  // scene with posts; await enabled "Search"; click
  await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "1d20+2", "Perception"));
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();   // the dice button stays hidden
});
test("roll is disabled while the module read is out and on an empty scene", ...)  // getCampaignModule never settles → disabled; messages [] → disabled
test("a failed saved roll says so in the composer", ...)                           // api.roll rejects ApiError(400, "bad") → status "Roll failed: bad"
test("summary task forces a bounded fold and bumps the context on success", async () => {
  // replies [{ id: "q3", label: "Summarize", kind: "task", task: "rolling_summary" }]; refreshRollingSummary resolves refreshed: true
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith("run", "s1", true, 2));
});
test("a declined fold or question is a notice", ...)                               // refreshed: false → /already current/; askSceneBreak asked: false → /No scene-break question/
test("a task running from the inspector disables the strip, and from the strip the inspector", ...)
  // refreshRollingSummary never settles; open "What the model saw →"; click strip "Summarize" → inspector "Refresh now" disabled;
  // then (fresh render) click "Refresh now" → strip "Summarize" disabled
test("next_scene opens the scene chooser", ...)                                    // click → findByTestId("scene-chooser")

// SceneInspector.test.tsx
test("a task the strip is running disables its manual button", ...)               // stripTasks={{ rolling_summary: true }} → "Refresh now" disabled
test("its manual buttons report themselves busy", ...)                             // onTaskBusy called ("rolling_summary", true) then (…, false)
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/routes/CampaignView.quickReplies.test.tsx src/components/SceneInspector.test.tsx`.
- [ ] **Step 3: Implement** the interfaces above; pass `stripTasks={stripTasks}` and `onTaskBusy={onInspectorTaskBusy}` to the memoized `<SceneInspector>` (both stable references).
- [ ] **Step 4: Run** the two files plus `src/routes/CampaignView.test.tsx` (the popover roll tests at "rolls dice from the input bar popover…") + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): saved rolls and auxiliary tasks from the strip`.

### Task 7: Composer — opening the opener generator

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx`, `frontend/src/components/OpenerComposer.tsx`, `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`componentStubs.CastPanel` renders `data-opener-request={openerRequest ?? 0}` on its root)
- Test: `frontend/src/components/CastPanel.test.tsx`, `frontend/src/routes/CampaignView.quickReplies.test.tsx`

**Interfaces:**
- Produces: `CastPanel` prop `openerRequest?: number` — the `<details>` becomes controlled (`const [open, setOpen] = useState(true)`, `open={open}`, `onToggle={(e) => setOpen(e.currentTarget.open)}`); an effect on `openerRequest` (when > 0) sets `open` true; forwards `focusRequest={openerRequest}` to `OpenerComposer`.
- Produces: `OpenerComposer` prop `focusRequest?: number` — a ref on the `aria-label="Opener prompt"` input; an effect on `focusRequest` (when > 0) calls `focus()`.
- Produces (CampaignView): state `openerRequest: number`; `openerOffered = !!activeId && landedScene?.cid === cid && landedScene.sid === activeId && messages.length === 0` (the CastPanel mount condition, hoisted into a const and reused there); strip `opener` → `setOpenerRequest((n) => n + 1)`; `ctx.openerOffered = openerOffered`.

- [ ] **Step 1: Write the failing tests:**

```ts
// CastPanel.test.tsx
test("an opener request expands the panel and focuses the opener prompt", async () => {
  const { container, rerender } = render(<CastPanel cid="c" sid="s" ready onSeeded={() => {}} />);
  const details = container.querySelector("details")!;
  details.open = false; fireEvent(details, new Event("toggle"));
  rerender(<CastPanel cid="c" sid="s" ready onSeeded={() => {}} openerRequest={1} />);
  await waitFor(() => expect(details.open).toBe(true));
  expect(document.activeElement).toBe(screen.getByLabelText("Opener prompt"));
});
// CampaignView.quickReplies.test.tsx
test("opener opens the generator on an empty scene and is absent otherwise", ...)
  // getScene messages [] → click "Write opener" → findByTestId("cast-panel") has data-opener-request "1";
  // scene with posts → queryByRole("button", { name: "Write opener" }) null
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/components/CastPanel.test.tsx src/routes/CampaignView.quickReplies.test.tsx`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same plus `src/components/OpenerComposer.test.tsx` + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): a quick reply can open the opener generator`.

### Task 8: `QuickReplyEditor` — world scope (list/detail)

**Files:**
- Create: `frontend/src/components/QuickReplyEditor.tsx`
- Modify: `frontend/src/components/quickReplies.ts` (set-edit helpers)
- Test: `frontend/src/components/QuickReplyEditor.test.tsx` (new; `vi.mock("../api/client", …)` with `getQuickReplies`/`setQuickReplies` as `vi.fn()`), `frontend/src/components/quickReplies.test.ts`

**Interfaces:**
- Produces (helpers, pure, never mutate): `upsertEntry(entries: (QuickReplyEntry | QuickReplyDraft)[], entry: QuickReplyEntry | QuickReplyDraft, replaceId?: string)` (replace the entry with `replaceId` in place, else append); `moveEntry(entries, id: string, delta: -1 | 1)` (no-op at either end); `removeEntry(entries, id)`; `hideEntry(entries, id)` (appends `{ id, hidden: true }`); `overrideOf(r: QuickReply): QuickReply` (a copy keeping `id`).
- Produces: `QuickReplyEditor({ scope }: { scope: QuickReplyScope })` — loads `api.getQuickReplies(scope)` into `{ replies, digest, inherited }`; `.editor` > `.editor-list` (`+ New quick reply`, one `.row` per non-hide own entry showing its `label`, and per row `↑`/`↓` buttons labelled `` `Move ${label} up` `` / `` `Move ${label} down` ``, disabled at the ends) + `.editor-body`; `mode: "view" | "edit"`.
  - View: `.detail-view` > `.detail-main` (`<h3>{label}</h3>`, `.detail-rendered` = `<Markdown remarkPlugins={[remarkGfm]}>` of `text` for send/direct, else `quickReplyTitle(r)`) and `<aside className="detail-sidebar">` (`.form-actions` with **Edit**; `.side-section`s "Kind" and, per kind, "Mode" / "Notation" / "Roll label" / "Task" as `<span className="chip on">`).
  - Form: Label (`maxLength={40}`), Kind select (`send` "Speak", `direct` "Direct", `roll` "Roll", `task` "Task", `opener` "Opener"), then per kind: Text textarea (`maxLength={2000}`) + Mode select (`send` "Send at once", `insert` "Put in the composer"); Notation + Roll label; Task select (`rolling_summary` "Refresh summary", `scene_break` "Ask about a scene break", `next_scene` "Next scene"). Save builds only the kind's fields, `upsertEntry`, `api.setQuickReplies(scope, next, digest)`; success → state from the response, re-select (a new entry: the returned id not among the previous ids) and `view`; Cancel → `view`; **Delete** (existing entry only) → `removeEntry` + PUT. Reorder → `moveEntry` + PUT.
  - Errors: `ApiError` 400 → its `detail` in a `.banner` in the form; 409 `kind === "set_changed"` → re-read the set, keep the form and draft, banner `"This set changed elsewhere and has been re-read — save again to apply your change."`.

- [ ] **Step 1: Write the failing tests:**

```ts
const SET = { version: 1, digest: "d1", replies: [
  { id: "a", label: "Look around", kind: "send", text: "I take in the **room**.", mode: "send" },
  { id: "b", label: "Search", kind: "roll", notation: "1d20+2" }] };
test("clicking a row shows the read-only view with its sidebar", async () => {
  // render scope {kind:"world", wid:"realm"}; click row "Look around"
  // → heading "Look around", <strong>room</strong> rendered, no textbox/textarea, chip "Speak", button "Edit"
});
test("Edit reveals the form", ...)                       // Label input value "Look around"
test("+ New opens the form directly", ...)              // empty Label, no row selected
test("save PUTs the whole set with the digest it read", async () => {
  // edit label → "Look closer", Save → setQuickReplies({kind:"world",wid:"realm"},
  //   [{ id: "a", label: "Look closer", kind: "send", text: "I take in the **room**.", mode: "send" }, SET.replies[1]], "d1")
  // → back to view with heading "Look closer"
});
test("the form's fields follow the kind", ...)          // Kind → roll shows Notation, hides Text
test("↓ reorders and saves", ...)                       // "Move Look around down" → replies order [b, a]
test("a 400 is shown in the form", ...)                 // setQuickReplies rejects ApiError(400, "can't read dice notation…") → text visible, still editing
test("a 409 re-reads and keeps the draft", ...)         // rejects ApiError(409, "…", "set_changed") → getQuickReplies called twice, banner /changed elsewhere/, Label still the draft
// quickReplies.test.ts: upsert in place / append, moveEntry no-op at ends, hideEntry appends the hide entry
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/components/QuickReplyEditor.test.tsx src/components/quickReplies.test.ts`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): list/detail editor for a world's quick replies`.

### Task 9: Campaign scope — inherited replies, Override/Hide/Show, and mounting

**Files:**
- Modify: `frontend/src/components/QuickReplyEditor.tsx`
- Modify: `frontend/src/worldPaths.ts` (`FLAT_SECTIONS` gains `"quick-replies"`; `SectionTarget`'s first section variant `at: "overview" | "push" | "tags" | "tracker" | "quick-replies"`; the doc comment counts six screens)
- Modify: `frontend/src/routes/WorldView.tsx` (`IndexKey` gains `"quick-replies"`; `INDEX` Writing group row `{ key: "quick-replies", label: "Quick replies" }` after Tracker; the campaign-shape filter drops it with `tags`/`tracker`; `countOf` → `api.getQuickReplies({ kind: "world", wid }).then((b) => b.replies.length)`; the world-shape `readCounts` keys become `["tags", "tracker", "quick-replies"]`; render `{!campaign && section === "quick-replies" && <QuickReplyEditor key={wid} scope={{ kind: "world", wid }} />}`)
- Modify: `frontend/src/routes/CampaignHub.tsx` (panel union gains `"quick-replies"`; Settings list gains `["quick-replies", "Quick replies"]`; render `{panel === "quick-replies" && <QuickReplyEditor key={`${cid}:qr`} scope={{ kind: "campaign", cid }} />}`)
- Test: `frontend/src/components/QuickReplyEditor.test.tsx`, `frontend/src/worldPaths.test.ts`, `frontend/src/routes/WorldView.test.tsx` (add `getQuickReplies: vi.fn()` to its api mock with a default), `frontend/src/routes/CampaignHub.test.tsx` (mock `../components/QuickReplyEditor` as it mocks `TrackerFieldsEditor`)

**Interfaces:**
- Produces (campaign scope of `QuickReplyEditor`): the rail gets a second heading "From the world" listing `inherited` rows (`.row`, label plus a `chip` "hidden" when a hide entry names it, "overridden" when a campaign reply shares its id); the own section lists campaign entries that are neither hides nor overrides.
  - An inherited row opens read-only. Its sidebar `.form-actions`: not overridden and not hidden → **Override** (opens the form on `overrideOf(world)`; Save `upsertEntry`s it, so the campaign entry keeps the world id) and **Hide** (`hideEntry` + PUT); hidden → **Show** (`removeEntry` of the hide entry + PUT); overridden → the view shows the campaign version with **Edit**, and the form's **Delete** removes the override (the world reply shows again).
- Produces (`worldPaths`): `sectionHref({ kind: "world", id: "realm" }, { kind: "section", at: "quick-replies" }) === "/worlds/realm/quick-replies"`; throws on the campaign shape.

- [ ] **Step 1: Write the failing tests:**

```ts
const CAMP = { version: 1, digest: "c1", replies: [{ id: "w2", hidden: true }],
  inherited: [{ id: "w1", label: "Look around", kind: "send", text: "t", mode: "send" },
              { id: "w2", label: "Search", kind: "roll", notation: "1d20" }] };
test("inherited replies are read-only with Override and Hide", ...)  // click "Look around" → no "Edit", buttons Override/Hide
test("Hide writes a hide entry", async () => {
  // click "Look around" → Hide → setQuickReplies({kind:"campaign",cid:"run"},
  //   [{ id: "w2", hidden: true }, { id: "w1", hidden: true }], "c1")
});
test("Show removes the hide entry", ...)        // click "Search" (chip "hidden") → Show → replies []
test("Override saves a campaign entry under the world id", ...)
  // Override → form prefilled; change Label "Look closer"; Save → replies contain { id: "w1", label: "Look closer", kind: "send", text: "t", mode: "send" }
// worldPaths.test.ts
test("quick replies are a world section with a screen of its own", ...) // href + campaign throws + parseWorldTail("quick-replies","world") ok, "campaign" not
// WorldView.test.tsx
test("the Quick replies row counts the world's replies and opens their editor", ...) // indexRow("Quick replies") has "2"; click → heading "Quick replies"
// CampaignHub.test.tsx
test("Settings opens the campaign's quick replies", ...) // click "Quick replies" → mocked editor rendered with scope {kind:"campaign", cid}
```

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/components/QuickReplyEditor.test.tsx src/worldPaths.test.ts src/routes/WorldView.test.tsx src/routes/CampaignHub.test.tsx`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same + `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(quick-replies): campaign overrides and hides, mounted in world view and hub`.

### Task 10: Full gate

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python`.
- [ ] **Step 2:** Fix whatever it reports (a changed ratchet count → `make baseline` and commit the new `lint-baselines/*.json`); nothing else.
- [ ] **Step 3: Commit** `chore(quick-replies): make check clean` (only if Step 2 changed anything).

## Plan-gate rulings (binding; override the tasks above where they differ)

Plan → implementation gate: independent adversarial review (stand-in for
`/codex:adversarial-review`; owner-approved).

1. **`upto` is absolute**: `firstIndex + messages.length` (the transcript is
   fetched in windows of 60). Task 6 adds a test where `getScene` returns a
   non-zero `offset`/`total` and asserts the absolute `upto`. (The spec's
   `messages.length` is corrected to the same.)
2. **Unbound-module mock**: `getCampaignModule` resolves
   `{ setting: "", resolved: null, source: null }` in the "offered without a
   bound module" test.
3. **Ratchet**: new code adds **zero** lint/mypy/eslint findings — narrow
   `unknown` errors (no `catch (err: any)`), `void` floating promises, no
   `async` onClick handlers. `make baseline` only when a count *decreases*.
4. **Opener focus**: CastPanel opens synchronously (derive `open` during
   render from `openerRequest` vs the last handled request kept in a ref that
   starts at the mount value), then focuses the prompt in a later effect. Tests:
   mounting with `openerRequest={1}` does not focus; a `focus` spy asserts
   `details.open === true` when called.
5. **Not found**: the five routes reuse `routes/tracker.py`'s
   `_world_or_404` / `_campaign_world` pattern; `write_campaign` raises
   `CampaignNotFound` unless `campaign_exists(cid)`, checked inside the lock.
6. **Insert into a different-kind draft** reuses the existing held-draft
   notice (`heldKind` hint) rather than new wording.
7. **Campaign-scope reorder** moves within the visible own entries only; the
   ends-disabled state comes from the same list.
8. ↑/↓ are sibling buttons of the `.row` button inside a row wrapper (no nested
   buttons).
9. Sidebar kind chips use the form's select labels (e.g. `send` → "Speak").
10. The no-lost-update guarantee (`expect`) is campaign-scope only; the world
    writer is atomic-only (spec-accepted). Stated in the module docstring.
11. A hide entry whose world reply no longer exists is dropped on save (it is
    invisible and would count toward the cap).
12. `replies: list[dict]` makes a non-dict element a 422 (accepted, TrackerLayer
    style).
