"""The Knowledge ledger's routes (roadmap 11 §7.4, 11-S4).

Hand edits to `knowledge.json` through `routes/ledger.py`: the server
resolves the scene to its identity and fingerprints the subject itself, a
grant is only stored about text the client was shown (`expect`), what cannot
be represented is refused, and every write is journalled as `manual` so Undo
reverses it.
"""

from __future__ import annotations

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
    return [{k: post[k] for k in ("key", "part", "variant", "digest")}]


def _post(client, w, **body):
    key = f"r-{w['rid']}"
    payload = {"audience": f"characters:{w['aid']}", "status": "knows", "scene": w["sid"],
               "posts": [{"key": key, "part": ""}], "expect": _expect(client, w, key),
               "note": "Told on the walk.", **body}
    return client.post(w["base"], json=payload)


def _whole(client, w, **body):
    return _post(client, w, posts=None, whole_scene=True,
                 expect_digest=_shown(client, w)["digest"], **body)


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
    r = _post(client, world, scene=sid, posts=None, whole_scene=True,
              expect_digest=_shown(client, world, sid)["digest"])
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
