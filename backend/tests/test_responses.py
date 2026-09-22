"""Durable response identity and strict handoff protocol."""

import pytest

from grimoire import store
from grimoire.store.response_protocol import ResponseWatcher, validate_handoff


@pytest.mark.parametrize("cut", range(1, 40))
def test_split_handoff_never_leaks(cut):
    text = 'Hello.\n```state\n{}\n```\n```handoff\n{"next":"characters:winifred"}\n```'
    watcher = ResponseWatcher()
    shown = watcher.feed(text[:cut]) + watcher.feed(text[cut:]) + watcher.finish()
    assert shown.strip() == "Hello."
    assert watcher.handoff == {"next": "characters:winifred"}
    assert watcher.narration.strip() == "Hello.\n```state\n{}\n```"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"next": []},
        {"next": "characters:mara"},
        {"next": "pcs:seraphine"},
        {"next": "absent"},
        {"next": "grimoire"},
        {"next": None, "extra": 1},
    ],
)
def test_invalid_handoff_stops(payload):
    nxt, issue = validate_handoff(
        payload, ["characters:winifred", "grimoire"], ["characters:mara", "grimoire"]
    )
    assert nxt is None and issue


def test_valid_stop_and_actor():
    assert validate_handoff({"next": None}, [], []) == (None, None)
    assert validate_handoff({"next": "characters:mara"}, ["characters:mara"], []) == (
        "characters:mara",
        None,
    )


def test_roll_precedes_handoff():
    watcher = ResponseWatcher()
    watcher.feed(
        'Wait.\n```roll\n{"check":"notice"}\n```\n```handoff\n{"next":"characters:mara"}\n```'
    )
    watcher.finish()
    assert watcher.roll.complete and watcher.handoff is None


def test_response_ids_survive_identical_text_and_middle_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "assistant", "Same.", speaker="Mara")
    store.scenes.append_message(cid, sid, "assistant", "Same.", speaker="Mara")
    records = store.responses.migrate(cid, sid)
    first, second = [r["id"] for r in records]
    assert first != second
    store.responses.delete(cid, sid, first)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1 and messages[0]["response_id"] == second
    assert messages[0]["context_changed"]
    store.scenes.edit_message(cid, sid, 0, "Edited.")
    assert store.scenes.read_scene(cid, sid)["messages"][0]["response_id"] == second
    assert store.responses.get(cid, sid, second)["content"] == "Edited."


@pytest.mark.parametrize("tail", ["```h", "```hand", "```handoff", '```handoff\n{"next":'])
def test_torn_handoff_hidden(tail):
    watcher = ResponseWatcher()
    shown = watcher.feed("Hello.\n" + tail) + watcher.finish()
    assert shown.strip() == "Hello."
    assert watcher.narration.strip() == "Hello."
    assert watcher.handoff is None


def test_legacy_migration_preserves_combined_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_reply(
        cid,
        sid,
        [{"speaker": "Mara", "content": "One."}, {"speaker": "Winifred", "content": "Two."}],
    )
    before = store.scenes.get_turn_sizes(cid, sid)
    store.responses.migrate(cid, sid)
    assert store.scenes.get_turn_sizes(cid, sid) == before
    assert store.scenes.remove_trailing_assistant_run(cid, sid)["size"] == 2


def test_recovery_replaces_partial_text_after_variant_commit_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    round_record = store.responses.new_round(
        cid, sid, eligible=[], automatic=False, post=None, run_id="test")
    record = store.responses.prepare(cid, sid, round_record["id"],
                                     "characters:mara", "Mara", {})
    rid = record["id"]
    store.responses.save_variant(cid, sid, rid, "Partial", "incomplete")
    from grimoire.store.scenes import write
    original = write.replace_messages
    def crash(*args, **kwargs):
        raise OSError("simulated transcript write failure")
    monkeypatch.setattr(write, "replace_messages", crash)
    with pytest.raises(OSError):
        store.responses.save_variant(cid, sid, rid, "Complete answer", "complete")
    monkeypatch.setattr(write, "replace_messages", original)
    store.responses.publish_saved(cid, sid, rid)
    store.responses.publish_saved(cid, sid, rid)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Complete answer"
    assert messages[0]["response_status"] == "complete"


# -- scene open: the migration fast path ------------------------------------
#
# Every GET of a scene (and every character-turn reroll) used to run the full
# `migrate`, which takes the campaign lock and parses the WHOLE campaign's
# response ledger to find nothing to do. A scene's posts are assigned their
# response exactly once, so the steady state must cost a transcript read and
# nothing else -- in particular it must not queue behind a turn that holds the
# lock for its whole ledger rewrite.


def _legacy_scene():
    """A scene whose reply predates response identities."""
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "Where is the tide ledger?")
    store.scenes.append_message(cid, sid, "assistant", "Under the table.", speaker="Mara")
    return cid, sid


def test_needs_migration_matches_what_migrate_assigns():
    from grimoire.store.scenes import serialize

    def post(**extra):
        return {"role": "assistant", "speaker": "Mara", "content": "Hm.", **extra}

    assert store.responses.needs_migration([post()])
    assert not store.responses.needs_migration([post(response_id="r1")])
    assert not store.responses.needs_migration(
        [{"role": "user", "speaker": "You", "content": "Hello."}])
    # Synthetic lines are nobody's reply: `migrate` skips them, so they must
    # not keep a scene on the slow path forever.
    assert not store.responses.needs_migration(
        [post(speaker=speaker) for speaker in serialize.SYNTHETIC_SPEAKERS])
    assert not store.responses.needs_migration([])


def test_the_first_open_assigns_a_legacy_post_its_response(client):
    cid, sid = _legacy_scene()
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}")
    assert body.status_code == 200, body.text
    shown = body.json()["messages"][1]
    assert shown["response_id"] and shown["response_status"] == "complete"
    # The id is on disk and in the ledger, not only in the response body.
    stored = store.scenes.read_scene(cid, sid)["messages"][1]
    assert stored["response_id"] == shown["response_id"]
    assert store.responses.get(cid, sid, shown["response_id"])["content"] == "Under the table."


def test_a_migrated_scene_opens_without_the_ledger_or_the_lock(client, monkeypatch):
    cid, sid = _legacy_scene()
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    first = client.get(base).json()
    first_window = client.get(base + "?limit=1").json()

    def refuse(*args, **kwargs):
        raise AssertionError("a steady-state scene open reached the ledger or the lock")

    # A context, not the test-scoped patch: the client's lifespan shutdown runs
    # before monkeypatch's own teardown and is entitled to the real lock.
    with monkeypatch.context() as patch:
        patch.setattr(store.responses, "_read", refuse)
        patch.setattr(store.locks, "campaign_lock", refuse)
        again = client.get(base)
        window = client.get(base + "?limit=1")
    assert again.status_code == 200, again.text
    assert window.status_code == 200, window.text
    assert again.json() == first
    assert window.json() == first_window


def test_a_scene_without_an_identity_still_takes_the_full_migrate(client, monkeypatch):
    from grimoire.store import frontmatter

    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "Nothing to migrate here.")
    # Written before identities existed: nothing needs a response id, but the
    # open is still what hands the scene its identity (`migrate` -> `_scope`
    # -> `ensure_identity`), so the fast path must not skip it.
    path = store.scenes.paths._scene_path(cid, sid)
    meta, text = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.pop("identity", None)
    path.write_text(frontmatter.dump_frontmatter(meta, text), encoding="utf-8")
    assert store.scenes.scene_identity(cid, sid) is None
    calls = []
    real = store.responses.migrate
    monkeypatch.setattr(store.responses, "migrate",
                        lambda c, s: calls.append((c, s)) or real(c, s))
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").status_code == 200
    assert calls == [(cid, sid)]
    assert store.scenes.scene_identity(cid, sid)


def test_reroll_takes_the_same_fast_path(client, monkeypatch):
    """`post_regenerate` migrated unconditionally too, right before reading the
    scene it then rerolls from."""
    cid, sid = _legacy_scene()
    client.get(f"/api/campaigns/{cid}/scenes/{sid}")   # the one real migration
    calls = []
    monkeypatch.setattr(store.responses, "migrate", lambda c, s: calls.append((c, s)))
    from grimoire.routes import character_turns
    seen = []
    monkeypatch.setattr(character_turns, "regenerate_response",
                        lambda cid, sid, rid, *rest: seen.append(rid) or {"ok": True})
    reply = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate", json={})
    assert reply.status_code == 200, reply.text
    assert calls == []
    assert seen == [store.scenes.read_scene(cid, sid)["messages"][1]["response_id"]]


def test_the_ledger_is_written_compact_and_still_reads_indented(tmp_path, monkeypatch):
    """Compact on write: the ledger is rewritten whole several times a turn, and
    `indent` both inflates it and pushes `json.dumps` onto the pure-Python
    encoder. Older builds wrote it indented, and that must still read."""
    import json

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "assistant", "Salt — and tide.", speaker="Mara")
    rid = store.responses.migrate(cid, sid)[0]["id"]
    path = store.responses._path(cid)
    raw = path.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))
    assert "—" in raw          # ensure_ascii stays off: prose is not \u-escaped
    path.write_text(json.dumps(json.loads(raw), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    assert store.responses.get(cid, sid, rid)["content"] == "Salt — and tide."
