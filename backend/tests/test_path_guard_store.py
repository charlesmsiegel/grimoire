"""#240: the store never joins a caller-supplied id onto a path unchecked.

`entities`/`scenes` already refused ids that could escape their directory; the
three highest-traffic root resolvers (`world_root`, `campaign_root`,
`_char_dir`) did not, and leaned on the router's path-parameter matching
instead. Anything that isn't an HTTP path parameter -- a request body field, a
CLI script, a batch importer -- got no protection at all.
"""

import os
import re
import types

import pytest
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import appearances, campaigns, characters, entities, overlay, pcs, sync, worlds
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
         "storage_key": "k1", "span_id": "sp1", "index": "0", "jid": "j1",
         "lid": "l1", "run_id": "r1", "identity": "0"*32,
         "attempt_id": "a1"}


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


# ---- round 5 of the review

# Win32 trims trailing dots and spaces off a path component, so `realm.` and
# `realm ` both open `realm`. An id that aliases another id is not a distinct
# child, whatever the platform -- and a store is synced between them.
WINDOWS_ALIASES = ["realm.", "realm ", "realm...", "realm. ", "realm .", " "]


@pytest.mark.parametrize("value", WINDOWS_ALIASES)
def test_safe_id_rejects_windows_normalized_names(value):
    assert not safe_id(value)


@pytest.mark.parametrize("value", [".hidden", "a.b", "realm-2", "a b"])
def test_safe_id_still_accepts_interior_dots_and_spaces(value):
    # only the trailing position is normalized away; interior ones are fine
    assert safe_id(value)


def test_a_world_in_use_cannot_be_deleted_through_a_normalized_alias(monkeypatch, tmp_path):
    """The alias bypassed the in-use guard and destroyed a live world.

    `delete_world("realm.")` opened `worlds/realm` on Windows, but compared the
    raw `realm.` against campaigns' stored `realm` references, concluded the
    world was unused, and rmtree'd it out from under the campaign.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    campaigns.create_campaign("Saltmarch", wid)
    for alias in ("realm.", "realm ", "realm..."):
        with pytest.raises(worlds.WorldNotFound):
            worlds.delete_world(alias)
    assert worlds.world_root(wid).exists()               # still there
    assert entities.list_entities(worlds.world_root(wid), "lore")


def test_deleting_a_world_alias_is_a_404(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    worlds.create_world("Realm")
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.delete("/api/worlds/realm.").status_code == 404
    assert worlds.world_root("realm").exists()


# A directory whose name no id can address is only creatable where the OS
# permits it; Windows rejects every such name at mkdir, so the store can only
# acquire one by being synced or restored from a POSIX machine.
posix_only = pytest.mark.skipif(os.name == "nt",
                                reason="Windows cannot create a directory with an unusable name")
UNUSABLE_DIR = "C:evil"


@posix_only
def test_listings_skip_unusable_directory_names(monkeypatch, tmp_path):
    """Enumeration must not hand out ids the resolvers refuse.

    Every listing feeds its ids straight back into a guarded resolver, so a
    single unusable directory would turn a whole list request into a 500.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine")
    pcs.create_pc(wroot, "Mara", [])
    entities.create_entity(wroot, "lore", "Salt Pact", "the pact")

    for base, marker in ((tmp_path / "worlds", "world.md"),
                         (tmp_path / "campaigns", "campaign.md"),
                         (wroot / "characters", "character.md"),
                         (wroot / "pcs", "pc.md")):
        d = base / UNUSABLE_DIR
        d.mkdir(parents=True, exist_ok=True)
        (d / marker).write_text("---\nname: Nope\n---\n", encoding="utf-8")
    (wroot / "lore" / f"{UNUSABLE_DIR}.md").write_text("---\nname: Nope\n---\n", encoding="utf-8")

    assert [w["id"] for w in worlds.list_worlds()] == [wid]
    assert [c["id"] for c in campaigns.list_campaigns()] == [cid]
    assert [c["id"] for c in characters.list_characters(wroot)] == ["seraphine"]
    assert characters.character_refs(wroot) == ["seraphine"]
    assert [p["id"] for p in pcs.list_pcs(wroot)] == ["mara"]
    assert pcs.pc_refs(wroot) == ["mara"]
    assert [e["id"] for e in entities.list_entities(wroot, "lore")] == ["salt-pact"]


@posix_only
def test_startup_migration_survives_an_unusable_campaign_directory(monkeypatch, tmp_path):
    # migrate_scene_ids runs in the app lifespan: an uncaught CampaignNotFound
    # here aborts startup, so one stray directory would stop the server booting
    from grimoire.store import migrations
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    campaigns.create_campaign("Saltmarch", wid)
    d = tmp_path / "campaigns" / UNUSABLE_DIR
    d.mkdir(parents=True)
    (d / "campaign.md").write_text("---\nname: Nope\nworld: realm\n---\n", encoding="utf-8")
    migrations.migrate_scene_ids()          # must not raise
    TestClient(create_app()).get("/api/campaigns")   # lifespan runs on first request


# ---- round 6 of the review: enumeration/lookup agreement, checked exhaustively

# Round 5 filtered the record-level listings and the summary claimed "every
# enumeration"; round 6 found three more a level down (version ids, style and
# preset catalogs, image names). Grepping for listings is what missed them, so
# this checks the property directly instead: plant an artifact at every level,
# make the shared guard reject it, and require that no listing returns it or
# raises. Portable -- a name the guard genuinely rejects is not creatable on
# Windows, so the guard is what changes here, not the name.
MARK = "zz-unusable"


def _reject_mark(monkeypatch):
    from grimoire import store as store_pkg
    from grimoire.store import paths as paths_mod
    real = paths_mod.safe_id

    def guarded(value):
        return real(value) and MARK not in str(value)

    # Submodules too, not only what `dir(store)` shows. A store subpackage
    # (`campaigns/`, `calendars/`, ...) holds `safe_id` on its *parts*, and the
    # package itself may not carry the name at all -- so a one-level sweep left
    # the real `safe_id` in place inside every listing this test drives, and the
    # test passed while guarding nothing.
    seen: set[int] = set()

    def patch(mod) -> None:
        if id(mod) in seen:
            return
        seen.add(id(mod))
        if hasattr(mod, "safe_id"):
            monkeypatch.setattr(mod, "safe_id", guarded)
        for name in dir(mod):
            sub = getattr(mod, name, None)
            if (isinstance(sub, types.ModuleType)
                    and getattr(sub, "__name__", "").startswith("grimoire.store")):
                patch(sub)

    patch(store_pkg)


def test_no_listing_hands_back_an_id_its_own_lookups_refuse(monkeypatch, tmp_path):
    from grimoire.store import (
        assets,
        greetings,
        llm_connections,
        response_presets,
        scenes,
        sheets,
        styles,
    )
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid = campaigns.create_campaign("Saltmarch", wid)
    ch, vid = characters.create_character(wroot, "Seraphine")
    pc, _ = pcs.create_pc(wroot, "Mara", [])
    entities.create_entity(wroot, "lore", "Salt Pact", "the pact")
    greetings.create_greeting(wroot, "Hello", ch, vid, "hi")
    scenes.create_scene(cid, "Opening")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    assets.put_image(wroot, ch, vid, assets.AVATAR, png, "png")

    (characters._char_dir(wroot, ch) / f"{MARK}.json").write_text(
        '{"data":{"name":"N"}}', encoding="utf-8")
    (pcs._pc_dir(wroot, pc) / f"{MARK}.md").write_text("---\nname: N\n---\n", encoding="utf-8")
    (assets._dir(wroot, ch, vid) / f"{MARK}.png").write_bytes(png)
    for d in (styles._custom_dir(), response_presets._custom_dir()):
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{MARK}.md").write_text("---\nname: N\n---\nbody\n", encoding="utf-8")
    (tmp_path / "worlds" / MARK).mkdir(parents=True)
    (tmp_path / "worlds" / MARK / "world.md").write_text("---\nname: N\n---\n", encoding="utf-8")
    (tmp_path / "campaigns" / MARK).mkdir(parents=True)
    (tmp_path / "campaigns" / MARK / "campaign.md").write_text(
        "---\nname: N\nworld: realm\n---\n", encoding="utf-8")
    (wroot / "lore" / f"{MARK}.md").write_text("---\nname: N\n---\n", encoding="utf-8")

    _reject_mark(monkeypatch)

    listings = {
        "worlds": lambda: [w["id"] for w in worlds.list_worlds()],
        "campaigns": lambda: [c["id"] for c in campaigns.list_campaigns()],
        "characters": lambda: [c["id"] for c in characters.list_characters(wroot)],
        "character versions": lambda: [v["id"] for c in characters.list_characters(wroot)
                                       for v in c["versions"]],
        "character detail versions": lambda: [v["id"] for v in
                                              characters.read_character(wroot, ch)["versions"]],
        "character refs": lambda: characters.character_refs(wroot),
        "pcs": lambda: [p["id"] for p in pcs.list_pcs(wroot)],
        "pc versions": lambda: [v["id"] for v in pcs.read_pc(wroot, pc)["versions"]],
        "pc refs": lambda: pcs.pc_refs(wroot),
        "entities": lambda: [e["id"] for e in entities.list_entities(wroot, "lore")],
        "entity refs": lambda: [e for _k, e in entities.all_refs(wroot)],
        "greetings": lambda: [g["id"] for g in greetings.list_greetings(wroot)],
        "scenes": lambda: [s["id"] for s in scenes.list_scenes(cid)],
        "styles": lambda: [s["id"] for s in styles.list_styles()],
        "response presets": lambda: [p["id"] for p in response_presets.list_presets()],
        "images": lambda: [i["name"] for i in assets.list_images(wroot, ch, vid)],
        "llm connections": lambda: [c.get("id") for c in llm_connections.list_connections()],
        "sheet refs": lambda: [e for _k, e in sheets.list_refs(cid)],
    }
    leaked = {}
    for label, fn in listings.items():
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 - the test's subject: report which listing raised, don't crash
            leaked[label] = f"{type(exc).__name__}: {exc}"
            continue
        if any(x and MARK in str(x) for x in got):
            leaked[label] = got
    assert not leaked, f"listings disagree with their lookups: {leaked}"


# ---- round 7 of the review

def test_a_world_cannot_be_deleted_through_a_case_variant(monkeypatch, tmp_path):
    """Case is another alias, and it destroyed a world in use just like the dot.

    On a case-insensitive filesystem `DELETE /api/worlds/REALM` found the world
    but compared the raw `REALM` against campaigns' stored `realm`, saw no
    user, and removed it. Asking the directory listing rather than lower-casing
    keeps a genuinely distinct `REALM` valid where the filesystem allows one.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    campaigns.create_campaign("Saltmarch", wid)
    if worlds.world_exists("REALM"):          # only where the filesystem aliases
        with pytest.raises(worlds.WorldNotFound):
            worlds.delete_world("REALM")
    assert worlds.world_root(wid).exists()
    assert entities.list_entities(worlds.world_root(wid), "lore")


def test_a_hidden_campaign_still_pins_its_world(monkeypatch, tmp_path):
    """Hiding a record from enumeration must not hide it from integrity checks.

    Filtering unusable campaigns out of `list_campaigns` (round 5) silently
    made this world deletable: `delete_world` used that listing as its whole
    in-use check, so the hidden campaign stopped counting as a user.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    d = tmp_path / "campaigns" / MARK
    d.mkdir(parents=True)
    (d / "campaign.md").write_text(f"---\nname: Hidden\nworld: {wid}\n---\n", encoding="utf-8")
    _reject_mark(monkeypatch)

    assert [c["id"] for c in campaigns.list_campaigns()] == []      # hidden from the UI
    assert (MARK, "Hidden", wid) in campaigns.world_refs()          # not from the check
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)
    assert worlds.world_root(wid).exists()


def test_startup_migrations_survive_unusable_ids(monkeypatch, tmp_path):
    """`bake_char_macros` and `migrate_scene_ids` both run in the app lifespan
    and both scan directories directly rather than through a listing."""
    from grimoire.store import greetings, migrations, scenes
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    ch, vid = characters.create_character(worlds.world_root(wid), "Seraphine")
    scenes.create_scene(cid, "Opening")
    croot = campaigns.campaign_root(cid)
    (croot / "greetings").mkdir(parents=True, exist_ok=True)
    (croot / "greetings" / f"{MARK}.md").write_text(
        f"---\nname: N\ncharacter: {ch}\nversion: {vid}\n---\nbody\n", encoding="utf-8")
    (croot / "scenes" / f"{MARK}.md").write_text("---\ntitle: N\n---\n", encoding="utf-8")
    _reject_mark(monkeypatch)

    migrations.migrate_scene_ids()      # neither may raise: they abort startup
    migrations.bake_char_macros()
    assert MARK not in [g["id"] for g in greetings.list_greetings(croot)]


def test_module_content_with_an_unusable_id_is_reported_not_advertised(monkeypatch, tmp_path):
    """A pack is authored, not user data, so an unusable content id is an error
    in the pack -- but it must never be listed as content the detail route
    cannot open."""
    from grimoire.store import modules
    home(monkeypatch, tmp_path)
    root = tmp_path / "modules" / "pack"
    (root / "content" / "locations").mkdir(parents=True)
    (root / "module.md").write_text("---\nname: Pack\n---\n", encoding="utf-8")
    (root / "content" / "locations" / f"{MARK}.md").write_text(
        "---\nname: N\n---\nbody\n", encoding="utf-8")
    (root / "content" / "locations" / "keep.md").write_text(
        "---\nname: Keep\n---\nbody\n", encoding="utf-8")
    _reject_mark(monkeypatch)

    pack = modules.load_pack("pack")
    assert [c["id"] for c in pack["content"]] == ["keep"]
    assert any(MARK in e for e in pack["errors"])


# ---- round 8 of the review

def test_an_unreadable_campaign_blocks_world_deletion(monkeypatch, tmp_path):
    """"We could not read it" must never be reported as "nothing uses it".

    `world_refs` swallowed the decode error and skipped the campaign, so a
    world its frontmatter referenced became deletable -- the same data loss the
    unfiltered scan was added to prevent, reintroduced by the scan itself.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    mp = campaigns.campaign_meta_path(cid)
    # valid frontmatter, undecodable body: the reference is still in there
    mp.write_bytes(f"---\nname: Saltmarch\nworld: {wid}\n---\n".encode() + b"\xff\xfe body")

    assert ((cid, "Saltmarch", wid) in campaigns.world_refs())   # recovered, not lost
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)
    assert worlds.world_root(wid).exists()


def test_a_campaign_that_cannot_be_read_at_all_still_blocks_deletion(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    mp = campaigns.campaign_meta_path(cid)

    def unreadable(self, *a, **k):
        raise OSError("locked by a sync client")

    monkeypatch.setattr(type(mp), "read_text", unreadable)
    assert (cid, cid, None) in campaigns.world_refs()   # unknown, not absent
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)


def test_export_survives_an_unusable_stored_world(monkeypatch, tmp_path):
    from grimoire.store import export
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world"] = "C:evil"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")

    data = export.collect(cid)          # must not raise WorldNotFound
    assert data["world_name"] == ""     # degrades to "no world", like a deleted one


def test_an_actor_never_reports_a_default_version_it_does_not_list(monkeypatch, tmp_path):
    """Filtering a version out of the listing must not leave `default_version`
    naming it: the editor asks for that version, then falls back to
    versions[0], and with neither present it crashes."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine")
    pid, pvid = pcs.create_pc(wroot, "Mara", [])
    # a second, unaddressable card that the stored meta points at
    (characters._char_dir(wroot, cid) / f"{MARK}.json").write_text(
        '{"data":{"name":"N"}}', encoding="utf-8")
    (pcs._pc_dir(wroot, pid) / f"{MARK}.md").write_text("---\nname: N\n---\n", encoding="utf-8")
    for meta_path, key in ((characters._meta_path(wroot, cid), "default_version"),
                           (pcs._meta_path(wroot, pid), "default_version")):
        meta, body = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
        meta[key] = MARK
        meta_path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    _reject_mark(monkeypatch)

    detail = characters.read_character(wroot, cid)
    listed = [c for c in characters.list_characters(wroot) if c["id"] == cid][0]
    for payload in (detail["meta"], listed):
        assert payload["default_version"] == vid
    assert {v["id"] for v in detail["versions"]} == {vid}

    pc_detail = pcs.read_pc(wroot, pid)
    pc_listed = [p for p in pcs.list_pcs(wroot) if p["id"] == pid][0]
    for payload in (pc_detail["meta"], pc_listed):
        assert payload["default_version"] == pvid


def test_an_actor_with_no_addressable_version_is_not_listed(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine")
    (characters._char_dir(wroot, cid) / f"{vid}.json").rename(
        characters._char_dir(wroot, cid) / f"{MARK}.json")
    _reject_mark(monkeypatch)

    assert [c["id"] for c in characters.list_characters(wroot)] == []
    with pytest.raises(characters.CharacterNotFound):
        characters.read_character(wroot, cid)


# ---- round 9 of the review

def test_a_case_variant_reference_still_pins_its_world(monkeypatch, tmp_path):
    """The alias can be in the *stored reference*, not just the request.

    `world_exists("REALM")` resolves `realm` where the filesystem is
    case-insensitive, so a campaign could be created holding `REALM`. A later
    `delete_world("realm")` passed the canonical-name check and then missed
    that reference on a string compare, deleting a world still inherited.
    """
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    cid = campaigns.create_campaign("Saltmarch", "REALM" if worlds.world_exists("REALM") else wid)
    # new campaigns store the canonical spelling ...
    assert campaigns.read_campaign(cid)["meta"]["world"] == wid

    # ... and a store written before that still pins the world
    mp = campaigns.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world"] = wid.upper()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if worlds.world_exists(wid.upper()):        # only where the filesystem aliases
        with pytest.raises(worlds.WorldInUse):
            worlds.delete_world(wid)
        assert worlds.world_root(wid).exists()


def test_a_reference_to_a_different_world_does_not_pin_this_one(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    keep = worlds.create_world("Realm")
    other = worlds.create_world("Saltmarch Deeps")
    campaigns.create_campaign("Elsewhere", other)
    worlds.delete_world(keep)                   # unused: must still be deletable
    assert not worlds.world_root(keep).exists()
    assert worlds.world_root(other).exists()


def test_an_actor_with_no_addressable_version_has_no_hash(monkeypatch, tmp_path):
    """`dir_hash` drove sync to a record `read_character` then refused, 500ing
    GET /campaigns/{cid}/incoming. `snapshot` has to agree with it (#247)."""
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine")
    pid, pvid = pcs.create_pc(wroot, "Mara", [])
    (characters._char_dir(wroot, cid) / f"{vid}.json").rename(
        characters._char_dir(wroot, cid) / f"{MARK}.json")
    (pcs._pc_dir(wroot, pid) / f"{pvid}.md").rename(
        pcs._pc_dir(wroot, pid) / f"{MARK}.md")
    _reject_mark(monkeypatch)

    assert characters.dir_hash(wroot, cid) is None
    assert characters.snapshot(wroot, cid) is None
    assert pcs.dir_hash(wroot, pid) is None
    assert pcs.snapshot(wroot, pid) is None


def test_incoming_survives_an_actor_with_no_addressable_version(monkeypatch, tmp_path):
    from grimoire.store import sync
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine")
    ccid = campaigns.create_campaign("Saltmarch", wid)
    campaigns.write_manifest(ccid, {f"characters/{cid}": "stale-hash"})
    (characters._char_dir(wroot, cid) / f"{vid}.json").rename(
        characters._char_dir(wroot, cid) / f"{MARK}.json")
    _reject_mark(monkeypatch)

    assert sync.incoming(ccid) == []     # must not raise CharacterNotFound
