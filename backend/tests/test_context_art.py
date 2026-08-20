"""`store.context.art` — the candidate pool, the ranking, and the stateless
handle resolution that runs on the way back in."""

import pytest

from grimoire.store import (
    appearances,
    assets,
    campaign_images,
    campaigns,
    characters,
    entities,
    image_descriptions,
    overlay,
    scenes,
    worlds,
)
from grimoire.store.context import art


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A world with a described character image and a described location image,
    plus a campaign on it holding one described library image."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)

    cid, vid = characters.create_character(wroot, "Seraphine", "main")
    assets.put_image(wroot, cid, vid, "gallery_1", b"png", "png")
    image_descriptions.set_description(
        wroot, cid, vid, "gallery_1", "Half-plate, rain-soaked, a burning keep behind her.")

    loc = entities.create_entity(wroot, "locations", "Saltmarch Harbour", "A grey quay.")
    assets.put_image(wroot, loc, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(
        wroot, loc, "default", "gallery_1", "Fishing boats at the quay under fog.",
        base="locations")

    camp = campaigns.create_campaign("Saltmarch", wid)
    campaign_images.put_image(camp, "coastline", b"png", "png")
    image_descriptions.set_in(campaign_images.images_dir(camp), "coastline",
                             "A hand-drawn map of the northern coastline.")
    return {"wid": wid, "wroot": wroot, "cid": camp, "char": cid, "vid": vid, "loc": loc}


def _cast(camp, char, vid):
    """Put the character on stage in a scene, so a locked version exists."""
    sid = scenes.create_scene(camp, "Scene one")
    appearances.appear(camp, sid, "characters", char, vid, "npc")
    return sid


# ---- the handle grammar ----------------------------------------------------

def test_handle_roundtrip_record_and_library():
    h = art.handle_for("characters", "seraphine", "gallery_1")
    assert h == "[[art:characters:seraphine:gallery_1]]"
    assert art.parse_handle(art.HANDLE.match(h)) == ("characters", "seraphine", "gallery_1")

    h = art.handle_for(art.LIBRARY, "", "coastline")
    assert h == "[[art:campaign:coastline]]"
    assert art.parse_handle(art.HANDLE.match(h)) == (art.LIBRARY, "", "coastline")


def test_parse_handle_rejects_an_unknown_kind():
    assert art.parse_handle(art.HANDLE.match("[[art:sausages:x:y]]")) is None
    assert art.parse_handle(art.HANDLE.match("[[art:notcampaign:y]]")) is None


def test_urls_are_campaign_scoped_for_every_kind():
    assert art.url_for("c1", "characters", "sera", "v1", "avatar") == (
        "/api/campaigns/c1/characters/sera/versions/v1/images/avatar")
    assert art.url_for("c1", "pcs", "shia", "v1", "avatar") == (
        "/api/campaigns/c1/pcs/shia/versions/v1/images/avatar")
    assert art.url_for("c1", "locations", "harbour", "default", "gallery_1") == (
        "/api/campaigns/c1/locations/harbour/images/gallery_1")
    assert art.url_for("c1", art.LIBRARY, "", "", "coastline") == (
        "/api/campaigns/c1/images/coastline")


# ---- the candidate pool ----------------------------------------------------

def test_pool_covers_cast_setting_worldinfo_and_library(world):
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}],
                           loc, [])
    handles = {c["handle"] for c in cands}
    assert handles == {f"[[art:characters:{char}:gallery_1]]",
                       f"[[art:locations:{loc}:gallery_1]]",
                       "[[art:campaign:coastline]]"}


def test_pool_excludes_undescribed_and_reviewed_empty_art(world):
    """Both halves of the absent-vs-empty distinction stay out of the offer."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    wroot = world["wroot"]
    _cast(camp, char, vid)
    assets.put_image(wroot, char, vid, "gallery_2", b"png", "png")          # never reviewed
    assets.put_image(wroot, char, vid, "gallery_3", b"png", "png")
    image_descriptions.set_description(wroot, char, vid, "gallery_3", "")   # reviewed-empty
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    assert {c["name"] for c in cands} == {"gallery_1", "coastline"}


def test_pool_excludes_an_actor_who_is_not_cast(world):
    """No locked version means no offer: the catalogue only ever shows art of
    actors who are actually on stage."""
    camp, char = world["cid"], world["char"]
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    assert {c["name"] for c in cands} == {"coastline"}


def test_pool_deduplicates_a_location_that_is_both_setting_and_world_info(world):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [{"kind": "locations", "id": loc}])
    assert sum(1 for c in cands if c["id"] == loc) == 1


# ---- ranking ---------------------------------------------------------------

def test_keyword_ranking_needs_two_shared_terms(world):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [])
    # one shared content word ("boats") is not enough on an unnamed record
    assert art.rank(camp, cands, "She watched the boats.") == []
    # two are
    got = art.rank(camp, cands, "Fishing boats sat at the quay, lost in fog.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_naming_the_record_lowers_the_bar_to_one_term(world):
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    cands = [c for c in cands if c["kind"] == "characters"]
    assert art.rank(camp, cands, "The rain kept on.") == []
    got = art.rank(camp, cands, "Seraphine stood in the rain.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_rank_respects_depth(world, monkeypatch):
    camp, loc = world["cid"], world["loc"]
    monkeypatch.setattr(art, "settings", lambda: {"depth": 0, "threshold": 0.4})
    assert art.rank(camp, art.candidates(camp, [], loc, []), "fishing boats quay fog") == []


def test_catalogue_never_raises(world, monkeypatch):
    monkeypatch.setattr(art, "candidates",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("store is gone")))
    assert art.catalogue(world["cid"], [], None, [], "anything") == []


# ---- the return path -------------------------------------------------------

def test_resolve_rewrites_a_valid_handle_with_the_description_as_alt(world):
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    out = art.resolve_handles(camp, f"She turns. [[art:characters:{char}:gallery_1]] Rain.")
    assert out == (
        "She turns. ![Half-plate, rain-soaked, a burning keep behind her.]"
        f"(/api/campaigns/{camp}/characters/{char}/versions/{vid}/images/gallery_1) Rain.")


def test_resolve_rewrites_a_library_handle(world):
    camp = world["cid"]
    out = art.resolve_handles(camp, "[[art:campaign:coastline]]")
    assert out == (f"![A hand-drawn map of the northern coastline.]"
                   f"(/api/campaigns/{camp}/images/coastline)")


def test_resolve_deletes_a_handle_naming_nothing(world):
    camp = world["cid"]
    assert art.resolve_handles(camp, "before [[art:characters:ghost:gallery_1]] after") == (
        "before  after")
    assert art.resolve_handles(camp, "x [[art:campaign:nope]] y") == "x  y"
    assert art.resolve_handles(camp, "x [[art:sausages:a:b]] y") == "x  y"


def test_resolve_deletes_a_handle_for_a_real_but_undescribed_image(world):
    """The rule that makes stateless resolution safe: an image the model was
    never offered -- because nobody described it -- cannot be reached by
    composing a plausible handle for it."""
    camp, char, vid, wroot = world["cid"], world["char"], world["vid"], world["wroot"]
    _cast(camp, char, vid)
    assets.put_image(wroot, char, vid, "gallery_9", b"png", "png")
    assert assets.image_path(wroot, char, vid, "gallery_9") is not None
    assert art.resolve_handles(camp, f"[[art:characters:{char}:gallery_9]]") == ""


def test_resolve_deletes_a_handle_for_a_tombstoned_image(world):
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    overlay.delete_image(camp, char, vid, "gallery_1")
    assert art.resolve_handles(camp, f"[[art:characters:{char}:gallery_1]]") == ""


def test_resolve_escapes_brackets_in_the_alt_text(world):
    """A name can hold neither `]` nor `)`, but a DESCRIPTION can hold both --
    and an unescaped `]` would close the alt text early."""
    camp = world["cid"]
    image_descriptions.set_in(campaign_images.images_dir(camp), "coastline",
                              "A map [annotated] by the\nharbourmaster.")
    out = art.resolve_handles(camp, "[[art:campaign:coastline]]")
    assert out.startswith("![A map (annotated) by the harbourmaster.](")
    assert "]" not in out[: out.index("](")]


def test_resolve_leaves_text_with_no_handles_untouched(world):
    text = "Ordinary narration, with a [markdown link](http://example.invalid) in it."
    assert art.resolve_handles(world["cid"], text) == text
