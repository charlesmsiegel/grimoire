"""Scene branching (play controls III) — branch groups, the derived closed
state, and `store/branch.py`.

A scene is *closed* when another member of its branch group is absorbed. That
is derived while listing rather than stored, so deleting or un-absorbing the
absorbed member reopens the rest with no bookkeeping — which is what most of
the store tests below pin.
"""

import json
import uuid

import pytest

from grimoire import routes, store
from grimoire.store import branch, campaigns, checks, dice, entities, scene_ids, scenes, worlds
from tests.llm_fakes import FakeLLM


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _played(cid, title, posts=2):
    """A scene with a transcript, so a cut has something to take."""
    sid = scenes.create_scene(cid, title)
    for i in range(posts):
        scenes.append_message(cid, sid, "user", f"{title} post {i}")
    return sid


def _group(cid, *sids):
    g = scenes.ensure_identity(cid, sids[0])
    for s in sids:
        scenes.write.set_branch_keys(cid, s, g, of=None if s == sids[0] else g)
    return g


# --- branch groups and the derived closed state ------------------------------


def test_a_member_is_closed_while_another_member_is_absorbed(cid):
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    _group(cid, a, b)
    scenes.mark_absorbed(cid, a, "x", "y")
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert rows[b]["closed_by"] == {"sid": a, "title": "Mara"}
    assert "closed_by" not in rows[a]
    assert scenes.read.closed_by(cid, b) == {"sid": a, "title": "Mara"}
    assert scenes.read.closed_by(cid, a) is None


def test_unabsorbing_or_deleting_the_absorbed_member_reopens_the_rest(cid):
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    _group(cid, a, b)
    scenes.mark_absorbed(cid, a, "x", "y")
    scenes.write.unmark_absorbed(cid, a)
    assert scenes.read.closed_by(cid, b) is None
    scenes.mark_absorbed(cid, a, "x", "y")
    assert scenes.read.closed_by(cid, b) is not None
    scenes.delete_scene(cid, a)
    assert scenes.read.closed_by(cid, b) is None


def test_the_source_needs_no_key_to_be_a_member(cid):
    """A group is `branch_group or identity`: the scene a branch was taken
    from carries no new key, and is still closed by its absorbed sibling."""
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    g = scenes.ensure_identity(cid, a)
    scenes.write.set_branch_keys(cid, b, g, of=g)
    assert "branch_group" not in scenes.read_scene_meta(cid, a)
    scenes.mark_absorbed(cid, b, "x", "y")
    assert scenes.read.closed_by(cid, a) == {"sid": b, "title": "Winifred"}
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert rows[a]["branch_group"] == rows[b]["branch_group"] == g


def test_a_scene_in_no_group_carries_no_branch_keys(cid):
    sid = _played(cid, "Mara")
    row = next(r for r in scenes.list_scenes(cid) if r["id"] == sid)
    assert not {"branch_group", "branch_of", "closed_by", "_identity"} & set(row)
    assert scenes.read.closed_by(cid, sid) is None


def test_closed_by_is_none_for_a_missing_scene_or_campaign(cid):
    assert scenes.read.closed_by(cid, "999--nobody") is None
    assert scenes.read.closed_by("no-such-campaign", "001--mara") is None


def test_setting_branch_keys_does_not_touch_updated(cid):
    sid = _played(cid, "Mara")
    before = scenes.read_scene_meta(cid, sid)["updated"]
    scenes.write.set_branch_keys(cid, sid, "a" * 32, of="b" * 32)
    meta = scenes.read_scene_meta(cid, sid)
    assert meta["updated"] == before
    assert (meta["branch_group"], meta["branch_of"]) == ("a" * 32, "b" * 32)


def test_setting_branch_keys_on_a_missing_scene_raises(cid):
    with pytest.raises(scenes.SceneNotFound):
        scenes.write.set_branch_keys(cid, "999--nobody", "a" * 32)


# --- the primitive: store/branch.py -----------------------------------------


def seed(client, module=None):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def _turn(client, cid, sid, content):
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": content, "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text


def _two_turns(client):
    cid, sid = seed(client)
    _turn(client, cid, sid, "Hello")
    _turn(client, cid, sid, "Onward")
    assert [m["role"] for m in store.scenes.read_scene(cid, sid)["messages"]] == \
        ["user", "assistant", "user", "assistant"]
    return cid, sid


def _shape(messages):
    return [(m["role"], m.get("speaker"), m["content"]) for m in messages]


def test_branch_keeps_exactly_the_posts_through_the_branch_point(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 1)
    src, out = (store.scenes.read_scene(cid, s)["messages"] for s in (sid, new))
    assert _shape(out) == _shape(src[:2])
    assert sum(store.scenes.get_turn_sizes(cid, new)) <= 1
    assert len(store.scenes.read_scene(cid, sid)["messages"]) == 4   # source untouched


def test_the_source_file_is_never_written(client):
    cid, sid = _two_turns(client)
    path = store.scenes._scene_path(cid, sid)
    before = path.read_bytes()
    store.branch.branch_scene(cid, sid, 1)
    assert path.read_bytes() == before


def test_branch_rewinds_location_history(cid):
    root = campaigns.campaign_root(cid)
    entities.create_entity(root, "locations", "The Wharf", body="")
    entities.create_entity(root, "locations", "The Chapel", body="")
    sid = scenes.create_scene(cid, "Moving")
    scenes.set_location(cid, sid, "the-wharf")          # first is silent
    scenes.append_message(cid, sid, "user", "we go inland")
    scenes.set_location(cid, sid, "the-chapel")         # appends a transition line
    scenes.append_message(cid, sid, "user", "at the chapel")
    new = branch.branch_scene(cid, sid, 0)
    assert scenes.get_location_history(cid, new) == ["the-wharf"]
    assert scenes.get_location_history(cid, sid) == ["the-wharf", "the-chapel"]


def test_the_sibling_sorts_directly_after_its_source(cid):
    first, second, third = (_played(cid, t) for t in ("Mara", "Winifred", "Seraphine"))
    new = branch.branch_scene(cid, first, 0)
    assert sorted(s["id"] for s in scenes.list_scenes(cid)) == [first, new, second, third]
    assert scene_ids.parse_sid(new)["number"] == scene_ids.parse_sid(first)["number"]
    assert scenes.read_scene_meta(cid, new)["title"] == "Mara (branch)"


def test_a_second_branch_is_titled_with_a_number(cid):
    sid = _played(cid, "Mara")
    branch.branch_scene(cid, sid, 0)
    new = branch.branch_scene(cid, sid, 1)
    assert scenes.read_scene_meta(cid, new)["title"] == "Mara (branch) 2"


def test_a_caller_title_is_used(cid):
    sid = _played(cid, "Mara")
    new = branch.branch_scene(cid, sid, 0, title="  Winifred  ")
    assert scenes.read_scene_meta(cid, new)["title"] == "Winifred"


def test_siblings_share_a_group_and_name_their_source(cid):
    sid = _played(cid, "Mara")
    ident = scenes.scene_identity(cid, sid)
    one = branch.branch_scene(cid, sid, 0)
    assert scenes.scene_identity(cid, one) != ident
    meta = scenes.read_scene_meta(cid, one)
    assert meta["branch_of"] == ident and meta["branch_group"] == ident
    assert "branch_group" not in scenes.read_scene_meta(cid, sid)   # source never written
    two = branch.branch_scene(cid, sid, 1)
    deep = branch.branch_scene(cid, one, 0)
    assert scenes.read_scene_meta(cid, two)["branch_group"] == ident
    assert scenes.read_scene_meta(cid, deep)["branch_group"] == ident
    assert scenes.read_scene_meta(cid, deep)["branch_of"] == scenes.scene_identity(cid, one)
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert {rows[s]["branch_group"] for s in (sid, one, two, deep)} == {ident}


def test_the_sibling_never_copies_absorb_or_summary_state(cid):
    sid = _played(cid, "Mara")
    scenes.set_rolling_summary(cid, sid, "So far.", 1, "d", "f")
    scenes.stamp_greeting(cid, sid, "g1")
    scenes.set_response(cid, sid, {"length_reply_words": "120"})
    scenes.add_dismissed(cid, sid, "characters/winifred")
    new = branch.branch_scene(cid, sid, 1)
    meta = scenes.read_scene_meta(cid, new)
    assert not {"done", "one_line", "summary", "greeting", "rolling_summary"} & set(meta)
    assert meta["length_reply_words"] == "120"
    assert meta["dismissed"] == "characters/winifred"


def test_the_sibling_keeps_the_scenes_group_play_settings(cid):
    """A scene's speaker order is the player's choice for that scene, like its
    reply settings: a branch of a List-order scene still plays in List order."""
    sid = _played(cid, "Mara")
    settings = store.group_play.validate(
        {"order": "list", "order_list": ["characters:mara"], "auto_rounds": 2})
    scenes.set_group(cid, sid, store.group_play.dump(settings))
    new = branch.branch_scene(cid, sid, 0)
    meta = scenes.read_scene_meta(cid, new)
    assert store.group_play.settings_of(meta) == settings


def test_a_post_hidden_from_context_stays_hidden_in_the_sibling(cid):
    sid = _played(cid, "Mara", posts=3)
    assert scenes.set_excluded(cid, sid, 1, True)
    new = branch.branch_scene(cid, sid, 2)
    flags = [scenes.serialize.is_excluded(m) for m in scenes.read_scene(cid, new)["messages"]]
    assert flags == [False, True, False]


def test_through_the_last_post_does_not_cut(cid, monkeypatch):
    sid = _played(cid, "Mara", posts=3)

    def refuse(*a, **k):
        raise AssertionError("no cut expected")

    monkeypatch.setattr(scenes.write, "delete_from", refuse)
    new = branch.branch_scene(cid, sid, 2)
    assert len(scenes.read_scene(cid, new)["messages"]) == 3


def test_a_point_inside_a_multi_part_response_snaps_to_its_last_part(cid):
    sid = _played(cid, "Mara", posts=1)
    rid = uuid.uuid4().hex
    scenes.append_reply(cid, sid, [
        {"role": "assistant", "speaker": "Mara", "content": "One.", "response_id": rid,
         "response_part": "", "response_status": "complete"},
        {"role": "assistant", "speaker": "Mara", "content": "Two.", "response_id": rid,
         "response_part": "1", "response_status": "complete"}])
    scenes.append_message(cid, sid, "user", "After.")
    new = branch.branch_scene(cid, sid, 1)
    assert [m["content"] for m in scenes.read_scene(cid, new)["messages"]] == \
        ["Mara post 0", "One.", "Two."]


def test_a_point_before_a_roll_split_snaps_past_the_roll_to_the_continuation(cid):
    """An accepted roll splits a reply: its first part, the roll line, then the
    continuation under the same response id. Branching at the first part keeps
    the whole reply -- and so the roll line inside it, with its log entry
    (codex review, PR #458)."""
    sid = _played(cid, "Mara", posts=1)
    rid = uuid.uuid4().hex
    scenes.append_reply(cid, sid, [
        {"role": "assistant", "speaker": "Mara", "content": "Mara swings.", "response_id": rid,
         "response_part": "", "response_status": "complete"}])
    r = dice.roll("1d20", 7)
    entry = store.rolls.append(cid, sid, None, r)
    scenes.append_message(cid, sid, "assistant", dice.format_roll(r),
                          speaker=scenes.serialize.ROLL_SPEAKER)
    scenes.append_reply(cid, sid, [
        {"role": "assistant", "speaker": "Mara", "content": "It lands.", "response_id": rid,
         "response_part": "p1", "response_status": "complete"}])
    scenes.append_message(cid, sid, "user", "After.")
    assert branch.snap(scenes.read_scene(cid, sid)["messages"], 1) == 3
    new = branch.branch_scene(cid, sid, 1)
    kept = scenes.read_scene(cid, new)["messages"]
    assert [m["content"] for m in kept] == \
        ["Mara post 0", "Mara swings.", dice.format_roll(r), "It lands."]
    assert kept[1]["response_id"] == kept[3]["response_id"] != rid
    copied = [e for e in store.rolls.read(cid) if e.get("scene") == new]
    assert [e["result"] for e in copied] == [entry["result"]]


def test_deleting_the_source_leaves_the_siblings_responses(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 3)
    rid = store.scenes.read_scene(cid, new)["messages"][-1]["response_id"]
    assert rid not in {m.get("response_id") for m in store.scenes.read_scene(cid, sid)["messages"]}
    store.scenes.delete_scene(cid, sid)
    rec = store.responses.get(cid, new, rid, private=True)
    assert rec["snapshot"]                         # snapshot file survived


def test_rerolling_a_response_in_the_sibling_works(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 1)
    rid = store.scenes.read_scene(cid, new)["messages"][-1]["response_id"]
    before = len(store.responses.get(cid, new, rid)["variants"])
    fake = FakeLLM([['Mara answers again.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{new}/responses/{rid}/regenerate", json={})
    assert r.status_code == 200, r.text
    assert "error" not in r.text, r.text
    assert len(store.responses.get(cid, new, rid)["variants"]) == before + 1
    assert store.scenes.read_scene(cid, new)["messages"][-1]["content"] == "Mara answers again."
    assert store.scenes.read_scene(cid, sid)["messages"][1]["content"] == "Mara answers."


def test_the_sibling_has_cast_and_presence(client):
    cid, sid = _two_turns(client)
    new = store.branch.branch_scene(cid, sid, 1)
    data = store.appearances.paths.record(cid)
    seated = {ref for ref, rec in data.items() if new in rec.get("scenes", [])}
    assert seated == {ref for ref, rec in data.items() if sid in rec.get("scenes", [])}
    for ref in seated:
        if sid in data[ref].get("presence", {}):
            assert new in data[ref]["presence"]


def test_kept_roll_lines_carry_their_entries(client):
    cid, sid = seed(client)
    _turn(client, cid, sid, "Hello")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "1d20"})
    assert r.status_code == 200, r.text
    _turn(client, cid, sid, "Onward")
    rid = store.scenes.read_scene(cid, sid)["messages"][1]["response_id"]
    past = store.branch.branch_scene(cid, sid, 3)
    assert store.audit.prompt.roll_lines(cid, past) == store.audit.prompt.roll_lines(cid, sid)
    assert len(store.audit.prompt.roll_lines(cid, past)) == 1
    clone = store.scenes.read_scene(cid, past)["messages"][1]["response_id"]
    assert store.responses.get(cid, past, clone)["mechanically_locked"] is True
    before = store.branch.branch_scene(cid, sid, 1)
    assert store.audit.prompt.roll_lines(cid, before) == []
    clone = store.scenes.read_scene(cid, before)["messages"][1]["response_id"]
    assert "mechanically_locked" not in store.responses.get(cid, before, clone)
    assert store.responses.get(cid, sid, rid)["mechanically_locked"] is True


def test_identical_roll_lines_consume_entries_in_order(cid):
    r = dice.roll("1d20", 3)
    line = dice.format_roll(r)
    entries = [{"id": "r1", "scene": "s", "label": None, "result": r},
               {"id": "r2", "scene": "s", "label": None, "result": r}]
    assert branch.match_rolls(entries, [line, line]) == ["r1", "r2"]
    assert branch.match_rolls(entries, [line]) == ["r1"]


def test_a_check_line_matches_by_label_and_result(cid):
    r = dice.roll("1d20+2", 5)
    resolution = {"actor_label": "Mara", "check_label": "Brawl", "result": r,
                  "difficulty": 12, "tier": "success"}
    entry = {"id": "r1", "scene": "s", "label": checks.roll_label(resolution),
             "result": r, "tier": "success"}
    assert branch.match_rolls([entry], [checks.format_check_roll(resolution)]) == ["r1"]


def test_an_unmatched_line_copies_nothing(cid):
    r = dice.roll("1d20", 3)
    entries = [{"id": "r1", "scene": "s", "label": None, "result": r},
               {"id": "r2", "scene": "s", "label": None, "result": {"bad": True}}]
    assert branch.match_rolls(entries, ["🎲 `2d6` → [1, 1] = **2**"]) == []


def test_branching_an_absorbed_scene_is_refused(cid):
    sid = _played(cid, "Mara")
    scenes.mark_absorbed(cid, sid, "x", "y")
    with pytest.raises(branch.BranchRefused) as exc:
        branch.branch_scene(cid, sid, 0)
    assert exc.value.kind == "absorbed_use_fork"


def test_branching_a_closed_scene_is_refused(cid):
    sid = _played(cid, "Mara")
    sibling = branch.branch_scene(cid, sid, 0)
    scenes.mark_absorbed(cid, sibling, "x", "y")
    with pytest.raises(branch.BranchRefused) as exc:
        branch.branch_scene(cid, sid, 0)
    assert exc.value.kind == "branch_closed"


def test_an_out_of_range_point_is_an_index_error(cid):
    sid = _played(cid, "Mara")
    before = {s["id"] for s in scenes.list_scenes(cid)}
    for through in (-1, 2):
        with pytest.raises(IndexError):
            branch.branch_scene(cid, sid, through)
    assert {s["id"] for s in scenes.list_scenes(cid)} == before


def test_a_failure_part_way_leaves_no_sibling(client, monkeypatch):
    cid, sid = _two_turns(client)
    before = {s["id"] for s in store.scenes.list_scenes(cid)}
    baselines_before = store.audit.baselines.read_baselines(cid)
    revision = store.revision.current(cid)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(store.audit.baselines, "copy_baseline", boom)
    with pytest.raises(RuntimeError):
        store.branch.branch_scene(cid, sid, 1)
    assert {s["id"] for s in store.scenes.list_scenes(cid)} == before
    data = json.loads((store.campaigns.campaign_root(cid) / "responses.json").read_text())
    assert set(data["scenes"]) == {store.scenes.scene_identity(cid, sid)}
    assert store.audit.baselines.read_baselines(cid) == baselines_before
    for rec in store.appearances.paths.record(cid).values():
        assert set(rec.get("scenes", [])) <= before
        assert set(rec.get("presence", {})) <= before
    assert store.revision.current(cid) != revision


def test_branch_copies_scene_note(client):
    """Branching spec resolution 16: the sibling keeps its source's author's
    note, under its own identity, and keeps it when the source is deleted."""
    cid, sid = _two_turns(client)
    note = {"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1}
    src = store.scenes.ensure_identity(cid, sid)
    store.authors_notes.set_scene(cid, src, note)
    new = store.branch.branch_scene(cid, sid, 1)
    notes = store.authors_notes.read(cid)["scenes"]
    assert notes[store.scenes.scene_identity(cid, new)] == note
    assert notes[src] == note
    store.scenes.delete_scene(cid, sid)
    assert store.authors_notes.read(cid)["scenes"] == {
        store.scenes.scene_identity(cid, new): note}


def test_a_failed_branch_leaves_no_copied_note(client, monkeypatch):
    cid, sid = _two_turns(client)
    src = store.scenes.ensure_identity(cid, sid)
    store.authors_notes.set_scene(cid, src, {"text": "Storm.", "depth": 4, "every": 1})

    def boom(*_a, **_k):
        raise RuntimeError("rolls")
    monkeypatch.setattr(store.rolls, "copy_for_branch", boom)
    with pytest.raises(RuntimeError):
        store.branch.branch_scene(cid, sid, 1)
    assert set(store.authors_notes.read(cid)["scenes"]) == {src}
