"""PC revision history over HTTP (#67): both scopes, and the rule that a
campaign's history is its own copy's and a campaign route never writes the
world."""

import grimoire.store as store


def _world(client, name="Realm"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


def _pc(client, wid, name="Winifred"):
    return client.post(f"/api/worlds/{wid}/pcs", json={"name": name}).json()["pc"]


def _save(client, base, **fields):
    persona = {**store.pcs.blank_persona("Winifred"), **fields}
    assert client.put(base, json={"persona": persona}).status_code == 200


def test_world_history_lists_reads_and_restores(client):
    wid = _world(client)
    pid = _pc(client, wid)
    base = f"/api/worlds/{wid}/pcs/{pid}/versions/default"
    _save(client, base, description="first")
    _save(client, base, description="second")

    revs = client.get(f"{base}/revisions").json()
    assert [r["name"] for r in revs] == ["Winifred", "Winifred"]
    newest = revs[0]["id"]
    assert client.get(f"{base}/revisions/{newest}").json()["description"] == "first"

    assert client.post(f"{base}/revisions/{newest}/restore").status_code == 200
    pc = client.get(f"/api/worlds/{wid}/pcs/{pid}").json()
    assert pc["versions"][0]["persona"]["description"] == "first"
    # the restore replaced "second", which is now the newest revision
    after = client.get(f"{base}/revisions").json()
    assert client.get(f"{base}/revisions/{after[0]['id']}").json()["description"] == "second"


def test_misses_name_what_was_missing(client):
    wid = _world(client)
    pid = _pc(client, wid)
    root = f"/api/worlds/{wid}/pcs"
    assert client.get(f"{root}/nobody/versions/default/revisions").json()["detail"] == "pc not found"
    assert client.get(f"{root}/{pid}/versions/nope/revisions").json()["detail"] == "version not found"
    r = client.get(f"{root}/{pid}/versions/default/revisions/20260101T000000000000Z")
    assert (r.status_code, r.json()["detail"]) == (404, "revision not found")
    r = client.post(f"{root}/{pid}/versions/default/revisions/20260101T000000000000Z/restore")
    assert (r.status_code, r.json()["detail"]) == (404, "revision not found")


def test_campaign_history_is_the_campaigns_own_and_never_writes_the_world(client):
    wid = _world(client)
    pid = _pc(client, wid)
    wbase = f"/api/worlds/{wid}/pcs/{pid}/versions/default"
    _save(client, wbase, description="world text")
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cbase = f"/api/campaigns/{cid}/pcs/{pid}/versions/default"

    # inherited: the world has history, the campaign has none of its own
    assert len(client.get(f"{wbase}/revisions").json()) == 1
    assert client.get(f"{cbase}/revisions").json() == []
    assert client.get(f"{cbase}/revisions/x").status_code == 404

    world_file = store.worlds.world_root(wid) / "pcs" / pid / "default.md"
    world_bytes = world_file.read_bytes()
    _save(client, cbase, description="campaign one")
    _save(client, cbase, description="campaign two")
    revs = client.get(f"{cbase}/revisions").json()
    texts = [client.get(f"{cbase}/revisions/{r['id']}").json()["description"] for r in revs]
    assert texts == ["campaign one", "world text"]   # the first entry is what it inherited

    assert client.post(f"{cbase}/revisions/{revs[1]['id']}/restore").status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/pcs/{pid}").json()
    assert detail["versions"][0]["persona"]["description"] == "world text"
    assert world_file.read_bytes() == world_bytes
    assert len(client.get(f"{wbase}/revisions").json()) == 1


def test_a_restore_is_held_to_the_name_rule(client):
    wid = _world(client)
    pid = _pc(client, wid, "Mara")
    base = f"/api/worlds/{wid}/pcs/{pid}/versions/default"
    _save(client, base, name="Seraphine")
    _save(client, base, name="Mara")        # history now holds a "Seraphine" text
    _pc(client, wid, "Seraphine")           # ...and another PC has since taken that name
    old = next(r for r in client.get(f"{base}/revisions").json() if r["name"] == "Seraphine")
    assert client.post(f"{base}/revisions/{old['id']}/restore").status_code == 409
    pc = client.get(f"/api/worlds/{wid}/pcs/{pid}").json()
    assert pc["versions"][0]["persona"]["name"] == "Mara"


# ---- history follows its text through the library operations (#67 review) ----

def _setup_two_versions(client):
    wid = _world(client)
    pid = _pc(client, wid)
    older = client.post(f"/api/worlds/{wid}/pcs/{pid}/versions",
                        json={"name": "Older",
                              "persona": store.pcs.blank_persona("Winifred")}).json()["version"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    return wid, pid, older, cid


def _history_dir(cid, pid):
    return store.campaigns.campaign_root(cid) / "pcs" / pid / "history"


def test_locking_a_version_drops_the_purged_siblings_history(client):
    _wid, pid, older, cid = _setup_two_versions(client)
    _save(client, f"/api/campaigns/{cid}/pcs/{pid}/versions/{older}", description="campaign edit")
    assert (_history_dir(cid, pid) / older).is_dir()
    assert client.post(f"/api/campaigns/{cid}/pcs/{pid}/pick-version",
                       json={"version": "default"}).status_code == 200
    assert not (_history_dir(cid, pid) / older).exists()


def test_importing_a_version_drops_the_replaced_ones_history(client):
    _wid, pid, older, cid = _setup_two_versions(client)
    cbase = f"/api/campaigns/{cid}/pcs/{pid}/versions/default"
    assert client.post(f"/api/campaigns/{cid}/pcs/{pid}/pick-version",
                       json={"version": "default"}).status_code == 200
    _save(client, cbase, description="campaign edit")
    assert len(client.get(f"{cbase}/revisions").json()) == 1
    assert client.post(f"/api/campaigns/{cid}/pcs/{pid}/import-version",
                       json={"version": older}).status_code == 200
    assert not (_history_dir(cid, pid) / "default").exists()


def test_accepting_the_worlds_text_over_a_locked_copy_keeps_the_campaign_edit(client):
    wid, pid, _older, cid = _setup_two_versions(client)
    cbase = f"/api/campaigns/{cid}/pcs/{pid}/versions/default"
    assert client.post(f"/api/campaigns/{cid}/pcs/{pid}/pick-version",
                       json={"version": "default"}).status_code == 200
    _save(client, cbase, description="campaign edit")
    _save(client, f"/api/worlds/{wid}/pcs/{pid}/versions/default", description="world edit")
    assert client.post(f"/api/campaigns/{cid}/incoming/accept",
                       json={"refs": [{"kind": "pcs", "id": pid}]}).status_code == 200
    detail = client.get(f"/api/campaigns/{cid}/pcs/{pid}").json()
    assert detail["versions"][0]["persona"]["description"] == "world edit"
    newest = client.get(f"{cbase}/revisions").json()[0]["id"]
    assert client.get(f"{cbase}/revisions/{newest}").json()["description"] == "campaign edit"


def test_reverting_a_copy_to_inherited_takes_its_history_with_it(client):
    wid = _world(client)
    pid = _pc(client, wid)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cbase = f"/api/campaigns/{cid}/pcs/{pid}/versions/default"
    _save(client, cbase, description="campaign edit")
    assert client.get(f"{cbase}/revisions").json()
    store.overlay.dematerialize_actor(cid, "pcs", pid)
    assert client.get(f"{cbase}/revisions").json() == []
    assert not _history_dir(cid, pid).exists()
    # and a fresh copy starts clean rather than inheriting the old trail
    _save(client, cbase, description="again")
    assert len(client.get(f"{cbase}/revisions").json()) == 1


def test_a_campaign_restore_of_a_missing_revision_materializes_nothing(client):
    wid = _world(client)
    pid = _pc(client, wid)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/pcs/{pid}/versions/default/revisions/"
                    "20260101T000000000000Z/restore")
    assert r.status_code == 404
    assert not (store.campaigns.campaign_root(cid) / "pcs" / pid / "pc.md").exists()


def test_a_name_with_a_line_break_cannot_slip_past_uniqueness(client):
    wid = _world(client)
    pid = _pc(client, wid, "Mara")
    _pc(client, wid, "Mara Vance")
    r = client.put(f"/api/worlds/{wid}/pcs/{pid}/versions/default",
                   json={"persona": {**store.pcs.blank_persona("x"), "name": "Mara\nVance"}})
    assert r.status_code == 409
