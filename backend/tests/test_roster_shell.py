"""Cheap rosters for callers that need ids, names or two sidecar facts.

`overlay.list_characters` is the full row: every character's image listing,
avatar focus, tagline and voice-anchor presence, off both roots. A caller that
only asks "who is in this campaign, and what are they called" -- the turn's
off-scene directory, cast-change detection, the suggestion rail, scene
suggestions, the sheet tally, the to-do list -- paid for all of it. These tests
pin the cheap readers to the full one, so they can never answer a different
roster, and pin the two routes that run on every navigation to building
neither the full listing nor the sheet tally more than once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.routes import todo as todo_routes
from grimoire.store import (
    campaigns,
    characters,
    entities,
    overlay,
    pcs,
    taglines,
    voice_anchors,
    worlds,
)
from grimoire.store.sheets import tally
from grimoire.store.sheets.paths import FILE_KINDS


def _unaddress(root, aid):
    """Leave `aid`'s meta in place but no card a reader can open."""
    for p in (root / "characters" / aid).glob("*.json"):
        p.unlink()


def _library(monkeypatch, tmp_path):
    """A world and a campaign on it, holding one of every roster case:

    - `mara`              inherited, world tagline and anchor
    - `seraphine`         inherited, campaign tagline over a world one,
                          campaign anchor TOMBSTONE over a world anchor
    - `winifred`          a campaign COPY, renamed campaign-side, whose default
                          version has gone stale, with a campaign anchor
                          override
    - `winifred-ashcroft` tombstoned campaign-side: listed by neither
    - `seraphine-vale`    a world character with no addressable version:
                          listed by neither
    - `saltmarch`         a campaign copy whose WORLD original lost every
                          version but kept its anchor -- the world scan does
                          not list it, so the campaign row must not pick the
                          anchor up from it
    - `mara-vance`        campaign-created, hence detached; the world later
                          took the same slug with its own tagline and anchor,
                          which must reach neither field
    - `winifred-vance`    campaign-created and nothing more
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    for name in ("Mara", "Seraphine", "Winifred", "Winifred Ashcroft", "Seraphine Vale",
                 "Saltmarch"):
        aid, _ = characters.create_character(wroot, name)
        taglines.write(wroot, aid, f"{name} of the world.")
        voice_anchors.write(wroot, aid, f"{name} speaks plainly.")
    characters.create_version(wroot, "winifred", "Older", characters.blank_card("Winifred"))
    pcs.create_pc(wroot, "Seraphine", [])
    pcs.create_pc(wroot, "Mara", [])
    for kind in ("locations", "lore"):
        entities.create_entity(wroot, kind, "Saltmarch Quay", "world text")
        entities.create_entity(wroot, kind, "Saltmarch Docks", "world text")

    cid = campaigns.create_campaign("A Long Run", wid)
    croot = campaigns.campaign_root(cid)

    taglines.write(croot, "seraphine", "Seraphine, as this campaign knows her.")
    overlay.set_voice_anchor(cid, "seraphine", "")          # tombstone over the world's
    overlay.materialize_actor(cid, "characters", "winifred")
    characters.set_name(croot, "winifred", "Winifred the Elder")
    characters.set_default_version(croot, "winifred", "older")
    (croot / "characters" / "winifred" / "older.json").unlink()   # stale default
    overlay.set_voice_anchor(cid, "winifred", "Winifred, clipped and dry.")
    overlay.delete_actor(cid, "characters", "winifred-ashcroft")
    _unaddress(wroot, "seraphine-vale")
    overlay.materialize_actor(cid, "characters", "saltmarch")
    _unaddress(wroot, "saltmarch")
    overlay.create_character(cid, "Mara Vance")
    characters.create_character(wroot, "Mara Vance")         # the world's stranger
    taglines.write(wroot, "mara-vance", "A stranger's line.")
    voice_anchors.write(wroot, "mara-vance", "A stranger's voice.")
    overlay.create_character(cid, "Winifred Vance")

    overlay.delete_actor(cid, "pcs", "mara")
    overlay.create_pc(cid, "Winifred", [])
    overlay.delete_entity(cid, "lore", "saltmarch-docks")
    overlay.create_entity(cid, "locations", "Tidewatch", "campaign text")
    return cid, wroot


def test_the_roster_is_list_characters_cut_to_three_fields(monkeypatch, tmp_path):
    cid, _ = _library(monkeypatch, tmp_path)
    full = overlay.list_characters(cid)
    assert [c["id"] for c in full] == [
        "mara", "mara-vance", "saltmarch", "seraphine", "winifred",
        "winifred-vance"]                                   # the fixture holds

    roster = overlay.character_roster(cid)
    assert roster == [{"id": c["id"], "name": c["name"], "default_version": c["default_version"]}
                      for c in full]
    assert overlay.character_ids(cid) == [c["id"] for c in full]
    # The name comes from whichever root owns the row, like the full listing's.
    assert {r["id"]: r["name"] for r in roster}["winifred"] == "Winifred the Elder"


def test_character_refs_is_the_id_roster(monkeypatch, tmp_path):
    """The turn's off-scene directory iterates `character_refs`; it must name
    exactly the characters the full listing would, and build none of it."""
    cid, _ = _library(monkeypatch, tmp_path)
    expected = [c["id"] for c in overlay.list_characters(cid)]

    def boom(*_a, **_kw):
        raise AssertionError("character_refs built the full listing")

    monkeypatch.setattr(overlay, "list_characters", boom)
    assert overlay.character_refs(cid) == expected


def test_a_shared_view_answers_the_same_roster(monkeypatch, tmp_path):
    cid, _ = _library(monkeypatch, tmp_path)
    v = overlay.view(cid)
    assert overlay.character_roster(cid, v=v) == overlay.character_roster(cid)
    assert overlay.character_sidecars(cid, v=v) == overlay.character_sidecars(cid)
    with pytest.raises(ValueError):
        overlay.character_ids("some-other-campaign", v=v)


def test_sidecars_keep_list_characters_precedence(monkeypatch, tmp_path):
    cid, _ = _library(monkeypatch, tmp_path)
    full = overlay.list_characters(cid)
    got = overlay.character_sidecars(cid)
    assert got == [{"id": c["id"], "name": c["name"], "tagline": c["tagline"],
                    "has_voice_anchor": c["has_voice_anchor"]} for c in full]
    # And the fixture really reaches each branch, rather than agreeing because
    # every row is the same shape.
    by = {r["id"]: r for r in got}
    assert by["mara"]["tagline"] == "Mara of the world." and by["mara"]["has_voice_anchor"]
    assert by["seraphine"]["tagline"] == "Seraphine, as this campaign knows her."
    assert not by["seraphine"]["has_voice_anchor"]          # the tombstone wins
    assert by["winifred"]["has_voice_anchor"]               # the campaign's override
    assert not by["saltmarch"]["has_voice_anchor"]          # unlisted world row
    assert by["mara-vance"] == {"id": "mara-vance", "name": "Mara Vance", "tagline": "",
                                "has_voice_anchor": False}  # detached: no stranger's fields
    assert not by["winifred-vance"]["tagline"] and not by["winifred-vance"]["has_voice_anchor"]


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_cast_ids_count_what_the_full_listings_count(monkeypatch, tmp_path, kind):
    """`sheets.coverage` tallies off `cast_ids`; `roster` and `create_missing`
    still read `_cast`. The two must name the same cast, or the rail's
    "sheeted of total" and the sheet page's rows disagree."""
    cid, _ = _library(monkeypatch, tmp_path)
    assert overlay.cast_ids(cid, kind) == [e["id"] for e in tally._cast(cid, kind)]


def test_store_level_ids_match_their_listings(monkeypatch, tmp_path):
    _cid, wroot = _library(monkeypatch, tmp_path)
    assert characters.listed_ids(wroot) == [c["id"] for c in characters.list_characters(wroot)]
    assert characters.roster(wroot) == [
        {"id": c["id"], "name": c["name"], "default_version": c["default_version"]}
        for c in characters.list_characters(wroot)]
    assert pcs.listed_ids(wroot) == [p["id"] for p in pcs.list_pcs(wroot)]
    for kind in entities.ENTITY_KINDS:
        assert entities.entity_ids(wroot, kind) == [e["id"] for e in entities.list_entities(wroot, kind)]
    with pytest.raises(entities.UnknownKind):
        entities.entity_ids(wroot, "greetings")


def test_world_rows_are_list_worlds_without_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    first = worlds.create_world("Realm")
    worlds.create_world("Saltmarch")
    characters.create_character(worlds.world_root(first), "Mara")
    (worlds._worlds_dir() / "not-a-world").mkdir()          # no world.md: not listed
    full = worlds.read.list_worlds()
    assert len(full) == 2
    assert worlds.read.list_world_rows() == [
        {k: v for k, v in w.items() if k != "counts"} for w in full]


# ---- the two routes on every navigation ------------------------------------

@pytest.fixture
def shell_client(user_pack_path):
    """A campaign bound to a module (so the sheet tally really sweeps the
    cast) with a world cast of characters, PCs and lore."""
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    for name in ("Mara", "Seraphine", "Winifred"):
        aid, _ = characters.create_character(wroot, name)
        taglines.write(wroot, aid, f"{name} of the world.")
    pcs.create_pc(wroot, "Mara", [])
    entities.create_entity(wroot, "lore", "Saltmarch Quay", "world text")
    cid = campaigns.create_campaign("A Long Run", wid, module=mid)
    store.sheets.write(cid, "characters", "mara", "warrior",
                       {"hp": {"current": 12, "max": 12}}, expected=None)
    with TestClient(create_app()) as c:
        yield c, cid


def _count_calls(monkeypatch, owner, name):
    calls = []
    real = getattr(owner, name)

    def counting(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(owner, name, counting)
    return calls


def test_the_shell_builds_no_full_listing_and_tallies_sheets_once(shell_client, monkeypatch):
    """`/api/shell` runs on every navigation. It used to build the full
    character listing three times (twice inside two sheet tallies, once for
    the to-do gaps) and the PC listing twice, to count ids and read two
    sidecar facts. The tally is shared through one to-do context now, and
    nothing on the route needs a full row."""
    client, cid = shell_client
    full_chars = _count_calls(monkeypatch, store.overlay, "list_characters")
    full_pcs = _count_calls(monkeypatch, store.overlay, "list_pcs")
    tallies = _count_calls(monkeypatch, store.sheets, "coverage")

    body = client.get("/api/shell", params={"campaign": cid}).json()

    # Three characters and the PC, who shares the character sheet types.
    assert body["campaign"]["sheets"] == {"sheeted": 1, "total": 4}
    assert len(tallies) == 1
    assert full_chars == [] and full_pcs == []
    # The sheet chore still counts from the shared tally: three of the cast
    # are unsheeted, so the badge includes it.
    assert body["todo"] == todo_routes.live(cid)["count"]
    assert "sheets" in {c["id"] for c in todo_routes.live(cid)["chores"]}


def test_the_fallback_campaign_shares_the_context_too(shell_client, monkeypatch):
    """With no id remembered the rail answers for the most recent campaign,
    and that path must tally once as well."""
    client, _cid = shell_client
    tallies = _count_calls(monkeypatch, store.sheets, "coverage")
    body = client.get("/api/shell").json()
    assert body["campaign"]["sheets"] == {"sheeted": 1, "total": 4}
    assert len(tallies) == 1


def test_a_failing_tally_still_fails_the_shell_and_spares_the_page(shell_client, monkeypatch):
    """Sharing the tally must not share its failure handling. The rail's
    campaign block has always let an unreadable sheet store surface; the
    sheet chore has always swallowed it. A memo that stored the exception, or
    a block that caught it, would change one of the two."""
    client, cid = shell_client

    def explode(_cid):
        raise OSError("sheets unreadable")

    monkeypatch.setattr(store.sheets, "coverage", explode)
    ctx = todo_routes._Ctx(cid)
    assert todo_routes._chore_sheets(ctx) is None
    with pytest.raises(OSError):
        ctx.coverage()          # asked again, and met again: nothing was memoized
    no_raise = TestClient(client.app, raise_server_exceptions=False)
    assert no_raise.get("/api/shell", params={"campaign": cid}).status_code == 500


def test_badge_count_refuses_a_context_for_another_campaign(shell_client):
    _client, cid = shell_client
    with pytest.raises(ValueError):
        todo_routes.badge_count(cid, todo_routes._Ctx("some-other-campaign"))
    assert todo_routes.badge_count(cid, todo_routes._Ctx(cid)) == todo_routes.badge_count(cid)


def test_todo_gaps_build_no_full_listing(shell_client, monkeypatch):
    client, cid = shell_client
    full_chars = _count_calls(monkeypatch, store.overlay, "list_characters")
    body = client.get("/api/todo", params={"campaign": cid}).json()
    assert full_chars == []
    anchors = client.get("/api/todo/anchors/items", params={"campaign": cid}).json()
    assert {i["id"] for i in anchors["items"]} == {"mara", "seraphine", "winifred"}
    assert "taglines" not in {c["id"] for c in body["chores"]}   # every one has a line
