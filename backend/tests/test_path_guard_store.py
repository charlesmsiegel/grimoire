"""#240: the store never joins a caller-supplied id onto a path unchecked.

`entities`/`scenes` already refused ids that could escape their directory; the
three highest-traffic root resolvers (`world_root`, `campaign_root`,
`_char_dir`) did not, and leaned on the router's path-parameter matching
instead. Anything that isn't an HTTP path parameter -- a request body field, a
CLI script, a batch importer -- got no protection at all.
"""

import pytest
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import (appearances, campaigns, characters, entities, overlay, pcs,
                            sync, worlds)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.paths import safe_id


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


# "" is in here because `parent / ""` is `parent` itself: an id that resolves to
# the collection dir is just as wrong as one that climbs out of it. The colon
# forms are the Windows-only escapes: `Path("store/worlds") / "C:evil"` is
# `C:evil` -- a drive-relative id replaces the base outright -- and `x:y` names
# an NTFS alternate data stream.
UNSAFE = ["", ".", "..", "../secret", "..\\secret", "a/b", "a\\b", "/etc", "\\etc",
          "C:evil", "C:/evil", "x:y"]


@pytest.mark.parametrize("value", UNSAFE)
def test_safe_id_rejects_ids_that_do_not_name_a_child(value):
    assert not safe_id(value)


@pytest.mark.parametrize("value", ["seraphine", "realm-2", "a.b", ".hidden"])
def test_safe_id_accepts_ids_that_name_a_child(value):
    assert safe_id(value)


@pytest.mark.parametrize("value", [None, 3, ["x"]])
def test_safe_id_rejects_non_strings(value):
    # ids read back out of on-disk JSON are not guaranteed to be strings
    assert not safe_id(value)


@pytest.mark.parametrize("wid", UNSAFE)
def test_world_root_rejects_unsafe_ids(monkeypatch, tmp_path, wid):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.world_root(wid)


@pytest.mark.parametrize("cid", UNSAFE)
def test_campaign_root_rejects_unsafe_ids(monkeypatch, tmp_path, cid):
    home(monkeypatch, tmp_path)
    with pytest.raises(campaigns.CampaignNotFound):
        campaigns.campaign_root(cid)


@pytest.mark.parametrize("cid", UNSAFE)
def test_char_dir_rejects_unsafe_ids(tmp_path, cid):
    # the private resolver is the chokepoint worth guarding: today's public
    # entry points all check the id themselves, but anything that joins onto
    # `_char_dir` later inherits the guard instead of having to remember it
    with pytest.raises(characters.CharacterNotFound):
        characters._char_dir(tmp_path, cid)


@pytest.mark.parametrize("pid", UNSAFE)
def test_pc_dir_rejects_unsafe_ids(tmp_path, pid):
    with pytest.raises(pcs.PCNotFound):
        pcs._pc_dir(tmp_path, pid)


def test_sibling_collection_is_not_reachable_through_a_world_id(monkeypatch, tmp_path):
    # the concrete escape the guard closes: worlds/ and campaigns/ are siblings
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.world_root("../campaigns")


def test_unsafe_world_id_is_a_404_not_a_500(monkeypatch, tmp_path):
    # a colon survives path-parameter matching where a slash does not, so the
    # router hands the store an id the guard now rejects -- that has to read as
    # "no such world", not as an unhandled WorldNotFound
    home(monkeypatch, tmp_path)
    worlds.create_world("Realm")
    client = TestClient(create_app())
    assert client.get("/api/worlds/C:evil/locations").status_code == 404


def _clear_world(cid: str) -> None:
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world"] = ""
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_campaign_with_no_world_reads_no_world_records(monkeypatch, tmp_path):
    """A campaign whose `world` meta is empty has no world root at all.

    It used to resolve to `world_root("")` -- the worlds *parent* dir, which
    exists -- so world-side reads walked a directory holding every world.
    Now it resolves to a path that cannot exist, and the reads find nothing.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    cid = campaigns.create_campaign("Saltmarch", wid)
    _clear_world(cid)

    wroot = overlay.wroot_of(cid)
    assert not wroot.exists()
    assert overlay.list_entities(cid, "lore") == []
    assert overlay.list_characters(cid) == []


def test_missing_world_root_is_outside_the_worlds_namespace(monkeypatch, tmp_path):
    """The no-world root must not be reachable as a world id.

    A sentinel inside `worlds/` would be an ordinary world directory: a
    restored or synced store containing it would silently feed its entities
    to every world-less campaign, and `list_worlds` would offer it in the UI.
    """
    home(monkeypatch, tmp_path)
    missing = worlds.world_root_or_missing("")
    assert not missing.exists()
    assert missing.parent != tmp_path / "worlds"   # world_root only builds in there

    # so even a store that happens to contain it can neither list it nor open
    # it as a world -- its name does not address it
    (missing / "characters").mkdir(parents=True)
    (missing / "world.md").write_text("---\nname: Nope\n---\n", encoding="utf-8")
    assert worlds.list_worlds() == []
    with pytest.raises(worlds.WorldNotFound):
        worlds.read_world(missing.name)


def test_campaign_with_no_world_still_slims(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    _clear_world(cid)
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")

    campaigns.ensure_campaign_slim(cid)   # must not raise, and must not migrate
    meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
    assert meta["world_copy"] == "full"   # no world to slim against


# ---- callers that reach a resolver through a helper, not a literal id (#259 review)

def _world_less_campaign(monkeypatch, tmp_path) -> str:
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    cid = campaigns.create_campaign("Saltmarch", wid)
    _clear_world(cid)
    return cid


def test_sync_incoming_on_a_world_less_campaign(monkeypatch, tmp_path):
    # sync reaches world_root through _world_id(cid), which yields "" here
    cid = _world_less_campaign(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []


def test_appearances_pick_on_a_world_less_campaign(monkeypatch, tmp_path):
    # the actor-lock path also resolves the world through _world_id(cid); with
    # no world there is no version to pick, which is a domain error, not a crash
    cid = _world_less_campaign(monkeypatch, tmp_path)
    with pytest.raises(appearances.AppearError):
        appearances.pick_version(cid, "characters", "seraphine", "default")


def test_world_name_of_an_unresolvable_id_is_none(monkeypatch, tmp_path):
    # a nullable lookup reports absence; it must not raise for an id it can't resolve
    home(monkeypatch, tmp_path)
    assert worlds.world_name("") is None
    assert worlds.world_name("../campaigns") is None
    assert worlds.world_name("C:evil") is None


def test_campaign_detail_route_for_a_world_less_campaign(monkeypatch, tmp_path):
    cid = _world_less_campaign(monkeypatch, tmp_path)
    client = TestClient(create_app())
    r = client.get(f"/api/campaigns/{cid}")
    assert r.status_code == 200
    assert r.json()["meta"]["world_name"] == ""


# Routes that check existence by calling the resolver directly rather than
# through _campaign_root_or_404 / _world_root_or_404. A colon-bearing id
# reaches them (FastAPI matches it as a path parameter), so each must still
# answer 404 rather than letting the guard's exception escape as a 500.
UNSAFE_ID_ROUTES = [
    "/api/campaigns/C:evil/groups/g/state",
    "/api/campaigns/C:evil/calendar",
    "/api/campaigns/C:evil/calendar/months?year=1",
    "/api/campaigns/C:evil/rolls",
    "/api/worlds/C:evil/calendar/months?year=1",
    "/api/worlds/C:evil/locations",
]


@pytest.mark.parametrize("path", UNSAFE_ID_ROUTES)
def test_unsafe_id_routes_answer_404(monkeypatch, tmp_path, path):
    home(monkeypatch, tmp_path)
    worlds.create_world("Realm")
    client = TestClient(create_app())
    assert client.get(path).status_code == 404


def test_unsafe_campaign_id_put_routes_answer_404(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    client = TestClient(create_app())
    assert client.put("/api/campaigns/C:evil/groups/g/state", json={"state": {}}).status_code == 404
    assert client.put("/api/campaigns/C:evil/calendar",
                      json={"primary": {"provider": "gregorian"}, "secondary": None,
                            "confirmed": True}).status_code == 404
