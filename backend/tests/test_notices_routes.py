"""Warn-once pre-notices (#106): the HTTP surface.

Two surfaces over one ledger, and the test that matters most is that they are
one: a notice dismissed from the scene panel must not still be waiting in the
scene-planning list. The store half — windows, keys, eviction, tolerance — is
`test_notices_store.py`.
"""

from __future__ import annotations

import grimoire.store as store

NOW = "2026-05-10"


def _campaign(client, holidays=()):
    """A campaign whose calendar observes exactly `holidays`, clocked at NOW.

    `region: ""` switches the holiday library off — otherwise the window under
    test also holds whatever the library says about mid-May, which varies with
    its version.
    """
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg["primary"] = {**cfg["primary"], "region": "", "custom_holidays": list(holidays)}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).status_code == 200
    client.post(f"/api/campaigns/{cid}/advance", json={"to": NOW, "reason": "the story so far"})
    return cid


def _notices(client, cid):
    return client.get(f"/api/campaigns/{cid}/notices").json()


def _rule(name, month, day):
    return {"name": name, "month": month, "day": day}


# ---- the campaign-wide surface --------------------------------------------

def test_a_fresh_campaign_has_nothing_to_warn_about(client):
    body = _notices(client, _campaign(client))
    assert body["notices"] == [] and body["now"] == NOW
    assert body["warn_days"] == store.calendars.WARN_DAYS


def test_an_unclocked_campaign_answers_an_empty_list(client):
    """No present means no window. It is a campaign nobody has dated yet, not
    an error, and the panel should say nothing rather than fail."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert _notices(client, cid) == {"notices": [], "now": "",
                                     "warn_days": store.calendars.WARN_DAYS}


def test_an_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/notices").status_code == 404
    assert client.post("/api/campaigns/nope/notices", json={"keys": ["k"]}).status_code == 404
    assert client.post("/api/campaigns/nope/notices/forget",
                       json={"keys": ["k"]}).status_code == 404


def test_notices_is_not_captured_by_the_generic_kind_route(client):
    """`/campaigns/{cid}/{kind}` would swallow this segment if it registered
    first — `test_route_order.py` holds the rule, this proves the URL works."""
    cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/notices")
    assert r.status_code == 200 and "notices" in r.json()


def test_an_imminent_event_and_holiday_are_both_reported(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 14)])
    client.post(f"/api/campaigns/{cid}/events",
                json={"name": "The envoy arrives", "date": "2026-05-12"})
    rows = _notices(client, cid)["notices"]
    assert [(r["kind"], r["name"], r["in_days"]) for r in rows] == [
        ("event", "The envoy arrives", 2), ("holiday", "Saltmarch Eve", 4)]


def test_dismissing_marks_the_key_and_is_idempotent(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _notices(client, cid)["notices"][0]["key"]
    r = client.post(f"/api/campaigns/{cid}/notices", json={"keys": [key], "scene": "s"})
    assert r.status_code == 200 and r.json() == {"ok": True, "marked": [key]}
    assert _notices(client, cid)["notices"] == []
    assert client.post(f"/api/campaigns/{cid}/notices",
                       json={"keys": [key]}).json()["marked"] == []


def test_forget_puts_a_dismissed_notice_back(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _notices(client, cid)["notices"][0]["key"]
    client.post(f"/api/campaigns/{cid}/notices", json={"keys": [key]})
    r = client.post(f"/api/campaigns/{cid}/notices/forget", json={"keys": [key]})
    assert r.status_code == 200 and r.json() == {"ok": True, "forgotten": [key]}
    assert [n["key"] for n in _notices(client, cid)["notices"]] == [key]


# ---- the scene surface, and the one ledger under both ----------------------

def _scene_at(client, cid, when):
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # The first `set_datetime` renames the scene, so the id to ask with is the
    # one the write hands back.
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                      json={"datetime": when}).json()["id"]


def _scene_notices(client, cid, sid):
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"]["notices"]


def test_a_dated_scene_carries_its_own_notices(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    sid = _scene_at(client, cid, NOW)
    assert [n["name"] for n in _scene_notices(client, cid, sid)] == ["Saltmarch Eve"]


def test_a_dateless_scene_has_no_notices_to_carry(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"] is None


def test_a_scene_is_warned_from_its_own_moment_not_the_clock(client):
    """A flashback must not be warned about next week. The scene surface asks
    from the scene's date; the campaign one asks from the clock."""
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    sid = _scene_at(client, cid, "2025-01-04")
    assert _scene_notices(client, cid, sid) == []
    assert [n["name"] for n in _notices(client, cid)["notices"]] == ["Saltmarch Eve"]


def test_dismissing_in_one_surface_silences_the_other(client):
    """One ledger, campaign-wide. This is the whole feature: the reader is
    warned once, not once per place a warning can appear."""
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    sid = _scene_at(client, cid, NOW)
    key = _scene_notices(client, cid, sid)[0]["key"]
    client.post(f"/api/campaigns/{cid}/notices", json={"keys": [key], "scene": sid})
    assert _scene_notices(client, cid, sid) == []
    assert _notices(client, cid)["notices"] == []


# ---- the warn window is configuration -------------------------------------

def test_warn_days_round_trips_through_the_calendar_route(client):
    cid = _campaign(client)
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert client.put(f"/api/campaigns/{cid}/calendar",
                      json={**cfg, "warn_days": 3}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["warn_days"] == 3
    assert _notices(client, cid)["warn_days"] == 3


def test_a_client_that_omits_warn_days_keeps_the_default(client):
    """`None`, not 0, is "no opinion" — so an older client saving the calendar
    cannot silently switch a campaign's warnings off."""
    cid = _campaign(client)
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    cfg.pop("warn_days")
    client.put(f"/api/campaigns/{cid}/calendar", json=cfg)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["warn_days"] == \
        store.calendars.WARN_DAYS


def test_warn_days_of_zero_is_saved_and_switches_warnings_off(client):
    cid = _campaign(client, holidays=[_rule("Saltmarch Eve", "05", 13)])
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    client.put(f"/api/campaigns/{cid}/calendar", json={**cfg, "warn_days": 0})
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["warn_days"] == 0
    assert _notices(client, cid)["notices"] == []


def test_a_world_calendar_carries_warn_days_into_its_campaigns(client):
    """calendar.json is copied on create, which is what lets a world set the
    default its campaigns start with."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cfg = client.get(f"/api/worlds/{wid}/calendar").json()
    assert client.put(f"/api/worlds/{wid}/calendar",
                      json={**cfg, "warn_days": 2}).status_code == 200
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["warn_days"] == 2
