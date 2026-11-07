"""Art inherits down 022 derivation chains, live and slot by slot (Sprint 046). Synthetic data only.

A catalog Mara's pictures reach every world portrayal derived from her, until that portrayal sets,
hides, re-crops or resets one slot. Nothing is matched by name: a broken chain inherits nothing.
"""

from __future__ import annotations

import pytest
from image_fixtures import GIF, JPEG, PNG, PNG_2, handle
from portrayal_fixtures import (
    BASE, CAST, ELSEWHERE, MODERN, MODERN_MARA, NAMESAKE, VAMPIRE, VAMPIRE_MARA, base, derived, derives, envelope,
    manifest, seed_elderfen,
)
from store_helpers import OWNER

from grimoire.core.entity import EntityDefinition
from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.images import Crop, Hidden, Placement, Shown, Unavailable
from grimoire.imagestore import ImageLibrary, InvalidPlacement
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.store.layout import address_parts

CATALOG_MARA, MODERN_ENTITY, VAMPIRE_ENTITY = BASE.unpinned(), MODERN_MARA.unpinned(), VAMPIRE_MARA.unpinned()
PORTRAIT, PORTRAIT_2 = ImageRef(CAST, "mara-portrait"), ImageRef(CAST, "mara-portrait-2")
FANGS = ImageRef(VAMPIRE, "mara-fangs")
STRANGER_ART = ImageRef(ELSEWHERE, "someone")
FAN = InstanceRef("c1", "character", "mara")
CROP = Crop(1000, 1000, 5000, 5000)


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_elderfen(opened, handle())
        yield opened


@pytest.fixture
def images(store):
    library = ImageLibrary(store, handle(), owner=OWNER)
    batch = library.change()
    batch.add_image(CAST, "mara-portrait", PNG)
    batch.add_image(CAST, "mara-portrait-2", PNG_2)
    batch.add_image(VAMPIRE, "mara-fangs", GIF)
    batch.add_image(ELSEWHERE, "someone", JPEG)
    batch.place(CATALOG_MARA, "portrait", Placement("set", "portrait", PORTRAIT), None)
    batch.place(CATALOG_MARA, "avatar", Placement("set", "avatar", PORTRAIT), None)
    batch.place(NAMESAKE.unpinned(), "portrait", Placement("set", "portrait", STRANGER_ART), None)
    batch.commit()
    return library


def _set(images, subject, slot, placement):
    ref = PlacementRef(subject, slot)
    try:
        _, etag = images.read_placement(ref)
    except Exception:  # noqa: BLE001 - no placement yet
        etag = None
    images.place(subject, slot, placement, etag).commit()


def test_a_portrayal_inherits_its_bases_art_live(images):
    shown = images.effective_placements(VAMPIRE_ENTITY)
    portrait = shown.slot("portrait")
    assert isinstance(portrait, Shown) and portrait.image == PORTRAIT and portrait.layer == "source"
    assert shown.origin("portrait").origin == "inherited" and shown.origin("portrait").placed_by == CATALOG_MARA
    assert shown.lineage == (CATALOG_MARA, VAMPIRE_ENTITY)
    # the base gets new art: every portrayal that has not overridden the slot follows at once
    _set(images, CATALOG_MARA, "portrait", Placement("set", "portrait", PORTRAIT_2))
    assert images.effective_placements(VAMPIRE_ENTITY).slot("portrait").image == PORTRAIT_2
    assert images.effective_placements(MODERN_ENTITY).slot("portrait").image == PORTRAIT_2


def test_an_override_keeps_its_own_picture_when_the_base_changes(images):
    _set(images, VAMPIRE_ENTITY, "portrait", Placement("set", "portrait", FANGS))
    _set(images, CATALOG_MARA, "portrait", Placement("set", "portrait", PORTRAIT_2))
    vampire = images.effective_placements(VAMPIRE_ENTITY)
    assert vampire.slot("portrait").image == FANGS and vampire.origin("portrait").origin == "replaced"
    assert vampire.slot("avatar").image == PORTRAIT  # another slot still inherits
    assert images.effective_placements(MODERN_ENTITY).slot("portrait").image == PORTRAIT_2


def test_clear_then_reset_returns_to_the_live_inherited_picture(images):
    _set(images, MODERN_ENTITY, "portrait", Placement("hide"))
    modern = images.effective_placements(MODERN_ENTITY)
    assert isinstance(modern.slot("portrait"), Hidden) and modern.origin("portrait").origin == "cleared"
    _set(images, CATALOG_MARA, "portrait", Placement("set", "portrait", PORTRAIT_2))
    assert isinstance(images.effective_placements(MODERN_ENTITY).slot("portrait"), Hidden)  # a clear holds
    _set(images, MODERN_ENTITY, "portrait", Placement("inherit"))  # reset: as if never placed
    reset = images.effective_placements(MODERN_ENTITY)
    assert reset.slot("portrait").image == PORTRAIT_2 and reset.origin("portrait").origin == "inherited"


def test_an_adjust_recrops_the_inherited_picture_and_goes_stale_when_it_changes(images):
    _set(images, VAMPIRE_ENTITY, "portrait", Placement("adjust", image=PORTRAIT, crop=CROP))
    adjusted = images.effective_placements(VAMPIRE_ENTITY).slot("portrait")
    assert isinstance(adjusted, Shown) and adjusted.crop == CROP and adjusted.layer == "source_adjusted"
    _set(images, CATALOG_MARA, "portrait", Placement("set", "portrait", PORTRAIT_2))
    stale = images.effective_placements(VAMPIRE_ENTITY).slot("portrait")
    assert isinstance(stale, Unavailable) and stale.reason == "stale_adjust"
    with pytest.raises(InvalidPlacement) as caught:  # writing one that names the old picture is refused
        images.place(MODERN_ENTITY, "portrait", Placement("adjust", image=PORTRAIT, crop=CROP), None)
    assert caught.value.reason == "stale_adjust"


def test_a_standalone_definition_still_cannot_clear_or_reset(images):
    for mode in ("hide", "inherit"):
        with pytest.raises(InvalidPlacement) as caught:
            images.place(CATALOG_MARA, "gallery-1", Placement(mode), None)
        assert caught.value.reason == "mode_not_allowed"


def test_a_portrayal_may_only_set_its_own_sources_pictures(images):
    with pytest.raises(InvalidPlacement) as caught:
        images.place(VAMPIRE_ENTITY, "gallery-1", Placement("set", "gallery", PORTRAIT), None)
    assert caught.value.reason == "wrong_scope"


def test_an_instance_inherits_through_the_whole_chain(store, images):
    txn = store.transaction()
    txn.put(RecordEnvelope(FAN, {}, derived_from=str(VAMPIRE_ENTITY)), None)
    txn.commit(handle())
    fan = images.effective_placements(FAN)
    assert fan.slot("portrait").image == PORTRAIT and fan.origin("portrait").placed_by == CATALOG_MARA
    assert fan.lineage == (CATALOG_MARA, VAMPIRE_ENTITY, FAN)
    _set(images, VAMPIRE_ENTITY, "portrait", Placement("hide"))  # the world's clear reaches its instances
    assert isinstance(images.effective_placements(FAN).slot("portrait"), Hidden)
    # the campaign may re-crop a picture its lineage shows, wherever it is owned
    _set(images, FAN, "avatar", Placement("adjust", image=PORTRAIT, crop=CROP))
    assert images.effective_placements(FAN).slot("avatar").layer == "campaign_adjusted"


def test_the_batch_judges_a_mode_by_the_head_it_will_leave(store, images):
    """A batch that also puts a revision never writes a placement its own reader would refuse."""
    standalone = EntityDefinition(VAMPIRE_ENTITY.at("r2"), "Mara", "individual", base({}).traits, "Alone now")
    batch = images.change()
    batch.put(manifest(VAMPIRE, derives(VAMPIRE_MARA), heads={"character/mara": "r2"}),
              store.check_source(VAMPIRE).etag)
    batch.put(envelope(standalone), None)
    with pytest.raises(InvalidPlacement) as caught:  # the head it leaves is standalone: no clear
        batch.place(VAMPIRE_ENTITY, "portrait", Placement("hide"), None)
    assert caught.value.reason == "mode_not_allowed"
    namesake_r2 = derived(NAMESAKE.unpinned().at("r2"), BASE)
    batch = images.change()
    batch.put(manifest(ELSEWHERE, derives(namesake_r2.ref), heads={"character/mara": "r1"}),
              store.check_source(ELSEWHERE).etag)
    batch.put(envelope(namesake_r2), None)  # derived, but not the head the batch leaves
    with pytest.raises(InvalidPlacement) as caught:
        batch.place(NAMESAKE.unpinned(), "avatar", Placement("hide"), None)
    assert caught.value.reason == "mode_not_allowed"


def _remove(store, ref):
    (store.root.joinpath(*address_parts(ref))).unlink()


def test_a_removed_base_inherits_nothing_and_never_a_namesake(store, images):
    _set(images, VAMPIRE_ENTITY, "avatar", Placement("set", "avatar", FANGS))
    _remove(store, BASE)
    vampire = images.effective_placements(VAMPIRE_ENTITY)
    assert vampire.slot("portrait") is None  # the namesake has a portrait; it is never shown instead
    assert vampire.slot("avatar").image == FANGS  # the portrayal's own art still shows
    assert any(problem.code == "missing_base" for problem in vampire.problems)
    assert vampire.lineage == (VAMPIRE_ENTITY,)


def test_an_unlinked_derivation_inherits_nothing(store, images):
    txn = store.transaction()
    txn.put(manifest(VAMPIRE), store.check_source(VAMPIRE).etag)
    txn.commit(handle())
    vampire = images.effective_placements(VAMPIRE_ENTITY)
    assert vampire.slot("portrait") is None
    assert [problem.code for problem in vampire.problems] == ["unlinked_derivation"]


def test_a_two_step_chain_layers_root_first(store, images):
    """A Regency portrayal derived from the Vampire one: the catalog's art reaches it through both."""
    regency = derived(SourceRef("world:elderfen-regency", "character", "mara", "r1"), VAMPIRE_MARA)
    txn = store.transaction()
    txn.put(manifest("world:elderfen-regency", derives(regency.ref, VAMPIRE_MARA)), None)
    txn.put(envelope(regency), None)
    txn.commit(handle())
    _set(images, VAMPIRE_ENTITY, "avatar", Placement("set", "avatar", FANGS))
    shown = images.effective_placements(regency.ref.unpinned())
    assert shown.lineage == (CATALOG_MARA, VAMPIRE_ENTITY, regency.ref.unpinned())
    assert shown.slot("portrait").image == PORTRAIT and shown.slot("avatar").image == FANGS
    assert shown.origin("avatar").placed_by == VAMPIRE_ENTITY
    assert MODERN not in {str(entity.source) for entity in shown.lineage}


# -- the sprint's proving scenario: a species gets new artwork ----------------------------------------

def test_a_species_gets_new_art_children_follow_the_customized_one_keeps_its_picture(store, images):
    from entity_fixtures import EMBER, LANTERN, goblin

    species = goblin()
    namesake = type(species)(SourceRef(LANTERN, "species", "goblin", "r1"), "Goblin", "template", species.traits,
                             "Someone else's goblin")
    children = [InstanceRef("c1", "creature", f"goblin-{n}") for n in (1, 2, 3)]
    txn = store.transaction()
    txn.put(manifest(EMBER), None)
    txn.put(envelope(species), None)
    txn.put(manifest(LANTERN), None)
    txn.put(envelope(namesake), None)
    for child in children:
        txn.put(RecordEnvelope(child, {"source": str(species.ref.unpinned())}, derived_from=str(species.ref.unpinned())),
                None)
    txn.commit(handle())
    batch = images.change()
    old, new = batch.add_image(EMBER, "goblin", PNG), batch.add_image(EMBER, "goblin-new", PNG_2)
    snik = batch.add_image("campaign:c1", "snik", GIF)
    alien = batch.add_image(LANTERN, "goblin", JPEG)
    batch.place(species.ref.unpinned(), "avatar", Placement("set", "avatar", old), None)
    batch.place(namesake.ref.unpinned(), "avatar", Placement("set", "avatar", alien), None)
    batch.place(children[2], "avatar", Placement("set", "avatar", snik), None)  # the one the table named Snik
    batch.commit()
    assert [images.effective_placements(child).slot("avatar").image for child in children] == [old, old, snik]
    _set(images, species.ref.unpinned(), "avatar", Placement("set", "avatar", new))
    assert [images.effective_placements(child).slot("avatar").image for child in children] == [new, new, snik]
    assert images.effective_placements(children[2]).origin("avatar").origin == "replaced"
    _remove(store, species.ref)  # the species is gone: never the other source's goblin instead
    gone = [images.effective_placements(child).slot("avatar") for child in children]
    assert gone[:2] == [None, None] and gone[2].image == snik


def test_a_later_put_cannot_leave_an_earlier_clear_on_a_standalone_head(store, images):
    standalone = EntityDefinition(VAMPIRE_ENTITY.at("r2"), "Mara", "individual", base({}).traits, "Alone now")
    batch = images.change()
    batch.place(VAMPIRE_ENTITY, "portrait", Placement("hide"), None)  # allowed: the head is derived now
    batch.put(manifest(VAMPIRE, derives(VAMPIRE_MARA), heads={"character/mara": "r2"}),
              store.check_source(VAMPIRE).etag)
    batch.put(envelope(standalone), None)
    with pytest.raises(InvalidPlacement) as caught:
        batch.commit()
    assert caught.value.reason == "mode_not_allowed"
    assert images.effective_placements(VAMPIRE_ENTITY).slot("portrait").image == PORTRAIT  # nothing written


def test_an_adjust_through_a_damaged_portrayal_layer_is_refused(store, images):
    txn = store.transaction()
    txn.put(RecordEnvelope(FAN, {}, derived_from=str(VAMPIRE_ENTITY)), None)
    txn.commit(handle())
    folder = store.root.joinpath(*address_parts(PlacementRef(VAMPIRE_ENTITY, "portrait"))).parent
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Portrait.json").write_text("{}")  # a look-alike: the layer might be hiding a clear
    with pytest.raises(InvalidPlacement) as caught:
        images.place(FAN, "portrait", Placement("adjust", image=PORTRAIT, crop=CROP), None)
    assert caught.value.reason == "nothing_to_adjust"
