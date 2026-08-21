import importlib
import json

import pytest
from fastapi.testclient import TestClient

from grimoire import store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(create_app()) as c:
        yield c


def doc(cid="saltmarch-fens", name="Fens", persistence=0.4):
    return {"id": cid, "name": name, "persistence": persistence,
            "seasons": [{"name": "all year", "from": 0.0, "to": 0.0,
                         "temperature": [{"name": "mild", "weight": 1}],
                         "conditions": [{"name": "clear", "weight": 1}],
                         "wind": [{"name": "calm", "weight": 1}]}]}


def test_list_reports_both_tier_flags(client):
    rows = client.get("/api/climates").json()["climates"]
    assert rows and all({"id", "name", "builtin", "custom"} <= set(r) for r in rows)
    assert all(r["builtin"] and not r["custom"] for r in rows)


def test_a_private_climate_appears_as_custom_only(client):
    client.put("/api/climates/saltmarch-fens", json=doc())
    row = next(r for r in client.get("/api/climates").json()["climates"]
               if r["id"] == "saltmarch-fens")
    assert row["custom"] is True and row["builtin"] is False


def test_editing_a_preset_copies_it_rather_than_writing_the_package(client, tmp_path):
    # Presets live inside the installed backend package and must never be
    # written; the edit lands in GRIMOIRE_HOME and shadows the shipped one.
    before = json.loads((__import__("pathlib").Path(store.climates._PRESETS)
                         / "temperate-interior.json").read_text())
    edited = {**before, "name": "My Interior"}
    assert client.put("/api/climates/temperate-interior", json=edited).status_code == 200
    assert (tmp_path / "climates" / "temperate-interior.json").exists()
    after = json.loads((__import__("pathlib").Path(store.climates._PRESETS)
                        / "temperate-interior.json").read_text())
    assert after == before  # the shipped file is untouched
    row = next(r for r in client.get("/api/climates").json()["climates"]
               if r["id"] == "temperate-interior")
    assert row["builtin"] and row["custom"]
    assert row["name"] == "My Interior"  # the custom copy shadows the preset


def test_get_returns_the_shadowing_copy(client):
    client.put("/api/climates/temperate-interior",
               json={**doc("temperate-interior", "Shadowed"), "persistence": 0.1})
    got = client.get("/api/climates/temperate-interior").json()
    assert got["climate"]["name"] == "Shadowed"
    assert got["builtin"] and got["custom"]


def test_deleting_a_custom_copy_reverts_to_the_preset(client):
    client.put("/api/climates/temperate-interior", json=doc("temperate-interior", "Shadowed"))
    r = client.delete("/api/climates/temperate-interior")
    assert r.status_code == 200 and r.json()["reverted_to_preset"] is True
    assert client.get("/api/climates/temperate-interior").json()["climate"]["name"] != "Shadowed"


def test_deleting_a_standalone_custom_climate_frees_the_id(client):
    client.put("/api/climates/saltmarch-fens", json=doc())
    r = client.delete("/api/climates/saltmarch-fens")
    assert r.status_code == 200 and r.json()["reverted_to_preset"] is False
    assert client.get("/api/climates/saltmarch-fens").status_code == 404


def test_delete_404s_when_there_is_no_custom_copy(client):
    assert client.delete("/api/climates/temperate-interior").status_code == 404


def test_an_invalid_document_is_a_400_not_a_silently_skipped_file(client):
    # The resolver is lenient so bad data cannot take a turn down, which makes
    # this the only place a mistake can be reported at all.
    bad = doc()
    bad["seasons"][0]["conditions"] = [{"name": "clear", "weight": -1}]
    r = client.put("/api/climates/saltmarch-fens", json=bad)
    assert r.status_code == 400 and "weight" in r.json()["detail"]
    assert client.get("/api/climates/saltmarch-fens").status_code == 404


def test_seasons_with_a_gap_are_rejected(client):
    bad = doc()
    bad["seasons"] = [{**bad["seasons"][0], "from": 0.0, "to": 0.4}]
    r = client.put("/api/climates/saltmarch-fens", json=bad)
    assert r.status_code == 400


def test_the_route_id_wins_over_a_mismatched_body_id(client):
    # Otherwise the write lands in a file the editor cannot reopen.
    client.put("/api/climates/saltmarch-fens", json=doc(cid="something-else"))
    assert client.get("/api/climates/saltmarch-fens").status_code == 200
    assert client.get("/api/climates/something-else").status_code == 404


def test_an_unopenable_id_is_rejected(client):
    # A dot-only id lists fine and is then unopenable, uneditable and
    # undeletable through these routes.
    assert client.put("/api/climates/..", json=doc(cid="..")).status_code in (400, 404, 405)


def test_a_saved_climate_is_usable_as_a_campaign_default(client):
    client.put("/api/climates/saltmarch-fens", json=doc())
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    r = client.post("/api/campaigns", json={"name": "Saltmarch Chronicle", "world": wid,
                                            "climate": "saltmarch-fens"})
    assert r.status_code == 200


# ---- from the second Codex review of #232 ----

def test_a_file_whose_id_does_not_match_its_name_is_skipped(client, tmp_path):
    # Registering it under the document id, while custom_path and deletion
    # address <id>.json, leaves a climate that lists as custom, opens as
    # non-custom, and cannot be removed.
    (tmp_path / "climates").mkdir(exist_ok=True)
    (tmp_path / "climates" / "wrong-name.json").write_text(
        json.dumps(doc("saltmarch-fens")), encoding="utf-8")
    ids = {r["id"] for r in client.get("/api/climates").json()["climates"]}
    assert "saltmarch-fens" not in ids
    assert client.get("/api/climates/saltmarch-fens").status_code == 404


# ---- referrers (#237): the scan the delete warning is built on ----

def campaign(client, climate=None):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    body = {"name": "Saltmarch Chronicle", "world": wid}
    if climate is not None:
        body["climate"] = climate
    return client.post("/api/campaigns", json=body).json()["id"]


def test_referrers_report_a_campaign_that_defaults_to_the_climate(client):
    client.put("/api/climates/saltmarch-fens", json=doc())
    cid = campaign(client, climate="saltmarch-fens")
    got = client.get("/api/climates/saltmarch-fens/referrers").json()
    assert [c["id"] for c in got["campaigns"]] == [cid]


def test_referrers_report_a_location_naming_the_climate(client):
    client.put("/api/climates/saltmarch-fens", json=doc())
    cid = campaign(client)
    lid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch Docks", "body": "A place",
                            "fields": {"climate": "saltmarch-fens"}}).json()["id"]
    got = client.get("/api/climates/saltmarch-fens/referrers").json()
    assert [(l["campaign"], l["id"]) for l in got["locations"]] == [(cid, lid)]


def test_a_campaign_with_no_stored_default_counts_as_using_the_fallback(client):
    # It resolves to the preset at every turn, so a warning that omits it is
    # wrong about who is affected. The file-reading scan reported nothing.
    from grimoire.store import campaign_climate
    cid = campaign(client)
    campaign_climate.path(cid).unlink()
    got = client.get(f"/api/climates/{store.climates.FALLBACK_ID}/referrers").json()
    assert [c["id"] for c in got["campaigns"]] == [cid]


def test_a_campaign_whose_default_went_dangling_is_not_a_referrer(client):
    # The named climate no longer exists, so the campaign resolves to the
    # preset; reporting it under a dead id would warn about nothing.
    client.put("/api/climates/saltmarch-fens", json=doc())
    cid = campaign(client, climate="saltmarch-fens")
    assert client.delete("/api/climates/saltmarch-fens").status_code == 200
    got = client.get("/api/climates/saltmarch-fens/referrers").json()
    assert got["campaigns"] == []
    assert [c["id"] for c in client.get(
        f"/api/climates/{store.climates.FALLBACK_ID}/referrers").json()["campaigns"]] == [cid]


def test_deleting_a_climate_returns_the_referrers_it_affects(client):
    # The delete is not blocked; the warning is the whole safety net.
    client.put("/api/climates/saltmarch-fens", json=doc())
    cid = campaign(client, climate="saltmarch-fens")
    got = client.delete("/api/climates/saltmarch-fens").json()
    assert [c["id"] for c in got["referrers"]["campaigns"]] == [cid]


def test_a_failed_delete_is_a_500_not_a_404(client, monkeypatch):
    # "no custom climate to delete" for a climate that still exists tells the
    # user the opposite of what happened.
    import pathlib
    client.put("/api/climates/saltmarch-fens", json=doc())
    def boom(self, *a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(pathlib.Path, "unlink", boom)
    assert client.delete("/api/climates/saltmarch-fens").status_code == 500
