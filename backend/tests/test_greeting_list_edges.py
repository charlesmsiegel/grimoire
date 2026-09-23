"""The greeting list carries each row's plot-map edges.

Two screens used to issue one `GET /greetings/{gid}` per greeting to learn what
the list could not tell them: the world overview, on every open, to decide one
checklist row, and the plot map, to draw its lines. Every edge lives in one
file, so the list carries them now for the price of one read of it -- and the
contract these tests hold is that a row's `edges` is exactly what that
greeting's own read reports, in both scopes, whatever the overlay did to it.
"""

import json

from fastapi.testclient import TestClient

from grimoire.store import greetings, overlay, worlds

EMPTY = {"leads_to": [], "excludes": []}


def _world(client, name="Saltmarch"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


def _greeting(client, base, name, character="mara"):
    return client.post(f"{base}/greetings", json={
        "name": name, "character": character, "version": "default", "body": "Hi."}).json()["id"]


def _agrees_with_each_read(client, base):
    """Every listed row's `edges` against that greeting's own read. Returns the
    rows so a caller can check the specifics too."""
    rows = client.get(f"{base}/greetings").json()
    for row in rows:
        own = client.get(f"{base}/greetings/{row['id']}").json()
        assert row["edges"] == own["edges"], row["id"]
    return rows


def test_world_rows_carry_the_edges_each_greetings_own_read_reports(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}"
    client.post(f"{base}/characters", json={"name": "Mara"})
    gala = _greeting(client, base, "Gala")
    docks = _greeting(client, base, "Docks")
    quay = _greeting(client, base, "Quay")
    before = client.get(f"{base}/greetings").json()
    client.put(f"{base}/greetings/{gala}/edges", json={"leads_to": [docks]})
    client.put(f"{base}/greetings/{docks}/edges", json={"excludes": [quay]})

    rows = _agrees_with_each_read(client, base)
    by_id = {r["id"]: r for r in rows}
    assert by_id[gala]["edges"] == {"leads_to": [docks], "excludes": []}
    assert by_id[docks]["edges"] == {"leads_to": [], "excludes": [quay]}
    # never named in the map at all: empty lists, the same as its own read
    assert by_id[quay]["edges"] == EMPTY
    # additive: the order and every field the list already carried are unchanged
    bare = [{k: v for k, v in r.items() if k != "edges"} for r in rows]
    assert bare == [{k: v for k, v in r.items() if k != "edges"} for r in before]


def test_a_world_with_no_plot_map_lists_empty_edges(client):
    wid = _world(client)
    base = f"/api/worlds/{wid}"
    client.post(f"{base}/characters", json={"name": "Mara"})
    _greeting(client, base, "Gala")
    assert not (worlds.world_root(wid) / "plotmap.json").exists()
    assert [r["edges"] for r in _agrees_with_each_read(client, base)] == [EMPTY]


def test_campaign_rows_agree_for_inherited_owned_and_tombstoned_greetings(client):
    wid = _world(client)
    wbase = f"/api/worlds/{wid}"
    client.post(f"{wbase}/characters", json={"name": "Mara"})
    gala = _greeting(client, wbase, "Gala")
    docks = _greeting(client, wbase, "Docks")
    quay = _greeting(client, wbase, "Quay")
    client.put(f"{wbase}/greetings/{gala}/edges", json={"leads_to": [docks, quay]})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cbase = f"/api/campaigns/{cid}"

    # Inherited: no campaign map yet, so both reads answer from the world's.
    rows = _agrees_with_each_read(client, cbase)
    assert {r["id"]: r["edges"] for r in rows}[gala] == {"leads_to": [docks, quay], "excludes": []}

    # Owned: a campaign greeting, and an edge from it, which forks the map.
    vow = _greeting(client, cbase, "Vow")
    client.put(f"{cbase}/greetings/{vow}/edges", json={"excludes": [gala]})
    # Tombstoned: deleting an inherited greeting drops it from the list and
    # unwires it from the campaign's map, which the surviving row must show.
    assert client.delete(f"{cbase}/greetings/{docks}").status_code == 200
    client.post(f"{cbase}/greetings/{quay}/mark", json={"status": "skipped"})

    rows = _agrees_with_each_read(client, cbase)
    by_id = {r["id"]: r for r in rows}
    assert docks not in by_id
    assert by_id[gala]["edges"] == {"leads_to": [quay], "excludes": []}
    assert by_id[vow]["edges"] == {"leads_to": [], "excludes": [gala]}
    assert by_id[quay]["mark"] == "skipped"      # the list's own field rides along
    # the world's map is the world's: the campaign's delete did not reach it
    assert {r["id"]: r["edges"] for r in client.get(f"{wbase}/greetings").json()}[gala] \
        == {"leads_to": [docks, quay], "excludes": []}


def test_a_detached_copy_lists_what_its_own_read_says(client):
    """A campaign's copy of a greeting the world deleted and then recreated under
    the same slug: the inherited map is filtered of it, node and inbound edges,
    and the list must be filtered the same way rather than read raw."""
    wid = _world(client)
    wbase = f"/api/worlds/{wid}"
    client.post(f"{wbase}/characters", json={"name": "Winifred"})
    gid = _greeting(client, wbase, "Arrival", "winifred")
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cbase = f"/api/campaigns/{cid}"
    rev = client.get(f"{cbase}/greetings/{gid}").json()["rev"]
    client.put(f"{cbase}/greetings/{gid}", json={"body": "the campaign's own", "rev": rev})
    assert client.delete(f"{wbase}/greetings/{gid}").status_code == 200
    assert f"greetings/{gid}" in overlay.detached(cid)

    wroot = worlds.world_root(wid)
    again = _greeting(client, wbase, "Arrival", "winifred")
    other = _greeting(client, wbase, "Departure", "winifred")
    assert again == gid
    greetings.set_edges(wroot, other, leads_to=[again], excludes=[])
    greetings.set_edges(wroot, again, leads_to=[other], excludes=[])
    # the inherited map, so the detachment filter is what is under test
    assert not (overlay.croot_of(cid) / "plotmap.json").exists()

    rows = _agrees_with_each_read(client, cbase)
    by_id = {r["id"]: r for r in rows}
    assert by_id[gid]["edges"] == EMPTY
    assert by_id[other]["edges"] == EMPTY


def test_one_plot_map_read_per_list_request(client, monkeypatch):
    wid = _world(client)
    wbase = f"/api/worlds/{wid}"
    client.post(f"{wbase}/characters", json={"name": "Mara"})
    ids = [_greeting(client, wbase, name) for name in ("Gala", "Docks", "Quay", "Vow")]
    client.put(f"{wbase}/greetings/{ids[0]}/edges", json={"leads_to": ids[1:]})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]

    reads = []
    real = greetings.read_plotmap

    def counted(root):
        reads.append(root)
        return real(root)

    monkeypatch.setattr(greetings, "read_plotmap", counted)
    assert len(client.get(f"{wbase}/greetings").json()) == 4
    assert len(reads) == 1
    reads.clear()
    assert len(client.get(f"/api/campaigns/{cid}/greetings").json()) == 4
    assert len(reads) == 1


def test_a_malformed_plot_map_leaves_edges_off_rather_than_failing_the_list(client):
    """The single-greeting read 500s on a garbled plotmap.json, and still does.
    The list never read the map before it carried edges, so it may not start
    failing on one -- the greetings tab, a character's page and the image
    gallery all read it. It leaves `edges` OFF instead: absent is "could not
    say", which a client must not mistake for "none" and write back."""
    wid = _world(client)
    wbase = f"/api/worlds/{wid}"
    client.post(f"{wbase}/characters", json={"name": "Mara"})
    gid = _greeting(client, wbase, "Gala")
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cbase = f"/api/campaigns/{cid}"
    # A detached copy too, so the campaign's read walks the inherited map's
    # entries to filter it -- which is where a map of the wrong shape breaks
    # first, before the route ever sees what was parsed.
    docks = _greeting(client, wbase, "Docks")
    rev = client.get(f"{cbase}/greetings/{docks}").json()["rev"]
    client.put(f"{cbase}/greetings/{docks}", json={"body": "the campaign's own", "rev": rev})
    client.delete(f"{wbase}/greetings/{docks}")
    assert f"greetings/{docks}" in overlay.detached(cid)
    lenient = TestClient(client.app, raise_server_exceptions=False)

    for bad in ("{not json", "[1, 2]", json.dumps({gid: ["a list, not an entry"]})):
        (worlds.world_root(wid) / "plotmap.json").write_text(bad, encoding="utf-8")
        for base, ids in ((wbase, [gid]), (cbase, [docks, gid])):
            r = client.get(f"{base}/greetings")
            assert r.status_code == 200, (bad, base)
            assert [row["id"] for row in r.json()] == ids
            assert not any("edges" in row for row in r.json()), (bad, base)
        assert lenient.get(f"{wbase}/greetings/{gid}").status_code == 500, bad
