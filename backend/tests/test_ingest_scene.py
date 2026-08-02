import asyncio
import json as json_module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_scene
from grimoire.store import campaigns, worlds


def _world(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("Ashgrove")


def test_ensure_campaign_creates_once(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid1 = ingest_scene.ensure_campaign("Silver Oath", wid)
    cid2 = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert cid1 == cid2
    assert campaigns.read_campaign(cid1)["meta"]["world"] == wid


def test_manifest_round_trips(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert ingest_scene.load_manifest(cid) == {}
    ingest_scene.save_manifest(cid, {"file1-scene01": {"status": "done", "sid": "001--x"}})
    assert ingest_scene.load_manifest(cid) == {"file1-scene01": {"status": "done", "sid": "001--x"}}


def test_ensure_character_creates_once(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    aid1 = ingest_scene.ensure_character(cid, {"name": "Cassian", "personality": "wary, precise"})
    aid2 = ingest_scene.ensure_character(cid, {"name": "Cassian"})
    assert aid1 == aid2 == "cassian"
    vid = ingest_scene.resolve_version(cid, "characters", aid1)
    from grimoire.store import characters
    assert characters.read_card(croot, aid1, vid)["data"]["personality"] == "wary, precise"


def test_ensure_character_returns_world_character_without_shadow_copy(monkeypatch, tmp_path):
    """A thin campaign's world may already hold a character of that name (by
    slug); ensure_character must return the world character's id and must
    NOT create a blank-card campaign-side shadow of it."""
    from grimoire.store import campaigns as campaigns_store, characters, overlay, worlds as worlds_store
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds_store.world_root(wid)
    card = characters.blank_card("Cassian")
    card["data"]["personality"] = "wary, precise"
    characters.create_character(wroot, "Cassian", "main", card)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)

    aid = ingest_scene.ensure_character(cid, {"name": "Cassian"})

    assert aid == "cassian"
    assert not (croot / "characters" / "cassian").exists()  # no campaign-side shadow
    vid = ingest_scene.resolve_version(cid, "characters", aid)
    assert characters.read_card(overlay.char_root(cid, aid), aid, vid)["data"]["personality"] == "wary, precise"


def test_ensure_location_creates_once(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    eid1 = ingest_scene.ensure_location(cid, {"name": "Thornfield Manor", "notes": "Seat of Corvin."})
    eid2 = ingest_scene.ensure_location(cid, {"name": "Thornfield Manor"})
    assert eid1 == eid2 == "thornfield-manor"


def test_resolve_version_for_pc(monkeypatch, tmp_path):
    from grimoire.store import pcs, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    wroot = worlds_store.world_root(wid)
    pcs.create_pc(wroot, "Julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    vid = ingest_scene.resolve_version(cid, "pcs", "julian")
    assert vid == "default"


def test_build_scene_writes_transcript_cast_location_date(monkeypatch, tmp_path):
    from grimoire.store import appearances, scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    wroot = worlds_store.world_root(wid)
    from grimoire.store import pcs
    pcs.create_pc(wroot, "Julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)

    scene = {
        "title": "The Reckoning",
        "date": "1818-05-15",
        "new_locations": [{"name": "Winterbourne Manor", "notes": "Family seat."}],
        "location": "winterbourne-manor",
        "new_characters": [{"name": "Marisol", "personality": "cruel, controlled"}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "marisol"}],
        "turns": [
            {"role": "assistant", "speaker": None, "content": "*The study is silent.*"},
            {"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""},
            {"role": "user", "speaker": "Julian", "content": "\"I have.\""},
        ],
    }
    sid = ingest_scene.build_scene(cid, scene)

    read = scenes.read_scene(cid, sid)
    assert [m["content"] for m in read["messages"]] == [
        "*The study is silent.*", "\"You've grown bold.\"", "\"I have.\""]
    assert read["messages"][1]["speaker"] == "Marisol"
    assert read["messages"][2]["role"] == "user"
    assert "1818-05-15" in sid  # first date-set stamps the filename
    cast = {(a["kind"], a["id"]) for a in appearances.scene_cast(cid, sid)}
    assert cast == {("pcs", "julian"), ("characters", "marisol")}


def test_build_scene_does_not_narrate_a_first_time_cast_member(monkeypatch, tmp_path):
    """build_scene's appear() call must pass narrate=False -- otherwise a
    first-time character (like Marisol, cast for the first time in this very
    scene) gets a synthetic "joins the scene" line injected after the real
    transcript, corrupting the ingested historical dialogue."""
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    scene = {
        "title": "The Reckoning",
        "new_characters": [{"name": "Marisol", "personality": "cruel, controlled"}],
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    }
    sid = ingest_scene.build_scene(cid, scene)
    from grimoire.store import scenes
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == \
        ["\"You've grown bold.\""]


class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def complete(self, messages, conn):
        self.calls.append((messages, conn))
        return self.text


def test_run_absorb_and_apply_scene(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, playstate, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene = {
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    }
    sid = ingest_scene.build_scene(cid, scene)

    fake_text = json_module.dumps({
        "one_line": "Marisol needles Julian.",
        "summary": "A tense study confrontation.",
        "keywords": ["study", "confrontation"],
        "timeline_events": [{"date": "1818-05-15", "text": "Julian confronts Marisol."}],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of Julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    result = asyncio.run(ingest_scene.run_absorb(cid, sid, client, conn))
    assert result["parsed"]["one_line"] == "Marisol needles Julian."
    assert any(e["kind"] == "character_state" for e in result["edits"])

    applied, failures = ingest_scene.apply_scene(cid, sid, result["parsed"], result["edits"])
    assert applied and failures == []
    st = playstate.read_state(croot, "marisol")
    assert "wary of Julian" in st["current_state"]
    assert client.calls[0][1]["model"] == "test/model" and client.calls[0][1]["api_key"] == "k"


def test_ingest_one_scene_is_resumable(monkeypatch, tmp_path):
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene = {
        "key": "file1-scene01",
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    }
    fake_text = json_module.dumps({
        "one_line": "Marisol needles Julian.", "summary": "s", "keywords": [],
        "timeline_events": [], "character_state_edits": [], "lore_edits": [],
        "authored_edits": [], "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    first = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, conn))
    assert first["status"] == "done"
    assert len(client.calls) == 1

    second = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, conn))
    assert second["status"] == "skipped"
    assert second["sid"] == first["sid"]
    assert len(client.calls) == 1  # no second LLM call


def test_ingest_one_scene_resumes_after_build_then_crash(monkeypatch, tmp_path):
    """If build_scene succeeded but absorb/apply never ran (process died in between),
    a retry must reuse the recorded sid instead of minting a duplicate scene."""
    from grimoire.store import scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene = {
        "key": "file1-scene01",
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    }

    # Simulate the crash: build_scene ran (creating the real scene on disk) but the
    # manifest was written as "in_progress" and the process died before absorb/apply.
    sid = ingest_scene.build_scene(cid, scene)
    manifest = ingest_scene.load_manifest(cid)
    manifest[scene["key"]] = {"status": "in_progress", "sid": sid}
    ingest_scene.save_manifest(cid, manifest)

    fake_text = json_module.dumps({
        "one_line": "Marisol needles Julian.", "summary": "s", "keywords": [],
        "timeline_events": [], "character_state_edits": [], "lore_edits": [],
        "authored_edits": [], "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    result = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, conn))
    assert result["status"] == "done"
    assert result["sid"] == sid
    assert len(scenes.list_scenes(cid)) == 1


def test_two_scenes_accumulate_state_in_order(monkeypatch, tmp_path):
    """Scene 2's snapshot must see scene 1's applied character-state edit."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("Ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    scene1 = {
        "key": "file1-scene01", "title": "Scene One",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    }
    text1 = json_module.dumps({
        "one_line": "a", "summary": "a", "keywords": [], "timeline_events": [],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of Julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene1, FakeClient(text1), conn))

    captured = {}
    real_snapshot = ingest_scene.absorb.state_snapshot

    def spying_snapshot(cid_, sid_):
        snap = real_snapshot(cid_, sid_)
        captured.update(snap)
        return snap

    monkeypatch.setattr(ingest_scene.absorb, "state_snapshot", spying_snapshot)

    scene2 = {
        "key": "file1-scene02", "title": "Scene Two",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"Still bold, I see.\""}],
    }
    text2 = json_module.dumps({
        "one_line": "b", "summary": "b", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene2, FakeClient(text2), conn))

    assert any("wary of Julian" in v for v in captured.values())


def test_run_absorb_primes_the_prompt_with_open_commitments(monkeypatch, tmp_path):
    """A later imported scene has to be able to resolve a commitment an earlier
    import opened. Without its id in the prompt the model can only file a
    duplicate."""
    from grimoire.store import commitments, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    sid = ingest_scene.build_scene(cid, {
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
    })
    commitments.set_movement(cid, "the-oath", "Marisol's oath", "promise", "open",
                             "by midwinter", "She swore it on the stair.", "earlier-scene")

    client = FakeClient("{}")
    asyncio.run(ingest_scene.run_absorb(cid, sid, client, {"kind": "openrouter"}))
    user_message = client.calls[0][0][1]["content"]
    assert "Open commitments:" in user_message
    assert "the-oath: Marisol's oath (promise, open), due by midwinter" in user_message


_PARTIAL_SCENE = {
    "key": "file1-scene01",
    "title": "The Reckoning",
    "characters": [{"kind": "characters", "id": "marisol"}],
    "turns": [{"role": "assistant", "speaker": "Marisol", "content": "\"You've grown bold.\""}],
}

#: One timeline event and two edits: the plot beat lands, the commitment does not.
#: Both halves matter — the timeline append and the applied beat are the two things
#: a whole-scene retry would file twice.
_PARTIAL_OUTPUT = json_module.dumps({
    "one_line": "Marisol needles Julian.", "summary": "s", "keywords": [],
    "timeline_events": [{"date": "Firstmonth 3", "text": "Marisol swore on the stair."}],
    "character_state_edits": [], "lore_edits": [], "authored_edits": [],
    "relationship_deltas": [], "bond_changes": [],
    "plot_movements": [{"id": "the-siege", "title": "The siege", "status": "open",
                        "beat": "The gate held."}],
    "commitment_movements": [{"id": "", "title": "The debt", "kind": "promise",
                              "status": "open", "beat": "Sworn."}],
})


def _break_commitments_during_apply(monkeypatch):
    """Break `commitments.json` between staging and saving, which `apply_edits`
    reports as a failure for that row while the plot row still lands."""
    from grimoire.store import campaigns as campaigns_store
    real_apply = ingest_scene.absorb.apply_edits

    def _break_then_apply(cid_, edits, sid_):
        (campaigns_store.campaign_root(cid_) / "commitments.json").write_text(
            "{ no", encoding="utf-8")
        return real_apply(cid_, edits, sid_)

    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", _break_then_apply)
    return real_apply


def test_a_scene_whose_edits_did_not_all_land_is_not_marked_done(monkeypatch, tmp_path):
    """`apply_edits` reports commitment failures now (#115), and a batch import is
    exactly where nobody is watching: marking the scene `done` would make the loss
    permanent, since a done key is skipped outright on the next run. It is recorded
    `incomplete` with the reasons, and the retry resumes on the same sid rather than
    minting a duplicate scene."""
    from grimoire.store import campaigns as campaigns_store, scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    real_apply = _break_commitments_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete"
    assert first["failures"] and "commitment" in first["failures"][0]["id"]

    # the retry resumes rather than skipping or duplicating
    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", real_apply)
    (campaigns_store.campaign_root(cid) / "commitments.json").unlink()
    second = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert second["status"] == "done" and second["sid"] == first["sid"]
    assert len(scenes.list_scenes(cid)) == 1


def test_an_incomplete_scene_retries_only_the_rows_that_did_not_land(monkeypatch, tmp_path):
    """Resuming an `incomplete` scene must replay the unapplied rows and nothing
    else. Re-running the whole scene is not a neutral retry: `append_timeline`
    appends, and so do plot and commitment beats, so the run that recovers the
    lost row would file every row that already landed a second time -- a
    duplicate beat under the same scene id, which no later run can tell from two
    real ones.

    The first version of this test used no timeline events and a single failing
    edit, which is exactly the shape that cannot show the bug. It has both here:
    the timeline event and the plot beat are the rows that already landed, and
    the assertion is that the retry leaves them at one each."""
    from grimoire.store import campaigns as campaigns_store, commitments, plot, \
        worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    timeline = campaigns_store.campaign_root(cid) / "timeline.md"

    real_apply = _break_commitments_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete"
    assert [e["id"] for e in first["pending"]] == ["commitment:the-debt"]   # only the loser
    assert first["applied"] == ["plot:the-siege"]
    assert len(plot.get(cid, "the-siege")["beats"]) == 1
    assert timeline.read_text(encoding="utf-8").count("swore on the stair") == 1

    # The retry is not allowed to call the model again either: the rows were
    # approved and persisted, so re-extracting would spend tokens to produce a
    # second, possibly different, set of them.
    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", real_apply)
    (campaigns_store.campaign_root(cid) / "commitments.json").unlink()
    client = FakeClient(_PARTIAL_OUTPUT)
    second = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, client, conn))

    assert second["status"] == "done"
    assert client.calls == []                                        # no second extraction
    assert timeline.read_text(encoding="utf-8").count("swore on the stair") == 1
    assert len(plot.get(cid, "the-siege")["beats"]) == 1              # not re-applied
    assert [b["text"] for b in commitments.get(cid, "the-debt")["beats"]] == ["Sworn."]
    assert sorted(second["applied"]) == ["commitment:the-debt", "plot:the-siege"]
    assert "pending" not in second and "failures" not in second


def test_apply_scene_holds_the_campaign_lock_across_the_whole_sequence(monkeypatch, tmp_path):
    """The conflict pass `apply_edits` runs before its first write only means
    anything if no other writer can move a record between the verdict and the
    write. `commitments.set_movement` locks its own write and nothing wider, so
    without a hold here a concurrent live save lands a newer movement inside that
    window and this older ingested beat is appended after it -- rewinding
    `last_scene` instead of being reported. Same hold, for the same reason, as
    the web chronicle-save route."""
    import contextlib

    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    sid = ingest_scene.build_scene(cid, _PARTIAL_SCENE)

    events: list[str] = []
    real_lock, real_timeline = ingest_scene.locks.campaign_lock, ingest_scene.chronicle.append_timeline
    real_apply = ingest_scene.absorb.apply_edits

    @contextlib.contextmanager
    def _recording_lock(cid_):
        events.append("acquire")
        with real_lock(cid_):
            yield
        events.append("release")

    def _recording_timeline(cid_, evs):
        events.append("timeline")
        return real_timeline(cid_, evs)

    def _recording_apply(cid_, edits_, sid_):
        events.append("apply-start")
        try:
            return real_apply(cid_, edits_, sid_)
        finally:
            events.append("apply-end")

    monkeypatch.setattr(ingest_scene.locks, "campaign_lock", _recording_lock)
    monkeypatch.setattr(ingest_scene.chronicle, "append_timeline", _recording_timeline)
    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", _recording_apply)

    parsed = ingest_scene.absorb.parse_output(_PARTIAL_OUTPUT)
    ingest_scene.apply_scene(cid, sid, parsed, ingest_scene.absorb.materialize(cid, sid, parsed))

    # The outer hold opens before the first write and closes after the last one;
    # the inner acquisitions in between are the store mutators' own, reentrant.
    assert events[0] == "acquire" and events[-1] == "release"
    assert events.index("timeline") < events.index("apply-start") < events.index("apply-end")


def _move_commitment_during_apply(monkeypatch):
    """Another writer advances `the-debt` between staging and applying, so the
    staged row's `before` no longer matches and `batch_verdicts` reports a
    conflict rather than an I/O error."""
    from grimoire.store import commitments
    real_apply = ingest_scene.absorb.apply_edits

    def _move_then_apply(cid_, edits, sid_):
        commitments.set_movement(cid_, "the-debt", "The debt", "promise", "open", "",
                                 "Someone else moved it first.", "live-scene")
        return real_apply(cid_, edits, sid_)

    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", _move_then_apply)
    return real_apply


def test_a_conflicting_row_is_not_replayed_on_the_next_run(monkeypatch, tmp_path):
    """A conflict and an I/O error are both failures and are not both retryable.

    An error is the store refusing a write, and the same row lands once that
    clears. A conflict is the row's staged `before` disagreeing with a record
    that has since moved — and the stored row still carries that same `before`,
    so replaying it reproduces the identical verdict forever. Retrying it would
    leave the scene `incomplete` on every future run while re-acquiring the lock
    and re-reading the store to be told the same thing."""
    from grimoire.store import campaigns as campaigns_store, commitments, plot, \
        worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    timeline = campaigns_store.campaign_root(cid) / "timeline.md"

    _move_commitment_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete"
    assert [f["kind"] for f in first["failures"]] == ["conflict"]
    assert "pending" not in first                       # nothing here can be replayed
    assert [b["text"] for b in commitments.get(cid, "the-debt")["beats"]] == \
        ["Someone else moved it first."]                # the ingested beat did not land

    # The next run reports the same standing failure and touches nothing.
    client = FakeClient(_PARTIAL_OUTPUT)
    second = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, client, conn))
    assert second["status"] == "incomplete" and second["failures"] == first["failures"]
    assert client.calls == []                           # no re-extraction
    assert timeline.read_text(encoding="utf-8").count("swore on the stair") == 1
    assert len(plot.get(cid, "the-siege")["beats"]) == 1
    assert len(commitments.get(cid, "the-debt")["beats"]) == 1


def test_a_conflict_is_carried_across_a_retry_that_clears_the_io_failure(monkeypatch, tmp_path):
    """A scene can fail both ways at once. The retry replays the I/O row, and the
    conflict has to survive that — otherwise the run that fixes the disk reports
    `done` on a scene still missing a movement, which is the silent loss the
    whole `incomplete` status exists to prevent."""
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    croot = campaigns_store.campaign_root(cid)

    # Hand-write the entry the first attempt would have produced: one conflict
    # (unreplayable) beside one error (replayable), so the retry has both.
    sid = ingest_scene.build_scene(cid, _PARTIAL_SCENE)
    ingest_scene.save_manifest(cid, {_PARTIAL_SCENE["key"]: {
        "status": "incomplete", "sid": sid, "one_line": "x", "applied": [],
        "failures": [{"id": "commitment:the-debt", "kind": "conflict", "reason": "moved"},
                     {"id": "plot:the-siege", "kind": "error", "reason": "disk full"}],
        "pending": [{"id": "plot:the-siege", "kind": "plot", "target": {"kind": "plot",
                     "id": "the-siege"}, "field": "beat", "before": "", "after": "The gate held.",
                     "payload": {"id": "the-siege", "title": "The siege", "status": "open",
                                 "scene": sid}}]}})
    assert not (croot / "plot.json").exists()

    result = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert result["applied"] == ["plot:the-siege"]              # the I/O row landed
    assert result["status"] == "incomplete"                     # but the conflict still stands
    assert [f["kind"] for f in result["failures"]] == ["conflict"]
    assert "pending" not in result                              # and nothing is left to replay


def test_the_cli_exits_nonzero_when_a_scene_is_incomplete(monkeypatch, tmp_path, capsys):
    """The batch driver works through a log in strict scene order and its only
    signal is the exit status. Scene N+1 is absorbed against the state scene N
    wrote, so carrying on past a scene whose movement never landed extracts every
    later scene against a snapshot missing it — the damage compounds down the
    file, silently."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json_module.dumps(_PARTIAL_SCENE), encoding="utf-8")
    monkeypatch.setattr(ingest_scene.llm_connections, "get_active",
                        lambda: {"kind": "openrouter", "model": "m", "api_key": "k"})
    monkeypatch.setattr(ingest_scene, "LLMClient", lambda: FakeClient(_PARTIAL_OUTPUT))
    monkeypatch.setattr(sys, "argv",
                        ["ingest_scene.py", "ingest", "--campaign", cid,
                         "--input", str(scene_file)])

    _break_commitments_during_apply(monkeypatch)
    assert ingest_scene.main() == 1
    err = capsys.readouterr().err
    assert "did not land" in err and "re-run this same command" in err

    # A clean scene still exits 0 — the nonzero code has to mean something.
    (campaigns.campaign_root(cid) / "commitments.json").unlink()
    monkeypatch.setattr(ingest_scene.absorb, "apply_edits",
                        ingest_scene.absorb.apply.apply_edits)
    assert ingest_scene.main() == 0


def test_a_renamed_scene_stops_the_resume_rather_than_writing_to_a_ghost(monkeypatch, tmp_path):
    """A scene rename is an id change, and `scene_refs.repoint` follows the seven
    stores that persist scene ids — not this script's manifest, which is its own
    journal. So an `incomplete` entry can outlive the id it names.

    Resuming anyway writes beats and change records stamped with an id no scene
    has, which is worse than not resuming: nothing surfaces it and the manifest
    then says `done`. Rebuilding is not the alternative either — a rename means
    the scene is still there under another name, and rebuilding duplicates it."""
    from grimoire.store import campaigns as campaigns_store, commitments, plot, scenes, \
        worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    _break_commitments_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete" and first["pending"]

    (campaigns_store.campaign_root(cid) / "commitments.json").unlink()
    new_sid = scenes.rename_scene(cid, first["sid"], "The Reckoning, Revisited")
    assert new_sid != first["sid"]

    client = FakeClient(_PARTIAL_OUTPUT)
    second = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, client, conn))
    assert second["status"] == "incomplete"
    assert first["sid"] in second["detail"]           # names the id that went missing
    assert client.calls == []                         # no re-extraction, no rebuild
    assert len(scenes.list_scenes(cid)) == 1          # and no duplicate scene
    assert commitments.read(cid) == {}                # nothing written against the ghost
    assert plot.get(cid, "the-siege")["beats"][0]["scene"] == new_sid   # the store followed


def test_the_cli_reports_a_resume_whose_scene_vanished(monkeypatch, tmp_path, capsys):
    """Same stop, through the exit code the batch driver actually reads."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    sid = ingest_scene.build_scene(cid, _PARTIAL_SCENE)
    ingest_scene.save_manifest(cid, {_PARTIAL_SCENE["key"]: {"status": "in_progress",
                                                             "sid": sid + "-gone"}})

    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json_module.dumps(_PARTIAL_SCENE), encoding="utf-8")
    monkeypatch.setattr(ingest_scene.llm_connections, "get_active",
                        lambda: {"kind": "openrouter", "model": "m", "api_key": "k"})
    monkeypatch.setattr(ingest_scene, "LLMClient", lambda: FakeClient(_PARTIAL_OUTPUT))
    monkeypatch.setattr(sys, "argv",
                        ["ingest_scene.py", "ingest", "--campaign", cid,
                         "--input", str(scene_file)])

    assert ingest_scene.main() == 1
    assert "no longer exists" in capsys.readouterr().err


def test_resolve_closes_a_conflicted_scene_without_rebuilding_it(monkeypatch, tmp_path):
    """The recovery this replaces was worse than the problem it recovered from.
    A standing conflict cannot be replayed, so the only way out of `incomplete`
    was deleting the key — and a deleted key is an unknown scene: the next run
    takes the no-entry branch, calls `build_scene`, and absorbs the transcript
    again, duplicating the scene, its timeline events and every beat that
    already landed. That is what the manifest entry exists to prevent, produced
    by following the instruction printed when nothing else could finish it."""
    from grimoire.store import campaigns as campaigns_store, commitments, plot, scenes, \
        worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    timeline = campaigns_store.campaign_root(cid) / "timeline.md"

    _move_commitment_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete" and "pending" not in first

    ok, closed = ingest_scene.resolve_key(cid, _PARTIAL_SCENE["key"])
    assert ok
    assert closed["status"] == "done" and closed["sid"] == first["sid"]
    # The scene is done because a person dealt with it, not because every row
    # landed — the manifest must not read as if it had.
    assert [f["kind"] for f in closed["reconciled"]] == ["conflict"]
    assert "failures" not in closed and "pending" not in closed

    client = FakeClient(_PARTIAL_OUTPUT)
    after = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, client, conn))
    assert after["status"] == "skipped"
    assert client.calls == []                                        # no re-absorb
    assert len(scenes.list_scenes(cid)) == 1                         # no duplicate scene
    assert timeline.read_text(encoding="utf-8").count("swore on the stair") == 1
    assert len(plot.get(cid, "the-siege")["beats"]) == 1
    assert len(commitments.get(cid, "the-debt")["beats"]) == 1


def test_resolve_refuses_a_key_it_should_not_close(monkeypatch, tmp_path):
    """It closes a scene that absorbed and could not finish — not an unknown key
    (a typo would silently invent an entry) and not one still mid-flight."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))

    ok, why = ingest_scene.resolve_key(cid, "file1-scene99")
    assert not ok and "no manifest entry" in why

    ingest_scene.save_manifest(cid, {"k": {"status": "in_progress", "sid": "001--x"}})
    ok, why = ingest_scene.resolve_key(cid, "k")
    assert not ok and "not incomplete" in why
    assert ingest_scene.load_manifest(cid)["k"]["status"] == "in_progress"   # untouched

    # Idempotent on a key that is already closed: re-running a recovery step
    # must not be an error.
    ingest_scene.save_manifest(cid, {"k": {"status": "done", "sid": "001--x"}})
    ok, entry = ingest_scene.resolve_key(cid, "k")
    assert ok and entry["status"] == "done"


def test_the_cli_points_at_resolve_rather_than_at_deleting_the_key(monkeypatch, tmp_path, capsys):
    """The instruction is the fix here: the old one told the operator to do the
    thing that duplicates the scene."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json_module.dumps(_PARTIAL_SCENE), encoding="utf-8")
    monkeypatch.setattr(ingest_scene.llm_connections, "get_active",
                        lambda: {"kind": "openrouter", "model": "m", "api_key": "k"})
    monkeypatch.setattr(ingest_scene, "LLMClient", lambda: FakeClient(_PARTIAL_OUTPUT))
    monkeypatch.setattr(sys, "argv",
                        ["ingest_scene.py", "ingest", "--campaign", cid,
                         "--input", str(scene_file)])

    _move_commitment_during_apply(monkeypatch)
    assert ingest_scene.main() == 1
    err = capsys.readouterr().err
    assert f"resolve --campaign {cid} --key {_PARTIAL_SCENE['key']}" in err
    assert "do NOT delete the key" in err


def test_the_resume_revalidates_the_scene_inside_the_lock(monkeypatch, tmp_path):
    """Checking before taking the lock is a check-then-act. `scenes` mutators
    serialize on the same campaign lock, so a rename that starts after the check
    completes before the replay acquires it — and the replay then stamps beats
    and change records with an id no scene has, then marks the entry done."""
    from grimoire.store import campaigns as campaigns_store, commitments, scenes, \
        worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}

    _break_commitments_during_apply(monkeypatch)
    first = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))
    assert first["status"] == "incomplete" and first["pending"]
    (campaigns_store.campaign_root(cid) / "commitments.json").unlink()
    monkeypatch.setattr(ingest_scene.absorb, "apply_edits",
                        ingest_scene.absorb.apply.apply_edits)

    # The rename lands in the window the unlocked check cannot cover: after it
    # has answered, before the replay takes the lock.
    real_exists, seen = ingest_scene._scene_exists, []

    def _rename_after_the_check(cid_, sid_):
        ok = real_exists(cid_, sid_)
        if ok and not seen:
            seen.append(sid_)
            scenes.rename_scene(cid_, sid_, "The Reckoning, Revisited")
        return ok

    monkeypatch.setattr(ingest_scene, "_scene_exists", _rename_after_the_check)
    second = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))

    assert second["status"] == "incomplete"          # not done, and nothing written
    assert first["sid"] in second["detail"]
    assert commitments.read(cid) == {}               # no beat against the ghost id


def test_the_cli_prints_each_unreplayable_reason(monkeypatch, tmp_path, capsys):
    """A `changes` failure — the write-back delta the Changes panel reads — is
    reported by `apply_edits` with an id that matches no staged row, so it is
    correctly not replayed (replaying would re-apply the edits that DID land).
    What it must not do is arrive under a message that says every unreplayable
    failure is a conflict: the reasons are printed, so the operator can tell a
    lost delta from a record that moved."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})

    scene_file = tmp_path / "scene.json"
    scene_file.write_text(json_module.dumps(_PARTIAL_SCENE), encoding="utf-8")
    monkeypatch.setattr(ingest_scene.llm_connections, "get_active",
                        lambda: {"kind": "openrouter", "model": "m", "api_key": "k"})
    monkeypatch.setattr(ingest_scene, "LLMClient", lambda: FakeClient(_PARTIAL_OUTPUT))
    monkeypatch.setattr(sys, "argv",
                        ["ingest_scene.py", "ingest", "--campaign", cid,
                         "--input", str(scene_file)])

    real_apply = ingest_scene.absorb.apply_edits

    def _apply_then_lose_the_delta(cid_, edits, sid_):
        applied, failures = real_apply(cid_, edits, sid_)
        return applied, [*failures, {"id": "changes", "kind": "error",
                                     "reason": "the changes panel could not be updated"}]

    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", _apply_then_lose_the_delta)
    assert ingest_scene.main() == 1
    err = capsys.readouterr().err
    assert "changes: the changes panel could not be updated" in err
    assert "none of them can be replayed" in err
    assert "conflict with records" not in err          # it is not a conflict
    assert "resolve --campaign" in err


def test_a_record_creating_row_is_not_replayed(monkeypatch, tmp_path):
    """`new_character` writes the character and THEN seats it in the scene, so a
    failure after the first step leaves the record made. Replaying does not retry
    the step that failed — `overlay.create_*` uniquifies an occupied slug, so the
    retry mints a second character and seats that one. Reported, kept out of
    `pending`, and left to a person."""
    edits = [{"id": "new_character:cassian", "kind": "new_character"},
             {"id": "new_location:the-pier", "kind": "new_location"},
             {"id": "new_lore:the-pact", "kind": "new_lore"},
             {"id": "commitment:the-debt", "kind": "commitment"}]
    failures = [{"id": e["id"], "kind": "error", "reason": "disk full"} for e in edits]
    # only the row that writes exactly one thing is replayable
    assert [e["id"] for e in ingest_scene._unapplied(edits, failures)] == ["commitment:the-debt"]


def test_a_scene_whose_only_failure_created_a_record_stops_for_a_person(monkeypatch, tmp_path):
    """End to end: no `pending`, so the run reports incomplete and the next one
    does nothing rather than minting a duplicate."""
    from grimoire.store import worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    text = json_module.dumps({
        "one_line": "a", "summary": "s", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
        "new_lore": [{"name": "The Pact", "body": "Sworn at the pier."}],
    })
    real_apply = ingest_scene.absorb.apply_edits

    def _fail_the_create(cid_, edits, sid_):
        applied, failures = real_apply(cid_, edits, sid_)
        return [], [*failures, *({"id": e["id"], "kind": "error", "reason": "disk full"}
                                 for e in edits if e["kind"] == "new_lore")]

    monkeypatch.setattr(ingest_scene.absorb, "apply_edits", _fail_the_create)
    first = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, FakeClient(text), conn))
    assert first["status"] == "incomplete"
    assert "pending" not in first                    # nothing safe to replay

    client = FakeClient(text)
    second = asyncio.run(ingest_scene.ingest_one_scene(cid, _PARTIAL_SCENE, client, conn))
    assert second["status"] == "incomplete" and client.calls == []


def test_an_ambiguous_edit_id_is_replayed_by_nobody():
    """`apply_edits` reports a failure by id and nothing else, and `materialize`
    does not promise uniqueness — two lore appends against one entry are both
    `lore:<eid>`. Matching in order queues whichever came FIRST, which is the one
    that landed, and drops the one that failed: the retry then re-applies stale
    text and the scene reports `done` with the real proposal gone."""
    edits = [{"id": "lore:the-pact", "kind": "lore", "after": "landed"},
             {"id": "lore:the-pact", "kind": "lore", "after": "failed"},
             {"id": "commitment:the-debt", "kind": "commitment"}]
    failures = [{"id": "lore:the-pact", "kind": "error", "reason": "disk full"},
                {"id": "commitment:the-debt", "kind": "error", "reason": "disk full"}]
    assert [e["id"] for e in ingest_scene._unapplied(edits, failures)] == \
        ["commitment:the-debt"]


def test_an_unreplayable_failure_survives_a_successful_retry(monkeypatch, tmp_path):
    """Conflicts were the first reason to carry failures forward; record-creating
    rows and the `changes` log joined them, and a `kind == "conflict"` filter
    silently dropped the new ones — so a retry that cleared the last I/O error
    reported the scene `done` while a half-created character still stood."""
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = ingest_scene.ensure_campaign("Silver Oath", worlds_store.create_world("Ashgrove"))
    ingest_scene.ensure_character(cid, {"name": "Marisol"})
    conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}
    sid = ingest_scene.build_scene(cid, _PARTIAL_SCENE)
    stranded = {"id": "new_character:cassian", "kind": "error",
                "reason": "could not seat the new character"}
    pending = [{"id": "commitment:the-debt", "kind": "commitment", "field": "beat",
                "target": {"kind": "commitments", "id": "the-debt"},
                "before": "", "after": "Sworn.",
                "payload": {"id": "the-debt", "title": "The debt", "kind": "promise",
                            "status": "open", "due": None, "scene": sid}}]
    ingest_scene.save_manifest(cid, {_PARTIAL_SCENE["key"]: {
        "status": "incomplete", "sid": sid, "one_line": "x", "applied": [],
        "failures": [stranded, {"id": "commitment:the-debt", "kind": "error",
                                "reason": "disk full"}],
        "pending": pending}})

    result = asyncio.run(ingest_scene.ingest_one_scene(
        cid, _PARTIAL_SCENE, FakeClient(_PARTIAL_OUTPUT), conn))

    assert result["applied"] == ["commitment:the-debt"]      # the retry landed
    assert result["status"] == "incomplete"                  # but the other still stands
    assert result["failures"] == [stranded]
    assert "pending" not in result                            # and it is not replayable
    assert campaigns_store.campaign_root(cid).exists()
