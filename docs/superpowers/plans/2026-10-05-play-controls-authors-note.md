# Play controls V — author's notes with depth injection: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standing author's notes at three levels (campaign, scene, character), each inserted into the projected history at a chosen post depth on an every-N cadence. The inspector shows them in a row of their own, and the play view can edit them.

**Architecture:** A new per-campaign JSON store `store/authors_notes.py` (scene notes keyed by identity) and a pure helper module `store/context/authors_note.py`. The helper decides which notes apply, where each one goes, and how the history projects around it. `_assemble` hands the note positions to `_prepare` through a side channel (`a["notes"]`). `pack.pack` uses those positions to keep its floor honest, and `_breakdown` uses them to move the notes out of the `history` row into an `authors_note` row. Openers carry their notes in `before_post`. The routes live in a new `routes/authors_notes.py`. The UI is an `AuthorsNotesPanel` in the scene inspector.

**Tech Stack:** FastAPI + pytest (`backend/`), Jinja2 templates (`templates/`), React + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-authors-note-design.md`. Its "Gate resolutions" are binding and override the body where the two differ.

**Depends on:** step 2 (hide-from-context: `scenes.serialize.in_context`, `scenes.serialize.without_excluded`, `scenes.write.set_excluded`, `excluded` message metadata) and step 3 (branching: `store/branch.py` `branch_scene`). Both land before this plan runs.

## Global Constraints

- A note is `{"text": str, "depth": int, "every": int}`:
  - `text` is at most 2000 characters and may span lines. Empty text means no note, and saving empty text clears that level.
  - `depth` is posts from the end, `0..50`, default `4`. `0` means after the last post.
  - `every` is `1..50`, default `1`.
- Storage is `<campaign>/authors_notes.json`, shaped `{"campaign": note, "scenes": {"<scene identity>": note}, "characters": {"characters:mara": note}}`.
  - Scene notes are keyed by **identity**, never by sid.
  - Character keys are the full actor ref.
- `store.authors_notes` goes in `locks.DOMAIN_MODULES`. Every write happens under `locks.campaign_lock(cid)` through `atomic.write_text`.
- Reads take no lock and are lenient: a missing file, garbled JSON or the wrong shape all read as no notes, and none of them fails a turn.
- `store.authors_notes` imports nothing from `store.scenes`.
- **Turn number** `note_turn` counts player posts (`role == "user"`) plus director-note lines (`serialize.is_director_note`) over the **full** scene transcript, before `observed_history`. Excluded posts are **included** in the count.
  - A note applies when `note_turn % every == 0`, with one explicit exception: `note_turn == 0` applies only `every == 1` notes.
- **Insertion** is built on `serialize.in_context(<history after actor.observed_history>)`.
  - The target index is `len - depth`, clamped to 0. Depth counts in-context posts.
  - `depth == 0` → `len`.
  - Otherwise the target snaps back to the nearest index `i <= target` where `history[i]["role"] == "user"` and either `i == 0` or `history[i-1]["role"] != "user"`. If there is no such index, the note goes at `0`.
  - Notes at the same point go in the order campaign, scene, character.
- **Which notes apply:** the campaign note, this scene's note, and the character note for `actor_ref`. The character note applies **only** when the composition is actor-scoped (`actor_ref not in (None, "grimoire")`).
- **Wire shape:** each note renders through `templates/scene/authors_note.j2` (vars `level`, `name`, `text`), is macro-expanded like the rest of the history, and goes out as `{"role": "system", "content": …}`.
  - Nothing extra rides on the wire message. The identity of each note travels in `a["notes"]` (indices into `a["history"]`).
- **Packing:** `HISTORY_FLOOR` counts non-note messages only. A note at the front of the trim is removed together with the post it sits before.
- **Inspector:** an `authors_note` row (label "Author's notes", tier `pack.HISTORY`) lists each note as applied, `trimmed` or `skipped (every N)` (0 tokens). The note text is removed from the `history` row, and `total_tokens` still counts it.
  - The row is emitted **only when at least one note is configured**, so the frozen-campaign snapshot does not move.
- **Openers:** only `every == 1` notes apply. They are placed in `before_post` after the opener prompt (and after prior contributions) and before the opener instruction. They are reserved through `extra` with the label `"Author's note — <level>"`.
- pydantic stays v1/v2-agnostic: the plain `BaseModel` `AuthorsNote {text: str = "", depth: int = 4, every: int = 1}`. Ranges are checked in the route (400). Responses are plain dicts.
- Imports go at module scope. Inside `store/`, bind submodules (`from ..scenes import identity as scenes_identity`), never names off a sibling package (`test_import_guard.py`).
- No new keyboard bindings. If one is ever added, it goes through `useHotkeys` only.
- Fixtures use only the placeholder names Mara, Winifred, Seraphine, Realm and Saltmarch.
- Ratchet gates: if a change resolves or adds a ruff, mypy or eslint finding, run `make baseline` and commit the new `lint-baselines/*.json` with the change.
- Test commands:
  - Backend: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`.
  - Frontend: `cd frontend && npx vitest run <files>`, then `npm run typecheck`.

## Review Focus

1. **The answered post must survive a tight budget.** A depth-0 note under a tight budget must not push out the post being answered. The floor counts non-notes (Task 4, `test_floor_ignores_notes`).
2. **A character's note stays in that character's call.** It must not reach another character's call or the narrator's (Task 5, `test_character_note_only_in_its_own_call`).
3. **A reroll replays the note the original turn saw**, even after the note was edited (Task 5, `test_reroll_replays_the_frozen_note`).
4. **Scene notes follow the scene, not the sid.** A scene note must survive a rename, and a recycled sid must not adopt a deleted scene's note (Task 1, `test_scene_note_follows_rename` / `test_delete_scene_drops_note`).
5. **A bad notes file never breaks a turn.** A garbled `authors_notes.json` must compose a turn with no notes and must not raise (Task 1 `test_read_is_lenient`, Task 5 `test_garbled_file_composes_without_notes`).

---

### Task 1: The note store

**Files:**
- Create: `backend/src/grimoire/store/authors_notes.py`
- Modify: `backend/src/grimoire/store/locks.py` (add `"store.authors_notes"` to `DOMAIN_MODULES`, with a one-paragraph reason comment like its neighbours)
- Modify: `backend/src/grimoire/store/__init__.py` (import `authors_notes` in the module list and add `"authors_notes"` to `__all__`, beside `"pins"`)
- Modify: `backend/src/grimoire/store/scenes/lifecycle.py`:
  - import `authors_notes` in the `from .. import (...)` block;
  - in `delete_scene`, beside the existing `tracker_records.drop(cid, ident)` after `p.unlink()`, call `authors_notes.drop(cid, ident)` in its own `try/except Exception: log.warning(...)`.
- Test: `backend/tests/test_authors_notes_store.py` (new)

**Interfaces:**
- Produces, in `store/authors_notes.py`:
  - `FILENAME = "authors_notes.json"`, `MAX_TEXT = 2000`, `DEPTH_MAX = 50`, `EVERY_MAX = 50`, `DEFAULT_DEPTH = 4`, `DEFAULT_EVERY = 1`
  - `normalize(raw: object) -> dict | None`: lenient. Returns `None` unless `raw` is a dict whose `text` is a non-empty `str` after `.strip()`. Otherwise returns `{"text": text[:MAX_TEXT], "depth": clamp(int(depth), 0, 50) or 4 if missing/bad, "every": clamp(int(every), 1, 50) or 1 if missing/bad}`.
  - `read(cid: str) -> dict`: always returns `{"campaign": dict | None, "scenes": {str: dict}, "characters": {str: dict}}`. Every entry goes through `normalize`, and invalid entries are dropped. It never raises for a missing file, `OSError`, `ValueError`, or a non-dict document.
  - `set_campaign(cid: str, note: dict | None) -> None`, `set_scene(cid: str, identity: str, note: dict | None) -> None`, `set_character(cid: str, ref: str, note: dict | None) -> None`: a `None` note, or one whose normalized form is `None`, removes the key.
  - `drop(cid: str, identity: str) -> None`
  - `copy_scene(cid: str, src_identity: str, dst_identity: str) -> None`: no-op when the source has no note.
  - `applies(note: dict, turn: int) -> bool`: `note["every"] == 1 if turn == 0 else turn % note["every"] == 0`.
- Every mutator does `with locks.campaign_lock(cid):`, then `read`, modify, and `atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")`.
  - `_path(cid)` is `campaigns_paths.campaign_root(cid) / FILENAME`, as in `pins._path`.
  - Decision: the mutators read leniently, so saving over a garbled file replaces it. These are short notes the player can retype, and refusing would leave the UI unable to repair the file. Say so in the module docstring.

- [ ] **Step 1: Write the failing tests** (`monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`; `wid = worlds.create_world("Realm")`; `cid = campaigns.create_campaign("Saltmarch", wid)`):

```python
NOTE = {"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1}

def test_read_is_lenient(...):
    assert authors_notes.read(cid) == {"campaign": None, "scenes": {}, "characters": {}}
    (campaigns.campaign_root(cid) / "authors_notes.json").write_text("{not json")
    assert authors_notes.read(cid)["campaign"] is None
    (campaigns.campaign_root(cid) / "authors_notes.json").write_text("[]")
    assert authors_notes.read(cid)["scenes"] == {}

def test_normalize_defaults_and_clamps():
    assert authors_notes.normalize({"text": "x"}) == {"text": "x", "depth": 4, "every": 1}
    assert authors_notes.normalize({"text": "  "}) is None
    assert authors_notes.normalize({"text": "x" * 2500})["text"] == "x" * 2000

def test_set_and_clear_each_level(...):
    authors_notes.set_campaign(cid, NOTE); authors_notes.set_character(cid, "characters:mara", NOTE)
    authors_notes.set_scene(cid, "a" * 32, NOTE)
    data = authors_notes.read(cid)
    assert data["campaign"] == NOTE and data["characters"]["characters:mara"] == NOTE
    assert data["scenes"]["a" * 32] == NOTE
    authors_notes.set_campaign(cid, {"text": ""})
    assert authors_notes.read(cid)["campaign"] is None

def test_applies_cadence():
    n3 = {**NOTE, "every": 3}
    assert [t for t in range(0, 10) if authors_notes.applies(n3, t)] == [3, 6, 9]
    assert authors_notes.applies(NOTE, 0) and not authors_notes.applies(n3, 4)

def test_scene_note_follows_rename(...):
    sid = scenes.create_scene(cid, "Mara"); ident = scenes.ensure_identity(cid, sid)
    authors_notes.set_scene(cid, ident, NOTE)
    new = scenes.rename_scene(cid, sid, "Winifred")
    assert scenes.scene_identity(cid, new) in authors_notes.read(cid)["scenes"]

def test_delete_scene_drops_note(...):
    # set a scene note, scenes.delete_scene(cid, sid) -> ident not in read(cid)["scenes"]

def test_copy_scene(...):
    # copy_scene(cid, src, dst) -> both keys hold NOTE; copy from a src with no note -> no dst key

def test_fork_copies_notes_and_cut_scenes_lose_theirs(...):
    # two scenes, a note on each and a campaign note; fork.fork_campaign(cid, "Saltmarch Redux", from_scene=first)["id"]
    # child read(): campaign note present, first scene's identity present, second's absent (cut via delete_scene)
```

- [ ] **Step 2: Run, verify FAIL** — `ImportError: cannot import name 'authors_notes'`.
- [ ] **Step 3: Implement** the module, the `DOMAIN_MODULES` entry, the facade export, and the fail-soft `drop` in `delete_scene`.
- [ ] **Step 4: Run** `test_authors_notes_store.py test_lock_domain_guard.py test_atomic_guard.py test_paths_guard.py test_import_guard.py test_fork_store.py` — PASS.
- [ ] **Step 5: Commit** `feat(store): author's notes at campaign, scene and character level`.

### Task 2: Branching copies the scene note

**Files:**
- Modify: `backend/src/grimoire/store/branch.py` (`branch_scene`)
- Test: `backend/tests/test_branch_store.py` (the step-3 suite; add one case)

**Interfaces:**
- Consumes: `authors_notes.copy_scene(cid, src_identity, dst_identity)` from Task 1.
- Changes: `branch_scene(cid, sid, through, *, title="") -> str` stays unchanged in signature. Once the sibling's fresh identity exists, and inside the same campaign-lock hold as the rest of the build, it calls `authors_notes.copy_scene(cid, <source identity>, <sibling identity>)`. Add `authors_notes` to `branch.py`'s `from .. import (...)` block.
  - If a build fails part-way, it already deletes the sibling through `scenes.lifecycle.delete_scene`, which drops the copied note (Task 1). No extra cleanup is needed.

- [ ] **Step 1: Write the failing test** `test_branch_copies_scene_note`:
  - Set a note on the source identity, then call `new = branch.branch_scene(cid, sid, 1)`.
  - `authors_notes.read(cid)["scenes"][scenes.scene_identity(cid, new)] == NOTE`, and the source key is unchanged.
  - `scenes.delete_scene(cid, sid)` leaves the sibling's note.
- [ ] **Step 2: Run, verify FAIL** — `KeyError`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_branch_store.py test_authors_notes_store.py` — PASS.
- [ ] **Step 5: Commit** `feat(branch): a branch keeps its source's author's note`.

### Task 3: The template and the placement helper

**Files:**
- Create: `templates/scene/authors_note.j2`:

```jinja
{%- if level == "character" -%}
[Author's note for {{ name }}: {{ text }}]
{%- else -%}
[Author's note: {{ text }}]
{%- endif -%}
```

- Create: `backend/src/grimoire/store/context/authors_note.py`. Its module docstring states:
  - the corrected provider caveat (gate 6): strict OpenAI-compatible endpoints turn a mid-history system message into a user turn, and the Claude agent path lifts it into the system prompt;
  - the cached-prefix cost of a note that moves deeper each turn.
- Modify: `backend/src/grimoire/store/context/__init__.py` (add `authors_note` to the `from . import (...)` list, alphabetically)
- Test: `backend/tests/test_authors_note_context.py` (new)

**Interfaces:**
- Produces, in `store/context/authors_note.py` (imports: `from ... import prompts`, `from .. import authors_notes`, `from ..scenes import serialize as scenes_serialize`, `from . import story`):
  - `note_turn(messages: list[dict]) -> int`: `sum(1 for m in messages if m.get("role") == "user" or scenes_serialize.is_director_note(m))`.
  - `applicable(notes: dict, identity: str | None, actor_ref: str | None, actor_name: str, turn: int) -> tuple[list[dict], list[dict]]`: returns `(applied, skipped)`, each in the order campaign, scene, character.
    - Each entry is `{"level": str, "name": str, "text": str, "depth": int, "every": int}`. `name` is `actor_name` for a character note and `""` otherwise.
    - The character note is considered only when `actor_ref not in (None, "grimoire")`.
    - The scene note is considered only when `identity` is truthy.
  - `split_point(history: list[dict], depth: int) -> int`: the snap rule from Global Constraints.
  - `render(note: dict) -> str`: `prompts.render("scene/authors_note.j2", level=note["level"], name=note["name"], text=note["text"])`.
  - `inject(history: list[dict], applied: list[dict]) -> tuple[list[dict], list[dict]]`: takes in-context raw messages and returns `(projected, positions)`.
    - Group the notes by `split_point`, walk the points in ascending order, and project each segment with `story._project_history(history[prev:k])`. Then append `{"role": "system", "content": render(n)}` for each note at `k`, and project the tail.
    - `positions` are the applied entries plus `"index"` (an index into `projected`) and `"status": "applied"`.
    - Because each point is a role boundary, segment-wise projection merges exactly as whole projection would.

- [ ] **Step 1: Write the failing tests** (pure, with hand-built message dicts; `P(n) = {"role": "user", "content": f"P{n}"}`, `R(n) = {"role": "assistant", "content": f"R{n}", "speaker": "Mara"}`):

```python
H = [P(1), R(1), P(2), R(2), P(3), R(3)]
def test_split_point_depths():
    assert split_point(H, 4) == 2          # before the fourth-most-recent post
    assert split_point(H, 0) == 6          # after the last post
    assert split_point(H, 50) == 0         # clamped to the start
    assert split_point(H, 3) == 2          # R2 snaps back to P2
    assert split_point([R(1), R(2)], 1) == 0
    assert split_point([P(1), R(1), P(2), P(3)], 1) == 2   # P3 is mid-run; snaps to the run's start

def test_note_turn_counts_excluded_and_director_lines():
    msgs = [P(1), {**P(2), "excluded": "2026-10-05T00:00:00"}, R(1),
            {"role": "assistant", "content": "steer", "speaker": scenes_serialize.DIRECTOR_SPEAKER}]
    assert note_turn(msgs) == 3

def test_applicable_order_and_scoping():
    notes = {"campaign": C, "scenes": {"i" * 32: S}, "characters": {"characters:mara": M}}
    applied, _ = applicable(notes, "i" * 32, "characters:mara", "Mara", 1)
    assert [n["level"] for n in applied] == ["campaign", "scene", "character"]
    assert applicable(notes, "i" * 32, "grimoire", "Grimoire", 1)[0][-1]["level"] == "scene"
    assert applicable(notes, "i" * 32, None, "", 1)[0][-1]["level"] == "scene"

def test_applicable_skips_by_cadence():
    applied, skipped = applicable({"campaign": {**C, "every": 3}, "scenes": {}, "characters": {}}, None, None, "", 4)
    assert applied == [] and skipped[0]["every"] == 3
    assert applicable({"campaign": {**C, "every": 3}, ...}, None, None, "", 0)[0] == []

def test_inject_is_its_own_message_and_never_merged():
    projected, pos = inject(H, [{"level": "campaign", "name": "", "text": "Storm.", "depth": 4, "every": 1}])
    assert projected[pos[0]["index"]] == {"role": "system", "content": "[Author's note: Storm.]"}
    assert "P2" in projected[pos[0]["index"] + 1]["content"]
    assert projected[:pos[0]["index"]] == story._project_history(H[:2])
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** the template and module.
- [ ] **Step 4: Run** `test_authors_note_context.py test_import_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(context): place author's notes by depth and cadence`.

### Task 4: The packer keeps notes off the floor

**Files:**
- Modify: `backend/src/grimoire/store/context/pack.py` (`pack`)
- Test: `backend/tests/test_context.py` (beside the existing `context_pack.pack` tests around line 3452)

**Interfaces:**
- Changes: `pack.pack(sections, history, reserved=0, budget=None, compose=None, count=None, notes: frozenset[int] = frozenset()) -> dict`. `notes` holds indices into the input `history` that are author's-note messages.
- Trim loop:
  - Keep going while `total > budget` and the count of **non-note** messages left is `> HISTORY_FLOOR`.
  - Pop the front message. If it was a note, keep popping until a non-note has been popped as well (the note goes out with the post it precedes).
  - Add `"notes_trimmed": int` to the result. The unbounded early return carries `"notes_trimmed": 0`.
- `history_trimmed` keeps its meaning: every message popped from the front, notes included.

- [ ] **Step 1: Write the failing tests** using `count=lambda t: 10` (message cost 15, empty system 10) and `sections=[]`:

```python
def M(c, r="user"): return {"role": r, "content": c}

def test_floor_ignores_notes():
    hist = [M("P1"), M("R1", "assistant"), M("P2"), M("NOTE", "system")]
    out = context_pack.pack([], hist, budget=20, count=lambda t: 10, notes=frozenset({3}))
    assert [m["content"] for m in out["history"]] == ["R1", "P2", "NOTE"]
    assert out["notes_trimmed"] == 0

def test_note_trimmed_with_the_post_it_precedes():
    hist = [M("NOTE", "system"), M("P1"), M("R1", "assistant"), M("P2"), M("R2", "assistant")]
    out = context_pack.pack([], hist, budget=70, count=lambda t: 10, notes=frozenset({0}))
    assert [m["content"] for m in out["history"]] == ["R1", "P2", "R2"]
    assert out["history_trimmed"] == 2 and out["notes_trimmed"] == 1

def test_no_notes_is_unchanged():
    # the same five-message history with notes=frozenset() trims exactly as today (history_trimmed == 1 at budget 70)
```

- [ ] **Step 2: Run, verify FAIL** — `TypeError: unexpected keyword 'notes'`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_context.py test_pins_context.py` — PASS.
- [ ] **Step 5: Commit** `feat(context): a note never pushes out the post it precedes`.

### Task 5: Notes in the composed turn, director turn and opener

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py`:
  - imports: `authors_notes`, `from ..scenes import identity as scenes_identity`, `from ..scenes import serialize as scenes_serialize` (if step 2 has not already added it), and `authors_note` in the `from . import ...` line;
  - `_assemble`, `_prepare`, `_packed`, `compose_opener`.
- Test: `backend/tests/test_authors_note_turns.py` (new)

**Interfaces:**
- Consumes: Tasks 1, 3 and 4. It also consumes step 2's `scenes_serialize.in_context`.
- Changes in `_assemble` (after the `actor.observed_history` branch and before `sub_history` is built):

```python
turn_no = authors_note.note_turn(scene["messages"])        # full transcript, not `history`
applied, skipped = authors_note.applicable(
    authors_notes.read(cid), scenes_identity.scene_identity(cid, sid),
    actor_ref if actor_scoped else None,
    response_actor["name"] if actor_scoped else "", turn_no)
projected, positions = authors_note.inject(scenes_serialize.in_context(history),
                                           [] if opener else applied)
sub_history = [{"role": m["role"], "content": _expanded(m["content"])} for m in projected]
```

  - The returned dict gains `"notes": positions + [{**n, "index": None, "status": "skipped"} for n in skipped]`.
  - It also gains `"opener_notes": [_expanded(authors_note.render(n)) for n in applied] if opener else []`.
  - `story._project_history` is no longer called directly here; `inject` calls it.
- Changes in `_prepare`: `pack.pack(..., notes=frozenset(n["index"] for n in a.get("notes", ()) if n["index"] is not None))`, and `_breakdown({"post_history": post_history, "notes": a.get("notes", [])}, packed, ...)`. `_packed` passes the same `notes=`.
- Changes in `compose_opener`:
  - `before` gets `{"role": "system", "content": t}` appended for each `t in a["opener_notes"]`, after the prompt and prior contributions.
  - `extra` gets `("Author's note — " + level, t)` for each one. Carry the levels by zipping with `[n["level"] for n in a["notes"] if n["status"] == "applied"]`, or return the opener notes as `(level, text)` pairs; pick one and keep it consistent with Task 8's mirror.

- [ ] **Step 1: Write the failing tests.**
  - Context-level tests use the `_campaign(monkeypatch, tmp_path)` pattern from `test_context.py`: copy its four lines, with names `Realm`/`Saltmarch`.
  - Route-level tests copy `seed` from `test_character_turns.py` (a module function, not a fixture) and use `FakeLLM` from `llm_fakes.py` via `client.app.dependency_overrides[routes.get_llm]`.
  - `NOTE_MSG = prompts.render("scene/authors_note.j2", level="campaign", name="", text="Keep the storm audible in every scene.")`.

```python
def test_depth_four_lands_before_fourth_most_recent_post(...):
    # append P1,R1,P2,R2,P3,R3; authors_notes.set_campaign(cid, {"text": ..., "depth": 4, "every": 1})
    msgs = context.build_messages(cid, sid)
    i = next(n for n, m in enumerate(msgs) if m["content"] == NOTE_MSG)
    assert msgs[i]["role"] == "system" and msgs[i + 1]["content"].endswith("P2")

def test_depth_zero_after_last_post_and_large_depth_clamps(...):
    # depth 0: msgs[i - 1] holds "R3" and msgs[i + 1] is the post-history system message (or i is last before it)
    # depth 50: i == 1 (directly after the system message)

def test_excluded_posts_and_director_notes_do_not_count_toward_depth(...):
    # P1,R1,P2,R2,P3,R3, then a user "X" and an assistant "Y", then a director-note line
    # (scenes.append_message(cid, sid, "assistant", "steer", speaker=scenes_serialize.DIRECTOR_SPEAKER));
    # scenes.write.set_excluded(cid, sid, 6, True); scenes.write.set_excluded(cid, sid, 7, True); depth 2
    # -> the note sits immediately before the message containing "P3"

def test_every_three_applies_on_three_not_four(...):
    # 3 player posts -> NOTE_MSG present; append a 4th -> absent

def test_character_note_only_in_its_own_call(client):
    cid, sid = seed(client)
    store.authors_notes.set_character(cid, "characters:winifred", {"text": "Winifred whispers.", "depth": 0, "every": 1})
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":"characters:winifred"}\n```'],
                    ['Winifred answers.\n```handoff\n{"next":null}\n```']])
    # POST /chat {"content": "Hello", "speaker_ref": "characters:mara"}
    joined = lambda r: "\n".join(m["content"] for m in r["messages"])
    assert "Winifred whispers." not in joined(fake.requests[0])
    assert "[Author's note for Winifred: Winifred whispers.]" in joined(fake.requests[1])
    assert "Winifred whispers." not in str(store.context.compose_turn(cid, sid, describe=False, actor_ref="grimoire")[0])
    assert "Winifred whispers." not in str(store.context.build_messages(cid, sid))

def test_reroll_replays_the_frozen_note(client):
    # campaign note "Old steer.", one chat turn (pattern: test_middle_reroll_frozen_and_later_retained),
    # then set_campaign(cid, {"text": "New steer."}); POST /responses/{rid}/regenerate {}
    # retry.requests[0]["messages"] contains "Old steer." and not "New steer."

def test_director_turn_carries_the_note(...):
    # compose_director_turn(cid, sid, "Make it rain.")[0]: NOTE_MSG present and before the final user message

def test_opener_gets_every_one_notes_before_the_instruction(...):
    # empty scene; campaign note every 1, scene note every 2
    msgs, detail = context.compose_opener(cid, sid, "A storm rolls in.")
    # NOTE_MSG is right after the user prompt and before the last (instruction) message; the scene note is absent;
    # detail["sections"] has an "appended_*" row labelled "Author's note — campaign"

def test_garbled_file_composes_without_notes(...):
    # write "{not json" to authors_notes.json; build_messages succeeds and has no "[Author's note" content

def test_no_notes_prompt_is_byte_identical(...):
    # build_messages before and after creating an empty authors_notes.json via set_campaign(cid, None) are equal
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_authors_note_turns.py test_context.py test_character_turns.py test_response_snapshots.py test_model_guidance.py test_tracker_context.py` — PASS.
- [ ] **Step 5: Commit** `feat(context): inject author's notes into the history at depth`.

### Task 6: The inspector row

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_breakdown`)
- Test: `backend/tests/test_authors_note_turns.py`

**Interfaces:**
- Consumes: Task 5's `a["notes"]` and Task 4's `p["notes_trimmed"]`.
- Changes in `_breakdown(a, p, extra=None, count=None)`:
  - `notes = a.get("notes") or []`, `cut = p["history_trimmed"]`.
  - Applied notes with `index >= cut` sit at packed index `index - cut`. Applied notes with `index < cut` become status `"trimmed"`.
  - The `history` row's `text` and `tokens` cover only non-note messages, and its `trimmed` is `p["history_trimmed"] - p.get("notes_trimmed", 0)`.
  - When `notes` is non-empty, insert this row directly after the history row: `{"id": "authors_note", "label": "Author's notes", "tier": pack.HISTORY, "dropped": False, "pinned": False, "trimmed": <count trimmed>, "tokens": <sum of pack.message_cost over kept note messages>, "text": <listing>, "notes": [{"level", "name", "depth", "every", "status", "tokens"}]}`.
    - The listing has one block per note: `"[Campaign · depth 4]\n<sent text>"`, `"[Scene · depth 12 · trimmed]"`, `"[Character: Mara · skipped (every 3)]"`.
  - `total_tokens` adds the kept note tokens beside `hist_tokens`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_inspector_lists_applied_and_skipped(...):
    # 2 player posts; campaign note every 1 depth 0, scene note (scenes.ensure_identity) every 3
    rows = {r["id"]: r for r in context.context_breakdown(cid, sid)["sections"]}
    row = rows["authors_note"]
    assert row["tier"] == "history" and row["label"] == "Author's notes"
    assert [(n["level"], n["status"]) for n in row["notes"]] == [("campaign", "applied"), ("scene", "skipped")]
    assert row["notes"][1]["tokens"] == 0 and "skipped (every 3)" in row["text"]
    assert "Keep the storm audible" not in rows["history"]["text"]
    assert row["tokens"] > 0

def test_trimmed_note_is_reported(...):
    # config.write_config(context_budget=<small>), campaign note depth 50 over a long history
    # -> row["notes"][0]["status"] == "trimmed", row["tokens"] == 0

def test_no_configured_notes_no_row(...):
    # no authors_notes.json -> "authors_note" not in row ids
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_authors_note_turns.py test_context.py test_prompt_log_store.py test_routes.py test_frozen_campaign.py` (snapshot unchanged) — PASS.
- [ ] **Step 5: Commit** `feat(inspector): an Author's notes row`.

### Task 7: Routes

**Files:**
- Create: `backend/src/grimoire/routes/authors_notes.py` (`router = APIRouter()`)
- Modify: `backend/src/grimoire/routes/models.py` (add `class AuthorsNote(BaseModel): text: str = ""; depth: int = 4; every: int = 1`)
- Modify: `backend/src/grimoire/routes/__init__.py`:
  - import `authors_notes`;
  - list it in the docstring table;
  - add it to the `_compose` tuple right after `character_turns`, which is before `campaigns`; `entities` stays last.
- Test: `backend/tests/test_authors_notes_routes.py` (new; uses the `client` fixture and `seed` copied from `test_character_turns.py`)

**Interfaces:**
- Produces (every handler is a plain `def`; 404 `campaign not found` via `store.campaigns.campaign_exists`):
  - `GET /campaigns/{cid}/authors-notes` → `{"campaign": note|None, "characters": {...}, "scenes": {sid: note}}`.
    - Identities resolve in one pass: `{store.scenes.scene_identity(cid, s["id"]): s["id"] for s in store.scenes.list_scenes(cid)}`.
    - Notes for identities with no scene are omitted.
  - `PUT /campaigns/{cid}/authors-notes/campaign` body `AuthorsNote` → the GET payload.
  - `PUT /campaigns/{cid}/authors-notes/characters/{ref}` body `AuthorsNote` → the GET payload.
    - `ref` is the full, URL-encoded actor ref.
    - It is 404 `character_not_found` unless `ref == f"characters:{c['id']}"` for some `c in store.overlay.list_characters(cid)`.
  - `PUT /campaigns/{cid}/scenes/{sid}/authors-note` body `AuthorsNote` → the GET payload.
    - The identity comes from `store.scenes.ensure_identity(cid, sid)`; `SceneNotFound` → 404.
  - `GET /campaigns/{cid}/scenes/{sid}/authors-notes/next` → `{"turn": int, "count": int, "notes": [{"level", "depth", "every", "applies": bool, "ref"?: str, "name"?: str}]}`.
    - `turn = context.authors_note.note_turn(read_scene(...)["messages"]) + 1`.
    - It lists the campaign note, the scene note, and one entry per NPC in `store.appearances.scene_cast(cid, sid)` that has a character note (with `ref` and `name`).
    - `count` is the number of entries with `applies` true.
- `_validate(body) -> dict | None` raises 400 for each bad field:
  - `detail="text_too_long"` when `len(text) > 2000`;
  - `"depth_out_of_range"` when depth is outside `0..50`;
  - `"every_out_of_range"` when every is outside `1..50`.
  - Otherwise it returns `None` for empty or whitespace text (which clears) and the note dict for anything else.

- [ ] **Step 1: Write the failing tests:**

```python
def test_put_validates_ranges(client):  # depth 51, depth -1, every 0, every 51, 2001 chars -> 400 with those details
def test_put_campaign_and_clear(client):  # PUT then GET["campaign"] == note; PUT {"text": ""} -> None
def test_character_ref_validated(client):
    # PUT .../characters/characters%3Amara -> 200 and GET["characters"]["characters:mara"]; .../characters%3Anobody -> 404
def test_scene_note_keyed_by_sid_in_get_and_survives_rename(client):
    # PUT scene note; GET["scenes"][sid]; store.scenes.rename_scene(...) -> GET["scenes"][new_sid]
def test_scene_note_unknown_scene_404(client)
def test_next_reports_applies_and_character_condition(client):
    # 1 player post on the scene (turn 2 next); campaign every 2 -> applies True; scene every 3 -> False;
    # character note for characters:mara -> entry with name "Mara"; count == number of applies
```

- [ ] **Step 2: Run, verify FAIL** — 404s.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_authors_notes_routes.py test_route_order.py test_pydantic_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(routes): read and save author's notes`.

### Task 8: Harnesses and the template README

**Files:**
- Modify: `scripts/verify_templates.py`:
  - in the fixture, after the `sid` transcript is appended (around line 539), set `astore.set_campaign(cid, {"text": "Keep the storm audible in every scene.", "depth": 1, "every": 1})` with `from grimoire.store import authors_notes as astore`. The note text contains no macros;
  - in `rendered_messages`, mirror the insertion.
- Modify: `evals/cases.py` (`build_turn_taking`, `grade_turn_taking`)
- Modify: `evals/README.md` (one line naming the new `prompt.authors_note` check under the turn-taking case)
- Modify: `templates/README.md` ("Message assembly")

**Interfaces:**
- `rendered_messages(scene_id, data, note=None, opener_prompt=None)`, for the non-opener path:
  - compute `k` with the same snap rule over `scenes.read_scene(cid, scene_id)["messages"]` (a local `_note_split(messages, depth)` written out in the script, not imported, because the script is a mirror);
  - project `messages[:k]`, append `{"role": "system", "content": render("scene/authors_note.j2", level="campaign", name="", text=NOTE)}`, then project `messages[k:]`.
  - With the fixture's `[user, assistant, assistant, user]` and depth 1, the note sits before "I follow her."
  - In the offscreen scene (one assistant line) it sits at the start.
  - On the opener path, append the note system message after the opener-prompt user message and before the post-history.
- `build_turn_taking` sets:
  - `authors_notes.set_campaign(cid, {"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1})`;
  - `authors_notes.set_character(cid, sera, {"text": "Seraphine never names the buyer.", "depth": 0, "every": 1})`.
  - It returns `"sera_ref": sera` and `"other_ref": f"characters:{ids['Tobin']}"`.
- `grade_turn_taking` adds:
  - `graders.grade_prompt_section(ctx["messages"], "authors_note", "scene/authors_note.j2", level="campaign", name="", text="Keep the storm audible in every scene.")`;
  - `Check("prompt.authors_note_scoped", ...)`, true iff the character render (`level="character", name="Seraphine Vale"`) is in `graders.prompt_text(context.compose_turn(cid, sid, describe=False, actor_ref=sera_ref)[0])` and absent from both the Tobin call and `ctx["messages"]` (the narrator's).
- `templates/README.md`, "Message assembly": add step 2a after the projected history.
  - It describes `scene/authors_note.j2` (vars `level`, `name`, `text`), rendered once per applying note (campaign, then scene, then the character note in that character's own call only) as its own system message. The message is inserted into the projected history at `len - depth` in-context posts, snapped back to the start of a player post.
  - Openers place it after the prompt.
  - Cadence is `every`, counted over player posts plus director notes.

- [ ] **Step 1: Make the harness changes first, against the unchanged mirror**, and run `make check-templates PY=$(pwd)/backend/.venv/bin/python`. Verify that the chat, director, opener and offscreen comparisons FAIL (the mirror does not insert yet), which proves the fixture exercises the note.
- [ ] **Step 2: Implement the mirror**, then re-run — PASS.
- [ ] **Step 3: Eval changes**, then run `test_evals.py test_eval_graders.py` — `prompt.authors_note` and `prompt.authors_note_scoped` pass and every recording still scores as declared. Temporarily emptying `authors_note.j2` makes `prompt.authors_note` fail. Revert that.
- [ ] **Step 4: README edits.**
- [ ] **Step 5: Commit** `test(prompts): author's notes in the template mirror and the evals`.

### Task 9: Play view — the Author's notes section

**Files:**
- Modify: `frontend/src/api/types.ts`:
  - `AuthorsNote = { text: string; depth: number; every: number }`;
  - `AuthorsNotes = { campaign: AuthorsNote | null; scenes: Record<string, AuthorsNote>; characters: Record<string, AuthorsNote> }`;
  - `AuthorsNotesNext = { turn: number; count: number; notes: { level: "campaign" | "scene" | "character"; depth: number; every: number; applies: boolean; ref?: string; name?: string }[] }`.
- Modify: `frontend/src/api/client.ts`:
  - `getAuthorsNotes(cid)`;
  - `setCampaignAuthorsNote(cid, note)`;
  - `setCharacterAuthorsNote(cid, ref, note)`, which encodes `ref` with `encodeURIComponent`;
  - `setSceneAuthorsNote(cid, sid, note)`;
  - `getAuthorsNotesNext(cid, sid)`. The GETs use `{ fresh: true }`, like `getCampaignRouting`.
- Create: `frontend/src/components/AuthorsNotesPanel.tsx`
- Modify: `frontend/src/components/SceneInspector.tsx`:
  - a new `SideSection id="authors_notes" title="Author's notes"` directly after the "Model routing" section, collapsed by default (`collapsed.authors_notes ?? true`);
  - its `extra` is `<span className="chip on" aria-label="Notes applying next turn">{next.count}</span>` when `count > 0`;
  - `next` comes from `api.getAuthorsNotesNext(cid, sid)`, fetched on `[cid, sid, refreshKey]` and again via the panel's `onSaved`.
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`vi.fn()` for all five new api functions) and `frontend/src/testkit/campaignHarness.tsx`:
  - in `installCampaignMocks`, `getAuthorsNotesNext` resolves `{ turn: 1, count: 0, notes: [] }`;
  - `getAuthorsNotes` resolves `{ campaign: null, scenes: {}, characters: {} }`.
- Modify: `frontend/src/components/SceneInspector.test.tsx`:
  - add `getAuthorsNotesNext: vi.fn()` to its `vi.mock("../api/client")` factory, with a default in its `beforeEach`;
  - stub `./AuthorsNotesPanel` the way it stubs `./ModelRoutingPicker`.
- Test: `frontend/src/components/AuthorsNotesPanel.test.tsx` (new)

**Interfaces:**
- Produces: `AuthorsNotesPanel({ cid, sid, cast, onSaved }: { cid: string; sid: string; cast: Actor[]; onSaved: () => void })`.
  - Three `role="tab"` buttons ("Campaign", "This scene", "Character") with `aria-selected`.
  - The Character tab shows `<select aria-label="Character">` over `cast.filter(a => a.role === "npc" && a.kind === "characters")`, with values `characters:<id>`.
  - Each tab shows a `<textarea aria-label="Author's note" maxLength={2000}>`, `<input type="number" aria-label="Depth" min={0} max={50}>`, `<input type="number" aria-label="Every N turns" min={1} max={50}>`, and a Save button.
  - Save calls the matching setter, reloads from its response, and calls `onSaved`.
  - A `.field-hint` explains depth ("posts from the end; 0 = after the last post").

- [ ] **Step 1: Write the failing tests:**
  - `AuthorsNotesPanel.test.tsx` (mock `../api/client`):
    - the Campaign tab shows the loaded campaign note text; editing it and clicking Save calls `setCampaignAuthorsNote("c", { text: "Keep the storm audible.", depth: 4, every: 1 })` and then `onSaved`;
    - the "This scene" tab saves through `setSceneAuthorsNote("c", "s", …)`;
    - the Character tab lists only NPC characters (Mara, Winifred; not a PC), and Save sends `setCharacterAuthorsNote("c", "characters:winifred", …)` after picking Winifred;
    - depth and every inputs are sent as numbers.
  - `SceneInspector.test.tsx`: with `getAuthorsNotesNext` resolving `{ turn: 3, count: 2, notes: [...] }`, `await screen.findByLabelText("Notes applying next turn")` has text `2`; with `count: 0` there is no such element.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd frontend && npx vitest run src/components/AuthorsNotesPanel.test.tsx src/components/SceneInspector.test.tsx src/routes/CampaignView.test.tsx src/components/review/SceneReview.test.tsx`, then `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): edit author's notes from the scene inspector`.

### Task 10: Gate

- [ ] **Step 1:** Run `make check PY=$(pwd)/backend/.venv/bin/python` and fix whatever fails. If a ratchet reports an improvement or a regression, fix it, or run `make baseline` and commit the baseline together with its cause. Do nothing else in this task.

## Plan-gate rulings (binding; override the tasks above where they differ)

Plan → implementation gate: independent adversarial review (stand-in for
`/codex:adversarial-review`; owner-approved).

1. Task 1 regenerates `backend/tests/store_api_baseline.json` (the facade gains
   `authors_notes`) and runs `tests/test_store_api_baseline.py` in its step 4.
2. Opener notes: `a["opener_notes"]` is a list of `(level, text)` pairs (the
   "zip applied levels" option is deleted); Task 8's mirror uses the same shape.
3. `turn_no = 0 if opener else authors_note.note_turn(scene["messages"])`, plus
   a test running an opener over a scene that has posts and expecting only
   `every == 1` notes.
4. The branch copy (`authors_notes.copy_scene`) runs with the branching plan's
   step-6 group — before the cut and before the rolls copy — and is imported
   in `store/branch.py` as `from . import authors_notes`.
5. `normalize`: depth defaults to 4 **only when missing or not an int** (0 is
   valid); `test_normalize_defaults_and_clamps` asserts
   `normalize({"text": "x", "depth": 0})["depth"] == 0`.
6. The panel lists the `/next` entries ("Campaign: applies next turn",
   "Mara: applies when Mara speaks"), with a test; the header `count` counts
   entries that apply next turn regardless of speaker, character entries
   included, and the panel says so.
7. The `authors_note` row goes after the history row if present, else before
   `post_history`.
8. Test commands use `tests/test_….py` paths under `cd backend`.
9. The `store/context/authors_note.py` docstring states gate 4 (repeated empty
   sends share a turn number) and gate 9 (a roll resume recomposes and sees the
   current notes).
10. The `/next` docstring and the panel say the live inspector describes the
    turn just composed while the panel describes the next one.
11. Task 2's test uses the branching suite's `_two_turns(client)` setup; Task
    5's depth-0 test asserts `i == len(msgs) - 1` for the `_campaign` scene.
