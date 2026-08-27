"""GET /api/todo — everything the app noticed, and the ignore that silences one.

Two properties are the whole feature, and both are the kind that rot quietly:

  * a chore at zero is not in the list, so a label's number is always the one
    this request computed;
  * an ignored chore is counted nowhere, and is still there to be restored.

Neither survives a refactor on its own, so both are held here.
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
                       json={"name": "A Long Run", "world": wid}).json()["id"], wid


def _todo(client, cid: str) -> dict:
    r = client.get("/api/todo", params={"campaign": cid})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_clean_campaign_has_no_chores(client, campaign):
    cid, _ = campaign
    body = _todo(client, cid)
    assert body["chores"] == []
    assert body["count"] == 0


def test_a_chore_at_zero_leaves_the_list(client, campaign):
    """The property the whole page rests on.

    A list that can go stale teaches the reader to distrust it, and then the one
    entry that mattered is the one they scroll past. Nothing is stored: the
    chore is derived, so it disappears the moment its cause does.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    ids = [c["id"] for c in _todo(client, cid)["chores"]]
    assert "open-scenes" in ids

    # Absorb one, and the chore has nothing left to say.
    scenes = store.scenes.read.list_scenes(cid)
    store.scenes.mark_absorbed(cid, scenes[0]["id"], "It ended.", "It ended.")
    assert "open-scenes" not in [c["id"] for c in _todo(client, cid)["chores"]]


def test_a_chore_carries_why_it_matters_not_just_a_count(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["why"]
    assert chore["fix"]


def test_ignoring_moves_a_chore_and_stops_counting_it(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert _todo(client, cid)["count"] == 1

    r = client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    assert r.status_code == 200

    body = _todo(client, cid)
    assert body["count"] == 0
    assert [c["id"] for c in body["chores"]] == []
    # ...and it is not gone. A dismissal that cannot be taken back is one
    # nobody dares make.
    assert [c["id"] for c in body["ignored"]] == ["open-scenes"]


def test_restoring_puts_it_back(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": False})
    assert _todo(client, cid)["count"] == 1


def test_the_shell_badge_does_not_count_an_ignored_chore(client, campaign):
    """The rail's number is what the reader still cares about.

    An ignore that silenced the page but left the badge lit would be worse than
    no ignore at all: the reader would have told the app to stop asking and it
    would still be asking, in the one place they cannot close.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] == 1
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] == 0


def test_an_unknown_chore_id_is_refused(client):
    """An ignore set that accumulates ids nothing emits grows forever and
    silences things nobody can name."""
    r = client.put("/api/todo/not-a-chore/ignored", json={"ignored": True})
    assert r.status_code == 400


def test_a_malformed_ignore_file_is_an_empty_set_not_an_error(client, campaign, tmp_path):
    """This decides what a list SHOWS. A broken judgement file must not stop the
    app telling the user what is waiting."""
    cid, _ = campaign
    (tmp_path / "chores.json").write_text("{ not json", encoding="utf-8")
    assert store.chores.ignored() == set()
    assert _todo(client, cid)["count"] == 0


def test_no_campaign_asked_for_answers_an_empty_list(client):
    r = client.get("/api/todo")
    assert r.status_code == 200
    assert r.json() == {"chores": [], "ignored": [], "count": 0}


def _items(client, chore_id: str, cid: str) -> dict:
    r = client.get(f"/api/todo/{chore_id}/items", params={"campaign": cid})
    assert r.status_code == 200, r.text
    return r.json()


def test_expanding_a_chore_names_its_instances(client, campaign):
    """A count says how much is undone; it never says which.

    The whole reason to expand: "2 scenes are open at once" is a number, and
    the two titles are the thing a reader can act on.
    """
    cid, _ = campaign
    made = [client.post(f"/api/campaigns/{cid}/scenes",
                        json={"title": t}).json()["id"]
            for t in ("The Lower Step", "The Weir")]
    body = _items(client, "open-scenes", cid)
    assert body["total"] == 2
    assert {i["label"] for i in body["items"]} == {"The Lower Step", "The Weir"}
    # Each instance can be gone to, which is what makes the list worth opening
    # rather than reading.
    assert {i["fix"] for i in body["items"]} == {
        f"/campaigns/{cid}/scenes/{sid}" for sid in made}


def test_an_instance_carries_detail_not_just_a_name(client, campaign):
    """A list of bare names is the count again, spelled out."""
    cid, _ = campaign
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Lower Step"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Weir"})
    for item in _items(client, "open-scenes", cid)["items"]:
        assert item["detail"]


def test_the_counts_and_the_instances_agree(client, campaign):
    """Two computations of one fact, and the page shows them together -- a
    chore reading "2" that expands to three rows is worse than either."""
    cid, _ = campaign
    for t in ("A", "B", "C"):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": t})
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["n"] == _items(client, "open-scenes", cid)["total"]


def test_items_for_an_unknown_chore_are_refused(client, campaign):
    cid, _ = campaign
    assert client.get("/api/todo/not-a-chore/items",
                      params={"campaign": cid}).status_code == 400


def test_items_with_no_campaign_are_empty_rather_than_an_error(client):
    r = client.get("/api/todo/open-scenes/items")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "truncated": False}


def test_a_capped_list_reports_that_it_was_capped(client, campaign, monkeypatch):
    """A short list nobody labels reads as a complete one."""
    from grimoire.routes import todo as todo_routes
    monkeypatch.setattr(todo_routes, "ITEM_CAP", 2)
    cid, _ = campaign
    for t in ("A", "B", "C", "D"):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": t})
    body = _items(client, "open-scenes", cid)
    assert len(body["items"]) == 2
    assert body["total"] == 4
    assert body["truncated"] is True


# ---- the campaign is copy-on-write over its world (#248) ----
#
# These three are the bug this section exists to keep out, from its three
# sides. The chore walked the WORLD root, which gets all three wrong at once:
# it sees records the campaign deleted, and it cannot see the per-file sidecars
# that are where a played campaign's taglines and anchors actually live.

def _world_character(client, wid: str, name: str) -> str:
    return client.post(f"/api/worlds/{wid}/characters",
                       json={"name": name}).json()["character"]


def test_a_character_the_campaign_deleted_is_not_its_problem(client, campaign):
    """A chore about somebody who is not in the campaign is one the reader can
    neither act on nor dismiss."""
    cid, wid = campaign
    keep = _world_character(client, wid, "Mara Vance")
    gone = _world_character(client, wid, "Winifred Ash")
    store.overlay.add_deleted(cid, f"characters/{gone}")

    listed = {i["id"] for i in _items(client, "taglines", cid)["items"]}
    assert keep in listed
    assert gone not in listed


def test_a_tagline_written_campaign_side_counts(client, campaign):
    """`tagline.md` is a sidecar that overlays PER FILE.

    Reading the world's `character.md` frontmatter sees none of them, so every
    character whose tagline was written inside a campaign was reported as
    lacking one -- which, in a campaign that has been played, is most of them.
    """
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara Vance")
    assert aid in {i["id"] for i in _items(client, "taglines", cid)["items"]}

    store.taglines.write(store.campaigns.paths.campaign_root(cid), aid,
                         "Harbour clerk who counts what the tide leaves.")
    assert aid not in {i["id"] for i in _items(client, "taglines", cid)["items"]}


def test_a_voice_anchor_written_campaign_side_counts(client, campaign):
    """Same shape, and `set_voice_anchor` is the one writer -- it exists so
    nothing outside the overlay hands `voice_anchors` a raw campaign root."""
    cid, wid = campaign
    aid = _world_character(client, wid, "Mara Vance")
    assert aid in {i["id"] for i in _items(client, "anchors", cid)["items"]}

    store.overlay.set_voice_anchor(cid, aid, "Clipped. Counts aloud.")
    assert aid not in {i["id"] for i in _items(client, "anchors", cid)["items"]}


# --- The library's own chores, and the two numbers that must not drift -------
#
# These arrived with the world chores (an undescribed image backlog, a world
# whose cast has no taglines). Each of the three tests below holds a place where
# the same fact is now computed twice, cheaply and honestly, and the cheap one
# is what ships on the hot path.


def _png() -> bytes:
    """A real 2x2 PNG. `assets.put_image` sniffs the bytes for the extension."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGNgYGD4"
        "z8DAwMDAAAANHQEDeuUOPQAAAABJRU5ErkJggg==")


def _add_images(client, wid: str, chid: str, *names: str) -> None:
    """Undescribed gallery art on a character, written through the store.

    Through `assets.put_image` rather than the upload route: the route is
    multipart and this is setting up a backlog, not exercising the upload.
    """
    root = store.worlds.paths.world_root(wid)
    vid = client.get(f"/api/worlds/{wid}/characters/{chid}").json()["meta"]["default_version"]
    for n in names:
        store.assets.put_image(root, chid, vid, n, _png(), "png")


def _world_with_a_character(client, name: str = "Realm") -> tuple[str, str]:
    wid = client.post("/api/worlds", json={"name": name}).json()["id"]
    r = client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})
    assert r.status_code == 200, r.text
    return wid, r.json()["character"]


def test_world_chores_answer_with_no_campaign_open(client):
    """The point of the whole scope split.

    Every chore used to be about a campaign, so `/todo` outside one said "open
    a campaign first" -- which is exactly wrong just after importing a world,
    when its backlog is largest and no campaign exists yet.
    """
    _world_with_a_character(client)
    body = _todo(client, "")
    ids = {c["id"] for c in body["chores"]}
    assert "world-taglines" in ids
    assert body["count"] == len(body["chores"])
    assert all(c["scope"] in ("world", "library") for c in body["chores"])


def test_a_campaigns_own_world_is_reported_once_not_twice(client, campaign):
    """`world-taglines` covers the worlds the open campaign does NOT use.

    Its own world is already answered by `taglines`, over the effective
    copy-on-write roster -- the more accurate of the two, since it can see a
    tagline written campaign-side and a character the campaign deleted.
    Reporting it from both sides would double-count the character and let the
    two rows disagree.
    """
    cid, wid = campaign
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})
    body = _todo(client, cid)
    ids = {c["id"] for c in body["chores"]}
    assert "taglines" in ids
    assert "world-taglines" not in ids

    # A second world the campaign does not use is reported, from the world side.
    _world_with_a_character(client, "Elsewhere")
    ids = {c["id"] for c in _todo(client, cid)["chores"]}
    assert {"taglines", "world-taglines"} <= ids


def test_the_image_backlog_is_not_excluded_for_the_campaigns_world(client, campaign):
    """The deliberate asymmetry beside the test above.

    `world-describe` covers EVERY world including the open campaign's, because
    nothing else reports an image backlog -- so excluding it there would hide
    the backlog rather than de-duplicate it. This is the rule a later reader is
    most likely to "fix" into consistency with `world-taglines`.
    """
    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")

    ids = {c["id"] for c in _todo(client, cid)["chores"]}
    assert "world-describe" in ids


def test_the_badge_never_disagrees_with_the_page(client, campaign):
    """`badge_count` short-circuits; `live` counts. They must still agree.

    The rail reads the cheap one on every navigation and the page computes the
    real totals. A badge saying 4 over a page showing 5 is precisely the stale
    number this module is arranged to not have, arrived at from the other side.
    """
    from grimoire.routes import todo as todo_routes

    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")
    _world_with_a_character(client, "Elsewhere")

    for c in (cid, "", "no-such-campaign"):
        assert todo_routes.badge_count(c) == todo_routes.live(c)["count"], c


def test_a_world_that_cannot_be_read_does_not_break_the_badge(client, campaign,
                                                              monkeypatch):
    """The page skips an unreadable world. The badge has to skip the same one.

    `_world_describe_counts` wraps each world in a `try`, so a directory that
    has gone unreadable -- permissions, a store mid-sync, a folder replaced
    under the walk -- costs that world's backlog and nothing else. The badge
    takes the short-circuit path instead, and if that path let the `OSError`
    out it would not merely disagree with the page: the badge is computed for
    `/api/shell`, so every navigation would 500 over a world the page below it
    renders without.
    """
    from grimoire.routes import todo as todo_routes

    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1")
    bad_wid, _ = _world_with_a_character(client, "Unreadable")
    bad_root = store.worlds.paths.world_root(bad_wid)

    real = store.image_descriptions.has_undescribed

    def explode(root, base="characters"):
        if root == bad_root:
            raise PermissionError(f"cannot read {root}")
        return real(root, base)

    monkeypatch.setattr(store.image_descriptions, "has_undescribed", explode)

    # Still the readable world's backlog, and still the same number the page
    # would draw -- which `live` reaches without going through `explode`.
    assert todo_routes.badge_count(cid) == todo_routes.live(cid)["count"]


def test_expanding_a_world_chore_lists_worlds_not_images(client, campaign):
    """A row per image would be hundreds of rows and would hit `ITEM_CAP`.

    The world is the grain the fix is applied at -- the describe queue runs over
    a whole world from its cast page -- so it is the grain the reader gets.
    """
    cid, wid = campaign
    ch = client.post(f"/api/worlds/{wid}/characters",
                     json={"name": "Winifred"}).json()["character"]
    _add_images(client, wid, ch, "gallery_1", "gallery_2")

    r = client.get("/api/todo/world-describe/items", params={"campaign": cid})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [wid]
    assert items[0]["detail"] == "2 images"


def test_a_world_chore_expands_with_no_campaign_open(client):
    """The items route used to answer empty without a campaign. A library chore
    has instances regardless, and an expansion that silently returns nothing is
    a long way from the rule that caused it."""
    _world_with_a_character(client)
    r = client.get("/api/todo/world-taglines/items", params={"campaign": ""})
    assert r.status_code == 200, r.text
    labels = [i["label"] for i in r.json()["items"]]
    assert labels == ["Winifred"]


def test_a_world_chore_can_be_ignored(client):
    """The ignore set is keyed by chore id and the new ids are in `KNOWN`."""
    _world_with_a_character(client)
    assert "world-taglines" in {c["id"] for c in _todo(client, "")["chores"]}
    assert client.put("/api/todo/world-taglines/ignored",
                      json={"ignored": True}).status_code == 200
    body = _todo(client, "")
    assert "world-taglines" not in {c["id"] for c in body["chores"]}
    assert "world-taglines" in {c["id"] for c in body["ignored"]}
    assert body["count"] == len(body["chores"])
