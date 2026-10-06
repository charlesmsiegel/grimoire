import json
import threading

import pytest

from grimoire.store import (
    assets,
    campaign_images,
    campaigns,
    characters,
    entities,
    image_descriptions,
    image_refs,
    image_store,
    world_images,
    worlds,
)


def _chars(tmp_path, images=("avatar", "gallery_1")):
    cid, vid = characters.create_character(tmp_path, "Seraphine", "main")
    for name in images:
        # Distinct bytes per image: one picture is one object with one
        # description, so two images sharing bytes would share their text.
        assets.put_image(tmp_path, cid, vid, name, f"png-{name}".encode(), "png")
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
    # vanished behind our back: its placement is what a sync has not brought
    ref = d / "image-refs" / "gallery_1.json"
    held = ref.read_bytes()
    ref.unlink()
    assert image_descriptions.read_in(d) == {"avatar": "one"}   # not offered while absent
    image_descriptions.set_in(d, "avatar", "one edited")
    ref.write_bytes(held)                                       # sync catches up
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


def test_catalog_reads_a_non_string_description_as_absent(tmp_path):
    """Same tolerance `read_in` has, for the same reason: a hand-edited or
    half-synced sidecar must not hand a list to a caller expecting text.

    Under R1 a non-string legacy value is no text at all, so it masks nothing:
    the image object's own description answers, and without one the image is
    unreviewed -- the same answer the describe queue gives for it."""
    cid, vid = _chars(tmp_path, images=("avatar",))
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.path_in(d).write_text('{"avatar": ["not", "text"]}', encoding="utf-8")
    (row,) = image_descriptions.catalog(tmp_path, "characters")
    assert (row["id"], row["vid"], row["name"]) == (cid, vid, "avatar")
    assert (row["described"], row["description"]) == (False, "")
    assert image_descriptions.undescribed(tmp_path) == [
        {"id": cid, "vid": vid, "name": "avatar"}]

    _describe_object(image_refs.read(d, "avatar").image, "In half-plate.")
    (row,) = image_descriptions.catalog(tmp_path, "characters")
    assert (row["described"], row["description"]) == (True, "In half-plate.")


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
    real_journal = image_refs.write_journal

    def blocking_journal(*a, **kw):
        # Called from inside the promotion, after it has taken the sidecar lock
        # and read the descriptions it will write back.
        if not inside.is_set():
            inside.set()
            release.wait(5)
        return real_journal(*a, **kw)

    monkeypatch.setattr(image_refs, "write_journal", blocking_journal)
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

    # A scoped patch, not `monkeypatch.undo()`: an undo would also lift the
    # suite's isolation of the image store, which the reads below resolve
    # the slots' placements through.
    with monkeypatch.context() as m:
        m.setattr(image_refs, "write_journal", boom)
        with pytest.raises(OSError):
            assets.promote_image(tmp_path, cid, vid, "gallery_1")

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


# --- Descriptions on the image object (stage 2) ------------------------------
#
# R1: a string legacy key in the directory wins; otherwise the object's
# `description`; otherwise undescribed. R2: a write goes to the object behind a
# resolving placement once `image_store.update` confirms it, and only then is
# the legacy key dropped; otherwise the legacy key takes it, as before.


def _object_text(d, name):
    return image_store.read(image_refs.read(d, name).image).raw.get("description")


def _describe_object(image_id, text):
    assert image_store.update(image_id, lambda raw: {**raw, "description": text})


def _legacy_keys(d, mapping):
    d.mkdir(parents=True, exist_ok=True)
    image_descriptions.path_in(d).write_text(json.dumps(mapping), encoding="utf-8")


def test_a_placement_description_lives_on_the_object(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-1", "png")
    image_descriptions.set_in(d, "avatar", "A grey arch")
    assert _object_text(d, "avatar") == "A grey arch"
    assert "avatar" not in image_descriptions.read_raw(d)
    assert image_descriptions.text_in(d, "avatar") == "A grey arch"
    assert image_descriptions.read_in(d) == {"avatar": "A grey arch"}


def test_a_shared_picture_shows_one_description_everywhere(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    dirs = []
    for world in ("Realm", "Saltmarch"):
        root = worlds.world_root(worlds.create_world(world))
        cid, vid = characters.create_character(root, "Mara", "main")
        assets.put_image(root, cid, vid, "avatar", b"png-shared", "png")
        dirs.append(assets.version_dir(root, cid, vid))
    image_descriptions.set_in(dirs[0], "avatar", "Mara at the gate.")
    assert image_descriptions.text_in(dirs[1], "avatar") == "Mara at the gate."
    assert image_descriptions.read_in(dirs[1]) == {"avatar": "Mara at the gate."}


def test_a_local_legacy_key_wins_over_the_object(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-2", "png")
    _legacy_keys(d, {"avatar": "Old"})
    _describe_object(image_refs.read(d, "avatar").image, "New")
    assert image_descriptions.text_in(d, "avatar") == "Old"
    assert image_descriptions.legacy_text_in(d, "avatar") == "Old"
    image_descriptions.set_in(d, "avatar", "Newer")
    assert image_descriptions.text_in(d, "avatar") == "Newer"
    assert "avatar" not in image_descriptions.read_raw(d)


def test_text_in_takes_the_callers_id_or_none(tmp_path):
    """`known_id`: a string reads that object and no placement; None reads no
    object at all (a legacy row); a non-string legacy value counts as absent."""
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-ids", "png")
    iid = image_refs.read(d, "avatar").image
    _describe_object(iid, "On the object")
    assert image_descriptions.text_in(d, "avatar", known_id=iid) == "On the object"
    assert image_descriptions.text_in(d, "avatar", known_id=None) is None
    _legacy_keys(d, {"avatar": ["not", "text"]})
    assert image_descriptions.text_in(d, "avatar") == "On the object"
    assert image_descriptions.legacy_text_in(d, "avatar") is None


def test_reviewed_empty_on_the_object_is_described(tmp_path):
    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "")
    assert _object_text(d, "avatar") == ""
    assert image_descriptions.undescribed(tmp_path) == [
        {"id": cid, "vid": vid, "name": "gallery_1"}]
    rows = {r["name"]: r for r in image_descriptions.catalog(tmp_path)}
    assert (rows["avatar"]["described"], rows["avatar"]["description"]) == (True, "")
    assert (rows["gallery_1"]["described"], rows["gallery_1"]["description"]) == (False, "")


def test_an_unarrived_placement_with_a_legacy_file_writes_the_legacy_key(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    (d / "avatar.png").write_bytes(b"png-legacy")
    image_refs.write(d, "avatar", "px1-" + "ab" * 32)        # its object never arrived
    image_descriptions.set_in(d, "avatar", "Waiting on a sync.")
    assert image_descriptions.read_raw(d) == {"avatar": "Waiting on a sync."}
    assert image_descriptions.text_in(d, "avatar") == "Waiting on a sync."


def test_an_unconfirmed_object_write_keeps_the_text_on_the_legacy_key(tmp_path, monkeypatch):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-3", "png")
    _legacy_keys(d, {"avatar": "Old"})
    monkeypatch.setattr(image_store, "update", lambda image_id, change: False)
    image_descriptions.set_in(d, "avatar", "New")
    assert image_descriptions.read_raw(d) == {"avatar": "New"}       # replaced, not lost
    assert image_descriptions.text_in(d, "avatar") == "New"
    assert _object_text(d, "avatar") is None


def test_fallback_dir_takes_the_legacy_key_when_the_object_write_is_not_confirmed(
        tmp_path, monkeypatch):
    """`fallback_dir` names the directory whose legacy key a write falls back
    to -- for an unconfirmed `update` and for a name with no resolving
    placement alike -- and `d` keeps its own key untouched."""
    d, edited = tmp_path / "v", tmp_path / "edited"
    assets.put_in(d, "avatar", b"png-fallback", "png")
    (d / "map.png").write_bytes(b"png-fallback-map")          # legacy: no placement
    _legacy_keys(d, {"avatar": "Old", "map": "Old map"})
    monkeypatch.setattr(image_store, "update", lambda image_id, change: False)
    image_descriptions.set_in(d, "avatar", "New", fallback_dir=edited)
    image_descriptions.set_in(d, "map", "New map", fallback_dir=edited)
    assert image_descriptions.read_raw(edited) == {"avatar": "New", "map": "New map"}
    assert image_descriptions.read_raw(d) == {"avatar": "Old", "map": "Old map"}
    assert _object_text(d, "avatar") is None


def test_an_object_deleted_before_update_keeps_the_text(tmp_path, monkeypatch):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-4", "png")
    real = image_descriptions.object_id_in

    def then_gone(dd, name):
        iid = real(dd, name)
        image_store.object_path(iid).unlink()
        return iid

    monkeypatch.setattr(image_descriptions, "object_id_in", then_gone)
    image_descriptions.set_in(d, "avatar", "Kept anyway.")
    assert image_descriptions.read_raw(d) == {"avatar": "Kept anyway."}


def test_description_too_long_is_its_own_error(tmp_path):
    assert issubclass(image_descriptions.DescriptionTooLongError, ValueError)
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-5", "png")
    with pytest.raises(image_descriptions.DescriptionTooLongError):
        image_descriptions.set_in(d, "avatar", "x" * 4001)
    with pytest.raises(ValueError) as unknown:
        image_descriptions.set_in(d, "nope", "x")
    assert not isinstance(unknown.value, image_descriptions.DescriptionTooLongError)


def test_set_in_refuses_an_overlong_description(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-6", "png")
    with pytest.raises(ValueError):
        image_descriptions.set_in(d, "avatar", "x" * (image_store.MAX_DESCRIPTION + 1))
    assert image_descriptions.text_in(d, "avatar") is None
    image_descriptions.set_in(d, "avatar", "x" * image_store.MAX_DESCRIPTION)
    assert image_descriptions.text_in(d, "avatar") == "x" * 4000


def test_set_in_clears_the_also_clear_directory(tmp_path, monkeypatch):
    d, visible = tmp_path / "campaign", tmp_path / "world"
    assets.put_in(d, "avatar", b"png-7", "png")
    _legacy_keys(visible, {"avatar": "Old", "gallery_1": "Other"})
    image_descriptions.set_in(d, "avatar", "New", also_clear=visible)
    assert image_descriptions.read_raw(visible) == {"gallery_1": "Other"}

    _legacy_keys(visible, {"avatar": "Old"})
    monkeypatch.setattr(image_store, "update", lambda image_id, change: False)
    image_descriptions.set_in(d, "avatar", "Newer", also_clear=visible)
    assert image_descriptions.read_raw(visible) == {"avatar": "Old"}     # a legacy write
    assert image_descriptions.read_raw(d) == {"avatar": "Newer"}


def test_carry_legacy_writes_only_the_key(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-8", "png")
    _legacy_keys(d, {"gallery_1": "Other"})
    image_descriptions.carry_legacy(d, "avatar", "Carried")
    assert image_descriptions.read_raw(d) == {"avatar": "Carried", "gallery_1": "Other"}
    assert _object_text(d, "avatar") is None


def test_object_id_in_answers_only_for_a_resolving_placement(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-9", "png")
    assert image_descriptions.object_id_in(d, "avatar") == image_refs.read(d, "avatar").image
    image_refs.write(d, "gallery_1", "px1-" + "cd" * 32)       # unarrived
    assert image_descriptions.object_id_in(d, "gallery_1") is None
    assert image_descriptions.object_id_in(d, "nope") is None


def test_described_names_reads_an_object_only_without_a_legacy_key(tmp_path, monkeypatch):
    d = tmp_path / "v"
    for n in ("avatar", "gallery_1", "gallery_2"):
        assets.put_in(d, n, f"png-dn-{n}".encode(), "png")
    ids = {n: image_refs.read(d, n).image for n in ("avatar", "gallery_1", "gallery_2")}
    _describe_object(ids["gallery_1"], "On the object")
    _legacy_keys(d, {"avatar": "Legacy"})
    rows = [{"name": n, "image_id": ids[n]} for n in ids] + [{"name": "map"}]
    read = []
    real = image_store.read
    monkeypatch.setattr(image_store, "read", lambda i: read.append(i) or real(i))
    assert image_descriptions.described_names(d, rows) == {"avatar", "gallery_1"}
    assert sorted(read) == sorted([ids["gallery_1"], ids["gallery_2"]])


# R12: a replaced picture sheds a stale caption -- only when the slot held a
# known, different identity and the new object already says something.

def test_put_in_drops_a_stale_caption_when_a_different_described_picture_replaces_it(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-old", "png")
    _legacy_keys(d, {"avatar": "Old caption"})
    new = image_store.ingest(b"png-new", "png")
    _describe_object(new.id, "New caption")
    assets.put_in(d, "avatar", b"png-new", "png")
    assert "avatar" not in image_descriptions.read_raw(d)
    assert image_descriptions.text_in(d, "avatar") == "New caption"


def test_put_in_over_a_different_legacy_file_drops_a_stale_caption(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    (d / "avatar.png").write_bytes(b"png-legacy-old")
    _legacy_keys(d, {"avatar": "Old caption"})
    new = image_store.ingest(b"png-legacy-new", "png")
    _describe_object(new.id, "New caption")
    assets.put_in(d, "avatar", b"png-legacy-new", "png")
    assert image_descriptions.text_in(d, "avatar") == "New caption"


def test_an_undescribed_new_picture_keeps_the_old_key(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-old-2", "png")
    _legacy_keys(d, {"avatar": "Old caption"})
    assets.put_in(d, "avatar", b"png-new-2", "png")
    assert image_descriptions.read_raw(d) == {"avatar": "Old caption"}


def test_a_first_placement_keeps_its_legacy_key(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    (d / "map.png").write_bytes(b"png-map")
    _legacy_keys(d, {"map": "K"})
    obj = image_store.ingest(b"png-map", "png")
    _describe_object(obj.id, "Another world's words.")
    assets.put_in(d, "map", b"png-map", "png")
    assert image_refs.read(d, "map").image == obj.id
    assert image_descriptions.text_in(d, "map") == "K"


def test_link_in_onto_an_empty_slot_keeps_a_carried_key(tmp_path):
    d = tmp_path / "v"
    obj = image_store.ingest(b"png-carry", "png")
    _describe_object(obj.id, "Shared")
    image_descriptions.carry_legacy(d, "avatar", "Carried")
    assets.link_in(d, "avatar", obj.id)
    assert image_descriptions.text_in(d, "avatar") == "Carried"


def test_link_in_over_a_different_placement_drops_a_stale_caption(tmp_path):
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-link-old", "png")
    _legacy_keys(d, {"avatar": "Old caption"})
    new = image_store.ingest(b"png-link-new", "png")
    _describe_object(new.id, "New caption")
    assets.link_in(d, "avatar", new.id)
    assert image_descriptions.text_in(d, "avatar") == "New caption"


def test_backlogs_never_resolve_blobs(tmp_path, monkeypatch):
    """Every backlog counts an object-described placement as described, off the
    placements' own ids, without resolving a single blob."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    rid, vid = characters.create_character(root, "Winifred", "main")
    for n in ("avatar", "gallery_1", "gallery_2"):
        assets.put_image(root, rid, vid, n, f"png-bl-{n}".encode(), "png")
    vdir = assets.version_dir(root, rid, vid)
    image_descriptions.set_in(vdir, "avatar", "On the object.")
    _legacy_keys(vdir, {"gallery_1": "Legacy."})
    for n in ("coastline", "harbour"):
        world_images.put_image(wid, n, f"png-wl-{n}".encode(), "png")
        campaign_images.put_image(cid, f"c-{n}", f"png-cl-{n}".encode(), "png")
    world_images.set_description(wid, "coastline", "A grey coast.")
    campaign_images.set_description(cid, "c-coastline", "A grey coast, closer.")

    def no_resolving(ref):
        raise AssertionError("a backlog resolved a blob")

    monkeypatch.setattr(image_refs, "resolve_ref", no_resolving)
    assert image_descriptions.undescribed(root) == [
        {"id": rid, "vid": vid, "name": "gallery_2"}]
    assert image_descriptions.undescribed_count(root) == 1
    assert image_descriptions.has_undescribed(root) is True
    assert world_images.undescribed(wid) == [{"name": "harbour"}]
    assert world_images.undescribed_count(wid) == 1
    assert world_images.has_undescribed(wid) is True
    assert campaign_images.own_undescribed(cid) == [{"name": "c-harbour"}]

    _describe_object(image_refs.read(vdir, "gallery_2").image, "")
    _describe_object(image_refs.read(world_images.images_dir(wid), "harbour").image, "")
    _describe_object(image_refs.read(campaign_images.images_dir(cid), "c-harbour").image, "")
    assert image_descriptions.undescribed_count(root) == 0
    assert image_descriptions.has_undescribed(root) is False
    assert world_images.undescribed(wid) == []
    assert world_images.has_undescribed(wid) is False
    assert campaign_images.own_undescribed(cid) == []


def test_a_legacy_key_that_cannot_be_cleared_fails_the_save(tmp_path, monkeypatch):
    """After the object write is confirmed, the legacy key is cleared strictly:
    a key left behind would keep masking the new text (R1), so the save says it
    failed rather than answering ok over the old words. The object already
    holds the new text, and a retry clears the key."""
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-strict", "png")
    _legacy_keys(d, {"avatar": "Old", "gallery_1": "Other"})
    real = image_descriptions.atomic.write_text

    def refuse_sidecar(path, *a, **kw):
        if path.name == image_descriptions.DESCRIPTIONS_FILE:
            raise OSError("read-only sidecar")
        return real(path, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(image_descriptions.atomic, "write_text", refuse_sidecar)
        with pytest.raises(OSError):
            image_descriptions.set_in(d, "avatar", "New")
    assert _object_text(d, "avatar") == "New"
    assert image_descriptions.read_raw(d)["avatar"] == "Old"
    image_descriptions.set_in(d, "avatar", "New")
    assert image_descriptions.read_raw(d) == {"gallery_1": "Other"}
    assert image_descriptions.text_in(d, "avatar") == "New"


def test_clearing_the_last_legacy_key_removes_the_sidecar(tmp_path):
    d, visible = tmp_path / "campaign", tmp_path / "world"
    assets.put_in(d, "avatar", b"png-last", "png")
    _legacy_keys(d, {"avatar": "Old"})
    _legacy_keys(visible, {"avatar": "Older"})
    image_descriptions.set_in(d, "avatar", "New", also_clear=visible)
    assert not image_descriptions.path_in(d).exists()
    assert not image_descriptions.path_in(visible).exists()


def test_an_also_clear_key_that_cannot_be_cleared_fails_the_save(tmp_path, monkeypatch):
    d, visible = tmp_path / "campaign", tmp_path / "world"
    assets.put_in(d, "avatar", b"png-strict-2", "png")
    _legacy_keys(visible, {"avatar": "Old", "gallery_1": "Other"})
    real = image_descriptions.atomic.write_text

    def refuse_visible(path, *a, **kw):
        if path.parent == visible:
            raise OSError("read-only sidecar")
        return real(path, *a, **kw)

    monkeypatch.setattr(image_descriptions.atomic, "write_text", refuse_visible)
    with pytest.raises(OSError):
        image_descriptions.set_in(d, "avatar", "New", also_clear=visible)
    assert _object_text(d, "avatar") == "New"
    assert image_descriptions.read_raw(visible)["avatar"] == "Old"


def _count(monkeypatch, module, name):
    calls = []
    real = getattr(module, name)

    def counted(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(module, name, counted)
    return calls


def test_reading_a_character_resolves_and_scans_nothing_extra_for_descriptions(
        tmp_path, monkeypatch):
    """`read_character` builds its version's image listing anyway; the
    description read takes its names and ids from that listing rather than
    listing (and resolving) the folder a second time and scanning placements
    a third. Measured against the same read with descriptions stubbed out."""
    from grimoire.store import pcs

    cid, vid = _chars(tmp_path)
    d = _dir_of(tmp_path, cid, vid)
    image_descriptions.set_in(d, "avatar", "On the object.")
    _legacy_keys(d, {"gallery_1": "Legacy."})
    pid, pvid = pcs.create_pc(tmp_path, "Mara", [])
    assets.put_image(tmp_path, pid, pvid, "avatar", b"png-pc", "png", pcs.ASSET_BASE)
    image_descriptions.set_description(tmp_path, pid, pvid, "avatar", "Mara, hooded.",
                                       pcs.ASSET_BASE)

    def costs(read):
        with monkeypatch.context() as m:
            resolves = _count(m, image_refs, "resolve_ref")
            scans = _count(m, image_refs, "scan")
            got = read()
        return len(resolves), len(scans), got

    with_desc = costs(lambda: characters.read_character(tmp_path, cid))
    pc_with_desc = costs(lambda: pcs.read_pc(tmp_path, pid))
    with monkeypatch.context() as m:
        m.setattr(image_descriptions, "read_all", lambda *a, **kw: {})
        without = costs(lambda: characters.read_character(tmp_path, cid))
        pc_without = costs(lambda: pcs.read_pc(tmp_path, pid))
    assert with_desc[:2] == without[:2]
    assert pc_with_desc[:2] == pc_without[:2]
    (version,) = with_desc[2]["versions"]
    assert version["image_descriptions"] == {"avatar": "On the object.",
                                             "gallery_1": "Legacy."}
    assert pc_with_desc[2]["versions"][0]["image_descriptions"] == {"avatar": "Mara, hooded."}


def test_the_r12_decision_and_drop_hold_the_sidecar_lock(tmp_path, monkeypatch):
    """Decide and drop under one hold: a description saved between the
    decision and the drop would otherwise be the key the drop removes."""
    d = tmp_path / "v"
    assets.put_in(d, "avatar", b"png-r12-old", "png")
    _legacy_keys(d, {"avatar": "Old caption"})
    new = image_store.ingest(b"png-r12-new", "png")
    _describe_object(new.id, "New caption")
    held = []
    real = assets._sheds_caption

    def observe(dd, *a, **kw):
        lock = assets.sidecar_lock(dd, image_descriptions.DESCRIPTIONS_FILE)
        held.append(lock._is_owned())
        return real(dd, *a, **kw)

    monkeypatch.setattr(assets, "_sheds_caption", observe)
    assets.put_in(d, "avatar", b"png-r12-new", "png")
    assert held == [True]
    assert "avatar" not in image_descriptions.read_raw(d)
