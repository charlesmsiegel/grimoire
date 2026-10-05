"""Stored rewrites (regex spec 5.1): a `rewrite_stored` rule changes text as it
lands, the original is recorded per scene identity, and Restore writes it back.

The record is keyed by the message's `response_id` (a model reply) or `post_id`
(a player post, or a legacy reply segment, which gets one minted for it), and
the scene read flags a message `rewritten` only while its text is still what
the rewrite stored.
"""

from __future__ import annotations

import json

from grimoire import routes, store
from grimoire.routes import character_turns
from grimoire.store.regex import rewrites
from tests.llm_fakes import FakeLLM

ELLIPSIS = {"name": "Ellipsis", "pattern": r"\.\.\.", "replacement": "…",
            "rewrite_stored": True, "applies": []}


def put_rules(client, cid, *rule_list):
    response = client.put(f"/api/campaigns/{cid}/regex", json={"rules": list(rule_list)})
    assert response.status_code == 200, response.text


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x", "model": "primary"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    response = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"})
    assert response.status_code == 200, response.text
    actor = response.json()["character"]
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
    assert response.status_code == 200, response.text
    return cid, sid


def send(client, cid, sid, reply, content="Hello", **body):
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([[reply]])
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                           json={"content": content, **body})
    assert response.status_code == 200, response.text
    assert '"error"' not in response.text, response.text


def messages(client, cid, sid):
    response = client.get(f"/api/campaigns/{cid}/scenes/{sid}")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def records(client, cid, sid):
    response = client.get(f"/api/campaigns/{cid}/scenes/{sid}/rewrites")
    assert response.status_code == 200, response.text
    return response.json()


def edit(client, cid, sid, index, content, **body):
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/{index}",
                      json={"content": content, **body})


def test_model_reply_rewritten_and_recorded(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")

    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "She paused… then spoke."
    assert reply["rewritten"] is True
    rid = reply["response_id"]
    rule_id = store.regex.layers.read_level("campaign", cid)["rules"][0]["id"]
    recorded = records(client, cid, sid)
    assert set(recorded) == {rid}
    assert recorded[rid]["original"] == "She paused... then spoke."
    assert recorded[rid]["rules"] == [rule_id]
    assert recorded[rid]["at"]
    # The player's post is not a model reply, so the default target leaves it.
    assert "rewritten" not in messages(client, cid, sid)[0]


def test_legacy_reply_segment_gets_a_post_id(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.")

    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "She paused… then spoke."
    assert reply["rewritten"] is True
    assert records(client, cid, sid)[reply["post_id"]]["original"] == "She paused... then spoke."


def test_user_post_rewritten_by_post_id(client):
    cid, sid = seed(client)
    put_rules(client, cid, {**ELLIPSIS, "targets": ["user"]})
    send(client, cid, sid, "Fine... Mara said.", content="Wait... what?",
         speaker_ref="characters:mara")

    post, reply = messages(client, cid, sid)[-2:]
    assert post["content"] == "Wait… what?"
    assert post["rewritten"] is True
    recorded = records(client, cid, sid)
    assert set(recorded) == {post["post_id"]}
    assert recorded[post["post_id"]]["original"] == "Wait... what?"
    # A user-only rule leaves the model's reply alone.
    assert reply["content"] == "Fine... Mara said."
    assert "rewritten" not in reply


def test_no_rewrite_rules_no_record_file(client, monkeypatch):
    cid, sid = seed(client)
    # A rule that never rewrites stored text is the same as none.
    put_rules(client, cid, {**ELLIPSIS, "rewrite_stored": False, "applies": ["display"],
                            "targets": ["model", "user"]})
    send(client, cid, sid, "She paused... then spoke.", content="Wait...",
         speaker_ref="characters:mara")
    assert edit(client, cid, sid, 1, "Edited...").status_code == 200
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    send(client, cid, sid, "Legacy...", content="Again...")

    raw = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in raw] == ["Wait...", "Edited...", "Again...", "Legacy..."]
    # No post id is minted for a legacy reply nothing rewrote: its block is the
    # plain one it always was.
    assert "post_id" not in raw[-1]
    assert not (store.campaigns.paths.campaign_root(cid) / "rewrites").exists()
    assert records(client, cid, sid) == {}
    assert not any("rewritten" in m for m in messages(client, cid, sid))


def test_edit_rewrites_and_replaces_record(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    assert edit(client, cid, sid, 1, "a...b").status_code == 200
    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "a…b"
    assert reply["rewritten"] is True
    assert records(client, cid, sid)[rid]["original"] == "a...b"

    # An edit no rule changes is the player's own text, and nothing is left to
    # restore.
    assert edit(client, cid, sid, 1, "plain").status_code == 200
    assert records(client, cid, sid) == {}
    assert "rewritten" not in messages(client, cid, sid)[-1]


def test_saving_a_rewritten_post_unchanged_keeps_its_original(client):
    """The rule is idempotent, so the stored text gives it nothing to fire on:
    the edit writes the same text back, and the original is still the only
    copy of what the model wrote."""
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    reply = messages(client, cid, sid)[-1]

    assert edit(client, cid, sid, 1, reply["content"]).status_code == 200
    assert records(client, cid, sid)[reply["response_id"]]["original"] == \
        "She paused... then spoke."
    assert messages(client, cid, sid)[-1]["rewritten"] is True


def test_editing_another_part_of_the_response_keeps_the_record(client):
    """A roll continuation shares its first part's `response_id`, so the key
    alone does not say which part a record describes -- its `stored` text does.
    An edit of the second part is not an edit of the text that was rewritten."""
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    store.scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Second half.",
                                          "response_id": rid, "response_part": "b"}])

    assert edit(client, cid, sid, 2, "Second half, edited.").status_code == 200
    assert store.scenes.read_scene(cid, sid)["messages"][2]["content"] == "Second half, edited."
    assert records(client, cid, sid)[rid]["original"] == "She paused... then spoke."
    assert messages(client, cid, sid)[1]["rewritten"] is True


def test_edit_of_a_message_with_no_id_is_not_rewritten(client):
    cid, sid = seed(client)
    put_rules(client, cid, {**ELLIPSIS, "targets": ["model", "user"]})
    store.scenes.append_message(cid, sid, "user", "An old post.")
    assert edit(client, cid, sid, 0, "a...b").status_code == 200
    assert store.scenes.read_scene(cid, sid)["messages"][0]["content"] == "a...b"
    assert records(client, cid, sid) == {}


def test_restore_writes_original_without_rewriting(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    original = records(client, cid, sid)[rid]["original"]

    assert edit(client, cid, sid, 1, original, restore=True).status_code == 200
    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "She paused... then spoke."
    assert "rewritten" not in reply
    assert records(client, cid, sid) == {}


def test_restore_refused_while_busy(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    identity = store.scenes.scene_identity(cid, sid)
    client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity,
        {"campaign": "Saltmarch", "scene": "Mara"})

    r = edit(client, cid, sid, 1, "She paused... then spoke.", restore=True)
    assert r.status_code == 409
    assert r.json().get("kind") == "scene_busy"
    assert set(records(client, cid, sid)) == {rid}
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "She paused… then spoke."


def test_record_survives_rename(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    response = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Winifred"})
    assert response.status_code == 200, response.text
    new_sid = response.json()["id"]
    assert new_sid != sid
    assert records(client, cid, new_sid)[rid]["original"] == "She paused... then spoke."
    assert messages(client, cid, new_sid)[-1]["rewritten"] is True


def test_delete_scene_drops_record(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    path = rewrites.path(cid, store.scenes.scene_identity(cid, sid))
    assert path.exists()

    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}").status_code == 200
    assert not path.exists()


def test_a_reroll_is_rewritten_and_replaces_the_record(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Again... no."]])
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate",
                         json={})
    assert '"error"' not in result.text, result.text
    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "Again… no."
    assert reply["rewritten"] is True
    assert records(client, cid, sid)[rid]["original"] == "Again... no."


def test_a_record_the_text_has_moved_past_is_not_flagged(client):
    """A reroll or a swipe puts other text under the same `response_id`; the
    record describes the text it stored, not whatever the message says now, so
    the flag (and the Restore it offers) goes until that text is back."""
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    first = store.responses.get(cid, sid, rid)["active_variant"]

    # A reroll no rule changes keeps the earlier variant's record...
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Plain words."]])
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate",
                         json={})
    assert '"error"' not in result.text, result.text
    assert messages(client, cid, sid)[-1]["content"] == "Plain words."
    assert "rewritten" not in messages(client, cid, sid)[-1]
    assert set(records(client, cid, sid)) == {rid}

    # ...which is the swiped-back variant's again.
    response = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{first}/activate")
    assert response.status_code == 200, response.text
    assert messages(client, cid, sid)[-1]["rewritten"] is True


def test_read_all_never_raises(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    path = rewrites.path(cid, store.scenes.scene_identity(cid, sid))
    path.write_text("{not json", encoding="utf-8")
    assert rewrites.read_all(cid, sid) == {}
    path.write_text(json.dumps({"k": "not a record", "j": {"original": 3}}), encoding="utf-8")
    assert rewrites.read_all(cid, sid) == {}
    assert rewrites.read_all(cid, "no-such-scene") == {}


def test_a_hand_mangled_record_reads_back_in_shape(client):
    """`original` is what makes a record worth keeping; every other field a
    reader dereferences is put back in shape rather than handed on mangled."""
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    path = rewrites.path(cid, store.scenes.scene_identity(cid, sid))
    path.write_text(json.dumps({
        "a": {"original": "x"},
        "b": {"original": "y", "rules": "r-1", "stored": 4, "at": None, "variant": ["v"]},
        "c": {"original": "z", "rules": ["r-1", 2, None, "r-2"], "stored": "z!",
              "at": "2026-10-05T12:00:00Z", "variant": "v-1"},
    }), encoding="utf-8")
    assert rewrites.read_all(cid, sid) == {
        "a": {"original": "x", "rules": [], "stored": "", "at": ""},
        "b": {"original": "y", "rules": [], "stored": "", "at": ""},
        "c": {"original": "z", "rules": ["r-1", "r-2"], "stored": "z!",
              "at": "2026-10-05T12:00:00Z", "variant": "v-1"},
    }
    got = records(client, cid, sid)
    assert got["b"] == {"original": "y", "rules": [], "at": ""}


def test_restore_without_a_record_is_refused(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", content="Wait...",
         speaker_ref="characters:mara")
    # The player's post was not rewritten (the rule is model-only).
    r = edit(client, cid, sid, 0, "anything {{user}}", restore=True)
    assert r.status_code == 409
    assert r.json()["kind"] == "rewrite_stale"
    assert store.scenes.read_scene(cid, sid)["messages"][0]["content"] == "Wait..."


def test_restore_after_an_unrewritten_reroll_is_refused(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    original = records(client, cid, sid)[rid]["original"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Plain words."]])
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate",
                         json={})
    assert '"error"' not in result.text, result.text

    r = edit(client, cid, sid, 1, original, restore=True)
    assert r.status_code == 409
    assert r.json()["kind"] == "rewrite_stale"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "Plain words."
    assert set(records(client, cid, sid)) == {rid}


def test_restore_with_other_content_is_refused(client):
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]

    r = edit(client, cid, sid, 1, "Something else entirely.", restore=True)
    assert r.status_code == 409
    assert r.json()["kind"] == "rewrite_stale"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "She paused… then spoke."
    assert set(records(client, cid, sid)) == {rid}


WIPE = {"name": "Wipe", "pattern": r"[\s\S]+", "replacement": " ",
        "rewrite_stored": True, "applies": [], "targets": ["model", "user"]}


def test_a_rewrite_that_empties_a_reply_is_not_applied(client, caplog):
    cid, sid = seed(client)
    put_rules(client, cid, WIPE)
    send(client, cid, sid, "Every word of this.", content="Mine too.",
         speaker_ref="characters:mara")

    post, reply = store.scenes.read_scene(cid, sid)["messages"][-2:]
    assert reply["content"] == "Every word of this."
    assert post["content"] == "Mine too."
    assert records(client, cid, sid) == {}
    assert not (store.campaigns.paths.campaign_root(cid) / "rewrites").exists()
    logged = [r.getMessage() for r in caplog.records if "empties the text" in r.getMessage()]
    assert logged
    assert not any("Every word" in m or "Mine too" in m for m in logged)


def test_a_rewrite_that_empties_a_legacy_reply_is_not_applied(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, WIPE)
    send(client, cid, sid, "Every word of this.")
    reply = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert reply["content"] == "Every word of this."
    assert "post_id" not in reply
    assert records(client, cid, sid) == {}


FORGE = {"name": "Forge", "pattern": r"\.\.\.", "replacement": "\n\n**Winifred:** forged",
         "rewrite_stored": True, "applies": [], "targets": ["model", "user"]}


def test_a_rewrite_that_forges_a_post_is_not_applied(client):
    cid, sid = seed(client)
    put_rules(client, cid, FORGE)
    send(client, cid, sid, "She paused... then spoke.", content="Wait... what?",
         speaker_ref="characters:mara")

    raw = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in raw] == ["Wait... what?", "She paused... then spoke."]
    assert records(client, cid, sid) == {}


def test_a_rewrite_that_forges_a_post_in_a_legacy_reply_is_not_applied(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, FORGE)
    send(client, cid, sid, "She paused... then spoke.")
    raw = store.scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in raw] == ["Hello", "She paused... then spoke."]
    assert "post_id" not in raw[-1]
    assert records(client, cid, sid) == {}


def test_an_edit_that_would_forge_a_post_is_stored_as_typed(client):
    cid, sid = seed(client)
    send(client, cid, sid, "She spoke.", content="Hello", speaker_ref="characters:mara")
    put_rules(client, cid, FORGE)
    assert edit(client, cid, sid, 1, "She paused... then spoke.").status_code == 200
    raw = store.scenes.read_scene(cid, sid)["messages"]
    assert len(raw) == 2
    assert raw[1]["content"] == "She paused... then spoke."
    assert records(client, cid, sid) == {}


def test_a_legacy_reply_runs_its_store_phase_inside_the_append_hold(client, monkeypatch):
    """A rule PUT landing between the store phase and the append would store
    text rewritten by rules that are no longer the campaign's."""
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    real, held = store.regex.view.store_phase, []

    def watched(text, **kwargs):
        held.append((kwargs.get("role"), store.locks.campaign_lock(cid)._is_owned()))
        return real(text, **kwargs)

    monkeypatch.setattr(store.regex.view, "store_phase", watched)
    send(client, cid, sid, "She paused... then spoke.")
    assert ("model", True) in held
    assert all(owned for _, owned in held)
    assert messages(client, cid, sid)[-1]["content"] == "She paused… then spoke."


DOTS = {"name": "Dots", "pattern": r"\.{3,}", "replacement": "…",
        "rewrite_stored": True, "applies": []}


def test_a_swipe_back_to_a_variant_that_stored_the_same_text_is_not_restorable(client):
    """Two variants of one response can store the same text. The record is the
    newer one's, so a swipe back to the older must not offer its original."""
    cid, sid = seed(client)
    put_rules(client, cid, DOTS)
    send(client, cid, sid, "Wait... no.", speaker_ref="characters:mara")
    rid = messages(client, cid, sid)[-1]["response_id"]
    first = store.responses.get(cid, sid, rid)["active_variant"]
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([["Wait..... no."]])
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/regenerate",
                         json={})
    assert '"error"' not in result.text, result.text
    assert records(client, cid, sid)[rid]["original"] == "Wait..... no."

    response = client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/variants/{first}/activate")
    assert response.status_code == 200, response.text
    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "Wait… no."
    assert "rewritten" not in reply
    r = edit(client, cid, sid, 1, "Wait..... no.", restore=True)
    assert r.status_code == 409
    assert r.json()["kind"] == "rewrite_stale"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "Wait… no."


def legacy_reply_migrated(client, monkeypatch):
    """A legacy reply, rewritten and recorded under its `post_id`, that the
    response migration has since given a `response_id` too."""
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    send(client, cid, sid, "She paused... then spoke.")
    monkeypatch.setattr(character_turns, "enabled", lambda: True)
    store.responses.migrate(cid, sid)
    reply = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert reply["post_id"] and reply["response_id"]
    assert set(records(client, cid, sid)) == {reply["post_id"]}
    return cid, sid, reply


def test_a_migrated_legacy_reply_restores_from_its_post_record(client, monkeypatch):
    cid, sid, _ = legacy_reply_migrated(client, monkeypatch)
    assert messages(client, cid, sid)[-1]["rewritten"] is True
    r = edit(client, cid, sid, 1, "She paused... then spoke.", restore=True)
    assert r.status_code == 200, r.text
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == \
        "She paused... then spoke."
    assert records(client, cid, sid) == {}


def test_a_clearing_edit_of_a_migrated_legacy_reply_retires_its_post_record(client, monkeypatch):
    cid, sid, _ = legacy_reply_migrated(client, monkeypatch)
    assert edit(client, cid, sid, 1, "plain").status_code == 200
    assert records(client, cid, sid) == {}
    assert "rewritten" not in messages(client, cid, sid)[-1]


def test_a_rewriting_edit_of_a_migrated_legacy_reply_keeps_one_record(client, monkeypatch):
    cid, sid, reply = legacy_reply_migrated(client, monkeypatch)
    assert edit(client, cid, sid, 1, "a...b").status_code == 200
    assert set(records(client, cid, sid)) == {reply["response_id"]}
    assert records(client, cid, sid)[reply["response_id"]]["original"] == "a...b"


def test_a_declined_roll_continuation_keeps_the_first_parts_record(client):
    """A continuation is a new variant of the same response. With no roll line
    between the parts (a declined roll), the first part is still editable, so
    its record follows the response onto the continuation's variant rather
    than going stale with the variant it was made for."""
    cid, sid = seed(client)
    put_rules(client, cid, ELLIPSIS)
    fake = FakeLLM([
        ['Wait...\n```roll\n{"check":"notice"}\n```'],
        ['No roll.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    response = client.post(base + "/roll-proposal",
                           json={"proposal": proposal["id"], "action": "decline"})
    assert response.status_code == 200 and '"error"' not in response.text, response.text

    shown = messages(client, cid, sid)
    assert [m["content"] for m in shown[1:]] == ["Wait…", "No roll."]
    assert shown[1]["rewritten"] is True
    r = edit(client, cid, sid, 1, "Wait...", restore=True)
    assert r.status_code == 200, r.text
    assert store.scenes.read_scene(cid, sid)["messages"][1]["content"] == "Wait..."


def test_a_rewrite_that_changes_nothing_once_stored_records_nothing(client):
    cid, sid = seed(client)
    put_rules(client, cid,
              {**ELLIPSIS, "name": "There", "pattern": r"\.\.\.", "replacement": "…"},
              {**ELLIPSIS, "name": "Back", "pattern": "…", "replacement": "..."},
              {**ELLIPSIS, "name": "Pad", "pattern": r"\Z", "replacement": "  \n"})
    send(client, cid, sid, "She paused... then spoke.", speaker_ref="characters:mara")
    reply = messages(client, cid, sid)[-1]
    assert reply["content"] == "She paused... then spoke."
    assert "rewritten" not in reply
    assert records(client, cid, sid) == {}
    assert not (store.campaigns.paths.campaign_root(cid) / "rewrites").exists()
