"""The campaign-clock HTTP surface (#100): read the clock, preview an advance,
advance it, and the reconciliation a per-scene date triggers."""

from __future__ import annotations

import grimoire.store as store


def _campaign(client, calendar=None):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    body = {"name": "Run", "world": wid}
    if calendar:
        body["calendar"] = calendar
    return wid, client.post("/api/campaigns", json=body).json()["id"]


def test_clock_of_a_fresh_campaign_is_empty(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/clock").json() == {"now": "", "friendly": "", "log": []}


def test_clock_of_an_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/clock").status_code == 404
    assert client.post("/api/campaigns/nope/advance",
                       json={"to": "2026-05-01", "reason": "x"}).status_code == 404


def test_advance_to_a_date_moves_the_clock_and_logs_it(client):
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"to": "2026-05-01", "reason": "the caravan sets out"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["moved"] is True
    assert body["now"] == "2026-05-01" and body["friendly"] == "1 May 2026"
    assert body["digest"]["to_friendly"] == "1 May 2026"
    got = client.get(f"/api/campaigns/{cid}/clock").json()
    assert got["now"] == "2026-05-01" and got["friendly"] == "1 May 2026"
    assert [(e["from"], e["to"], e["reason"]) for e in got["log"]] == [
        ("", "2026-05-01", "the caravan sets out")]


def test_advance_by_days_reports_the_digest(client):
    _wid, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-12-24", "reason": "start"})
    digest = client.post(f"/api/campaigns/{cid}/advance",
                         json={"days": 3, "reason": "through Christmas"}).json()["digest"]
    assert digest["elapsed_days"] == 3 and digest["backward"] is False
    assert "Christmas Day" in [h["name"] for h in digest["holidays"]]


def test_advance_without_a_reason_is_400(client):
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-05-01", "reason": "  "})
    assert r.status_code == 400
    assert client.get(f"/api/campaigns/{cid}/clock").json()["now"] == ""


def test_advance_with_neither_target_nor_duration_is_400(client):
    _wid, cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/advance",
                       json={"reason": "nothing to go on"}).status_code == 400


def test_advance_by_days_without_an_anchor_is_400(client):
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/advance", json={"days": 5, "reason": "no start"})
    assert r.status_code == 400


def test_advance_to_a_bad_date_is_400(client):
    _wid, cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/advance",
                       json={"to": "2026-13-40", "reason": "no such day"}).status_code == 400


def test_preview_reports_the_digest_without_moving_the_clock(client):
    _wid, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-12-24", "reason": "start"})
    digest = client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 3}).json()["digest"]
    assert digest["elapsed_days"] == 3
    assert "Christmas Day" in [h["name"] for h in digest["holidays"]]
    assert client.get(f"/api/campaigns/{cid}/clock").json()["now"] == "2026-12-24"
    assert len(client.get(f"/api/campaigns/{cid}/clock").json()["log"]) == 1  # only the seed


def test_preview_needs_no_reason(client):
    _wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/advance/preview", json={"to": "2026-05-01"})
    assert r.status_code == 200 and r.json()["digest"]["to"] == "2026-05-01"


def test_preview_carries_the_checkpoint_nudge_and_the_threshold_behind_it(client):
    """#107: the panel offers to fork before a large skip, and reads both the
    verdict and the number it was reached by off the digest — so nothing on the
    client re-implements calendar arithmetic to decide what "large" is."""
    _wid, cid = _campaign(client)
    client.put("/api/config", json={"advance_fork_threshold": "5"})
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-01-01", "reason": "start"})
    small = client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 5}).json()["digest"]
    assert small["fork"] is False and small["fork_threshold"] == 5
    big = client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 6}).json()["digest"]
    assert big["fork"] is True and big["fork_threshold"] == 5
    # Still a nudge and not a gate: the endpoint takes the same body it always did.
    r = client.post(f"/api/campaigns/{cid}/advance", json={"days": 6, "reason": "a long road"})
    assert r.status_code == 200 and r.json()["digest"]["fork"] is True


def test_advance_writes_nothing_to_the_transcript(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    before = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-05-01", "reason": "a month off"})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == before


# ---- reconciliation with per-scene time ------------------------------------

def test_setting_a_scene_date_moves_the_clock_forward(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-06-01"})
    assert r.json()["clock"] == {"moved": True, "now": "2026-06-01", "fired": []}
    assert client.get(f"/api/campaigns/{cid}/clock").json()["now"] == "2026-06-01"


def test_a_flashback_scene_does_not_drag_the_clock_back(client):
    _wid, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-06-01", "reason": "the present"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2025-01-01"})
    assert r.json()["clock"] == {"moved": False, "now": "2026-06-01", "fired": []}
    assert client.get(f"/api/campaigns/{cid}/clock").json()["now"] == "2026-06-01"


def test_a_new_scene_pre_fills_from_the_advanced_clock(client):
    _wid, cid = _campaign(client)
    store.chronicle.absorb(cid, {"id": "001--old", "one_line": "x", "summary": "y",
                                 "keywords": [], "cast": [], "location": "",
                                 "date": "2026-06-01"})
    client.post(f"/api/campaigns/{cid}/advance", json={"days": 30, "reason": "a month on the road"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["suggested"] == "2026-07-01"   # not the chronicle's 1 June


def test_a_new_scene_still_pre_fills_from_the_chronicle_without_a_clock(client):
    _wid, cid = _campaign(client)
    store.chronicle.absorb(cid, {"id": "001--old", "one_line": "x", "summary": "y",
                                 "keywords": [], "cast": [], "location": "",
                                 "date": "2026-07-04T21:30"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["suggested"] == "2026-07-04"


def test_the_creation_hint_still_beats_the_clock(client):
    _wid, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-06-01", "reason": "the present"})
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "S", "suggested_date": "2026-06-09"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["suggested"] == "2026-06-09"


def test_advance_in_a_non_gregorian_campaign(client):
    _wid, cid = _campaign(client, calendar="hebrew")
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"to": "5786-kislev-24", "reason": "the eve of the festival"})
    assert r.json()["now"] == "5786-Kislev-24"
    digest = client.post(f"/api/campaigns/{cid}/advance",
                         json={"days": 2, "reason": "two days"}).json()["digest"]
    assert digest["to"] == "5786-Kislev-26"
    assert any("Chanuka" in h["name"] for h in digest["holidays"])
