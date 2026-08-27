import threading

import pytest

from grimoire.store import assets, characters, entities, image_descriptions


def _chars(tmp_path, images=("avatar", "gallery_1")):
    cid, vid = characters.create_character(tmp_path, "Seraphine", "main")
    for name in images:
        assets.put_image(tmp_path, cid, vid, name, b"png", "png")
    return cid, vid


def _dir_of(tmp_path, cid, vid):
    return tmp_path / "characters" / cid / "assets" / vid


def test_roundtrip_and_missing_file(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    assert image_descriptions.read_in(d) == {}
    image_descriptions.write_in(d, {"avatar": "Rain-soaked, the keep burning."})
    assert image_descriptions.read_in(d) == {"avatar": "Rain-soaked, the keep burning."}


def test_write_rejects_unknown_image_and_persists_empty(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    with pytest.raises(ValueError):
        image_descriptions.write_in(d, {"nope": "a picture"})
    image_descriptions.write_in(d, {"avatar": "In half-plate.", "gallery_1": ""})
    # An explicit "" persists: "reviewed, deliberately no description".
    assert image_descriptions.read_in(d) == {"avatar": "In half-plate.", "gallery_1": ""}


def test_read_drops_vanished_images(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "one", "gallery_1": "two"})
    assets.delete_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "one"}


def test_read_tolerates_garbled_sidecar(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text("{not json", encoding="utf-8")
    assert image_descriptions.read_in(d) == {}


def test_read_tolerates_non_string_values(tmp_path):
    _cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, _cid, vid)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text(
        '{"avatar": ["a", "list"], "gallery_1": "kept"}', encoding="utf-8")
    assert image_descriptions.read_in(d) == {"gallery_1": "kept"}


def test_set_in_updates_one_entry(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "one")
    image_descriptions.set_in(d, "gallery_1", "two")
    image_descriptions.set_in(d, "avatar", "")
    assert image_descriptions.read_in(d) == {"avatar": "", "gallery_1": "two"}


def test_set_in_rejects_unknown_image(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    with pytest.raises(ValueError):
        image_descriptions.set_in(d, "nope", "a picture")


def test_set_in_preserves_entries_for_images_that_vanished_outside_the_api(tmp_path):
    """Raw read on the modify path: editing one image's entry does not discard
    another's just because its file is missing right now.

    The case is a synced store, not a deletion -- `assets.delete_image` takes
    the entry with the bytes, so a file that is gone *and* whose entry is gone
    is the API's own doing. A sync client that has not yet brought the file
    down leaves the entry with nothing behind it, and a `write_in`-style strict
    rewrite here would silently discard the author's text for an image that is
    about to come back.
    """
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "one")
    image_descriptions.set_in(d, "gallery_1", "two")
    (d / "gallery_1.png").unlink()          # vanished behind our back
    assert image_descriptions.read_in(d) == {"avatar": "one"}   # not offered while absent
    image_descriptions.set_in(d, "avatar", "one edited")
    (d / "gallery_1.png").write_bytes(b"png")                   # sync catches up
    assert image_descriptions.read_in(d) == {"avatar": "one edited", "gallery_1": "two"}


def test_version_wrappers(tmp_path):
    cid, vid = _chars(tmp_path)
    assert image_descriptions.read(tmp_path, cid, vid, "avatar") == ""
    image_descriptions.set_description(tmp_path, cid, vid, "avatar", "In half-plate.")
    assert image_descriptions.read(tmp_path, cid, vid, "avatar") == "In half-plate."
    assert image_descriptions.read_all(tmp_path, cid, vid) == {"avatar": "In half-plate."}


def test_version_wrappers_reject_unsafe_ids(tmp_path):
    _cid, vid = _chars(tmp_path)
    assert image_descriptions.read(tmp_path, "../evil", vid, "avatar") == ""
    with pytest.raises(ValueError):
        image_descriptions.set_description(tmp_path, "../evil", vid, "avatar", "x")


def test_entity_base(tmp_path):
    eid = entities.create_entity(tmp_path, "locations", "Saltmarch Harbour", "A grey quay.")
    assets.put_image(tmp_path, eid, "default", "gallery_1", b"png", "png", base="locations")
    image_descriptions.set_description(tmp_path, eid, "default", "gallery_1",
                                       "The quay at low tide.", base="locations")
    assert image_descriptions.read(tmp_path, eid, "default", "gallery_1",
                                   base="locations") == "The quay at low tide."


def test_delete_image_drops_the_description(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "one", "gallery_1": "two"})
    assets.delete_image(tmp_path, cid, vid, "avatar")
    # The raw sidecar, not the filtered read: the entry is really gone, so
    # re-uploading under the same name does not resurrect a stale description.
    assert image_descriptions.read_raw(d) == {"gallery_1": "two"}


def test_undescribed_lists_only_images_with_no_key(tmp_path):
    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1", "gallery_2"))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "described", "gallery_1": ""})
    # `avatar` has text and `gallery_1` an explicit "" -- both reviewed.
    assert image_descriptions.undescribed(tmp_path, "characters") == [
        {"id": cid, "vid": vid, "name": "gallery_2"}]


def test_undescribed_empty_when_no_base_dir(tmp_path):
    assert image_descriptions.undescribed(tmp_path, "characters") == []


def test_catalog_lists_every_image_with_its_review_state(tmp_path):
    """The gallery's listing (#200) is the describe queue's walk without the
    filter: described, reviewed-empty and unreviewed art all appear, and each
    row says which it is."""
    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1", "gallery_2"))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "In half-plate.", "gallery_1": ""})
    rows = image_descriptions.catalog(tmp_path, "characters")
    assert [(r["name"], r["described"], r["description"]) for r in rows] == [
        ("avatar", True, "In half-plate."),
        ("gallery_1", True, ""),      # reviewed, deliberately blank
        ("gallery_2", False, ""),     # never looked at
    ]
    # The cache-busting token comes along, so a tile can request `?v=` and be
    # served immutable rather than revalidating every image in the gallery.
    assert all(r["v"] and r["ext"] == "png" for r in rows)


def test_catalog_reads_a_non_string_description_as_empty(tmp_path):
    """Same tolerance `read_in` has, for the same reason: a hand-edited or
    half-synced sidecar must not hand a list to a caller expecting text. The
    key is still present, so the image is still *reviewed*."""
    cid, vid = _chars(tmp_path, images=("avatar",))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.path_in(d).write_text('{"avatar": ["not", "text"]}', encoding="utf-8")
    (row,) = image_descriptions.catalog(tmp_path, "characters")
    assert (row["id"], row["vid"], row["name"]) == (cid, vid, "avatar")
    assert (row["described"], row["description"]) == (True, "")


def test_catalog_empty_when_no_base_dir(tmp_path):
    assert image_descriptions.catalog(tmp_path, "characters") == []


def test_catalog_skips_stranded_promotion_residue(tmp_path):
    """A gallery tile links to the serve route, and nothing serves
    `promote-tmp` -- the same filter the describe queue takes."""
    cid, vid = _chars(tmp_path)
    (_dir_of(tmp_path, cid, vid) / "promote-tmp.png").write_bytes(b"png")
    assert {r["name"] for r in image_descriptions.catalog(tmp_path, "characters")} == {
        "avatar", "gallery_1"}


def test_promotion_moves_descriptions_with_the_bytes(tmp_path):
    """A description is a claim about particular bytes, so it travels with them.
    Without this the swap left each picture wearing the other's sentence."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait.",
                                    "gallery_1": "Half-plate in the rain."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "Half-plate in the rain.",
                                             "gallery_1": "The old portrait."}


def test_promotion_with_no_avatar_takes_the_description_along(tmp_path):
    """With nothing to swap back the promoted image LEAVES its gallery slot, so
    its description has to leave with it rather than be dropped."""
    cid, vid = _chars(tmp_path, images=("gallery_1",))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"gallery_1": "Half-plate in the rain."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"avatar": "Half-plate in the rain."}


def test_promoting_an_undescribed_image_clears_the_avatars_description(tmp_path):
    """The avatar slot must not keep words written about the picture that just
    left it: the new occupant is different art and is simply undescribed."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait."})
    assets.promote_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_raw(d) == {"gallery_1": "The old portrait."}


def test_stranded_promotion_residue_is_never_describable(tmp_path):
    """`assets.list_in` shows a stranded `promote-tmp` on purpose -- crash
    residue is worth seeing (#253) -- but nothing can serve, promote or delete
    it. Unfiltered it entered the describe queue and could take a sidecar entry
    that the next ordinary listing would strand under a key no image has."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    (d / "promote-tmp.png").write_bytes(b"png")
    assert "promote-tmp" in {i["name"] for i in assets.list_in(d)}   # visible...
    assert "promote-tmp" not in image_descriptions._names(d)         # ...not describable
    assert {i["name"] for i in image_descriptions.undescribed(tmp_path, "characters")} == {
        "avatar", "gallery_1"}
    with pytest.raises(ValueError):
        image_descriptions.set_in(d, "promote-tmp", "crash residue")


def test_a_description_save_cannot_land_in_the_middle_of_a_promotion(tmp_path, monkeypatch):
    """One file, one lock.

    A promotion snapshots the sidecar and rewrites it after the bytes have
    moved; a save read-modify-writes the same file. They used to hold
    *different* locks -- `_image_lock` on the slots being swapped versus
    `locks.campaign_lock` -- so a save landing inside that window was read
    back out and overwritten by the promotion's rewrite, and the author's
    sentence was simply gone.
    """
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait.",
                                    "gallery_1": "Half-plate in the rain."})

    inside, release = threading.Event(), threading.Event()
    real_put = assets.put_image

    def blocking_put(*a, **kw):
        # Called from inside the promotion, after it has taken the sidecar lock.
        if not inside.is_set():
            inside.set()
            release.wait(5)
        return real_put(*a, **kw)

    monkeypatch.setattr(assets, "put_image", blocking_put)
    promoting = threading.Thread(
        target=assets.promote_image, args=(tmp_path, cid, vid, "gallery_1"))
    promoting.start()
    assert inside.wait(5)

    saving = threading.Thread(target=image_descriptions.set_description,
                              args=(tmp_path, cid, vid, "avatar", "Newly written."))
    saving.start()
    # It must NOT get in: the promotion is holding the sidecar mid-swap.
    saving.join(0.2)
    assert saving.is_alive()

    release.set()
    promoting.join(5)
    saving.join(5)
    assert not promoting.is_alive() and not saving.is_alive()
    # The save landed after the swap, so it is the last word -- rather than
    # being clobbered by the promotion's rewrite of the slot it names.
    assert image_descriptions.read_in(d) == {"avatar": "Newly written.",
                                             "gallery_1": "The old portrait."}


def test_a_failed_promotion_does_not_wedge_the_directory(tmp_path, monkeypatch):
    """Every step of the swap can raise, and a hand-rolled `acquire()` whose
    `release()` sat in the last statement's `finally` leaked the lock on any of
    them -- wedging every later description save, deletion and promotion for
    this directory, on a worker thread that had already returned."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"avatar": "The old portrait."})

    def boom(*a, **kw):
        raise OSError("the disk went away mid-swap")

    monkeypatch.setattr(assets, "put_image", boom)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, cid, vid, "gallery_1")
    monkeypatch.undo()

    # The lock is free, so ordinary work still lands. (Same thread, so an RLock
    # left owned here would not block -- ask it directly.)
    assert assets.sidecar_lock(d, image_descriptions.DESCRIPTIONS_FILE).acquire(blocking=False)
    assets.sidecar_lock(d, image_descriptions.DESCRIPTIONS_FILE).release()
    image_descriptions.set_in(d, "gallery_1", "Half-plate in the rain.")
    assert image_descriptions.read_in(d)["gallery_1"] == "Half-plate in the rain."


def test_an_unrelated_save_leaves_a_malformed_entry_exactly_as_it_found_it(tmp_path):
    """`read_in` drops a non-string value rather than handing a list to a
    template. Stringifying the whole mapping on the way out undid that: the
    next read accepted `"['not', 'a', 'string']"` as somebody's description,
    where it could mask an inherited one and reach a prompt as alt text."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    (d / image_descriptions.DESCRIPTIONS_FILE).write_text(
        '{"avatar": ["not", "a", "string"]}\n', encoding="utf-8")

    image_descriptions.set_in(d, "gallery_1", "Half-plate in the rain.")
    raw = image_descriptions.read_raw(d)
    assert raw["avatar"] == ["not", "a", "string"]     # untouched, still hand-fixable
    assert image_descriptions.read_in(d) == {"gallery_1": "Half-plate in the rain."}


def test_a_failed_deletion_keeps_the_description_it_would_have_dropped(tmp_path, monkeypatch):
    """`delete_in` swallows an unlink failure by design -- a scanner holding
    the file on Windows, a read-only directory. Dropping the sentence anyway
    loses what somebody wrote about a picture that is still sitting there."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"gallery_1": "Half-plate in the rain."})

    monkeypatch.setattr(assets, "delete_in", lambda *a, **kw: None)  # the unlink that did not
    assets.delete_image(tmp_path, cid, vid, "gallery_1")
    assert image_descriptions.read_in(d) == {"gallery_1": "Half-plate in the rain."}


def test_a_delete_that_lost_the_race_to_an_upload_keeps_its_hands_off_the_sidecar(
        tmp_path, monkeypatch):
    """`delete_in` takes this slot's lock and gives it back, and in that gap an
    upload can publish replacement bytes -- which reads as "the delete failed",
    so the removed picture's sentence stays captioning the new one. Under one
    hold, the upload cannot get in until the sidecar decision is made."""
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.write_in(d, {"gallery_1": "Half-plate in the rain."})

    inside, done = threading.Event(), threading.Event()
    real_delete_in = assets.delete_in

    def slow_delete_in(*a, **kw):
        real_delete_in(*a, **kw)
        inside.set()
        done.wait(5)          # the window an upload used to slip through

    monkeypatch.setattr(assets, "delete_in", slow_delete_in)
    deleting = threading.Thread(
        target=assets.delete_image, args=(tmp_path, cid, vid, "gallery_1"))
    deleting.start()
    assert inside.wait(5)

    uploading = threading.Thread(
        target=assets.put_image, args=(tmp_path, cid, vid, "gallery_1", b"png", "png"))
    uploading.start()
    uploading.join(0.2)
    assert uploading.is_alive()          # held out until the deletion is finished

    done.set()
    deleting.join(5)
    uploading.join(5)
    # The delete really did happen, so its description went with it -- and the
    # re-uploaded picture is undescribed rather than wearing the old words.
    assert image_descriptions.read_raw(d) == {}

# --- The cheap count, and what keeps it honest -------------------------------
#
# `undescribed_count` and `has_undescribed` exist because the to-do list needs
# this number for every world on every read, and building the list to get it
# resolves an extension and a cache-busting token per image -- a stat apiece.
# They walk the same tree by the same two rules (`assets.storable`, and key
# PRESENCE rather than non-empty text), and the tests below are what hold them
# to it. A cheap count that can drift from the list behind it is the stale
# number the whole to-do page is arranged to not have.


def test_the_count_agrees_with_the_list_it_stands_for(tmp_path):
    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1", "gallery_2"))
    d = _dir_of(tmp_path, cid, vid)

    assert image_descriptions.undescribed_count(tmp_path) == 3
    assert len(image_descriptions.undescribed(tmp_path)) == 3

    # A reviewed-EMPTY description is described. The distinction this module
    # turns on, and the one a count is most likely to get wrong.
    image_descriptions.write_in(d, {"avatar": ""})
    assert image_descriptions.undescribed_count(tmp_path) == 2
    assert len(image_descriptions.undescribed(tmp_path)) == 2

    image_descriptions.write_in(d, {"avatar": "", "gallery_1": "A lit stair.",
                                    "gallery_2": ""})
    assert image_descriptions.undescribed_count(tmp_path) == 0
    assert len(image_descriptions.undescribed(tmp_path)) == 0


def test_the_count_agrees_across_bases_and_empty_roots(tmp_path):
    """Entity kinds and a root with nothing in it — the two ends of the walk."""
    assert image_descriptions.undescribed_count(tmp_path, "locations") == 0
    assert image_descriptions.undescribed(tmp_path, "locations") == []

    eid = entities.create_entity(tmp_path, "locations", "Saltmarch")
    assets.put_image(tmp_path, eid, "default", "avatar", b"png", "png", base="locations")
    assert image_descriptions.undescribed_count(tmp_path, "locations") == 1
    assert len(image_descriptions.undescribed(tmp_path, "locations")) == 1


def test_presence_is_the_same_predicate_as_a_non_zero_count(tmp_path):
    """`has_undescribed` is what the rail's badge asks, and it stops early.

    The badge counts chores, not instances, so it must agree with `n > 0`
    exactly -- a badge that can disagree with the page under it is the stale
    number arrived at from the other side.
    """
    assert image_descriptions.has_undescribed(tmp_path) is False

    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1"))
    d = _dir_of(tmp_path, cid, vid)
    for mapping in ({}, {"avatar": "A lit stair."},
                    {"avatar": "A lit stair.", "gallery_1": ""}):
        if mapping:
            image_descriptions.write_in(d, mapping)
        assert (image_descriptions.has_undescribed(tmp_path)
                is (image_descriptions.undescribed_count(tmp_path) > 0))


def test_names_in_is_list_ins_stem_set(tmp_path):
    """The two must admit exactly the same names.

    `undescribed_count` filters on `names_in` and `undescribed` on `list_in`;
    a name one accepts and the other does not is precisely how the count and
    the list start to disagree.
    """
    cid, vid = _chars(tmp_path, images=("avatar", "gallery_1", "gallery_2"))
    d = _dir_of(tmp_path, cid, vid)
    names, found = assets.names_in(d)
    assert names == {i["name"] for i in assets.list_in(d)}
    assert found is False

    # `also` reports one extra file from the SAME directory read.
    image_descriptions.write_in(d, {"avatar": ""})
    names, found = assets.names_in(d, image_descriptions.DESCRIPTIONS_FILE)
    assert names == {i["name"] for i in assets.list_in(d)}
    assert found is True
