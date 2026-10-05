"""Publishing a reviewed new record to the world (spec §9.4).

A row marked *World library* is created campaign-local by `apply_edits`, then
promoted as one more journalled step of the same chronicle commit. The journal
is what keeps a lost response or a crash after promote from reading the
commit's own world record as somebody else's.
"""

from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.store import campaigns, entities, overlay, sync, worlds
from grimoire.store.absorb import publish


class _Crash(BaseException):
    """A process death, not an application error: it escapes every
    `except Exception` the commit has, the way a killed interpreter would."""


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    return wid, cid, sid


def _new_lore(destination="world"):
    payload = {"name": "Lantern", "keys": "lantern", "kind": "lore"}
    if destination is not None:
        payload["destination"] = destination
    return {"id": "new_lore:lantern", "kind": "new_lore",
            "target": {"kind": "lore", "id": ""}, "label": "New lore — Lantern",
            "field": "body", "before": "", "after": "A brass lantern.",
            "authored": False, "payload": payload}


def _new_location(destination="world"):
    return {"id": "new_location:the-crypt", "kind": "new_location",
            "target": {"kind": "locations", "id": ""}, "label": "New location — The Crypt",
            "field": "body", "before": "", "after": "A cold crypt.", "authored": False,
            "payload": {"name": "The Crypt", "keys": "", "sd_prompt": "",
                        "current_setting": False, "destination": destination}}


def _save(cid, sid, edits):
    return {"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
            "edits": edits,
            "commit_token": store.commits.mint(store.commits.scene_epoch(cid, sid))}


def _put(client, cid, sid, save):
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save)


def _counting(monkeypatch):
    calls: list[tuple[str, str]] = []
    real = sync.promote

    def counted(cid, kind, eid):
        calls.append((kind, eid))
        return real(cid, kind, eid)

    monkeypatch.setattr(sync, "promote", counted)
    return calls


def _world_body(wid, kind, eid):
    return entities.read_entity(worlds.world_root(wid), kind, eid)["body"].strip()


# ---- the commit step --------------------------------------------------------

def test_world_destination_publishes_after_create(client):
    wid, cid, sid = _scene(client)
    r = _put(client, cid, sid, _save(cid, sid, [_new_lore(), _new_location()]))
    assert r.status_code == 200
    assert r.json()["applied"] == ["new_lore:lantern", "new_location:the-crypt"]
    assert r.json()["published"] == [{"kind": "lore", "id": "lantern"},
                                      {"kind": "locations", "id": "the-crypt"}]
    assert r.json()["publish_failed"] == []
    assert _world_body(wid, "lore", "lantern") == "A brass lantern."
    assert _world_body(wid, "locations", "the-crypt") == "A cold crypt."
    # one record from here on: mine == world == base, so nothing is incoming
    assert sync.incoming(cid) == []


def test_campaign_destination_never_promotes(client, monkeypatch):
    wid, cid, sid = _scene(client)
    calls = _counting(monkeypatch)
    r = _put(client, cid, sid, _save(cid, sid, [_new_lore("campaign"),
                                                _new_location(None)]))
    assert r.status_code == 200 and len(r.json()["applied"]) == 2
    assert r.json()["published"] == [] and r.json()["publish_failed"] == []
    assert calls == []
    wroot = worlds.world_root(wid)
    assert not (wroot / "lore" / "lantern.md").exists()
    assert not (wroot / "locations" / "the-crypt.md").exists()


def test_a_failed_create_is_not_published(client, monkeypatch):
    """Only a slot that applied AND names what it created is published -- a row
    whose create failed has nothing campaign-side to copy up."""
    _wid, cid, sid = _scene(client)
    calls = _counting(monkeypatch)
    broken = _new_lore()
    del broken["payload"]["name"]
    r = _put(client, cid, sid, _save(cid, sid, [broken]))
    assert r.status_code == 200 and r.json()["applied"] == []
    assert r.json()["published"] == [] and r.json()["publish_failed"] == []
    assert calls == []


def test_precondition_failure_is_reported_not_raised(client, monkeypatch):
    """Another campaign of the same world publishes its own Lantern between the
    create and the promote. The save still lands; the record stays in the
    campaign and the reviewer is told why."""
    wid, cid, sid = _scene(client)
    real = sync.promote

    def raced(c, kind, eid):
        entities.create_entity(worlds.world_root(wid), kind, "Lantern", body="the world's")
        return real(c, kind, eid)

    monkeypatch.setattr(sync, "promote", raced)
    r = _put(client, cid, sid, _save(cid, sid, [_new_lore()]))
    assert r.status_code == 200
    assert r.json()["applied"] == ["new_lore:lantern"]
    assert r.json()["published"] == []
    assert r.json()["publish_failed"] == [
        {"kind": "lore", "id": "lantern",
         "reason": "the world already has a record named Lantern"}]
    assert _world_body(wid, "lore", "lantern") == "the world's"     # not overwritten
    assert overlay.record_text(cid, "lore", "lantern") is not None  # kept campaign-side


def test_any_other_promote_failure_is_reported_with_its_own_reason(client, monkeypatch):
    _wid, cid, sid = _scene(client)

    def refuse(c, kind, eid):
        raise sync.DanglingReferenceError("it points at campaign-local content")

    monkeypatch.setattr(sync, "promote", refuse)
    r = _put(client, cid, sid, _save(cid, sid, [_new_lore()]))
    assert r.status_code == 200
    assert r.json()["publish_failed"] == [
        {"kind": "lore", "id": "lantern", "reason": "it points at campaign-local content"}]


def test_a_filesystem_failure_does_not_leak_the_store_path(client, monkeypatch, tmp_path):
    _wid, cid, sid = _scene(client)

    def disk_full(c, kind, eid):
        raise OSError(28, "No space left on device", str(tmp_path / "worlds" / "x.md"))

    def nameless(c, kind, eid):
        raise RuntimeError()

    monkeypatch.setattr(sync, "promote", disk_full)
    r = _put(client, cid, sid, _save(cid, sid, [_new_lore()]))
    assert r.status_code == 200
    assert r.json()["publish_failed"] == [
        {"kind": "lore", "id": "lantern", "reason": "could not write the world record"}]
    assert str(tmp_path) not in r.text

    monkeypatch.setattr(sync, "promote", nameless)
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    lore = _new_lore()
    lore["payload"]["name"] = "Lamp"
    r = _put(client, cid, sid2, _save(cid, sid2, [lore]))
    assert r.json()["publish_failed"] == [
        {"kind": "lore", "id": "lamp", "reason": "RuntimeError"}]


def test_lost_response_retry_replays_published(client, monkeypatch):
    _wid, cid, sid = _scene(client)
    calls = _counting(monkeypatch)
    save = _save(cid, sid, [_new_lore()])
    first = _put(client, cid, sid, save)
    second = _put(client, cid, sid, save)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["published"] == [{"kind": "lore", "id": "lantern"}]
    assert second.json() == first.json()      # the spent token replays the lists too
    assert calls == [("lore", "lantern")]     # and never reaches promote again


def test_crash_between_promote_and_outcome_resolves_published(client, monkeypatch):
    """Promote wrote the world file and the process died before the outcome was
    journalled. The retry finds the step only attempted, sees the world holding
    exactly the bytes the campaign's base describes, and records `published`
    rather than promoting again into its own record."""
    wid, cid, sid = _scene(client)
    calls = _counting(monkeypatch)
    save = _save(cid, sid, [_new_lore()])
    real_checkpoint = store.commits.checkpoint

    def die_after_promote(c, token, progress):
        if isinstance(progress.get("publish", {}).get("0"), dict):
            raise _Crash("killed")
        return real_checkpoint(c, token, progress)

    monkeypatch.setattr(store.commits, "checkpoint", die_after_promote)
    # Not re-raising, for the reason `test_routes.py`'s mid-apply crash gives:
    # a propagated handler crash would unwind the shared client's lifespan.
    TestClient(client.app, raise_server_exceptions=False).put(
        f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save)
    monkeypatch.setattr(store.commits, "checkpoint", real_checkpoint)
    assert calls == [("lore", "lantern")]
    assert _world_body(wid, "lore", "lantern") == "A brass lantern."
    entry = store.commits.lookup(cid, save["commit_token"])
    assert entry["done"] is False and entry["progress"]["publish"] == {"0": "pending"}

    retry = _put(client, cid, sid, save)
    assert retry.status_code == 200
    assert retry.json()["published"] == [{"kind": "lore", "id": "lantern"}]
    assert retry.json()["publish_failed"] == []
    assert calls == [("lore", "lantern")]     # resolved from the world, not re-run


def test_pending_with_base_but_foreign_world_file_is_not_published(client, monkeypatch):
    """The crash landed between promote's two writes: the base is recorded and
    the world file never was. Before the retry, a different record claims the
    slug in the world. A base that matches no world file is not proof of a
    publish, so the retry promotes again and promote's precondition refuses."""
    wid, cid, sid = _scene(client)
    save = _save(cid, sid, [_new_lore()])
    real = sync.promote

    def base_then_die(c, kind, eid):
        manifest = campaigns.read_manifest(c)
        manifest[f"{kind}/{eid}"] = entities.content_hash(overlay.record_text(c, kind, eid))
        campaigns.write_manifest(c, manifest)
        raise _Crash("killed")

    monkeypatch.setattr(sync, "promote", base_then_die)
    TestClient(client.app, raise_server_exceptions=False).put(
        f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=save)
    monkeypatch.setattr(sync, "promote", real)
    assert store.commits.lookup(cid, save["commit_token"])["progress"]["publish"] \
        == {"0": "pending"}
    entities.create_entity(worlds.world_root(wid), "lore", "Lantern", body="someone else's")

    retry = _put(client, cid, sid, save)
    assert retry.status_code == 200
    assert retry.json()["published"] == []
    assert retry.json()["publish_failed"] == [
        {"kind": "lore", "id": "lantern",
         "reason": "the world already has a record named Lantern"}]
    assert _world_body(wid, "lore", "lantern") == "someone else's"


def test_a_settled_slot_is_reused_without_promoting(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("Realm"))
    calls = _counting(monkeypatch)
    progress = {"edits": {"0": {"state": "applied", "id": "new_lore:lantern",
                                "created": {"kind": "lore", "id": "lantern"}}},
                "publish": {"0": {"state": "failed", "reason": "earlier"}}}
    published, failed = publish.publish_created(cid, [_new_lore()], progress, None)
    assert published == [] and calls == []
    assert failed == [{"kind": "lore", "id": "lantern", "reason": "earlier"}]


# ---- sync.promoted_intact ---------------------------------------------------

def _store_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid, overlay.create_entity(cid, "lore", "Lantern", "A brass lantern.")


def test_promoted_intact_after_a_promote(monkeypatch, tmp_path):
    _wid, cid, eid = _store_campaign(monkeypatch, tmp_path)
    assert sync.promoted_intact(cid, "lore", eid) is False
    sync.promote(cid, "lore", eid)
    assert sync.promoted_intact(cid, "lore", eid) is True


def test_promoted_intact_needs_the_world_file(monkeypatch, tmp_path):
    _wid, cid, eid = _store_campaign(monkeypatch, tmp_path)
    manifest = campaigns.read_manifest(cid)
    manifest[f"lore/{eid}"] = entities.content_hash(overlay.record_text(cid, "lore", eid))
    campaigns.write_manifest(cid, manifest)
    assert sync.promoted_intact(cid, "lore", eid) is False


def test_promoted_intact_compares_the_world_file_with_the_base(monkeypatch, tmp_path):
    wid, cid, eid = _store_campaign(monkeypatch, tmp_path)
    sync.promote(cid, "lore", eid)
    # The campaign copy moving afterwards says nothing about the publish...
    overlay.update_entity(cid, "lore", eid, body="A dented lantern.")
    assert sync.promoted_intact(cid, "lore", eid) is True
    # ...the world file not matching the base does.
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Somebody's lamp.")
    assert sync.promoted_intact(cid, "lore", eid) is False
