# Play controls III — branching on one fork primitive: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Branch from here" forks an unabsorbed scene at a post into a sibling scene; absorbing one member of a branch group makes the others read-only; an absorbed scene branches by forking the campaign at the post; replay (#151) runs inside a branch so the original is never touched.

**Architecture:** One store primitive, `store/branch.py::branch_scene`, builds a sibling by writing a remapped copy of the transcript, then copying each keyed record through a small helper owned by that record's module (responses, tracker, appearances, rolls, audit baseline), then cutting with the existing `scenes.write.delete_from`. Closedness is **derived** in `scenes.read.list_scenes` from the group's `done` flags — no stored key, no close/reopen hook. A route-side guard `runs.require_scene_open` (409 `branch_closed`) is called by `reserve_turn` and by each transcript-shaping door. `store/fork.py` gains `from_index`; `POST .../replay` gains `branch`. The play view adds ⑂ in the gutter, chips, a closed banner and a branch option in the replay dialog.

**Tech Stack:** FastAPI + pytest (`backend/`), React + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-branching-design.md` — its **Gate resolutions** section is binding and overrides the text above it.

## Global Constraints

- **Closed is derived, not stored** (gate 1). A scene is closed when another member of its `branch_group` has `done: true`. Rows/payloads carry `closed_by: {sid, title}`. No `branch_closed` frontmatter key, no close/reopen hook.
- Frontmatter keys, flat strings: `branch_group` (source's existing group, else the source's identity token, written onto the source on its first branch) and `branch_of` (source identity token). New scene-row keys (`branch_group`, `branch_of`, `closed_by`) are emitted **only when present**, so `backend/tests/fixtures/frozen_campaign/snapshot.json` does not move (gate 17).
- `branch_scene(cid, sid, through, *, title="") -> str` keeps `messages[: through + 1]`; refusals `BranchRefused(kind)` with kinds `absorbed_use_fork`, `branch_closed`; out-of-range `through` raises `IndexError` (route → 400). `through == len(messages) - 1` means **no cut** (gate 4).
- Sibling id: the source's number at the source id's width, the source's date slug, `slugify(title)`, uniquified against `scenes.paths._sid_taken` **and** against every `scene` value in `rolls.json` (gate 8). Title: `"<source title> (branch)"`, then `" 2"`, `" 3"`… unless the caller passes one. Fresh identity token.
- Frontmatter copied: `model`, `location_history`, `time_history`, `suggested_date`, `pcless`, `turn_sizes`, `dismissed`, and every key in `scenes.write.RESPONSE_FIELDS` (gate 12). **Never** `done`, `one_line`, `summary`, `greeting`, rolling-summary (`rolling_*`) or scene-break (`break_*`) keys.
- Responses: each record whose messages survive is cloned under a **new response id** with its snapshots re-written under that id; a clone is `mechanically_locked` iff a kept roll line follows its first message in the kept transcript (gate 7); referenced rounds are cloned, unfinished ones (`pending`/`incomplete`/`paused`) marked `superseded`.
- Rolls: for each kept roll line, the earliest unconsumed source entry whose `dice.format_roll(result, label)` equals the line, else whose `label` and `result["notation"]` both appear in the line; copies get a fresh id and `scene` = new sid and drop `proposal` (gate 8).
- Appearances are written **before** the cut (gate 13): membership = actors present at `through` (an interval with `start <= through` and `end is None or end > through`, or a legacy member of the source with no presence for it); intervals with `start <= through` are copied and the cut's `remap_presence` clips them.
- Tracker records copied, post keys unchanged, response keys remapped through the old→new id map, plus the scene field layer (gate 11). Steering log **not** copied (gate 15). Not copied: prompt log, attempts, pins, commits, pending reviews, proposals, legacy alternates, pending replay.
- Author's note (gate 16): copy via `store.authors_notes` **only if that module exists** when Task 3 is implemented; otherwise leave the `# step 5:` marker described in Task 3 — step 5 adds the copy.
- Build under `locks.campaign_lock(cid)`; failure after the sibling file exists deletes it through `scenes.lifecycle.delete_scene`. No repad check (gate 17).
- Guard (gate 2): `runs.require_scene_open` is **its own check**, not folded into `require_scene_free`. Called by `reserve_turn` and the doors listed in Task 4. **Not** by scene delete, rename, replay accept, replay cancel.
- Absorbing waits for the whole group (gate 3): `PUT .../chronicle` and `POST .../absorb` answer `scene_busy` while any group member holds a live run.
- Closed scenes are not open work (gate 14): excluded from the rail's open scenes (`GET /api/shell`), the `open-scenes` chore and its items, and export book chapters (`export.collect`).
- Fork (gate 9): `from_index` validated in `_check_source` (`0 <= from_index < len(messages)`, else `IndexError` → 400) before any copy; `_cut_after` compares **by number** (removes every other scene with the fork scene's number and every later number); report gains optional `cut_at`. Fork-first from the replay dialog keeps the whole scene (gate 6).
- Replay (gate 5): server default `branch: false`; with `branch: true` every `replay.begin` refusal is evaluated before branching, and a sibling whose `begin` still refuses is deleted. Client defaults the option on for an unabsorbed scene.
- UI (gate 10): a closed scene has no composer, no gutter actions, no swipes, no `r`/arrow hotkeys — each binding carries the `closed` condition (registry rule; never a `window` listener).
- Project rules: every store write through `store.atomic` (no `shutil.copy`); `store/branch.py` in `locks.DOMAIN_MODULES` and every new public `cid`-taking mutator takes `locks.campaign_lock(cid)`; imports at module scope, and inside `store/` bind submodules (`from .scenes import read as scenes_read`), never names off a sibling package; pydantic plain `BaseModel` fields only; responses as plain dicts; keys only through `useHotkeys`.
- Adding `branch` to `grimoire.store` changes its facade: regenerate `backend/tests/store_api_baseline.json` in the same commit with the snippet in `test_store_api_baseline.py`'s docstring.
- Placeholder names only (Mara, Winifred, Seraphine, Realm, Saltmarch).
- Ratchet gates: if a change adds or resolves a ruff/mypy/eslint finding, `make baseline` and commit the new `lint-baselines/*.json` with that change.
- Backend tests: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`. Frontend tests: `cd frontend && npx vitest run <files>` and `npm run typecheck`.

## Review Focus

1. Deleting the source must not take the sibling's prompt snapshots with it (`responses.drop_scene` removes every snapshot a record references) — new response ids, pinned in Task 3 (`test_deleting_the_source_leaves_the_siblings_responses`).
2. A turn landing in a sibling after another member is absorbed would write to a read-only scene — the chronicle commit refuses `scene_busy` while any member holds a run, and `reserve_turn` checks closedness under the campaign lock; Task 4 (`test_absorbing_waits_for_every_member_of_the_group`).
3. A branch that fails part-way must leave no half-built sibling and no ledger scope or presence for it — Task 3 (`test_a_failure_part_way_leaves_no_sibling`).
4. A replay-in-a-branch refused by `begin` must leave no stray sibling and the original byte-identical — Task 7 (`test_a_refused_branch_replay_creates_no_sibling`).
5. Two identical roll lines (`1d20` both rolling the same face) must each get their own entry, consumed in source order — Task 2 (`test_identical_roll_lines_consume_entries_in_order`).

---

### Task 1: Branch keys, derived `closed_by`, and closed scenes are not open work

**Files:**
- Modify: `backend/src/grimoire/store/scenes/read.py` (`_scene_row`, `list_scenes`, new `closed_by`)
- Modify: `backend/src/grimoire/store/scenes/write.py` (new `set_branch_keys`)
- Modify: `backend/src/grimoire/routes/shell.py` (`open_scenes` filter, ~line 167)
- Modify: `backend/src/grimoire/routes/todo.py` (`_chore_open_scenes`, `_items_open_scenes`)
- Modify: `backend/src/grimoire/store/export.py` (`collect`, the `sids = …list_scenes…` line ~426; **not** `build_json`)
- Test: `backend/tests/test_branch_store.py` (new), `backend/tests/test_shell_route.py`, `backend/tests/test_todo_route.py`, `backend/tests/test_export_store.py`

**Interfaces:**
- Produces: `scenes.write.set_branch_keys(cid: str, sid: str, group: str, of: str | None = None) -> None` — `@locking._serialized`; sets `meta["branch_group"] = group`, and `meta["branch_of"] = of` when given; does **not** touch `updated` (not a transcript change). `SceneNotFound` for a missing scene.
- Produces: `_scene_row` adds `branch_group` / `branch_of` only when the frontmatter value is non-empty.
- Produces: `list_scenes(cid)` post-pass: for each row with a `branch_group` that is not `done`, if another row of that group is `done`, set `row["closed_by"] = {"sid": <that row's id>, "title": <its title>}` (the done member with the smallest id when several). Rows are already fresh dicts.
- Produces: `scenes.read.closed_by(cid: str, sid: str) -> dict | None` — reads the scene's head (`parse_frontmatter_head`); `None` when the file is missing/unsafe or has no `branch_group` (the fast path every turn takes); otherwise the matching `list_scenes` row's `closed_by`.

- [ ] **Step 1: Write the failing tests** in `test_branch_store.py` (fixtures: `monkeypatch.setenv("GRIMOIRE_HOME", …)`, `worlds.create_world("Realm")`, `campaigns.create_campaign("Saltmarch", wid)`, and a `_played(cid, title, posts=2)` helper copied from `test_fork_store.py`):

```python
def _group(cid, *sids):
    g = scenes.ensure_identity(cid, sids[0])
    for s in sids:
        scenes.write.set_branch_keys(cid, s, g, of=None if s == sids[0] else g)
    return g

def test_a_member_is_closed_while_another_member_is_absorbed(cid):
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    _group(cid, a, b)
    scenes.mark_absorbed(cid, a, "x", "y")
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert rows[b]["closed_by"] == {"sid": a, "title": "Mara"}
    assert "closed_by" not in rows[a]
    assert scenes.read.closed_by(cid, b) == {"sid": a, "title": "Mara"}

def test_unabsorbing_or_deleting_the_absorbed_member_reopens_the_rest(cid): ...
    # scenes.write.unmark_absorbed(cid, a) -> closed_by(cid, b) is None;
    # mark_absorbed again, scenes.delete_scene(cid, a) -> closed_by(cid, b) is None

def test_a_scene_in_no_group_carries_no_branch_keys(cid): ...
    # row has none of "branch_group", "branch_of", "closed_by"; closed_by(cid, sid) is None

def test_setting_branch_keys_does_not_touch_updated(cid): ...
```

In `test_shell_route.py`: `test_a_closed_branch_is_not_open` — two scenes via the route, grouped with `set_branch_keys`, one `mark_absorbed` → `block["open"] == []`, `block["scenes"] == 2`.
In `test_todo_route.py`: the `open-scenes` chore is absent and `/api/todo/open-scenes/items` is empty when the only unabsorbed scene is closed.
In `test_export_store.py`: `export.collect(cid)["chapters"]` has one chapter (the absorbed member), not two.

- [ ] **Step 2: Run, verify FAIL** — `AttributeError: … set_branch_keys`.
- [ ] **Step 3: Implement.** Consumers filter with `not s.get("closed_by")` beside their existing `done` test.
- [ ] **Step 4: Run** `tests/test_branch_store.py tests/test_shell_route.py tests/test_todo_route.py tests/test_export_store.py tests/test_scene_store.py tests/test_frozen_campaign.py` — PASS (the frozen snapshot must not move).
- [ ] **Step 5: Commit** `feat(scenes): branch groups and derived closed scenes`.

### Task 2: Copy helpers owned by each record's module

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (new `clone_for_branch`)
- Modify: `backend/src/grimoire/store/tracker/records.py` (new `clone`)
- Modify: `backend/src/grimoire/store/appearances/paths.py` (new `join_branch`)
- Modify: `backend/src/grimoire/store/rolls.py` (new `copy_for_branch`)
- Modify: `backend/src/grimoire/store/audit/baselines.py` (new `copy_baseline`)
- Test: `backend/tests/test_branch_copy.py` (new)

**Interfaces** (each takes `locks.campaign_lock(cid)` around its read-modify-write; all writes via `atomic`):
- `responses.clone_for_branch(cid: str, src_sid: str, dst_sid: str, rid_map: dict[str, str], locked: set[str]) -> None` — source scope by `identity.scene_identity(cid, src_sid)` (no-op if none), destination by `_scope(cid, dst_sid, data)`. For each `old -> new`: deep-copy the record, set `id = new`; re-publish `snapshot_ref`, `resume_snapshot_ref` and each of `resume_snapshot_refs` with `response_snapshots.write(cid, new, kind, response_snapshots.read(cid, ref))` (`kind` = the `primary`/`resume` prefix of the reference's file name); set `mechanically_locked = True` iff `new in locked`, else remove the key. Rounds named by cloned records' `round_id` are deep-copied under the same round id with `pending_response` mapped through `rid_map` (else `None`) and an unfinished status set to `"superseded"`. One `_write`.
- `tracker.records.clone(cid: str, src_identity: str, dst_identity: str, rid_map: dict[str, str]) -> None` — for each `read_index(cid, src_identity)` entry: key `r-<old>-<vid>` becomes `r-<rid_map[old]>-<vid>` (skipped when `old` is not mapped), `p-…` keys unchanged; snapshot file bytes copied with `atomic.write_bytes` to `_snapshot_path(cid, dst_identity, new_key)`; then `_write_index(cid, dst_identity, new_entries)`; the scene layer (`paths.scene_layer_path`) bytes copied when present. No source directory → no-op.
- `appearances.paths.join_branch(cid: str, src_sid: str, dst_sid: str, through: int) -> None` — per record: copy the source intervals with `start <= through` into `presence[dst_sid]`; append `dst_sid` to `scenes` when the actor is present at `through` (interval with `start <= through and (end is None or end > through)`) or is a member of `src_sid` with no presence for it. One `_write` when anything changed.
- `rolls.copy_for_branch(cid: str, src_sid: str, dst_sid: str, lines: list[str]) -> list[dict]` — the gate-8 match over `[e for e in read(cid) if e.get("scene") == src_sid]`, consuming each entry once, in line order; a `format_roll` that raises on a malformed entry counts as no match. Each copy: `{"id": f"r{len(entries) + 1}", "ts": now_iso(), "scene": dst_sid, "label", "result", **tier}` — never `proposal`. One `_write`; returns the copies.
- `baselines.copy_baseline(cid: str, src_sid: str, dst_sid: str) -> None` — `campaign_lock(cid)` then `_lock(cid)` (capture_baseline's order); `data[dst_sid] = deepcopy(data[src_sid])` when the source has one.

- [ ] **Step 1: Write the failing tests** in `test_branch_copy.py`. Copy the `seed(client, module=None)` helper body from `tests/test_character_turns.py` (it is a module function there, not a fixture) and drive one chat turn with `FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])` injected at `client.app.dependency_overrides[routes.get_llm]`.

```python
def test_cloned_response_has_its_own_id_and_snapshot(client):
    cid, sid = seed(client); _turn(client, cid, sid)
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    dst = store.scenes.create_scene(cid, "Mara (branch)")
    new = uuid.uuid4().hex
    store.responses.clone_for_branch(cid, sid, dst, {rid: new}, set())
    src = store.responses.get(cid, sid, rid, private=True)
    out = store.responses.get(cid, dst, new, private=True)
    assert out["snapshot_ref"].startswith(f"{new}/")
    assert out["snapshot"] == src["snapshot"]
    assert not out.get("mechanically_locked")

def test_a_clone_is_locked_only_when_named(client): ...      # locked={new} -> True
def test_an_unfinished_round_is_superseded_in_the_clone(client): ...
    # store.responses.update_round(cid, sid, round_id, status="paused") before cloning;
    # the clone's round status == "superseded", the source's still "paused"

def test_tracker_records_follow_the_new_response_ids(cid): ...
    # records.save(cid, src_ident, f"r-{old}-{vid}", {...}, changed=[], fields_digest="d", model="m")
    # and a "p-<32 hex>" key; after clone: read_index(cid, dst_ident) keys ==
    # {f"r-{new}-{vid}", "p-…"}; read_snapshot equal; source index unchanged

def test_join_branch_seats_who_was_present_at_the_branch_point(client): ...
    # Mara present [0, None), Winifred left at 2 ([0, 2]); join_branch(…, through=3):
    # dst in Mara's scenes, not Winifred's; Winifred's presence[dst] == [{"start": 0, "end": 2}]

def test_identical_roll_lines_consume_entries_in_order(cid): ...
    # two rolls.append(cid, sid, None, r) with the same result dict, two equal lines:
    # copy_for_branch returns two copies whose result came from r1 then r2
    # (assert ids are fresh, scene == dst, and "proposal" not in either)

def test_a_check_line_matches_by_label_and_notation(cid): ...
    # entry label "Mara — Brawl", line built with checks.format_check_roll -> matched

def test_an_unmatched_line_copies_nothing(cid): ...

def test_copy_baseline_copies_the_source_entry(cid): ...
    # write data[src] via baselines._write; copy; read_baselines(cid)[dst] == data[src]
```

- [ ] **Step 2: Run, verify FAIL** — `AttributeError` on each helper.
- [ ] **Step 3: Implement** the five helpers per the interfaces.
- [ ] **Step 4: Run** `tests/test_branch_copy.py tests/test_responses.py tests/test_response_snapshots.py tests/test_tracker_records.py tests/test_appearances_store.py tests/test_rolls_store.py tests/test_audit_store.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(store): per-module copy helpers for a scene branch`.

### Task 3: `store/branch.py` — the primitive

**Files:**
- Create: `backend/src/grimoire/store/branch.py`
- Modify: `backend/src/grimoire/store/__init__.py` (import and `__all__` entry `"branch"`)
- Modify: `backend/src/grimoire/store/locks.py` (`"store.branch"` in `DOMAIN_MODULES`, with a reason comment)
- Modify: `backend/tests/store_api_baseline.json` (regenerated — Global Constraints)
- Test: `backend/tests/test_branch_store.py`

**Interfaces:**
- Consumes: Task 1 (`set_branch_keys`, `read.closed_by`), Task 2 helpers.
- Produces: `class BranchRefused(Exception)` with `.kind: str` and `.detail: str`.
- Produces: `branch_scene(cid: str, sid: str, through: int, *, title: str = "") -> str`.
- Imports (submodules only): `from . import atomic, locks, responses, rolls, scene_ids`; `from .appearances import paths as appearances_paths`; `from .audit import baselines`; `from .scenes import identity as scenes_identity, lifecycle as scenes_lifecycle, paths as scenes_paths, read as scenes_read, serialize as scenes_serialize, write as scenes_write`; `from .tracker import records as tracker_records, walk as tracker_walk`; `from .frontmatter import dump_frontmatter`; `from .paths import now_iso, slugify, uniquify`.

Algorithm (all inside one `locks.campaign_lock(cid)` hold):
1. `scene = scenes_read.read_scene(cid, sid)` (raises `SceneNotFound`). `done` (case-insensitive `"true"`) → `BranchRefused("absorbed_use_fork", …)`; `scenes_read.closed_by(cid, sid)` → `BranchRefused("branch_closed", …)`; `not 0 <= through < len(messages)` → `IndexError(through)`.
2. `src_ident = scenes_identity.ensure_identity(cid, sid)`; `group = meta.get("branch_group") or src_ident`; when the source had none, `scenes_write.set_branch_keys(cid, sid, group)`.
3. Title: caller's (stripped) or `"<title> (branch)"`, `" 2"`, `" 3"`… against every `list_scenes` title. Sid: `scene_ids.format_sid(number, width, date_slug, slugify(title))` from `scene_ids.parse_sid(sid)` (an unparseable legacy id falls back to `scene_ids.fit_sid(f"{meta['created'][:10]}-", slugify(title))`, as `rename_scene` does), passed through `uniquify` with `lambda c: scenes_paths._sid_taken(cid, c) or c in {e.get("scene") for e in rolls.read(cid)}`.
4. `kept = messages[: through + 1]`; `rid_map = {old: uuid.uuid4().hex}` for each distinct `response_id` in `kept`; rewrite `response_id` through it in **all** messages (the tail is cut in step 7). `locked` = the new ids whose first kept message is followed in `kept` by a message with `speaker == scenes_serialize.ROLL_SPEAKER`.
5. Meta: the copied keys from Global Constraints that the source has, plus `title`, `created`/`updated` = `now_iso()`, `identity = scenes_identity.mint()`, `branch_group = group`, `branch_of = src_ident`. Write with `atomic.write_text(scenes_paths._scene_path(cid, new_sid), dump_frontmatter(meta, scenes_serialize._serialize_messages(messages)))`.
6. Inside `try:` — `appearances_paths.join_branch(cid, sid, new_sid, through)`; `responses.clone_for_branch(cid, sid, new_sid, rid_map, locked)`; `baselines.copy_baseline(cid, sid, new_sid)`; `tracker_records.clone(cid, src_ident, new_ident, rid_map)`.
7. If `through < len(messages) - 1`: `scenes_write.delete_from(cid, new_sid, through + 1)` then `tracker_walk.prune(cid, new_sid)`.
8. `rolls.copy_for_branch(cid, sid, new_sid, [m["content"] for m in kept if m.get("speaker") == scenes_serialize.ROLL_SPEAKER])` — last, because `delete_scene` cannot take a roll back out.
9. Author's note: if `backend/src/grimoire/store/authors_notes.py` exists, copy the source identity's scene note to `new_ident` with that module's API, inside the `try`. If it does not, write `# step 5: copy the scene's author's note here (spec gate 16)` at this point and nothing else.
10. `except BaseException:` → `scenes_lifecycle.delete_scene(cid, new_sid)` (itself in `try/except Exception` + `log.warning`), then re-raise. Return `new_sid`.

- [ ] **Step 1: Write the failing tests** in `test_branch_store.py` (client-backed tests reuse Task 2's copied `seed` and a two-turn helper: chat "Hello" then "Onward", one `FakeLLM` reply each, giving `[user, Mara, user, Mara]`):

```python
def test_branch_keeps_exactly_the_posts_through_the_branch_point(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 1)
    src, out = (store.scenes.read_scene(cid, s)["messages"] for s in (sid, new))
    assert [(m["role"], m.get("speaker"), m["content"]) for m in out] == \
           [(m["role"], m.get("speaker"), m["content"]) for m in src[:2]]
    assert sum(store.scenes.get_turn_sizes(cid, new)) <= 1
    assert len(store.scenes.read_scene(cid, sid)["messages"]) == 4   # source untouched

def test_branch_rewinds_location_history(client): ...   # two moves, as `_two_locations` in test_cascade_store.py; branch before the second transition -> one entry
def test_the_sibling_sorts_directly_after_its_source(cid): ...
    # three played scenes; branch the first -> sorted ids: [first, new, second, third];
    # scene_ids.parse_sid(new)["number"] == parse_sid(first)["number"]; title == "Mara (branch)"
def test_a_second_branch_is_titled_with_a_number(cid): ...  # "Mara (branch) 2"
def test_siblings_share_a_group_and_name_their_source(cid): ...
    # identity differs; read_scene_meta(new)["branch_of"] == identity(sid);
    # both "branch_group" == identity(sid); a second branch and a branch of the branch join
    # the same group, the latter with branch_of == identity(first sibling)
def test_through_the_last_post_does_not_cut(cid, monkeypatch): ...
    # monkeypatch store.scenes.write.delete_from to raise; branch at len-1 succeeds
def test_deleting_the_source_leaves_the_siblings_responses(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 3)
    rid = store.scenes.read_scene(cid, new)["messages"][-1]["response_id"]
    assert rid not in {m.get("response_id") for m in store.scenes.read_scene(cid, sid)["messages"]}
    store.scenes.delete_scene(cid, sid)
    rec = store.responses.get(cid, new, rid, private=True)
    assert rec["snapshot"]                         # snapshot file survived
def test_rerolling_a_response_in_the_sibling_works(client): ...
    # POST /api/campaigns/{cid}/scenes/{new}/responses/{rid}/regenerate json={} with a fresh FakeLLM -> 200,
    # record gains a variant
def test_the_sibling_has_cast_and_presence(client): ...
def test_kept_roll_lines_carry_their_entries(client): ...
    # POST /scenes/{sid}/roll {"notation": "1d20"} after the first turn; branch past it:
    # store.audit.prompt.roll_lines(cid, new) == roll_lines(cid, sid); branch before it: roll_lines(cid, new) == []
def test_branching_an_absorbed_scene_is_refused(cid): ...   # BranchRefused, .kind == "absorbed_use_fork"
def test_branching_a_closed_scene_is_refused(cid): ...      # absorb the sibling -> source .kind == "branch_closed"
def test_an_out_of_range_point_is_an_index_error(cid): ...
def test_a_failure_part_way_leaves_no_sibling(client, monkeypatch):
    cid, sid = _two_turns(client)
    before = {s["id"] for s in store.scenes.list_scenes(cid)}
    monkeypatch.setattr(store.audit.baselines, "copy_baseline", boom)   # boom raises RuntimeError
    with pytest.raises(RuntimeError):
        store.branch.branch_scene(cid, sid, 1)
    assert {s["id"] for s in store.scenes.list_scenes(cid)} == before
    data = json.loads((store.campaigns.campaign_root(cid) / "responses.json").read_text())
    assert set(data["scenes"]) == {store.scenes.scene_identity(cid, sid)}
```

(Patched on the module object, which intercepts because `branch.py` binds the submodule and calls `baselines.copy_baseline` through it.)

- [ ] **Step 2: Run, verify FAIL** — `AttributeError: module 'grimoire.store' has no attribute 'branch'`.
- [ ] **Step 3: Implement** per the algorithm; classify in `locks.py`; regenerate `store_api_baseline.json`.
- [ ] **Step 4: Run** `tests/test_branch_store.py tests/test_branch_copy.py tests/test_lock_domain_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py tests/test_atomic_guard.py tests/test_paths_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(store): branch a scene into a sibling`.

### Task 4: The `branch_closed` guard and group-wide absorb exclusion

**Files:**
- Modify: `backend/src/grimoire/routes/runs.py` (new `require_scene_open`, `scene_held_open`, `require_group_free`; `reserve_turn`)
- Modify: `backend/src/grimoire/routes/scenes.py` — doors: `POST cast` (~4334), `DELETE cast/{kind}/{id}` (~4349), `cast/batch` (~4363), `cast/emergent` (~4407), `PUT location` (~4478), `PUT datetime` (~4533), `PUT messages/{index}` (~4765), `DELETE messages/{index}` (~4827), `retcon` (~4865), `POST replay` (~4953), alternates promotion (~1319, inside its own hold), `PUT chronicle` (~4056, inside its hold), `POST absorb` (~2484), and step 2's `PUT messages/{index}/excluded`
- Modify: `backend/src/grimoire/routes/mechanics.py` (`POST roll` ~61, `POST check` ~363, inside their holds)
- Modify: `backend/src/grimoire/routes/greetings.py` (~505 and ~616)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`DELETE responses/{rid}` ~877, `…/activate` ~894 — swipes shape the transcript, gate 10)
- Test: `backend/tests/test_branch_routes.py` (new)

**Interfaces:**
- Produces: `runs.require_scene_open(cid: str, sid: str) -> None` — `by = scenes.read.closed_by(cid, sid)`; if set, `HTTPException(409, {"kind": "branch_closed", "closed_by": by, "detail": "a sibling branch of this scene was absorbed; this branch is read-only"})`.
- Produces: `runs.scene_held_open(app, cid: str, sid: str)` — context manager: `store.locks.campaign_lock(cid)`, `require_scene_free(app, cid, sid)`, `require_scene_open(cid, sid)`, `yield`. Doors that used `scene_held_free` switch to it; doors that call `require_scene_free` inside their own hold add `require_scene_open(cid, sid)` right after it. Scene rename (`PUT /scenes/{sid}`), scene delete, `replay/accept`, `replay/cancel` and `passage_characters.save_character` keep `scene_held_free`.
- Produces: `runs.require_group_free(app, cid: str, sid: str) -> None` — for every other `list_scenes` row sharing this scene's `branch_group`, `require_scene_free(app, cid, member)`.
- Changes: `reserve_turn` runs `require_scene_open(cid, sid)` inside `with store.locks.campaign_lock(cid):` around its `_reserve` call (reentrant), so a close and a reservation cannot interleave. `reserve_review` is unchanged.
- Changes: `PUT chronicle` calls `require_scene_open` then `require_group_free` right after its existing `require_scene_free`. `POST absorb` wraps `require_scene_open`, `require_group_free` and `runs.reserve_review(...)` in one `store.locks.campaign_lock(cid)` hold.

- [ ] **Step 1: Write the failing tests** in `test_branch_routes.py`. Fixture `closed(client)`: copied `seed`, a played post in `sid`, `b = store.branch.branch_scene(cid, sid, 0)`, then `store.scenes.mark_absorbed(cid, sid, "x", "y")`; returns `(cid, sid, b)` — the absorbed member, then the closed one.

```python
def test_every_transcript_door_refuses_a_closed_scene(closed, client):
    """One row per door, as test_scene_freeze.py: the guard is per call site."""
    cid, absorbed, sid = closed
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    calls = [
        ("post", f"{base}/chat", {"content": "Hi"}),
        ("post", f"{base}/retry", None),
        ("post", f"{base}/regenerate", None),
        ("post", f"{base}/responses/nope/regenerate", {}),
        ("post", f"{base}/replay/turn", None),
        ("post", f"{base}/roll-proposal", {}),
        ("put", f"{base}/messages/0", {"content": "edited"}),
        ("delete", f"{base}/messages/0", None),
        ("post", f"{base}/messages/0/retcon", {"content": "retconned"}),
        ("put", f"{base}/messages/0/excluded", {"excluded": True}),
        ("post", f"{base}/alternates/v-nope", None),
        ("post", f"{base}/replay", {"index": 0}),
        ("post", f"{base}/roll", {"notation": "1d20"}),
        ("post", f"{base}/cast", {"kind": "characters", "id": "nobody", "version": "default", "role": "npc"}),
        ("delete", f"{base}/cast/characters/x", None),
        ("post", f"{base}/cast/batch", {"refs": []}),
        ("post", f"{base}/cast/emergent", {"name": "Winifred", "role": "npc"}),
        ("put", f"{base}/location", {"location": "saltmarch-docks"}),
        ("put", f"{base}/datetime", {"datetime": "1834-04-02"}),
        ("delete", f"{base}/responses/nope", None),
        ("post", f"{base}/responses/nope/variants/v/activate", None),
        ("post", f"{base}/absorb", None),
        ("put", f"{base}/chronicle", {"one_line": "x", "summary": "y", "keywords": [],
                                      "timeline_events": [], "edits": [], "commit_token": "t"}),
        ("post", f"{base}/first-post", {"text": "The lamps are lit."}),
        ("post", f"{base}/start-from-greeting", {"greeting": "g1"}),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 409, f"{method} {path} answered {r.status_code}"
        assert r.json()["kind"] == "branch_closed", f"{method} {path}: {r.json()}"
        assert r.json()["closed_by"]["sid"] == absorbed
```

A row that answers some other 4xx first gets a valid body — never a dropped row. The manual check needs a bound module and is its own test, set up exactly as `test_a_manual_check_is_refused_while_a_turn_holds_the_scene` in `test_scene_freeze.py`. Also:

```python
def test_a_closed_scene_can_still_be_renamed_deleted_and_its_replay_stopped(closed, client): ...
    # PUT base {"title": "Winifred"} -> 200; replay accept/cancel answer a kind other than
    # "branch_closed" (no_replay); DELETE base -> 200
def test_cutting_into_the_absorbed_member_reopens_the_others(closed, client): ...
    # DELETE /scenes/{absorbed}/messages/0 -> 200 (un-absorbs it); then PUT /scenes/{closed}/messages/0 -> 200
def test_absorbing_waits_for_every_member_of_the_group(client):
    # open group (nothing absorbed); a live turn on the sibling via
    # client.app.state.runs.start_or_existing(("scene", cid, ident), "turn", "chat", "a1", ident, labels)
    # (held_scene's pattern); PUT /scenes/{source}/chronicle -> 409 "scene_busy";
    # POST /scenes/{source}/absorb -> 409 "scene_busy"
def test_an_open_member_is_not_refused(client): ...   # unabsorbed group: PUT messages/0 on either -> 200
```

- [ ] **Step 2: Run, verify FAIL** — the doors answer 200/404 instead of 409.
- [ ] **Step 3: Implement** the three functions and switch each door.
- [ ] **Step 4: Run** `tests/test_branch_routes.py tests/test_scene_freeze.py tests/test_scene_store.py tests/test_cascade_store.py` and every `tests/test_*routes*.py` touching scenes (`-k "scene or greeting or roll or check or response"`) — PASS.
- [ ] **Step 5: Commit** `feat(routes): a closed branch is read-only`.

### Task 5: `POST .../branch` and `closed_by` on the scene payload

**Files:**
- Modify: `backend/src/grimoire/routes/models.py` (new `BranchScene`)
- Modify: `backend/src/grimoire/routes/scenes.py` (new `post_branch`; `get_scene` adds `closed_by`)
- Test: `backend/tests/test_branch_routes.py`, `backend/tests/test_scene_freeze.py` (one row)

**Interfaces:**
- Produces: `class BranchScene(BaseModel): through: int; title: str = ""`.
- Produces: `POST /campaigns/{cid}/scenes/{sid}/branch` → `{"id": new_sid, "scene": store.scenes.read_scene(cid, new_sid)}`. Body: `_require_scene(cid, sid)`; `with runs.scene_held_open(request.app, cid, sid): new = store.branch.branch_scene(cid, sid, body.through, title=body.title.strip())`. `BranchRefused` → 409 `{"kind": exc.kind, "detail": exc.detail}`; `IndexError` → 400 `"message index out of range"`; `SceneNotFound`/`CampaignNotFound` → 404. No middleware change — the revision stamp covers a 2xx POST under `/api/campaigns/`.
- Produces: `get_scene` sets `scene["meta"]["closed_by"]` from `store.scenes.read.closed_by(cid, sid)` only when set (both the whole and the windowed shapes).

- [ ] **Step 1: Write the failing tests:**

```python
def test_branch_route_answers_the_sibling(client):
    cid, sid = seed(client); store.scenes.append_message(cid, sid, "user", "Mara waits.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/branch", json={"through": 0})
    assert r.status_code == 200
    assert r.json()["scene"]["meta"]["title"] == "Mara (branch)"
    assert r.json()["id"] in {s["id"] for s in store.scenes.list_scenes(cid)}

def test_branch_route_refusals(client): ...
    # absorbed source -> 409 "absorbed_use_fork"; {"through": 9} -> 400; closed -> 409 "branch_closed"
def test_branch_route_takes_a_title(client): ...      # {"through": 0, "title": "Winifred"} -> title "Winifred"
def test_branch_route_stamps_the_campaign_revision(client): ...
    # store.revision.current(cid) differs before/after
def test_the_scene_payload_names_who_closed_it(closed, client): ...
    # GET /scenes/{sid} -> meta["closed_by"] == {"sid": absorbed, "title": …}; open scene has no key
```

Add `("post", f"{base}/branch", {"through": 0})` to the `calls` list in `test_scene_freeze.py::test_every_shape_change_is_refused_while_a_run_holds_the_scene` (→ 409 `scene_busy`), and the same row is already in Task 4's door table.

- [ ] **Step 2: Run, verify FAIL** — 404/405 on the new route.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_branch_routes.py tests/test_scene_freeze.py tests/test_routing_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(routes): branch a scene from a post`.

### Task 6: Fork at a post (`from_index`) and number-based cuts

**Files:**
- Modify: `backend/src/grimoire/store/fork.py` (`fork_campaign`, `_check_source`, `_cut_after`; import `from .scene_ids` via `from . import scene_ids` and `from .tracker import walk as tracker_walk`)
- Modify: `backend/src/grimoire/routes/models.py` (`ForkCampaign.from_index: int | None = None`)
- Modify: `backend/src/grimoire/routes/campaigns.py` (fork route ~973)
- Test: `backend/tests/test_fork_store.py`, fork route test beside the existing ones (grep `"/fork"` under `backend/tests/`)

**Interfaces:**
- Changes: `fork_campaign(cid, name, from_scene=None, key="", expect_revision="", from_index: int | None = None) -> dict`.
- Changes: `_check_source(cid, from_scene, expect_revision, from_index=None)` — when `from_index is not None`: `from_scene` required (else `ValueError`), and `0 <= from_index < len(read_scene(cid, from_scene)["messages"])` else `IndexError(from_index)`. Runs before the claim, so a bad index copies nothing.
- Changes: `_cut_after(cid, from_scene)` — `later` = every scene whose `scene_ids.parse_sid` number is greater than the fork scene's, **plus** every other scene with the same number; ids that do not parse fall back to the string comparison used today.
- Produces: after `_cut_after`, when `from_index is not None and from_index < len(messages) - 1`: `cascade.delete_from(new_cid, from_scene, from_index + 1)` (its `records` added, `refused` extended, `failed` prefixed `f"{from_scene}/{step}"`), then `tracker_walk.prune(new_cid, from_scene)` in `try/except Exception` (a failure appends `f"{from_scene}/tracker"`). The report gains `"cut_at": from_index` whenever `from_index` is given. `_REPORT_FIELDS` is **not** changed (`cut_at` stays optional for old markers).
- Route: passes `from_index=body.from_index`; `IndexError` → 400 `"message index out of range"` (the existing `ValueError` → 400 covers the missing-scene case).

- [ ] **Step 1: Write the failing tests** in `test_fork_store.py` (existing `cid`, `_played`, `_sids`):

```python
def _tree(cid):
    root = campaigns.campaign_root(cid)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

def test_a_fork_at_a_post_keeps_the_scene_through_that_post(cid):
    one = _played(cid, "Mara", posts=4); two = _played(cid, "Winifred")
    scenes.mark_absorbed(cid, one, "They swore.", "A long night.")
    before = _tree(cid)
    out = fork.fork_campaign(cid, "Branch", from_scene=one, from_index=1)
    child = out["id"]
    assert out["cut_at"] == 1 and out["removed_scenes"] == [two]
    assert [m["content"] for m in scenes.read_scene(child, one)["messages"]] == ["Mara post 0", "Mara post 1"]
    assert "done" not in scenes.read_scene_meta(child, one)          # un-absorbed in the copy
    assert _tree(cid) == before                                       # source byte-identical

def test_a_fork_at_a_post_reverses_that_posts_absorb_writes(cid): ...
    # the `absorbed` writes from test_cascade_store.py (`_absorb_lore_edit`, chronicle.absorb with
    # the scene id): in the child the lore body is back to "old body" and the chronicle has no row for `one`
def test_a_fork_at_the_last_post_cuts_nothing(cid): ...   # from_index = len-1: transcript whole, done kept, cut_at present
def test_an_out_of_range_index_copies_nothing(cid): ...   # IndexError; no new campaign on the shelf
def test_a_fork_removes_the_scenes_alternatives_by_number(cid): ...
    # one, a branch of one via store.branch.branch_scene, two: fork at one ->
    # removed_scenes contains the branch and two, not one
def test_a_marker_without_cut_at_still_replays(cid): ...  # existing keyed-replay marker shape passes _whole
```

Route: `POST /api/campaigns/{cid}/fork {"name": "B", "from_scene": sid, "from_index": 99}` → 400; `{"name": "B", "from_index": 0}` → 400.

- [ ] **Step 2: Run, verify FAIL** — `TypeError: unexpected keyword 'from_index'`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_fork_store.py tests/test_cascade_store.py tests/test_import_guard.py` plus the fork route test file — PASS.
- [ ] **Step 5: Commit** `feat(fork): fork a campaign at a post`.

### Task 7: Replay inside a branch (#151)

**Files:**
- Modify: `backend/src/grimoire/store/replay.py` (extract `check_begin`; `begin` calls it)
- Modify: `backend/src/grimoire/routes/models.py` (`ReplayStart.branch: bool = False`)
- Modify: `backend/src/grimoire/routes/scenes.py` (`post_replay`, ~4934)
- Test: `backend/tests/test_replay_store.py`, `backend/tests/test_branch_routes.py`

**Interfaces:**
- Produces: `replay.check_begin(cid: str, sid: str, index: int) -> None` — the refusals `begin` makes today, in its order (running session → `ReplayError`, `SceneNotFound`, `IndexError`, `_moves` → `ReplayError(BLOCKED_TRANSITION)`, no generation step → `ReplayError`), under `locks.campaign_lock(cid)`. `begin` calls it first and keeps its own body otherwise.
- Changes: `post_replay`, inside the existing hold (now `runs.scene_held_open`, Task 4): when `body.branch` — `store.replay.check_begin(cid, sid, body.index)`; `new = store.branch.branch_scene(cid, sid, len(messages) - 1)`; `try: report = store.replay.begin(cid, new, body.index)` / `except BaseException: store.scenes.delete_scene(cid, new); raise`; `tracker_routes.after_cut(cid, new)`; return `{**report, "branched": new}`. `BranchRefused` → 409 `{"kind": exc.kind, …}`. Without `branch`, today's path unchanged.

- [ ] **Step 1: Write the failing tests:**

```python
# test_replay_store.py
def test_check_begin_refuses_what_begin_refuses_and_writes_nothing(cid, sid): ...
    # a second session running -> ReplayError, replay.read(cid) unchanged; transcript bytes unchanged

# test_branch_routes.py
def test_replay_in_a_branch_leaves_the_original_byte_identical(client):
    cid, sid = seed(client)
    for role, text in (("user", "Mara waits."), ("assistant", "The tide turns."),
                       ("user", "She runs."), ("assistant", "The bell rings.")):
        store.scenes.append_message(cid, sid, role, text, **({"speaker": "Mara"} if role == "assistant" else {}))
    path = store.scenes._scene_path(cid, sid); before = path.read_bytes()
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/replay", json={"index": 2, "branch": True})
    assert r.status_code == 200
    new = r.json()["branched"]
    assert new != sid and path.read_bytes() == before
    assert store.replay.state(cid)["scene"] == new
    assert len(store.scenes.read_scene(cid, new)["messages"]) == 2

def test_replay_without_branch_behaves_as_today(client): ...   # cuts sid in place, no "branched"
def test_a_refused_branch_replay_creates_no_sibling(client): ...
    # index that leaves no generation step -> 409 "replay_refused"; scene ids unchanged
def test_a_branch_replay_of_an_absorbed_scene_is_refused(client): ...  # 409 "absorbed_use_fork"
def test_begin_failing_after_the_branch_deletes_it(client, monkeypatch): ...
    # patch store.replay.begin to raise ReplayError -> 409; scene ids unchanged
```

- [ ] **Step 2: Run, verify FAIL** — `KeyError: 'branched'`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_replay_store.py tests/test_branch_routes.py tests/test_scene_freeze.py` — PASS.
- [ ] **Step 5: Commit** `feat(replay): replay inside a branch`.

### Task 8: Client API, types, scene-list chips and the replay option

**Files:**
- Modify: `frontend/src/api/types.ts` (`SceneMeta`, `ForkReport`, a `ReplayStarted` alias)
- Modify: `frontend/src/api/client.ts` (`branchScene`, `forkCampaign`, `startReplay`)
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`branchScene: vi.fn()`), `frontend/src/testkit/campaignHarness.tsx` (default in `installCampaignMocks`)
- Modify: `frontend/src/routes/ScenesView.tsx` (chips)
- Modify: `frontend/src/components/ReplayPanel.tsx` (branch option)
- Test: `frontend/src/api/client.test.ts`, `frontend/src/routes/ScenesView.test.tsx`, `frontend/src/components/ReplayPanel.test.tsx`

**Interfaces:**
- `SceneMeta` gains `branch_group?: string; branch_of?: string; closed_by?: { sid: string; title: string }`. `ForkReport` gains `cut_at?: number`. `ReplayStarted = ReplaySession & { cascade: CascadeReport; branched?: string }` (the return type of `startReplay`).
- `branchScene(cid: string, sid: string, through: number, title?: string) => request<{ id: string; scene: ScenePage }>("POST", `/api/campaigns/${cid}/scenes/${sid}/branch`, { through, ...(title ? { title } : {}) }).then(notifyShell)`.
- `forkCampaign(cid, name, fromScene?, guards: ForkGuards = {}, fromIndex?: number)` — adds `from_index` to the body only when `fromIndex !== undefined`.
- `startReplay(cid, sid, index, branch = false)` — body `{ index, ...(branch ? { branch: true } : {}) }`.
- Harness default: `(api.branchScene as any).mockResolvedValue({ id: "s1-b", scene: { meta: {}, messages: [] } })`.
- `ScenesView`: count rows per `branch_group`; a row whose group has more than one row shows `<span className="chip">branch</span>`; a row with `closed_by` shows `<span className="chip" title="A sibling branch was absorbed">closed</span>` in place of the `open` status chip (its `waiting` unreviewed chip still wins).
- `ReplayPanel` gains props `branchable?: boolean` (default `false`) and `onBranched?: (sid: string) => void`. When `branchable`, a checkbox labelled `Replay in a branch (keeps this scene)`, checked by default, renders above the actions, and the start button reads `Replay in a branch` while it is checked (`Replay in place` otherwise). `start` calls `api.startReplay(cid, sid, startAt, true)` when checked and the three-argument form otherwise (existing `toHaveBeenCalledWith("c1", "s1", 3)` assertions must still hold); a result carrying `branched` calls `onStartHandled()` then `onBranched(result.branched)` instead of `refresh()`.

- [ ] **Step 1: Write the failing tests:**
  - `client.test.ts`: `branchScene("c1", "s1", 2)` POSTs `{through: 2}` to `/api/campaigns/c1/scenes/s1/branch`; `forkCampaign("c1", "B", "s1", {}, 3)` body contains `from_index: 3`, and without it has no `from_index`; `startReplay("c1", "s1", 2, true)` body `{index: 2, branch: true}`, `startReplay("c1","s1",2)` body `{index: 2}`.
  - `ScenesView.test.tsx`: `listScenes` → two rows sharing `branch_group: "g"` (one `done: true`, the other `closed_by: {sid, title}`) and one ungrouped row: two `branch` chips; one `closed` chip with title `A sibling branch was absorbed`; the ungrouped row has neither. A group with a single row shows no `branch` chip.
  - `ReplayPanel.test.tsx`: with `branchable` and `startAt={3}`, the checkbox is checked; clicking `Replay in a branch` calls `startReplay("c1", "s1", 3, true)`; with `startReplay` resolving `{ cut: 3, cascade: {}, branched: "s1-b" }`, `onBranched` gets `"s1-b"`. Unchecking it and clicking `Replay in place` calls `startReplay("c1", "s1", 3)`. Without `branchable` there is no checkbox.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `src/api/client.test.ts src/routes/ScenesView.test.tsx src/components/ReplayPanel.test.tsx` and `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(web): branch api, scene chips and replay-in-a-branch option`.

### Task 9: Play view — ⑂, the absorbed fork path, and closed scenes

**Files:**
- Modify: `frontend/src/components/play/TranscriptPost.tsx` (`TranscriptActions.branchFrom`, the ⑂ button beside ⏩)
- Modify: `frontend/src/routes/CampaignView.tsx` (`branchFrom`, `forkAtScene`, `activeClosed`, banner, scene-head chips, hotkeys, `ReplayPanel` props)
- Test: `frontend/src/routes/CampaignView.test.tsx` (harness: `installCampaignMocks`, `renderCampaign`, `here` from `testkit/campaignHarness.tsx`)

**Interfaces:**
- `TranscriptActions.branchFrom: (index: number) => void`. In the gutter, beside ⏩ and under the same `active` condition (so it is on every post of an active scene, roll lines included): `<button className="msg-edit" title="Branch from here" aria-label={`Branch from message ${index + 1}`} disabled={rolling} onClick={() => actions.branchFrom(index)}>⑂</button>`.
- `CampaignView.branchFrom(index)`: when `activeDone`, `forkAtScene(activeId, index)`; else `api.branchScene(cid, activeId, index)` → `await loadScenes()` → `navigate(sceneUrl(cid, result.id))`; errors through `fail(err, false)`.
- `forkAtScene(sid: string, fromIndex?: number)`: with `fromIndex`, the confirm text opens with `This scene is absorbed, so branching it from this post makes a copy of the campaign cut at that post.` and the call is `api.forkCampaign(cid, forkName, sid, {}, fromIndex)`. The `later` count compares scene numbers (`sceneNumber` from `routes/sceneNumber.ts`), counting same-number siblings too, to match the server's cut (gate 9).
- `activeClosed = scenes.find((s) => s.id === activeId)?.closed_by ?? null` (memoized like `activeDone`). When set:
  - the composer is replaced as for `activeDone`, by a banner: `<div className="scene-complete scene-closed">A sibling branch was absorbed: <Link to={sceneUrl(cid, activeClosed.sid)}>{activeClosed.title}</Link>. This branch is read-only — delete it to discard it.</div>`;
  - the transcript context's `active` is `transcriptIsActive && !activeClosed` (no gutter actions, no `ResponseControls`, no swipe arrows); the swipe gesture's enabled flag and the variant swipe both carry `!activeClosed`;
  - hotkeys: `mod+enter` gains `!activeClosed` beside `!activeDone`; `r`, `arrowleft`, `arrowright` gain `!activeClosed` in `enabled`. (Step 4's Keep-writing binding will carry the same condition.)
- Scene head (`.scene-head`, beside the Offscreen badge): `branch` chip when the active scene's group has more than one row in `scenes`, `closed` chip (title `A sibling branch was absorbed`) when `activeClosed`.
- `ReplayPanel` is passed `branchable={!activeDone}` and `onBranched={(sid) => { void loadScenes(); navigate(sceneUrl(cid, sid)); }}`.

- [ ] **Step 1: Write the failing tests** in `CampaignView.test.tsx`:

```tsx
test("⑂ on an unabsorbed scene branches and opens the sibling", async () => {
  installCampaignMocks();
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Branch from message 1" }));
  await waitFor(() => expect(api.branchScene).toHaveBeenCalledWith("run", "s1", 0));
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
});
```

  - ⑂ on an absorbed scene (`listScenes` → `[{ ...ONE_SCENE[0], done: true }]`, `window.confirm`/`window.prompt` stubbed to accept and answer `"Run One (fork)"`): `api.forkCampaign` is called with `("run", "Run One (fork)", "s1", {}, 1)` for message 2, `api.branchScene` is not, and the confirm text contains `copy of the campaign`;
  - a closed scene (`listScenes` → `[{ ...ONE_SCENE[0], branch_group: "g", closed_by: { sid: "s0", title: "Mara" } }, { id: "s0", title: "Mara", done: true, branch_group: "g", … }]`): the banner text `A sibling branch was absorbed` with a link to `/campaigns/run/scenes/s0`, no composer textarea, no `Branch from message 1` button, a `closed` chip in the scene head; pressing `r` opens no reroll box;
  - the replay dialog on an unabsorbed scene: click ⏩, the panel's checkbox is checked, `Replay in a branch` calls `api.startReplay("run", "s1", <index>, true)`, and with it resolving `{ …, branched: "s1-b" }` the view navigates to `/campaigns/run/scenes/s1-b`. (The panel's own behaviour is Task 8's; this pins the wiring.)
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `src/routes/CampaignView.test.tsx src/routes/CampaignView.shortcuts.test.tsx src/routes/CampaignView.render.test.tsx src/components/review/SceneReview.test.tsx src/components/ReplayPanel.test.tsx` and `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): branch from here, and closed branches`.

### Task 10: Gate

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python`. Fix what fails; if a ratchet reports a changed count, `make baseline` and commit the new `lint-baselines/*.json` with the fix that moved it. Nothing else in this task.
