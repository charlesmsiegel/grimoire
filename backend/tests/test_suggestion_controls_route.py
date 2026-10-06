"""`POST /scene-suggestions` with Story Pressure controls (capstone spec §16.3,
§15.2; Slice E Decisions 3, 7, 8, 25 and 26).

The body is validated against the snapshot the route builds for the prompt,
every refusal is answered before a run is reserved, and the payload is shaped
from that same capture -- so the labels and the claims a card shows are the
ones the prompt offered. Query parameters keep working when no body is sent.

The campaign: the clock at 2026-05-10, Gregorian with no holiday library
(`region = ""`), the threads "Mara's map" and "Find the ledger", the promise
"Mara's oath" due 2026-05-14 and the coronation on 2026-05-13.
"""

from __future__ import annotations

import json

from grimoire import routes
from grimoire.store import (
    calendars,
    campaigns,
    characters,
    clock,
    commitments,
    events,
    overlay,
    plot,
    suggest,
    usage,
)
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.continuity import drivers as continuity_drivers
from grimoire.store.continuity import pressure
from tests import draft_runs as drafts
from tests.llm_fakes import FakeOpenRouterComplete
from tests.test_suggest_store import _break_calendar

MAP = "thread:mara-s-map"
LEDGER = "thread:find-the-ledger"
OATH = "commitment:mara-s-oath"
CORONATION = "event:the-coronation"

REPLY = json.dumps({"suggestions": [
    {"title": "The gate at dusk", "premise": "Mara waits.", "cast": [], "location": "",
     "date": "2026-05-12", "drivers": [{"ref": MAP, "action": "advance"}]},
    {"title": "The ledger room", "premise": "Dust and ink.", "cast": [], "location": ""},
], "next_date": "2026-05-14"})


def _campaign(client, *, key: bool = True) -> str:
    if key:
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    root = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": "", "custom_holidays": []}
    calendars.write_calendar(root, cfg)
    clock.advance(cid, to="2026-05-10")
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                      "The map turned up in Saltmarch.", "001--gate")
    plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open",
                      "The ledger left Saltmarch.", "001--gate")
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-05-14", "Mara swore it at the gate.", "001--gate")
    assert events.create(cid, "The coronation", "2026-05-13") == "the-coronation"
    return cid


def _fake(client, reply: str = REPLY) -> FakeOpenRouterComplete:
    fake = FakeOpenRouterComplete(reply)
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def _url(cid: str) -> str:
    return f"/api/campaigns/{cid}/scene-suggestions"


def _post(client, cid: str, **kw):
    return drafts.post(client, _url(cid), **kw)


def _system(fake) -> str:
    return fake.messages[0]["content"]


def _runs(client, cid: str) -> list:
    return client.get(f"/api/campaigns/{cid}/runs").json()["runs"]


def _refused(r, status: int, reason: str) -> None:
    assert r.status_code == status, r.json()
    assert r.json()["kind"] == "bad_controls"
    assert r.json()["reason"] == reason


# ---- the body and the query ---------------------------------------------------


def test_no_body_is_todays_request(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid)
    assert r.status_code == 200
    assert fake.messages == suggest.build_prompt(suggest.build_snapshot(cid), [],
                                                 offscreen=False, direction="")
    for needle in ("Focus drivers:", "Avoid drivers:", "Must-include drivers:",
                   "Anchor every suggestion to"):
        assert needle not in _system(fake)


def test_query_parameters_still_work(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, params={"offscreen": "true", "rank": "false", "direction": "x"})
    assert r.status_code == 200
    assert fake.messages == suggest.build_prompt(suggest.build_snapshot(cid, offscreen=True),
                                                 [], offscreen=True, direction="x")
    system = _system(fake)
    assert system.startswith("You help a game master write OFFSCREEN scenes")
    assert "greeting_picks" not in system
    assert 'their instruction appears under "Direction" below' in system


def test_a_body_wins_over_the_query(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, params={"offscreen": "true"}, json={"offscreen": False})
    assert r.status_code == 200
    assert _system(fake).startswith("You help a game master start the next scene")


def test_controls_reach_the_prompt(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, json={"focus_refs": [MAP], "time_mode": "anchor",
                                 "time_anchor_ref": CORONATION,
                                 "time_anchor_relation": "before"})
    assert r.status_code == 200
    system = _system(fake)
    assert "Focus drivers: thread:mara-s-map = Mara's map." in system
    assert "Anchor every suggestion to event:the-coronation" in system


# ---- refusals: every one before a run is reserved ------------------------------


def test_must_and_avoid_overlap_is_400(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, json={"must_refs": [MAP], "avoid_refs": [MAP]})
    _refused(r, 400, "must_avoid")
    assert fake.calls == 0
    assert _runs(client, cid) == []


def test_must_cap_and_kind_are_400(client):
    cid = _campaign(client)
    _fake(client)
    _refused(_post(client, cid, json={"must_refs": [MAP, LEDGER, OATH, "thread:a"]}),
             400, "must_cap")
    _refused(_post(client, cid, json={"must_refs": [CORONATION]}), 400, "must_kind")


def test_anchor_rules_are_400(client):
    cid = _campaign(client)
    _fake(client)
    _refused(_post(client, cid, json={"time_mode": "anchor"}), 400, "anchor_missing")
    _refused(_post(client, cid, json={"time_mode": "near", "time_anchor_ref": CORONATION}),
             400, "anchor_without_mode")
    _refused(_post(client, cid, json={"time_mode": "anchor", "time_anchor_ref": MAP}),
             400, "anchor_kind")


def test_an_unknown_mode_or_relation_is_422(client):
    cid = _campaign(client)
    _fake(client)
    assert _post(client, cid, json={"time_mode": "soon"}).status_code == 422
    assert _post(client, cid, json={"time_mode": "anchor", "time_anchor_ref": CORONATION,
                                    "time_anchor_relation": "around"}).status_code == 422


def test_month_only_anchor_takes_only_on(client):
    cid = _campaign(client)
    aid, _ = overlay.create_character(cid, "Winifred")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "--06")
    [ref] = [a["ref"] for a in suggest.build_snapshot(cid)["anchors"]
             if a["ref"].startswith(f"birthday:characters:{aid}:month:")]
    _fake(client)
    body = {"time_mode": "anchor", "time_anchor_ref": ref}
    _refused(_post(client, cid, json={**body, "time_anchor_relation": "before"}),
             400, "anchor_relation")
    assert _post(client, cid, json={**body, "time_anchor_relation": "on"}).status_code == 200


def test_stale_refs_are_409(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, json={"focus_refs": ["thread:ghost"], "time_mode": "anchor",
                                 "time_anchor_ref": "event:gone"})
    assert r.status_code == 409
    assert r.json()["kind"] == "stale_drivers"
    assert r.json()["refs"] == ["thread:ghost", "event:gone"]
    assert fake.calls == 0
    assert _runs(client, cid) == []


def test_a_passed_event_anchor_is_stale(client):
    cid = _campaign(client)
    eid = events.create(cid, "The debt", "2026-05-01")
    _fake(client)
    r = _post(client, cid, json={"time_mode": "anchor", "time_anchor_ref": f"event:{eid}"})
    assert r.status_code == 409
    assert r.json()["refs"] == [f"event:{eid}"]


def test_missing_connection_is_told_first(client):
    cid = _campaign(client, key=False)
    _fake(client)
    r = _post(client, cid, json={"must_refs": [MAP], "avoid_refs": [MAP],
                                 "focus_refs": ["thread:ghost"]})
    assert r.status_code == 409
    assert r.json()["kind"] == "missing_key"


# ---- canonicalization -----------------------------------------------------------


def test_an_alias_source_focus_is_canonicalized(client):
    cid = _campaign(client)
    merged = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                         json={"ref": MAP, "to": LEDGER})
    assert merged.status_code == 200, merged.text
    fake = _fake(client)
    r = _post(client, cid, json={"focus_refs": [MAP]})
    assert r.status_code == 200
    assert "Focus drivers: thread:find-the-ledger = Find the ledger." in _system(fake)
    _refused(_post(client, cid, json={"must_refs": [MAP], "avoid_refs": [LEDGER]}),
             400, "must_avoid")


def test_a_dangling_alias_source_is_its_own_driver(client):
    cid = _campaign(client)
    continuity_doc.put_alias(cid, MAP, {"to": "thread:gone", "created": "", "source": "manual",
                                        "note": ""})
    fake = _fake(client)
    r = _post(client, cid, json={"focus_refs": [MAP]})
    assert r.status_code == 200
    assert "Focus drivers: thread:mara-s-map = Mara's map." in _system(fake)


# ---- precedence -------------------------------------------------------------------


def test_precedence_must_over_focus(client):
    cid = _campaign(client)
    fake = _fake(client)
    r = _post(client, cid, json={"focus_refs": [MAP], "must_refs": [MAP]})
    assert r.status_code == 200
    system = _system(fake)
    assert "Must-include drivers: thread:mara-s-map = Mara's map." in system
    assert "Focus drivers:" not in system


def test_precedence_avoid_over_focus(client):
    cid = _campaign(client)
    fake = _fake(client)
    started = client.post(_url(cid), json={"focus_refs": [MAP], "avoid_refs": [MAP]})
    assert started.status_code == 202
    assert drafts.settle(client, started).status_code == 200
    system = _system(fake)
    assert "Avoid drivers: thread:mara-s-map = Mara's map." in system
    assert "Focus drivers:" not in system


def test_the_anchor_beats_avoid(client):
    cid = _campaign(client)
    fake = _fake(client)
    started = client.post(_url(cid), json={"avoid_refs": [CORONATION], "time_mode": "anchor",
                                           "time_anchor_ref": CORONATION})
    assert started.status_code == 202
    landed = drafts.settle(client, started)
    assert landed.status_code == 200
    assert "Avoid drivers:" not in _system(fake)
    rows = landed.json()["suggestions"]
    assert rows and all(s["avoided"] == [] for s in rows)
    assert all(s["time_anchor"]["ref"] == CORONATION for s in rows)


# ---- the payload ------------------------------------------------------------------


def test_payload_carries_resolved_provenance(client):
    cid = _campaign(client)
    _fake(client, json.dumps({"suggestions": [{
        "title": "Before the crown", "premise": "Mara counts the days.", "cast": [],
        "location": "", "date": "2026-05-12",
        "drivers": [{"ref": MAP, "action": "advance"},
                    {"ref": "commitment:ghost", "action": "address"}],
        "time_anchor": {"ref": CORONATION, "relation": "before"}}]}))
    r = _post(client, cid)
    assert r.status_code == 200
    [s] = r.json()["suggestions"]
    assert s["drivers"] == [
        {"ref": MAP, "kind": "thread", "action": "advance", "label": "Mara's map"},
        {"ref": CORONATION, "kind": "event", "action": "anchor", "label": "The coronation"}]
    assert s["time_anchor"]["label"] == "The coronation"
    assert s["time_anchor"]["relation"] == "before"
    assert s["date"] == "2026-05-12"
    assert s["date_rejected"] is False
    assert s["unmet_must"] == [] and s["avoided"] == []
    # the route's own shapes are unchanged
    assert s["cast"] == [] and s["location"] is None


def test_payload_uses_the_captured_index(client, monkeypatch):
    cid = _campaign(client)
    _fake(client)
    counts: dict[str, int] = {}

    def recorder(module, name):
        real = getattr(module, name)

        def record(*args, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, record)

    recorder(continuity_drivers, "snapshot")
    recorder(pressure, "build")
    r = _post(client, cid, json={"focus_refs": [MAP]})
    assert r.status_code == 200
    first = r.json()["suggestions"][0]
    assert first["drivers"] == [{"ref": MAP, "kind": "thread", "action": "advance",
                                 "label": "Mara's map"}]
    # the route's one `build_snapshot`; a payload that re-read would make it 2
    assert counts == {"snapshot": 1, "build": 1}


def test_controls_survive_a_raising_plugin(client, tmp_path):
    cid = _campaign(client)
    _break_calendar(cid, tmp_path)
    _fake(client)
    r = _post(client, cid, json={"time_mode": "anchor", "time_anchor_ref": CORONATION})
    assert r.status_code == 409
    assert r.json()["kind"] == "stale_drivers"
    assert r.json()["refs"] == [CORONATION]
    assert _runs(client, cid) == []

    started = client.post(_url(cid), json={"focus_refs": [MAP]})
    assert started.status_code == 202
    landed = drafts.settle(client, started)
    assert landed.status_code == 200
    rows = landed.json()["suggestions"]
    assert [s["title"] for s in rows] == ["The gate at dusk", "The ledger room"]
    assert all(s["date"] == "" and s["date_rejected"] is False for s in rows)
    assert landed.json()["next_date"] == ""


def test_routing_task_is_still_suggestions(client):
    cid = _campaign(client)
    _fake(client)
    assert _post(client, cid, json={"focus_refs": [MAP]}).status_code == 200
    assert [row.get("task") for row in usage.calls(campaign=cid)] == ["suggestions"]
