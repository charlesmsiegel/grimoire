"""A wizard draft gets the world's real gating verdict without saving it (#68)."""

import pytest

from grimoire.store import appearances, campaigns, greetings, pcs, playing, scenes, worlds


@pytest.fixture
def world(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return wid, worlds.world_root(wid)


@pytest.mark.parametrize("wid", ["missing", "..%2Fmissing"])
def test_preview_requires_an_existing_world(client, wid):
    assert client.get(f"/api/worlds/{wid}/greeting-availability").status_code == 404


def test_empty_world_preview(client, world):
    wid, _root = world
    response = client.get(f"/api/worlds/{wid}/greeting-availability")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("tags, available", [
    ("", False), ("sailor", False), ("sailor,vip", True),
    ("Sailor,vip", False), (" sailor , vip , ,sailor", True),
])
def test_preview_uses_tag_subsets_and_keeps_predecessors_blocked(client, world, tags, available):
    wid, root = world
    opening = greetings.create_greeting(root, "Open", "", "")
    locked = greetings.create_greeting(root, "Locked", "", "", requires_tags=["sailor", "vip"])
    later = greetings.create_greeting(root, "Later", "", "", predecessor_join="any")
    greetings.set_edges(root, opening, leads_to=[later])

    response = client.get(f"/api/worlds/{wid}/greeting-availability", params={"tags": tags})
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert rows[opening]["available"] is True
    assert rows[locked]["available"] is available
    assert rows[locked]["reasons"] == ([] if available else ["missing required tags"])
    assert rows[later]["available"] is False
    assert rows[later]["reasons"] == ["predecessors not played (any)"]
    assert not any("unlocked" in row or "mark" in row for row in rows.values())


def test_preview_preserves_metadata_without_shadowing_a_greeting_id(client, world):
    wid, root = world
    gid = greetings.create_greeting(root, "Availability", "", "", pcless=True, location="saltmarch")
    assert gid == "availability"

    response = client.get(f"/api/worlds/{wid}/greeting-availability")
    assert response.status_code == 200
    assert response.json() == [{
        "id": gid, "name": "Availability", "available": True, "reasons": [],
        "pcless": True, "location": "saltmarch",
    }]
    detail = client.get(f"/api/worlds/{wid}/greetings/{gid}")
    assert detail.status_code == 200
    assert detail.json()["meta"]["name"] == "Availability"


def test_preview_does_not_change_the_store(client, world, tmp_path):
    wid, root = world
    greetings.create_greeting(root, "Open", "", "", requires_tags=["sailor"])
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    response = client.get(f"/api/worlds/{wid}/greeting-availability", params={"tags": "sailor"})
    assert response.status_code == 200
    assert response.json()[0]["available"] is True
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_preview_normalizes_tags_like_the_eventual_campaign_pc(client, world):
    wid, root = world
    gid = greetings.create_greeting(root, "Open", "", "", requires_tags=["sailor", "vip"])
    draft_tags = [" sailor , vip ", ""]
    response = client.get(f"/api/worlds/{wid}/greeting-availability",
                          params={"tags": ",".join(draft_tags)})
    assert response.status_code == 200
    assert response.json()[0]["available"] is True

    cid = campaigns.create_campaign("Saltmarch", wid)
    pid, vid = pcs.create_pc(campaigns.campaign_root(cid), "Mara", draft_tags)
    sid = scenes.create_scene(cid, "Open")
    appearances.appear(cid, sid, "pcs", pid, vid, "player")
    assert playing.player_tags(cid) == {"sailor", "vip"}
    assert {row["id"]: row["available"] for row in playing.available_greetings(cid)}[gid] is True
