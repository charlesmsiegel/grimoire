"""GET /api/shell — the nav rail's badges.

The rail renders beside every page and refetches on every navigation, so this
route's contract is mostly about what it *refuses* to answer. Three things are
worth holding to the code rather than to a docstring:

  * an unavailable count is ``None`` and never ``0`` -- the rail draws no tail
    for one and draws ``0`` for the other, so collapsing them would make a
    campaign with nothing to do indistinguishable from one nobody measured;
  * the money in the payload is the ALL-TIME rollup and never a bounded window
    wearing its name, and it arrives as three columns that are never summed --
    with ``partial`` saying when none of them can be believed;
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


def test_turns_is_zero_for_a_scene_with_no_posts_yet(client, campaign):
    """A brand-new scene has genuinely made no model replies yet.

    Distinct from the None cases below: nobody failed to answer this, the
    honest answer is zero, and the rail draws a real zero differently from no
    tail at all.
    """
    client.post(f"/api/campaigns/{campaign}/scenes", json={"title": "The Lower Step"})
    assert _shell(client, campaign)["campaign"]["open"][0]["turns"] == 0


def test_turns_counts_model_replies_not_player_posts(client, campaign):
    """The count is of REPLIES, off `scenes._model_blocks` -- the same walk
    `turn_sizes` is expressed in, so a player's own posts do not inflate it."""
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    store.scenes.append_message(campaign, sid, "user", "I open the door.", speaker="You")
    store.scenes.append_message(campaign, sid, "assistant", "The door creaks open.")
    store.scenes.append_message(campaign, sid, "assistant", "A shadow moves inside.")
    assert _shell(client, campaign)["campaign"]["open"][0]["turns"] == 2


def test_turns_is_zero_for_an_all_player_transcript(client, campaign):
    """A real zero, not the None a broken read would report -- a scene held
    open while waiting on its first reply is not a scene nobody could open."""
    sid = client.post(f"/api/campaigns/{campaign}/scenes",
                      json={"title": "The Lower Step"}).json()["id"]
    store.scenes.append_message(campaign, sid, "user", "I look around.", speaker="You")
    assert _shell(client, campaign)["campaign"]["open"][0]["turns"] == 0


def test_an_unreadable_transcript_reports_turns_as_none_not_zero(client, campaign,
                                                                  monkeypatch):
    """"Nobody could read this" and "this scene has no replies" are different
    answers. Claiming 0 for the first would tell the reader a scene is caught
    up when nobody actually checked."""
    client.post(f"/api/campaigns/{campaign}/scenes", json={"title": "The Lower Step"})

    def explode(cid, s):
        raise OSError("boom")

    monkeypatch.setattr(store.scenes.read, "read_scene", explode)
    assert _shell(client, campaign)["campaign"]["open"][0]["turns"] is None


def test_images_undescribed_is_null_when_the_world_cannot_be_read(client, campaign,
                                                                    monkeypatch):
    """Same error contract as `todo._world_describe_counts`: an unreadable
    world directory must not 500 `/api/shell`, and must not be misreported as
    a caught-up backlog either."""
    def explode(root, base="characters"):
        raise OSError("boom")

    monkeypatch.setattr(store.image_descriptions, "undescribed_count", explode)
    assert _shell(client, campaign)["campaign"]["images_undescribed"] is None


def _png() -> bytes:
    """A real 2x2 PNG. `assets.put_image` sniffs the bytes for the extension."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGNgYGD4"
        "z8DAwMDAAAANHQEDeuUOPQAAAABJRU5ErkJggg==")


def test_images_undescribed_reflects_the_worlds_gallery(client, campaign):
    """Not null once there is a real answer -- the count this slice was
    waiting on `image_descriptions.undescribed_count` to compute.

    Off the campaign's own bound world, which the block's new `world` field
    (added alongside `world_name` so the hub can link to it) names directly.
    """
    wid = _shell(client, campaign)["campaign"]["world"]
    chid = client.post(f"/api/worlds/{wid}/characters",
                       json={"name": "Winifred"}).json()["character"]
    vid = client.get(f"/api/worlds/{wid}/characters/{chid}").json()["meta"]["default_version"]
    root = store.worlds.paths.world_root(wid)
    store.assets.put_image(root, chid, vid, "gallery_1", _png(), "png")

    assert _shell(client, campaign)["campaign"]["images_undescribed"] == 1


def test_world_id_travels_alongside_its_name(client, campaign):
    """`world_name` alone is a label; the hub needs the id to link at the
    world's own pages, so it rides beside it the way `CampaignMeta` pairs
    `world`/`world_name` already."""
    wid = store.campaigns.read.read_campaign(campaign)["meta"]["world"]
    assert _shell(client, campaign)["campaign"]["world"] == wid


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


def test_money_is_three_columns_and_never_a_total(client, campaign):
    """The rail's spend figure, and the rule it has to arrive under.

    Three separate claims about money. A payload carrying their sum -- under
    any name -- is the one thing this route may not ship, because a number
    nobody can decompose is a number that gets quoted.
    """
    money = _shell(client, campaign)["campaign"]["money"]
    for column in ("cost_usd", "estimated_usd", "modelled_usd"):
        assert column in money
    assert "total_usd" not in money and "spend_usd" not in money


def test_a_campaign_that_has_run_nothing_reports_a_real_zero(client, campaign):
    """`partial` is "nobody could count", and this is not that.

    A fresh campaign has spent nothing, and that is a measurement. The flag is
    reserved for an aggregate that could not be brought up to date at all --
    which is the one case the rail must draw as silence rather than `$0.00`.
    """
    money = _shell(client, campaign)["campaign"]["money"]
    assert money["partial"] is False
    assert money["calls"] == 0
    assert money["cost_usd"] == 0


def test_the_figure_is_all_time_rather_than_a_bounded_window(client, campaign):
    """The whole reason this waited for `store.usage_rollup`.

    A 30-day window would have been cheap and would have put the same
    unlabelled figure on the rail meaning something else than the page it links
    to. A row older than any window this route could have chosen still counts.
    """
    store.usage.record(task="chat", campaign=campaign, model="realm/opus",
                       cost_usd=2.50, ts="2019-01-01T00:00:00Z")

    money = _shell(client, campaign)["campaign"]["money"]
    assert money["cost_usd"] == 2.50
    assert money["calls"] == 1


def test_a_subscription_billed_campaign_reports_no_spend_but_says_why(
        client, campaign):
    """The case the rail's tail has to stay silent for.

    Everything billed to a subscription: real usage, and no money anybody paid.
    `cost_usd` is a true zero, `estimated_usd` carries the figure, and a tail
    that rendered the first as `$0.00` would call a played campaign free.
    """
    store.usage.record(task="chat", campaign=campaign, model="realm/opus",
                       cost_usd=1.25, cost_basis="equivalent",
                       ts="2026-08-01T00:00:00Z")

    money = _shell(client, campaign)["campaign"]["money"]
    assert money["cost_usd"] == 0
    assert money["estimated_usd"] == 1.25
    assert money["subscription_calls"] == 1


def test_an_unpriced_call_is_counted_rather_than_costed_as_zero(client, campaign):
    store.usage.record(task="chat", campaign=campaign, model="realm/opus",
                       ts="2026-08-01T00:00:00Z")

    money = _shell(client, campaign)["campaign"]["money"]
    assert money["unpriced_calls"] == 1
    assert money["cost_usd"] == 0


def test_another_campaigns_spend_is_not_this_ones(client, campaign):
    other = client.post("/api/campaigns",
                        json={"name": "Elsewhere",
                              "world": _shell(client, campaign)["campaign"]["world"]}
                        ).json()["id"]
    store.usage.record(task="chat", campaign=other, model="realm/opus",
                       cost_usd=9.99, ts="2026-08-01T00:00:00Z")

    assert _shell(client, campaign)["campaign"]["money"]["cost_usd"] == 0
    assert _shell(client, other)["campaign"]["money"]["cost_usd"] == 9.99


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

