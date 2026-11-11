"""114's art editor over a 046 derived portrayal: its edit modes follow its own head. Synthetic data only."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from image_fixtures import PNG, handle
from portrayal_fixtures import BASE, CAST, VAMPIRE_MARA, seed_elderfen
from store_helpers import OWNER

from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary
from grimoire.mediaapi import EDIT_MODES, MediaSyncApi
from grimoire.store import LibraryStore
from grimoire.store.layout import address_parts

VAMPIRE_ENTITY = VAMPIRE_MARA.unpinned()


@pytest.fixture
def media(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as store:
        seed_elderfen(store, handle())
        images = ImageLibrary(store, handle(), owner=OWNER)
        batch = images.change()
        portrait = batch.add_image(CAST, "mara-portrait", PNG)
        batch.place(BASE.unpinned(), "portrait", Placement("set", "portrait", portrait), None)
        batch.commit()
        # the adapter's own ops for one subject's art read only the store and the image library
        text = SimpleNamespace(store=store, authority=None, images=images, owner=OWNER, _lock=threading.RLock(),
                               peers={}, reports={})
        yield store, MediaSyncApi(text)  # type: ignore[arg-type]


def test_a_portrayal_whose_base_is_gone_may_still_hide_or_reset_its_own_picture(media):
    store, api = media
    assert api.call("media.art", {"subject": str(VAMPIRE_ENTITY)})["modes"] == list(EDIT_MODES)
    assert api.call("media.art", {"subject": str(BASE.unpinned())})["modes"] == ["set", "hide"]  # it inherits nothing
    store.root.joinpath(*address_parts(BASE)).unlink()
    art = api.call("media.art", {"subject": str(VAMPIRE_ENTITY)})
    assert art["lineage"] == [str(VAMPIRE_ENTITY)]  # nothing is inherited now
    assert art["modes"] == list(EDIT_MODES)  # but the portrayal is still derived: 038 lets it hide
    hidden = api.call("media.place", {"subject": str(VAMPIRE_ENTITY), "slot": "portrait", "mode": "hide",
                                      "expected": None})
    assert next(slot for slot in hidden["slots"] if slot["slot"] == "portrait")["state"] == "hidden"
