"""The scheduled-events HTTP surface (#101): CRUD, the fire stamp an advance
writes, and the undo for it."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import FakeOpenRouter


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _events(client, cid):
    return client.get(f"/api/campaigns/{cid}/events").json()["events"]


def test_a_fresh_campaign_has_no_events(client):
    cid = _campaign(client)
    assert _events(client, cid) == []


def test_an_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/events").status_code == 404


def test_events_is_not_captured_by_the_generic_kind_route(client):
    """`/campaigns/{cid}/{kind}` would swallow this segment if it registered
    first — `test_route_order.py` holds the rule, this proves the URL works."""
    cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/events").status_code == 200


def test_create_read_edit_delete(client):
    cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/events",
                    json={"name": "Coronation", "date": "2026-5-9", "note": "In the old hall."})
    eid = r.json()["id"]
    row = _events(client, cid)[0]
    assert row["name"] == "Coronation" and row["date"] == "2026-05-09"
    assert row["friendly"] and row["fired"] is None

    assert client.put(f"/api/campaigns/{cid}/events/{eid}",
                      json={"note": "In the new hall."}).status_code == 200
    assert _events(client, cid)[0]["note"] == "In the new hall."

    assert client.delete(f"/api/campaigns/{cid}/events/{eid}").status_code == 200
    assert _events(client, cid) == []


def test_a_date_this_calendar_cannot_read_is_a_400_that_says_so(client):
    """The calendar's own sentence, not a 422 naming a field."""
    cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/events",
                    json={"name": "Nonsense", "date": "the third of never"})
    assert r.status_code == 400 and r.json()["detail"]


def test_editing_an_unknown_event_is_404(client):
    cid = _campaign(client)
    assert client.put(f"/api/campaigns/{cid}/events/nope", json={"note": "x"}).status_code == 404
    assert client.delete(f"/api/campaigns/{cid}/events/nope").status_code == 404
    assert client.post(f"/api/campaigns/{cid}/events/nope/unfire").status_code == 404


def test_a_corrupt_file_is_a_400_rather_than_a_silent_overwrite(client):
    cid = _campaign(client)
    store.events._path(cid).write_text("{not json", encoding="utf-8")
    r = client.post(f"/api/campaigns/{cid}/events", json={"name": "x", "date": "2026-05-09"})
    assert r.status_code == 400
    # ...and the reader's file is still there to be repaired.
    assert store.events._path(cid).read_text(encoding="utf-8") == "{not json"


def test_a_campaign_with_no_calendar_still_lists_its_events(client):
    """Unlabelled and in id order, but present: the row is the only place the
    reader can see the configuration that broke."""
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Coronation", "date": "2026-05-09"})
    (store.campaigns.campaign_root(cid) / "calendar.json").write_text(
        '{"primary": {"provider": "no-such-calendar"}}', encoding="utf-8")
    rows = _events(client, cid)
    assert [r["name"] for r in rows] == ["Coronation"] and rows[0]["friendly"] == ""


def test_an_advance_fires_and_reports_it(client):
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-05-01", "reason": "start"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Coronation", "date": "2026-05-09"})
    r = client.post(f"/api/campaigns/{cid}/advance",
                    json={"days": 30, "reason": "a month passes"}).json()
    assert [e["name"] for e in r["digest"]["events"]] == ["Coronation"]
    assert [e["id"] for e in r["fired"]] == ["coronation"]
    assert _events(client, cid)[0]["fired"]["moment"] == "2026-05-31"


def test_a_preview_lists_without_firing(client):
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-05-01", "reason": "start"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Coronation", "date": "2026-05-09"})
    r = client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 30}).json()
    assert [e["name"] for e in r["digest"]["events"]] == ["Coronation"]
    assert _events(client, cid)[0]["fired"] is None


def test_unfire_puts_it_back_on_the_upcoming_list(client):
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-05-01", "reason": "start"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Coronation", "date": "2026-05-09"})
    client.post(f"/api/campaigns/{cid}/advance", json={"days": 30, "reason": "a month passes"})
    assert client.post(f"/api/campaigns/{cid}/events/coronation/unfire").status_code == 200
    assert _events(client, cid)[0]["fired"] is None


def test_the_digest_ages_the_ledger_against_the_target(client):
    """#103 through the same response #100 already returns: what a skip leaves
    overdue is the question a reader has before confirming it."""
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The oath"}).json()["id"]
    # The id a scene's first date gives it: dating a scene stamps the date into
    # its filename, and the beat has to name the scene that exists afterwards.
    sid = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                     json={"datetime": "2026-05-01"}).json()["id"]
    store.commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                                   "2026-05-20", "Mara swore to repay it.", sid)
    r = client.post(f"/api/campaigns/{cid}/advance/preview", json={"days": 60}).json()
    owed = r["digest"]["commitments"]
    assert owed[0]["aging"]["state"] == "overdue"
    assert r["digest"]["aging"]["overdue"] == 1


def test_the_ledger_carries_the_same_aging(client):
    cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The oath"}).json()["id"]
    # The id a scene's first date gives it: dating a scene stamps the date into
    # its filename, and the beat has to name the scene that exists afterwards.
    sid = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime",
                     json={"datetime": "2026-05-01"}).json()["id"]
    store.commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                                   "2026-05-20", "Mara swore to repay it.", sid)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-06-01", "reason": "a month"})
    ledger = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert ledger["commitments"][0]["aging"] == {"state": "overdue", "days_since": 31,
                                                 "days_over": 12, "due_in": None}
    assert ledger["stale_after_days"] == store.calendars.STALE_AFTER_DAYS


def test_the_staleness_threshold_survives_a_calendar_save(client):
    """A client that sends the field keeps it; the store's default answers a
    zero, so a client that does not send it cannot reset the campaign to a
    threshold nobody chose."""
    cid = _campaign(client)
    cfg = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert cfg["stale_after_days"] == store.calendars.STALE_AFTER_DAYS
    client.put(f"/api/campaigns/{cid}/calendar", json={**cfg, "stale_after_days": 7})
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["stale_after_days"] == 7
    client.put(f"/api/campaigns/{cid}/calendar",
               json={"primary": cfg["primary"], "secondary": None, "confirmed": True})
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["stale_after_days"] \
        == store.calendars.STALE_AFTER_DAYS


def test_the_list_says_which_events_the_clock_has_gone_by(client):
    """A day already behind the campaign's present that no move ever fired.
    Nothing else in the app would say so, and no advance can reach it."""
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-06-01", "reason": "start"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Mistyped", "date": "2026-05-09"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Ahead", "date": "2026-07-01"})
    body = client.get(f"/api/campaigns/{cid}/events").json()
    assert body["now"] == "2026-06-01" and body["friendly"]
    assert {e["name"]: e["passed"] for e in body["events"]} == {"Mistyped": True, "Ahead": False}


def test_a_campaign_with_no_clock_marks_nothing_as_passed(client):
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Someday", "date": "2026-05-09"})
    body = client.get(f"/api/campaigns/{cid}/events").json()
    assert body["now"] == "" and body["events"][0]["passed"] is False


def test_an_event_can_be_re_dated_through_the_route(client):
    """The repair the `passed` label points at — and the only caller the edit
    endpoint has, which is the point of it existing."""
    cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/advance", json={"to": "2026-06-01", "reason": "start"})
    client.post(f"/api/campaigns/{cid}/events", json={"name": "Mistyped", "date": "2026-05-09"})
    assert client.put(f"/api/campaigns/{cid}/events/mistyped",
                      json={"date": "2026-06-10"}).status_code == 200
    row = _events(client, cid)[0]
    assert row["date"] == "2026-06-10" and row["passed"] is False
