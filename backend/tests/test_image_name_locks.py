"""The per-name and sidecar image locks: process-scoped and striped (stage 4, M5).

Two families, `locks.image_name_lock(d, name)` and `locks.image_sidecar_lock(d,
filename)`, each `IMAGE_NAME_STRIPES` stripes keyed by the resolved directory
and the case-folded name. One lock instance per (store, family, stripe), and a
file lock under it, so a second process serializes with this one.

Striping is what makes the set of lock files bounded, and it is also what makes
two unrelated names share a lock. That is safe only under three rules, and the
deadlock test below forces the stripe function so that every one of them is
exercised: a family's stripes are taken sorted and deduplicated, all up front;
`set_in` takes both of its sidecar stripes before either write; and a heal
reached while a stripe is already held never waits.

Synthetic images only.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from grimoire.store import (
    assets,
    campaigns,
    characters,
    image_descriptions,
    image_refs,
    image_store,
    locks,
    overlay,
    proclock,
    sync,
    world_bundle,
    worlds,
)

from .world_fixtures import seed_world

_FAMILIES = ("image-names", "image-sidecars")


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    """The image store and the lock files are both keyed on `paths.home()`."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))


def _png(seed: int) -> bytes:
    im = Image.new("RGB", (8, 6))
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y + seed) % 256)
                for y in range(6) for x in range(8)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _hold(lock):
    """Hold `lock` on another thread (the locks are reentrant, so the test's own
    thread would sail through); returns the release callable."""
    held, release = threading.Event(), threading.Event()

    def holder():
        with lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert held.wait(10), "holder thread never took the lock"

    def done():
        release.set()
        t.join(10)
    return done


# ---- case aliases ----------------------------------------------------------

def test_case_aliases_share_a_lock(monkeypatch, tmp_path):
    """On a case-insensitive filesystem `Gallery_1.png` IS `gallery_1.png`, so
    the two spellings must be one lock -- the gap `_image_locks_held`'s
    docstring used to own up to."""
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    d.mkdir(parents=True)
    assert locks.image_name_lock(d, "Gallery_1") is locks.image_name_lock(d, "gallery_1")
    assert assets._image_lock(d, "Avatar") is assets._image_lock(d, assets.AVATAR)
    assert (locks.image_sidecar_lock(d, "Descriptions.json")
            is locks.image_sidecar_lock(d, assets.DESCRIPTIONS_FILE))
    # Spelled through `..`, the directory is the same directory.
    assert locks.image_name_lock(d / ".." / "default", "avatar") is locks.image_name_lock(d, "avatar")

    # And it is the lock a writer takes: an upload under the other spelling waits.
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.3)
    done = _hold(assets._image_lock(d, "Gallery_1"))
    try:
        with pytest.raises(locks.StoreBusy):
            assets.put_in(d, "gallery_1", _png(1), "png")
    finally:
        done()
    assets.put_in(d, "gallery_1", _png(1), "png")


def test_the_two_families_never_share_a_lock(monkeypatch, tmp_path):
    """A name and a sidecar on the same stripe number are still two locks: the
    order is name THEN sidecar, and one family's stripe standing in for the
    other's would make that order a cycle."""
    monkeypatch.setattr(locks, "_image_stripe", lambda d, key: 0)
    d = tmp_path / "v"
    name, side = locks.image_name_lock(d, "a"), locks.image_sidecar_lock(d, "a")
    assert name is not side
    done = _hold(name)
    try:
        assert side.acquire(blocking=False)
        side.release()
    finally:
        done()


def test_a_shared_stripe_is_taken_once(monkeypatch, tmp_path):
    """Two names on one stripe are one acquisition, not two: the set is
    deduplicated before it is taken."""
    monkeypatch.setattr(locks, "_image_stripe", lambda d, key: 3)
    d = tmp_path / "v"
    got = locks.image_name_locks(d, ["avatar", "gallery_1", "Gallery_1"])
    assert got == [locks.image_name_lock(d, "avatar")]
    with assets._image_locks_held(d, "avatar", "gallery_1"):
        assert got[0]._depth == 1


def test_stripes_are_taken_in_stripe_order_not_name_order(monkeypatch, tmp_path):
    table = {"avatar": 9, "gallery_1": 2, "promote-tmp": 5}
    monkeypatch.setattr(locks, "_image_stripe", lambda d, key: table[key.casefold()])
    d = tmp_path / "v"
    got = locks.image_name_locks(d, ["avatar", "promote-tmp", "gallery_1"])
    assert got == [locks.image_name_lock(d, n) for n in ("gallery_1", "promote-tmp", "avatar")]


# ---- deadlock freedom under forced collisions ------------------------------

def _forced_stripes(d: Path, key: str) -> int:
    """Stripe numbers chosen so the stripe order is NOT the name order, and so
    names collide: `avatar` and `gallery_2` share one stripe, `promote-tmp` and
    `gallery_3` another, below it; the two directories' description sidecars
    land on different stripes, in the opposite order to their names."""
    key = key.casefold()
    if key == assets.DESCRIPTIONS_FILE:
        return 6 if "sera" in str(d) else 2
    return {"avatar": 7, "gallery_1": 3, "gallery_2": 7,
            "promote-tmp": 1, "gallery_3": 1}.get(key, 5)


def test_forced_stripe_collisions_do_not_deadlock(monkeypatch, tmp_path):
    """Every pattern that crosses stripes, run against each other with the
    stripe function forced to collide:

    - promotions (two name stripes, then the sidecar) against uploads (one name,
      then the sidecar);
    - a description save editing one directory and clearing the other, against
      the save that crosses the same two directories the other way -- which, if
      either took its second sidecar only once inside the first, is ABBA;
    - a delete of the avatar, which looks the slot up while holding it and so
      reaches the stranded-promotion heal NESTED, against an ordinary listing
      whose heal takes {promote-tmp, avatar, slot} from nothing. `promote-tmp`
      sorts below `avatar` here, so a nested heal that waited would hold
      `avatar` while waiting for `promote-tmp`, which the listing holds while
      it waits for `avatar`.

    A wedge surfaces as `StoreBusy` once the (shortened) timeout runs out."""
    monkeypatch.setattr(locks, "_image_stripe", _forced_stripes)
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", 3.0)
    root = tmp_path / "root"
    for cid in ("sera", "mara"):
        for i, name in enumerate((assets.AVATAR, "gallery_1", "gallery_2", "gallery_3")):
            assets.put_image(root, cid, "default", name, _png(10 * len(cid) + i), "png")
    sera = assets.version_dir(root, "sera", "default")
    # Pre-#253 residue whose rescue keeps failing, so every heal tries.
    (sera / "promote-tmp.png").write_bytes(_png(99))
    real_rename = Path.rename

    def rename(self, target):
        if self.name.startswith("promote-tmp"):
            raise OSError("held by a sync client")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    errors: list[BaseException] = []
    threads = [threading.Thread(target=_until(time.monotonic() + 1.5, w, errors), daemon=True)
               for w in _crossing_workers(root)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not any(t.is_alive() for t in threads), "a worker wedged"
    assert not errors, errors


def _until(stop: float, work, errors: list[BaseException]):
    """`work(i)` in a loop until `stop`; a `ValueError`/`OSError` is a lost race
    between these writers (a slot promoted away under a save, say), anything
    else -- `StoreBusy` above all -- is recorded."""
    def run():
        i = 0
        try:
            while time.monotonic() < stop:
                with contextlib.suppress(ValueError, OSError):
                    work(i)
                i += 1
        except BaseException as exc:  # noqa: BLE001 -- reported by the caller
            errors.append(exc)
    return run


def _crossing_workers(root: Path) -> list:
    sera = assets.version_dir(root, "sera", "default")
    mara = assets.version_dir(root, "mara", "default")

    def promote(i):
        assets.promote_image(root, "sera", "default", ("gallery_1", "gallery_2")[i % 2])

    def upload(i):
        assets.put_image(root, "sera", "default", ("gallery_3", "gallery_2")[i % 2],
                         _png(40 + i % 3), "png")

    def describe(a, b):
        return lambda i: image_descriptions.set_in(a, assets.AVATAR, f"text {i}", also_clear=b)

    def delete_avatar(i):
        cid = "mara" if i % 2 else "sera"
        assets.delete_image(root, cid, "default", assets.AVATAR)
        assets.put_image(root, cid, "default", assets.AVATAR, _png(60 + i % 2), "png")

    def listing(i):
        assets.list_images(root, "sera", "default")

    return [promote, upload, describe(sera, mara), describe(mara, sera), delete_avatar, listing]


def test_a_nested_heal_never_waits(monkeypatch, tmp_path):
    """Deterministically, the half of the test above that has to hold: a heal
    reached while this thread holds a stripe skips a busy lock instead of
    waiting on it."""
    monkeypatch.setattr(locks, "_image_stripe", _forced_stripes)
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.5)
    root = tmp_path / "root"
    assets.put_image(root, "sera", "default", "gallery_1", _png(1), "png")
    d = assets.version_dir(root, "sera", "default")
    (d / "promote-tmp.png").write_bytes(_png(2))
    done = _hold(locks.image_name_lock(d, "promote-tmp"))
    try:
        with assets._image_lock(d, "gallery_1"):
            assets._heal_stranded_promotion(d)          # returns; does not raise
        assert (d / "promote-tmp.png").exists()          # skipped, left for the next scan
    finally:
        done()
    assets._heal_stranded_promotion(d)                   # not nested, lock free: heals
    assert not (d / "promote-tmp.png").exists()
    assert assets.path_in(d, assets.AVATAR) is not None


# ---- two instances, one file lock ------------------------------------------

def test_two_instances_serialize_across_the_file_lock(monkeypatch, tmp_path):
    """The lock is the FILE, not the object: an instance built afresh (what a
    second process has) cannot take a stripe this one holds."""
    d = tmp_path / "v"
    for factory in (locks.image_name_lock, locks.image_sidecar_lock):
        first = factory(d, "avatar")
        monkeypatch.setattr(locks, "_image_stripe_locks", {})
        second = factory(d, "Avatar")
        assert second is not first
        with first:
            assert not second.acquire(blocking=False)
        assert second.acquire(blocking=False)
        second.release()
    # Contention past the timeout is ordinary busy-ness, not a claim about
    # who holds it -- it may be this very process.
    assert "another" not in str(locks.StoreBusy("stripe-001"))


_CHILD = """
import sys
from pathlib import Path
sys.path[:0] = {path!r}
from grimoire.store import locks
lock = locks.image_name_lock(Path({d!r}), "AVATAR")
with lock:
    print("HELD", flush=True)
    sys.stdin.readline()
"""


def test_a_second_process_waits_for_the_stripe(monkeypatch, tmp_path):
    d = tmp_path / "v"
    src = _CHILD.format(path=sys.path, d=str(d))
    p = subprocess.Popen([sys.executable, "-c", src], text=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         env={**os.environ, "GRIMOIRE_HOME": str(tmp_path / "home")})
    try:
        assert p.stdout.readline().strip() == "HELD"
        assert not locks.image_name_lock(d, "avatar").acquire(timeout=0.3)
    finally:
        p.stdin.write("\n")
        p.stdin.flush()
        p.wait(timeout=10)
    lock = locks.image_name_lock(d, "avatar")
    assert lock.acquire(timeout=5)
    lock.release()


# ---- _locks_if_free --------------------------------------------------------

def test_locks_if_free_never_raises(monkeypatch, tmp_path):
    d = tmp_path / "v"
    avatar, gallery = assets._image_lock(d, assets.AVATAR), assets._image_lock(d, "gallery_1")

    # A stripe listed twice is one acquisition, and is fully released after.
    with assets._locks_if_free([avatar, avatar]) as free:
        assert free
    assert not avatar._is_owned() and not locks.image_stripes_held()

    # Busy elsewhere: not free, and what was taken first is given back.
    if avatar is not gallery:
        done = _hold(gallery)
        try:
            with assets._locks_if_free([avatar, gallery]) as free:
                assert not free
            assert not avatar._is_owned()
        finally:
            done()

    # The lock directory unusable: not free, never an exception.
    def broken(path, deadline):
        raise PermissionError(13, "lock directory is read-only")

    monkeypatch.setattr(proclock, "acquire", broken)
    with assets._locks_if_free([avatar]) as free:
        assert not free
    assert not avatar._is_owned() and not locks.image_stripes_held()


def test_a_read_that_cannot_lock_still_reads(monkeypatch, tmp_path):
    """The read-side promotion recovery runs from every listing and lookup, so
    a lock it cannot take -- busy, or an OSError from the lock directory --
    leaves the journal for the next read rather than failing this one."""
    root = tmp_path / "root"
    assets.put_image(root, "sera", "default", assets.AVATAR, _png(1), "png")
    assets.put_image(root, "sera", "default", "gallery_1", _png(2), "png")
    d = assets.version_dir(root, "sera", "default")
    a, g = (image_refs.read(d, n).image for n in (assets.AVATAR, "gallery_1"))
    image_refs.write_journal(d, {"name": "gallery_1",
                                 "pre": {"avatar": a, "gallery_1": g},
                                 "post": {"avatar": g, "gallery_1": a},
                                 "desc": {"avatar": None, "gallery_1": None}})

    def broken(path, deadline):
        raise PermissionError(13, "lock directory is read-only")

    with monkeypatch.context() as m:
        m.setattr(proclock, "acquire", broken)
        assert [i["name"] for i in assets.list_images(root, "sera", "default")] == [
            "avatar", "gallery_1"]
        assert assets.image_path(root, "sera", "default", assets.AVATAR) is not None
        assert image_refs.read_journal(d) is not None
    assets.list_images(root, "sera", "default")
    assert image_refs.read_journal(d) is None
    assert image_refs.read(d, assets.AVATAR).image == g


# ---- set_in takes its stripes up front -------------------------------------

def test_set_in_takes_both_sidecar_stripes_before_either_write(monkeypatch, tmp_path):
    root = tmp_path / "root"
    assets.put_image(root, "sera", "default", assets.AVATAR, _png(1), "png")
    d = assets.version_dir(root, "sera", "default")
    other = tmp_path / "campaign-side"
    taken = []
    real = locks._ProcessScopedLock.acquire

    def acquire(self, blocking=True, timeout=-1):
        if self._domain == "image-sidecars" and not self._is_owned():
            taken.append(self)
        return real(self, blocking, timeout)

    monkeypatch.setattr(locks._ProcessScopedLock, "acquire", acquire)
    image_descriptions.set_in(d, assets.AVATAR, "At the gate.", also_clear=other)
    expected = locks.image_sidecar_locks([(d, assets.DESCRIPTIONS_FILE),
                                          (other, assets.DESCRIPTIONS_FILE)])
    assert taken == expected


# ---- every multi-step caller re-runs cleanly after StoreBusy ---------------

def _busy_at(monkeypatch, n):
    """Make the `n`th blocking acquisition of a name or sidecar stripe this
    thread does not already hold time out -- `StoreBusy` from `__enter__`, at
    exactly that step. Returns the running count."""
    real = locks._ProcessScopedLock.acquire
    seen = [0]

    def acquire(self, blocking=True, timeout=-1):
        if self._domain in _FAMILIES and blocking and not self._is_owned():
            seen[0] += 1
            if seen[0] == n:
                return False
        return real(self, blocking, timeout)

    monkeypatch.setattr(locks._ProcessScopedLock, "acquire", acquire)
    return seen


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


def _converges(monkeypatch, setup, run, state):
    """Stop `run` at every stripe acquisition in turn, re-run it, and require
    the end state an uninterrupted run reaches. Returns how many stop points
    there were (so a caller that takes no stripe at all is not vacuously
    passed)."""
    ctx = setup("control")
    run(ctx)
    want = state(ctx)
    n = 1
    while True:
        ctx = setup(f"stop-{n}")
        with monkeypatch.context() as m:
            _busy_at(m, n)
            try:
                run(ctx)
            except locks.StoreBusy:
                stopped = True
            else:
                stopped = False
        if not stopped:
            assert state(ctx) == want, f"uninterrupted run {n} differs"
            return n - 1
        run(ctx)
        assert state(ctx) == want, f"re-run after a stop at acquisition {n} differs"
        n += 1


def _slotted(base: Path, label: str) -> Path:
    """A version folder with placements, a legacy file, a crop and captions."""
    src = base / label / "assets" / "default"
    assets.put_in(src, assets.AVATAR, _png(1), "png")
    assets.put_in(src, "gallery_1", _png(2), "png")
    src.mkdir(parents=True, exist_ok=True)
    (src / "gallery_2.png").write_bytes(_png(3))          # legacy
    image_refs.write(src, assets.AVATAR, image_refs.read(src, assets.AVATAR).image, focus=40)
    assets.edit_sidecar(src, assets.DESCRIPTIONS_FILE, {"avatar": "Gate.", "gallery_1": "Stair."})
    return src


def _copy_slots_case(tmp_path, overwrite):
    def setup(label):
        src = _slotted(tmp_path, label)
        dst = tmp_path / label / "dst"
        assets.put_in(dst, "gallery_1", _png(9), "png")      # one slot already held
        return src, dst

    return (setup, lambda ctx: assets.copy_slots(*ctx, overwrite=overwrite),
            lambda ctx: _tree(ctx[1]))


def _copy_tree_case(tmp_path):
    def setup(label):
        src = _slotted(tmp_path, label)
        return src.parent, tmp_path / label / "library" / "assets"

    return setup, lambda ctx: sync._copy_tree(*ctx), lambda ctx: _tree(ctx[1])


def _copy_record_dir_down_case(tmp_path):
    def setup(label):
        wid = worlds.create_world(f"Realm {label}")
        wroot = worlds.world_root(wid)
        aid, vid = characters.create_character(wroot, "Seraphine")
        for i, name in enumerate((assets.AVATAR, "gallery_1", "gallery_2")):
            assets.put_image(wroot, aid, vid, name, _png(20 + i), "png")
        cid = campaigns.create_campaign(f"Run {label}", wid)
        croot = campaigns.campaign_root(cid)
        assets.write_focus(croot, aid, vid, 30)              # an override on the avatar
        return cid, aid, croot

    return (setup, lambda ctx: overlay.copy_record_dir_down(ctx[0], "characters", ctx[1]),
            lambda ctx: _tree(ctx[2] / "characters" / ctx[1] / "assets"))


def _replace_gallery_case(tmp_path):
    fresh = [(1, image_store.ingest(_png(31), "png").id),
             (2, image_store.ingest(_png(32), "png").id)]

    def setup(label):
        root = tmp_path / label
        cid, vid = characters.create_character(root, "Seraphine")
        for i, name in enumerate(("gallery_1", "gallery_2", "gallery_3")):
            assets.put_image(root, cid, vid, name, _png(40 + i), "png")
        d = assets.version_dir(root, cid, vid)
        assets.edit_sidecar(d, assets.DESCRIPTIONS_FILE,
                            {"gallery_1": "Old one.", "gallery_3": "Going."})
        return root, cid, vid

    def state(ctx):
        root, cid, vid = ctx
        d = assets.version_dir(root, cid, vid)
        slots = {n: r.image for n, r in image_refs.scan(d).items()}
        files = sorted(p.name for p in d.iterdir() if p.is_file())
        return slots, files, image_descriptions.read_raw(d)

    return setup, lambda ctx: characters._replace_gallery(*ctx, fresh), state


_CALLERS = {
    "copy_slots": lambda tmp_path: _copy_slots_case(tmp_path, overwrite=False),
    "copy_slots-overwrite": lambda tmp_path: _copy_slots_case(tmp_path, overwrite=True),
    "_copy_tree": _copy_tree_case,
    "copy_record_dir_down": _copy_record_dir_down_case,
    "_replace_gallery": _replace_gallery_case,
}


@pytest.mark.parametrize("caller", sorted(_CALLERS))
def test_each_multi_step_caller_is_safe_to_rerun_after_store_busy(monkeypatch, tmp_path, caller):
    """Each caller that takes the name or sidecar locks more than once can now
    stop part-way with `StoreBusy` (a stripe held past the timeout, possibly by
    an unrelated name). Stopped at every one of its acquisitions in turn, a
    re-run reaches the same state an uninterrupted run does. (`_replace_gallery`
    and `delete_image` take the sidecar lock up front for this: stopped between
    a slot's new image and the old caption's drop, a re-run found nothing left
    to redo.)"""
    setup, run, state = _CALLERS[caller](tmp_path)
    assert _converges(monkeypatch, setup, run, state) >= 1


def test_bundle_containment_takes_no_name_or_sidecar_stripe(monkeypatch, tmp_path):
    """The fifth multi-step caller. Containment rewrites placements in a
    staging tree nobody else can name, so it takes no name or sidecar lock and
    cannot stop part-way on one; the export's promotion recovery never waits.
    So an export and an import run to the end with every such acquisition
    refused."""
    old = seed_world()
    with monkeypatch.context() as m:
        seen = _busy_at(m, 1)
        dest = tmp_path / "bundle.zip"
        world_bundle.write_bundle(old, dest)
        new = world_bundle.import_bundle(dest)
    assert seen[0] == 0
    assert worlds.read_world(new)["counts"] == worlds.read_world(old)["counts"]
