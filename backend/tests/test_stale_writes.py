"""A save that would land on top of an external edit is refused (#35).

Covers both flat-markdown record surfaces: entities and greetings.

The store is a folder of markdown files, and CLAUDE.md invites pointing it at a
synced folder — so "the file changed while you had it open" is ordinary here.
Every entity read hands back a `rev`; a save echoing it back gets a 409 instead
of last-writer-wins.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from grimoire import store
from grimoire.main import create_app
from grimoire.store import campaigns, worlds


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return TestClient(create_app())


@pytest.fixture
def wid(client):
    return worlds.create_world("Realm")


@pytest.fixture
def cid(client, wid):
    return campaigns.create_campaign("Saltmarch", wid)


def make_lore(client, wid, body="The first telling."):
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "The Pact", "body": body}).json()["id"]
    return eid


def edit_on_disk(tmp_path, wid, eid, text):
    """What Syncthing (or the user's text editor) does behind the app's back."""
    p = tmp_path / "worlds" / wid / "lore" / f"{eid}.md"
    p.write_text(text, encoding="utf-8")


def test_a_read_carries_the_rev_of_what_it_returned(client, wid):
    eid = make_lore(client, wid)
    got = client.get(f"/api/worlds/{wid}/lore/{eid}").json()
    assert got["rev"] == store.entities.entity_hash(store.home() / "worlds" / wid, "lore", eid)
    assert got["body"].strip() == "The first telling."


def test_saving_with_the_rev_you_read_succeeds(client, wid):
    eid = make_lore(client, wid)
    rev = client.get(f"/api/worlds/{wid}/lore/{eid}").json()["rev"]
    res = client.put(f"/api/worlds/{wid}/lore/{eid}",
                     json={"name": "The Pact", "body": "Mine.", "rev": rev})
    assert res.status_code == 200
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["body"].strip() == "Mine."


def test_an_external_edit_between_read_and_save_is_a_409(client, wid, tmp_path):
    eid = make_lore(client, wid)
    rev = client.get(f"/api/worlds/{wid}/lore/{eid}").json()["rev"]
    edit_on_disk(tmp_path, wid, eid, "---\nname: The Pact\n---\nTheirs.\n")

    res = client.put(f"/api/worlds/{wid}/lore/{eid}",
                     json={"name": "The Pact", "body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert res.json()["kind"] == "stale_record"
    # The other writer's text is still there: a refused write writes nothing.
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["body"].strip() == "Theirs."


def test_the_409_hands_back_the_current_rev_so_a_retry_can_succeed(client, wid, tmp_path):
    eid = make_lore(client, wid)
    rev = client.get(f"/api/worlds/{wid}/lore/{eid}").json()["rev"]
    edit_on_disk(tmp_path, wid, eid, "---\nname: The Pact\n---\nTheirs.\n")

    fresh = client.put(f"/api/worlds/{wid}/lore/{eid}",
                       json={"body": "Mine.", "rev": rev}).json()["rev"]
    assert fresh != rev
    assert client.put(f"/api/worlds/{wid}/lore/{eid}",
                      json={"body": "Mine.", "rev": fresh}).status_code == 200


def test_a_save_with_no_rev_still_writes(client, wid, tmp_path):
    # Scripts and the store's own callers write records they never read. The
    # precondition is opt-in, so leaving `rev` out keeps the old behaviour.
    eid = make_lore(client, wid)
    edit_on_disk(tmp_path, wid, eid, "---\nname: The Pact\n---\nTheirs.\n")
    assert client.put(f"/api/worlds/{wid}/lore/{eid}", json={"body": "Mine."}).status_code == 200


def test_a_rewrite_of_identical_bytes_is_not_a_conflict(client, wid, tmp_path):
    # The rev is a content hash, not a stat signature: a sync client that
    # re-lands the same bytes (or a `touch`) moves mtime without changing the
    # record, and refusing the user's save for that would be crying wolf.
    eid = make_lore(client, wid)
    got = client.get(f"/api/worlds/{wid}/lore/{eid}").json()
    path = tmp_path / "worlds" / wid / "lore" / f"{eid}.md"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    assert client.put(f"/api/worlds/{wid}/lore/{eid}",
                      json={"body": "Mine.", "rev": got["rev"]}).status_code == 200


def test_a_record_deleted_underneath_is_a_conflict_not_a_write(client, wid, tmp_path):
    eid = make_lore(client, wid)
    rev = client.get(f"/api/worlds/{wid}/lore/{eid}").json()["rev"]
    (tmp_path / "worlds" / wid / "lore" / f"{eid}.md").unlink()

    res = client.put(f"/api/worlds/{wid}/lore/{eid}", json={"body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert res.json()["rev"] is None


def test_an_unknown_kind_is_still_a_404_even_with_a_rev(client, wid):
    res = client.put(f"/api/worlds/{wid}/sonnets/x", json={"body": "Mine.", "rev": "whatever"})
    assert res.status_code == 404


# ---- campaign scope: the same rule, resolved through the overlay ----

def test_a_campaign_copy_refuses_a_stale_save(client, cid, wid, tmp_path):
    eid = make_lore(client, wid)
    client.put(f"/api/campaigns/{cid}/lore/{eid}", json={"body": "Ours."})  # materializes
    rev = client.get(f"/api/campaigns/{cid}/lore/{eid}").json()["rev"]
    copy = tmp_path / "campaigns" / cid / "lore" / f"{eid}.md"
    copy.write_text("---\nname: The Pact\n---\nTheirs.\n", encoding="utf-8")

    res = client.put(f"/api/campaigns/{cid}/lore/{eid}", json={"body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert client.get(f"/api/campaigns/{cid}/lore/{eid}").json()["body"].strip() == "Theirs."


def test_an_inherited_record_carries_the_world_files_rev(client, cid, wid, tmp_path):
    # Nothing is materialized yet, so the campaign read answers out of the
    # world — and a world-side edit before the save means the copy the save
    # would make is not the text the user was shown.
    eid = make_lore(client, wid)
    rev = client.get(f"/api/campaigns/{cid}/lore/{eid}").json()["rev"]
    assert rev == client.get(f"/api/worlds/{wid}/lore/{eid}").json()["rev"]
    edit_on_disk(tmp_path, wid, eid, "---\nname: The Pact\n---\nTheirs.\n")

    res = client.put(f"/api/campaigns/{cid}/lore/{eid}", json={"body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert not (tmp_path / "campaigns" / cid / "lore" / f"{eid}.md").exists()


def test_a_refused_campaign_save_leaves_no_undo_entry(client, cid, wid, tmp_path):
    eid = make_lore(client, wid)
    client.put(f"/api/campaigns/{cid}/lore/{eid}", json={"body": "Ours."})
    before = len(client.get(f"/api/campaigns/{cid}/journal").json())
    assert before, "the accepted save must journal, or this proves nothing about the refused one"
    rev = client.get(f"/api/campaigns/{cid}/lore/{eid}").json()["rev"]
    (tmp_path / "campaigns" / cid / "lore" / f"{eid}.md").write_text(
        "---\nname: The Pact\n---\nTheirs.\n", encoding="utf-8")

    assert client.put(f"/api/campaigns/{cid}/lore/{eid}",
                      json={"body": "Mine.", "rev": rev}).status_code == 409
    after = client.get(f"/api/campaigns/{cid}/journal").json()
    assert len(after) == before


# ---- greetings: the same flat markdown record, the same rule ----

def make_greeting(client, wid, body="Hello."):
    return client.post(f"/api/worlds/{wid}/greetings",
                       json={"name": "At the Gate", "character": "", "version": "",
                             "body": body}).json()["id"]


def test_a_greeting_read_carries_a_rev(client, wid):
    gid = make_greeting(client, wid)
    got = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()
    assert got["rev"] == store.entities.entity_hash(store.home() / "worlds" / wid, "greetings", gid)


def test_a_stale_greeting_save_is_refused(client, wid, tmp_path):
    gid = make_greeting(client, wid)
    rev = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()["rev"]
    (tmp_path / "worlds" / wid / "greetings" / f"{gid}.md").write_text(
        "---\nname: At the Gate\n---\nTheirs.\n", encoding="utf-8")

    res = client.put(f"/api/worlds/{wid}/greetings/{gid}",
                     json={"name": "At the Gate", "body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert res.json()["kind"] == "stale_record"
    assert client.get(f"/api/worlds/{wid}/greetings/{gid}").json()["body"].strip() == "Theirs."


def test_a_greeting_save_with_the_right_rev_lands(client, wid):
    gid = make_greeting(client, wid)
    rev = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()["rev"]
    assert client.put(f"/api/worlds/{wid}/greetings/{gid}",
                      json={"body": "Mine.", "rev": rev}).status_code == 200


def test_a_campaign_greeting_resolves_its_rev_through_the_overlay(client, cid, wid, tmp_path):
    gid = make_greeting(client, wid)
    rev = client.get(f"/api/campaigns/{cid}/greetings/{gid}").json()["rev"]
    assert rev == client.get(f"/api/worlds/{wid}/greetings/{gid}").json()["rev"]
    (tmp_path / "worlds" / wid / "greetings" / f"{gid}.md").write_text(
        "---\nname: At the Gate\n---\nTheirs.\n", encoding="utf-8")

    res = client.put(f"/api/campaigns/{cid}/greetings/{gid}", json={"body": "Mine.", "rev": rev})
    assert res.status_code == 409
    assert not (tmp_path / "campaigns" / cid / "greetings" / f"{gid}.md").exists()


def test_an_empty_rev_is_no_precondition_at_all(client, wid, tmp_path):
    # A client with nothing to offer must not become one that can never write.
    gid = make_greeting(client, wid)
    (tmp_path / "worlds" / wid / "greetings" / f"{gid}.md").write_text(
        "---\nname: At the Gate\n---\nTheirs.\n", encoding="utf-8")
    assert client.put(f"/api/worlds/{wid}/greetings/{gid}",
                      json={"body": "Mine.", "rev": ""}).status_code == 200
