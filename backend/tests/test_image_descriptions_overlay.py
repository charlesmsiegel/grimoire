"""`overlay.read_description`'s per-image resolution, and the fork prune
carve-out that keeps a campaign's sidecar from being deduped out from under a
divergent image."""

import threading

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


def _world_legacy_key(wroot, cid, vid):
    """The fixture's world text as a legacy ``descriptions.json`` key too, the
    way a store written before stage 2 holds it."""
    image_descriptions.carry_legacy(assets.version_dir(wroot, cid, vid), "gallery_1",
                                    "The world's quay.")


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
    # Distinct bytes: one picture is one object with one description.
    assets.put_image(wroot, cid, vid, "gallery_2", b"png-2", "png")
    image_descriptions.set_description(wroot, cid, vid, "gallery_2", "")
    assets.put_image(overlay.croot_of(camp), cid, vid, "gallery_3", b"png-3", "png")
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
    # A campaign that diverged the art AND holds a description that happens to
    # read exactly like the world's -- which is what makes the file prunable.
    # Both as LEGACY sidecar keys, written directly: a description write onto
    # a resolving placement now lands on the image object (stage 2) and leaves
    # no sidecar, so the files this carve-out protects are the legacy ones.
    assets.put_image(croot, cid, vid, "gallery_1", b"other", "png")
    _world_legacy_key(wroot, cid, vid)
    d = croot / "characters" / cid / "assets" / vid
    image_descriptions.carry_legacy(d, "gallery_1", "The world's quay.")
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
    _world_legacy_key(wroot, cid, vid)          # a legacy world sidecar to duplicate
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
    # Distinct bytes: one picture is one object with one description.
    assets.put_image(wroot, cid, vid, "avatar", b"png-avatar", "png")
    image_descriptions.set_description(wroot, cid, vid, "avatar", "")
    overlay.promote_image(camp, cid, vid, "gallery_1")
    # the demoted avatar keeps its reviewed-empty mark in the gallery slot
    assert image_descriptions.read_all(overlay.croot_of(camp), cid, vid) == {
        "avatar": "The world's quay.", "gallery_1": ""}


def test_a_promotion_cannot_overwrite_a_description_saved_while_it_ran(pair, monkeypatch):
    """Promotion READS the world's legacy keys and carries them up a few
    statements later (R5). The sidecar lock serializes each write and does not
    span that gap, so a save landing inside it was read past and then masked by
    the stale carried key -- losing text somebody had just written. A
    read-modify-write needs the lock the other writer takes."""
    wroot, camp, cid, vid = pair
    # Distinct bytes: one picture is one object with one description. The
    # portrait's text is a legacy world key, the case a promote carries.
    assets.put_image(wroot, cid, vid, "avatar", b"png-avatar", "png")
    image_descriptions.carry_legacy(assets.version_dir(wroot, cid, vid), "avatar",
                                    "The world's portrait.")

    inside, done = threading.Event(), threading.Event()
    real = image_descriptions.legacy_text_in

    def slow_read(*a, **kw):
        out = real(*a, **kw)
        inside.set()
        done.wait(5)
        return out

    monkeypatch.setattr(image_descriptions, "legacy_text_in", slow_read)
    promoting = threading.Thread(target=overlay.promote_image,
                                 args=(camp, cid, vid, "gallery_1"))
    promoting.start()
    assert inside.wait(5)

    saving = threading.Thread(target=overlay.set_description,
                              args=(camp, cid, vid, "avatar", "The campaign's own words."))
    saving.start()
    saving.join(0.2)
    assert saving.is_alive()          # held out: the promotion has the campaign

    done.set()
    promoting.join(5)
    saving.join(5)
    assert not promoting.is_alive() and not saving.is_alive()
    # The save landed after the swap, so it is the last word -- rather than
    # being overwritten by a snapshot taken before it was ever written.
    assert overlay.read_description(camp, cid, vid, "avatar") == "The campaign's own words."
    # ...and the swap itself still happened: the demoted portrait keeps its own.
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "The world's portrait."


# ---- stage 2: a campaign edit of inherited art changes the shared image (D1) --

def _vdir(root, cid, vid):
    return assets.version_dir(root, cid, vid)


def _object_text(root, cid, vid, name):
    from grimoire.store import image_refs, image_store
    ref = image_refs.read(_vdir(root, cid, vid), name)
    return image_store.read(ref.image).raw.get("description")


def test_campaign_edit_clears_exactly_the_named_keys(pair):
    """R2/R3, Review Focus 2: the write lands on the object behind the visible
    (world) placement and clears the key in the directory edited (campaign A)
    and in the visible placement's directory (the world). Campaign B's key is
    nobody's to clear: it keeps masking the shared text until migration."""
    wroot, camp_a, cid, vid = pair
    camp_b = campaigns.create_campaign("Winifred's Watch", wroot.name)
    assets.put_image(wroot, cid, vid, "avatar", b"png-avatar", "png")
    image_descriptions.carry_legacy(_vdir(wroot, cid, vid), "avatar", "W")
    a_dir = _vdir(overlay.croot_of(camp_a), cid, vid)
    b_dir = _vdir(overlay.croot_of(camp_b), cid, vid)
    image_descriptions.carry_legacy(a_dir, "avatar", "C")
    image_descriptions.carry_legacy(b_dir, "avatar", "B")

    overlay.set_description(camp_a, cid, vid, "avatar", "New")

    assert _object_text(wroot, cid, vid, "avatar") == "New"
    assert image_descriptions.read(wroot, cid, vid, "avatar") == "New"
    assert "avatar" not in image_descriptions.read_raw(_vdir(wroot, cid, vid))
    assert overlay.read_description(camp_a, cid, vid, "avatar") == "New"
    assert "avatar" not in image_descriptions.read_raw(a_dir)
    assert overlay.read_description(camp_b, cid, vid, "avatar") == "B"
    assert overlay.read_descriptions(camp_b, cid, vid)["avatar"] == "B"
    assert image_descriptions.read_raw(b_dir)["avatar"] == "B"


def test_campaign_legacy_override_masks_until_edited(pair):
    """R1 from the campaign's side: its own legacy key for an inherited image
    masks the shared object's text, in the per-image read and the sweep alike,
    until an edit moves the text onto the object and clears it."""
    wroot, camp, cid, vid = pair
    cdir = _vdir(overlay.croot_of(camp), cid, vid)
    image_descriptions.carry_legacy(cdir, "gallery_1", "C")
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "C"
    assert overlay.read_descriptions(camp, cid, vid) == {"gallery_1": "C"}
    assert image_descriptions.read(wroot, cid, vid, "gallery_1") == "The world's quay."

    overlay.set_description(camp, cid, vid, "gallery_1", "New")
    assert overlay.read_description(camp, cid, vid, "gallery_1") == "New"
    assert overlay.read_descriptions(camp, cid, vid) == {"gallery_1": "New"}
    assert image_descriptions.read(wroot, cid, vid, "gallery_1") == "New"
    assert "gallery_1" not in image_descriptions.read_raw(cdir)


def test_inherited_legacy_world_file_keeps_the_campaign_key(pair):
    """No resolving world placement, so there is no object to write: the edit
    is the campaign's legacy key, exactly as before stage 2, and the world's
    own text stays its own."""
    wroot, camp, cid, vid = pair
    wdir = _vdir(wroot, cid, vid)
    (wdir / "gallery_2.png").write_bytes(b"png-legacy-world")
    image_descriptions.carry_legacy(wdir, "gallery_2", "The world's own words.")

    overlay.set_description(camp, cid, vid, "gallery_2", "New")

    cdir = _vdir(overlay.croot_of(camp), cid, vid)
    assert image_descriptions.read_raw(cdir)["gallery_2"] == "New"
    assert overlay.read_description(camp, cid, vid, "gallery_2") == "New"
    assert image_descriptions.read_raw(wdir)["gallery_2"] == "The world's own words."
    assert image_descriptions.read(wroot, cid, vid, "gallery_2") == "The world's own words."


@pytest.mark.parametrize("world_file", ["placement", "legacy"])
def test_promote_carries_a_world_legacy_text_as_a_campaign_key(pair, world_file):
    """R5: the world's raw key is the one text a promote would otherwise lose
    (after it, the campaign's slot answers by its own key or the object). It
    travels as a campaign LEGACY key, and the shared object is not touched."""
    wroot, camp, cid, vid = pair
    wdir = _vdir(wroot, cid, vid)
    if world_file == "legacy":
        (wdir / "gallery_2.png").write_bytes(b"png-legacy-world")
        name = "gallery_2"
    else:
        name = "gallery_1"          # the fixture's placement, object text set
    image_descriptions.carry_legacy(wdir, name, "W")

    overlay.promote_image(camp, cid, vid, name)

    cdir = _vdir(overlay.croot_of(camp), cid, vid)
    assert image_descriptions.read_raw(cdir)["avatar"] == "W"
    assert overlay.read_description(camp, cid, vid, "avatar") == "W"
    if world_file == "placement":
        assert _object_text(wroot, cid, vid, "gallery_1") == "The world's quay."
    else:
        assert _object_text(overlay.croot_of(camp), cid, vid, "avatar") is None


def test_promote_does_not_carry_object_text(pair):
    """Text that came from the object is still the object's after the promote
    (the campaign slot links the same image), so nothing is copied into a
    campaign key -- a copy would only mask the next edit of the shared text."""
    _wroot, camp, cid, vid = pair
    overlay.promote_image(camp, cid, vid, "gallery_1")
    cdir = _vdir(overlay.croot_of(camp), cid, vid)
    assert "avatar" not in image_descriptions.read_raw(cdir)
    assert "gallery_1" not in image_descriptions.read_raw(cdir)
    assert overlay.read_description(camp, cid, vid, "avatar") == "The world's quay."


def _counted(monkeypatch, module, name):
    calls = []
    real = getattr(module, name)

    def counted(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(module, name, counted)
    return calls


def test_shadowed_rows_read_the_shared_description(pair, monkeypatch):
    """A shadowed world copy carries the world's text -- here, the shared
    object's -- read off the world listing's own ids: no placement is scanned
    beyond what the listings themselves scan."""
    from grimoire.store import image_refs
    _wroot, camp, cid, vid = pair
    assets.put_image(overlay.croot_of(camp), cid, vid, "gallery_1", b"png-other", "png")

    with monkeypatch.context() as m:
        m.setattr(image_descriptions, "read_all", lambda *a, **kw: {})
        m.setattr(image_descriptions, "read_in", lambda *a, **kw: {})
        baseline = _counted(m, image_refs, "scan")
        overlay.shadowed_images(camp, cid, vid)
    scans = _counted(monkeypatch, image_refs, "scan")
    rows = overlay.shadowed_images(camp, cid, vid)
    assert [(r["name"], r.get("description")) for r in rows] == [
        ("gallery_1", "The world's quay.")]
    assert len(scans) == len(baseline)


def test_read_descriptions_scans_only_what_the_listing_scans(pair, monkeypatch):
    """The per-turn sweep takes its names and ids from the overlay listing it
    builds, so it reads no placement that listing did not."""
    from grimoire.store import image_refs
    _wroot, camp, cid, vid = pair
    assets.put_image(overlay.croot_of(camp), cid, vid, "gallery_3", b"png-3", "png")
    image_descriptions.set_description(overlay.croot_of(camp), cid, vid, "gallery_3", "Mine.")

    with monkeypatch.context() as m:
        listing = _counted(m, image_refs, "scan")
        overlay.list_images(camp, cid, vid)
    scans = _counted(monkeypatch, image_refs, "scan")
    got = overlay.read_descriptions(camp, cid, vid)
    assert got == {"gallery_1": "The world's quay.", "gallery_3": "Mine."}
    assert len(scans) == len(listing)
