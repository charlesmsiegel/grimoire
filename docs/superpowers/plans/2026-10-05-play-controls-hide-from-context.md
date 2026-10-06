# Play controls II — hide a post from context: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a player mark a player or model post *excluded*: it stays in the transcript, the play view and every export (marked), and reaches no prompt.

**Architecture:** The flag is one more key in the per-post `<!-- grimoire-response {json} -->` metadata comment (`serialize.RESPONSE_METADATA`), stored as the ISO time it was set. Four helpers in `store/scenes/serialize.py` carry every rule (`is_excluded`, `without_excluded`, `in_context`, `excluded_since`); every LLM input filters through them, the three staleness digests hash the flag when present, the response ledger carries it across variant rebuilds, and a frozen prompt that predates an exclusion is refused rather than replayed. A `PUT .../messages/{index}/excluded` route toggles it under `runs.scene_held_free`; the play view adds a gutter toggle.

**Tech Stack:** FastAPI + pytest (`backend/`), React + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-hide-from-context-design.md` (its Gate record resolutions are binding).

## Global Constraints

- Excludable: player posts and model posts only. A roll line (`ROLL_SPEAKER`), a transition line (`TRANSITION_SPEAKER`) and a director note (`DIRECTOR_SPEAKER`) are refused **400 `not_excludable`** — i.e. anything in `serialize.SYNTHETIC_SPEAKERS`.
- Stored value: the ISO time it was excluded (`paths.now_iso()`); an included post has **no key** — `excluded: false` is never written.
- Exclusion belongs to the post slot and the whole response: toggling any part sets every part with that `response_id`; a new part inherits; `activate` keeps it if any part had it; `save_variant`, `activate`, `publish_saved` carry it through `responses._message`.
- Frozen prompt rule: a reroll of a response whose record `created` is not later than the latest currently-excluded post before it is refused **409 `context_excluded`**, detail exactly: `A post this reply was written from is now hidden. Replay from here to regenerate without it.` (ties at the same second refuse — see resolved ambiguities).
- Refusals on the toggle: **404** unknown scene, **400** index out of range, **400 `not_excludable`**, **409 `scene_absorbed`**, **409 `round_open`**, **409 `scene_busy`** (freeze row).
- `chronicle.transcript_text` filters excluded posts **by default**; only the markdown and plain-text exports pass `include_excluded=True`.
- Excluding is not deleting: `turn_sizes`, reroll targeting, the ledger, attempts, pins and usage attribution by post index are unchanged. `tracker/walk._tracked` is **unchanged** (prune must keep an excluded post's records).
- Digests (`rolling_summary.covered_digest`, `pending_reviews.watermark`, `responses.transcript_hash`) add the flag only when present: an unflagged transcript hashes byte-identically to before.
- Export marker line: `*(not in context)*` (markdown, plain text); HTML/EPUB: class `excluded` + a `not in context` tag; JSON: verbatim.
- Legacy alternates (`alternates.promote`) lose the flag — stated, not fixed.
- Project rules: every store write through `store.atomic`; scene mutators wear `@locking._serialized`; imports at module scope, inside `store/` bind submodules (`test_import_guard.py`) — `rolling_summary` and `pending_reviews` must **not** import `scenes` (scenes → write → rolling_summary would cycle), so they test `m.get("excluded")` inline; pydantic plain `BaseModel` fields only, responses as plain dicts; no new keyboard binding (if one is ever added, `useHotkeys` only); placeholder names only (Mara, Winifred, Seraphine, Realm, Saltmarch); ratchet gates — if a lint/mypy/eslint count moves, `make baseline` and commit `lint-baselines/*.json` with the change.
- Test commands: backend `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`; frontend `cd frontend && npx vitest run <files>` plus `npm run typecheck`.

## Review Focus

1. A reroll whose frozen snapshot predates an exclusion must be refused, not silently replay the hidden post into a new variant — Task 4 (`test_reroll_of_a_reply_composed_before_an_exclusion_is_refused`).
2. Swiping, rerolling or activating a variant of an excluded (possibly multi-part) response must keep it excluded; `_message` rebuilds the dict and drops unknown keys — Task 2 (`test_activate_keeps_the_flag_*`, `test_a_new_part_inherits_the_flag`).
3. Shipping the digest change must not invalidate every stored rolling summary, scene-break watermark and pending review on upgrade — Task 3 (`test_unflagged_digests_match_the_previous_formula`).
4. Replaying an excluded model turn must land through `append_reply` so `turn_sizes` stays in step; otherwise the next reroll raises `TurnSizesDesynced` — Task 7 (`test_kept_step_lands_with_its_turn_boundary`).
5. Exporting and re-importing a scene must not bring hidden posts back into context — Task 8 (`test_markdown_export_reimports_excluded`).

---

### Task 1: The flag and its helpers in `serialize`

**Files:**
- Modify: `backend/src/grimoire/store/scenes/serialize.py`
- Modify: `backend/src/grimoire/store/scenes/__init__.py` (re-export the new names in the `from .serialize import (...)` block)
- Test: `backend/tests/test_hide_from_context.py` (new)

**Interfaces:**
- Produces: `RESPONSE_METADATA` gains `"excluded"` (appended last).
- Produces: `EXCLUDED_MARKER = "*(not in context)*"` (shared by export and import, so neither imports the other).
- Produces: `excludable(m: dict) -> bool` — `m.get("speaker") not in SYNTHETIC_SPEAKERS`.
- Produces: `is_excluded(m: dict) -> bool` — `bool(m.get("excluded"))`.
- Produces: `without_excluded(messages: list[dict]) -> list[dict]` — drops excluded posts only (keeps director notes).
- Produces: `in_context(messages: list[dict]) -> list[dict]` — drops excluded posts **and** director notes.
- Produces: `excluded_since(messages: list[dict], before_index: int) -> str | None` — the greatest `str(m["excluded"])` over excluded posts in `messages[:before_index]`, else `None`. (A hand-edited non-string truthy value stringifies to e.g. `"True"`, which sorts after any ISO time and so refuses — the safe direction.) Step 4's Keep writing reuses this through Task 4's `responses.require_context_included`.
- Modifies: `_message_block(m)` writes a metadata key only when present **and**, for `"excluded"`, truthy — `{k: m[k] for k in RESPONSE_METADATA if k in m and (k != "excluded" or m[k])}`.

- [ ] **Step 1: Write the failing tests** in `test_hide_from_context.py`:

```python
from grimoire.store.scenes import serialize

STAMP = "2026-10-05T12:00:00Z"

def _round_trip(messages):
    return serialize._parse_messages(serialize._serialize_messages(messages), frozenset())

def test_flag_round_trips_on_player_model_and_response_posts():
    out = _round_trip([
        {"role": "user", "speaker": "You", "content": "ooc: brb", "excluded": STAMP},
        {"role": "assistant", "speaker": "Mara", "content": "Hm.", "excluded": STAMP},
        {"role": "assistant", "speaker": "Winifred", "content": "Go.", "response_id": "a" * 32,
         "excluded": STAMP},
    ])
    assert [m.get("excluded") for m in out] == [STAMP, STAMP, STAMP]
    assert all("grimoire-response" not in m["content"] for m in out)

def test_excluded_false_writes_no_key():
    body = serialize._serialize_messages([{"role": "assistant", "speaker": "Mara",
                                           "content": "Hm.", "excluded": False}])
    assert "grimoire-response" not in body
    assert "excluded" not in _round_trip([{"role": "assistant", "speaker": "Mara",
                                           "content": "Hm.", "excluded": False}])[0]

def test_without_excluded_keeps_director_notes_and_in_context_drops_them(): ...
    # [note, excluded post, plain post]: without_excluded -> [note, plain]; in_context -> [plain]

def test_excludable_refuses_the_three_synthetic_speakers(): ...
    # ROLL_SPEAKER, TRANSITION_SPEAKER, DIRECTOR_SPEAKER -> False; "Mara", None -> True

def test_excluded_since_is_the_latest_stamp_before_the_index(): ...
    # stamps "2026-10-05T10:00:00Z" at 0, "2026-10-05T11:00:00Z" at 2, STAMP at 4:
    # excluded_since(ms, 4) == "2026-10-05T11:00:00Z"; excluded_since(ms, 0) is None
```

- [ ] **Step 2: Run, verify FAIL** — `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_hide_from_context.py` (`AttributeError: ... is_excluded`, flag dropped on parse).
- [ ] **Step 3: Implement** the names above in `serialize.py` beside `is_director_note`, and re-export `EXCLUDED_MARKER, excludable, excluded_since, in_context, is_excluded, without_excluded` from `store/scenes/__init__.py`.
- [ ] **Step 4: Run** the new file plus `tests/test_scene_store.py tests/test_scene_import_store.py tests/test_replay_store.py` (round-trip neighbours) — PASS.
- [ ] **Step 5: Commit** `feat(scenes): an excluded flag in post metadata, and the helpers that read it`.

### Task 2: The store mutator and the ledger carrying the flag

**Files:**
- Modify: `backend/src/grimoire/store/scenes/write.py` (`set_excluded`, `NotExcludable`)
- Modify: `backend/src/grimoire/store/scenes/__init__.py` (re-export both from the `.write` block)
- Modify: `backend/src/grimoire/store/responses.py` (`_message`, new `_excluded_of`, `save_variant`, `activate`, `publish_saved`)
- Test: `backend/tests/test_hide_from_context.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `class NotExcludable(Exception)` in `write.py`.
- Produces: `@locking._serialized def set_excluded(cid: str, sid: str, index: int, excluded: bool) -> bool` — returns whether anything changed. Parse exactly as `edit_message` does (`frozenset(cast.player_names(cid, sid))`, `serialize._parse_messages`); `IndexError` out of range; `NotExcludable` when `not serialize.excludable(messages[index])`. The group is every index sharing the target's `response_id` (or just `[index]`). If every member already has the requested state, return `False` without writing. Otherwise set `paths.now_iso()` on members lacking a value (keep existing stamps) or pop the key; flag `context_changed = True` on every later message with a `response_id` (after the group's last index); stamp `meta["updated"]`; one `atomic.write_text`. `turn_sizes` untouched.
- Produces: `responses._excluded_of(messages: list[dict], rid: str) -> str | None` — greatest truthy `excluded` among that response's parts.
- Modifies: `responses._message(record, content, status, variant=None, excluded=None)` adds `"excluded": excluded` only when truthy.
- Modifies: `save_variant` and `publish_saved` pass `excluded=_excluded_of(messages, rid)` (read before the write; covers both replacing a part and appending a new one); `activate` computes it from the pre-collapse `messages` and passes it to the collapsed message.

- [ ] **Step 1: Write the failing tests** (reuse `seed` from `tests/test_character_turns.py` and `_answer` from `tests/test_response_controls_routes.py`):

```python
def test_set_excluded_sets_and_clears_and_is_idempotent(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    assert store.scenes.set_excluded(cid, sid, 0, True) is True
    stamp = store.scenes.read_scene(cid, sid)["messages"][0]["excluded"]
    assert store.scenes.set_excluded(cid, sid, 0, True) is False
    assert store.scenes.read_scene(cid, sid)["messages"][0]["excluded"] == stamp
    assert store.scenes.set_excluded(cid, sid, 0, False) is True
    assert "excluded" not in store.scenes.read_scene(cid, sid)["messages"][0]

def test_set_excluded_refuses_roll_transition_and_note_lines(client): ...
    # append_message with speaker ROLL_SPEAKER / TRANSITION_SPEAKER / DIRECTOR_SPEAKER;
    # each raises store.scenes.NotExcludable; index 99 raises IndexError

def test_toggling_one_part_sets_every_part(client): ...
    # drive the roll fence + decline as test_roll_decline_continues_same_actor_then_handoff does
    # (two parts "Wait." / "No roll." sharing a response_id); set_excluded on the FIRST part;
    # both parts carry the flag

def test_a_new_part_inherits_the_flag(client): ...
    # _answer a reply; set_excluded on it; save_variant(cid, sid, rid, "More.", "complete", part="p2")
    # -> the appended "More." message has "excluded"

def test_activate_keeps_the_flag_on_a_swiped_response(client): ...
    # _answer; save_variant(..., "Replacement.", "complete", activate=False); exclude; POST
    # .../variants/{vid}/activate -> 200, the message reads "Replacement." and is still excluded

def test_reroll_keeps_the_flag(client): ...
    # _answer; exclude the reply; POST .../responses/{rid}/regenerate with a FakeLLM answering
    # 'Again.\n```handoff\n{"next":null}\n```' -> message "Again." still excluded

def test_a_later_response_is_flagged_context_changed(client): ...
    # chat "Hello" (index 0) + reply; exclude index 0; the reply's "context_changed" is True
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** as specified above.
- [ ] **Step 4: Run** `tests/test_hide_from_context.py tests/test_responses.py tests/test_response_controls_routes.py tests/test_character_turns.py` — PASS.
- [ ] **Step 5: Commit** `feat(scenes): set_excluded, and every variant rebuild keeps the flag`.

### Task 3: The flag in the three staleness digests

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (`transcript_hash`)
- Modify: `backend/src/grimoire/store/rolling_summary.py` (`covered_digest`)
- Modify: `backend/src/grimoire/store/pending_reviews.py` (`watermark`)
- Test: `backend/tests/test_hide_from_context.py`

**Interfaces:**
- Modifies: `transcript_hash(messages)` — each public dict gains `"excluded": True` only when `m.get("excluded")`.
- Modifies: `covered_digest(messages, player_label="")` and `watermark(messages)` — each per-message list gains a trailing `True` only when `m.get("excluded")` (inline test, no `scenes` import — see Global Constraints).

- [ ] **Step 1: Write the failing tests:**

```python
MSGS = [{"role": "user", "speaker": "You", "content": "hi"},
        {"role": "assistant", "speaker": "Mara", "content": "Hm."}]

def test_unflagged_digests_match_the_previous_formula():
    old_hash = hashlib.sha256(json.dumps(
        [{k: m[k] for k in ("role", "speaker", "content")} for m in MSGS],
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert store.responses.transcript_hash(MSGS) == old_hash
    old_cov = hashlib.sha256(json.dumps(
        [[[m["role"], m["speaker"], m["content"]] for m in MSGS], "Mara"],
        ensure_ascii=False).encode("utf-8")).hexdigest()
    assert store.rolling_summary.covered_digest(MSGS, "Mara") == old_cov
    h = hashlib.sha256()
    for m in MSGS:
        h.update(json.dumps([m["role"], m["speaker"], m["content"]], ensure_ascii=False).encode("utf-8"))
        h.update(b"\x1e")
    assert store.pending_reviews.watermark(MSGS) == {"count": 2, "digest": h.hexdigest()}

def test_excluding_a_post_moves_every_digest():
    flagged = [MSGS[0], {**MSGS[1], "excluded": "2026-10-05T12:00:00Z"}]
    assert store.responses.transcript_hash(flagged) != store.responses.transcript_hash(MSGS)
    assert store.rolling_summary.covered_digest(flagged) != store.rolling_summary.covered_digest(MSGS)
    assert store.pending_reviews.watermark(flagged)["digest"] != store.pending_reviews.watermark(MSGS)["digest"]
```

- [ ] **Step 2: Run, verify FAIL** (second test).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new file plus `tests/test_rolling_summary_store.py tests/test_pending_reviews_store.py tests/test_responses.py` — PASS.
- [ ] **Step 5: Commit** `feat(scenes): exclusion moves the rolling, review and round digests`.

### Task 4: The toggle route, and refusing a stale frozen prompt

**Files:**
- Modify: `backend/src/grimoire/routes/models.py` (`ExcludeMessage`)
- Modify: `backend/src/grimoire/routes/scenes.py` (`put_scene_message_excluded`, beside `put_scene_message`; import `ExcludeMessage`)
- Modify: `backend/src/grimoire/store/responses.py` (`require_context_included`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`regenerate_response`)
- Test: `backend/tests/test_hide_from_context.py`, `backend/tests/test_scene_freeze.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `class ExcludeMessage(BaseModel): excluded: bool`.
- Produces: `PUT /api/campaigns/{cid}/scenes/{sid}/messages/{index}/excluded` → `def put_scene_message_excluded(cid: str, sid: str, index: int, body: ExcludeMessage, request: Request)`. `_require_scene` first; then inside one `runs.scene_held_free(request.app, cid, sid)` hold: `_already_absorbed(store.scenes.read_scene(cid, sid))` → `HTTPException(409, detail={"kind": "scene_absorbed", "detail": ...})`; `store.responses.unfinished(cid, sid) is not None` → 409 `round_open`; `changed = store.scenes.set_excluded(cid, sid, index, body.excluded)`; if `changed`, `tracker_routes.after_text_edit(cid, sid, index)`. `IndexError` → 400 `"message index out of range"`; `store.scenes.NotExcludable` → 400 `{"kind": "not_excludable", ...}`; `(store.SceneNotFound, store.CampaignNotFound)` → 404. Returns `{"ok": True}` (the edit route's reply; the client reloads). The activity middleware stamps the revision.
- Produces: `responses.require_context_included(messages: list[dict], record: dict) -> None` — `index = _response_index(messages, record["id"])`; `None` → return; `since = serialize.excluded_since(messages, index)`; if `since and since >= record.get("created", "")` raise `ResponseConflict("context_excluded", "A post this reply was written from is now hidden. Replay from here to regenerate without it.")`.
- Modifies: `regenerate_response` — inside the existing campaign-lock hold, after the `historical_context_unavailable` check, call `require_context_included(store.scenes.read_scene(cid, sid)["messages"], record)`, mapping `ResponseConflict` through `_public_error`. Retry and roll continuations need no check: they run only while a round is open, which the toggle refuses, and `transcript_hash` (Task 3) moves anyway.

- [ ] **Step 1: Write the failing tests:**

```python
def test_route_excludes_and_reincludes(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: brb")
    url = f"/api/campaigns/{cid}/scenes/{sid}/messages/0/excluded"
    assert client.put(url, json={"excluded": True}).json() == {"ok": True}
    assert store.scenes.read_scene(cid, sid)["messages"][0]["excluded"]
    assert client.put(url, json={"excluded": False}).status_code == 200
    assert "excluded" not in store.scenes.read_scene(cid, sid)["messages"][0]

@pytest.mark.parametrize("speaker", [ROLL_SPEAKER, TRANSITION_SPEAKER, DIRECTOR_SPEAKER])
def test_route_refuses_a_synthetic_line(client, speaker): ...
    # r.status_code == 400 and r.json()["kind"] == "not_excludable"

def test_route_refuses_out_of_range_absorbed_and_open_round(client): ...
    # index 9 -> 400; store.scenes.mark_absorbed(cid, sid, "x", "y") -> 409 "scene_absorbed";
    # (fresh scene) a paused roll fence (the decline test's first call only) -> 409 "round_open";
    # unknown sid -> 404

def test_reroll_of_a_reply_composed_before_an_exclusion_is_refused(client, monkeypatch):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    # chat "Hello" (index 0) answered by Mara -> rid at index 1
    monkeypatch.setattr(store.scenes.write, "now_iso", lambda: "2999-01-01T00:00:00Z")
    client.put(base + "/messages/0/excluded", json={"excluded": True})
    r = client.post(base + f"/responses/{rid}/regenerate")
    assert r.status_code == 409 and r.json()["kind"] == "context_excluded"
    assert fake.calls == 1   # no second generation

def test_a_reply_composed_after_the_exclusion_rerolls(client, monkeypatch): ...
    # now_iso patched to "2000-01-01T00:00:00Z" for the exclusion of index 0, THEN chat+answer;
    # regenerate -> no "error" in the stream, a new variant saved
```

  In `test_scene_freeze.py`, add to `calls` in `test_every_shape_change_is_refused_while_a_run_holds_the_scene`:
  `("put", f"{base}/messages/0/excluded", {"excluded": True}),`

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_hide_from_context.py tests/test_scene_freeze.py tests/test_response_controls_routes.py tests/test_routes.py` — PASS.
- [ ] **Step 5: Commit** `feat(play): hide a post from context, and refuse a reroll whose prompt still holds it`.

### Task 5: Every prompt input skips excluded posts

**Files:**
- Modify: `backend/src/grimoire/store/context/story.py` (`_project_history`)
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_assemble`; add `from ..scenes import serialize as scenes_serialize`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_selector_messages`)
- Modify: `backend/src/grimoire/store/chronicle.py` (`transcript_text`)
- Modify: `backend/src/grimoire/store/export.py` (`build_markdown_bundle`, `build_text` pass `include_excluded=True`)
- Modify: `backend/src/grimoire/store/absorb/routing.py` (`speaker_index`)
- Modify: `backend/src/grimoire/routes/scenes.py` (`_absorb_start`, `post_dossiers`, `_rolling_view`, `_rolling_due`)
- Test: `backend/tests/test_hide_from_context.py`

**Interfaces:**
- Modifies: `_project_history` — `continue` on `scenes_serialize.is_excluded(message)` beside the director-note skip.
- Modifies: `_assemble` — after the `if actor_ref is not None:` block (so `pins.active(..., len(history))` and `actor.observed_history` still run on the full list), `visible = scenes_serialize.without_excluded(history)`; use `visible` for the voice-example window (`history[-4:]`), `recent_text`, `birthday_text`, `speaker.nominate(npc_names, ...)` and `length_drift.measure_contributions(...)`. `story._project_history(history)` stays on `history`.
- Modifies: `_selector_messages` — iterate `store.scenes.in_context(messages)[-12:]`, keeping the existing synthetic-speaker filter.
- Modifies: `chronicle.transcript_text(messages, player_label=None, *, include_excluded=False)` — drops `scenes_serialize.is_excluded(m)` unless `include_excluded`.
- Modifies: `speaker_index(...)` — `messages = scenes_serialize.without_excluded(messages)` after the fallback read, covering the snapshot `materialize(..., prepared.scene["messages"])` passes and the fallback alike (director-note behaviour unchanged).
- Modifies: `_absorb_start` guard → `if not store.scenes.in_context(scene["messages"])`; `post_dossiers` guard likewise.
- Modifies: `_rolling_view` adds `"fresh": len(store.scenes.in_context(messages[base:]))` (`base` as already computed); `_rolling_due` returns `False` when `view["fresh"] <= 0`, before the existing `pending` logic.

- [ ] **Step 1: Write the failing tests:**

```python
def test_composed_turn_and_inspector_omit_an_excluded_post(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "ooc: the pact is my cat")
    store.scenes.append_message(cid, sid, "user", "Mara, the tide is turning.")
    entities.create_entity(campaigns.campaign_root(cid), "lore", "The Pact",
                           "Signed at dusk.", keys="pact")
    store.scenes.set_excluded(cid, sid, 0, True)
    text = "\n".join(m["content"] for m in store.context.build_messages(cid, sid))
    assert "pact is my cat" not in text and "Signed at dusk." not in text
    assert "the tide is turning" in text
    rows = store.context.context_breakdown(cid, sid)["sections"]
    assert all("pact is my cat" not in r.get("text", "") for r in rows)

def test_selector_conversation_omits_an_excluded_post(client): ...
    # character_turns._selector_messages(cid, sid, {"eligible": [], "note": ""})[0]["content"]
    # lacks the excluded post's text, keeps the other

def test_transcript_text_filters_by_default(): ...
    # excluded line absent from transcript_text(ms); present with include_excluded=True

def test_absorb_evidence_ignores_a_quote_only_in_an_excluded_post(monkeypatch, tmp_path): ...
    # store.absorb.routing.speaker_index(cid, sid, messages, player_label="") where Mara's
    # excluded line says "I hid the key": "I hid the key" not in index["texts"].get("Mara", "")

def test_an_all_excluded_scene_is_empty_to_absorb_dossiers_and_the_fold(client): ...
    # two posts, both excluded; POST /absorb -> 400 "nothing to absorb";
    # POST /dossiers -> 400 "nothing to build dossiers from";
    # POST /rolling-summary?force=true with a FakeLLM -> fake.calls == 0
```

  (`context_breakdown` returns its rows under `sections` — `_breakdown`'s return dict.)

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_hide_from_context.py tests/test_context.py tests/test_context_speaker.py tests/test_absorb_routing.py tests/test_rolling_summary_routes.py tests/test_review_detach.py tests/test_character_turns.py tests/test_export_store.py tests/test_llm_fakes.py` and, from the repo root, `backend/.venv/bin/python scripts/verify_templates.py` — PASS (prompts for unflagged scenes are byte-identical).
- [ ] **Step 5: Commit** `feat(context): no prompt input sees an excluded post`.

### Task 6: Tracker and passage draft

**Files:**
- Modify: `backend/src/grimoire/store/tracker/walk.py` (`key_excluded`)
- Modify: `backend/src/grimoire/routes/tracker.py` (`mark`, `_context_posts`)
- Modify: `backend/src/grimoire/routes/passage_characters.py` (`draft_character`, `_observable_neighbors`)
- Test: `backend/tests/test_tracker_routes.py`, `backend/tests/test_passage_characters.py`

**Interfaces:**
- Produces: `walk.key_excluded(cid: str, sid: str, key: str) -> bool` — reads the scene; a `p-` key matches `paths.post_key(m["post_id"])`, an `r-` key matches a message whose `response_id` is `key[2:34]`; true when a matching message `serialize.is_excluded`.
- Modifies: `routes/tracker.mark` — inside its existing `try`, after the `enabled` check, `if store.tracker.walk.key_excluded(cid, sid, key): return None` (so `mark_response`, `schedule`, re-run-from-here and the post-landing marks all skip it). `_tracked`, `prune` and `flag_*` unchanged.
- Modifies: `_context_posts` — skip `store.scenes.is_excluded(m)` beside the synthetic skip.
- Modifies: `draft_character` — after `index` is found, if any message with `response_id == rid` is excluded, `HTTPException(409, detail={"kind": "excluded_source", "detail": "This response is hidden from context. Return it to context before drafting from it."})`; `_observable_neighbors` skips `serialize.is_excluded(post)`.

- [ ] **Step 1: Write the failing tests:**

```python
# test_tracker_routes.py (reuses _use, _llm, _scene, _played, _index, _keys)
def test_an_excluded_post_is_not_marked_and_its_record_survives_a_prune(client):
    _use(client, _llm())
    cid, sid = _scene(client)
    keys = _played(client, cid, sid)
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0/excluded", json={"excluded": True})
    assert tracker_routes.mark(cid, sid, keys[0]) is None
    store.tracker.walk.prune(cid, sid)
    assert keys[0] in _index(cid, sid)

def test_an_excluded_post_is_not_context_for_its_neighbour(client): ...
    # exclude index 1 (Mara's reply) via store.scenes.set_excluded;
    # tracker_routes._context_posts(cid, sid, 2) has no entry whose content is that reply

# test_passage_characters.py (reuses source)
def test_draft_refuses_an_excluded_source(client): ...
    # scenes.set_excluded(cid, sid, 0, True); POST .../character-draft -> 409, kind "excluded_source"

def test_neighbor_selection_skips_excluded_posts(): ...
    # passage_characters._observable_neighbors([a, b_excluded, c, target], 3) == [a, c]
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_tracker_routes.py tests/test_tracker_flow.py tests/test_tracker_post_ids.py tests/test_passage_characters.py` — PASS.
- [ ] **Step 5: Commit** `feat(tracker): no update is paid for an excluded post, and a draft refuses one`.

### Task 7: Replay keeps an excluded model turn verbatim

**Files:**
- Modify: `backend/src/grimoire/store/replay.py` (`_segment`, `_append_steps`, `stage`, `accept`, `state`)
- Test: `backend/tests/test_replay_store.py` (reuses its `cid`, `sid` fixtures)

**Interfaces:**
- Produces: a third step kind, `"kept"` — a generation step every one of whose messages `scenes_serialize.is_excluded` (the flag already rides `_message` via `RESPONSE_METADATA`). Not counted by `_turns_left`, `preview`'s `turns` or `begin`'s "no model turn" refusal.
- Modifies: `_append_steps` — `kind in ("generation", "kept")` goes through `scenes_write.append_reply` (keeps the turn boundary).
- Modifies: `stage` — when nothing is staged, append every leading pending step whose kind is not `"generation"` (verbatim via `append_messages` as today, kept via `append_reply`); `rec["staged"]` = total messages appended (unchanged meaning), new `rec["staged_steps"]` = steps appended.
- Modifies: `accept` — `taken = int(rec.get("staged_steps", 1 if staged else 0))`; reset `staged_steps` to 0 with `staged`.
- Modifies: `state` — when staged, `nxt` is `pending[staged_steps]["kind"]` if it exists, else `"done"`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_an_excluded_generation_is_a_kept_step(cid, sid): ...
    # player post, reply A (append_reply), player post, reply B (append_reply); exclude reply A;
    # _segment over the cut at 0 -> kinds ["verbatim", "kept", "verbatim", "generation"]

def test_kept_step_lands_with_its_turn_boundary(cid, sid): ...
    # begin at 0; stage() appends the player post AND reply A (still excluded);
    # store.scenes.get_turn_sizes / _tracked_suffix_fits holds; state()["next"] == "generation";
    # after the replayed turn lands, accept() steps past all three and the next stage() appends
    # the second player post

def test_cancel_restores_a_kept_step_through_append_reply(cid, sid): ...
    # begin, cancel(restore=True): reply A is back, excluded, and turn_sizes fits the transcript
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_replay_store.py tests/test_retcon_routes.py` — PASS.
- [ ] **Step 5: Commit** `feat(replay): an excluded model turn is kept verbatim, not regenerated`.

### Task 8: Exports mark it, imports restore it

**Files:**
- Modify: `backend/src/grimoire/store/export.py` (`_chapter`, `_html_message`, `build_html`, `build_markdown_bundle`, `build_text`, `_HTML_CSS`)
- Modify: `backend/src/grimoire/store/epub.py` (`_message_html`, `_chapter_doc`)
- Modify: `backend/src/grimoire/store/scene_import.py` (`parse`, `_expanded`)
- Modify: `backend/src/grimoire/routes/models.py` (`ImportedMessage.excluded: str = ""`)
- Test: `backend/tests/test_export_store.py`, `backend/tests/test_scene_import_store.py` (both reuse their `_campaign` helper)

**Interfaces:**
- Modifies: `_chapter` messages carry `"excluded": True` when the source post is excluded (absent otherwise).
- Produces: `export._marked(messages: list[dict]) -> list[dict]` — content of an excluded message becomes `f"{scenes_serialize.EXCLUDED_MARKER}\n{content}"`. Markdown and text call `chronicle.transcript_text(_marked(...), include_excluded=True)`.
- Modifies: `_html_message(speaker, content, excluded=False)` and `epub._message_html(speaker, content, excluded=False)` — when excluded, wrap as `<div class="excluded"><span class="not-in-context">not in context</span>…</div>`; add `.excluded{opacity:.6;border-left:1px dashed #999;padding-left:.5em}` and `.not-in-context{font-size:.75em;font-style:italic}` to `_HTML_CSS`.
- `build_json` unchanged (already verbatim; assert it).
- Modifies: `scene_import.parse` — after `scenes._parse_messages`, for each message: if `content.split("\n", 1)[0].strip() == scenes.EXCLUDED_MARKER`, strip that line and set `excluded`; a truthy `excluded` that is not a `str` becomes `now_iso()`; the marker sets `now_iso()` unless a stored stamp is present.
- Modifies: `_expanded` carries `"excluded": m["excluded"]` when truthy; `append_messages` writes it through `_message_block`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_every_export_marks_an_excluded_post(monkeypatch, tmp_path):
    # campaign with one scene: "hi" (kept), "ooc: brb" (excluded)
    md = zipfile.ZipFile(io.BytesIO(export.build_markdown_bundle(cid)[0]))
    chapter = next(n for n in md.namelist() if n != "index.md" and not n.startswith("images/"))
    assert "*(not in context)*\nooc: brb" in md.read(chapter).decode()
    assert "*(not in context)*\nooc: brb" in export.build_text(cid)[0].decode()
    html = export.build_html(cid)[0].decode()
    assert 'class="excluded"' in html and "not in context" in html
    assert json.loads(export.build_json(cid)[0])["scenes"][0]["messages"][1]["excluded"]  # read_scene docs, verbatim
    # epub: the chapter xhtml in build_epub's zip carries class="excluded"

def test_markdown_export_reimports_excluded(monkeypatch, tmp_path): ...
    # parse(cid, chapter_bytes): the "ooc: brb" message has content "ooc: brb" and a str excluded;
    # commit(...) into a new scene -> read_scene(...)["messages"][1]["excluded"] truthy, content "ooc: brb"

def test_a_stored_scene_file_import_keeps_its_stamp(monkeypatch, tmp_path): ...
    # STORED-style text whose block carries <!-- grimoire-response {"excluded": "2026-10-05T12:00:00Z"} -->
    # parse -> excluded == "2026-10-05T12:00:00Z"
```


- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_export_store.py tests/test_scene_import_store.py tests/test_scene_import_routes.py` and `tests/test_frozen_campaign.py` — PASS with no snapshot change (no flagged post in the fixture).
- [ ] **Step 5: Commit** `feat(export): mark posts hidden from context, and restore the flag on import`.

### Task 9: The play view toggle

**Files:**
- Modify: `frontend/src/api/types.ts` (`Message.excluded?: string`)
- Modify: `frontend/src/api/client.ts` (`setExcluded`)
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`setExcluded: vi.fn()` beside `editMessage`)
- Modify: `frontend/src/testkit/campaignHarness.tsx` (`installCampaignMocks`: `(api.setExcluded as any).mockResolvedValue({ ok: true })`)
- Modify: `frontend/src/components/play/TranscriptPost.tsx` (`TranscriptActions.toggleExcluded`, `TranscriptContext.absorbed`, the gutter button, the row class and tag)
- Modify: `frontend/src/routes/CampaignView.tsx` (`toggleExcluded`, `transcriptActions`, `transcriptCtx`)
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx` (reuses `twoPostScene`, `DONE_SCENE`, `renderCampaign`)

**Interfaces:**
- Produces: `Message.excluded?: string` — the ISO stamp; truthy means excluded.
- Produces: `api.setExcluded(cid: string, sid: string, index: number, excluded: boolean) => request<{ ok: boolean }>("PUT", \`/api/campaigns/${cid}/scenes/${sid}/messages/${index}/excluded\`, { excluded })`.
- Produces: `TranscriptActions.toggleExcluded: (index: number, excluded: boolean) => void`; `TranscriptContext.absorbed: boolean` (from `activeDone`, added to the memo's deps).
- Gutter button, after ✎: rendered when `active && !ctx.absorbed` (pass `absorbed` down as a prop like `active`) and `m.speaker` is not `ROLL_SPEAKER`, `TRANSITION_SPEAKER` or `DIRECTOR_SPEAKER`: `className="msg-edit msg-exclude"`, text `⊘`, `title={m.excluded ? "Return to context" : "Hide from context"}`, `aria-label={\`${m.excluded ? "Return" : "Hide"} message ${index + 1} ${m.excluded ? "to" : "from"} context\`}`, `aria-pressed={!!m.excluded}`, `disabled={rolling}`, `onClick={() => actions.toggleExcluded(index, !m.excluded)}`.
- Row: `.msg` gets class `excluded` when `m.excluded`; `.msg-body` opens with `<span className="not-in-context">not in context</span>` (outside the edit form).
- `CampaignView.toggleExcluded(index, excluded)`: guarded like `saveEdit` (`!activeId || rolling || !transcriptIsActive` → return); `takeRollLatch(activeId)` around `api.setExcluded`, `fail(err, false)` on error as `deleteMessagesFrom` does; then `const seen = await selectScene(activeId); askAfterPost(activeId, seen);`.
- CSS: `.msg.excluded .msg-body { opacity: 0.55; }`, `.msg.excluded { border-left: 1px dashed var(--muted); }`, `.not-in-context { font-size: 0.75em; font-style: italic; color: var(--muted); margin-right: 0.5em; }`, `.msg-exclude[aria-pressed="true"] { color: var(--accent); }` (use the tokens `index.css` already defines).

- [ ] **Step 1: Write the failing tests:**

```tsx
test("the hide toggle is on player and model posts only", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "assistant", speaker: ROLL_SPEAKER, content: "🎲 1d20 = 12" },
    { role: "assistant", speaker: TRANSITION_SPEAKER, content: "*Time passes.*" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.getAllByTitle("Hide from context")).toHaveLength(2);
});

test("hiding a post calls setExcluded and reloads the scene", async () => {
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  const loads = (api.getScene as any).mock.calls.length;
  fireEvent.click(screen.getAllByTitle("Hide from context")[0]);
  await waitFor(() => expect(api.setExcluded).toHaveBeenCalledWith("run", "s1", 0, true));
  await waitFor(() => expect((api.getScene as any).mock.calls.length).toBeGreaterThan(loads));
});

test("an excluded post is marked and its toggle is pressed", async () => { ... });
  // getScene with { role: "user", content: "hi", excluded: "2026-10-05T12:00:00Z" }:
  // the button titled "Return to context" has aria-pressed="true"; its .msg has class "excluded";
  // "not in context" is rendered; clicking calls setExcluded("run", "s1", 0, false)

test("an absorbed scene offers no hide toggle", async () => { ... });
  // listScenes -> DONE_SCENE: queryAllByTitle("Hide from context") is empty
```

  (Import `ROLL_SPEAKER` / `TRANSITION_SPEAKER` from `../components/play/TranscriptPost`; adjust the `getScene` call-count assertion if the file reads the window through another mocked function — whatever `selectScene` calls.)

- [ ] **Step 2: Run, verify FAIL** — `cd frontend && npx vitest run src/routes/CampaignView.test.tsx`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/routes/CampaignView.test.tsx src/routes/CampaignView.shortcuts.test.tsx src/routes/CampaignView.swipes.test.tsx src/components/review/SceneReview.test.tsx` and `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): a gutter toggle that hides a post from context`.

### Task 10: Gate

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python` — fix whatever fails (a moved ratchet count: `make baseline`, commit the baseline with its cause), and nothing else.

## Resolved ambiguities

- The route returns `{"ok": True}` — the spec's "returns the scene (as the edit route does)" contradicts the edit route, which returns `{"ok": True}`; the client reloads either way.
- `Message.excluded` is typed `string` (the stored ISO stamp the API actually returns), not the spec's `boolean`; `ImportedMessage.excluded` is `str` for the same reason.
- Same-second ties in the frozen-prompt rule refuse (`>=`): compose and `prepare` share one lock hold, so a tie cannot prove the snapshot came after the exclusion; refusing points at replay, the safe direction.
- "Importing the JSON export" has no parser to mean; it is read as the import draft/commit JSON (`ImportedMessage`) plus a stored scene file, whose metadata comment carries the flag.
- `_rolling_due`'s threshold stays on the raw post count; only an all-excluded (or note-only) pending span is treated as empty.
- Replay gets a third step kind, `"kept"`, rather than reclassifying excluded turns as `"verbatim"`, because verbatim steps append without a turn boundary.

## Plan-gate rulings (binding; override the tasks above where they differ)

Plan → implementation gate: independent adversarial review (stand-in for
`/codex:adversarial-review`; owner-approved).

1. **Replay (Task 7).** Segment exactly as today, then relabel a generation
   step as `kept` only when **every** message in it is excluded. The
   segmentation test fixture gets a third exchange (player post, reply C) so
   "the next stage() appends the second player post" is reachable; assertions
   follow the plan's stage semantics (stage appends every leading non-generation
   step). `state()` reports a pending `kept` step as `next: "verbatim"` (the
   frontend type is unchanged).
2. **Tracker running state.** `walk._latest_ok` (what `current()` and the
   absorb snapshot read) skips records whose owning message is excluded, so a
   hidden post's snapshot never becomes the tracker section of a prompt;
   `_tracked` stays unchanged so prune keeps the records. Tests: `current()`
   skips the hidden post's snapshot; re-run-from-here schedules nothing for the
   hidden key; `mark(...)` is None.
3. **Timestamp.** Stamp with `store/scenes/write.py`'s module-level `now_iso`
   (imported from `..paths`), which is what the tests patch. A same-second
   hide-then-send makes that reply un-rerollable (ties refuse) — accepted.
4. **Director notes keep their behaviour.** The selector uses
   `without_excluded(messages)[-12:]` and keeps its existing synthetic-speaker
   filter; `_rolling_due`'s `fresh` count and the absorb/dossier empty guards use
   `without_excluded` too (a notes-only scene is not newly "empty").
5. **Frontend absorbed-scene test** awaits `findByText(/scene complete/i)` before
   asserting the toggle is absent.
6. **EPUB**: the class and tag are enough; no epub stylesheet change.
7. **`round_open`** counts `pending` rounds too (what `responses.unfinished`
   returns) — stated.
8. **Gates**: after `make check`, the orchestrator runs the code review and the
   final adversarial review against the spec; the implementer does not.
9. Global Constraints' reason for inline flag checks in `pending_reviews` is
   wrong (it already imports `scenes.paths`); the inline check stays, reason
   corrected in the code comment to the real one (no new import needed).
