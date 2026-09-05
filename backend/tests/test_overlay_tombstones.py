"""`deleted.json` is rewritten whole, so its writers take the campaign lock.

`overlay.add_deleted` was an unlocked read-modify-write of the whole file. Two
concurrent writers therefore lost one of the two tombstones, and a *lost*
tombstone resurrects a record or an image the user deleted -- which
`overlay.deleted`'s own fail-soft docstring names as the one direction of
failure a user cannot spot by looking.

That was survivable while every writer came from one request. The world image
library makes it load-bearing: a campaign hiding an inherited picture writes a
tombstone on a path that can run beside a record-image delete.
"""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from grimoire.store import campaigns, overlay, world_images, worlds


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def wid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("Realm")


@pytest.fixture
def cid(wid):
    return campaigns.create_campaign("Saltmarch Nights", wid)


def test_concurrent_tombstone_writes_all_survive(cid):
    """Measured against the pre-lock body, this fails two ways rather than one:
    on POSIX the losing writer's tombstone silently disappears, and on Windows
    the two `os.replace` calls collide outright and `atomic.write_text` raises
    `PermissionError` -- so the unlocked version could not even fail quietly
    here. Either way the lock is what makes all 24 land."""
    refs = [overlay.library_ref(f"pic{i:02d}") for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda r: overlay.add_deleted(cid, r), refs))
    assert overlay.deleted(cid) == set(refs)


def test_a_library_tombstone_can_be_dropped_again(cid):
    overlay.add_deleted(cid, overlay.library_ref("map"))
    assert overlay.library_ref("map") in overlay.deleted(cid)

    overlay.drop_library_tombstone(cid, "map")
    assert overlay.library_ref("map") not in overlay.deleted(cid)


def test_dropping_a_tombstone_that_is_not_there_is_quiet(cid):
    overlay.drop_library_tombstone(cid, "never-hidden")
    assert overlay.deleted(cid) == set()


def test_a_library_ref_cannot_be_confused_with_a_record_or_asset_ref():
    """Three segments where every asset ref has five, and `library` is not a
    kind -- so nothing that parses the record shape can misread it."""
    ref = overlay.library_ref("map")
    assert ref == "assets/library/map"
    assert len(ref.split("/")) == 3
    assert len(overlay._asset_ref("characters", "seraphine", "default",
                                  "avatar").split("/")) == 5


def test_deleting_a_world_image_clears_the_campaigns_that_hid_it(cid, wid):
    """A tombstone may not outlive the image it hid.

    `forget_world_record` calls this shape a defect for record art -- they hide
    by slot -- and sweeps it. A library image has no record to be swept along
    with, so without this, deleting `coastline` and uploading a new one under
    that name would leave this campaign blind to the new picture forever, with
    no UI able to say why.
    """
    world_images.put_image(wid, "coastline", _png(), "png")
    overlay.add_deleted(cid, overlay.library_ref("coastline"))

    world_images.delete_image(wid, "coastline")
    assert overlay.library_ref("coastline") not in overlay.deleted(cid)


def test_a_busy_campaign_does_not_fail_the_world_side_delete(cid, wid, monkeypatch):
    """Best-effort per campaign, `forget_world_record`'s shape and its reason:
    aborting the sweep would 500 a delete that has already happened. The stale
    tombstone that survives is visible through `list_hidden` and clearable by
    hand, which is the whole reason best-effort is affordable here."""
    from grimoire.store import locks

    world_images.put_image(wid, "coastline", _png(), "png")
    overlay.add_deleted(cid, overlay.library_ref("coastline"))

    def busy(_cid, _name):
        raise locks.CampaignBusy(_cid)

    monkeypatch.setattr(overlay, "drop_library_tombstone", busy)
    world_images.delete_image(wid, "coastline")          # must not raise

    assert world_images.image_path(wid, "coastline") is None      # bytes gone
    assert overlay.library_ref("coastline") in overlay.deleted(cid)  # entry kept
