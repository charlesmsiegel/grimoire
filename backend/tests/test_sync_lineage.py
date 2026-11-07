"""046 art lineage over device sync: what reads a base chain is held until sync can compare it.

A portrayal's ``hide`` shows nothing and syncs; its ``inherit`` (a reset) and a campaign's ``inherit``
show whatever the chain above them shows, which sync does not compare yet, so they are held rather
than copied to a device whose chain might show another picture. Synthetic data only.
"""

from __future__ import annotations

import pytest
from image_fixtures import PNG, WREN_A, handle, seed
from portrayal_fixtures import BASE, CAST, VAMPIRE_MARA, seed_elderfen
from store_helpers import OWNER
from sync_helpers import DESKTOP, PHONE

from grimoire.core.identity import PlacementRef
from grimoire.images import Hidden, Placement
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore
from grimoire.store.layout import address_parts
from grimoire.sync import identify, sync_until_quiet

VAMPIRE = VAMPIRE_MARA.unpinned()
FULL = ("sources", "campaigns", "assets", "kept")
H = {DESKTOP: handle(*FULL), PHONE: handle(*FULL)}


def _path(ref) -> str:  # type: ignore[no-untyped-def]
    return "/".join(address_parts(ref))


@pytest.fixture
def devices(tmp_path):
    desktop, phone = LibraryStore.create(tmp_path / DESKTOP), LibraryStore.create(tmp_path / PHONE)
    seed(desktop)
    seed_elderfen(desktop, H[DESKTOP])
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    images = ImageLibrary(desktop, H[DESKTOP], owner=OWNER)
    batch = images.change()
    portrait = batch.add_image(CAST, "mara-portrait", PNG)
    batch.place(BASE.unpinned(), "portrait", Placement("set", "portrait", portrait), None)
    batch.place(VAMPIRE, "portrait", Placement("hide"), None)
    batch.place(WREN_A, "avatar", Placement("hide"), None)
    batch.commit()
    assert sync_until_quiet(desktop, phone, H).conflicts == ()
    yield desktop, phone, images
    desktop.close()
    phone.close()


def _reset(images, subject, slot):  # type: ignore[no-untyped-def]
    _, etag = images.read_placement(PlacementRef(subject, slot))
    images.place(subject, slot, Placement("inherit"), etag).commit()


def test_a_portrayals_clear_syncs(devices):
    desktop, phone, _ = devices
    shown = ImageLibrary(phone, H[PHONE], owner=OWNER).effective_placements(VAMPIRE).slot("portrait")
    assert isinstance(shown, Hidden)


@pytest.mark.parametrize(("subject", "slot"), [(VAMPIRE, "portrait"), (WREN_A, "avatar")],
                         ids=["portrayal", "campaign"])
def test_a_reset_is_held_because_it_shows_what_the_chain_shows(devices, subject, slot):
    desktop, phone, images = devices
    _reset(images, subject, slot)
    report = sync_until_quiet(desktop, phone, H)
    held = _path(PlacementRef(subject, slot))
    assert {conflict.path: conflict.code for conflict in report.conflicts} == {held: "head_dependency"}
    assert "inherit" in report.conflict(held).detail
    assert isinstance(ImageLibrary(phone, H[PHONE], owner=OWNER).effective_placements(subject).slot(slot), Hidden)
