"""The per-turn prompt log: what the model saw, frozen at the moment it saw it.

The load-bearing property is the first test — a snapshot does not move when the
store it was composed from does. Everything else here is retention, scene-id
repointing, and the refusals that must never cost a turn.
"""

import json
import threading
import time

import pytest

from grimoire.store import (campaigns, config, context, entities, locks, prompt_log,
                            scene_refs, scenes, worlds)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("Realm"))
    return cid, scenes.create_scene(cid, "Saltmarch")


def _record(cid, sid, task="chat"):
    _messages, breakdown = context.compose_turn(cid, sid)
    return prompt_log.record(cid, sid, task, breakdown, model="test/model")


def test_a_snapshot_does_not_move_when_the_store_does(monkeypatch, tmp_path):
    """The whole point of #157. The live breakdown follows the store; the
    snapshot stays where it was."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "we make for the harbor")
    eid = _record(cid, sid)

    frozen_then = prompt_log.read_entry(cid, eid)

    # The store moves on: new lore lands and the transcript grows, both of which
    # the live composition picks up.
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Harbor Pact",
                           "The pact was signed at dusk.")
    scenes.append_message(cid, sid, "user", "tell me of the pact")

    live = context.context_breakdown(cid, sid)
    assert "The pact was signed at dusk." in json.dumps(live["sections"])

    frozen_now = prompt_log.read_entry(cid, eid)
    assert frozen_now == frozen_then
    assert "The pact was signed at dusk." not in json.dumps(frozen_now["sections"])


def test_the_snapshot_describes_the_messages_that_were_sent(monkeypatch, tmp_path):
    """One assemble/pack pass feeds both, so the record cannot describe a
    different prompt than the one that went out.

    Checked against the WHOLE request rather than the system message: rows are
    an inventory of what was sent, and post-history and appended blocks ride as
    separate messages (see `context._breakdown`)."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    block = "Rewrite the last reply, shorter."
    messages, breakdown = context.compose_turn(
        cid, sid, appended=(("Regenerate guidance", "system", block),))

    sent = "\n".join(m["content"] for m in messages)
    kept = [s for s in breakdown["sections"]
            if not s["dropped"] and s["tier"] != context.HISTORY]
    assert kept, "a scene with a message must report at least one kept row"
    for row in kept:
        assert row["text"] in sent, row["label"]
    # and a dropped row is reported precisely because it did NOT go out
    for row in breakdown["sections"]:
        if row["dropped"]:
            assert row["text"] not in sent


def test_appended_blocks_are_reserved_appended_and_reported_together(monkeypatch, tmp_path):
    """`appended` owns all three consequences at once — the drift this exists to
    prevent is a regenerate whose snapshot omits the guidance the model read."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    block = "Rewrite the last reply, shorter."
    messages, breakdown = context.compose_turn(
        cid, sid, appended=(("Regenerate guidance", "system", block),))

    assert messages[-1] == {"role": "system", "content": block}
    row = next(r for r in breakdown["sections"] if r["label"] == "Regenerate guidance")
    assert row["text"] == block and not row["dropped"]


def test_entries_are_listed_newest_first_and_scoped_to_the_scene(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    other = scenes.create_scene(cid, "Elsewhere")
    first = _record(cid, sid, "chat")
    _record(cid, other, "chat")
    second = _record(cid, sid, "retry")

    listed = prompt_log.list_entries(cid, sid)
    assert [e["id"] for e in listed] == [second, first]
    assert [e["task"] for e in listed] == ["retry", "chat"]
    assert all(e["scene"] == sid for e in listed)
    # the list view carries the totals but never the section text
    assert "sections" not in listed[0]


def test_retention_evicts_oldest_and_unlinks_the_payload(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    config.write_config(prompt_log_depth="2")
    a = _record(cid, sid)
    b = _record(cid, sid)
    c = _record(cid, sid)

    assert [e["id"] for e in prompt_log.list_entries(cid, sid)] == [c, b]
    assert prompt_log.read_entry(cid, a) is None
    assert not (campaigns.campaign_root(cid) / "prompts" / f"{a}.json").exists()


def test_ids_are_never_reissued_after_eviction(monkeypatch, tmp_path):
    """A recycled id would hand a pruned turn's URL to a different turn."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    config.write_config(prompt_log_depth="1")
    seen = {_record(cid, sid) for _ in range(4)}
    assert len(seen) == 4


def test_depth_zero_records_nothing(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    config.write_config(prompt_log_depth="0")
    assert _record(cid, sid) is None
    assert prompt_log.list_entries(cid, sid) == []
    assert not (campaigns.campaign_root(cid) / "prompts").exists()


def test_a_malformed_depth_falls_back_to_the_default(monkeypatch, tmp_path):
    """Same judgement `pack.budget_tokens` makes: a hand-edited config.md must
    not take generation down with it."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    config.write_config(prompt_log_depth="soon")
    assert prompt_log.depth() == int(config.DEFAULT_PROMPT_LOG_DEPTH)
    assert _record(cid, sid) is not None


def test_a_non_numeric_id_in_the_index_does_not_reach_the_caller(monkeypatch, tmp_path):
    """`next` is derived from the ids, so a row `int()` chokes on used to raise
    straight through `record`'s guard and 500 the turn that captured it. A
    hand-edited debug file must cost the log, never the generation."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / "index.json").write_text(
        json.dumps({"next": 2, "entries": [{"id": "legacy", "scene": sid}]}), encoding="utf-8")

    assert prompt_log.list_entries(cid, sid) == []
    eid = _record(cid, sid)
    assert eid is not None
    assert [e["id"] for e in prompt_log.list_entries(cid, sid)] == [eid]


def test_repoint_follows_a_renamed_scene(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    prompt_log.repoint_scenes(cid, {sid: "0002-saltmarch"})

    assert prompt_log.list_entries(cid, sid) == []
    assert [e["id"] for e in prompt_log.list_entries(cid, "0002-saltmarch")] == [eid]


def test_the_index_alone_owns_the_scene_field(monkeypatch, tmp_path):
    """A copy in the payload would go stale on the first rename, and a reader
    trusting it would refuse the entry it had just listed."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    assert "scene" not in prompt_log.read_entry(cid, eid)

    prompt_log.repoint_scenes(cid, {sid: "0002-saltmarch"})
    assert prompt_log.read_entry(cid, eid, scene="0002-saltmarch") is not None
    assert prompt_log.read_entry(cid, eid, scene=sid) is None


def test_scene_refs_repoint_reaches_the_prompt_log(monkeypatch, tmp_path):
    """The fan-out, not just the function — a store that repoints only when
    called directly is a store that stops repointing."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    scene_refs.repoint(cid, {sid: "0009-renamed"})
    assert [e["id"] for e in prompt_log.list_entries(cid, "0009-renamed")] == [eid]


def test_deleting_a_scene_drops_its_snapshots(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    other = scenes.create_scene(cid, "Elsewhere")
    gone = _record(cid, sid)
    kept = _record(cid, other)

    prompt_log.forget_scene(cid, sid)
    assert prompt_log.read_entry(cid, gone) is None
    assert prompt_log.read_entry(cid, kept) is not None


def test_a_write_failure_never_costs_a_turn(monkeypatch, tmp_path):
    """A debug view that can fail generation is worse than the bug it
    diagnoses."""
    cid, sid = _campaign(monkeypatch, tmp_path)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(prompt_log.atomic, "write_text", boom)
    assert _record(cid, sid) is None


def test_a_corrupt_index_reads_as_empty_rather_than_raising(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / "index.json").write_text("{[", encoding="utf-8")

    assert prompt_log.list_entries(cid, sid) == []
    # and the next capture rebuilds rather than refusing
    assert _record(cid, sid) is not None


def test_read_entry_refuses_a_traversing_id(monkeypatch, tmp_path):
    cid, _sid = _campaign(monkeypatch, tmp_path)
    assert prompt_log.read_entry(cid, "../../config") is None


# ---- fixes from PR #307 review ----

def test_a_non_utf8_index_reads_as_empty_rather_than_raising(monkeypatch, tmp_path):
    """`read_text` raises UnicodeDecodeError, which is a ValueError and NOT an
    OSError — so the narrow catch let it past `record` and every generating turn
    failed over an unreadable debug file."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / "index.json").write_bytes(b'{"next": \xff\xfe}')

    assert prompt_log.list_entries(cid, sid) == []
    assert _record(cid, sid) is not None


def test_a_non_utf8_payload_reads_as_missing(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / f"{eid}.json").write_bytes(b"\xff\xfe")
    assert prompt_log.read_entry(cid, eid) is None


def test_forget_scene_refuses_rather_than_freeing_an_id_it_could_not_clear(monkeypatch, tmp_path):
    """Scene ids are recycled, so rows left behind are adopted by the next scene
    to take the id. `delete_scene` already refuses over the alternates sidecar
    for this reason; the prompt log gets the same answer."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(prompt_log.atomic, "write_text", boom)
    with pytest.raises(OSError):
        scenes.delete_scene(cid, sid)
    # the scene — and therefore its id — is still there
    assert sid in [s["id"] for s in scenes.list_scenes(cid)]


def test_the_frozen_budget_is_the_one_the_packer_used(monkeypatch, tmp_path):
    """Saving a new ceiling mid-compose used to make the breakdown report a
    budget the packing pass never applied — drift the live panel could shrug
    off, but a snapshot would claim forever."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    config.write_config(context_budget="4000")

    real_pack = context.pack.pack

    def repack(*args, **kwargs):
        # the user hits Save on the Configuration page mid-compose
        config.write_config(context_budget="999999")
        return real_pack(*args, **kwargs)

    monkeypatch.setattr(context.pack, "pack", repack)
    _messages, breakdown = context.compose_turn(cid, sid)
    assert breakdown["budget_tokens"] == 4000


def test_describe_false_builds_no_breakdown(monkeypatch, tmp_path):
    """Capture off must not leave a full tokenizer pass on every turn: on the
    default unbounded budget the packer counts nothing, and `_breakdown` counts
    everything."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")

    calls = {"n": 0}
    real = context.tokens.count_tokens

    def counted(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(context.tokens, "count_tokens", counted)
    messages, breakdown = context.compose_turn(cid, sid, describe=False)
    assert breakdown is None
    assert messages
    assert calls["n"] == 0          # unbounded budget: nothing counted at all

    calls["n"] = 0
    _m, described = context.compose_turn(cid, sid, describe=True)
    assert described is not None
    assert calls["n"] > 0


def test_capturing_follows_the_configured_depth(monkeypatch, tmp_path):
    _cid, _sid = _campaign(monkeypatch, tmp_path)
    assert prompt_log.capturing() is True
    config.write_config(prompt_log_depth="0")
    assert prompt_log.capturing() is False


def test_an_absurdly_long_numeric_id_does_not_reach_the_caller(monkeypatch, tmp_path):
    """`safe_id` caps nothing and `"1" * 5000` is `isdigit()`, but CPython
    refuses `int()` past 4300 digits — with a ValueError, straight through
    `record`'s guard. Third instance of this hole; the length bound closes it."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / "index.json").write_text(
        json.dumps({"next": 2, "entries": [{"id": "1" * 5000, "scene": sid}]}),
        encoding="utf-8")

    assert prompt_log.list_entries(cid, sid) == []
    assert _record(cid, sid) is not None


def test_a_structurally_corrupt_payload_reads_as_missing(monkeypatch, tmp_path):
    """`{}` is valid JSON. Served as a snapshot it reaches `ContextBreakdown`,
    which calls `ctx.total_tokens.toLocaleString()` and `ctx.sections.map()`
    unguarded — so a hand-edited payload would take the inspector down rather
    than read as a debug entry that is unavailable."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    path = campaigns.campaign_root(cid) / "prompts" / f"{eid}.json"

    good = json.loads(path.read_text(encoding="utf-8"))

    for bad in ({}, {"sections": []},
                {**good, "sections": "not a list"},
                {**good, "sections": [{"label": "World info"}]},   # row missing fields
                {**good, "total_tokens": "lots"},
                # The metadata the FROZEN panel renders, which validating only
                # the index row left reachable one click later: `seen.task` and
                # `whenLabel(seen.ts)` come off the snapshot, not off the row.
                {**good, "task": {}},
                {**good, "ts": 17},
                {**good, "model": None},
                {**good, "id": 1}):
        path.write_text(json.dumps(bad), encoding="utf-8")
        assert prompt_log.read_entry(cid, eid) is None, bad


def test_a_payload_with_unknown_extra_fields_still_reads(monkeypatch, tmp_path):
    """Strict about what the panel dereferences, tolerant of everything else —
    or a payload written by a later version stops being readable."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    eid = _record(cid, sid)
    path = campaigns.campaign_root(cid) / "prompts" / f"{eid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["some_future_field"] = {"nested": True}
    path.write_text(json.dumps(data), encoding="utf-8")

    assert prompt_log.read_entry(cid, eid) is not None


def test_a_repoint_that_cannot_be_written_drops_the_rows(monkeypatch, tmp_path):
    """The rename cannot be refused — transcripts are already moved by the time
    `scene_refs.repoint` runs — so rows that cannot follow are dropped instead.
    Left keyed to the old id they would be adopted by a recreated scene that
    lands on it."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    real = prompt_log.atomic.write_text
    calls = {"n": 0}

    def fail_once(path, text):
        calls["n"] += 1
        if calls["n"] == 1:          # the repoint write
            raise OSError("disk full")
        return real(path, text)      # the drop write

    monkeypatch.setattr(prompt_log.atomic, "write_text", fail_once)
    prompt_log.repoint_scenes(cid, {sid: "0002-renamed"})

    # neither the old id nor the new one carries them any more
    assert prompt_log.list_entries(cid, sid) == []
    assert prompt_log.list_entries(cid, "0002-renamed") == []


def test_a_locked_index_blocks_the_delete_rather_than_freeing_the_id(monkeypatch, tmp_path):
    """The read-side twin of the write-side raise. An index that cannot be read
    comes back empty, so `gone` is empty and the delete would sail through and
    free a recycled id with the rows still on disk.

    The failure is injected by pointing the index at a DIRECTORY: reading one
    raises IsADirectoryError, a real OSError, without stubbing out reads the
    rest of the store needs."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    blocked = campaigns.campaign_root(cid) / "prompts" / "unreadable.json"
    blocked.mkdir()
    real_index_path = prompt_log._index_path
    monkeypatch.setattr(prompt_log, "_index_path", lambda _cid: blocked)

    with pytest.raises(OSError):
        scenes.delete_scene(cid, sid)
    # restore just this one -- `monkeypatch.undo()` would also revert the
    # GRIMOIRE_HOME the fixture set, pointing the assertions at the real store
    monkeypatch.setattr(prompt_log, "_index_path", real_index_path)
    assert sid in [s["id"] for s in scenes.list_scenes(cid)]
    assert [e["scene"] for e in prompt_log.list_entries(cid, sid)] == [sid]


def test_a_corrupt_index_does_not_block_deletion_forever(monkeypatch, tmp_path):
    """The other half of that judgement: corruption is not transient, so raising
    on it would wedge every scene deletion in the campaign. Its rows are
    invisible to `list_entries` anyway, so a recycled id inherits nothing a user
    can see."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    (campaigns.campaign_root(cid) / "prompts" / "index.json").write_text("{[", encoding="utf-8")

    scenes.delete_scene(cid, sid)
    assert sid not in [s["id"] for s in scenes.list_scenes(cid)]


def test_an_unreadable_config_does_not_make_depth_raise(monkeypatch, tmp_path):
    """`capturing()` runs on every generating route, so this must fall back
    rather than raise. It does not make an unreadable config.md survivable
    overall — `context._assemble` reads it unguarded moments later — it only
    keeps this module's own promise."""
    _cid, _sid = _campaign(monkeypatch, tmp_path)

    def boom():
        raise OSError("locked by another process")

    monkeypatch.setattr(prompt_log.config, "read_config", boom)
    assert prompt_log.depth() == int(config.DEFAULT_PROMPT_LOG_DEPTH)
    assert prompt_log.capturing() is True


def test_capture_skips_a_contended_campaign_rather_than_waiting(monkeypatch, tmp_path):
    """`record` runs synchronously before the route returns its stream, so a
    blocking acquisition would stall the turn for the whole LOCK_TIMEOUT and
    then discard the snapshot anyway. Contended means skipped, not delayed."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _messages, breakdown = context.compose_turn(cid, sid)

    held = threading.Event()
    done = threading.Event()

    def hold():
        with locks.campaign_lock(cid):
            held.set()
            done.wait(10)

    keeper = threading.Thread(target=hold)
    keeper.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        assert prompt_log.record(cid, sid, "chat", breakdown) is None
        assert time.monotonic() - started < 1.0     # not LOCK_TIMEOUT (30s)
    finally:
        done.set()
        keeper.join(10)

    assert prompt_log.list_entries(cid, sid) == []
    # and the very next capture, uncontended, records normally
    assert prompt_log.record(cid, sid, "chat", breakdown) is not None


def test_capture_still_works_when_the_caller_already_holds_the_lock(monkeypatch, tmp_path):
    """Non-blocking must not mean non-reentrant: the underlying RLock grants a
    non-blocking request to a thread that already owns it."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _messages, breakdown = context.compose_turn(cid, sid)
    with locks.campaign_lock(cid):
        assert prompt_log.record(cid, sid, "chat", breakdown) is not None


def test_a_repoint_whose_index_cannot_be_read_still_drops_the_rows(monkeypatch, tmp_path):
    """A lenient read turned a locked index into an empty one, so the function
    returned having neither repointed nor dropped — exiting above the fallback
    meant to catch exactly this."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    _record(cid, sid)
    real_index_path = prompt_log._index_path
    blocked = campaigns.campaign_root(cid) / "prompts" / "unreadable.json"
    blocked.mkdir()

    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        return blocked if calls["n"] == 1 else real_index_path(c)

    monkeypatch.setattr(prompt_log, "_index_path", flaky)
    prompt_log.repoint_scenes(cid, {sid: "0002-renamed"})
    monkeypatch.setattr(prompt_log, "_index_path", real_index_path)

    assert prompt_log.list_entries(cid, sid) == []
    assert prompt_log.list_entries(cid, "0002-renamed") == []


def test_a_row_with_malformed_metadata_is_not_listed(monkeypatch, tmp_path):
    """The id was validated but the UI-facing fields were not, so a row like
    `"task": {}` reached `SceneInspector`, which renders it straight into the
    DOM — and React throws on an object child, taking the panel down."""
    cid, sid = _campaign(monkeypatch, tmp_path)
    good = _record(cid, sid)
    path = campaigns.campaign_root(cid) / "prompts" / "index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    row = dict(index["entries"][0])

    for bad in ({"task": {}}, {"ts": 17}, {"model": None}, {"scene": ["s"]},
                {"total_tokens": "lots"}, {"budget_tokens": None}):
        index["entries"] = [{**row, "id": "000009", **bad}, dict(row)]
        path.write_text(json.dumps(index), encoding="utf-8")
        assert [e["id"] for e in prompt_log.list_entries(cid, sid)] == [good], bad
