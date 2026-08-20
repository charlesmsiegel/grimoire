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
    # Resolution is scene-scoped -- `sid` is required, not optional -- so the
    # fixture carries a scene for every test to resolve against.
    return {"wid": wid, "wroot": wroot, "cid": camp, "char": cid, "vid": vid,
            "loc": loc, "sid": scenes.create_scene(camp, "Scene one")}


@pytest.fixture
def sid(world):
    """The fixture scene, for the many tests that only need one."""
    return world["sid"]


def _cast(camp, char, vid, sid):
    """Put the character on stage in `sid`, so a locked version exists."""
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

def test_pool_covers_cast_setting_worldinfo_and_library(world, sid):
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid, sid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}],
                           loc, [])
    handles = {c["handle"] for c in cands}
    assert handles == {f"[[art:characters:{char}:gallery_1]]",
                       f"[[art:locations:{loc}:gallery_1]]",
                       "[[art:campaign:coastline]]"}


def test_pool_excludes_undescribed_and_reviewed_empty_art(world, sid):
    """Both halves of the absent-vs-empty distinction stay out of the offer."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    wroot = world["wroot"]
    _cast(camp, char, vid, sid)
    assets.put_image(wroot, char, vid, "gallery_2", b"png", "png")          # never reviewed
    assets.put_image(wroot, char, vid, "gallery_3", b"png", "png")
    image_descriptions.set_description(wroot, char, vid, "gallery_3", "")   # reviewed-empty
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    assert {c["name"] for c in cands} == {"gallery_1", "coastline"}


def test_pool_excludes_an_actor_who_is_not_cast(world, sid):
    """No locked version means no offer: the catalogue only ever shows art of
    actors who are actually on stage."""
    camp, char = world["cid"], world["char"]
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    assert {c["name"] for c in cands} == {"coastline"}


def test_pool_deduplicates_a_location_that_is_both_setting_and_world_info(world, sid):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [{"kind": "locations", "id": loc}])
    assert sum(1 for c in cands if c["id"] == loc) == 1


# ---- ranking ---------------------------------------------------------------

def test_keyword_ranking_needs_two_shared_terms(world, sid):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [])
    # one shared content word ("boats") is not enough on an unnamed record
    assert art.rank(camp, cands, "She watched the boats.") == []
    # two are
    got = art.rank(camp, cands, "Fishing boats sat at the quay, lost in fog.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_naming_the_record_is_enough_on_its_own(world, sid):
    """The commonest useful case, and the one the docstring advertises: the
    record being named in the scene IS the evidence, so its art is eligible
    even when the description shares no vocabulary with the post."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid, sid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}], None, [])
    cands = [c for c in cands if c["kind"] == "characters"]
    # Not named, one shared word ("rain") -- below the two an unnamed record needs.
    assert art.rank(camp, cands, "The rain kept on.") == []
    # Named, and sharing NOTHING: still offered.
    got = art.rank(camp, cands, "Seraphine draws her blade.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_shared_words_outrank_a_bare_name(world, sid):
    """Naming is evidence, not a trump card: a description that actually
    describes the moment sorts above art whose record merely got mentioned."""
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid, sid)
    cands = art.candidates(camp, [{"kind": "characters", "id": char, "role": "npc"}],
                           loc, [])
    cands = [c for c in cands if c["kind"] != art.LIBRARY]
    got = art.rank(camp, cands, "Seraphine watched the fishing boats at the quay, lost in fog.")
    # The harbour picture shares "fishing"/"boats"/"quay"/"fog"; Seraphine's
    # shares nothing and rides on her name alone.
    assert [c["kind"] for c in got] == ["locations", "characters"]


def test_rank_respects_depth(world, sid, monkeypatch):
    camp, loc = world["cid"], world["loc"]
    monkeypatch.setattr(art, "settings", lambda: {"depth": 0, "threshold": 0.4})
    assert art.rank(camp, art.candidates(camp, [], loc, []), "fishing boats quay fog") == []


def test_catalogue_never_raises(world, sid, monkeypatch):
    monkeypatch.setattr(art, "candidates",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("store is gone")))
    assert art.catalogue(world["cid"], [], None, [], "anything") == []


# ---- the return path -------------------------------------------------------

def test_resolve_rewrites_a_valid_handle_with_the_description_as_alt(world, sid):
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid, sid)
    out = art.resolve_handles(camp, f"She turns. [[art:characters:{char}:gallery_1]] Rain.", sid)
    assert out == (
        "She turns. ![Half-plate, rain-soaked, a burning keep behind her.]"
        f"(/api/campaigns/{camp}/characters/{char}/versions/{vid}/images/gallery_1) Rain.")


def test_resolve_rewrites_a_library_handle(world, sid):
    camp = world["cid"]
    out = art.resolve_handles(camp, "[[art:campaign:coastline]]", sid)
    assert out == (f"![A hand-drawn map of the northern coastline.]"
                   f"(/api/campaigns/{camp}/images/coastline)")


def test_resolve_deletes_a_handle_naming_nothing(world, sid):
    camp = world["cid"]
    assert art.resolve_handles(camp, "before [[art:characters:ghost:gallery_1]] after", sid) == (
        "before  after")
    assert art.resolve_handles(camp, "x [[art:campaign:nope]] y", sid) == "x  y"
    assert art.resolve_handles(camp, "x [[art:sausages:a:b]] y", sid) == "x  y"


def test_resolve_deletes_a_handle_for_a_real_but_undescribed_image(world, sid):
    """The rule that makes stateless resolution safe: an image the model was
    never offered -- because nobody described it -- cannot be reached by
    composing a plausible handle for it."""
    camp, char, vid, wroot = world["cid"], world["char"], world["vid"], world["wroot"]
    _cast(camp, char, vid, sid)
    assets.put_image(wroot, char, vid, "gallery_9", b"png", "png")
    assert assets.image_path(wroot, char, vid, "gallery_9") is not None
    assert art.resolve_handles(camp, f"[[art:characters:{char}:gallery_9]]", sid) == ""


def test_resolve_deletes_a_handle_for_a_tombstoned_image(world, sid):
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid, sid)
    overlay.delete_image(camp, char, vid, "gallery_1")
    assert art.resolve_handles(camp, f"[[art:characters:{char}:gallery_1]]", sid) == ""


def test_resolve_escapes_brackets_in_the_alt_text(world, sid):
    """A name can hold neither `]` nor `)`, but a DESCRIPTION can hold both --
    and an unescaped `]` would close the alt text early."""
    camp = world["cid"]
    image_descriptions.set_in(campaign_images.images_dir(camp), "coastline",
                              "A map [annotated] by the\nharbourmaster.")
    out = art.resolve_handles(camp, "[[art:campaign:coastline]]", sid)
    assert out.startswith("![A map (annotated) by the harbourmaster.](")
    assert "]" not in out[: out.index("](")]


def test_resolve_leaves_text_with_no_handles_untouched(world, sid):
    text = "Ordinary narration, with a [markdown link](http://example.invalid) in it."
    assert art.resolve_handles(world["cid"], text, sid) == text


# ---- what statelessness would otherwise let through ------------------------

def test_a_link_breaking_image_name_is_percent_encoded(world, sid):
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
        out = art.resolve_handles(camp, f"[[art:locations:{loc}:{raw}]]", sid)
        assert out == f"![A quay.](/api/campaigns/{camp}/locations/{loc}/images/{encoded})"
        # the whole destination is inside the parens -- nothing spilled
        assert out.count("(") == out.count(")") == 1


def test_a_gm_only_entitys_art_never_resolves(world, sid):
    """A `gm-only` entry's body never reaches a prompt at all, so a picture of
    it appearing in a post the player reads is a straight leak. The catalogue
    would never offer it; the stateless resolver has to refuse it too."""
    camp, wroot = world["cid"], world["wroot"]
    sec = entities.create_entity(wroot, "locations", "The Vault", "hidden",
                                 secrecy="gm-only")
    assets.put_image(wroot, sec, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(wroot, sec, "default", "gallery_1",
                                       "A locked door.", base="locations")
    assert art.resolve_handles(camp, f"[[art:locations:{sec}:gallery_1]]", sid) == ""


def test_a_secret_entitys_art_still_resolves(world, sid):
    """`secret` is not `gm-only`: its body DOES reach the prompt, and the
    catalogue does offer its art -- so refusing it here would make the two
    halves disagree about the same picture."""
    camp, wroot = world["cid"], world["wroot"]
    sec = entities.create_entity(wroot, "locations", "The Cellar", "quiet",
                                 secrecy="secret")
    assets.put_image(wroot, sec, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(wroot, sec, "default", "gallery_1",
                                       "A cellar.", base="locations")
    assert art.resolve_handles(camp, f"[[art:locations:{sec}:gallery_1]]", sid).startswith("![A cellar.]")


def test_an_actor_not_cast_in_this_scene_does_not_resolve(world, sid):
    """An actor must be cast in the scene the reply belongs to. Without this,
    art of anyone the campaign has ever cast resolves in every scene it has --
    and `sid` is a required argument precisely so the weaker gate is not one
    forgotten parameter away."""
    camp, char, vid = world["cid"], world["char"], world["vid"]
    here = _cast(camp, char, vid, sid)
    elsewhere = scenes.create_scene(camp, "Scene two")
    handle = f"[[art:characters:{char}:gallery_1]]"
    assert art.resolve_handles(camp, handle, here).startswith("![")
    assert art.resolve_handles(camp, handle, elsewhere) == ""


def test_switching_the_section_off_skips_the_ranking_entirely(world, sid, monkeypatch):
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
    sid = _cast(camp, char, vid, sid)

    assemble._assemble(camp, sid)
    assert calls == [1]                       # on by default

    layout.write_layout([{"id": s.id, "enabled": s.id != "available_art"}
                         for s in assemble.SECTIONS])
    config.write_config(prompt_layout_enabled="on")
    calls.clear()
    assemble._assemble(camp, sid)
    assert calls == []                        # switched off: never asked


def test_history_sends_a_picture_to_the_model_as_its_alt_text(world, sid):
    """A post carries an image as `![description](url)` however it got there --
    the picker or a resolved handle. The model gets the description and not the
    URL: the URL costs ~27 tokens per image on every remaining turn and is a
    worked example of a shape the model must not produce, since a raw markdown
    URL is passed through unvalidated where a handle is checked."""
    from grimoire.store.context import story
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid, sid)
    md = art.resolve_handles(camp, f"She turns. [[art:characters:{char}:gallery_1]] Rain.", sid)
    projected = story._project_history([{"role": "assistant", "content": md, "speaker": None}])
    text = projected[0]["content"]
    assert "Half-plate, rain-soaked, a burning keep behind her." in text
    assert "/api/campaigns/" not in text
    assert "![" not in text
    assert "She turns." in text and "Rain." in text


def test_a_picture_does_not_count_against_the_length_budget(world, sid):
    """The model wrote a ten-character handle; the alt text came out of an
    author's sidecar. Counting it as prose punishes the model for doing what
    the available-art section asked -- and under a `terse` budget one image was
    enough on its own (7 phantom words, a phantom paragraph) to trip the drift
    correction and tell it to write less."""
    from grimoire.store import length_drift
    camp, char, vid = world["cid"], world["char"], world["vid"]
    _cast(camp, char, vid, sid)
    prose = "She turns from the rail, rain running off the pauldron, saying nothing."
    withart = art.resolve_handles(
        camp, f"{prose}\n\n[[art:characters:{char}:gallery_1]]", sid)
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


def test_semantic_mode_replaces_the_scores_but_keeps_the_name_rule(world, sid, monkeypatch):
    """"Semantic as an upgrade" has to mean an upgrade. Replacing the scores
    wholesale switched off the commonest reason this feature is useful --
    a description that never mentions Seraphine is not close to a sentence about
    her either -- so configuring an endpoint would have quietly stopped her art
    being offered when the scene named her."""
    camp, char, vid, loc = world["cid"], world["char"], world["vid"], world["loc"]
    _cast(camp, char, vid, sid)
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


def test_a_semantic_failure_falls_back_to_the_keyword_ranking(world, sid, monkeypatch):
    camp, loc = world["cid"], world["loc"]
    cands = art.candidates(camp, [], loc, [])
    monkeypatch.setattr(art.embed_space, "resolve",
                        lambda cfg=None: {"space": "s", "model": "m", "key": "k",
                                          "base_url": "u"})
    monkeypatch.setattr(art, "_semantic_scores", lambda *a, **k: None)   # provider down
    got = art.rank(camp, cands, "Fishing boats sat at the quay, lost in fog.")
    assert [c["name"] for c in got] == ["gallery_1"]


def test_a_backticked_handle_still_becomes_an_image(world, sid):
    """The section used to print handles in backticks, under an instruction to
    write one "exactly as spelled below" -- so a model that obliged produced a
    code span, which renders as literal text and never shows the picture. The
    section prints them bare now; this is the other half, because markdown habit
    outlives a template edit."""
    camp = world["cid"]
    out = art.resolve_handles(camp, "Look. `[[art:campaign:coastline]]`", sid)
    assert out == (f"Look. ![A hand-drawn map of the northern coastline.]"
                   f"(/api/campaigns/{camp}/images/coastline)")


def test_a_lone_backtick_beside_a_handle_is_left_alone(world, sid):
    """Matched pair or nothing: a backtick that belongs to something else must
    not be eaten."""
    camp = world["cid"]
    out = art.resolve_handles(camp, "a `code` span and [[art:campaign:coastline]]`", sid)
    assert out.startswith("a `code` span and ![")
    assert out.endswith("`")


def test_the_section_offers_handles_bare(world, sid):
    """The rendered section must not print the shape that breaks when copied."""
    from grimoire import prompts
    rendered = prompts.render(
        "scene/sections/available_art.j2",
        available_art=[{"handle": "[[art:campaign:coastline]]", "description": "A map."}])
    assert "[[art:campaign:coastline]]" in rendered
    assert "`[[art:" not in rendered


def test_only_one_picture_per_reply_lands(world, sid):
    """"At most one picture per reply" is what the section asks for, and every
    other clause of that contract is a rule resolution applies. Left as advice,
    a model offered four candidates has an obvious way to use all four."""
    camp = world["cid"]
    for name in ("the-inn", "the-keep"):
        campaign_images.put_image(camp, name, b"png", "png")
        image_descriptions.set_in(campaign_images.images_dir(camp), name, f"A {name}.")
    out = art.resolve_handles(camp, "A. [[art:campaign:coastline]] B. [[art:campaign:the-inn]] "
                                    "C. [[art:campaign:the-keep]]", sid)
    assert out.count("![") == 1
    assert "[[art:" not in out
    assert out.startswith("A. ![A hand-drawn map")     # the first one wins
    assert "B." in out and "C." in out                 # the prose is untouched


def test_an_unresolvable_first_handle_does_not_spend_the_one_slot(world, sid):
    """The cap counts pictures that LANDED, not handles that were written --
    otherwise a typo in the first one would silently suppress a good second."""
    camp = world["cid"]
    out = art.resolve_handles(camp, "[[art:campaign:nope]] then [[art:campaign:coastline]]", sid)
    assert out.count("![") == 1
    assert "A hand-drawn map" in out


def test_a_short_name_is_matched_as_a_word_not_a_substring(world, sid, monkeypatch):
    """`world_state.keyword_hit` exists so retrieval selects by one set of
    semantics "rather than a lookalike that drifts from them" -- and a substring
    test here was exactly that lookalike, and looser: a character called Rain
    counted as named by the word "training". Short names are common enough
    (Ash, Ari, Ivo) that this was a steady source of art nobody asked for."""
    monkeypatch.setattr(art, "_record_name", lambda cid, c: "Rain")
    cands = [{"kind": "characters", "id": "x", "vid": "v", "description": "A portrait."}]
    assert art._keyword_scores("c", cands, "He spent the morning training hard.")[0] == [0.0]
    assert art._keyword_scores("c", cands, "Rain stepped through the door.")[0] == [1.0]
    # ...and case still does not matter, which is what `keyword_hit` promises
    assert art._keyword_scores("c", cands, "then rain came.")[0] == [1.0]


def test_a_name_a_word_boundary_cannot_bound_still_matches(world, sid, monkeypatch):
    """`\\b` sits between a word character and a non-word one, and two adjacent
    CJK characters are both word characters -- so the boundary never appears and
    a Japanese name would simply never match. That is the fallback's whole
    reason for existing."""
    monkeypatch.setattr(art, "_record_name", lambda cid, c: "霧子")
    cands = [{"kind": "characters", "id": "x", "vid": "v", "description": "A portrait."}]
    assert art._keyword_scores("c", cands, "霧子は埠頭に立っていた。")[0] == [1.0]
    assert art._keyword_scores("c", cands, "彼女は埠頭に立っていた。")[0] == [0.0]


def test_a_name_with_ordinary_punctuation_is_still_bounded(world, sid, monkeypatch):
    """`Mara O'Dell` and `Jean-Luc` are names a boundary can bound; the
    fallback is for scripts without word spacing, not for apostrophes."""
    monkeypatch.setattr(art, "_record_name", lambda cid, c: "Mara O'Dell")
    cands = [{"kind": "characters", "id": "x", "vid": "v", "description": "A portrait."}]
    assert art._keyword_scores("c", cands, "Mara O'Dell drew her cloak in.")[0] == [1.0]
    assert art._keyword_scores("c", cands, "Nobody was there.")[0] == [0.0]


def test_three_letter_nouns_count_as_shared_terms(world, sid):
    """A four-letter floor discarded exactly the nouns a picture description
    leans on. "Fog over the sea at dawn" and "down to the sea in the fog and
    found the inn" share fog, sea and inn -- and every one of them was thrown
    away, so the obvious picture for that moment was never offered."""
    shared = art._terms("Fog over the sea at dawn, the inn's lamp still lit.") & art._terms(
        "They came down to the sea in the fog and found the inn.")
    assert shared == {"fog", "sea", "inn"}


def test_function_words_still_carry_no_match(world, sid):
    """The floor only works because the stoplist reaches down to meet it: two
    unrelated sentences of English must share nothing."""
    assert not (art._terms("A red door and a low wall.")
                & art._terms("She was not there and did not care."))
    for filler in ("the", "and", "was", "you", "how", "did", "got"):
        assert filler not in art._terms(f"{filler} something entirely")
