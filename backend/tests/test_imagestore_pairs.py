"""Sprint 018 W11: art is never placed on an instance id two kinds share (IDENTITY.md).

``load()`` leaves both members of a cross-kind pair out, so neither is one individual to picture.
A ``set`` or ``adjust`` naming one is refused when added (``instance_id_conflict``), and a pair formed
after planning is ``StaleWrite`` at ``preview``/``commit``. A ``hide`` stays allowed as the repair
that takes earlier art off the slot. Every picture is a tiny synthetic byte string.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import ImageRef, PlacementRef
from grimoire.images import Crop, Placement, Shown
from grimoire.imagestore import ImageLibrary, InvalidPlacement
from grimoire.store import LibraryStore, StaleWrite
from grimoire.testing.fileops import snapshot

from graph_fixtures import hand_pair
from image_fixtures import HARBOR, JPEG, PNG, SAILOR, WREN_A, WREN_B, handle, seed
from store_helpers import OWNER

WREN_IMG = ImageRef(HARBOR, "wren")
WREN_A_PATH = "campaigns/c1/instances/character/wren-a.json"


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def images(store):
    """The sailor portrayal shows one picture as its avatar, which every Wren instance inherits."""
    library = ImageLibrary(store, handle(), owner=OWNER)
    batch = library.change()
    batch.add_image(HARBOR, "wren", PNG)
    batch.place(SAILOR, "avatar", Placement("set", "avatar", WREN_IMG), None)
    batch.commit()
    return library


def _refusal(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidPlacement) as caught:
        call()
    return caught.value.reason


SET = Placement("set", "portrait", WREN_IMG)
ADJUST = Placement("adjust", image=WREN_IMG, crop=Crop(100, 100, 5000, 5000))


@pytest.mark.parametrize(("slot", "placement"), [("portrait", SET), ("avatar", ADJUST)], ids=["set", "adjust"])
def test_art_naming_either_member_of_a_pair_is_refused_when_added(images, store, slot, placement):
    creature = hand_pair(store, WREN_A)  # a hand edit or a second device gave a creature Wren's id
    before = snapshot(store.root)
    for member in (WREN_A, creature):
        assert _refusal(lambda: images.place(member, slot, placement, None)) == "instance_id_conflict"
    assert snapshot(store.root) == before  # nothing staged, nothing written


def test_a_pair_formed_after_planning_is_a_stale_write_at_preview_and_commit(images, store):
    batch = images.change()
    batch.place(WREN_A, "portrait", SET, None)
    batch.place(WREN_A, "avatar", ADJUST, None)
    batch.preview()  # a human approved this while Wren was still one individual
    creature = hand_pair(store, WREN_A)
    before = snapshot(store.root)
    stale = [(WREN_A_PATH, "instance_id_conflict")]  # one entry per subject, not per slot
    for step in (batch.preview, batch.commit):
        with pytest.raises(StaleWrite) as caught:
            step()
        assert list(caught.value.stale) == stale
    assert snapshot(store.root) == before
    store.root.joinpath(*f"campaigns/c1/instances/creature/{creature.instance_id}.json".split("/")).unlink()
    batch.commit()  # the pair undone by hand: the very same batch now lands as previewed
    portrait = images.effective_placements(WREN_A).slot("portrait")
    assert isinstance(portrait, Shown) and portrait.image == WREN_IMG


def test_an_unpaired_instance_of_the_same_campaign_still_takes_art(images, store):
    hand_pair(store, WREN_A)
    images.place(WREN_B, "portrait", SET, None).commit()
    portrait = images.effective_placements(WREN_B).slot("portrait")
    assert isinstance(portrait, Shown) and (portrait.layer, portrait.image) == ("campaign", WREN_IMG)


def test_a_campaign_image_is_still_created_while_the_campaign_holds_a_pair(images, store):
    hand_pair(store, WREN_A)
    images.add_image("campaign:c1", "harbor-map", JPEG).commit()  # names no subject: no pair to refuse
    assert images.read_image(ImageRef("campaign:c1", "harbor-map"))[0].blob.media_type == "image/jpeg"


def test_a_hide_on_a_paired_instance_is_the_repair_that_takes_earlier_art_off(images, store):
    images.place(WREN_A, "portrait", SET, None).commit()  # art placed while Wren was one individual
    _, etag = images.read_placement(PlacementRef(WREN_A, "portrait"))
    hand_pair(store, WREN_A)
    batch = images.place(WREN_A, "portrait", Placement("hide"), etag)  # planned against the pair
    batch.place(WREN_A, "avatar", Placement("hide"), None)  # and a new hide over inherited art
    hand_pair(store, WREN_A, "place")  # a third kind joins after planning: a hide is still no picture
    batch.commit()
    assert images.read_placement(PlacementRef(WREN_A, "portrait"))[0] == Placement("hide")
    assert images.read_placement(PlacementRef(WREN_A, "avatar"))[0] == Placement("hide")


def test_the_entity_a_paired_instance_derives_from_still_takes_art(images, store):
    hand_pair(store, WREN_A)  # Wren-a derives from the sailor, but the sailor's art is the source's own
    images.place(SAILOR, "portrait", SET, None).commit()
    assert images.effective_placements(SAILOR).slot("portrait").image == WREN_IMG
