"""Sprint 038: an ``unresolved`` placement keeps a missing picture's slot, and nothing ever stands in for it.

Synthetic pictures and invented sources only (``image_fixtures``).
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import ImageRef, PlacementRef
from grimoire.images import Expression, Hidden, Placement, Shown, Unavailable, placement_from_payload, placement_to_payload
from grimoire.images.model import ImageModelError
from grimoire.imagestore import ImageLibrary, InvalidPlacement
from grimoire.store import LibraryStore
from grimoire.store.layout import address_parts
from grimoire.sync import identify, sync_until_quiet

from image_fixtures import HARBOR, PNG, PNG_2, SAILOR, WREN_A, WREN_B, WREN_C, handle, seed
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE

NOTE = {"origin": "test import", "note": "a remote picture; nothing is fetched"}
SMILE = "expr.default.smile"


def missing(role: str = "expression", **kwargs) -> Placement:
    expression = Expression("default", "smile") if role == "expression" else None
    return Placement("unresolved", role, expression=expression, provenance=NOTE, **kwargs)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def images(store):
    return ImageLibrary(store, handle(), owner=OWNER)


def test_a_source_marks_a_missing_sprite_and_every_individual_sees_it_missing(images):
    batch = images.change()
    batch.add_image(HARBOR, "wren", PNG)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", ImageRef(HARBOR, "wren")), None)
    batch.place(SAILOR, SMILE, missing(), None)
    batch.commit()
    own = images.effective_placements(SAILOR).slot(SMILE)
    assert own == Unavailable(SMILE, "unresolved", NOTE["note"])
    inherited = images.effective_placements(WREN_A)
    # the instance inherits the missing sprite as missing: the avatar, the only picture there, never stands in
    assert inherited.slot(SMILE) == Unavailable(SMILE, "unresolved", NOTE["note"])
    assert isinstance(inherited.slot("avatar"), Shown)


def test_one_individual_supplies_its_own_picture_without_touching_the_source_or_another(images):
    batch = images.change()
    batch.place(SAILOR, SMILE, missing(), None)
    own = batch.add_image("campaign:c1", "wren-a-smile", PNG_2)
    batch.place(WREN_A, SMILE, Placement("set", "expression", own, expression=Expression("default", "smile")), None)
    batch.commit()
    assert images.effective_placements(WREN_A).slot(SMILE).image == own
    assert images.effective_placements(WREN_B).slot(SMILE).reason == "unresolved"
    assert images.effective_placements(SAILOR).slot(SMILE).reason == "unresolved"


def test_a_campaign_unresolved_never_falls_back_to_the_source_picture(images):
    batch = images.change()
    shared = batch.add_image(HARBOR, "wren", PNG)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", shared), None)
    batch.place(WREN_A, "avatar", missing("avatar"), None)
    batch.commit()
    assert images.effective_placements(WREN_A).slot("avatar") == Unavailable("avatar", "unresolved", NOTE["note"])
    assert images.effective_placements(WREN_B).slot("avatar").image == shared


def test_a_missing_picture_cannot_be_adjusted(images):
    batch = images.change()
    batch.add_image(HARBOR, "wren", PNG)  # a picture exists, but the slot says its own is missing
    batch.place(SAILOR, "avatar", missing("avatar"), None)
    batch.commit()
    with pytest.raises(InvalidPlacement) as caught:
        images.place(WREN_C, "avatar", Placement("adjust", image=ImageRef(HARBOR, "wren")), None)
    assert caught.value.reason == "nothing_to_adjust"


@pytest.mark.parametrize("build", [
    lambda: Placement("unresolved"),  # no role: what is missing is unknown
    lambda: Placement("unresolved", "avatar", ImageRef(HARBOR, "wren")),  # a picture is not missing
    lambda: Placement("unresolved", "expression"),  # a sprite needs its outfit and name
])
def test_an_unresolved_placement_names_its_role_and_no_picture(build):
    with pytest.raises(ImageModelError):
        build()


def test_a_source_may_mark_a_picture_missing_or_hide_one_but_never_reset_one(images):
    with pytest.raises(InvalidPlacement) as caught:
        images.place(SAILOR, "avatar", Placement("inherit"), None)
    assert caught.value.reason == "mode_not_allowed"
    images.place(SAILOR, "gallery_9", Placement("hide"), None).commit()  # a hide shows nothing and is allowed
    assert isinstance(images.effective_placements(SAILOR).slot("gallery_9"), Hidden)
    with pytest.raises(InvalidPlacement) as caught:
        images.place(SAILOR, "expr.default.frown", missing(), None)  # the slot names another sprite
    assert caught.value.reason == "role_slot_mismatch"


def test_the_note_survives_the_codec_and_a_rewrite_keeps_unknown_fields(images):
    placement = Placement("unresolved", "gallery", provenance={**NOTE, "x-asset": {"index": 3}},
                          extras={"x-later": True})
    assert placement_from_payload(placement_to_payload(placement)) == placement
    images.place(SAILOR, "background", placement, None).commit()
    read, _ = images.read_placement(PlacementRef(SAILOR, "background"))
    assert read == placement


def test_an_unresolved_source_placement_syncs_without_any_blob(tmp_path):
    full = ("sources", "campaigns", "assets", "kept")
    handles = {DESKTOP: handle(*full), PHONE: handle("sources", "campaigns", "kept")}  # the phone may hold no blob
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    try:
        seed(desktop)
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        ImageLibrary(desktop, handles[DESKTOP], owner=OWNER).place(SAILOR, SMILE, missing(), None).commit()
        report = sync_until_quiet(desktop, phone, handles)
        path = "/".join(address_parts(PlacementRef(SAILOR, SMILE)))
        assert report.conflicts == () and (path, PHONE) in report.copied
        on_phone = ImageLibrary(phone, handles[PHONE]).effective_placements(WREN_A).slot(SMILE)
        assert on_phone == Unavailable(SMILE, "unresolved", NOTE["note"])
    finally:
        desktop.close()
        phone.close()
