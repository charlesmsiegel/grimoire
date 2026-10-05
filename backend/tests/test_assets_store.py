import errno
import hashlib
import io
import json
import os
import re
import time

import pytest
from PIL import Image

from grimoire.store import assets, image_refs, image_store, statcache


@pytest.fixture(autouse=True)
def _home(tmp_path):
    """The image store is global (`paths.home()`), so every test here points it
    at a directory of its own -- one that is not `tmp_path` itself, which most
    of these tests use as a record ROOT, so a listing of it never meets the
    store's own `assets/` tree.

    Its own `MonkeyPatch`, not the test's: several tests here call
    `monkeypatch.undo()` mid-test to lift a fault they injected, and that must
    not also move the store out from under the reads that follow."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
        yield


def _png(seed: int = 0) -> bytes:
    """A real, decodable PNG made from arithmetic."""
    im = Image.new("RGB", (8, 6))
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y + seed) % 256)
                for y in range(6) for x in range(8)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _png_level(seed: int, level: int) -> bytes:
    """`_png(seed)`'s pixels under another zlib level: other bytes, same image."""
    im = Image.open(io.BytesIO(_png(seed)))
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=level)
    return buf.getvalue()


def _vdir(tmp_path, cid="sera", vid="default"):
    return tmp_path / "characters" / cid / "assets" / vid


def _named(imgs):
    """(name, ext) pairs — the identity part of a listing, ignoring the v token."""
    return [(i["name"], i["ext"]) for i in imgs]


def test_put_list_get_round_trip(tmp_path):
    assert assets.list_images(tmp_path, "sera", "default") == []
    ext = assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"\x89PNG", "png")
    assert ext == "png"
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("avatar", "png")]
    p = assets.image_path(tmp_path, "sera", "default", "avatar")
    assert p is not None and p.read_bytes() == b"\x89PNG"


def test_replace_with_different_ext_leaves_one_file(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "jpg")
    imgs = assets.list_images(tmp_path, "sera", "default")
    assert _named(imgs) == [("avatar", "jpg")]  # exactly one, new ext
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"b"


@pytest.mark.parametrize("ext", ["gif", "webp"])
def test_list_images_reports_one_entry_per_logical_image(tmp_path, ext):
    """A stem with two files on disk is one image, listed once, as the one
    `image_path` resolves.

    `put_in` writes the new extension before dropping the old, and `path_in`
    self-heals an unlink that never happened, so the two-file state is
    reachable and can persist. Listing it twice double-counts galleries, gives
    the frontend two tiles under one key, and -- the reason this was found --
    lets a cache token be read off the sibling the server will not serve, which
    a `?v=` URL then pins immutably for a year.
    """
    import os
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"served bytes", "png")
    served = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    stale = served.with_suffix(f".{ext}")
    stale.write_bytes(b"stale bytes of another length")
    os.utime(stale, (1, 1))

    imgs = assets.list_images(tmp_path, "sera", "default")
    assert _named(imgs) == [("avatar", "png")]
    assert imgs[0]["v"] == assets.image_version(served)


def test_a_file_that_vanishes_mid_scan_is_dropped_not_raised(tmp_path, monkeypatch):
    """The listing stats each file it just saw in the directory, and `put_in`
    unlinks a stale sibling right after publishing the new one -- so a
    concurrent reader can genuinely scan a path that is gone by the stat.
    `_mtime_ns` already tolerates it; the token stat has to as well, or one
    vanishing file 500s a route that lists every character.
    """
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_0", b"b", "png")
    doomed = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    real = assets._addressable_name

    def vanishing(stem):
        # runs per candidate file, immediately before it is stat'd
        if stem == assets.AVATAR and doomed.exists():
            doomed.unlink()
        return real(stem)

    monkeypatch.setattr(assets, "_addressable_name", vanishing)
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("gallery_0", "png")]


def test_delete_and_absent(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)
    assert assets.image_path(tmp_path, "sera", "default", "avatar") is None
    assets.delete_image(tmp_path, "sera", "default", "ghost")  # no error


def test_unsafe_and_unsupported_rejected(tmp_path):
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "../x", b"a", "png")
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "a.b", b"a", "png")  # dot in name
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "*", b"a", "png")  # glob metacharacter
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "svg")  # not allowlisted
    with pytest.raises(ValueError):
        # reserved: the recovery in #253 treats a file under this name as crash
        # residue, and the old swap would have clobbered a real image there anyway
        assets.put_image(tmp_path, "sera", "default", "promote-tmp", b"a", "png")
    with pytest.raises(ValueError):
        # case variants too: on Windows and macOS this is the same file
        assets.put_image(tmp_path, "sera", "default", "Promote-Tmp", b"a", "png")
    assert assets.image_path(tmp_path, "..", "default", "avatar") is None  # unsafe cid


def test_promote_swaps_gallery_with_avatar(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"old", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_2", b"new", "webp")
    assets.promote_image(tmp_path, "sera", "default", "gallery_2")
    av = assets.image_path(tmp_path, "sera", "default", "avatar")
    gal = assets.image_path(tmp_path, "sera", "default", "gallery_2")
    assert av.read_bytes() == b"new" and av.suffix == ".webp"
    assert gal.read_bytes() == b"old" and gal.suffix == ".png"


def test_promote_without_existing_avatar_renames(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"n", "png")
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"n"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1") is None


def test_promote_missing_image_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_9")


def test_promote_avatar_itself_is_a_noop(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "png")
    assets.promote_image(tmp_path, "sera", "default", "avatar")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"a"


def test_focus_round_trip_and_clamp(tmp_path):
    assert assets.read_focus(tmp_path, "sera", "default") is None
    assets.write_focus(tmp_path, "sera", "default", 62)
    assert assets.read_focus(tmp_path, "sera", "default") == 62
    assets.write_focus(tmp_path, "sera", "default", 250)
    assert assets.read_focus(tmp_path, "sera", "default") == 100
    assets.clear_focus(tmp_path, "sera", "default")
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_focus_sidecar_not_listed_as_image(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("avatar", "png")]


def test_focus_cleared_when_avatar_changes(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "png")  # re-upload clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"g", "png")  # non-avatar keeps it
    assert assets.read_focus(tmp_path, "sera", "default") == 30

    assets.promote_image(tmp_path, "sera", "default", "gallery_1")  # promote clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)  # delete clears
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_base_param_roots_other_kinds(tmp_path):
    assets.put_image(tmp_path, "docks", "default", "avatar", b"i", "png", base="locations")
    p = assets.image_path(tmp_path, "docks", "default", "avatar", base="locations")
    assert p is not None
    assert image_refs.read(tmp_path / "locations" / "docks" / "assets" / "default",
                           "avatar") is not None
    # not visible under the default characters/ base
    assert assets.image_path(tmp_path, "docks", "default", "avatar") is None
    assert _named(assets.list_images(tmp_path, "docks", "default", base="locations")) == [
        ("avatar", "png")]
    assets.delete_image(tmp_path, "docks", "default", "avatar", base="locations")
    assert assets.image_path(tmp_path, "docks", "default", "avatar", base="locations") is None


def test_list_images_version_token_tracks_content(tmp_path):
    from grimoire.store import assets

    assets.put_image(tmp_path, "c", "v1", "avatar", b"png-one", "png")
    first = assets.list_images(tmp_path, "c", "v1")
    assert first[0]["v"]
    assets.put_image(tmp_path, "c", "v1", "avatar", b"png-two", "png")
    assert assets.list_images(tmp_path, "c", "v1")[0]["v"] != first[0]["v"]

    # A legacy file's token is its stat, so it moves with the file.
    p = tmp_path / "characters" / "c" / "assets" / "v1" / "gallery_1.png"
    p.write_bytes(b"legacy")
    before = assets.list_images(tmp_path, "c", "v1")[1]["v"]
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    assert assets.list_images(tmp_path, "c", "v1")[1]["v"] != before


def test_a_failed_image_write_keeps_the_previous_image(tmp_path, monkeypatch):
    """put_image used to unlink prior-extension files BEFORE writing the new
    one, so anything that failed in between lost the image outright -- a bug no
    amount of write atomicity can fix, because the delete came first (#233)."""
    from grimoire.store import atomic
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"original", "png")

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(atomic, "write_bytes", boom)
    with pytest.raises(OSError):
        assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"new", "jpg")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"original"


def test_a_stale_sibling_extension_does_not_win_the_lookup(tmp_path):
    """Writing before unlinking leaves both extensions present for a moment.
    image_path used to return sorted(...)[0], which hands back the STALE file
    whenever the old extension sorts first (jpg < png)."""
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    d.mkdir(parents=True)
    (d / "avatar.jpg").write_bytes(b"stale")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"fresh", "png")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"fresh"


def test_an_orphaned_sibling_still_resolves_to_the_newest(tmp_path, monkeypatch):
    """If the stale-sibling unlink ever fails, the wrong image must not become
    permanently sticky -- the mtime tie-break makes that state self-healing."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"old", "jpg")
    monkeypatch.setattr("pathlib.Path.unlink", lambda self, **kw: None)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"new", "png")

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"new"


def test_lookup_survives_a_sibling_vanishing_mid_scan(tmp_path, monkeypatch):
    """put_image writes the new extension then unlinks the stale one, so a
    concurrent reader can glob a path that is gone by the time it stats. The
    old sorted(...)[0] never stat'd, so raising here would be a regression.

    The legacy rule, so the avatar is a legacy file planted by hand: `put_image`
    writes a placement now, and a placement is never globbed."""
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    d.mkdir(parents=True)
    (d / "avatar.png").write_bytes(b"real")
    (d / "avatar.jpg").write_bytes(b"about to vanish")
    (d / "avatar.jpg").unlink()

    real_stat = assets.Path.stat

    def vanishing(self, *a, **kw):
        # Only the file that actually vanished is absent. Raising for everything
        # *except* avatar.png also made the enclosing directory look gone, and
        # image_path checks `d.exists()` first -- which reaches Path.stat on 3.11
        # but not on 3.14, so the inverted condition passed only by accident of
        # the development interpreter's pathlib internals.
        #
        # The errno matters too: pathlib's exists() swallows an OSError only when
        # _ignore_error() recognises its errno, and `FileNotFoundError(self)`
        # carries none -- so a synthetic error without it does not behave like a
        # real vanishing file.
        if self.name == "avatar.jpg":
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(assets.Path, "stat", vanishing)
    monkeypatch.setattr(assets.Path, "glob",
                        lambda self, pat: iter([d / "avatar.jpg", d / "avatar.png"]))

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.name == "avatar.png"


def test_concurrent_puts_of_different_extensions_leave_exactly_one_image(tmp_path):
    """The real invariant, exercised with real threads rather than a simulated
    re-entrancy the lock now forbids: whichever upload wins, exactly one image
    survives and it is one that was actually written. Two earlier attempts at
    this (glob-after-write, then snapshot-by-path) each left a window where
    both calls succeeded and NO image remained."""
    import threading

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"ORIGINAL", "png")
    payloads = {"png": b"NEW-PNG", "jpg": b"NEW-JPG", "webp": b"NEW-WEBP"}
    start = threading.Barrier(len(payloads))
    errors = []

    def upload(ext):
        try:
            start.wait(timeout=5)
            for _ in range(20):        # hammer the window
                assets.put_image(tmp_path, "sera", "default", assets.AVATAR,
                                 payloads[ext], ext)
        except Exception as e:         # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=upload, args=(e,)) for e in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent put raised: {errors}"
    assert not any(t.is_alive() for t in threads), "a put_image deadlocked"

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None, "concurrent uploads destroyed the image"
    assert p.read_bytes() in payloads.values(), "lookup returned a stale image"
    survivors = assets.list_images(tmp_path, "sera", "default")
    assert len(survivors) == 1, f"stale siblings left behind: {survivors}"


def _image_files(tmp_path, cid="sera", vid="default"):
    d = tmp_path / "characters" / cid / "assets" / vid
    return [p for p in d.iterdir()
            if p.is_file() and p.suffix.lstrip(".").lower() in ("png", "jpg", "jpeg",
                                                                "gif", "webp")]


def _fail_after(monkeypatch, allowed: int):
    """Crash simulator: let `allowed` filesystem mutations through, then raise.

    Hooks both `Path.rename` and `atomic.write_bytes` on purpose, so the cut
    lands between two of promotion's steps whichever way it moves bytes -- a
    rename dance and a republish are both covered, which is what makes this a
    test of the invariant and not of one implementation of it.

    Each hooked call is one indivisible event; the windows *inside* an atomic
    write (a failed replace, a failed write, a leftover temp) are
    `test_atomic.py`'s -- see `test_replace_failure_leaves_the_previous_record
    _intact`, `test_write_failure_leaves_the_previous_record_intact` and
    `test_temp_files_are_invisible_to_the_record_listers` (PR review asked
    where that coverage lives).
    """
    from grimoire.store import atomic

    left = [allowed]
    real_rename, real_write = assets.Path.rename, atomic.write_bytes

    def spend():
        if left[0] <= 0:
            raise OSError("crash")
        left[0] -= 1

    def rename(self, target):
        spend()
        return real_rename(self, target)

    def write_bytes(path, data):
        spend()
        return real_write(path, data)

    monkeypatch.setattr(assets.Path, "rename", rename)
    monkeypatch.setattr(atomic, "write_bytes", write_bytes)


@pytest.mark.parametrize("new_ext", ["png", "webp"])
@pytest.mark.parametrize("allowed", [0, 1, 2, 3])
def test_a_crash_mid_promotion_always_leaves_a_resolvable_avatar(
        tmp_path, monkeypatch, allowed, new_ext):
    """Promotion used to swap through a fixed `promote-tmp<ext>` name in three
    renames (#253). Each rename was atomic; the sequence was not, so a crash
    after the second left the promoted image parked under a name nothing ever
    looks for and NO avatar at all. The avatar slot must always resolve, and
    nothing may be left behind under an unreachable name.

    The swap makes only two hooked mutations, so the last cut points let it run
    to completion -- those assert the same invariant on the success path."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"OLD", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"NEW", new_ext)

    _fail_after(monkeypatch, allowed)
    try:
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    except OSError:
        pass
    monkeypatch.undo()

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None, f"a crash after {allowed} steps left no avatar at all"
    assert p.read_bytes() in (b"OLD", b"NEW"), "the avatar is neither image"
    stranded = [q.name for q in _image_files(tmp_path)
                if q.stem not in ("avatar", "gallery_1")]
    assert not stranded, f"a crash after {allowed} steps stranded {stranded}"


def test_promotion_leaves_one_file_per_slot(tmp_path):
    """A completed swap across extensions must not leave the old extension of
    either slot behind -- image_path would tie-break it away, but list_images
    would show a phantom duplicate."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"OLD", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"NEW", "webp")
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [
        ("avatar", "webp"), ("gallery_1", "png")]


def test_a_promotion_that_cannot_clear_its_source_does_not_claim_success(tmp_path, monkeypatch):
    """With no avatar to swap back, the promoted image has to leave its slot --
    overlay.promote_image reads that emptiness to decide whether to tombstone an
    inherited image, so a silently-kept source becomes a visible duplicate.
    delete_image swallows unlink failures by design (PR review), so promotion
    has to confirm the slot rather than report a move that didn't happen."""
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"NEW", "png")
    monkeypatch.setattr("pathlib.Path.unlink", lambda self, **kw: None)  # unlink no-ops

    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")

    monkeypatch.undo()
    # The avatar was still published -- the leftover is the failure, not the promotion.
    av = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert av is not None and av.read_bytes() == b"NEW"


def test_promoting_a_file_of_an_unsupported_type_changes_nothing(tmp_path):
    """image_path will hand back any extension; put_image accepts only the
    allowlist. Checking both sides up front keeps that from surfacing halfway
    through the swap, with the avatar already replaced."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"OLD", "png")
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    (d / "gallery_1.bmp").write_bytes(b"NEW")  # only an external tool can put this here

    with pytest.raises(ValueError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")

    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR).read_bytes() == b"OLD"
    assert (d / "gallery_1.bmp").read_bytes() == b"NEW"


def _stranded(tmp_path, ext="png", data=b"STRANDED"):
    """The wreckage a pre-#253 promotion left when it crashed mid-swap."""
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"promote-tmp.{ext}").write_bytes(data)
    return d


def test_a_stranded_pre_fix_temp_is_adopted_as_the_avatar(tmp_path):
    """The damage the old swap could leave: the promoted image on disk under
    `promote-tmp`, no avatar, and nothing in the app looking for it -- image_path
    globs `<name>.*` and the editor renders only avatar/gallery_N, so the user's
    only recovery was to re-upload (#253). The directory scan adopts it."""
    d = _stranded(tmp_path)
    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p is not None and p.read_bytes() == b"STRANDED"
    assert p.name == "avatar.png"
    assert not list(d.glob("promote-tmp.*"))
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("avatar", "png")]


def test_a_stranded_temp_beside_a_live_avatar_becomes_a_gallery_image(tmp_path):
    """Crashing before the avatar moved leaves a working avatar plus a temp
    whose original slot is unrecoverable. It must not displace the avatar; a
    free gallery slot makes it visible again and overwrites nothing."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"LIVE", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"KEEP", "png")
    _stranded(tmp_path, ext="webp")

    assert _named(assets.list_images(tmp_path, "sera", "default")) == [
        ("avatar", "png"), ("gallery_1", "png"), ("gallery_2", "webp")]
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR).read_bytes() == b"LIVE"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1").read_bytes() == b"KEEP"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_2").read_bytes() == b"STRANDED"


def test_the_newest_stranded_temp_is_the_one_that_becomes_the_avatar(tmp_path):
    """Two interrupted promotions leave two temps. The freshest is the one the
    user was promoting, so it gets the avatar slot -- picking by sorted name
    would hand it to whichever extension sorts first (#253 says image_path
    prefers the newest mtime, so recovery has a sensible target)."""
    import os

    # .png sorts first but is the OLDER file, so name order and mtime order
    # disagree -- which is the whole point of the assertion below.
    d = _stranded(tmp_path, ext="png", data=b"OLDER")
    (d / "promote-tmp.webp").write_bytes(b"NEWER")
    older = d / "promote-tmp.png"
    os.utime(older, ns=(older.stat().st_atime_ns,
                        (d / "promote-tmp.webp").stat().st_mtime_ns - 10_000_000_000))

    assert _named(assets.list_images(tmp_path, "sera", "default")) == [
        ("avatar", "webp"), ("gallery_1", "png")]
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR).read_bytes() == b"NEWER"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1").read_bytes() == b"OLDER"


def test_recovery_re_decides_when_its_chosen_slot_is_taken(tmp_path, monkeypatch):
    """The slot is chosen before the lock is held, so an upload can take it in
    between. Recovery must pick another slot, not skip the temp (leaving it
    stranded) and not rename onto the upload's file -- POSIX rename replaces
    silently, so that would eat the upload."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"LIVE", "png")
    d = _stranded(tmp_path)
    real_free, raced = assets._free_gallery, []

    def racing(dd):
        slot = real_free(dd)
        if not raced:  # exactly once: an upload publishes the slot we just chose
            raced.append(slot)
            (dd / f"{slot}.png").write_bytes(b"UPLOADED")
        return slot

    monkeypatch.setattr(assets, "_free_gallery", racing)
    assets.list_images(tmp_path, "sera", "default")
    monkeypatch.undo()

    assert (d / f"{raced[0]}.png").read_bytes() == b"UPLOADED"  # not clobbered
    assert not list(d.glob("promote-tmp.*")), "the temp was left stranded"
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR).read_bytes() == b"LIVE"
    recovered = [p for p in _image_files(tmp_path) if p.read_bytes() == b"STRANDED"]
    assert len(recovered) == 1 and recovered[0].stem.startswith("gallery_")


def test_recovery_repairs_every_temp_even_on_a_tight_retry_budget(tmp_path, monkeypatch):
    """The budget bounds lost races, not work done. Budgeting total passes
    instead left a directory holding more temps than the budget with one still
    stranded after an uncontended scan (PR review)."""
    d = _stranded(tmp_path, ext="png", data=b"one")
    for ext, data in (("webp", b"two"), ("jpg", b"three"), ("gif", b"four")):
        (d / f"promote-tmp.{ext}").write_bytes(data)
    monkeypatch.setattr(assets, "_HEAL_RETRIES", 1)  # no room for a single retry

    assets.list_images(tmp_path, "sera", "default")

    assert not list(d.glob("promote-tmp.*")), "a temp was left behind"
    assert sorted(p.read_bytes() for p in _image_files(tmp_path)) == [
        b"four", b"one", b"three", b"two"]


def test_recovery_skips_a_gallery_slot_a_case_variant_already_holds(tmp_path):
    """`Gallery_1.png` and `gallery_1.png` are one file on Windows and macOS, so
    a case-sensitive occupancy check would keep choosing a slot that cannot be
    claimed -- recovery would find its target there every pass and give up
    without repairing anything."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"LIVE", "png")
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    (d / "Gallery_1.png").write_bytes(b"MIXED-CASE")
    (d / "promote-tmp.png").write_bytes(b"STRANDED")

    assets.list_images(tmp_path, "sera", "default")

    assert not list(d.glob("promote-tmp.*")), "the temp was left stranded"
    assert (d / "Gallery_1.png").read_bytes() == b"MIXED-CASE"  # untouched
    recovered = [p for p in _image_files(tmp_path) if p.read_bytes() == b"STRANDED"]
    assert len(recovered) == 1
    assert recovered[0].stem.casefold() not in ("gallery_1", "avatar")


def test_a_failed_recovery_never_breaks_the_read(tmp_path, monkeypatch):
    """The heal is cleanup on a read path: a read-only store or a sync client
    holding the file must degrade to "no avatar yet", not raise at the caller."""
    _stranded(tmp_path)

    def no_rename(self, target):
        raise OSError("read-only store")

    monkeypatch.setattr(assets.Path, "rename", no_rename)
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is None
    assert _named(assets.list_images(tmp_path, "sera", "default")) == [("promote-tmp", "png")]


def test_recovery_leaves_a_normal_directory_alone(tmp_path):
    """No temp, no writes: the scan must not touch a healthy directory."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    d = tmp_path / "characters" / "sera" / "assets" / "default"
    before = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
    assert assets.image_path(tmp_path, "sera", "default", "gallery_9") is None  # a miss
    assets.list_images(tmp_path, "sera", "default")
    assert {p.name: p.stat().st_mtime_ns for p in d.iterdir()} == before


def test_concurrent_promotions_neither_collide_nor_lose_an_image(tmp_path):
    """The old temp name was the fixed string `promote-tmp<ext>`, so two
    promotions in one process fought over one path. Whatever order they land
    in, promotion only ever permutes the images: all three survive, one per
    slot."""
    import threading

    payloads = {assets.AVATAR: b"A", "gallery_1": b"B", "gallery_2": b"C"}
    for name, data in payloads.items():
        assets.put_image(tmp_path, "sera", "default", name, data, "png")

    start = threading.Barrier(2)
    errors = []

    def promote(name):
        try:
            start.wait(timeout=5)
            assets.promote_image(tmp_path, "sera", "default", name)
        except Exception as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=promote, args=(n,))
               for n in ("gallery_1", "gallery_2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent promotion raised: {errors}"
    assert not any(t.is_alive() for t in threads), "a promotion deadlocked"
    names = [i["name"] for i in assets.list_images(tmp_path, "sera", "default")]
    assert names == ["avatar", "gallery_1", "gallery_2"]
    assert sorted(assets.image_path(tmp_path, "sera", "default", n).read_bytes()
                  for n in names) == [b"A", b"B", b"C"]


def test_a_promotion_racing_an_upload_does_not_interleave(tmp_path):
    """Promotion takes the same per-image lock as put_image/delete_image, on
    both slots it touches, so an upload cannot land in the middle of a swap."""
    import threading

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"OLD", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"NEW", "png")
    start = threading.Barrier(2)
    errors = []

    def promoter():
        try:
            start.wait(timeout=5)
            for _ in range(20):
                assets.promote_image(tmp_path, "sera", "default", "gallery_1")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def uploader():
        try:
            start.wait(timeout=5)
            for _ in range(20):
                assets.put_image(tmp_path, "sera", "default", assets.AVATAR,
                                 b"UPLOADED", "jpg")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=promoter), threading.Thread(target=uploader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent operation raised: {errors}"
    assert not any(t.is_alive() for t in threads), "an operation deadlocked"
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is not None
    for name in (assets.AVATAR, "gallery_1"):
        assert len([p for p in _image_files(tmp_path) if p.stem == name]) <= 1


def test_a_delete_racing_an_upload_does_not_interleave(tmp_path):
    """delete_image shares put_image's lock, so it removes the whole set or
    none of it -- never half of a set an upload is mid-way through replacing."""
    import threading

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"ORIGINAL", "png")
    start = threading.Barrier(2)
    errors = []

    def uploader():
        try:
            start.wait(timeout=5)
            for _ in range(20):
                assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"NEW", "jpg")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def deleter():
        try:
            start.wait(timeout=5)
            for _ in range(20):
                assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=uploader), threading.Thread(target=deleter)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent operation raised: {errors}"
    assert not any(t.is_alive() for t in threads), "an operation deadlocked"
    # Either outcome is legal; a half-state (two extensions) is not.
    assert len(assets.list_images(tmp_path, "sera", "default")) <= 1


def test_path_in_returns_newest_and_puts_round_trip(tmp_path):
    d = tmp_path / "assets"
    assert assets.path_in(d, "cover") is None          # directory absent
    assert assets.put_in(d, "cover", b"one", "png") == "png"
    p = assets.path_in(d, "cover")
    assert p is not None and p.suffix == ".png" and p.read_bytes() == b"one"
    assert image_store.blob_sha_of(p) is not None


def test_put_in_replaces_across_extensions(tmp_path):
    d = tmp_path / "assets"
    assets.put_in(d, "cover", b"one", "png")
    assets.put_in(d, "cover", b"two", "jpg")
    assert [(i["name"], i["ext"]) for i in assets.list_in(d)] == [("cover", "jpg")]
    assert assets.path_in(d, "cover").read_bytes() == b"two"


def test_put_in_rejects_unsupported_ext_and_unsafe_name(tmp_path):
    d = tmp_path / "assets"
    with pytest.raises(ValueError):
        assets.put_in(d, "cover", b"x", "svg")
    with pytest.raises(ValueError):
        assets.put_in(d, "../cover", b"x", "png")


def test_supported_only_ignores_and_spares_a_foreign_sibling(tmp_path):
    """A store directory is one a human browses and a sync client writes into:
    a `cover.txt` must neither become the cover nor be deleted by us."""
    d = tmp_path / "assets"
    d.mkdir()
    (d / "cover.png").write_bytes(b"png")    # legacy: a placement is never globbed
    (d / "cover.txt").write_text("sync conflict note", encoding="utf-8")
    os.utime(d / "cover.txt", (2 ** 31, 2 ** 31))  # newest by mtime

    assert assets.path_in(d, "cover", supported_only=True).name == "cover.png"
    assert assets.path_in(d, "cover").name == "cover.txt"  # legacy behaviour kept

    assets.put_in(d, "cover", b"jpg", "jpg", supported_only=True)
    assert (d / "cover.txt").exists()
    assert not (d / "cover.png").exists()       # the supported sibling is replaced

    assets.delete_in(d, "cover", supported_only=True)
    assert assets.path_in(d, "cover", supported_only=True) is None
    assert (d / "cover.txt").exists()


def test_delete_in_is_a_noop_without_the_directory(tmp_path):
    assets.delete_in(tmp_path / "nope", "cover")  # no error


def test_delete_version_images_drops_the_folder_and_its_sidecar(tmp_path):
    """What `characters.delete_version` / `pcs.delete_version` call once the
    version file is gone: nothing can address the folder afterwards, so
    leaving it is how a version delete manufactures orphaned bytes (#360)."""
    assets.put_image(tmp_path, "sera", "older", assets.AVATAR, b"a", "png")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "png")
    assets.write_focus(tmp_path, "sera", "older", 30)

    assets.delete_version_images(tmp_path, "sera", "older")
    assert not (tmp_path / "characters" / "sera" / "assets" / "older").exists()
    assert assets.read_focus(tmp_path, "sera", "older") is None
    # the sibling version and the record itself are untouched
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"b"

    assets.delete_version_images(tmp_path, "sera", "older")     # gone already: no error
    assets.delete_version_images(tmp_path, "sera", "../escape")  # unsafe id: refused
    assert (tmp_path / "characters" / "sera").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks on Windows needs elevation")
def test_a_symlinked_asset_folder_loses_the_link_not_the_library(tmp_path):
    """`shutil.rmtree` refuses a symlink outright, and this runs *after* the
    version file is unlinked -- so an unguarded rmtree would 500 the delete
    with the record already gone. A store on a synced folder is exactly where
    someone points an asset directory somewhere else."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "avatar.png").write_bytes(b"a")
    d = tmp_path / "characters" / "sera" / "assets" / "older"
    d.parent.mkdir(parents=True)
    d.symlink_to(library, target_is_directory=True)
    assert assets.image_path(tmp_path, "sera", "older", "avatar") is not None

    assets.delete_version_images(tmp_path, "sera", "older")
    assert not d.exists() and not d.is_symlink()
    assert (library / "avatar.png").read_bytes() == b"a"   # the target is somebody else's


# ---- placements: images live in the content-addressed store ---------------

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def test_put_writes_ref_not_file(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", _png(), "png")
    d = _vdir(tmp_path)
    assert not list(d.rglob("*.png"))
    assert (d / "image-refs" / "avatar.json").is_file()
    p = assets.image_path(tmp_path, "sera", "default", "avatar")
    assert p is not None and p.is_relative_to(image_store.store_root())
    assert image_store.blob_sha_of(p) is not None


def test_same_bytes_two_slots_one_blob(tmp_path):
    data = _png(3)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, data, "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", data, "png")
    blobs = [p for p in (image_store.store_root() / "blobs").rglob("*") if p.is_file()]
    assert len(blobs) == 1
    rows = assets.list_images(tmp_path, "sera", "default")
    assert [r["name"] for r in rows] == ["avatar", "gallery_1"]
    assert rows[0]["image_id"] == rows[1]["image_id"]
    assert (assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
            == assets.image_path(tmp_path, "sera", "default", "gallery_1"))


def test_delete_one_placement_keeps_other_and_store(tmp_path):
    data = _png(4)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, data, "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", data, "png")
    blob = assets.image_path(tmp_path, "sera", "default", "gallery_1")
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is None
    assert not (_vdir(tmp_path) / "image-refs" / "avatar.json").exists()
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1") == blob
    assert blob.read_bytes()            # the store keeps the blob (GC is stage 4)
    assert [r["name"] for r in assets.list_images(tmp_path, "sera", "default")] == ["gallery_1"]


def test_ref_wins_over_legacy_and_unresolved_ref_falls_back(tmp_path):
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    legacy = d / "avatar.png"
    legacy.write_bytes(b"LEGACY")
    obj = image_store.ingest(_png(5), "png")
    image_refs.write(d, assets.AVATAR, obj.id)

    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert p == image_store.blob_path(obj.blob_sha256, obj.ext)
    [row] = assets.list_images(tmp_path, "sera", "default")
    assert row["image_id"] == obj.id and row["v"] == obj.blob_sha256

    p.unlink()                         # the blob has not synced in yet
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) == legacy
    [row] = assets.list_images(tmp_path, "sera", "default")
    assert "image_id" not in row and row["ext"] == "png"

    legacy.unlink()
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is None
    assert assets.list_images(tmp_path, "sera", "default") == []


def test_list_row_shapes(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(6), "png")
    (_vdir(tmp_path) / "gallery_1.webp").write_bytes(b"LEGACY")
    ref_row, legacy_row = assets.list_images(tmp_path, "sera", "default")
    assert set(ref_row) == {"name", "ext", "v", "image_id"}
    assert _SHA.match(ref_row["v"]) and ref_row["ext"] == "png"
    assert set(legacy_row) == {"name", "ext", "v"}
    assert legacy_row["ext"] == "webp"
    assert re.fullmatch(r"[0-9a-f]+-[0-9a-f]+", legacy_row["v"])
    assert assets.list_in(_vdir(tmp_path)) == [ref_row, legacy_row]


def test_names_in_and_free_gallery_see_refs(tmp_path):
    for i in (1, 2):
        assets.put_image(tmp_path, "sera", "default", f"gallery_{i}", _png(10 + i), "png")
    d = _vdir(tmp_path)
    assert not [p for p in d.iterdir() if p.is_file()]      # refs only
    assert assets.names_in(d) == ({"gallery_1", "gallery_2"}, False)
    assert assets._free_gallery(d) == "gallery_3"
    # an image-less override is not an image
    assets.write_focus(tmp_path, "sera", "default", 20)
    assert assets.names_in(d)[0] == {"gallery_1", "gallery_2"}


def test_version_art_uncacheable_while_ref_unresolved(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(7), "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(8), "png")
    images, _focus, stamps = assets.version_art(tmp_path, "sera", "default")
    assert stamps is not None
    refs = os.fspath(_vdir(tmp_path) / "image-refs")
    assert any(s[0] == refs for s in stamps)
    assert any(s[0] == os.path.join(refs, "avatar.json") for s in stamps)
    avatar = next(i for i in images if i["name"] == assets.AVATAR)
    assert avatar["v"] == assets.image_version(
        assets.image_path(tmp_path, "sera", "default", assets.AVATAR))

    blob = assets.image_path(tmp_path, "sera", "default", "gallery_1")
    data = blob.read_bytes()
    blob.unlink()
    images, _focus, stamps = assets.version_art(tmp_path, "sera", "default")
    assert stamps is None
    assert [i["name"] for i in images] == [assets.AVATAR]

    blob.write_bytes(data)
    images, _focus, stamps = assets.version_art(tmp_path, "sera", "default")
    assert stamps is not None
    assert [i["name"] for i in images] == [assets.AVATAR, "gallery_1"]


def _cached_art(tmp_path, pool):
    """`version_art` memoized as a listing row memoizes it."""
    return statcache.memo_stamped(
        "art", lambda: (lambda r: (r[0], r[2]))(
            assets.version_art(tmp_path, "sera", "default")),
        pool=pool, max_entries=4)


def _age(root):
    """Back-date everything under `root` past the racy window, so a memoized
    row is actually stored."""
    old = time.time_ns() - 3 * statcache.RACY_WINDOW_NS
    for p in [root, *root.rglob("*")]:
        os.utime(p, ns=(old, old))


def _avatar_v(images):
    return next(i["v"] for i in images if i["name"] == assets.AVATAR)


def test_version_art_stamps_the_avatars_object_and_blob(tmp_path):
    data = _png(7)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, data, "png")
    image_id = image_refs.resolve(_vdir(tmp_path), assets.AVATAR).image_id
    _age(tmp_path)
    pool: dict = {}
    first = _cached_art(tmp_path, pool)
    assert _avatar_v(first) == hashlib.sha256(data).hexdigest()
    assert _cached_art(tmp_path, pool) is first             # cached

    # Adoption: the object now retains another encoding of the same pixels.
    # Nothing in the version folder moved, only the store's sidecar.
    other = _png_level(7, 1)
    sha = hashlib.sha256(other).hexdigest()
    assert sha != _avatar_v(first)
    blob = image_store.blob_path(sha, "png")
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(other)

    def adopt(raw):
        raw["blob"] = {**raw["blob"], "sha256": sha, "size": len(other)}
        return raw

    image_store.update(image_id, adopt)
    _age(image_store.store_root())          # the store's files only, not the folder's
    again = _cached_art(tmp_path, pool)
    assert _avatar_v(again) == sha
    assert _cached_art(tmp_path, pool) is again

    # The retained blob vanishing is noticed too (the row falls back to nothing).
    blob.unlink()
    assert [i["name"] for i in _cached_art(tmp_path, pool)] == []


def test_image_version_of_a_blob_is_its_sha(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(9), "png")
    p = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    assert assets.image_version(p) == image_store.blob_sha_of(p)


def test_focus_lives_on_ref(tmp_path):
    d = _vdir(tmp_path)
    a, b = _png(20), _png(21)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, a, "png")
    assets.write_focus(tmp_path, "sera", "default", 43)
    assert not (d / assets.FOCUS_FILE).exists()
    assert image_refs.read(d, assets.AVATAR).focus == 43
    assert assets.read_focus(tmp_path, "sera", "default") == 43

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, a, "png")   # same bytes
    assert assets.read_focus(tmp_path, "sera", "default") == 43

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b, "png")   # new picture
    assert assets.read_focus(tmp_path, "sera", "default") is None
    assert image_refs.read(d, assets.AVATAR).focus is None

    assets.write_focus(tmp_path, "sera", "default", 12)
    assets.clear_focus(tmp_path, "sera", "default")
    ref = image_refs.read(d, assets.AVATAR)
    assert ref.focus is None and ref.image is not None     # clearing keeps the image


def test_focus_override_without_avatar(tmp_path):
    d = _vdir(tmp_path)
    assets.write_focus(tmp_path, "sera", "default", 55)
    ref = image_refs.read(d, assets.AVATAR)
    assert ref is not None and ref.image is None and ref.focus == 55
    assert not (d / assets.FOCUS_FILE).exists()
    assert assets.read_focus(tmp_path, "sera", "default") == 55
    assert assets.list_images(tmp_path, "sera", "default") == []
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is None

    assets.clear_focus(tmp_path, "sera", "default")       # an image-less ref goes entirely
    assert not image_refs.ref_path(d, assets.AVATAR).exists()
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_focus_beside_a_legacy_avatar_stays_in_focus_json(tmp_path):
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    (d / "avatar.png").write_bytes(b"LEGACY")
    assets.write_focus(tmp_path, "sera", "default", 61)
    assert (d / assets.FOCUS_FILE).exists()
    assert not image_refs.ref_path(d, assets.AVATAR).exists()
    assert assets.read_focus(tmp_path, "sera", "default") == 61
    assets.clear_focus(tmp_path, "sera", "default")
    assert not (d / assets.FOCUS_FILE).exists()


def test_unsniffable_bytes_round_trip(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"\x89PNG", "png")
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR).read_bytes() == b"\x89PNG"


def test_put_in_passes_the_source_url_through(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(22), "png",
                     source_url="https://example.invalid/seraphine.png")
    iid = assets.image_id(tmp_path, "sera", "default", assets.AVATAR)
    assert iid is not None
    assert "example.invalid" in str(image_store.read(iid).raw.get("sources"))


def test_put_in_returns_the_blob_ext(tmp_path):
    # the bytes sniff as PNG whatever the caller called them
    assert assets.put_in(tmp_path / "lib", "map", _png(23), "jpg") == "png"


def test_heal_slot_choice_with_ref_avatar(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(30), "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(31), "png")
    avatar = assets.image_path(tmp_path, "sera", "default", assets.AVATAR)
    d = _vdir(tmp_path)
    (d / "promote-tmp.png").write_bytes(b"STRANDED")

    rows = assets.list_images(tmp_path, "sera", "default")
    assert [r["name"] for r in rows] == ["avatar", "gallery_1", "gallery_2"]
    assert (d / "gallery_2.png").read_bytes() == b"STRANDED"
    assert not (d / "promote-tmp.png").exists()
    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) == avatar


def test_link_in_and_resolve(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "map.webp").write_bytes(b"LEGACY")
    obj = image_store.ingest(_png(40), "png")
    image_refs.write(d, "map", obj.id, focus=10)
    assets.link_in(d, "map", obj.id, keep_focus=True)
    assert not (d / "map.webp").exists()
    r = assets.resolve(d, "map")
    assert r is not None and r.image_id == obj.id and r.focus == 10
    other = image_store.ingest(_png(41), "png")
    assets.link_in(d, "map", other.id)
    assert image_refs.read(d, "map") == image_refs.Ref("map", other.id, None)
    with pytest.raises(ValueError):
        assets.link_in(d, "../map", other.id)
    with pytest.raises(ValueError):
        assets.link_in(d, "map", "px1-not-an-id")
    assert assets.resolve(d, "nothing") is None


def test_adopt_legacy(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    data = _png(50)
    (d / "map.png").write_bytes(data)
    iid = assets.adopt_legacy(d, "map")
    assert iid is not None and not (d / "map.png").exists()
    assert image_refs.read(d, "map").image == iid
    assert assets.path_in(d, "map").read_bytes() == data
    assert assets.adopt_legacy(d, "map") == iid          # already ref-backed
    assert assets.adopt_legacy(d, "missing") is None


def test_adopting_a_legacy_avatar_carries_its_focus(tmp_path):
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    (d / "avatar.png").write_bytes(_png(51))
    assets.write_focus(tmp_path, "sera", "default", 33)            # legacy focus.json
    assert assets.adopt_legacy(d, assets.AVATAR) is not None
    assert assets.read_focus(tmp_path, "sera", "default") == 33
    assert not (d / assets.FOCUS_FILE).exists()


def test_image_id(tmp_path):
    assert assets.image_id(tmp_path, "sera", "default", assets.AVATAR) is None
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(60), "png")
    [row] = assets.list_images(tmp_path, "sera", "default")
    assert assets.image_id(tmp_path, "sera", "default", assets.AVATAR) == row["image_id"]
    assert assets.image_id(tmp_path, "..", "default", assets.AVATAR) is None


def test_campaign_focus_override_list_and_detail_agree(tmp_path):
    from grimoire.store import campaigns, characters, overlay, worlds

    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    chid, vid = characters.create_character(wroot, "Seraphine")
    assets.put_image(wroot, chid, vid, assets.AVATAR, _png(70), "png")
    assets.write_focus(wroot, chid, vid, 10)
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)

    def row():
        return next(r for r in overlay.list_characters(cid) if r["id"] == chid)

    assert overlay.read_focus(cid, chid, vid) == 10 == row()["avatar_focus"]

    assets.write_focus(croot, chid, vid, 40)       # the campaign's override, inherited art
    ref = image_refs.read(_vdir(croot, chid, vid), assets.AVATAR)
    assert ref is not None and ref.image is None
    assert overlay.read_focus(cid, chid, vid) == 40
    assert row()["avatar_focus"] == 40
    facts = characters.version_facts(croot, chid, vid, crop=True)
    assert facts["focus_file"] is True and facts["focus"] == 40
    # the art is still the world's
    assert row()["has_avatar"] is True
    assert overlay.list_images(cid, chid, vid)[0]["image_id"] == assets.image_id(
        wroot, chid, vid, assets.AVATAR)

    # a later world crop does not reach past the campaign's override
    assets.write_focus(wroot, chid, vid, 90)
    assert overlay.read_focus(cid, chid, vid) == 40 == row()["avatar_focus"]


def test_link_in_keeps_legacy_files_when_the_new_ref_does_not_resolve(tmp_path):
    """Redundant data beats lost data (spec section 6): a placement whose
    object has not arrived must not cost the name the bytes it still has."""
    d = tmp_path / "lib"
    d.mkdir()
    legacy = d / "gallery_1.png"
    legacy.write_bytes(b"LEGACY")
    obj = image_store.ingest(_png(95), "png")
    image_store.object_path(obj.id).unlink()           # the object has not synced in yet
    assets.link_in(d, "gallery_1", obj.id)
    assert image_refs.read(d, "gallery_1").image == obj.id
    assert legacy.read_bytes() == b"LEGACY"
    assert assets.path_in(d, "gallery_1") == legacy


def test_heal_treats_an_unresolved_avatar_ref_as_present(tmp_path):
    """An avatar placement whose object has not synced in is still the avatar:
    a healed stray must not take the slot it will resolve into."""
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    obj = image_store.ingest(_png(96), "png")
    image_store.object_path(obj.id).unlink()
    image_refs.write(d, assets.AVATAR, obj.id)
    (d / "promote-tmp.png").write_bytes(b"STRANDED")

    assert assets.image_path(tmp_path, "sera", "default", assets.AVATAR) is None
    assert (d / "gallery_1.png").read_bytes() == b"STRANDED"
    assert not (d / "avatar.png").exists()
    assert image_refs.read(d, assets.AVATAR).image == obj.id


# ---- promotion: a journalled ref swap ---------------------------------------

def _blobs():
    root = image_store.store_root() / "blobs"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _slot_ids(tmp_path, *names):
    d = _vdir(tmp_path)
    return tuple(getattr(image_refs.read(d, n), "image", None) for n in names)


def _write_descriptions(tmp_path, mapping):
    (_vdir(tmp_path) / assets.DESCRIPTIONS_FILE).write_text(json.dumps(mapping), encoding="utf-8")


def _descriptions(tmp_path):
    p = _vdir(tmp_path) / assets.DESCRIPTIONS_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _two_slots(tmp_path):
    """An avatar and gallery_1, each described, the avatar cropped; their ids."""
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(80), "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(81), "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    _write_descriptions(tmp_path, {"avatar": "Seraphine at the gate",
                                   "gallery_1": "Seraphine on the stair"})
    return _slot_ids(tmp_path, assets.AVATAR, "gallery_1")


def _assert_swapped(tmp_path, a, g):
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (g, a)
    assert _descriptions(tmp_path) == {"avatar": "Seraphine on the stair",
                                       "gallery_1": "Seraphine at the gate"}
    assert image_refs.read_journal(_vdir(tmp_path)) is None


def _no_ingest(monkeypatch):
    """A reference operation never reads bytes back into the store."""
    def ingest(*args, **kwargs):
        raise AssertionError("bytes were re-ingested")
    monkeypatch.setattr(image_store, "ingest", ingest)


def test_promote_swaps_refs_without_new_blobs(tmp_path, monkeypatch):
    a, g = _two_slots(tmp_path)
    before = _blobs()
    _no_ingest(monkeypatch)
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assert _blobs() == before
    _assert_swapped(tmp_path, a, g)
    assert assets.read_focus(tmp_path, "sera", "default") is None
    assert image_refs.read(_vdir(tmp_path), assets.AVATAR).focus is None


def test_promote_without_avatar_deletes_source_slot(tmp_path, monkeypatch):
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(82), "png")
    _write_descriptions(tmp_path, {"gallery_1": "Mara by the window"})
    [g] = _slot_ids(tmp_path, "gallery_1")
    _no_ingest(monkeypatch)
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    d = _vdir(tmp_path)
    assert _slot_ids(tmp_path, assets.AVATAR) == (g,)
    assert image_refs.read(d, "gallery_1") is None
    assert assets.path_in(d, "gallery_1") is None
    assert _descriptions(tmp_path) == {"avatar": "Mara by the window"}
    assert [r["name"] for r in assets.list_images(tmp_path, "sera", "default")] == ["avatar"]
    assert image_refs.read_journal(d) is None


def test_promote_legacy_slots_adopted(tmp_path):
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    (d / "avatar.png").write_bytes(_png(83))
    (d / "gallery_1.png").write_bytes(_png(84))
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert not [p for p in d.iterdir() if p.is_file() and p.suffix == ".png"]
    # ingesting the same pictures again names the objects the swap placed
    old, new = image_store.ingest(_png(83), "png").id, image_store.ingest(_png(84), "png").id
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (new, old)


def _crash_on_write(monkeypatch, nth):
    """Make the `nth` placement write after the journal lands raise."""
    real_write, real_journal = image_refs.write, image_refs.write_journal
    seen = [None]

    def write_journal(d, journal):
        real_journal(d, journal)
        seen[0] = 0

    def write(*args, **kwargs):
        if seen[0] is not None:
            seen[0] += 1
            if seen[0] == nth:
                raise OSError("crash")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(image_refs, "write_journal", write_journal)
    monkeypatch.setattr(image_refs, "write", write)


def test_promote_crash_after_journal_rolls_forward(tmp_path, monkeypatch):
    a, g = _two_slots(tmp_path)
    _crash_on_write(monkeypatch, 1)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)   # nothing moved yet
    assert image_refs.read_journal(_vdir(tmp_path)) is not None

    assets.list_images(tmp_path, "sera", "default")
    _assert_swapped(tmp_path, a, g)
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_promote_crash_after_avatar_write_rolls_forward(tmp_path, monkeypatch):
    a, g = _two_slots(tmp_path)
    _crash_on_write(monkeypatch, 2)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (g, g)   # mid-swap

    p = assets.image_path(tmp_path, "sera", "default", "gallery_1")
    assert p == image_refs.resolve(_vdir(tmp_path), "gallery_1").blob_path
    _assert_swapped(tmp_path, a, g)


def test_crash_after_description_write_does_not_double_swap(tmp_path, monkeypatch):
    a, g = _two_slots(tmp_path)

    def clear_journal(d):
        raise OSError("crash")

    monkeypatch.setattr(image_refs, "clear_journal", clear_journal)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assert image_refs.read_journal(_vdir(tmp_path)) is not None

    images, _focus, stamps = assets.version_art(tmp_path, "sera", "default")
    _assert_swapped(tmp_path, a, g)        # each picture keeps its own description
    assert stamps is not None
    assert {i["name"]: i["image_id"] for i in images} == {"avatar": g, "gallery_1": a}


def test_a_failed_journal_clear_is_recovered_without_a_crop(tmp_path, monkeypatch):
    _two_slots(tmp_path)                       # the avatar is cropped at 30

    def clear_journal(d):
        raise OSError("crash")

    monkeypatch.setattr(image_refs, "clear_journal", clear_journal)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assets.version_art(tmp_path, "sera", "default")      # recovery rolls forward
    d = _vdir(tmp_path)
    assert image_refs.read_journal(d) is None
    assert image_refs.read(d, assets.AVATAR).focus is None
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_the_crop_goes_before_the_journal(tmp_path, monkeypatch):
    # A crash just after the journal is cleared leaves nothing to finish the
    # job, so the crop -- here a legacy focus.json that synced in beside the
    # avatar placement -- must already be gone by then.
    a, g = _two_slots(tmp_path)
    d = _vdir(tmp_path)
    (d / assets.FOCUS_FILE).write_text(json.dumps({assets.AVATAR: 70}), encoding="utf-8")
    real = image_refs.clear_journal

    def clear_then_crash(d):
        real(d)
        raise OSError("crash")

    monkeypatch.setattr(image_refs, "clear_journal", clear_then_crash)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assets.version_art(tmp_path, "sera", "default")
    _assert_swapped(tmp_path, a, g)
    assert not (d / assets.FOCUS_FILE).exists()
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_stale_journal_discarded(tmp_path):
    a, g = _two_slots(tmp_path)
    x, y = image_store.ingest(_png(90), "png").id, image_store.ingest(_png(91), "png").id
    d = _vdir(tmp_path)
    image_refs.write_journal(d, {"name": "gallery_1",
                                 "pre": {"avatar": x, "gallery_1": y},
                                 "post": {"avatar": y, "gallery_1": x},
                                 "desc": {"avatar": "wrong", "gallery_1": "wrong"}})
    assets.list_images(tmp_path, "sera", "default")
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)
    assert _descriptions(tmp_path) == {"avatar": "Seraphine at the gate",
                                       "gallery_1": "Seraphine on the stair"}
    assert image_refs.read_journal(d) is None
    assert assets.read_focus(tmp_path, "sera", "default") == 30

    image_refs.write_journal(d, {"name": "../avatar", "pre": [], "post": None})
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1") is not None
    assert image_refs.read_journal(d) is None          # a malformed journal goes too
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)


def test_recovery_never_waits_on_a_busy_slot(tmp_path, monkeypatch):
    """A read that finds a journal while the slots are held (a promotion in
    flight, or a caller holding one slot's lock as `delete_image` does while it
    looks) leaves the journal for the next read rather than waiting."""
    import threading

    a, g = _two_slots(tmp_path)
    _crash_on_write(monkeypatch, 1)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()

    d = _vdir(tmp_path)
    held, release = threading.Event(), threading.Event()

    def holder():
        with assets._image_lock(d, assets.AVATAR):
            held.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(timeout=5)
        assets.list_images(tmp_path, "sera", "default")          # returns, does not wait
        assert image_refs.read_journal(d) is not None
        assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)
    finally:
        release.set()
        t.join(timeout=10)
    assets.list_images(tmp_path, "sera", "default")
    _assert_swapped(tmp_path, a, g)


def test_promote_never_overwrites_a_journal_it_could_not_finish(tmp_path, monkeypatch):
    """A half-done swap's journal is the only record of the picture it moved
    out of the avatar slot. A promotion of ANOTHER slot that cannot finish it
    first (its slot busy) must refuse rather than write its own over it."""
    import threading

    a, g = _two_slots(tmp_path)
    assets.put_image(tmp_path, "sera", "default", "gallery_2", _png(85), "png")
    _crash_on_write(monkeypatch, 2)          # avatar written, gallery_1 not
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    d = _vdir(tmp_path)
    journal = image_refs.read_journal(d)
    assert journal is not None and journal["pre"]["avatar"] == a

    held, release = threading.Event(), threading.Event()

    def holder():
        with assets._image_lock(d, "gallery_1"):
            held.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(timeout=5)
        with pytest.raises(OSError):
            assets.promote_image(tmp_path, "sera", "default", "gallery_2")
        assert image_refs.read_journal(d) == journal
    finally:
        release.set()
        t.join(timeout=10)
    assets.list_images(tmp_path, "sera", "default")
    _assert_swapped(tmp_path, a, g)


def test_a_journal_with_a_non_text_description_is_discarded(tmp_path):
    """Recovery writes the journal's sentences into the sidecar as they stand,
    so one that is not text marks the journal malformed rather than replayed."""
    a, g = _two_slots(tmp_path)
    d = _vdir(tmp_path)
    image_refs.write_journal(d, {"name": "gallery_1",
                                 "pre": {"avatar": a, "gallery_1": g},
                                 "post": {"avatar": g, "gallery_1": a},
                                 "desc": {"avatar": ["not", "text"], "gallery_1": None}})
    assets.list_images(tmp_path, "sera", "default")
    assert image_refs.read_journal(d) is None
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)
    assert _descriptions(tmp_path) == {"avatar": "Seraphine at the gate",
                                       "gallery_1": "Seraphine on the stair"}


def _unresolved_ref_beside_legacy(d, name, seed):
    """`name` holds an image-bearing placement whose object has not arrived,
    plus the legacy file `link_in` kept because of it (ruling 1)."""
    (d / f"{name}.png").write_bytes(_png(seed))
    obj = image_store.ingest(_png(seed + 1), "png")
    image_store.object_path(obj.id).unlink()
    assets.link_in(d, name, obj.id)
    assert image_refs.read(d, name).image == obj.id and image_refs.resolve(d, name) is None
    return obj.id


def test_promote_refuses_a_slot_whose_image_has_not_arrived(tmp_path):
    """Promoting an unresolved placement with no avatar used to journal a swap
    that could never finish -- the legacy file beside it kept the source slot
    occupied -- and that journal then refused every later promotion here."""
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    pending = _unresolved_ref_beside_legacy(d, "gallery_1", 100)
    assets.put_image(tmp_path, "sera", "default", "gallery_2", _png(102), "png")
    [g2] = _slot_ids(tmp_path, "gallery_2")

    with pytest.raises(assets.ImageNotYetAvailableError, match="not yet available"):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert image_refs.read_journal(d) is None
    assert image_refs.read(d, assets.AVATAR) is None                # nothing written
    assert image_refs.read(d, "gallery_1").image == pending
    assert (d / "gallery_1.png").exists()

    assets.promote_image(tmp_path, "sera", "default", "gallery_2")
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_2") == (g2, None)
    assert image_refs.read_journal(d) is None


def test_promote_refuses_an_avatar_whose_image_has_not_arrived(tmp_path):
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    pending = _unresolved_ref_beside_legacy(d, assets.AVATAR, 103)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(105), "png")
    [g] = _slot_ids(tmp_path, "gallery_1")
    with pytest.raises(OSError, match="not yet available"):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert image_refs.read_journal(d) is None
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (pending, g)


def test_promote_an_unresolved_slot_with_no_legacy_file_is_not_yet_available(tmp_path):
    """The same refusal as an unresolved slot beside a legacy file: the image
    is placed and has not arrived, which is not "no image here"."""
    d = _vdir(tmp_path)
    d.mkdir(parents=True)
    obj = image_store.ingest(_png(106), "png")
    image_store.object_path(obj.id).unlink()
    assets.link_in(d, "gallery_1", obj.id)
    assert assets.path_in(d, "gallery_1") is None
    with pytest.raises(assets.ImageNotYetAvailableError, match="not yet available") as got:
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert not isinstance(got.value, FileNotFoundError)
    assert image_refs.read_journal(d) is None
    assert image_refs.read(d, "gallery_1").image == obj.id
    # An empty slot is still "no image".
    with pytest.raises(FileNotFoundError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_7")


def test_a_write_finishes_an_interrupted_promotion_first(tmp_path, monkeypatch):
    """A write landing before any read must not turn a half-done swap into a
    state recovery no longer recognises -- which discards the journal, and with
    it the old avatar's last placement."""
    a, g = _two_slots(tmp_path)
    _crash_on_write(monkeypatch, 2)          # avatar written, gallery_1 not
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (g, g)

    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(106), "png")
    new = image_store.ingest(_png(106), "png").id
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (new, a)
    assert image_refs.read_journal(_vdir(tmp_path)) is None


@pytest.mark.parametrize("writer", ["link_in", "delete_in", "delete_image", "write_focus"])
def test_every_writer_finishes_an_interrupted_promotion_first(tmp_path, monkeypatch, writer):
    a, _g = _two_slots(tmp_path)
    _crash_on_write(monkeypatch, 2)
    with pytest.raises(OSError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    monkeypatch.undo()
    d = _vdir(tmp_path)
    other = image_store.ingest(_png(107), "png").id
    {"link_in": lambda: assets.link_in(d, assets.AVATAR, other),
     "delete_in": lambda: assets.delete_in(d, assets.AVATAR),
     "delete_image": lambda: assets.delete_image(tmp_path, "sera", "default", assets.AVATAR),
     "write_focus": lambda: assets.write_focus(tmp_path, "sera", "default", 70)}[writer]()
    assert _slot_ids(tmp_path, "gallery_1") == (a,)          # rolled forward first
    assert image_refs.read_journal(d) is None


def test_a_journal_whose_post_is_not_the_swap_of_its_pre_is_discarded(tmp_path):
    a, g = _two_slots(tmp_path)
    x = image_store.ingest(_png(108), "png").id
    d = _vdir(tmp_path)
    for post in ({"avatar": x, "gallery_1": a},       # not the swap
                 {"avatar": g, "gallery_1": g}):
        image_refs.write_journal(d, {"name": "gallery_1",
                                     "pre": {"avatar": a, "gallery_1": g},
                                     "post": post,
                                     "desc": {"avatar": None, "gallery_1": None}})
        assets.list_images(tmp_path, "sera", "default")
        assert image_refs.read_journal(d) is None
        assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)
    image_refs.write_journal(d, {"name": "gallery_1",                # no source
                                 "pre": {"avatar": a, "gallery_1": None},
                                 "post": {"avatar": None, "gallery_1": a},
                                 "desc": {}})
    assets.list_images(tmp_path, "sera", "default")
    assert image_refs.read_journal(d) is None
    assert _slot_ids(tmp_path, assets.AVATAR, "gallery_1") == (a, g)
    assert _descriptions(tmp_path) == {"avatar": "Seraphine at the gate",
                                       "gallery_1": "Seraphine on the stair"}


# ---- copy_slots: tree copies per logical name -------------------------------

def _src_dst(tmp_path):
    src = _vdir(tmp_path, "sera")
    dst = _vdir(tmp_path, "mara")
    return src, dst


def test_copy_slots_copies_refs_as_refs_and_legacy_as_files(tmp_path):
    src, dst = _src_dst(tmp_path)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, _png(1), "png")
    assets.write_focus(tmp_path, "sera", "default", 20)
    (src / "gallery_1.png").write_bytes(_png(2))
    (src / assets.DESCRIPTIONS_FILE).write_text("{}", encoding="utf-8")
    image_refs.write_journal(src, {"name": "nonsense"})   # malformed: recovery drops it

    assets.copy_slots(src, dst)

    assert image_refs.read(dst, assets.AVATAR) == image_refs.read(src, assets.AVATAR)
    assert image_refs.read(dst, assets.AVATAR).focus == 20
    assert (dst / "gallery_1.png").read_bytes() == _png(2)
    assert image_refs.read(dst, "gallery_1") is None
    assert not (dst / assets.DESCRIPTIONS_FILE).exists()    # sidecars are the caller's
    assert image_refs.read_journal(dst) is None


def test_copy_slots_skips_tombstoned_names_and_held_slots(tmp_path):
    src, dst = _src_dst(tmp_path)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(1), "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_2", _png(2), "png")
    assets.put_image(tmp_path, "mara", "default", "gallery_1", _png(3), "png")

    assets.copy_slots(src, dst, skip=lambda n: n == "gallery_2")

    assert assets.image_path(tmp_path, "mara", "default", "gallery_1").read_bytes() == _png(3)
    assert image_refs.read(dst, "gallery_2") is None


def test_copy_slots_overwrite_replaces_a_legacy_slot_with_the_ref(tmp_path):
    src, dst = _src_dst(tmp_path)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", _png(1), "png")
    dst.mkdir(parents=True)
    (dst / "gallery_1.jpg").write_bytes(b"old legacy bytes")

    assets.copy_slots(src, dst, overwrite=True)

    assert image_refs.read(dst, "gallery_1") == image_refs.read(src, "gallery_1")
    assert not (dst / "gallery_1.jpg").exists()
    assert assets.image_path(tmp_path, "mara", "default", "gallery_1").read_bytes() == _png(1)


def test_copy_slots_overwrite_with_an_override_replaces_only_the_crop(tmp_path):
    src, dst = _src_dst(tmp_path)
    assets.write_focus(tmp_path, "sera", "default", 60)       # image-less override
    assets.put_image(tmp_path, "mara", "default", assets.AVATAR, _png(4), "png")
    held = image_refs.read(dst, assets.AVATAR).image

    assets.copy_slots(src, dst, overwrite=True)

    assert image_refs.read(dst, assets.AVATAR) == image_refs.Ref(assets.AVATAR, held, 60)
