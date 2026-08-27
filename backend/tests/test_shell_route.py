"""GET /api/shell — the nav rail's badges.

The rail renders beside every page and refetches on every navigation, so this
route's contract is mostly about what it *refuses* to answer. Three things are
worth holding to the code rather than to a docstring:

  * an unavailable count is ``None`` and never ``0`` -- the rail draws no tail
    for one and draws ``0`` for the other, so collapsing them would make a
    campaign with nothing to do indistinguishable from one nobody measured;
  * there is no money in the payload, because the figure the design asked for
    is backed by an all-time ledger scan that ``usage.lifetime_since`` reserves
    for the all-time view;
  * an id that does not resolve is ``campaign: null`` and HTTP 200, because the
    rail asks with an id the browser remembered and a deleted campaign is an
    ordinary state rather than a failed request.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def campaign(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    return client.post("/api/campaigns",
                       json={"name": "A Long Run", "world": wid}).json()["id"]


def _shell(client, cid: str | None = None) -> dict:
    r = client.get("/api/shell", params={"campaign": cid} if cid else None)
    assert r.status_code == 200, r.text
    return r.json()


def test_no_campaign_asked_for_answers_null(client):
    body = _shell(client)
    assert body["campaign"] is None
    assert body["campaigns"] == 0


def test_unknown_campaign_is_null_not_404(client):
    """The rail asks with a remembered id, which may name a deleted campaign.

    A 404 would make the client unable to tell "this campaign is gone" from
    "the request failed" -- and those must be told apart, because only the
    first may clear the remembered id.
    """
    r = client.get("/api/shell", params={"campaign": "no-such-campaign"})
    assert r.status_code == 200
    assert r.json()["campaign"] is None


def test_campaigns_counts_what_the_shelf_lists(client, campaign):
    assert _shell(client)["campaigns"] == 1


def test_open_scenes_come_off_frontmatter(client, campaign):
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    block = _shell(client, campaign)["campaign"]
    assert block["scenes"] == 1
    assert [s["sid"] for s in block["open"]] == [sid]
    assert block["open"][0]["title"] == "The Lower Step"


def test_an_absorbed_scene_is_not_open(client, campaign):
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    store.scenes.mark_absorbed(campaign, sid, "It ended.", "It ended, at length.")
    block = _shell(client, campaign)["campaign"]
    assert block["open"] == []
    # ...but it is still a scene. The two counts answer different questions and
    # a rail that conflated them would say a finished campaign had no scenes.
    assert block["scenes"] == 1


def test_turns_is_null_rather_than_a_number_this_slice(client, campaign):
    """Scene frontmatter carries no turn count.

    The rail renders no tail for ``None``. Answering with a number derived from
    something else would be wrong for exactly the oldest scenes -- the ones
    written before whatever field it was derived from existed.
    """
    client.post(f"/api/campaigns/{campaign}/scenes", json={"title": "The Lower Step"})
    assert _shell(client, campaign)["campaign"]["open"][0]["turns"] is None


@pytest.mark.parametrize("field", ["images_undescribed"])
def test_deferred_counts_are_null_not_zero(client, campaign, field):
    """A count this slice does not compute says so.

    Zero is an answer: it means "nothing is waiting". These fields mean "nobody
    asked", and the rail draws them differently -- no tail at all, rather than
    a tail reading 0.
    """
    assert _shell(client, campaign)["campaign"][field] is None


def test_unreviewed_counts_proposals_across_pending_reviews(client, campaign):
    """A scene holding a review is a scene holding the world back.

    Counted off the `<sid>.review.json` sidecars beside the transcripts -- a
    directory listing plus one small read each, and normally there is at most
    one. The transcripts themselves are never opened.
    """
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    store.pending_reviews.publish(
        campaign, sid, "gen-1",
        {"edits": [{"kind": "fact"}, {"kind": "plot"}, {"kind": "commitment"}]},
        {"posts": 0})
    block = _shell(client, campaign)["campaign"]
    assert block["unreviewed"] == 3
    assert block["pending"] == [{"sid": sid, "proposals": 3}]


def test_zero_pending_reviews_is_zero_not_null(client, campaign):
    """Nothing waiting is an answer, and the hub says so in words.

    Distinct from the fields that report `None`: those mean nobody computed it,
    and the rail draws them differently.
    """
    block = _shell(client, campaign)["campaign"]
    assert block["unreviewed"] == 0
    assert block["pending"] == []


def test_an_unreadable_review_sidecar_is_skipped_not_fatal(client, campaign):
    """This feeds chrome. One malformed record must not take navigation down."""
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    store.scenes.paths._review_path(campaign, sid).write_text("{ not json",
                                                              encoding="utf-8")
    assert _shell(client, campaign)["campaign"]["unreviewed"] == 0


def test_todo_counts_the_library_with_no_campaign_open(client):
    """A zero, not a null, and the difference is the point.

    Every chore used to be about a campaign, so outside one the tail was `null`
    -- no tail at all, which is the cost rule's answer for a number nobody can
    answer cheaply. The library chores (an undescribed image backlog, a world
    whose cast has no taglines) DO have an answer before a campaign is chosen,
    so the badge now reports it. An empty store has none of them, which is a
    real zero.
    """
    assert _shell(client)["todo"] == 0


def test_sheets_is_null_when_no_module_is_bound(client, campaign):
    """"No mechanics bound" is legal, and is not "0 of 0 sheeted"."""
    assert _shell(client, campaign)["campaign"]["sheets"] is None


def test_no_money_anywhere_in_the_payload(client, campaign):
    """The rail carries no spend figure, and this is what keeps it that way.

    The figure the design asked for is an all-time ledger rollup, which
    ``store.usage.lifetime_since`` documents as backing the all-time view "and
    nothing on the play path". The rail is the play path, on every navigation.
    A later slice adds a maintained aggregate; until then the row has no tail.
    """
    body = _shell(client, campaign)
    flat = repr(body)
    for word in ("cost_usd", "estimated_usd", "modelled_usd", "spend",
                 "unpriced"):
        assert word not in flat, f"{word} leaked into the shell payload"


def test_library_is_not_answered_here(client):
    """One manifest, one language.

    The number of library sections lives in `librarySections.ts`. Answering it
    from Python as well would be two lists with nothing holding them level, and
    a seventh section would ship a badge of six.
    """
    assert "library" not in _shell(client)


def test_world_name_is_resolved_not_echoed(client, campaign):
    assert _shell(client, campaign)["campaign"]["world_name"] == "Saltmarch"


def test_an_unreadable_campaign_directory_does_not_break_the_read(client, campaign, tmp_path):
    """A stray directory under campaigns/ is skipped, not fatal.

    The rail is chrome: it must keep rendering navigation even when one record
    in the store is malformed, or a single bad directory takes the whole app's
    navigation with it.
    """
    (tmp_path / "campaigns" / "not-a-campaign").mkdir(parents=True, exist_ok=True)
    assert _shell(client)["campaigns"] == 1

