"""An overlay listing resolves its campaign once, not once per row.

`list_characters` patches every row through `list_images`, `read_focus` and
`tagline`, and each of those used to resolve the campaign on its own: the world
root (a parse of campaign.md), the tombstones (deleted.json) and the
detachments (detached.json), several times over per character. None of that
changes between two rows of one listing, so an `overlay.View` now carries it
through the sweep. These tests count the opens of those three files and pin the
point of the change -- the count does not grow with the cast.
"""

import io
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from grimoire.main import app
from grimoire.store import (
    assets,
    campaigns,
    characters,
    failsoft,
    overlay,
    paths,
    pcs,
    statcache,
    taglines,
    worlds,
)

client = TestClient(app)

#: The per-campaign files every overlay resolution reads.
CAMPAIGN_FILES = ("campaign.md", "deleted.json", "detached.json")


@pytest.fixture(autouse=True)
def _forget_corruption_warnings():
    """`failsoft` dedupes on module state; tests must not inherit each other's."""
    failsoft._warned.clear()


def _campaign(cast: int, *, versions: int = 1) -> tuple[str, list[str]]:
    """A world of `cast` characters with art and taglines, and a campaign on it
    whose tombstone and detachment ledgers both exist -- so the reads being
    counted are real opens, not a cheap FileNotFoundError."""
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    ids = []
    for i in range(cast):
        aid, vid = characters.create_character(wroot, f"Mara {i}")
        for n in range(1, versions):
            era = characters.create_version(wroot, aid, f"Era {n}", characters.blank_card(f"Mara {i}"))
            assets.put_image(wroot, aid, era, "avatar", b"png", "png")
        assets.put_image(wroot, aid, vid, "avatar", b"png", "png")
        assets.put_image(wroot, aid, vid, "gallery_1", b"gallery", "png")
        taglines.write(wroot, aid, "Keeps the Saltmarch ledgers.")
        pid, pvid = pcs.create_pc(wroot, f"Seraphine {i}", [])
        assets.put_image(wroot, pid, pvid, "avatar", b"png", "png", pcs.ASSET_BASE)
        ids.append(aid)
    cid = campaigns.create_campaign("Winifred's Run", wid)
    overlay.add_deleted(cid, "lore/nothing-here")
    overlay.add_detached(cid, "characters/nobody-here")
    return cid, ids


class _Opens:
    """Count `io.open` of files with these basenames while active -- every Path
    read in the store (`read_text`, `failsoft.read_json`) goes through it.

    A context manager rather than `monkeypatch`, because a test here counts
    several windows and `monkeypatch.undo()` would also undo the store
    isolation the test is running under."""

    def __init__(self, names=CAMPAIGN_FILES):
        self.counts = dict.fromkeys(names, 0)

    def __enter__(self):
        self._real = real = io.open

        def counting(file, *a, **kw):
            if isinstance(file, (str, os.PathLike)):
                base = os.path.basename(os.fspath(file))
                if base in self.counts:
                    self.counts[base] += 1
            return real(file, *a, **kw)

        io.open = counting
        return self.counts

    def __exit__(self, *exc):
        io.open = self._real


def _opens_during(fn, names=CAMPAIGN_FILES):
    with _Opens(names) as counts:
        fn()
    return counts


def _home(monkeypatch, tmp_path, sub):
    root = tmp_path / sub
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    return root


@pytest.mark.parametrize("listing", ["list_characters", "list_pcs"])
def test_listing_resolves_the_campaign_a_fixed_number_of_times(monkeypatch, tmp_path, listing):
    got = {}
    for cast in (5, 50):
        _home(monkeypatch, tmp_path, f"cast{cast}")
        cid, _ids = _campaign(cast)
        rows = getattr(overlay, listing)(cid)
        assert len(rows) == cast
        got[cast] = _opens_during(lambda cid=cid: getattr(overlay, listing)(cid))
    assert got[5] == got[50]
    assert all(n <= 1 for n in got[50].values()), got[50]


def test_read_character_resolves_once_across_its_versions(monkeypatch, tmp_path):
    """The detail read patches every version with the same helpers (and the
    world's pass-through versions beside them), so it has the same shape."""
    got = {}
    for versions in (1, 6):
        _home(monkeypatch, tmp_path, f"v{versions}")
        cid, ids = _campaign(1, versions=versions)
        assert len(overlay.read_character(cid, ids[0])["versions"]) == versions
        got[versions] = _opens_during(lambda cid=cid, aid=ids[0]: overlay.read_character(cid, aid))
    assert got[1] == got[6]
    assert all(n <= 1 for n in got[6].values()), got[6]


def test_campaign_gallery_resolution_does_not_grow_with_the_cast(monkeypatch, tmp_path):
    got = {}
    for cast in (5, 50):
        _home(monkeypatch, tmp_path, f"gallery{cast}")
        cid, _ids = _campaign(cast)
        r = client.get(f"/api/campaigns/{cid}/gallery")
        assert r.status_code == 200
        # avatar + gallery_1 per character, avatar per PC
        assert len(r.json()) == 3 * cast
        got[cast] = _opens_during(lambda cid=cid: client.get(f"/api/campaigns/{cid}/gallery"))
    assert got[5] == got[50]


def test_a_listing_is_unchanged_by_sharing_the_view(monkeypatch, tmp_path):
    """The view is an optimisation of the same rules, not new ones: a detached
    character shows only its own art and tagline, a tombstoned image is gone,
    and inherited rows are patched from the world, all in one listing."""
    _home(monkeypatch, tmp_path, "rules")
    cid, ids = _campaign(3)
    croot = overlay.croot_of(cid)
    kept, severed, trimmed = ids
    # `severed` is the campaign's own record now: its world id-mate is a stranger
    own, _ = characters.create_character(croot, "Mara 1")
    assert own == severed
    overlay.add_detached(cid, f"characters/{severed}")
    overlay.delete_image(cid, trimmed, "default", "gallery_1")
    rows = {r["id"]: r for r in overlay.list_characters(cid)}

    assert (rows[kept]["has_avatar"], rows[kept]["gallery_count"]) == (True, 1)
    assert rows[kept]["tagline"] == "Keeps the Saltmarch ledgers."
    assert (rows[severed]["has_avatar"], rows[severed]["gallery_count"]) == (False, 0)
    assert rows[severed]["tagline"] == ""
    assert (rows[trimmed]["has_avatar"], rows[trimmed]["gallery_count"]) == (True, 0)
    for aid in ids:
        alone = overlay.list_images(cid, aid, "default")
        assert [i["name"] for i in alone] == (
            [] if aid == severed else ["avatar"] if aid == trimmed else ["avatar", "gallery_1"])


def test_a_single_call_still_reads_only_what_it_needs(monkeypatch, tmp_path):
    """The view resolves lazily, so a helper called on its own costs what it
    did before it took one: a campaign that holds its own tagline answers
    without parsing campaign.md or reading its tombstones."""
    _home(monkeypatch, tmp_path, "lazy")
    cid, ids = _campaign(1)
    taglines.write(overlay.croot_of(cid), ids[0], "The campaign's own line.")
    got = _opens_during(lambda: overlay.tagline(cid, ids[0]))
    assert got == {"campaign.md": 0, "deleted.json": 0, "detached.json": 0}


def test_a_view_answers_only_for_its_own_campaign(monkeypatch, tmp_path):
    """Passing one campaign's view to another campaign's read would silently
    resolve against the wrong world and the wrong tombstones."""
    _home(monkeypatch, tmp_path, "mismatch")
    cid, ids = _campaign(1)
    other = campaigns.create_campaign("Other", campaigns.read_campaign(cid)["meta"]["world"])
    with pytest.raises(ValueError):
        overlay.list_images(other, ids[0], "default", v=overlay.view(cid))


def test_a_pointer_resolved_listing_opens_the_pointer_at_most_once(monkeypatch, tmp_path):
    """Both halves together, the way desktop and Android resolve the store: no
    `GRIMOIRE_HOME`, only the bootstrap pointer."""
    store_root = _home(monkeypatch, tmp_path, "pointed")
    cid, _ids = _campaign(20)
    pointer = paths.pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"data_dir": str(store_root)}), encoding="utf-8")
    old = time.time_ns() - 2 * statcache.RACY_WINDOW_NS
    os.utime(pointer, ns=(old, old))
    monkeypatch.delenv("GRIMOIRE_HOME")
    assert paths.home() == store_root

    with _Opens((pointer.name,)) as counts:
        assert len(overlay.list_characters(cid)) == 20
    assert counts[pointer.name] <= 1, counts


def test_views_on_different_threads_do_not_queue_behind_each_other(monkeypatch, tmp_path):
    """A view resolves on the request thread that built it, and two requests'
    views share nothing -- so one parsing a slow campaign.md must not hold up
    another. (`functools.cached_property` on 3.11 would: its lock is per
    property for the whole class, and held across the computation.)"""
    _home(monkeypatch, tmp_path, "threads")
    cid, _ids = _campaign(1)
    inside, release, done = threading.Event(), threading.Event(), threading.Event()
    real = overlay.wroot_of

    def slow(c):
        if threading.current_thread().name == "slow-view":
            inside.set()
            release.wait(5)
        return real(c)

    monkeypatch.setattr(overlay, "wroot_of", slow)
    held = threading.Thread(target=lambda: overlay.view(cid).wroot, name="slow-view")
    held.start()
    assert inside.wait(5)
    other = threading.Thread(target=lambda: (overlay.view(cid).wroot, done.set()))
    other.start()
    try:
        assert done.wait(2), "a second view waited on the first one's resolution"
    finally:
        release.set()
        held.join(5)
        other.join(5)
