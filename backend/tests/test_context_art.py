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


def test_naming_the_record_is_enough_on_its_own(world):
    """The commonest useful case, and the one the docstring advertises: the
    record being named in the scene IS the evidence, so its art is eligible
    even when the description shares no vocabulary with the post."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    cands = [c for c in cands if c["kind"] == "characters"]
    # Not named, one shared word ("rain") -- below the two an unnamed record needs.
    assert art.rank(camp, cands, "The rain kept on.") == []
    # Named, and sharing NOTHING: still offered.
    got = art.rank(camp, cands, "Seraphine draws her blade.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_shared_words_outrank_a_bare_name(world):
    """Naming is evidence, not a trump card: a description that actually
    describes the moment sorts above art whose record merely got mentioned."""
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}],
                           loc, [])
    cands = [c for c in cands if c["kind"] != art.LIBRARY]
    got = art.rank(camp, cands, "Seraphine watched the fishing boats at the quay, lost in fog.")
    # The harbour picture shares "fishing"/"boats"/"quay"/"fog"; Seraphine's
    # shares nothing and rides on her name alone.
    assert [c["kind"] for c in got] == ["locations", "characters"]


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


# ---- what statelessness would otherwise let through ------------------------

def test_a_link_breaking_image_name_is_percent_encoded(world):
    """`assets.storable` accepts `art(1)`, `my art` and `a#b` -- names
    `campaign_images.addressable` refuses precisely because each of them ends a
    markdown destination early and spills the rest of the URL into the prose.
    Only the library has that rule, so the other three surfaces have to encode."""
    camp, loc, wroot = world["cid"], world["loc"], world["wroot"]
    for raw, encoded in [("art(1)", "art%281%29"), ("my art", "my%20art"),
                         ("a#b", "a%23b")]:
        assets.put_image(wroot, loc, "default", raw, b"png", "png", base="locations")
        image_descriptions.set_description(wroot, loc, "default", raw, "A quay.",
                                           base="locations")
        out = art.resolve_handles(camp, f"[[art:locations:{loc}:{raw}]]")
        assert out == f"![A quay.](/api/campaigns/{camp}/locations/{loc}/images/{encoded})"
        # the whole destination is inside the parens -- nothing spilled
        assert out.count("(") == out.count(")") == 1


def test_a_gm_only_entitys_art_never_resolves(world):
    """A `gm-only` entry's body never reaches a prompt at all, so a picture of
    it appearing in a post the player reads is a straight leak. The catalogue
    would never offer it; the stateless resolver has to refuse it too."""
    camp, wroot = world["cid"], world["wroot"]
    sec = entities.create_entity(wroot, "locations", "The Vault", "hidden",
                                 secrecy="gm-only")
    assets.put_image(wroot, sec, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(wroot, sec, "default", "gallery_1",
                                       "A locked door.", base="locations")
    assert art.resolve_handles(camp, f"[[art:locations:{sec}:gallery_1]]") == ""


def test_a_secret_entitys_art_still_resolves(world):
    """`secret` is not `gm-only`: its body DOES reach the prompt, and the
    catalogue does offer its art -- so refusing it here would make the two
    halves disagree about the same picture."""
    camp, wroot = world["cid"], world["wroot"]
    sec = entities.create_entity(wroot, "locations", "The Cellar", "quiet",
                                 secrecy="secret")
    assets.put_image(wroot, sec, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(wroot, sec, "default", "gallery_1",
                                       "A cellar.", base="locations")
    assert art.resolve_handles(camp, f"[[art:locations:{sec}:gallery_1]]").startswith("![A cellar.]")


def test_an_actor_not_cast_in_this_scene_does_not_resolve(world):
    """Given a scene, an actor must be in it. Without this, art of anyone the
    campaign has ever cast resolves in every scene it has."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    here = _cast(camp, char, vid)
    elsewhere = scenes.create_scene(camp, "Scene two")
    handle = f"[[art:characters:{char}:gallery_1]]"
    assert art.resolve_handles(camp, handle, sid=here).startswith("![")
    assert art.resolve_handles(camp, handle, sid=elsewhere) == ""
    # ...and with no scene in hand the other two rules still stand
    assert art.resolve_handles(camp, handle).startswith("![")


def test_switching_the_section_off_skips_the_ranking_entirely(world, monkeypatch):
    """The prompt-layout toggle is this feature's off switch, so it has to turn
    off the WORK, not just the output: with an embeddings endpoint configured
    the catalogue makes a blocking HTTP call, and paying for one whose section
    will not render is the one case where "assemble everything, render what
    survives" costs real money."""
    from grimoire.store import config
    from grimoire.store.context import assemble, layout

    calls = []
    monkeypatch.setattr(assemble.art, "catalogue",
                        lambda *a, **k: calls.append(1) or [])
    camp, char, vid = world["cid"], world["char"], world["vid"]
    sid = _cast(camp, char, vid)

    assemble._assemble(camp, sid)
    assert calls == [1]                       # on by default

    layout.write_layout([{"id": s.id, "enabled": s.id != "available_art"}
                         for s in assemble.SECTIONS])
    config.write_config(prompt_layout_enabled="on")
    calls.clear()
    assemble._assemble(camp, sid)
    assert calls == []                        # switched off: never asked


def test_history_sends_a_picture_to_the_model_as_its_alt_text(world):
    """A post carries an image as `![description](url)` however it got there --
    the picker or a resolved handle. The model gets the description and not the
    URL: the URL costs ~27 tokens per image on every remaining turn and is a
    worked example of a shape the model must not produce, since a raw markdown
    URL is passed through unvalidated where a handle is checked."""
    from grimoire.store.context import story
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    md = art.resolve_handles(camp, f"She turns. [[art:characters:{char}:gallery_1]] Rain.")
    projected = story._project_history([{"role": "assistant", "content": md, "speaker": None}])
    text = projected[0]["content"]
    assert "Half-plate, rain-soaked, a burning keep behind her." in text
    assert "/api/campaigns/" not in text
    assert "![" not in text
    assert "She turns." in text and "Rain." in text


def test_a_picture_does_not_count_against_the_length_budget(world):
    """The model wrote a ten-character handle; the alt text came out of an
    author's sidecar. Counting it as prose punishes the model for doing what
    the available-art section asked -- and under a `terse` budget one image was
    enough on its own (7 phantom words, a phantom paragraph) to trip the drift
    correction and tell it to write less."""
    from grimoire.store import length_drift
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid)
    prose = "She turns from the rail, rain running off the pauldron, saying nothing."
    withart = art.resolve_handles(
        camp, f"{prose}\n\n[[art:characters:{char}:gallery_1]]")
    assert withart != prose                       # the picture really is in there
    assert length_drift._words(withart) == length_drift._words(prose)
    assert length_drift._paragraphs(withart) == length_drift._paragraphs(prose)


def test_the_documented_knobs_are_actually_readable(monkeypatch, tmp_path):
    """`read_config` narrows to `_CONFIG_KEYS`, so a key missing from that tuple
    is dropped in silence — "no error, just the wrong budget", as the comment
    on it says. Both of these were documented and neither was listed, so
    `settings()` answered with its defaults whatever anyone wrote."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config
    config.write_config(art_catalog_depth="9", art_catalog_threshold="0.7")
    got = art.settings()
    assert (got["depth"], got["threshold"]) == (9, 0.7)


def test_a_hand_edited_knob_falls_back_rather_than_raising(monkeypatch, tmp_path):
    """A store may be hand-edited or half-synced; a preference must not be able
    to take a turn down with it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config
    config.write_config(art_catalog_depth="lots", art_catalog_threshold="nan")
    got = art.settings()
    assert (got["depth"], got["threshold"]) == (art.DEFAULT_DEPTH, art.DEFAULT_THRESHOLD)


def test_semantic_mode_replaces_the_scores_but_keeps_the_name_rule(world, monkeypatch):
    """"Semantic as an upgrade" has to mean an upgrade. Replacing the scores
    wholesale switched off the commonest reason this feature is useful --
    a description that never mentions Seraphine is not close to a sentence about
    her either -- so configuring an endpoint would have quietly stopped her art
    being offered when the scene named her."""
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}],
                           loc, [])
    cands = [c for c in cands if c["kind"] != art.LIBRARY]

    # An endpoint that resolves, and a scorer that matches the harbour strongly
    # and everything else not at all.
    monkeypatch.setattr(art.embed_space, "resolve",
                        lambda cfg=None: {"space": "s", "model": "m", "key": "k",
                                          "base_url": "u"})
    monkeypatch.setattr(art, "_semantic_scores",
                        lambda cands, text, cfg: [0.9 if c["kind"] == "locations" else 0.0
                                                  for c in cands])
    got = art.rank(camp, cands, "Seraphine watched the boats.")
    # The cosine hit ranks first; the named record is still offered, last.
    assert [c["kind"] for c in got] == ["locations", "characters"]

    # ...and a record neither named nor matched is not offered at all.
    got = art.rank(camp, cands, "Nobody in particular said nothing.")
    assert [c["kind"] for c in got] == ["locations"]


def test_a_semantic_failure_falls_back_to_the_keyword_ranking(world, monkeypatch):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [])
    monkeypatch.setattr(art.embed_space, "resolve",
                        lambda cfg=None: {"space": "s", "model": "m", "key": "k",
                                          "base_url": "u"})
    monkeypatch.setattr(art, "_semantic_scores", lambda *a, **k: None)   # provider down
    got = art.rank(camp, cands, "Fishing boats sat at the quay, lost in fog.")
    assert [c["name"] for c in got] == ["gallery_1"]
