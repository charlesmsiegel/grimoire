"""024's portrait clause on 018 art: changing one goblin's portrait does not change the species card.

Synthetic fixtures only. The species card has a source portrait; every goblin instance inherits it through
its derivation. Individualizing goblin-1 and giving it a campaign portrait changes only goblin-1: the species
card and the other goblins keep the species picture, and goblin-1 keeps its own after a reopen.
"""

from entity_fixtures import EMBER, goblin
from image_fixtures import PNG, PNG_2, handle
from store_helpers import OWNER

from grimoire.core import DIRECTOR, CampaignRoster, InstanceRef
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_from_payload, instance_to_payload
from grimoire.core.identity import ImageRef
from grimoire.core.individual import IndividualizationDecision, individualize
from grimoire.images import Placement, Shown
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from test_individual_store import decode_index

C = "c1"
G1, G2, G3 = (InstanceRef(C, "creature", f"goblin-{n}") for n in (1, 2, 3))
SPECIES = goblin()
SPECIES_ART = ImageRef(EMBER, "goblin")
SNIK_ART = ImageRef(f"campaign:{C}", "snik")


def instance_envelope(instance: EntityInstance) -> RecordEnvelope:
    """Keeps the derivation 018 inherits through, which the 024 store test's join deliberately omits."""
    return RecordEnvelope(instance.ref, instance_to_payload(instance), derived_from=str(SPECIES.ref.unpinned()),
                          base=str(SPECIES.ref))


def portrait(images: ImageLibrary, subject) -> Shown:
    shown = images.effective_placements(subject).slot("portrait")
    assert isinstance(shown, Shown), shown
    return shown


def test_changing_the_individual_portrait_leaves_the_species_card_alone(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        images = ImageLibrary(store, handle(), owner=OWNER)
        batch = images.change()
        batch.put(SourceManifest(EMBER, OWNER, "Emberfall", {"species/goblin": "r1"}), None)
        batch.put(RecordEnvelope(SPECIES.ref, definition_to_payload(SPECIES), name=SPECIES.name), None)
        for ref in (G1, G2, G3):
            batch.put(instance_envelope(EntityInstance(ref, SPECIES.ref.unpinned())), None)
        batch.add_image(EMBER, "goblin", PNG)
        batch.place(SPECIES.ref.unpinned(), "portrait", Placement("set", "portrait", SPECIES_ART), None)
        batch.commit()
        for subject in (SPECIES.ref.unpinned(), G1, G2, G3):
            assert portrait(images, subject).image == SPECIES_ART

        view = store.load()
        roster = CampaignRoster(C, tuple(instance_from_payload(ref, ENTITY_SCHEMA, stored.envelope.payload)
                                         for ref, stored in view.instances.items()))
        snik = individualize(roster, IndividualizationDecision(C, "d-snik", G1, "Snik", DIRECTOR, "s3"),
                             decode_index(view), ()).instance
        species_files = {path: path.read_bytes() for path in (root / "sources").rglob("*") if path.is_file()}

        batch = images.change()
        batch.put(instance_envelope(snik), view.instances[G1].etag)
        batch.add_image(f"campaign:{C}", "snik", PNG_2)
        batch.place(G1, "portrait", Placement("set", "portrait", SNIK_ART), None)
        batch.commit()

        assert {path: path.read_bytes() for path in (root / "sources").rglob("*") if path.is_file()} == species_files
    with LibraryStore.open(root) as store:
        images = ImageLibrary(store, handle())
        assert store.load().problems == ()
        assert portrait(images, G1).image == SNIK_ART and portrait(images, G1).layer == "campaign"
        assert images.read_image_bytes(SNIK_ART) == PNG_2
        for subject in (SPECIES.ref.unpinned(), G2, G3):
            shown = portrait(images, subject)
            assert shown.image == SPECIES_ART and images.read_image_bytes(shown.image) == PNG
        reopened = instance_from_payload(G1, ENTITY_SCHEMA, store.load().instances[G1].envelope.payload)
        assert reopened.local_name == "Snik" and reopened.individuality.template == SPECIES.ref
