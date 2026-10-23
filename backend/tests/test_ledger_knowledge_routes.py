"""The Knowledge ledger's routes (roadmap 11 §7.4, 11-S4).

Hand edits to `knowledge.json` through `routes/ledger.py`: the server
resolves the scene to its identity and fingerprints the subject itself, a
grant is only stored about text the client was shown (`expect`), what cannot
be represented is refused, and every write is journalled as `manual` so Undo
reverses it.
"""

from __future__ import annotations

import json
import uuid

import pytest

from grimoire import store
from grimoire.store.scenes import identity as scenes_identity


def _hex() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def world(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    aid, _vid = store.overlay.create_character(cid, "Mara")
    sid = store.scenes.create_scene(cid, "Pier")
    pid = _hex()
    store.scenes.append_message(cid, sid, "user", "Seraphine waits.", post_id=pid)
    rnd = store.responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                                    run_id="test")
    record = store.responses.prepare(cid, sid, rnd["id"], f"characters:{aid}", "Mara", {})
    store.responses.save_variant(cid, sid, record["id"], "Mara whispers the code.", "complete")
    return {"cid": cid, "aid": aid, "sid": sid, "pid": pid, "rid": record["id"],
            "base": f"/api/campaigns/{cid}/ledger/knowledge"}


def _shown(client, w, sid=None):
    r = client.get(f"{w['base']}/scenes/{sid or w['sid']}")
    assert r.status_code == 200, r.text
    return r.json()


def _expect(client, w, key):
    post = next(p for p in _shown(client, w)["posts"] if p["key"] == key)
    return [{k: post[k] for k in ("key", "part", "variant", "digest", "shown")}]


def _post(client, w, **body):
    key = f"r-{w['rid']}"
    payload = {"audience": f"characters:{w['aid']}", "status": "knows", "scene": w["sid"],
               "posts": [{"key": key, "part": ""}], "note": "Told on the walk.", **body}
    if "expect" not in body:
        payload["expect"] = _expect(client, w, key)
    return client.post(w["base"], json=payload)


def _whole(client, w, sid=None, **body):
    shown = _shown(client, w, sid)
    return _post(client, w, posts=None, whole_scene=True, expect_digest=shown["digest"],
                 expect_shown=shown["shown"], **{"scene": sid or w["sid"], **body})


def _entries(client, w):
    r = client.get(w["base"])
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def _index(w):
    return next(i for i, m in enumerate(store.scenes.read_scene(w["cid"], w["sid"])["messages"])
                if m.get("response_id") == w["rid"])


def test_the_scene_read_offers_keyed_posts_with_their_fingerprints(client, world):
    shown = _shown(client, world)
    assert [p["key"] for p in shown["posts"]] == [f"p-{world['pid']}", f"r-{world['rid']}"]
    assert all(len(p["digest"]) == 16 for p in shown["posts"]) and len(shown["digest"]) == 64
    assert shown["posts"][1]["text"] == "Mara whispers the code."


def test_a_statement_about_a_post_is_fingerprinted_by_the_server(client, world):
    r = _post(client, world)
    assert r.status_code == 200, r.text
    stored = store.knowledge.get(world["cid"], r.json()["id"])
    post = stored["subject"]["posts"][0]
    assert post["key"] == f"r-{world['rid']}" and len(post["digest"]) == 16 and post["variant"]
    assert stored["audience_name"] == "Mara" and stored["source"] == "manual"
    [row] = _entries(client, world)
    assert row["scene"]["sid"] == world["sid"] and row["scene"]["title"] == "Pier"
    assert row["scene"]["digest"] == _shown(client, world)["digest"]
    assert row["posts"][0]["digest"] == post["digest"]
    assert row["posts"][0]["text"] == "Mara whispers the code."
    assert not row["stale"] and row["audience_now"] == "Mara"


def test_a_whole_scene_statement_and_an_accepted_source(client, world):
    r = _whole(client, world, source="accepted", audience="*")
    assert r.status_code == 200, r.text
    stored = store.knowledge.get(world["cid"], r.json()["id"])
    assert stored["subject"]["kind"] == "scene" and stored["source"] == "accepted"
    assert stored["audience_name"] == ""


def test_a_grant_about_text_that_moved_since_it_was_shown_is_refused(client, world):
    expect = _expect(client, world, f"r-{world['rid']}")
    digest = _shown(client, world)["digest"]
    store.responses.save_variant(world["cid"], world["sid"], world["rid"], "Mara lies.",
                                 "complete")
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "moved"
    r = _post(client, world, posts=None, whole_scene=True, expect_digest=digest)
    assert r.status_code == 409 and r.json()["kind"] == "moved"
    # A withholding needs no such care.
    assert _post(client, world, status="unaware", expect=expect).status_code == 200


def test_a_grant_with_no_expect_is_refused(client, world):
    r = _post(client, world, expect=None)
    assert r.status_code == 409 and r.json()["kind"] == "moved"


def test_leaving_out_posts_is_not_the_whole_scene(client, world):
    r = _post(client, world, posts=None)
    assert r.status_code == 400 and r.json()["kind"] == "bad_subject"


def test_a_legacy_scene_is_given_an_identity_and_a_refusal_writes_none(client, world):
    sid = store.scenes.create_scene(world["cid"], "Old")
    store.scenes.append_message(world["cid"], sid, "user", "Long ago.", post_id=_hex())
    path = store.scenes.paths._scene_path(world["cid"], sid)
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                              if not line.startswith("identity:")) + "\n", encoding="utf-8")
    assert scenes_identity.scene_identity(world["cid"], sid) is None
    refused = _post(client, world, scene=sid, posts=[{"key": "p-" + "0" * 32, "part": ""}])
    assert refused.status_code == 400
    assert scenes_identity.scene_identity(world["cid"], sid) is None
    r = _whole(client, world, sid)
    assert r.status_code == 200, r.text
    token = scenes_identity.scene_identity(world["cid"], sid)
    assert token and store.knowledge.get(world["cid"], r.json()["id"])["subject"]["scene"] == token


@pytest.mark.parametrize("change,status", [
    ({"posts": [{"key": "r-" + "0" * 32, "part": ""}]}, 400),
    ({"audience": "characters:nobody"}, 400),
    ({"audience": "groups:nowhere"}, 400),
    ({"audience": "pcs:winifred"}, 400),
    ({"status": "maybe"}, 400),
    ({"source": "model"}, 400),
    ({"whole_scene": True}, 400),
    ({"scene": "0001-gone"}, 404),
])
def test_refusals(client, world, change, status):
    assert _post(client, world, **change).status_code == status
    assert store.knowledge.read(world["cid"]) == {}


def test_a_grant_about_a_keyless_post_is_unkeyed_and_an_unaware_goes_whole_scene(client, world):
    r = _post(client, world, posts=[{"key": "", "part": ""}])
    assert r.status_code == 400 and r.json()["kind"] == "unkeyed"
    r = _post(client, world, posts=[{"key": "", "part": ""}], status="unaware")
    assert r.status_code == 200, r.text
    kid = r.json()["id"]
    assert store.knowledge.get(world["cid"], kid)["subject"]["kind"] == "scene"
    # And it cannot become the grant `unkeyed` refused by a status flip.
    r = client.put(world["base"] + f"/{kid}", json={"status": "knows"})
    assert r.status_code == 400 and r.json()["kind"] == "bad_subject"
    assert store.knowledge.get(world["cid"], kid)["status"] == "unaware"


def test_a_grant_about_a_hidden_post_is_refused(client, world):
    store.scenes.set_excluded(world["cid"], world["sid"], _index(world), True)
    r = _post(client, world)
    assert r.status_code == 400 and r.json()["kind"] == "hidden"


def test_an_unreadable_scan_marks_the_entry_not_the_list(client, world, monkeypatch):
    _post(client, world)

    def unreadable(cid, identity):
        raise scenes_identity.UnreadableError("mid-sync")
    monkeypatch.setattr(scenes_identity, "find_by_identity", unreadable)
    [row] = _entries(client, world)
    assert row["unverifiable"] and row["scene"] is None and row["stale"]


def test_an_unreadable_scene_reads_unverifiable_not_gone(client, world, monkeypatch):
    _post(client, world)

    def unreadable(cid, identity):
        raise scenes_identity.UnreadableError("mid-sync")
    monkeypatch.setattr(scenes_identity, "find_by_identity", unreadable)
    assert _entries(client, world)[0]["stale_reason"] == "unverifiable"


def test_a_write_against_an_unreadable_scene_is_busy(client, world, monkeypatch):
    expect = _expect(client, world, f"r-{world['rid']}")

    def unreadable(cid, sid):
        raise scenes_identity.UnreadableError("mid-sync")
    monkeypatch.setattr(scenes_identity, "scene_identity", unreadable)
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "busy"
    assert client.get(f"{world['base']}/scenes/{world['sid']}").status_code == 409
    assert store.knowledge.read(world["cid"]) == {}


def test_a_restamp_cannot_turn_an_unaware_into_a_grant(client, world):
    kid = _post(client, world, posts=[{"key": "", "part": ""}], status="unaware").json()["id"]
    r = client.put(world["base"] + f"/{kid}", json={
        "status": "knows", "restamp": True, "expect_digest": _shown(client, world)["digest"]})
    assert r.status_code == 400 and r.json()["kind"] == "bad_subject"
    assert store.knowledge.get(world["cid"], kid)["status"] == "unaware"


def test_a_garbled_file_refuses_a_branch_as_a_conflict(client, world):
    store.knowledge._path(world["cid"]).write_text("{not json", encoding="utf-8")
    r = client.post(f"/api/campaigns/{world['cid']}/scenes/{world['sid']}/branch",
                    json={"through": 0})
    assert r.status_code == 409


def test_unknown_ids_are_404(client, world):
    assert client.put(world["base"] + "/k-00000000", json={"note": "x"}).status_code == 404
    assert client.delete(world["base"] + "/k-00000000").status_code == 404
    assert client.get("/api/campaigns/nowhere/ledger/knowledge").status_code == 404


def test_an_edit_shows_stale_and_only_a_restamp_against_what_was_shown_confirms(client, world):
    kid = _post(client, world).json()["id"]
    store.scenes.edit_message(world["cid"], world["sid"], _index(world), "Mara whispers a lie.")
    row = _entries(client, world)[0]
    assert row["stale"] and row["stale_reason"] == "changed"
    # A note edit keeps the stored fingerprint: still stale.
    assert client.put(world["base"] + f"/{kid}", json={"note": "Still true?"}).status_code == 200
    row = _entries(client, world)[0]
    assert row["stale"] and row["note"] == "Still true?"
    assert row["posts"][0]["text"] == "Mara whispers a lie."
    # A restamp without what was shown is refused; with it, it confirms.
    r = client.put(world["base"] + f"/{kid}", json={"restamp": True})
    assert r.status_code == 409 and r.json()["kind"] == "moved"
    expect = _expect(client, world, f"r-{world['rid']}")
    assert client.put(world["base"] + f"/{kid}",
                      json={"restamp": True, "expect": expect}).status_code == 200
    assert not _entries(client, world)[0]["stale"]


def test_a_hidden_post_reads_stale_as_hidden(client, world):
    _post(client, world)
    store.scenes.set_excluded(world["cid"], world["sid"], _index(world), True)
    assert _entries(client, world)[0]["stale_reason"] == "hidden"


def test_a_renamed_audience_reads_stale(client, world):
    _post(client, world)
    r = client.put(f"/api/campaigns/{world['cid']}/characters/{world['aid']}/name",
                   json={"name": "Mara Vance"})
    assert r.status_code == 200, r.text
    row = _entries(client, world)[0]
    assert row["audience_now"] == "Mara Vance" and row["stale_reason"] == "audience"


def test_a_deleted_scene_is_listed_and_restamp_says_gone(client, world):
    kid = _post(client, world).json()["id"]
    store.scenes.delete_scene(world["cid"], world["sid"])
    row = _entries(client, world)[0]
    assert row["scene"] is None and row["stale_reason"] == "gone" and row["posts"][0]["missing"]
    r = client.put(world["base"] + f"/{kid}", json={"restamp": True})
    assert r.status_code == 409 and r.json()["kind"] == "gone"


def test_a_new_audience_records_its_name_and_dates_the_statement_now(client, world):
    kid = _post(client, world).json()["id"]
    doc = store.knowledge._load_strict(world["cid"])
    doc["entries"][kid]["created"] = "2020-01-01T00:00:00Z"
    store.knowledge._write(world["cid"], doc)
    assert client.put(world["base"] + f"/{kid}", json={"note": "x"}).status_code == 200
    assert store.knowledge.get(world["cid"], kid)["created"] == "2020-01-01T00:00:00Z"
    assert client.put(world["base"] + f"/{kid}", json={"audience": "*"}).status_code == 200
    stored = store.knowledge.get(world["cid"], kid)
    assert stored["audience"] == "*" and stored["audience_name"] == ""
    assert stored["created"] > "2020-01-01T00:00:00Z"


def test_every_write_journals_as_manual_and_undo_reverses_it(client, world):
    cid = world["cid"]
    kid = _post(client, world).json()["id"]
    created = store.journal.read(cid)[-1]
    assert created["source"] == "manual" and created["kind"] == "knowledge"
    assert created["after"] == "Mara: knows — Told on the walk."
    held = store.knowledge.get(cid, kid)

    assert client.put(world["base"] + f"/{kid}", json={"status": "suspects"}).status_code == 200
    edited = store.journal.read(cid)[-1]
    assert client.post(f"/api/campaigns/{cid}/journal/{edited['id']}/undo").status_code == 200
    assert store.knowledge.get(cid, kid)["status"] == "knows"

    assert client.delete(world["base"] + f"/{kid}").status_code == 200
    removed = store.journal.read(cid)[-1]
    assert store.knowledge.get(cid, kid) is None
    store.undo.undo(cid, removed["id"])
    assert store.knowledge.get(cid, kid)["subject"] == held["subject"]

    store.undo.undo(cid, created["id"])
    assert store.knowledge.get(cid, kid) is None


def test_an_orphaned_unaware_reads_gone(client, world):
    _post(client, world, status="unaware")
    store.scenes.delete_scene(world["cid"], world["sid"])
    row = _entries(client, world)[0]
    assert row["stale"] and row["stale_reason"] == "gone"


def test_a_garbled_response_ledger_marks_the_entry_not_the_list(client, world):
    _post(client, world)
    store.responses._path(world["cid"]).write_text("[]", encoding="utf-8")
    [row] = _entries(client, world)
    assert row["unverifiable"] and row["stale_reason"] == "unverifiable"
    r = client.get(f"{world['base']}/scenes/{world['sid']}")
    assert r.status_code == 409 and r.json()["kind"] == "malformed"


def test_an_empty_edit_writes_nothing(client, world):
    kid = _post(client, world).json()["id"]
    before = store.knowledge._path(world["cid"]).read_text(encoding="utf-8")
    rows = len(store.journal.read(world["cid"]))
    assert client.put(world["base"] + f"/{kid}", json={}).status_code == 200
    assert store.knowledge._path(world["cid"]).read_text(encoding="utf-8") == before
    assert len(store.journal.read(world["cid"])) == rows


def test_a_repeated_post_is_refused_before_any_identity_is_written(client, world):
    sid = store.scenes.create_scene(world["cid"], "Old")
    pid = _hex()
    store.scenes.append_message(world["cid"], sid, "user", "Long ago.", post_id=pid)
    path = store.scenes.paths._scene_path(world["cid"], sid)
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                              if not line.startswith("identity:")) + "\n", encoding="utf-8")
    twice = [{"key": f"p-{pid}", "part": ""}] * 2
    r = _post(client, world, scene=sid, posts=twice, status="unaware")
    assert r.status_code == 400 and r.json()["kind"] == "bad_subject"
    assert scenes_identity.scene_identity(world["cid"], sid) is None


def test_an_unreadable_transcript_is_busy_not_a_500(client, world, monkeypatch):
    expect = _expect(client, world, f"r-{world['rid']}")

    def unreadable(cid, sid):
        raise OSError("mid-sync")
    monkeypatch.setattr(store.scenes, "read_scene", unreadable)
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "busy"


def test_an_unparseable_chronicle_is_malformed_not_a_500(client, world):
    expect = _expect(client, world, f"r-{world['rid']}")
    store.chronicle._chronicle_path(world["cid"]).write_text("{not json", encoding="utf-8")
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "malformed"
    assert client.get(f"{world['base']}/scenes/{world['sid']}").status_code == 409
    assert store.knowledge.read(world["cid"]) == {}


def test_an_unreadable_chronicle_is_busy_not_a_500(client, world, monkeypatch):
    expect = _expect(client, world, f"r-{world['rid']}")

    def unreadable(cid, sid):
        raise OSError("mid-sync")
    monkeypatch.setattr(store.chronicle, "get_record", unreadable)
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "busy"


def test_a_partly_cut_unaware_still_applies_but_a_grant_is_gone(client, world):
    cid, sid = world["cid"], world["sid"]
    rnd = store.responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                                    run_id="test")
    later = store.responses.prepare(cid, sid, rnd["id"], f"characters:{world['aid']}", "Mara", {})
    store.responses.save_variant(cid, sid, later["id"], "Mara leaves.", "complete")
    keys = [f"r-{world['rid']}", f"r-{later['id']}"]
    shown = {p["key"]: p for p in _shown(client, world)["posts"]}
    expect = [{k: shown[key][k] for k in ("key", "part", "variant", "digest", "shown")}
              for key in keys]
    posts = [{"key": k, "part": ""} for k in keys]
    _post(client, world, posts=posts, expect=expect, status="unaware")
    _post(client, world, posts=posts, expect=expect, status="knows")
    last = next(i for i, m in enumerate(store.scenes.read_scene(cid, sid)["messages"])
                if m.get("response_id") == later["id"])
    store.scenes.delete_from(cid, sid, last)
    rows = {r["status"]: r for r in _entries(client, world)}
    assert not rows["unaware"]["stale"]
    assert rows["knows"]["stale_reason"] == "gone"


def test_a_whole_scene_grant_stops_when_the_title_or_chronicle_changes(client, world):
    cid = world["cid"]
    kid = _whole(client, world).json()["id"]
    assert not _entries(client, world)[0]["stale"]
    # An absorb writing the chronicle summary the grant would expose.
    store.chronicle.restore(cid, world["sid"], {"id": world["sid"], "summary": "Mara knew.",
                                                "one_line": "Mara knew."})
    assert _entries(client, world)[0]["stale_reason"] == "changed"
    store.chronicle.restore(cid, world["sid"], None)
    assert not _entries(client, world)[0]["stale"]
    # A rename changes the title it would expose.
    store.scenes.rename_scene(cid, world["sid"], "The Long Pier")
    row = next(r for r in _entries(client, world) if r["id"] == kid)
    assert row["stale"] and row["stale_reason"] == "changed"


def test_the_list_parses_the_response_ledger_once(client, world, monkeypatch):
    for title in ("Dock", "Harbour", "Quay"):
        sid = store.scenes.create_scene(world["cid"], title)
        store.scenes.append_message(world["cid"], sid, "user", f"{title}.", post_id=_hex())
        assert _whole(client, world, sid).status_code == 200
    calls = []
    real = store.responses._read
    monkeypatch.setattr(store.responses, "_read", lambda cid: calls.append(cid) or real(cid))
    assert len(_entries(client, world)) == 3
    assert len(calls) == 1


def test_a_garbled_ledger_refuses_before_a_legacy_scene_gets_an_identity(client, world):
    sid = store.scenes.create_scene(world["cid"], "Old")
    pid = _hex()
    store.scenes.append_message(world["cid"], sid, "user", "Long ago.", post_id=pid)
    path = store.scenes.paths._scene_path(world["cid"], sid)
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                              if not line.startswith("identity:")) + "\n", encoding="utf-8")
    store.knowledge._path(world["cid"]).write_text("{not json", encoding="utf-8")
    r = _post(client, world, scene=sid, posts=[{"key": f"p-{pid}", "part": ""}],
              status="unaware")
    assert r.status_code == 409 and r.json()["kind"] == "malformed"
    assert scenes_identity.scene_identity(world["cid"], sid) is None


def test_a_damaged_response_scope_marks_entries_not_the_list(client, world):
    _post(client, world)
    token = scenes_identity.scene_identity(world["cid"], world["sid"])
    store.responses._path(world["cid"]).write_text(
        json.dumps({"scenes": {token: {"responses": []}}}), encoding="utf-8")
    [row] = _entries(client, world)
    assert row["unverifiable"]


def test_a_renamed_audience_under_an_open_form_is_refused(client, world):
    r = _post(client, world, audience_name="Mara")
    assert r.status_code == 200, r.text
    r = _post(client, world, audience_name="Seraphine")
    assert r.status_code == 409 and r.json()["kind"] == "moved"


def test_a_grant_binds_to_the_prompt_text_it_showed(client, world):
    expect = _expect(client, world, f"r-{world['rid']}")
    expect[0]["shown"] = store.knowledge.digest("something the form never showed")
    r = _post(client, world, expect=expect)
    assert r.status_code == 409 and r.json()["kind"] == "moved"


def test_the_scene_read_shows_everything_a_whole_scene_grant_exposes(client, world):
    messages = store.scenes.read_scene(world["cid"], world["sid"])["messages"]
    store.scenes.replace_messages(world["cid"], world["sid"],
                                 [*messages, {"role": "user", "content": "An old keyless line."}])
    store.chronicle.restore(world["cid"], world["sid"],
                            {"id": world["sid"], "summary": "Mara learned the code.",
                             "one_line": "The code."})
    shown = _shown(client, world)
    assert shown["title"] == "Pier" and shown["summary"] == "Mara learned the code."
    assert shown["one_line"] == "The code."
    assert [p["text"] for p in shown["keyless_posts"]] == ["An old keyless line."]
    assert _whole(client, world).status_code == 200
