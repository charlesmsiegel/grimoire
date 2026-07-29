"""#240: the store never joins a caller-supplied id onto a path unchecked.

`entities`/`scenes` already refused ids that could escape their directory; the
three highest-traffic root resolvers (`world_root`, `campaign_root`,
`_char_dir`) did not, and leaned on the router's path-parameter matching
instead. Anything that isn't an HTTP path parameter -- a request body field, a
CLI script, a batch importer -- got no protection at all.
"""

import re

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
    # sync resolves the world through the campaign, whose `world` meta is empty here
    cid = _world_less_campaign(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []


def test_appearances_pick_on_a_world_less_campaign(monkeypatch, tmp_path):
    # the actor-lock path resolves the world the same way; with no world there
    # is no version to pick, which is a domain error, not a crash
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


# ---- round 3 of the review

def test_no_world_root_cannot_be_collided_with(monkeypatch, tmp_path):
    """The missing-world root must be absent by construction, not by convention.

    A sentinel directory anywhere in the store is one a restored or
    hand-managed data dir can already contain, and then every world-less
    campaign inherits whatever is inside it. Resolving *under the campaign's
    own campaign.md* -- a regular file -- makes the filesystem itself the
    guarantee: nothing can be created below a regular file.
    """
    cid = _world_less_campaign(monkeypatch, tmp_path)
    wroot = overlay.wroot_of(cid)
    assert not wroot.exists()
    with pytest.raises(OSError):
        (wroot / "lore").mkdir(parents=True)
    assert overlay.list_entities(cid, "lore") == []


def test_a_stray_store_directory_is_not_adopted_as_a_world(monkeypatch, tmp_path):
    cid = _world_less_campaign(monkeypatch, tmp_path)
    # every plausible sentinel name a previous revision might have used
    for stray in (".no-world", ".none", "no-world"):
        d = tmp_path / stray / "lore"
        d.mkdir(parents=True, exist_ok=True)
        (d / "leak.md").write_text("---\nname: Leak\n---\nleaked\n", encoding="utf-8")
        (tmp_path / "worlds" / stray / "lore").mkdir(parents=True, exist_ok=True)
        (tmp_path / "worlds" / stray / "lore" / "leak.md").write_text(
            "---\nname: Leak\n---\nleaked\n", encoding="utf-8")
    assert overlay.list_entities(cid, "lore") == []


SCENE_ROUTES = [
    ("get", "/api/campaigns/C:evil/scenes"),
    ("get", "/api/campaigns/C:evil/scenes/s1"),
    ("get", "/api/campaigns/C:evil/scenes/s1/weather"),
    ("get", "/api/campaigns/C:evil/scenes/s1/messages"),
    ("delete", "/api/campaigns/C:evil/scenes/s1"),
]


@pytest.mark.parametrize("method,path", SCENE_ROUTES)
def test_scene_routes_answer_404_for_an_unsafe_campaign_id(monkeypatch, tmp_path, method, path):
    # scene paths are built from campaign_root, so the guard fires inside the
    # scene store; those handlers catch SceneNotFound only
    home(monkeypatch, tmp_path)
    client = TestClient(create_app())
    assert getattr(client, method)(path).status_code == 404


def test_scene_rename_route_answers_404_for_an_unsafe_campaign_id(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    client = TestClient(create_app())
    r = client.put("/api/campaigns/C:evil/scenes/s1", json={"title": "New"})
    assert r.status_code == 404


# ---- round 4 of the review

def test_a_campaign_whose_stored_world_id_is_unusable_reads_no_world_records(
        monkeypatch, tmp_path):
    """A corrupt `world` value is as good as no world, not a crash.

    A restored or hand-edited campaign can carry a `world` the guard refuses
    to resolve. A missing world directory and an empty `world` both already
    mean "inherits nothing"; an unusable id has to land in the same place,
    or every overlay and sync route 500s on that campaign.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    cid = campaigns.create_campaign("Saltmarch", wid)
    for broken in ("C:evil", "../realm", "..", "a/b"):
        mp = campaigns.campaign_meta_path(cid)
        meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
        meta["world"] = broken
        mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
        assert not campaigns.world_root_of(cid).exists()
        assert overlay.list_entities(cid, "lore") == []
        assert sync.incoming(cid) == []


# Every guarded resolver is reached through some path builder, and each new
# one has been found the same way: a reviewer noticing a handler that catches
# only its own domain error. This sweeps the whole router instead -- an
# unsafe id must never reach a handler as an unhandled exception, whatever
# route carries it.
_FILL = {"sid": "s1", "rid": "r1", "gid": "g1", "eid": "e1", "kind": "locations",
         "vid": "default", "mid": "pool-basic", "name": "avatar", "pid": "p1",
         "tid": "t1", "char": "c1", "aid": "a1", "version": "default",
         "actor_id": "a1", "id": "x1", "content_id": "c1", "nonce": "n1",
         "preset_id": "p1", "style_id": "s1", "key": "k1", "kid": "k1",
         "slot": "avatar", "tag": "t1", "provider": "gregorian", "who": "a1",
         "storage_key": "k1", "span_id": "sp1", "index": "0"}


# Enumerated from the OpenAPI schema, not from `router.routes`: the router is
# composed of per-domain sub-routers, so walking `.routes` yields the
# inclusions rather than the paths. MIN_ID_ROUTES is a floor -- if enumeration
# ever breaks again, the sweep must fail loudly rather than quietly testing
# nothing.
MIN_ID_ROUTES = 150


def _id_routes():
    import os
    import tempfile
    os.environ.setdefault("GRIMOIRE_HOME", tempfile.mkdtemp())
    schema = create_app().openapi()
    out = []
    for path, ops in schema["paths"].items():
        params = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", path))
        if not ({"cid", "wid"} & params):
            continue
        unfilled = sorted(params - set(_FILL) - {"cid", "wid"})
        for method in sorted(m.upper() for m in ops
                             if m.upper() in {"GET", "DELETE", "POST", "PUT", "PATCH"}):
            out.append(pytest.param(method, path, unfilled, id=f"{method}-{path}"))
    assert len(out) >= MIN_ID_ROUTES, (
        f"only {len(out)} id-carrying routes found; enumeration is broken "
        f"(the sweep would silently cover nothing)")
    return out


@pytest.mark.parametrize("method,path,unfilled", _id_routes())
def test_no_route_500s_on_an_unsafe_id(monkeypatch, tmp_path, method, path, unfilled):
    assert not unfilled, f"add {unfilled} to _FILL so this route is actually exercised"
    home(monkeypatch, tmp_path)
    url = path.replace("{cid}", "C:evil").replace("{wid}", "C:evil")
    for name, value in _FILL.items():
        url = url.replace("{" + name + "}", value)
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.request(method, url, json={}).status_code != 500
