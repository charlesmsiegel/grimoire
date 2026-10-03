# Scene State Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the transient turn-state ledger with a per-scene, per-post tracker. It stores a full snapshot of every present character's typed fields, each value tagged with who is aware of it. The snapshot is updated by a background LLM call after every post and filtered into each character's prompt.

**Architecture:** A new `store/tracker/` package holds the pieces:

- **`paths`:** file locations.
- **`fields`:** built-in defaults plus world, campaign and scene layers.
- **`records`:** snapshot files and the per-scene index. Its I/O is keyed by scene identity, and it never imports `store.scenes`.
- **`walk`:** transcript-aware lookups: which key belongs to which post, the state at a point, flagging and pruning.
- **`merge`** and **`view`:** pure logic for merging replies and filtering for prompts.
- **`prompt`:** builds the update-call messages.

`routes/tracker.py` owns scheduling: one `background` detached run per post, serialized per scene by an `asyncio.Lock`. It also owns the update coroutine and the HTTP API. The frontend adds a lazy per-post Tracker disclosure, a mood label on cast tiles, a Now section in the dossier, three field-layer editors and two settings.

**Tech Stack:** FastAPI + pydantic (v1/v2-agnostic), Jinja2 templates, pytest; React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-10-03-scene-state-tracker-design.md`. Read it before any task. This plan refines one illustrative shape: per-character snapshot entries are `{"present": bool, "fields": {...}}` rather than carrying a `_present` key among the fields.

## Global Constraints

- CLAUDE.md governs everything. In particular:
  - Every store write goes through `store.atomic` under `locks.campaign_lock(cid)`.
  - Every new campaign-mutating store module is listed in `store/locks.py` `DOMAIN_MODULES` with a reason comment.
  - Imports are at module scope only. Inside `store/`, cross-package imports bind a submodule (`from ..scenes import read as scenes_read`).
  - pydantic: plain `BaseModel` fields only, dumped with `routes.common._dump`.
  - Every prompt is a Jinja2 template under `templates/`.
- Test names and fixtures use only the existing placeholder names: Mara, Winifred, Seraphine, Realm, Saltmarch.
- Field types: `text`, `list`, `enum`. Default awareness: `present` or `self`. Text values and list items are collapsed to one line and truncated at **200** characters.
- `visible_mood` options are exactly the 28 labels in Task 3, in that order.
- The global setting `tracker` defaults to `"on"` and `perception_rider` to `"on"`. Both are stored as `"on"`/`"off"`. The campaign key `tracker` in `campaign.md` is `""` (inherit), `"on"` or `"off"`.
- The new route key is `tracker` with the single task `tracker-update`, `campaign_scoped=True`.
- Record keys are `r-<response_id>-<variant_id>` (character posts) and `p-<post_id>` (user posts). All ids are 32-hex.
- The ratchet gates fail on an improvement as well as a regression. After removing code that had lint or mypy findings, run `make baseline` and commit the smaller files.
- Commit messages are sentence-case and descriptive (the repo's style). End every commit with the attribution lines the session supplies.
- Commands below run from the repo root. `PY=backend/.venv/bin/python`, and `T="PYTHONPATH=backend/src $PY -m pytest -q"` is shorthand used in steps.

## Review Focus

1. **Rapid posts.** Three posts land in one round before the first update finishes. Each update must start from its predecessor's snapshot, not a stale one. Covered in Task 8 (`test_updates_in_one_scene_run_in_order`).
2. **A post removed mid-update.** A cut, or a response deleted while its update is in flight, must not resurrect a record for a post that no longer exists. Covered in Task 8 (`test_update_for_a_cut_post_is_discarded`).
3. **Malformed model output.** Prose around the JSON, unknown names, unknown fields, an enum value outside its options, or a list given as a string. These are dropped, never applied, and never crash. Covered in Task 6.
4. **A scene renamed while an update is in flight.** The record must still land, because records are keyed by identity, not sid. Covered in Task 8 (`test_update_survives_a_rename`).
5. **Tracker switched off for a campaign mid-scene.** No new updates are scheduled, the prompt section disappears, and existing records stay readable. Covered in Task 8 (`test_campaign_off_schedules_nothing`) and Task 10 (`test_section_absent_when_tracker_off`).

---

### Task 0: Environment

**Files:** none (not committed).

- [ ] **Step 1:** `python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "backend[dev,desktop]"`
- [ ] **Step 2:** `cd frontend && npm ci && cd ..`
- [ ] **Step 3:** `make check-py PY=backend/.venv/bin/python`. Expected: passes on the untouched branch. Note any pre-existing failures before starting, so they are not attributed to this work.

---

### Task 1: Retire turn state

Remove `store/turnstate.py` and everything that reads or writes it. Keep the trailing-`state`-fence stripping.

**Files:**
- Create: `backend/src/grimoire/store/state_fence.py`. Move into it, unchanged: `parse_block`, `split_block` and `StreamRedactor` from `turnstate.py`, with their private helpers and the `MAX_VALUE` cap that `parse_block` uses.
- Delete: `backend/src/grimoire/store/turnstate.py`, `backend/tests/test_turnstate_flow.py`, `backend/tests/test_turnstate_store.py`, `templates/scene/sections/transient_state.j2`, `templates/scene/sections/transient_tracker.j2`.
- Modify, removing each turnstate reference listed below:
  - **Store:**
    - `store/replay.py:61,457`
    - `store/scene_refs.py:60,74`
    - `store/responses.py:17,415`
    - `store/config.py:121,126,130,209,248,250,382-400`: `turnstate_depth`/`promote_streak` keys, defaults and accessors.
    - `store/__init__.py:140,271`
    - `store/scenes/lifecycle.py:29,239`
    - `store/scenes/write.py:15,299-307,353-354,456,610`. The parked-entries token in `remove_trailing_assistant_run`/`restore_trailing_assistant_run` goes too.
    - `store/locks.py:270-275`: the `"store.turnstate"` entry.
    - `store/response_protocol.py:8,94`: import `state_fence` instead.
    - `store/cascade.py:127,356`
    - `store/retcon.py:77,350`
    - `store/absorb/materializer.py:23,226-304,392`: delete `_promote` and the `turn_ledger` parameter of `materialize`.
    - `store/context/world_state.py:22,1204-1236`: delete `_transient_states`.
    - `store/context/assemble.py:42,121-125,364,375-376,528,689-694,786-787`. `_CAST_SECTIONS` becomes `("character_state",)` for now. Keep the plain `with locks.best_effort_campaign_lock(cid): scene = scenes_read.read_scene(cid, sid)`.
  - **Routes:**
    - `routes/streaming.py:218-307,328,348,715`: delete `_record_turnstate`. `split_block` and `StreamRedactor` now come from `store.state_fence`.
    - `routes/character_turns.py:216,228,248`: `_normalise` returns `(text, issue)`. Update its three callers.
    - `routes/scenes.py:2231,4712`: `_absorb_snapshot` returns `(epoch, scene)`, and `_Prepared` loses `ledger`.
    - `routes/config.py:93`, `routes/models.py:44`
  - **Elsewhere:**
    - `scripts/verify_templates.py:399-418,515-519,580-602,847-854,913-915,940-941`
    - `templates/README.md:306-317`
    - `evals/cases.py:62,186` and `evals/README.md:117`
  - **Tests:**
    - `backend/tests/test_cascade_store.py:28,109-112`
    - `test_character_turns.py:332-333`
    - `test_replay_store.py:331-336`
    - `test_retcon_store.py:23,97-100`
    - `test_config_store.py:194-218`
    - `test_stream_watchers_incremental.py:35,125`
    - `backend/tests/store_api_baseline.json`: regenerate it the way the test that reads it says to (grep `store_api_baseline`).
  - **Frontend:**
    - `frontend/src/api/types.ts:126,188`: drop `turnstate_depth`/`promote_streak`.
    - `frontend/src/routes/ConfigView.tsx:57,127-128,964-980`: remove the "transient" section and its `SectionId` member.
    - `frontend/src/routes/ConfigView.test.tsx:39,443`

**Interfaces:**
- Produces: `store.state_fence.split_block(text: str) -> tuple[str, dict]` and `store.state_fence.StreamRedactor` (same behaviour as before).

- [ ] **Step 1: Move the fence tests**

  Rename `test_turnstate_store.py`'s block-grammar tests (every test that calls `split_block`, `parse_block` or `StreamRedactor`) into a new `backend/tests/test_state_fence.py` that imports `from grimoire.store import state_fence`. Delete the remaining turnstate tests.

- [ ] **Step 2: Create `state_fence.py` and delete `turnstate.py`, editing every listed file**

  `grep -rn turnstate backend/src scripts evals templates frontend/src backend/tests` must print nothing except `test_state_fence.py`'s docstring, if any.

- [ ] **Step 3: Pin that a trailing `state` block is still stripped**

  Add to `backend/tests/test_state_fence.py`:

```python
def test_trailing_state_block_still_stripped_from_a_landed_turn(client):
    # a reply ending in a legacy ```state block lands without it
    ...  # use llm_fakes.FakeOpenRouter([ "She waits.\n\n```state\n{\"Mara\": {\"mood\": \"calm\"}}\n```" ])
    # assert "```state" not in the stored transcript and "She waits." is
```

  Model it on the existing turn tests in `test_character_turns.py`: a scene with Mara present, `POST .../chat`, then read the scene.

- [ ] **Step 4: Run the affected suites**

  Run: `PYTHONPATH=backend/src $PY -m pytest -q backend/tests/test_state_fence.py backend/tests/test_character_turns.py backend/tests/test_cascade_store.py backend/tests/test_replay_store.py backend/tests/test_retcon_store.py backend/tests/test_config_store.py backend/tests/test_stream_watchers_incremental.py backend/tests/test_routes.py`

  Expected: PASS.

- [ ] **Step 5: Templates, frozen campaign, frontend**

  Run `$PY scripts/verify_templates.py` (PASS). Run `cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep && cd ..` and confirm `git diff --stat backend/tests/fixtures/frozen_campaign/snapshot.json` shows **no change**. The removed sections were inert at the default depth of 0; investigate any diff before continuing. Then run `cd frontend && npx vitest run src/routes/ConfigView.test.tsx && npm run typecheck && cd ..`.

- [ ] **Step 6: Full gate and commit**

  Run `make check PY=$PY`, run `make baseline PY=$PY` if a ratchet reports improvements, and re-run until green. Then:

  `git add -A && git commit -m "Retire the transient turn-state ledger, keeping the trailing state-fence strip"`

---

### Task 2: Tracker settings

**Files:**
- Modify: `backend/src/grimoire/store/config.py`. Add `DEFAULT_TRACKER = "on"` and `DEFAULT_PERCEPTION_RIDER = "on"`, and add both keys to `_CONFIG_KEYS` and the `read_config()` defaults. Add `tracker_enabled() -> bool` and `perception_rider() -> bool`, each meaning `== "on"`.
- Modify: `backend/src/grimoire/store/campaigns/lifecycle.py`. Add `set_campaign_tracker(cid: str, value: str) -> None`, modelled on `set_campaign_routing`: it takes the campaign lock, pops the key when `value == ""`, raises `ValueError` unless value ∈ `("", "on", "off")`, and stamps `updated`.
- Create: `backend/src/grimoire/store/tracker/__init__.py`. Leave it empty; submodules are imported explicitly so the package import never triggers a cycle.
- Create: `backend/src/grimoire/store/tracker/settings.py`:
  - `campaign_setting(cid) -> str` returns `""`, `"on"` or `"off"` from `campaigns_read.read_campaign(cid)["meta"].get("tracker")`; anything else counts as `""`.
  - `enabled(cid) -> bool`: the campaign setting when it is `"on"` or `"off"`, otherwise `config.tracker_enabled()`.
- Modify: `backend/src/grimoire/store/__init__.py`. At the end of its imports, add `from . import tracker  # noqa: F401` and `from .tracker import settings as _tracker_settings  # noqa: F401`. Each later task that creates a `store/tracker/` submodule adds one more line of the same form (`from .tracker import fields as _tracker_fields  # noqa: F401`, and so on), so `store.tracker.<submodule>` resolves for routes and tests.
- Modify: `routes/models.py`. Add `tracker: str | None = None` and `perception_rider: str | None = None` to `ConfigUpdate`, plus `class CampaignTracker(BaseModel): setting: str`.
- Modify: `routes/config.py` `_public_config`: add both keys with their defaults.
- Create: `backend/src/grimoire/routes/tracker.py` with `router = APIRouter()` and the two campaign-setting routes:
  - `GET /campaigns/{cid}/tracker` returns `{"setting": str, "enabled": bool}`.
  - `PUT /campaigns/{cid}/tracker` takes body `CampaignTracker`. It returns 400 on a bad value, 404 for an unknown campaign, and otherwise the GET body.
- Modify: `routes/__init__.py`. Add `tracker` to the `_compose` loop, before `entities`.
- Test: `backend/tests/test_tracker_settings.py`

**Interfaces:**
- Produces: `store.tracker.settings.enabled(cid: str) -> bool` and `store.config.perception_rider() -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tracker_defaults_on_and_campaign_overrides(monkeypatch, tmp_path):
    # GRIMOIRE_HOME isolated; world "Realm", campaign "Saltmarch"
    assert settings.enabled(cid) is True
    campaigns.set_campaign_tracker(cid, "off");  assert settings.enabled(cid) is False
    store.write_config(tracker="off"); campaigns.set_campaign_tracker(cid, "")
    assert settings.enabled(cid) is False
    campaigns.set_campaign_tracker(cid, "on");   assert settings.enabled(cid) is True

def test_bad_campaign_value_rejected(...):
    with pytest.raises(ValueError): campaigns.set_campaign_tracker(cid, "maybe")

def test_campaign_tracker_routes(client):
    r = client.get(f"/api/campaigns/{cid}/tracker"); assert r.json() == {"setting": "", "enabled": True}
    r = client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "off"})
    assert r.json() == {"setting": "off", "enabled": False}
    assert client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "x"}).status_code == 400

def test_public_config_carries_tracker_keys(client):
    cfg = client.get("/api/config").json()
    assert cfg["tracker"] == "on" and cfg["perception_rider"] == "on"
```

- [ ] **Step 2:** Run `PYTHONPATH=backend/src $PY -m pytest -q backend/tests/test_tracker_settings.py`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement the files above.
- [ ] **Step 4:** Run the same command plus `backend/tests/test_routes.py`, which holds `_public_config` to `ConfigUpdate`, and `backend/tests/test_lock_domain_guard.py backend/tests/test_import_guard.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Add the tracker on/off settings, global and per campaign"`

---

### Task 3: Field definitions and layers

**Files:**
- Create: `backend/src/grimoire/store/tracker/paths.py`:
  - `world_layer_path(wid) -> Path` returns `worlds_paths.world_root(wid) / "tracker.json"`.
  - `campaign_layer_path(cid) -> Path` returns `campaigns_paths.campaign_root(cid) / "tracker.json"`.
  - `scene_dir(cid, identity) -> Path` returns `campaign_root(cid) / "tracker" / identity`.
  - `scene_layer_path(cid, identity) -> Path` returns `scene_dir(...) / "fields.json"`.
  - `response_key(rid, vid) -> str` returns `f"r-{rid}-{vid}"`.
  - `post_key(post_id) -> str` returns `f"p-{post_id}"`.
  - `KEY_RE = re.compile(r"\A(r-[0-9a-f]{32}-[0-9a-f]{32}|p-[0-9a-f]{32})\Z")` and `valid_key(key) -> bool`.
- Create: `backend/src/grimoire/store/tracker/fields.py` (contents below).
- Modify: `store/locks.py`. Add `"store.tracker.fields"` to `DOMAIN_MODULES` with a reason comment: it writes `campaign.md`-adjacent layer files and scene layers under the campaign lock.
- Modify: `routes/tracker.py`. Add the field-layer routes:
  - `GET`/`PUT /worlds/{wid}/tracker-fields`. GET returns `{"layer": dict, "effective": [field], "inherited": [field]}`, where `inherited` is the built-in list.
  - `GET`/`PUT /campaigns/{cid}/tracker-fields`. Here `inherited` is the world's effective list.
  - `GET`/`PUT /campaigns/{cid}/scenes/{sid}/tracker-fields`. Here `inherited` is the campaign's effective list.
  - The PUT body model is `TrackerLayer(BaseModel): fields: list[dict] = []; change: dict = {}; off: list[str] = []`.
  - `FieldLayerError` maps to 400 with its message.
- Tests: `backend/tests/test_tracker_fields.py`

`fields.py` contents to pin:

```python
VISIBLE_MOODS = ("admiration", "amusement", "anger", "annoyance", "approval", "caring",
    "confusion", "curiosity", "desire", "disappointment", "disapproval", "disgust",
    "embarrassment", "excitement", "fear", "gratitude", "grief", "joy", "love",
    "nervousness", "neutral", "optimism", "pride", "realization", "relief", "remorse",
    "sadness", "surprise")

DEFAULT_FIELDS = (
  {"key": "clothing", "label": "Clothing", "type": "text", "aware": "present",
   "hint": "What they are wearing right now, briefly. Change it only when the post shows clothing put on, taken off or damaged."},
  {"key": "position", "label": "Position", "type": "text", "aware": "present",
   "hint": "Where they are in the space, relative to people and things."},
  {"key": "pose", "label": "Pose", "type": "text", "aware": "present",
   "hint": "Body posture right now."},
  {"key": "holding", "label": "Holding", "type": "text", "aware": "present",
   "hint": "What is in their hands right now; empty when nothing."},
  {"key": "condition", "label": "Condition", "type": "list", "aware": "present",
   "hint": "Visible physical states, one short item each (soaked, bleeding, limping, flushed, exhausted). Remove an item when it stops being true."},
  {"key": "visible_mood", "label": "Visible mood", "type": "enum", "aware": "present",
   "options": list(VISIBLE_MOODS),
   "hint": "The demeanour others can read from face, voice and body right now."},
  {"key": "true_mood", "label": "True mood", "type": "text", "aware": "self",
   "hint": "What they actually feel, including toward others present."},
  {"key": "intent", "label": "Intent", "type": "text", "aware": "self",
   "hint": "What they are trying to get out of this scene right now."},
  {"key": "concealed", "label": "Concealed", "type": "text", "aware": "self",
   "hint": "Something on them or about them that others do not know."},
  {"key": "attention", "label": "Attention", "type": "text", "aware": "present",
   "hint": "Who or what they are focused on right now."},
)
```

Signatures:

- `class FieldLayerError(ValueError)`
- `validate_layer(layer: dict, *, scene: bool = False) -> dict` returns the normalized `{"version": 1, "fields": [...], "change": {...}, "off": [...]}` and raises `FieldLayerError` when:
  - a key does not match `^[a-z][a-z0-9_]{0,31}$`;
  - a type is not in `TYPES`;
  - `aware` is not in `("present", "self")`;
  - an `enum` has no non-empty `options`, or a non-enum has `options`;
  - the layer adds a key that already exists;
  - `change` touches `key`;
  - `scene=True` and `change` is non-empty.
- `apply_layer(base: list[dict], layer: dict) -> list[dict]`: switched-off fields are kept but marked `"off": True`, so stored values still have labels; changes are merged; additions are appended.
- `read_layer(path: Path) -> dict` returns `{}` on a missing or garbled file and never raises.
- `world_fields(wid) -> list[dict]`, `campaign_fields(cid) -> list[dict]`, and `effective(cid, sid) -> list[dict]`. The last reads the scene layer via `identity.scene_identity`; a `None` identity means no scene layer.
- Writers:
  - `write_world_layer(wid, layer)` uses `atomic.write_text`; there is no campaign lock because it is not campaign-scoped.
  - `write_campaign_layer(cid, layer)` and `write_scene_layer(cid, sid, layer)` take `locks.campaign_lock(cid)`; the scene one uses `identity.ensure_identity`.
  - All three call `validate_layer` first.
- `active(fields) -> list[dict]` returns the fields without `off`.
- `digest(fields) -> str` returns the first 16 hex of the sha256 of `json.dumps(fields, sort_keys=True)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_defaults_are_the_ten_fields_with_28_moods():
    assert [f["key"] for f in fields.DEFAULT_FIELDS] == ["clothing", "position", "pose", "holding",
        "condition", "visible_mood", "true_mood", "intent", "concealed", "attention"]
    assert len(fields.VISIBLE_MOODS) == 28

def test_world_then_campaign_then_scene_layering(home):          # home: Realm/Saltmarch/scene
    fields.write_world_layer(wid, {"off": ["attention"], "change": {"pose": {"label": "Stance"}}})
    fields.write_campaign_layer(cid, {"fields": [{"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": "h"}]})
    fields.write_scene_layer(cid, sid, {"off": ["clothing"]})
    eff = {f["key"]: f for f in fields.effective(cid, sid)}
    assert eff["attention"]["off"] and eff["clothing"]["off"] and eff["pose"]["label"] == "Stance"
    assert "rage" in eff and [f["key"] for f in fields.active(list(eff.values()))].count("rage") == 1

def test_scene_layer_may_not_redefine(home):
    with pytest.raises(fields.FieldLayerError):
        fields.write_scene_layer(cid, sid, {"change": {"visible_mood": {"type": "text"}}})

@pytest.mark.parametrize("bad", [
    {"fields": [{"key": "Bad Key", "label": "x", "type": "text", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "number", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "enum", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "clothing", "label": "x", "type": "text", "aware": "self", "hint": ""}]},
    {"change": {"pose": {"key": "stance"}}}])
def test_invalid_layers_rejected(bad):
    with pytest.raises(fields.FieldLayerError): fields.validate_layer(bad)

def test_garbled_layer_reads_empty(home):
    paths.campaign_layer_path(cid).write_text("{nope", encoding="utf-8")   # test-only raw write
    assert fields.read_layer(paths.campaign_layer_path(cid)) == {}

def test_layer_routes_round_trip(client): ...  # PUT world layer → GET campaign shows it under "inherited"; bad PUT → 400
```

- [ ] **Step 2:** Run `PYTHONPATH=backend/src $PY -m pytest -q backend/tests/test_tracker_fields.py`. Expected: FAIL.
- [ ] **Step 3:** Implement `paths.py`, `fields.py` and the routes.
- [ ] **Step 4:** Re-run with `backend/tests/test_atomic_guard.py backend/tests/test_paths_guard.py backend/tests/test_import_guard.py backend/tests/test_lock_domain_guard.py backend/tests/test_route_order.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Add tracker field definitions with world, campaign and scene layers"`

---

### Task 4: Post ids on user posts

**Files:**
- Modify: `store/scenes/serialize.py:353`. Append `"post_id"` to `RESPONSE_METADATA`.
- Modify: `store/scenes/write.py:78`. The new signature is `append_message(cid, sid, role, content, speaker=None, post_id: str | None = None) -> int`. With `post_id` set, the block is built with `serialize._message_block({"role": role, "speaker": speaker, "content": content, "post_id": post_id})`; without it, the current `_block` path is unchanged.
- Modify: `routes/scenes.py:748` (`_chat_run`). Before the append, compute `post_id = uuid.uuid4().hex if store.tracker.settings.enabled(cid) else None` and pass `post_id=post_id`. Scheduling comes in Task 8.
- Modify: `frontend/src/api/types.ts:342`. Add `post_id?: string` to `Message`.
- Test: `backend/tests/test_tracker_post_ids.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_post_id_round_trips_through_every_rewrite(home):
    i = scenes.append_message(cid, sid, "user", "Hello.", post_id="a" * 32)
    assert scenes.read_scene(cid, sid)["messages"][i]["post_id"] == "a" * 32
    scenes.edit_message(cid, sid, i, "Hello again.")
    assert scenes.read_scene(cid, sid)["messages"][i]["post_id"] == "a" * 32

def test_plain_append_carries_no_post_id(home):
    i = scenes.append_message(cid, sid, "user", "Hello.")
    assert scenes.read_scene(cid, sid)["messages"][i] == {"role": "user", "content": "Hello."}

def test_chat_stamps_a_post_id_only_when_tracking(client): ...
    # tracker on → the user message has a 32-hex post_id; PUT /campaigns/{cid}/tracker off → next user post has none
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `test_tracker_post_ids.py backend/tests/test_scene_store.py backend/tests/test_routes.py backend/tests/test_replay_store.py`. Expected: PASS. Any route test asserting exact user-message dicts must now either set `tracker` off in its setup or assert on the fields it cares about; prefer the latter, and record each such edit in the commit body.
- [ ] **Step 5:** `git add -A && git commit -m "Give new user posts a stable post id for the tracker"`

---

### Task 5: Records, index and transcript walk

**Files:**
- Modify: `store/responses.py`. Add `variants_by_response(cid: str, sid: str) -> dict[str, tuple[str | None, list[str]]]`, mapping each rid to (active variant id, all variant ids), read under the campaign lock.
- Create: `backend/src/grimoire/store/tracker/records.py`. Its imports are only `atomic`, `locks`, `campaigns.paths` and `tracker.paths`; it must never import `store.scenes`, so `scenes.lifecycle` can import it. Every function takes `(cid, identity, ...)`:
  - `read_index(cid, identity) -> dict[str, dict]`. It returns the `entries` map. A missing or garbled `index.json` is rebuilt from the `*.json` snapshot files present: each one becomes `{"status": "ok", "changed": [], "flags": {"upstream_changed": False, "text_changed": False}}`.
  - `read_snapshot(cid, identity, key) -> dict | None` returns the file body `{"version": 1, "snapshot": {...}, "fields_digest", "model", "at"}`.
  - `mark_pending(cid, identity, key) -> None`
  - `save(cid, identity, key, snapshot: dict, *, changed: list[list], fields_digest: str, model: str) -> None`. It writes the snapshot file first, then the index entry (`status "ok"`, `changed`, cleared flags).
  - `mark_failed(cid, identity, key, error: str) -> None`
  - `set_flags(cid, identity, keys: list[str], flag: str) -> None`, where `flag ∈ ("upstream_changed", "text_changed")`; unknown keys are ignored.
  - `discard(cid, identity, keys: list[str]) -> None` removes the files and index entries.
  - `drop(cid, identity) -> None` removes the whole scene directory (`shutil.rmtree`, `ignore_errors=False`, missing ok).
  - Every mutator wraps its body in `with locks.campaign_lock(cid):`.
- Create: `backend/src/grimoire/store/tracker/walk.py`. It imports `scenes.read`, `scenes.identity`, `scenes.serialize` (for `SYNTHETIC_SPEAKERS`), `responses`, `appearances.paths`, `appearances.cast` and `tracker.records`/`paths`:
  - `ordered_keys(cid, sid) -> list[tuple[int, str]]`. It yields one `(index, key)` per trackable post in transcript order:
    - a user message with `post_id` gives `p-…`;
    - each `response_id` gives `r-<rid>-<active vid>` at the index of that response's **last** message;
    - synthetic speakers and posts without ids are skipped.
  - `key_at(cid, sid, index) -> str | None`
  - `index_of(cid, sid, key) -> int | None`
  - `state_before(cid, sid, index) -> tuple[str | None, dict]` is the snapshot of the latest `ok` key at a message index strictly below `index`, or `(None, {})`.
  - `current(cid, sid) -> tuple[str | None, dict]`
  - `present_at(cid, sid, index) -> set[str]` returns refs `"characters:<id>"`/`"pcs:<id>"` whose presence interval covers `index`, using the predicate `start <= i and (end is None or i < end)`. An actor in `scene_cast` with no intervals for this scene counts as present.
  - `roster(cid, sid) -> dict[str, str]` maps ref to name from `appearances.cast.scene_cast`.
  - `flag_edited(cid, sid, index) -> None`: the key at `index` gets `text_changed`, and every later key gets `upstream_changed`.
  - `flag_after(cid, sid, index) -> None`: every key strictly after `index` gets `upstream_changed`.
  - `prune(cid, sid) -> int` discards every index entry and file whose key is neither a current `p-<post_id>` nor `r-<rid>-<vid>` for a rid in the transcript and a vid among that rid's variants. Inactive variants are kept for swiping. It returns the count.
  - The writers (`flag_edited`, `flag_after`, `prune`) take `locks.campaign_lock(cid)`.
- Modify: `store/scenes/lifecycle.py` `delete_scene`. Before the transcript unlink, insert `ident = identity.scene_identity(cid, sid)` and `if ident: tracker_records.drop(cid, ident)`, with the import `from ..tracker import records as tracker_records`.
- Modify: `store/locks.py`. Add `"store.tracker.records"` and `"store.tracker.walk"` to `DOMAIN_MODULES` with reasons.
- Test: `backend/tests/test_tracker_records.py`

- [ ] **Step 1: Write the failing tests**

  Build scenes in the store directly. Use `responses.new_round`/`prepare`/`save_variant`, or simply `scenes.append_reply` followed by `responses.migrate`, to get response ids.

```python
def test_save_then_read(home):  # snapshot file + index entry; status ok, flags false
def test_garbled_index_rebuilds_from_files(home):
    records.save(cid, ident, key, SNAP, changed=[], fields_digest="d", model="m")
    (paths.scene_dir(cid, ident) / "index.json").write_text("{", encoding="utf-8")
    assert records.read_index(cid, ident)[key]["status"] == "ok"
def test_ordered_keys_uses_active_variant_and_last_part(home): ...
def test_state_before_skips_failed_and_pending(home):
    # keys k1 ok, k2 failed, k3 pending → state_before(index_of(k3)) returns k1's snapshot
def test_flag_edited_marks_text_and_later(home): ...
def test_prune_keeps_inactive_variants_drops_cut_posts(home):
    # two variants of r1, cut a later user post → r1's both vids kept, p-key of cut post gone
def test_delete_scene_drops_tracker_dir_and_recycled_sid_starts_clean(home):
    # delete scene, create a new scene of the same title → walk.current(...) == (None, {})
def test_present_at_follows_intervals(home):  # appear Mara at 0, Winifred at 2, leave Mara at 4
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `test_tracker_records.py backend/tests/test_scene_store.py backend/tests/test_lock_domain_guard.py backend/tests/test_import_guard.py backend/tests/test_atomic_guard.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Store per-post tracker snapshots keyed by scene identity"`

---

### Task 6: Merge and view (pure)

**Files:**
- Create: `backend/src/grimoire/store/tracker/merge.py`. It imports `fields` only.
  - `MAX_TEXT = 200`
  - `class TrackerReplyError(ValueError)`
  - `parse_reply(text: str) -> dict`. It strips a surrounding ```` ```json ```` fence, takes the first balanced `{…}` object and returns `{"changes": dict, "awareness": dict}`, with missing keys becoming `{}`. It raises `TrackerReplyError` when there is no JSON object or the top level is not an object.
  - `apply_reply(prev: dict, reply: dict, fields: list[dict], roster: dict[str, str], present: set[str], match: Callable[[str, list[str]], str | None]) -> tuple[dict, list[list]]`:
    - It returns the new snapshot and `changed` as `[[ref, key, new_value], ...]`.
    - **Starting point:** a deep copy of `prev`. Every ref in `present` gets an entry; a new one is `{"present": True, "fields": {}}`. Every other existing ref gets `present: False`.
    - **Name resolution:** names are resolved by `match(name, list(roster.values()))`, then mapped back to the ref. Only refs in `present` are writable.
    - **What is dropped:** unknown fields, switched-off fields, enum values outside the options, and a non-list given for a list field.
    - **Normalizing:** text values are collapsed to one line and truncated at `MAX_TEXT`, and so is each list item. Empty list items are removed.
    - **Applying a value:** an unchanged value is a no-op. A changed value replaces `{"value", "aware"}` with the field's default awareness (`"present"`, or `[]` for `self`) and drops `set_by`.
    - **Awareness keys** (`"Name.field"`) add resolved, present refs (excluding the owner) to that value's list. They are ignored when the value's awareness is `"present"`.
  - `apply_edit(prev: dict, edits: dict, fields: list[dict]) -> tuple[dict, list[list]]`:
    - `edits` is `{ref: {key: {"value": ..., "aware": "present" | [refs]}}}`.
    - It uses the same validation, and raises `ValueError` on an invalid edit instead of dropping it.
    - A changed value gets `"set_by": "user"`. An aware-only change also sets `set_by`.
- Create: `backend/src/grimoire/store/tracker/view.py`:
  - `lines_for(snapshot: dict, fields: list[dict], viewer: str | None, roster: dict[str, str]) -> list[dict]` returns `[{"name": str, "own": bool, "values": [{"label": str, "text": str, "private": bool}]}]`.
    - It covers only `present: True` entries and active fields with a non-empty value. List values are joined with `", "`.
    - **`viewer` is `None` or `"grimoire"` (narrator mode):** every value, with `private` true when the awareness is a list.
    - **`viewer` is a ref:** that ref's entry comes first with `own: True` and every value. Each other entry shows `present`-aware values, plus list-aware values whose list contains `viewer` (`private: True`).
    - Refs that are not in `roster` are skipped.
- Test: `backend/tests/test_tracker_merge.py`

- [ ] **Step 1: Write the failing tests**

```python
FIELDS = list(fields.DEFAULT_FIELDS)
ROSTER = {"characters:mara": "Mara", "characters:winifred": "Winifred"}
PRESENT = {"characters:mara", "characters:winifred"}

def test_parse_reply_tolerates_prose_and_fences():
    assert merge.parse_reply('Sure:\n```json\n{"changes": {}}\n```')["changes"] == {}
    with pytest.raises(merge.TrackerReplyError): merge.parse_reply("no json here")

def test_changes_apply_with_default_awareness():
    snap, changed = merge.apply_reply({}, {"changes": {"Mara": {"visible_mood": "fear", "concealed": "a letter"}}},
                                      FIELDS, ROSTER, PRESENT, scenes.match_name)
    m = snap["characters:mara"]["fields"]
    assert m["visible_mood"] == {"value": "fear", "aware": "present"} and m["concealed"]["aware"] == []
    assert ["characters:mara", "visible_mood", "fear"] in changed

def test_invalid_entries_are_dropped():
    reply = {"changes": {"Nobody": {"pose": "x"}, "Mara": {"visible_mood": "furious", "bogus": "y", "condition": "wet"}}}
    snap, changed = merge.apply_reply({}, reply, FIELDS, ROSTER, PRESENT, scenes.match_name)
    assert changed == [] and snap["characters:mara"]["fields"] == {}

def test_changed_value_clears_user_marker_and_resets_awareness():
    prev = {"characters:mara": {"present": True, "fields": {"true_mood": {"value": "calm", "aware": ["characters:winifred"], "set_by": "user"}}}}
    snap, _ = merge.apply_reply(prev, {"changes": {"Mara": {"true_mood": "afraid"}}}, FIELDS, ROSTER, PRESENT, scenes.match_name)
    assert snap["characters:mara"]["fields"]["true_mood"] == {"value": "afraid", "aware": []}

def test_unmentioned_user_value_survives():
    prev = {"characters:mara": {"present": True, "fields": {"clothing": {"value": "grey cloak", "aware": "present", "set_by": "user"}}}}
    snap, changed = merge.apply_reply(prev, {"changes": {}}, FIELDS, ROSTER, PRESENT, scenes.match_name)
    assert snap["characters:mara"]["fields"]["clothing"]["set_by"] == "user" and changed == []

def test_awareness_addition():
    prev = {"characters:mara": {"present": True, "fields": {"concealed": {"value": "a letter", "aware": []}}}}
    snap, _ = merge.apply_reply(prev, {"awareness": {"Mara.concealed": ["Winifred"]}}, FIELDS, ROSTER, PRESENT, scenes.match_name)
    assert snap["characters:mara"]["fields"]["concealed"]["aware"] == ["characters:winifred"]

def test_departed_character_kept_not_present(): ...
def test_text_truncated_at_200(): ...
def test_apply_edit_marks_user_and_rejects_bad_enum(): ...

def test_view_filters_by_viewer():
    snap = {... mara: concealed aware [] , visible_mood present; winifred: true_mood aware ["characters:mara"] ...}
    mara = view.lines_for(snap, FIELDS, "characters:mara", ROSTER)
    assert mara[0]["name"] == "Mara" and mara[0]["own"]
    win = next(l for l in mara if l["name"] == "Winifred")
    assert any(v["private"] for v in win["values"])        # known to Mara
    wview = view.lines_for(snap, FIELDS, "characters:winifred", ROSTER)
    mara_seen = next(l for l in wview if l["name"] == "Mara")
    assert all(v["label"] != "Concealed" for v in mara_seen["values"])
    narr = view.lines_for(snap, FIELDS, "grimoire", ROSTER)
    assert any(v["label"] == "Concealed" and v["private"] for l in narr for v in l["values"])
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Merge tracker replies and filter state by who is aware of it"`

---

### Task 7: The update call: route, prompt, templates

**Files:**
- Modify: `store/routing.py` `ROUTES`. Add, after the `summary` route:

  ```python
  Route("tracker", "Scene state tracker",
        "One small call after every post to keep each character's tracked state current.",
        ("tracker-update",), True)
  ```

- Create: `backend/src/grimoire/store/tracker/prompt.py`:
  - `newcomer(cid: str, ref: str) -> dict` returns `{"ref", "name", "description", "state"}`:
    - **`characters`:** `description` is the card's `data["description"]` at the locked version, read through `characters.read_card(overlay.char_root(cid, id), id, appearances_versions.locked_version(cid, "characters", id))`.
    - **`pcs`:** `description` is `persona["summary"]` and `persona["description"]` joined with a blank line.
    - **`state`:** the `current_state` of `playstate.read_state(campaign_root, id, kind)`. The `kind` parameter arrives in Task 12; until then pass characters only and leave `state` empty for `pcs`.
    - Every read failure yields an empty string.
  - `build_messages(fields: list[dict], prior: dict, roster: dict[str, str], present: set[str], newcomers: list[dict], context_posts: list[dict], post: dict) -> list[dict]` returns `[{"role": "system", "content": render("tracker/update_system.j2")}, {"role": "user", "content": render("tracker/update_user.j2", ...)}]`. `post` and `context_posts` items are `{"speaker": str, "content": str}`.
- Create: `templates/tracker/update_system.j2`. Pin this text; the cassette matches on its first sentence:

```
You maintain the scene state tracker for a roleplay scene.
You receive the tracked fields, each present character's current values, and one new post. Report only what the new post changes.

Rules:
- Change a value only when the new post shows the change. Inference from what the post shows is fine; invention is not.
- Values marked (user-set) were entered by the player: change one only if the post explicitly changes it.
- For a character marked new, fill every field the material supports from their description, their standing state and the posts; leave the rest out.
- An enum field takes exactly one of its listed options.
- A list field is returned whole, as its complete new list.
- Keep each value to a short phrase.
- Under "awareness", name the characters who have just learned a private value, keyed "Name.field".

Reply with one JSON object and nothing else:
{"changes": {"<character name>": {"<field>": <value>}}, "awareness": {"<character name>.<field>": ["<character name>"]}}
Reply {"changes": {}} when nothing changed.
```

- Create: `templates/tracker/update_user.j2`. It renders these sections in order:
  - `# Fields`: one line each, `key (type[, options]) [private by default] — hint`.
  - `# Characters present`: one block per present character. The block starts with `Name` (and `(new)` for newcomers); each value line is `field: value`, with `(user-set)` and `(private, known to: …)` annotations.
  - `# New characters`: description and standing state, only when there are newcomers.
  - `# Earlier posts (context only)`
  - `# New post`: `Speaker: content`.

  Document both templates' variables in `templates/README.md` under a new `tracker/` heading.
- Modify: `backend/tests/test_llm_fakes.py` `_rendered_prompts()`. Append `prompts.render("tracker/update_system.j2")`.
- Modify: `backend/tests/fixtures/llm/campaign_flow.json`. Add the entry `{"when": {"system_contains": "You maintain the scene state tracker"}, "reply": "{\"changes\": {}}"}`.
- Test: `backend/tests/test_tracker_prompt.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_prompt_lists_fields_values_markers_and_post(home):
    prior = {"characters:mara": {"present": True, "fields": {"clothing": {"value": "grey cloak", "aware": "present", "set_by": "user"},
                                                              "concealed": {"value": "a letter", "aware": ["characters:winifred"]}}}}
    msgs = prompt.build_messages(list(fields.DEFAULT_FIELDS), prior, ROSTER, PRESENT,
                                 [{"ref": "characters:winifred", "name": "Winifred", "description": "A tall archivist.", "state": ""}],
                                 [{"speaker": "Mara", "content": "Earlier."}], {"speaker": "Winifred", "content": "She enters."})
    user = msgs[1]["content"]
    assert "grey cloak (user-set)" in user and "known to: Winifred" in user
    assert "Winifred (new)" in user and "A tall archivist." in user
    assert user.rstrip().endswith("Winifred: She enters.")
    assert "visible_mood (enum: admiration, amusement" in user

def test_routing_knows_the_task():
    assert routing.TASK_ROUTE["tracker-update"] == "tracker"
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `test_tracker_prompt.py backend/tests/test_llm_fakes.py backend/tests/test_routing_guard.py`. The routing guard's "registered task is named by a call site" test will fail until Task 8 adds the `meter("tracker-update", …)` call; mark that one parametrized case `xfail(strict=True)` with reason `"call site lands in Task 8"`, and remove the mark in Task 8. Also run `$PY scripts/verify_templates.py`. Expected: PASS apart from the strict xfail.
- [ ] **Step 5:** `git add -A && git commit -m "Add the tracker route and its update prompt"`

---

### Task 8: Scheduling and the update run

**Files:**
- Modify: `backend/src/grimoire/routes/tracker.py`. Add:
  - `schedule(app, cid: str, sid: str, key: str, client) -> None`:
    1. Return immediately unless `store.tracker.settings.enabled(cid)`.
    2. `records.mark_pending(cid, ensure_identity(cid, sid), key)`.
    3. `run = runs.reserve_background(app, cid, sid, "tracker-update")`; return if it is `None`.
    4. `runs.start_computing(app, run, lambda: _update(app, cid, sid, key, client))`.

    Start failures are handled exactly as `scenes._start_background` handles them (log, `release_before_start`), plus `records.mark_failed`.
  - `schedule_response(app, cid, sid, rid, client) -> None`: looks up the active variant through `responses.variants_by_response`, then calls `schedule` with `paths.response_key(rid, vid)`. It is a no-op when there is no active variant.
  - `schedule_untracked(app, cid, sid, client) -> None`: schedules every key from `walk.ordered_keys` that has no index entry, in order. It is **only** for a scene that was empty before the call (opener, greeting).
  - `_scene_lock(app, identity) -> asyncio.Lock`: one lock per identity, in `app.state.tracker_locks` (created lazily under a `threading.Lock`).
  - `async def _update(app, cid, sid, key, client) -> dict`. Inside `async with _scene_lock(...)`:
    1. `conn = _require_connection("tracker-update", cid)`. An `HTTPException` leads to `mark_failed` and returns `{"state": "failed", "error": run_error(exc)}`.
    2. `prep = await run_in_threadpool(_prepare, cid, sid, key)`. It returns `None` when the key is no longer in `walk.ordered_keys`; in that case return `{"state": "landed", "result": {"skipped": True}}`.
    3. Build the messages with `prompt.build_messages`.
    4. `with store.usage.meter("tracker-update", campaign=cid, scene=sid, post=prep["post"]) as m: text = await client.complete(messages, conn, m.usage)`.
    5. `merge.parse_reply` → `merge.apply_reply`. `LLMError` or `TrackerReplyError` leads to `mark_failed(error=str(exc))` and returns failed.
    6. `await run_in_threadpool(_commit, ...)`, then return `{"state": "landed", "result": {"changed": len(changed)}}`.
  - `_prepare(cid, sid, key) -> dict | None` gathers, under one `campaign_lock` hold:
    - `index`, `post` (the user post's own index for `p-` keys, or the response record's round `post` for `r-` keys) and `fields = fields.effective(cid, sid)`;
    - `prior` (from `walk.state_before(cid, sid, index)`), `roster`, `present = walk.present_at(cid, sid, index)`;
    - `newcomers`: present refs absent from `prior`, built via `prompt.newcomer`;
    - `context_posts`: the two preceding non-synthetic messages;
    - `post_msg`: the post text; all parts of a response are joined with blank lines.
  - `_commit(cid, sid, key, snapshot, changed, digest, model) -> None`: takes the campaign lock, re-checks that the key is in `walk.ordered_keys` (otherwise it returns without writing), calls `records.save`, then calls `store.revision.bump(cid)` inside the same hold. A detached run writes after the activity middleware has stamped its request (CLAUDE.md, write token).
- Modify: the trigger points. Each is one call, placed after the persisting write and outside any campaign-lock hold that `schedule` itself does not need:
  - `routes/scenes.py` `_chat_run`, after `append_message` with a `post_id`: `tracker_routes.schedule(request.app, cid, sid, store.tracker.paths.post_key(post_id), client)`.
  - `routes/character_turns.py` `_frames`, after `outcome.persisted(at)` at L501, and the same in `_pause` (L397) and `_rescue.save` (L611): `if at is not None: tracker_routes.schedule_response(request.app, cid, sid, record["id"], client)`. Thread `request`/`app` into any of these that lack it.
  - `routes/character_turns.py` `_reroll_frames`: when `accepted` is true, call `tracker_routes.schedule_response(app, cid, sid, rid, client)`. Pass `app` in from `regenerate_response`.
  - `routes/greetings.py` `post_first_post` and the start-from-greeting route that calls `store.playing.start_from_greeting`. Add `client=Depends(get_llm)` where missing. After the write, call `store.responses.migrate_if_needed(cid, sid)` and then `tracker_routes.schedule_untracked(request.app, cid, sid, client)`.
- Modify: `backend/tests/test_draft_suppression.py`. Make `_WRITERS` accept dotted module paths (resolve with `functools.reduce(getattr, mod.split("."), store)`), and add `("tracker.records", "save")` and `("tracker.records", "mark_pending")`.
- Remove the Task 7 `xfail` mark.
- Test: `backend/tests/test_tracker_flow.py`

- [ ] **Step 1: Write the failing tests**

  Wait for runs with the `test_turn_follow_ups.py` pattern: find `cls == "background"` and `kind == "tracker-update"` runs via `client.app.state.runs.for_subject(("scene", cid, identity))`, then `run.terminal.wait(timeout=10)`. Use `from_entries` with a `system_contains: "You maintain the scene state tracker"` entry ahead of a catch-all turn reply.

```python
def test_a_send_tracks_the_user_post_and_the_reply(client): ...
    # tracker reply {"changes": {"Mara": {"visible_mood": "joy"}}}; send; settle
    # GET .../tracker shows both keys ok; walk.current(...)[1]["characters:mara"]["fields"]["visible_mood"]["value"] == "joy"

def test_updates_in_one_scene_run_in_order(client):
    # tracker replies, in order: set clothing "cloak", then {} ; the second update's prior has clothing "cloak"
    # assert via FakeLLM.requests: the 2nd tracker request's user message contains "clothing: cloak"

def test_failure_marks_failed_and_play_continues(client):
    # tracker entry replies "not json" → index status failed; the chat response still 200 and the reply landed

def test_reroll_gets_its_own_record_and_swipe_back_is_free(client):
    # send; regenerate the response; both r-<rid>-<vid> keys ok; activate the first variant;
    # ordered_keys now names the first vid; no new tracker run started by activation

def test_update_for_a_cut_post_is_discarded(client):
    # use llm_fakes.HeldOpenRouter for the tracker call; cut the post (DELETE messages-from) while held; release;
    # index has no entry for the key and no snapshot file exists

def test_update_survives_a_rename(client): ...   # rename the scene while held; record lands; GET under new sid shows it
def test_campaign_off_schedules_nothing(client): ... # PUT tracker off; send; no tracker-update runs; no tracker dir
def test_opener_and_greeting_posts_are_tracked(client): ...   # first-post adoption → every key ok
def test_draft_never_writes_a_record(...)  # covered by test_draft_suppression's instrumented writers
```

- [ ] **Step 2:** Run `PYTHONPATH=backend/src $PY -m pytest -q backend/tests/test_tracker_flow.py`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `test_tracker_flow.py backend/tests/test_draft_suppression.py backend/tests/test_routing_guard.py backend/tests/test_character_turns.py backend/tests/test_turn_follow_ups.py backend/tests/test_routes.py backend/tests/test_import_guard.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Run a tracker update after every post, in order per scene"`

---

### Task 9: Tracker API and edit, cut and swipe hooks

**Files:**
- Modify: `routes/tracker.py`. Add the scene routes:
  - `GET /campaigns/{cid}/scenes/{sid}/tracker` returns:
    ```
    {"enabled": bool,
     "names": roster,
     "keys": [{"index": int, "key": str}],
     "entries": index,
     "moods": {ref: visible_mood},
     "labels": {field_key: label}}
    ```
    `moods` comes from `walk.current`, and `labels` from `fields.effective`.
  - `PUT .../records/{key}`, `retry` and `rerun-from` answer **409** `{"detail": "tracker_off"}` when `settings.enabled(cid)` is false. Records stay readable through the GET routes.
  - `GET .../tracker/records/{key}` returns `{"key", "status", "flags", "snapshot", "fields", "names", "error"?}`. It is 404 when the key is unknown or invalid (`paths.valid_key`).
  - `PUT .../tracker/records/{key}` takes body `TrackerEdit(BaseModel): edits: dict`. Under the lock it reads the snapshot, applies `merge.apply_edit`, saves with `model="user"`, and calls `walk.flag_after(index)`. It is 400 on `ValueError` and 404 on an unknown key, and returns the GET-record body.
  - `POST .../tracker/records/{key}/retry` schedules the key. After a successful save, the update path calls `walk.flag_after(index)` when the record had been `failed`.
  - `POST .../tracker/records/{key}/rerun-from` schedules that key and every later key in `ordered_keys`, in order.
- Modify these routes; each gains one call after its store write:
  - `routes/scenes.py` `put_scene_message` (replacing the removed turnstate line) and the retcon route: `store.tracker.walk.flag_edited(cid, sid, index)`.
  - The cut route (`DELETE` messages-from), the replay begin/cancel/accept routes, and the response-delete route in `character_turns`: `store.tracker.walk.prune(cid, sid)`, plus `walk.flag_after(index)` where an index is known.
  - The variant-activate route in `character_turns`: `store.tracker.walk.flag_after(cid, sid, <index of that response's last message>)`.
- Test: `backend/tests/test_tracker_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_summary_lists_keys_in_order_with_entries(client): ...
def test_edit_marks_user_and_flags_later(client):
    # three tracked posts; PUT edit on the first: clothing "red coat"
    # record: clothing.set_by == "user"; entries of posts 2 and 3: flags.upstream_changed True; nothing scheduled
def test_edit_rejects_bad_enum(client):   # 400
def test_editing_post_text_flags_it_and_later(client): ...   # PUT messages/{i} → text_changed on i, upstream on later
def test_cut_prunes_records(client): ...
def test_rerun_from_reschedules_in_order_and_clears_flags(client): ...
def test_retry_turns_failed_into_ok(client): ...
def test_unknown_key_404(client): ...
def test_edits_refused_when_tracker_off(client): ...   # PUT tracker off → PUT record 409 "tracker_off"; GET record still 200
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `test_tracker_routes.py backend/tests/test_scene_freeze.py backend/tests/test_routes.py backend/tests/test_route_order.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Expose tracker records and keep them consistent with edits, cuts and swipes"`

---

### Task 10: Scene state section and the leak test

**Files:**
- Create: `templates/scene/sections/tracker_state.j2`. It takes `tracker_lines` (the `view.lines_for` output) and `tracker_narrator: bool`. It renders nothing when `tracker_lines` is empty; otherwise:

```
# Scene state
What each character present looks like and is doing right now, as tracked from the scene so far.
{% for c in tracker_lines %}{{ c.name }}{% if c.own %} (you){% elif not tracker_narrator %} (what you can perceive){% endif %} — {% for v in c.values %}{{ v.label }}: {{ v.text }}{% if v.private %}{% if tracker_narrator %} (private: never state or imply in narration){% elif not c.own %} (known to you){% endif %}{% endif %}{% if not loop.last %}; {% endif %}{% endfor %}
{% endfor %}
```

  Strip trailing whitespace the way the other sections do.
- Modify: `store/context/assemble.py`:
  - Add `Section("tracker_state", "Scene state", "scene/sections/tracker_state.j2", pack.SPOTLIGHT, except_opener=True)` directly after `character_state`.
  - `_CAST_SECTIONS = ("character_state", "tracker_state")`.
  - In `_assemble`, inside the existing `best_effort_campaign_lock` block, read `tracker_key, tracker_snap = walk.current(cid, sid)` when `tracker_settings.enabled(cid)`; otherwise use `(None, {})`. Also read `tracker_fields = fields.effective(cid, sid)` and `tracker_roster = walk.roster(cid, sid)`.
  - The data dict gets:
    - `"tracker_lines": view.lines_for(tracker_snap, tracker_fields, viewer, tracker_roster)`, where `viewer` is the NPC `actor_ref` for an actor-scoped call and `None` for `"grimoire"` or no actor;
    - `"tracker_narrator": viewer is None`.

    This is computed **before** `cast` is narrowed, and it uses the full roster.

  > **Scope note:** this reads the state at the transcript tail, which is the post being answered for a fresh turn. A reroll's compose reads the same tail, because the response being replaced is removed or re-composed by the existing reroll path before compose. Verify this in Step 1's reroll test. If the reroll path composes with the old response still in place, pass the response's index down and use `walk.state_before`.
- Modify: `store/context/layout.py` `_RENAMED`: add `"transient_state": "tracker_state"`.
- Modify: `scripts/verify_templates.py`:
  - Seed one tracker record for the fixture scene with `records.save`, with values for two characters including one private value.
  - Mirror the `tracker_lines` computation.
  - Add `"scene/sections/tracker_state.j2"` after `character_state.j2` in `rendered_system`'s `names` (non-opener only).
- Modify: `templates/README.md`. Document `tracker_lines` and `tracker_narrator` in the `scene/` vars.
- Modify: `evals/cases.py` `grade_turn_taking`. Add `graders.grade_prompt_section(ctx["messages"], "tracker_state", "scene/sections/tracker_state.j2", tracker_lines=..., tracker_narrator=...)` with the lines the case's build seeded, and seed a record in that case's `build`. Update `backend/tests/test_eval_graders.py` if it enumerates checks.
- Test: `backend/tests/test_tracker_context.py`

- [ ] **Step 1: Write the failing tests**

  Use the `cast_scene` fixture shape from `test_actor_context.py` (Mara and Winifred). Add Seraphine as a PC with `appearances.appear(cid, sid, "pcs", "seraphine", ..., "player", narrate=False)`. Seed records directly with `records.save` against keys from `walk.ordered_keys` after appending a user post with a `post_id`.

```python
def test_no_actor_prompt_leaks_another_actors_private_value(cast_scene):
    # every character and the PC hold a private value  <REF>_PRIVATE ; Mara's private value lists Winifred
    for ref in ("characters:mara", "characters:winifred"):
        text = str(context.compose_turn(cid, sid, actor_ref=ref)[0])
        for owner in ("characters:mara", "characters:winifred", "pcs:seraphine"):
            secret = owner + "_PRIVATE"
            allowed = owner == ref or (owner == "characters:mara" and ref == "characters:winifred")
            assert (secret in text) == allowed

def test_narrator_sees_all_with_private_label(cast_scene):
    text = str(context.compose_turn(cid, sid, actor_ref="grimoire")[0])
    assert "characters:winifred_PRIVATE (private: never state or imply in narration)" in text

def test_section_absent_when_tracker_off(cast_scene): ...   # write_config(tracker="off") → "# Scene state" not in text
def test_reroll_compose_reads_state_before_the_replaced_post(...): ...  # see the scope note above
def test_frozen_prompt_carries_the_state_block(client):
    # after a tracked turn, the next response's snapshot_ref prompt (responses.get(..., private=True)["snapshot"])
    # contains the "# Scene state" text that compose produced
def test_saved_layout_keeps_transient_position(): ...   # layout._migrate([{"id": "transient_state", "enabled": False}]) → tracker_state disabled
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run:
  - `test_tracker_context.py backend/tests/test_actor_context.py backend/tests/test_context.py backend/tests/test_eval_graders.py`
  - `$PY scripts/verify_templates.py`
  - the frozen sweep. Expect **no** `snapshot.json` change, because the frozen home has no tracker records. If it changes, the section rendered non-empty with no records — fix that, don't regenerate.

  Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Give each prompt the scene state its speaker can perceive"`

---

### Task 11: Slim the perception rider

**Files:**
- Modify: `templates/scene/response_actor.j2`:
  - Wrap the perception paragraph (L41-63) in `{% if perception_rider %}…{% endif %}`.
  - Replace its "what this actor can perceive about others' state" guidance with one sentence: `For what you can see of the others right now, rely on the Scene state section; list here only what you heard or saw happen.`
  - Keep the `heard_or_seen` / `known` / `unknown` shape and the 80-word cap.
- Modify: `store/context/assemble.py`. Add `"perception_rider": config.perception_rider()` to the data dict.
- Modify: `routes/character_turns.py` L456 and L829. Use `ResponseWatcher(perception=actor != "grimoire" and store.config.perception_rider())`, and the matching expression with `record["actor_ref"]`.
- Modify: `scripts/verify_templates.py` (pass `perception_rider=True`) and `templates/README.md`.
- Test: extend `backend/tests/test_actor_context.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_perception_rider_switch(cast_scene):
    on = str(context.compose_turn(cid, sid, actor_ref="characters:mara")[0])
    assert "```perception" in on and "rely on the Scene state section" in on
    store.write_config(perception_rider="off")
    off = str(context.compose_turn(cid, sid, actor_ref="characters:mara")[0])
    assert "```perception" not in off
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `backend/tests/test_actor_context.py backend/tests/test_character_turns.py` and `$PY scripts/verify_templates.py`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Narrow the perception rider to events and make it switchable"`

---

### Task 12: Absorb handoff and PC state.md

**Files:**
- Modify: `store/playstate.py`. Add `kind: str = "characters"` as the last parameter of `state_path`, `read_state` and `write_state`, so the path is `root / kind / id / "state.md"`. Raise `ValueError` unless kind ∈ `("characters", "pcs")`.
- Modify: `store/casefile.py:142`. Pass the actor's `kind`; this fixes reading a PC's state from `characters/`.
- Modify: `routes/scenes.py` `_absorb_snapshot`:
  - Return `(epoch, scene, tracked)`, where `tracked = view.lines_for(walk.current(cid, sid)[1], fields.effective(cid, sid), None, walk.roster(cid, sid))` is read under the same lock hold.
  - Thread it through `_Prepared` into `store.absorb.build_prompt(..., tracked_snapshot=tracked)`.
- Modify: `store/absorb/prompt.py`. Add the kwarg `tracked_snapshot: list | None = None` and pass it to `user.j2`.
- Modify: `templates/absorb/user.j2`. After the "Current character state" head block, add a "Final tracked state (as the scene ended; private values marked):" block, one line per character: `- Name: Label: text; Label: text (private)`.
- Modify: `templates/absorb/system.j2:9`. Extend the `character_state_edits` sentence: ids may be `characters/<id>` or `pcs/<id>`; for a player character only `current_state` applies; prefer the final tracked state over re-deriving it from the transcript.
- Modify: `store/absorb/materializer.py` `materialize` (L352-388):
  - Accept `pcs` ids, requiring `pcs.read_persona` to succeed.
  - For pcs, compose `before`/`after` with `knows=""` and `suspects=""`.
  - Emit `_character_state_edit(cid, kind, char_id, before, after)`, now taking `kind`, with id `f"character_state:{char_id}"` for characters and `f"character_state:pcs:{char_id}"` for pcs, and `target {"kind": kind, "id": char_id}`. The label uses the persona name.
- Modify: `store/absorb/apply.py:195`. Use `playstate.write_state(croot, target["id"], after, kind=target.get("kind", "characters"))`.
- Modify: `store/undo.py:276,325`. Pass the target kind through.
- Modify: `store/tracker/prompt.py` `newcomer`. Read the PC `state` with `kind="pcs"`.
- Modify: `scripts/verify_templates.py`. Render the new absorb block with a fixture.
- Test: `backend/tests/test_tracker_absorb.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_absorb_prompt_carries_final_tracked_state(client):
    # tracked scene with Mara visible_mood "fear" and a private concealed value; POST absorb with a FakeLLM capturing requests
    # the absorb request's user message contains "Final tracked state" and "Visible mood: fear" and "(private)"

def test_pc_state_edit_is_staged_and_applied(home):
    parsed = {"character_state_edits": [{"id": "pcs/seraphine", "current_state": "Sprained wrist."}], ...}
    edits = materialize(cid, sid, parsed)
    e = next(e for e in edits if e["target"] == {"kind": "pcs", "id": "seraphine"})
    apply_edits(cid, sid, [e]);  assert playstate.read_state(croot, "seraphine", "pcs")["current_state"] == "Sprained wrist."

def test_next_scene_newcomer_reads_pc_state(home):
    playstate.write_state(croot, "seraphine", "## Current state\nSprained wrist.", kind="pcs")
    assert prompt.newcomer(cid, "pcs:seraphine")["state"] == "Sprained wrist."
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `test_tracker_absorb.py backend/tests/test_absorb*.py backend/tests/test_undo*.py backend/tests/test_casefile*.py`, `$PY scripts/verify_templates.py`, and the frozen sweep. If the absorb templates' rendering with no tracked state changed `snapshot.json`, render nothing when `tracked_snapshot` is empty and re-run. Expected: PASS with no snapshot change.
- [ ] **Step 5:** `git add -A && git commit -m "Hand the final tracked state to absorb and give player characters a state file"`

---

### Task 13: Frontend API surface

**Files:**
- Modify: `frontend/src/api/types.ts`. Add:

```ts
export type TrackerAware = "present" | string[];
export type TrackerValue = { value: string | string[]; aware: TrackerAware; set_by?: "user" };
export type TrackerActor = { present: boolean; fields: Record<string, TrackerValue> };
export type TrackerSnapshot = Record<string, TrackerActor>;
export type TrackerField = { key: string; label: string; type: "text" | "list" | "enum";
  aware: "present" | "self"; hint: string; options?: string[]; off?: boolean };
export type TrackerEntry = { status: "pending" | "ok" | "failed";
  changed: [string, string, string | string[]][];
  flags: { upstream_changed: boolean; text_changed: boolean }; error?: string };
export type TrackerSummary = { enabled: boolean; names: Record<string, string>;
  keys: { index: number; key: string }[]; entries: Record<string, TrackerEntry>; moods: Record<string, string>;
  labels: Record<string, string> };
export type TrackerRecord = { key: string; status: TrackerEntry["status"]; flags: TrackerEntry["flags"];
  snapshot: TrackerSnapshot; fields: TrackerField[]; names: Record<string, string>; error?: string };
export type TrackerLayer = { fields?: TrackerField[]; change?: Record<string, Partial<TrackerField>>; off?: string[] };
export type TrackerLayerBundle = { layer: TrackerLayer; effective: TrackerField[]; inherited: TrackerField[] };
export type TrackerEdits = Record<string, Record<string, { value: string | string[]; aware: TrackerAware }>>;
```

  Also add `tracker` and `perception_rider` (string) to `Config` and `ConfigUpdate`.
- Modify: `frontend/src/api/client.ts`. Add to `api`:
  - `getTracker(cid, sid)`: GET `/api/campaigns/${cid}/scenes/${sid}/tracker` with `{fresh: true}`.
  - `getTrackerRecord(cid, sid, key)`
  - `editTrackerRecord(cid, sid, key, edits: TrackerEdits)`: PUT `{edits}`.
  - `retryTracker(cid, sid, key)` and `rerunTrackerFrom(cid, sid, key)`: POSTs.
  - `getTrackerFields(scope)` / `setTrackerFields(scope, layer)`, where `scope = {kind: "world"; wid} | {kind: "campaign"; cid} | {kind: "scene"; cid; sid}`.
  - `getCampaignTracker(cid)` / `setCampaignTracker(cid, setting)`
- Modify: `frontend/src/testkit/campaignMocks.tsx`. Add `getTracker`, `getTrackerRecord`, `editTrackerRecord`, `retryTracker`, `rerunTrackerFrom`, `getTrackerFields` and `setTrackerFields` as `vi.fn()`.
- Modify: `frontend/src/testkit/campaignHarness.tsx` `installCampaignMocks()`. Add the default `(api.getTracker as any).mockResolvedValue({enabled: true, names: {}, keys: [], entries: {}, moods: {}, labels: {}})`.

- [ ] **Step 1:** `cd frontend && npm run typecheck`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npx vitest run src/routes/CampaignView.test.tsx`. Expected: PASS (the mocks are in place).
- [ ] **Step 3:** `git add -A && git commit -m "Add the tracker API surface to the frontend client"`

---

### Task 14: The Tracker disclosure on each post

**Files:**
- Create: `frontend/src/components/tracker/TrackerDisclosure.tsx`. Export `TrackerDisclosure({ cid, sid, trackerKey, entry, names, labels, onChanged }: { cid: string; sid: string; trackerKey: string; entry: TrackerEntry | undefined; names: Record<string, string>; labels: Record<string, string>; onChanged: () => void })`.
  - **Rendering:** a `<details className="tracker">` with a `<summary>`, like `SavedThinking`.
  - **Summary text:** `summaryText(entry, names, labels)`, exported for tests:
    - `undefined` → `"Tracker · updating…"`
    - `pending` → `"Tracker · updating…"`
    - `failed` → `"Tracker · untracked"`, followed by a **Retry** button outside the `summary`
    - `ok` with an empty `changed` → `"Tracker · no change"`
    - otherwise `"Tracker · " + changes`, where each change reads `Name: Label → value`, joined with `"; "`. List values are joined with `", "`, and labels come from the summary's `labels`, falling back to the field key.
    - A flag appends `" · earlier state changed"` or `" · text changed since tracked"`.
  - **On first open:** `api.getTrackerRecord`. It renders one block per present actor, then absent ones, using `TrackerValues`. Fields in this post's `changed` get `className="changed"`.
  - **Buttons:** **Edit** (opens `TrackerEditForm`) and **Re-run tracker from here** (`api.rerunTrackerFrom` then `onChanged()`).
- Create: `frontend/src/components/tracker/TrackerValues.tsx`. A read-only list of one actor's values. Its awareness tag is nothing for `"present"`, `private` for `[]`, and `known to: <names>` otherwise.
- Create: `frontend/src/components/tracker/TrackerEditForm.tsx`:
  - **Props:** `{ record: TrackerRecord; onSave: (edits: TrackerEdits) => Promise<void>; onCancel: () => void }`.
  - **Inputs by field type:**
    - `text` → `<input>`
    - `list` → a comma-separated `<input>` split on save
    - `enum` → `<select>` with an empty option
  - **Awareness picker:** each value gets a `<select>` with "Everyone present" (`"present"`), "Private" (`[]`) and one option per other present character, which adds that character.
  - **Save:** submits only the changed values.
- Modify: `frontend/src/components/play/TranscriptPost.tsx`:
  - `TranscriptContext` gains `tracker: TrackerSummary | null` and `trackerKeys: Record<number, string>`. Pass `trackerKey={ctx.trackerKeys[index]}` and `trackerEntry`.
  - In the non-editing branch, after `RenderedMarkdown`, render `TrackerDisclosure` when `trackerKey` is set and `(m.role === "user" || lastOfResponse)`.
  - Keep the props primitive or identity-stable, per the memo comments there.
- Modify: `frontend/src/routes/CampaignView.tsx`:
  - State: `const [tracker, setTracker] = useState<TrackerSummary | null>(null)`.
  - Load it with `api.getTracker(loaded.cid, loaded.sid)` whenever `loaded` changes and after every turn settles (where `getSceneUsage` is refreshed).
  - While any entry is `pending`, re-fetch every 2 s using a `setTimeout` chain cleared on unmount or scene change. No `window` key listeners.
  - `trackerKeys = useMemo(() => Object.fromEntries((tracker?.keys ?? []).map((k) => [k.index, k.key])), [tracker])`. Add both to `transcriptCtx` and its deps.
- Modify: `frontend/src/index.css`. Style `.tracker` like `.thinking`, and `.tracker .changed { font-weight: 600 }`.
- Tests: `frontend/src/components/tracker/TrackerDisclosure.test.tsx`, plus one case in `routes/CampaignView.test.tsx`.

- [ ] **Step 1: Write the failing tests**

```ts
vi.mock("../../api/client", () => ({ api: { getTrackerRecord: vi.fn(), editTrackerRecord: vi.fn(),
  retryTracker: vi.fn(), rerunTrackerFrom: vi.fn() } }));

test("summary states", () => {
  expect(summaryText(undefined, {}, {})).toBe("Tracker · updating…");
  expect(summaryText({ status: "ok", changed: [], flags: F }, {}, {})).toBe("Tracker · no change");
  expect(summaryText({ status: "ok", changed: [["characters:mara", "visible_mood", "fear"]], flags: F },
    { "characters:mara": "Mara" }, { visible_mood: "Visible mood" })).toBe("Tracker · Mara: Visible mood → fear");
  expect(summaryText({ status: "failed", changed: [], flags: F }, {}, {})).toBe("Tracker · untracked");
  expect(summaryText({ status: "ok", changed: [], flags: { ...F, upstream_changed: true } }, {}, {}))
    .toBe("Tracker · no change · earlier state changed");
});
test("fetches only on open and renders read-only values with awareness", async () => { ... no textarea/input until Edit });
test("Edit reveals the form and Save sends only changed values", async () => { ... editTrackerRecord called with {"characters:mara": {"clothing": {...}}} });
test("Retry on a failed entry calls retryTracker", async () => { ... });
// CampaignView.test.tsx:
test("a tracked post shows its Tracker summary", async () => { getTracker resolves keys [{index: 0, key: "p-…"}] → "Tracker · no change" visible });
```

- [ ] **Step 2:** Run `cd frontend && npx vitest run src/components/tracker src/routes/CampaignView.test.tsx`. Expected: FAIL.
- [ ] **Step 3:** Implement. **Step 4:** Run again, plus `npm run typecheck` and `npm run lint` if present (the eslint ratchet is otherwise run in Task 18). Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Show and edit each post's tracked state in a Tracker disclosure"`

---

### Task 15: Cast tile mood and the dossier's Now section

**Files:**
- Modify: `frontend/src/components/play/CastColumn.tsx`. Add the prop `moods: Record<string, string>` (ref → visible mood). `Tile` renders `<span className="cast-mood">{mood}</span>` under `.cast-state` when `moods[`${tile.kind}:${tile.id}`]` is set.
- Modify: `frontend/src/components/play/DossierColumn.tsx`. Add the props `tracker: { cid: string; sid: string; key: string | null; ref: string } | null` and `onTrackerChanged: () => void`.
  - When `tracker?.key` is set, render a `column-section` headed "Now", with the label `scene state` in the `column-count` slot.
  - It fetches `getTrackerRecord` and shows `TrackerValues` for `tracker.ref`, with an **Edit** button opening `TrackerEditForm` restricted to that actor.
- Modify: `frontend/src/routes/CampaignView.tsx`. Pass `moods={tracker?.moods ?? {}}` to `CastColumn`. Pass `tracker={{cid, sid: activeId, key: last ok key from tracker.keys/entries, ref}}` to `DossierColumn`, with `onTrackerChanged` re-fetching the summary.
- Modify: `frontend/src/index.css`. `.cast-mood` is small and muted. Inside `.shell.phone` it is `display: none`, mirroring `.cast-state`.
- Tests: `CastColumn.test.tsx` (create it if absent) and `DossierColumn.test.tsx`.

- [ ] **Step 1: Write the failing tests**

```ts
test("tile shows the visible mood", () => { render(<CastColumn ... moods={{"characters:mara": "fear"}} />); expect(screen.getByText("fear")) });
test("dossier Now section shows current values and Edit reveals the form", async () => { ... });
test("no Now section without a tracked key", () => { ... queryByText("Now") is null });
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** `git add -A && git commit -m "Show current mood on cast tiles and current state in the dossier"`

---

### Task 16: Field-layer editors

**Files:**
- Create: `frontend/src/components/tracker/TrackerFieldsEditor.tsx`. Props: `{ scope: { kind: "world"; wid: string } | { kind: "campaign"; cid: string } }`. It follows the list/detail pattern in CLAUDE.md (`.editor` / `.editor-list` / `.editor-body`, `mode: "view" | "edit"`):
  - **Rail:** a `+ New field` button and one `.row` per effective field, with switched-off fields shown dimmed.
  - **View:** `.detail-view` with `.detail-main` (h3 label, hint) and a `.detail-sidebar` holding:
    - **Edit**;
    - chips for type, default awareness, options and source (built-in / world / this layer);
    - for inherited fields, **Switch off** or **Switch on**, and **Revert** when this layer changes the field.
  - **Edit form:** label, hint, type, options (enum; a comma list), default awareness. For an inherited field, saving writes a `change` entry; for a new one, a `fields` entry.
  - **Save:** `api.setTrackerFields(scope, nextLayer)`, then reload and return to view.
- Create: `frontend/src/components/tracker/SceneTrackerPanel.tsx`. Props: `{ cid, sid }`. It shows a checkbox per inherited field (on/off → the scene layer's `off`) and a small "+ Scene-only field" form (key, label, type, hint, awareness).
- Modify:
  - `frontend/src/routes/worldPaths.ts`: add `"tracker"` to `FLAT_SECTIONS` and to the `SectionTarget` `at` union.
  - `frontend/src/routes/WorldView.tsx`: add `"tracker"` to `IndexKey`; add `{ key: "tracker", label: "Tracker" }` to the "Writing" group, filtered out on the campaign shape like tags; give `countOf` the field count via `getTrackerFields`; add a world-only dispatch `section === "tracker" && <TrackerFieldsEditor scope={{kind: "world", wid}} />`.
  - `frontend/src/routes/worldPaths.test.ts`: add `tracker` to the round-trip cases.
- Modify: `frontend/src/routes/CampaignHub.tsx`. The `panel` union gains `"tracker"`, with a "Tracker" button in the Settings `ColumnSection`. The panel renders:
  - a tri-state `<select aria-label="Tracker">` with Inherit, On and Off (`getCampaignTracker`/`setCampaignTracker`);
  - `<TrackerFieldsEditor scope={{kind: "campaign", cid}} />`.
- Modify: `frontend/src/routes/CampaignView.tsx`. Add a "Tracker" scene panel: the `showTracker` flag, an `openPanel` entry, a `closePanels` entry, a menu row, and the slot `{!focus && showTracker && activeId && <div className="panel-slot"><SceneTrackerPanel key={`${cid}:${activeId}`} cid={cid} sid={activeId} /></div>}`. Update the "seven" comment.
- Tests: `TrackerFieldsEditor.test.tsx`, `SceneTrackerPanel.test.tsx`, `worldPaths.test.ts`.

- [ ] **Step 1: Write the failing tests**

```ts
test("clicking a row shows the read-only view with its sidebar", async () => { ... no input/textarea; "Edit" present });
test("Edit reveals the form", async () => { ... });
test("+ New field opens the form directly", async () => { ... });
test("switching off an inherited field writes it to off", async () => { setTrackerFields called with {off: ["attention"], ...} });
test("scene panel toggles a field off for this scene only", async () => { ... scope {kind: "scene"} });
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `npx vitest run src/components/tracker src/routes/worldPaths.test.ts src/routes/WorldView*.test.tsx src/routes/CampaignHub*.test.tsx src/routes/CampaignView.test.tsx`. Expected: PASS.
- [ ] **Step 5:** `git add -A && git commit -m "Edit tracker fields per world, campaign and scene"`

---

### Task 17: Settings page

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`:
  - Add `"tracker"` and `"perception_rider"` to `DRAFT_FIELDS`.
  - Add a section `{ id: "tracker", group: "What the model sees", label: "Scene tracker", fields: ["tracker", "perception_rider"] }` in place of the removed transient section, with its `SectionId` member and a status case (`"on"`/`"off"` from `draft.tracker`).
  - Render two `checkbox-row`s using the existing on/off pattern:
    - "Track each character's state after every post" (`tracker`);
    - "Ask each character to note what it heard or saw before replying" (`perception_rider`).
  - The copy paragraph says that the tracker makes one extra call per post on the "Scene state tracker" model route, which can be pointed at a cheaper model.
- Test: `frontend/src/routes/ConfigView.test.tsx`

- [ ] **Step 1: Write the failing test**

```ts
test("tracker settings toggle and save", async () => { ... uncheck "Track each character's state after every post" → saveConfig called with tracker: "off" });
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. **Step 5:** `git add -A && git commit -m "Add the scene tracker settings to the configuration page"`

---

### Task 18: Full gate

- [ ] **Step 1:** `make check PY=backend/.venv/bin/python`. Fix every failure at its root. Never skip a test.
- [ ] **Step 2:** If ruff, mypy or eslint report improvements, run `make baseline PY=backend/.venv/bin/python` and include the regenerated `lint-baselines/*.json` files.
- [ ] **Step 3:** Re-run the frozen sweep and confirm `git diff backend/tests/fixtures/frozen_campaign/` is empty. If a change is intended, review the text and commit `snapshot.json` alone, with a message saying what moved and why.
- [ ] **Step 4:** Spot-check end to end with the `verify` skill, against an isolated store and the mocked provider:
  - send a post and confirm both Tracker disclosures fill in;
  - edit a value and confirm later posts show "earlier state changed";
  - swipe a reroll and confirm the disclosure follows it;
  - switch the campaign tracker off.
- [ ] **Step 5:** `git add -A && git commit -m "Bring lint baselines up to date for the scene tracker"`. Only make this commit if Step 2 changed files.
