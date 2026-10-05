"""One live background run per campaign, over the HTTP surface (slice D task 7).

The registry half is in `test_runs_registry.py`; this file holds what needs an
app: the campaign run routes finding an adopted run by its attempt, the
campaign-background reservation refusing during a storage move, and a storage
move dropping the refs a trigger left pending.
"""

from __future__ import annotations

from grimoire.routes import runs

RECONCILE = "continuity-reconcile"


def test_a_refresh_during_a_live_sweep_adopts_it_under_its_own_attempt(client):
    """Review Focus 4: a Refresh pressed while the automatic sweep after End
    Scene is still live adopts it, and its own attempt id finds it -- through
    the registry and through `GET /campaigns/{cid}/runs?attempt=` -- so a lost
    202 can be re-found."""
    app = client.app
    started = runs.reserve_campaign_background(app, "saltmarch", RECONCILE)
    assert started is not None
    live, fresh = started
    assert fresh is True and live.attempt_id is None

    adopted = runs.reserve_campaign_background(app, "saltmarch", RECONCILE, "a1")
    assert adopted is not None
    run, fresh = adopted
    assert run is live and fresh is False
    assert run.adopted_attempts == ["a1"]
    assert app.state.runs.for_attempt(runs.campaign_subject("saltmarch"), "a1") is live

    r = client.get("/api/campaigns/saltmarch/runs", params={"attempt": "a1"})
    assert r.status_code == 200, r.text
    assert [found["id"] for found in r.json()["runs"]] == [live.id]
    live.finish("landed")
    app.state.runs.retire(live.id)


def test_two_draft_runs_are_each_found_only_by_their_own_attempt(client):
    """The `_subject_runs` change must not widen the attempt filter: drafts
    overlap on one subject and each is found by its own attempt alone."""
    app = client.app
    subject = runs.campaign_subject("saltmarch")
    one, _ = runs.reserve_draft(app, subject, "tagline", "d1")
    two, _ = runs.reserve_draft(app, subject, "tagline", "d2")
    try:
        for attempt, run in (("d1", one), ("d2", two)):
            r = client.get("/api/campaigns/saltmarch/runs", params={"attempt": attempt})
            assert r.status_code == 200, r.text
            assert [found["id"] for found in r.json()["runs"]] == [run.id]
    finally:
        for run in (one, two):
            run.finish("landed")
            app.state.runs.retire(run.id)


def test_reserve_campaign_background_maps_a_store_move_to_none(client):
    app = client.app
    with app.state.runs.hold_still():
        assert runs.reserve_campaign_background(app, "saltmarch", RECONCILE) is None


def test_a_storage_move_drops_pending_touched(client, tmp_path):
    app = client.app
    subject = runs.campaign_subject("saltmarch")
    app.state.runs.pend_touched(subject, ["thread:mara-map", "commitment:mara-oath"])

    r = client.put("/api/config/data-dir", json={"data_dir": str(tmp_path / "moved")})

    assert r.status_code == 200, r.text
    assert app.state.runs.take_touched(subject) == set()
