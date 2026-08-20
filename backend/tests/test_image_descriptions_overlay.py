"""`overlay.read_description`'s per-image resolution, and the fork prune
carve-out that keeps a campaign's sidecar from being deduped out from under a
divergent image."""

import pytest

from grimoire.store import assets, campaigns, characters, image_descriptions, overlay, worlds
from grimoire.store.campaigns import lifecycle


@pytest.fixture
def pair(monkeypatch, tmp_path):
    """A world with one described character image + a thin campaign on it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine", "main")
    assets.put_image(wroot, cid, vid, "gallery_1", b"png", "png")
    image_descriptions.set_description(wroot, cid, vid, "gallery_1", "The world's quay.")
    camp = campaigns.create_campaign("Saltmarch", wid)
    return wroot, camp, cid, vid


def test_inherited_image_reads_the_world_description(pair):
    _wroot, camp, cid, vid = pair
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "The world's quay."


def test_campaign_may_describe_inherited_art_without_diverging_the_art(pair):
    _wroot, camp, cid, vid = pair
    overlay.set_description(camp, cid, vid, "gallery_1", "This campaign's take.")
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "This campaign's take."
    # the bytes are still the world's -- describing did not materialize the image
    assert assets.image_path(overlay.croot_of(camp), cid, vid, "gallery_1") is None


def test_divergent_campaign_image_does_not_inherit_the_worlds_description(pair):
    """The rule this feature turns on: a campaign-side `gallery_1` is different
    art, so captioning it with the world's sentence would describe another
    picture -- and that caption becomes alt text in a transcript."""
    _wroot, camp, cid, vid = pair
    assets.put_image(overlay.croot_of(camp), cid, vid, "gallery_1", b"other", "png")
    assert overlay.read_description(camp, cid, vid, "gallery_1") == ""


def test_tombstoned_image_reads_campaign_side_only(pair):
    _wroot, camp, cid, vid = pair
    overlay.delete_image(camp, cid, vid, "gallery_1")
    assert overlay.read_description(camp, cid, vid, "gallery_1") == ""


def test_read_descriptions_maps_the_visible_union(pair):
    wroot, camp, cid, vid = pair
    assets.put_image(wroot, cid, vid, "gallery_2", b"png", "png")
    image_descriptions.set_description(wroot, cid, vid, "gallery_2", "")
    assets.put_image(overlay.croot_of(camp), cid, vid, "gallery_3", b"png", "png")
    got = overlay.read_descriptions(camp, cid, vid)
    # gallery_1 described in the world; gallery_2 reviewed-empty (present, "");
    # gallery_3 campaign-side and never reviewed (absent, not "").
    assert got == {"gallery_1": "The world's quay.", "gallery_2": ""}


def test_set_description_rejects_an_image_the_union_does_not_hold(pair):
    _wroot, camp, cid, vid = pair
    with pytest.raises(ValueError):
        overlay.set_description(camp, cid, vid, "nope", "a picture")


def test_prune_keeps_a_sidecar_beside_a_divergent_campaign_image(pair):
    """A fork's dedupe pass must not drop a description sidecar out of a folder
    holding campaign-side art: `read_description` treats that art as
    authoritative and will not fall back to the world."""
    wroot, camp, cid, vid = pair
    croot = overlay.croot_of(camp)
    # A campaign that diverged the art AND wrote a description that happens to
    # read exactly like the world's -- which is what makes the file prunable.
    assets.put_image(croot, cid, vid, "gallery_1", b"other", "png")
    overlay.set_description(camp, cid, vid, "gallery_1", "The world's quay.")
    d = croot / "characters" / cid / "assets" / vid
    assert (d / image_descriptions.DESCRIPTIONS_FILE).exists()
    lifecycle._prune_duplicate_files(croot, wroot)
    assert (d / image_descriptions.DESCRIPTIONS_FILE).exists()
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "The world's quay."


def test_prune_still_drops_a_sidecar_from_a_folder_with_no_campaign_art(pair):
    """The carve-out is not "never prune": a folder holding only inherited art
    has a redundant sidecar, and the overlay serves the world's copy."""
    wroot, camp, cid, vid = pair
    croot = overlay.croot_of(camp)
    d = croot / "characters" / cid / "assets" / vid
    d.mkdir(parents=True, exist_ok=True)
    src = wroot / "characters" / cid / "assets" / vid / image_descriptions.DESCRIPTIONS_FILE
    (d / image_descriptions.DESCRIPTIONS_FILE).write_bytes(src.read_bytes())
    lifecycle._prune_duplicate_files(croot, wroot)
    assert not (d / image_descriptions.DESCRIPTIONS_FILE).exists()
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "The world's quay."


def test_describing_art_never_makes_a_record_look_edited_to_sync(pair):
    """The reason this is a sidecar and not a field on the card.

    Images are deliberately not hashed into a character card, so editing art
    does not make a character look edited to the world/campaign sync. A
    description in the card would have undone that: describing a picture would
    show up as a diverged record, and a campaign that had only ever described
    its own art would materialize the whole card. Nothing else enforces this,
    so it is pinned here.
    """
    from grimoire.store import sync
    wroot, camp, cid, vid = pair
    overlay.materialize_actor(camp, "characters", cid)
    assert sync.incoming(camp) == []

    overlay.set_description(camp, cid, vid, "gallery_1", "This campaign's take.")
    image_descriptions.set_description(wroot, cid, vid, "gallery_1", "A newer world take.")
    assert sync.incoming(camp) == []


def test_a_malformed_campaign_entry_does_not_mask_the_worlds_description(pair):
    """The sweep and the per-image read must agree.

    `read_all` drops a non-string value (a hand-edited or half-synced store must
    not hand a list to a template) but the raw key is still there. Deciding
    "the campaign has spoken" on raw key presence turned that into `""`: the
    world's perfectly good sentence was masked and the image looked
    reviewed-with-nothing-to-say. `read_description` never agreed."""
    _wroot, camp, cid, vid = pair
    croot = overlay.croot_of(camp)
    d = croot / "characters" / cid / "assets" / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text(
        '{"gallery_1": ["not", "a", "string"]}\n', encoding="utf-8")

    assert overlay.read_description(camp, cid, vid, "gallery_1") == "The world's quay."
    assert overlay.read_descriptions(camp, cid, vid) == {"gallery_1": "The world's quay."}


def test_promoting_inherited_art_takes_its_description_campaign_side(pair):
    """Copying the bytes up is what makes the campaign hold the picture, and
    from that moment the per-image rule stops falling through to the world --
    deliberately, because a campaign-side image is normally different art. Here
    it is the SAME art, so the sentence has to travel with it or promoting the
    picture silently strips its description in that campaign."""
    _wroot, camp, cid, vid = pair
    overlay.promote_image(camp, cid, vid, "gallery_1")
    assert overlay.read_description(camp, cid, vid, "avatar") == "The world's quay."


def test_a_reviewed_empty_description_travels_with_a_promotion_too(pair):
    """Key presence, not truthiness: `""` is "reviewed, nothing to say", and
    losing it walks the image back into a queue somebody already answered."""
    wroot, camp, cid, vid = pair
    assets.put_image(wroot, cid, vid, "avatar", b"png", "png")
    image_descriptions.set_description(wroot, cid, vid, "avatar", "")
    overlay.promote_image(camp, cid, vid, "gallery_1")
    # the demoted avatar keeps its reviewed-empty mark in the gallery slot
    assert image_descriptions.read_all(overlay.croot_of(camp), cid, vid) == {
        "avatar": "The world's quay.", "gallery_1": ""}
