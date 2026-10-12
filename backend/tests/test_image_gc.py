"""Image garbage collection (stage 4, spec section 12 and rulings M12-M15).

`image_gc.scan` is the dry run: it walks every root strictly and -- when
something is collectable and nothing blocked the walk -- hands back a
single-use token.
`image_gc.collect(token)` disposes of at most what that scan reported,
intersected with a fresh strict walk, in batches under the ingest/GC lock: a
picture's blob is moved into this device's outbox under the lock and sent to
the recycle bin (the suite's `fake_bin`) after it; its sidecar, index entry and
thumbnails are deleted.

Everything here fails closed, so most tests are about what is NOT deleted.
Every image is drawn with Pillow from arithmetic; every name is invented. The
store is `tmp_path/home`, and `tmp_path/outside` holds files a collection must
never touch.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from grimoire.routes import common
from grimoire.store import (
    image_collections,
    image_gc,
    image_migration,
    image_refs,
    image_store,
    locks,
    maintenance_reports,
    proclock,
    recycle_bin,
    thumbs,
)
from grimoire.store.module_edit import staging as module_staging
from grimoire.store.worlds import staging as world_staging

DAY = 24 * 3600.0
T = 1_900_000_000.0          # a fixed "now" for the first scan of each test
OLD = T - 2 * 3600           # an mtime past the one-hour grace at T


def _never() -> bool:
    return False


@pytest.fixture(autouse=True)
def _machine(monkeypatch, tmp_path_factory):
    """This machine's lock directory (the device id lives there), outside the
    store and outside the developer's real one."""
    machine = tmp_path_factory.mktemp("machine-a")
    monkeypatch.setattr(proclock, "lock_dir", lambda: machine)
    return machine


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    home = home.resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.png").write_bytes(_png(99))
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    return home


# ---- helpers --------------------------------------------------------------

def _png(seed: int = 0, size=(16, 12)) -> bytes:
    im = Image.new("RGB", size)
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y + seed) % 256)
                for y in range(size[1]) for x in range(size[0])])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _obj(seed: int, *, mtime: float = OLD) -> image_store.ImageObject:
    """An ingested object whose sidecar and blob are `mtime` old."""
    obj = image_store.ingest(_png(seed), "png")
    _age(obj, mtime)
    return obj


def _age(obj: image_store.ImageObject, mtime: float) -> None:
    os.utime(image_store.object_path(obj.id), (mtime, mtime))
    os.utime(image_store.blob_path(obj.blob_sha256, obj.ext), (mtime, mtime))


def _sidecar(obj) -> Path:
    return image_store.object_path(obj.id)


def _blob(obj) -> Path:
    return image_store.blob_path(obj.blob_sha256, obj.ext)


def _char_dir(root: Path, world="realm", char="seraphine", vid="v1") -> Path:
    return root / "worlds" / world / "characters" / char / "assets" / vid


def _place(root: Path, obj, name="avatar", d: Path | None = None) -> Path:
    d = d or _char_dir(root)
    image_refs.write(d, name, obj.id)
    return d


def _scan(root, now=T, **kw) -> dict:
    return image_gc.scan(root, cancel=_never, now=now, **kw)


def _ready(root) -> dict:
    """One scan: whatever is unreachable and an hour old is collectable."""
    return _scan(root, now=T)


def _collect(root, token, now=T + 60, **kw) -> dict:
    return image_gc.collect(root, token, cancel=_never, now=now, **kw)


def _ids(report, key="collectable") -> set[str]:
    return {row["id"] for row in report[key]}


def _tree(base: Path, *, skip=()) -> dict[str, bytes]:
    out = {}
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        if any(rel == s or rel.startswith(s + "/") for s in skip):
            continue
        if p.is_file() and not p.is_symlink():
            out[rel] = p.read_bytes()
    return out


# ---- what is kept ---------------------------------------------------------

def test_referenced_and_multiply_referenced_images_are_kept(root):
    once, twice, orphan = _obj(1), _obj(2), _obj(3)
    _place(root, once)
    _place(root, twice, "gallery_1")
    _place(root, twice, "map", root / "campaigns" / "saltmarch" / "locations" / "harbor"
           / "assets" / "default")
    report = _scan(root, now=T)
    assert report["state"] == "complete"
    assert report["counts"]["placements"] == 3
    assert report["counts"]["objects"] == 3
    assert _ids(report) == {orphan.id}
    assert report["token"]

    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert {r["id"] for r in done["deleted"]["objects"]} == {orphan.id}
    for kept in (once, twice):
        assert _sidecar(kept).is_file() and _blob(kept).is_file()
    assert not _sidecar(orphan).exists() and not _blob(orphan).exists()


# ---- strict roots -----------------------------------------------------------

def test_a_sync_conflict_copy_of_a_placement_is_a_root(root):
    kept, other = _obj(1), _obj(2)
    d = _place(root, other)
    # The copy a sync client leaves beside the placement it could not merge:
    # nothing serves it, and its picture is still somebody's.
    # A dotted stem is no placement name, so `walk` and `scan` pass it by.
    conflict = d / image_refs.REFS_DIR / "avatar.sync-conflict-20261006-101500-ABCDEFG.json"
    conflict.write_text(json.dumps({"format": 1, "image": kept.id}) + "\n", encoding="utf-8")
    assert kept.id not in image_refs.walk_ids(root / "worlds")
    report = _ready(root)
    assert report["state"] == "complete"
    assert kept.id not in _ids(report)
    assert report["counts"]["unreferenced"] == 0


def test_promotion_journals_and_atomic_temps_are_roots(root):
    pre, post, temp_obj, junk_temp = _obj(1), _obj(2), _obj(3), _obj(4)
    d = _char_dir(root)
    image_refs.write_journal(d, {"name": "gallery_1",
                                 "pre": {"avatar": pre.id, "gallery_1": post.id},
                                 "post": {"avatar": post.id, "gallery_1": pre.id},
                                 "desc": {"avatar": None, "gallery_1": None}})
    refs = d / image_refs.REFS_DIR
    (refs / ".avatar.json.abcd1234.tmp").write_text(
        json.dumps({"format": 1, "image": temp_obj.id}), encoding="utf-8")
    (refs / ".gallery_2.json.efgh5678.tmp").write_text('{"format": 1, "ima', encoding="utf-8")
    report = _ready(root)
    assert report["state"] == "complete"
    # A half-written temp is ignored rather than fatal; a readable one counts.
    assert _ids(report) == {junk_temp.id}


@pytest.mark.parametrize("case", ["unreadable", "unknown-format", "symlink",
                                  "garbled", "bad-image"])
def test_an_unreadable_dir_an_unknown_format_and_a_symlink_abort(root, monkeypatch, tmp_path,
                                                                 case):
    orphan = _obj(1)
    placed = _obj(2)
    d = _place(root, placed)
    first = _ready(root)
    assert _ids(first) == {orphan.id}
    token = first["token"]

    if case == "unreadable":
        bad = root / "campaigns" / "saltmarch" / "scenes"
        bad.mkdir(parents=True)
        real = os.scandir

        def scandir(path="."):
            if Path(path) == bad:
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)

        monkeypatch.setattr(os, "scandir", scandir)
    elif case == "unknown-format":
        (d / image_refs.REFS_DIR / "gallery_1.json").write_text(
            json.dumps({"format": 2, "image": orphan.id}), encoding="utf-8")
    elif case == "garbled":
        (d / image_refs.REFS_DIR / "gallery_1.json").write_text("{not json", encoding="utf-8")
    elif case == "bad-image":
        (d / image_refs.REFS_DIR / "gallery_1.json").write_text(
            json.dumps({"format": 1, "image": "px1-nothex"}), encoding="utf-8")
    else:
        elsewhere = tmp_path / "outside" / "linked"
        elsewhere.mkdir()
        (root / "worlds" / "realm" / "locations").mkdir(parents=True)
        (root / "worlds" / "realm" / "locations" / "harbor").symlink_to(elsewhere)

    blocked = _scan(root, now=T + 30)
    assert blocked["state"] == "blocked"
    assert blocked["blocking"], "a blocked scan names what blocked it"
    assert blocked["token"] is None
    assert blocked["collectable"] == []

    # And the token an earlier, clean scan issued deletes nothing now.
    done = _collect(root, token, now=T + 90)
    assert done["state"] == "blocked"
    assert done["blocking"]
    assert done["deleted"]["objects"] == []
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()


def test_manifests_journals_staging_and_pending_map_entries_are_roots(root, monkeypatch):
    member, harvested, staged, pending, orphan = (_obj(i) for i in range(1, 6))
    manifests = root / "worlds" / "realm" / "assets" / "image-collections"
    manifests.mkdir(parents=True)
    (manifests / ("a" * 32 + ".json")).write_text(
        json.dumps({"format": 2, "members": [member.id]}), encoding="utf-8")
    # The journal of a world that is gone: read from the raw directory (M10).
    journals = image_collections.raw_journal_directory(root, "vanished")
    journals.mkdir(parents=True)
    (journals / ("b" * 64 + ".json")).write_text(json.dumps(
        {"format": 2, "job_id": "b" * 64, "members": [harvested.id], "accepted": False}),
        encoding="utf-8")
    work = root / ".world-staging" / ("c" * 32) / "world"
    image_refs.write(work / "characters" / "mara" / "assets" / "v1", "avatar", staged.id)
    for p in [work.parent, *work.parent.rglob("*")]:
        os.utime(p, (T - 2 * DAY, T - 2 * DAY))
    asked: list[Path] = []

    def pending_ids(r):
        asked.append(r)
        return {pending.id}

    monkeypatch.setattr(image_migration, "pending_ids", pending_ids)

    report = _scan(root, now=T)
    assert report["state"] == "complete", report["blocking"]
    assert _ids(report) == {orphan.id}
    assert asked and all(r == root for r in asked), "the map is read under the pinned root"


@pytest.mark.parametrize("what", ["manifest", "journal", "map"])
def test_an_unreadable_manifest_journal_or_map_aborts(root, monkeypatch, what):
    _obj(1)
    if what == "manifest":
        p = root / "worlds" / "realm" / "assets" / "image-collections" / ("a" * 32 + ".json")
        body = json.dumps({"format": 3, "members": []})
    elif what == "journal":
        p = image_collections.raw_journal_directory(root, "realm") / ("b" * 64 + ".json")
        body = "{half"
    else:
        p = image_migration.work_map_path(root)
        body = "[1, 2"

        def garbled(r):
            raise ValueError("the work map does not parse")

        monkeypatch.setattr(image_migration, "pending_ids", garbled)
    p.parent.mkdir(parents=True)
    p.write_text(body, encoding="utf-8")
    report = _scan(root, now=T)
    assert report["state"] == "blocked"
    assert report["token"] is None
    assert any(b["path"] == p.relative_to(root).as_posix() for b in report["blocking"])


def test_an_old_staging_dir_is_walked_and_a_young_one_aborts(root):
    staged, orphan = _obj(1), _obj(2)
    old = root / ".module-staging" / ("d" * 32)
    image_refs.write(old / "world" / "lore" / "rumor" / "assets" / "default", "map", staged.id)
    for p in [old, *old.rglob("*")]:
        os.utime(p, (T - DAY, T - DAY))
    report = _scan(root, now=T)
    assert report["state"] == "complete"
    assert _ids(report) == {orphan.id}

    young = root / ".world-staging" / ("e" * 32) / "world"
    young.mkdir(parents=True)
    os.utime(young, (T + 3000, T + 3000))
    blocked = _scan(root, now=T + 3600)
    assert blocked["state"] == "blocked"
    assert blocked["token"] is None
    assert any(b["reason"] == "staging-in-progress" for b in blocked["blocking"])
    done = _collect(root, report["token"], now=T + 3600)
    assert done["state"] == "blocked" and done["deleted"]["objects"] == []
    assert _sidecar(orphan).is_file()


# ---- grace and clock skew -------------------------------------------------------

def test_collectable_on_the_first_scan_after_an_hour(root):
    orphan = _obj(1, mtime=T - 3601)
    report = _scan(root, now=T)
    assert report["state"] == "complete"
    assert _ids(report) == {orphan.id}
    assert report["protected"] == []
    assert report["token"]
    assert report["grace_hours"] == image_gc.GRACE_HOURS == 1


def test_younger_than_an_hour_is_protected_with_a_time(root):
    fresh = _obj(1, mtime=T - 600)
    report = _scan(root, now=T)
    assert report["collectable"] == []
    row = next(r for r in report["protected"] if r["id"] == fresh.id)
    assert row["why"] == "grace"
    assert row["collectable_at"] == pytest.approx(T + 3000)
    assert report["token"] is None
    # An hour on, the same object is offered.
    assert _ids(_scan(root, now=T + 3000)) == {fresh.id}


def test_no_sightings_file_is_written(root):
    _obj(1)
    _scan(root, now=T)
    gc_dir = root / ".cache" / "image-store" / "gc"
    key = maintenance_reports.device_key(root)
    assert not (gc_dir / f"{key}.json").exists()
    assert list(gc_dir.glob("*.json")) == []


def test_an_older_format_token_is_refused(root):
    _obj(1)
    report = _ready(root)
    rec = image_gc.token_path(root, report["token"])
    raw = json.loads(rec.read_text(encoding="utf-8"))
    assert raw["format"] == image_gc.FORMAT == 2 and raw["grace_hours"] == image_gc.GRACE_HOURS
    raw["format"] = 1
    rec.write_text(json.dumps(raw), encoding="utf-8")
    done = _collect(root, report["token"])
    assert done["state"] == "refused" and done["deleted"]["objects"] == []


def test_future_mtimes_are_never_collectable(root):
    ahead = _obj(1, mtime=T + 400 * DAY)
    blob_ahead = _obj(2)
    os.utime(_blob(blob_ahead), (T + 400 * DAY, T + 400 * DAY))
    report = _ready(root)
    assert report["collectable"] == []
    skewed = {r["id"] for r in report["protected"] if r["why"] == "clock-skew"}
    assert skewed == {ahead.id, blob_ahead.id}
    assert {r["id"] for r in report["clock_skew"]} >= {ahead.id, blob_ahead.id}


# ---- blob refcounts -----------------------------------------------------------

def _share_blob(of: image_store.ImageObject, seed: int) -> str:
    """Another object's sidecar naming `of`'s blob -- what a pixel-identity
    change or a hand-built bundle could leave. Returns its id."""
    other = image_store.ingest(_png(seed), "png")
    raw = json.loads(_sidecar(other).read_text(encoding="utf-8"))
    os.unlink(_blob(other))
    raw["blob"] = dict(of.raw["blob"])
    _sidecar(other).write_text(json.dumps(raw), encoding="utf-8")
    _age(of, OLD)
    os.utime(_sidecar(other), (OLD, OLD))
    return other.id


def test_a_shared_blob_survives_its_unreachable_object(root):
    kept = _obj(1)
    _place(root, kept)
    sharer = _share_blob(kept, 2)
    report = _ready(root)
    assert _ids(report) == {sharer}
    assert report["reclaimable_bytes"] == _sidecar_size(sharer)
    done = _collect(root, report["token"])
    assert {r["id"] for r in done["deleted"]["objects"]} == {sharer}
    assert "blobs" not in done["deleted"]
    assert done["released"] == [{"id": sharer, "blob": kept.blob_sha256 + ".png",
                                 "reason": "shared"}]
    assert done["recycled"] == []
    assert _blob(kept).is_file()
    assert image_refs.resolve(_char_dir(root), "avatar") is not None


def _sidecar_size(image_id: str) -> int:
    return image_store.object_path(image_id).stat().st_size


def test_an_unreadable_sidecar_keeps_every_blob(root):
    orphan, garbled = _obj(1), _obj(2)
    stray_blob = _obj(3)
    os.unlink(_sidecar(stray_blob))           # an orphan blob, too
    _sidecar(garbled).write_text("{torn", encoding="utf-8")
    os.utime(_sidecar(garbled), (OLD, OLD))
    report = _ready(root)
    # The hold protects the object the collection would otherwise skip the
    # blob of, so the scan offers nothing and issues no token.
    assert report["collectable"] == [] and report["collectable_blobs"] == []
    assert report["token"] is None
    assert {"id": orphan.id, "blob": orphan.blob_sha256 + ".png",
            "why": "unreadable-sidecars", "collectable_at": None} in report["protected"]
    assert report["unreadable_sidecars"] == [_sidecar(garbled).relative_to(root).as_posix()]
    assert report["reclaimable_bytes"] == 0
    assert report["counts"]["collectable"] == 0
    for kept in (orphan, stray_blob, garbled):
        assert _blob(kept).is_file()
    assert _sidecar(orphan).is_file() and _sidecar(garbled).is_file()


def test_held_objects_are_protected_not_offered(root):
    """An otherwise collectable object whose blob is held (an unreadable
    sidecar, an unarrived object) is protected with that reason and left out
    of the token; a shared blob does not protect its object."""
    orphan, garbled = _obj(1), _obj(2)
    _sidecar(garbled).write_text("{torn", encoding="utf-8")
    os.utime(_sidecar(garbled), (OLD, OLD))
    unreadable = _scan(root, now=T)
    assert unreadable["collectable"] == [] and unreadable["token"] is None
    row = next(r for r in unreadable["protected"] if r["id"] == orphan.id)
    assert row["why"] == "unreadable-sidecars" and row["collectable_at"] is None

    os.unlink(_sidecar(garbled))
    image_refs.write(_char_dir(root), "avatar", "px1-" + "cd" * 32)
    unarrived = _scan(root, now=T)
    assert unarrived["collectable"] == [] and unarrived["token"] is None
    row = next(r for r in unarrived["protected"] if r["id"] == orphan.id)
    assert row["why"] == "unarrived-objects" and row["collectable_at"] is None
    assert unarrived["counts"]["protected"] == len(unarrived["protected"])


@pytest.mark.parametrize("hold", ["unreadable-sidecars", "unarrived-objects"])
def test_a_hold_that_appears_after_the_scan_keeps_the_blob(root, hold, fake_bin):
    """The scan was clean, then a sync delivered a placement whose sidecar has
    not arrived, or a sidecar went bad: the collection re-checks the hold and
    skips the whole object -- its sidecar would carry the description away and
    bin nothing."""
    orphan = _obj(1)
    report = _ready(root)
    assert _ids(report) == {orphan.id} and report["token"]
    if hold == "unreadable-sidecars":
        bad = _obj(2)
        _sidecar(bad).write_text("{torn", encoding="utf-8")
        os.utime(_sidecar(bad), (OLD, OLD))
    else:
        image_refs.write(_char_dir(root), "avatar", "px1-" + "ef" * 32)
    done = _collect(root, report["token"])
    assert _blob(orphan).is_file(), "the blob survives a hold that arrived after the scan"
    assert _sidecar(orphan).is_file(), "and so does its sidecar"
    assert {"id": orphan.id, "blob": orphan.blob_sha256 + ".png", "reason": hold} \
        in done["skipped"]
    assert done["deleted"]["objects"] == [] and done["recycled"] == []
    assert fake_bin.paths == []


def test_an_orphan_blob_is_a_candidate_after_grace(root):
    gone = _obj(1)
    blob = _blob(gone)
    os.unlink(_sidecar(gone))
    thumb = thumbs.thumbnail(blob, 128)
    assert thumb is not None and thumb.is_file()
    os.utime(blob, (OLD, OLD))
    report = _ready(root)
    assert report["collectable"] == []
    assert [r["blob"] for r in report["collectable_blobs"]] == [blob.name]
    assert report["reclaimable_bytes"] == blob.stat().st_size
    done = _collect(root, report["token"])
    name = f"grimoire - {gone.blob_sha256[:8]}.png"
    assert done["recycled"] == [{"id": None, "blob": blob.name, "name": name,
                                 "bytes": report["reclaimable_bytes"]}]
    assert not blob.exists()
    assert not thumb.exists()


def test_a_collected_objects_index_entry_and_thumbnails_go_with_its_blob(root):
    orphan, other = _obj(1), _obj(2)
    _place(root, other)
    idx = image_store.index_path(orphan.blob_sha256, root=root)
    other_idx = image_store.index_path(other.blob_sha256, root=root)
    assert idx.is_file() and other_idx.is_file()
    thumb = thumbs.thumbnail(_blob(orphan), 320)
    kept_thumb = thumbs.thumbnail(_blob(other), 320)
    _age(orphan, OLD)
    report = _ready(root)
    _collect(root, report["token"])
    assert not idx.exists() and not thumb.exists()
    assert other_idx.is_file() and kept_thumb.is_file()


# ---- a racing ingest ----------------------------------------------------------

def test_a_concurrent_ingest_is_never_left_dangling(root, monkeypatch, fake_bin):
    """The blob goes to the outbox before the sidecar is deleted, both under
    the ingest/GC lock and the object's stripe: an ingest of the same picture
    queued on the lock finds no sidecar and recreates the object with a fresh
    blob, never a sidecar naming the blob that just left."""
    orphan = _obj(1)
    report = _ready(root)
    assert _ids(report) == {orphan.id}

    real = image_gc._move_to_outbox
    started = threading.Event()
    result: dict = {}

    def ingest():
        started.set()
        result["obj"] = image_store.ingest(_png(1), "png")

    worker = threading.Thread(target=ingest)

    def move(rootp, blob, name):
        # Inside the batch, holding the ingest/GC lock: start an ingest of the
        # same bytes and give it time to queue on that lock.
        if not worker.is_alive() and "obj" not in result:
            worker.start()
            started.wait(5)
            time.sleep(0.3)
        assert "obj" not in result, "the ingest waits on the lock"
        return real(rootp, blob, name)

    monkeypatch.setattr(image_gc, "_move_to_outbox", move)
    done = _collect(root, report["token"])
    worker.join(10)
    assert not worker.is_alive()
    assert done["state"] == "complete"
    assert [r["id"] for r in done["recycled"]] == [orphan.id]
    got = result["obj"]
    assert got.id == orphan.id
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert image_store.read_fresh(orphan.id) is not None
    assert image_refs.resolve_ref(image_refs.Ref("x", orphan.id, None)) is not None


def test_an_ingest_that_touched_the_object_after_the_scan_is_skipped(root):
    orphan = _obj(1)
    report = _ready(root)
    image_store.ingest(_png(1), "png")       # touches the sidecar's mtime ...
    os.utime(_sidecar(orphan), (T + 1800, T + 1800))   # ... after the scan
    done = _collect(root, report["token"], now=T + 3600)
    assert done["deleted"]["objects"] == []
    assert any(s["id"] == orphan.id and s["reason"] == "grace" for s in done["skipped"])
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()


# ---- the token -----------------------------------------------------------------

def test_a_token_is_single_use_device_bound_and_expires(root, monkeypatch, tmp_path_factory):
    for seed in (1, 2, 3):
        _obj(seed)
    report = _ready(root)
    token = report["token"]

    # Expired: a day after the scan.
    late = _collect(root, token, now=T + 25 * 3600)
    assert late["state"] == "refused" and late["deleted"]["objects"] == []

    report = _scan(root, now=T + 26 * 3600)
    token = report["token"]
    # Another device: the same synced .cache, read by a machine with its own id.
    other = tmp_path_factory.mktemp("machine-b")
    with monkeypatch.context() as m:
        m.setattr(proclock, "lock_dir", lambda: other)
        elsewhere = _collect(root, token, now=T + 26 * 3600 + 60)
    assert elsewhere["state"] == "refused" and elsewhere["deleted"]["objects"] == []

    done = _collect(root, token, now=T + 26 * 3600 + 120)
    assert done["state"] == "complete" and len(done["deleted"]["objects"]) == 3
    again = _collect(root, token, now=T + 26 * 3600 + 180)
    assert again["state"] == "refused"

    for bad in ("", "../x", "A" * 32, None):
        assert _collect(root, bad)["state"] == "refused"


def test_a_token_for_another_root_is_refused(root):
    _obj(1)
    report = _ready(root)
    rec = image_gc.token_path(root, report["token"])
    raw = json.loads(rec.read_text(encoding="utf-8"))
    raw["root"] = str(root.parent / "elsewhere")
    rec.write_text(json.dumps(raw), encoding="utf-8")
    assert _collect(root, report["token"])["state"] == "refused"


def test_collect_deletes_at_most_the_reported_ids(root):
    a, b = _obj(1), _obj(2)
    report = _ready(root)
    assert _ids(report) == {a.id, b.id}
    # After the scan: a third orphan, as old as the others, and `b` placed.
    c = _obj(3)
    _place(root, b)
    done = _collect(root, report["token"])
    assert {r["id"] for r in done["deleted"]["objects"]} == {a.id}
    assert any(s["id"] == b.id and s["reason"] == "reachable" for s in done["skipped"])
    assert _sidecar(b).is_file() and _sidecar(c).is_file() and _blob(c).is_file()


# ---- the pinned root -------------------------------------------------------------

def test_a_root_flip_mid_collect_deletes_nothing(root, monkeypatch, tmp_path):
    for seed in (1, 2):
        _obj(seed)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    before = _tree(root), _tree(other)
    real = locks.image_object_lock

    def flip(image_id):
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        return real(image_id)

    monkeypatch.setattr(locks, "image_object_lock", flip)
    done = _collect(root, report["token"])
    assert done["state"] == "root-changed"
    assert done["deleted"]["objects"] == [] and done["recycled"] == []
    after = _tree(root, skip=(".cache/image-store",)), _tree(other)
    assert after[0] == {k: v for k, v in before[0].items()
                        if not k.startswith(".cache/image-store/")}
    assert after[1] == before[1]


def test_a_root_that_is_not_the_live_one_is_refused(root, tmp_path):
    orphan = _obj(1)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    assert image_gc.scan(other.resolve(), cancel=_never, now=T)["state"] == "root-changed"
    done = image_gc.collect(other.resolve(), report["token"], cancel=_never, now=T + 60)
    assert done["state"] == "root-changed"
    assert _sidecar(orphan).is_file()


def test_nothing_outside_image_store_is_deleted(root, tmp_path, fake_bin):
    placed, orphan = _obj(1), _obj(2)
    _place(root, placed)
    (root / "worlds" / "realm" / "world.md").write_text("---\nname: Realm\n---\n",
                                                         encoding="utf-8")
    legacy = _char_dir(root, char="mara") / "avatar.png"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(_png(2))                # the orphan's bytes, as a legacy file
    stray = root / "assets" / "image-store" / "blobs" / "zz" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("mine", encoding="utf-8")
    report = _ready(root)
    before_home = _tree(root, skip=("assets/image-store", ".cache"))
    before_outside = _tree(tmp_path / "outside")
    done = _collect(root, report["token"])
    assert {r["id"] for r in done["deleted"]["objects"]} == {orphan.id}
    assert _tree(root, skip=("assets/image-store", ".cache")) == before_home
    assert _tree(tmp_path / "outside") == before_outside
    assert stray.is_file() and legacy.is_file()
    left = {p.relative_to(root).as_posix() for p in (root / "assets" / "image-store").rglob("*")
            if p.is_file()}
    assert _sidecar(placed).relative_to(root).as_posix() in left
    assert _blob(placed).relative_to(root).as_posix() in left
    # The orphan's picture left through the outbox, to the bin, and only it.
    assert [p.parent for p in fake_bin.paths] == [image_gc.outbox_dir(root)]
    assert [r["id"] for r in done["recycled"]] == [orphan.id]
    assert list(image_gc.outbox_dir(root).iterdir()) == []


# ---- batching ---------------------------------------------------------------------

class _Counting:
    """The ingest/GC lock, counting holds and checking each one is released."""

    def __init__(self, real):
        self.real = real
        self.entered = 0
        self.held = False

    def __call__(self):
        return self

    def __enter__(self):
        lock = self.real()
        lock.__enter__()
        self.entered += 1
        self.held = True
        self.lock = lock
        return self

    def __exit__(self, *exc):
        self.held = False
        return self.lock.__exit__(*exc)


@pytest.mark.parametrize("objects, seconds, holds", [(2, 60.0, 3), (100, 0.0, 5)])
def test_deletion_is_batched_and_releases_the_ingest_lock(root, monkeypatch,
                                                         objects, seconds, holds):
    made = [_obj(seed) for seed in range(1, 6)]
    report = _ready(root)
    counting = _Counting(locks.image_ingest_gc_lock)
    monkeypatch.setattr(locks, "image_ingest_gc_lock", counting)
    monkeypatch.setattr(image_gc, "BATCH_OBJECTS", objects)
    monkeypatch.setattr(image_gc, "BATCH_SECONDS", seconds)
    free_between: list[bool] = []
    real = image_gc._delete_file

    def delete_file(rootp, path):
        free_between.append(counting.held)
        return real(rootp, path)

    monkeypatch.setattr(image_gc, "_delete_file", delete_file)
    done = _collect(root, report["token"])
    assert len(done["deleted"]["objects"]) == 5
    assert counting.entered == holds
    assert all(free_between), "every delete happens under the ingest/GC lock"
    assert not counting.held
    for obj in made:
        assert not _sidecar(obj).exists()


# ---- reports on every ending ----------------------------------------------------

def test_scan_and_collect_leave_a_report_on_success_failure_and_cancel(root, monkeypatch):
    _obj(1)
    ok = _scan(root, now=T, run_id="1" * 32)
    assert maintenance_reports.read(root, "1" * 32) == ok

    cancelled = image_gc.scan(root, cancel=lambda: True, now=T, run_id="2" * 32)
    assert cancelled["state"] == "cancelled" and cancelled["token"] is None
    assert maintenance_reports.read(root, "2" * 32)["state"] == "cancelled"

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    with monkeypatch.context() as m:
        m.setattr(image_gc, "_list_objects", boom)
        with pytest.raises(RuntimeError):
            _scan(root, now=T, run_id="3" * 32)
    assert maintenance_reports.read(root, "3" * 32)["state"] == "failed"

    report = _scan(root, now=T)
    done = image_gc.collect(root, report["token"], cancel=lambda: True,
                            now=T + 60, run_id="4" * 32)
    assert done["state"] == "cancelled" and done["deleted"]["objects"] == []
    assert maintenance_reports.read(root, "4" * 32)["state"] == "cancelled"
    assert done["mode"] == "collect" and ok["mode"] == "scan"
    assert set(done) == set(ok), "a dry run and a real run report the same shape"


# ---- spellings shared with other modules --------------------------------------------

def test_the_paths_gc_spells_agree_with_their_owners(root):
    assert tuple(common.THUMB_BUCKETS) == thumbs.WIDTHS
    assert set(image_gc.STAGING_DIRS) == {world_staging.staging_root().name,
                                          module_staging._staging_root().name}
    (root / "worlds" / "realm").mkdir(parents=True)
    (root / "worlds" / "realm" / "world.md").write_text("---\nname: Realm\n---\n",
                                                         encoding="utf-8")
    assert (image_collections.journal_directory("realm").parent
            == image_collections.raw_journals_root(root))
    obj = _obj(1)
    thumb = thumbs.thumbnail(_blob(obj), 256)
    assert thumb.resolve() in {p.resolve() for p in image_gc.blob_thumbnails(root, obj.blob_sha256)}


# ---- fix round 1 ----------------------------------------------------------------------

def test_a_case_variant_image_refs_folder_aborts(root):
    """A case-insensitive filesystem serves `Image-Refs/` as `image-refs/`,
    so its placements are roots the exact-spelling walk would have missed."""
    orphan, hidden = _obj(1), _obj(2)
    d = _char_dir(root)
    variant = d / "Image-Refs"
    variant.mkdir(parents=True)
    (variant / "avatar.json").write_text(json.dumps({"format": 1, "image": hidden.id}),
                                         encoding="utf-8")
    report = _scan(root, now=T)
    assert report["state"] == "blocked" and report["token"] is None
    assert {"path": variant.relative_to(root).as_posix(), "reason": "case-variant"} \
        in report["blocking"]
    assert _sidecar(hidden).is_file() and _sidecar(orphan).is_file()


@pytest.mark.parametrize("slug", ["image-refs", "Image-Refs"])
def test_a_record_named_image_refs_is_not_a_placement_folder(root, slug):
    """Only a folder below an `assets` component holds placements. A record
    whose own slug is `image-refs` (or a case variant of it) is a markdown
    folder like any other, and parsing it as placements would block every
    collection for good."""
    kept, orphan = _obj(1), _obj(2)
    _place(root, kept)
    lore = root / "worlds" / "realm" / "lore"
    # Each spelling gets its own store: an insensitive filesystem cannot hold
    # both siblings, and collection must accept either spelling independently.
    (lore / slug).mkdir(parents=True)
    (lore / slug / "entry.md").write_text("# Saltmarch\n", encoding="utf-8")
    report = _scan(root, now=T)
    assert report["state"] != "blocked" and report["blocking"] == []
    assert report["token"] is not None
    ids = {row["id"] for row in report["collectable"]}
    assert orphan.id in ids and kept.id not in ids


def test_an_unarrived_object_keeps_every_orphan_blob(root):
    gone = _obj(1)
    os.unlink(_sidecar(gone))                     # an orphan blob, old enough
    os.utime(_blob(gone), (OLD, OLD))
    report = _ready(root)
    assert [r["blob"] for r in report["collectable_blobs"]] == [_blob(gone).name]

    # A placement synced in whose object has not: its blob may be that one.
    unarrived = "px1-" + "ab" * 32
    image_refs.write(_char_dir(root), "avatar", unarrived)
    held = _scan(root, now=T + 60)
    assert held["state"] == "complete"
    assert held["unarrived_objects"] == [unarrived]
    assert held["collectable_blobs"] == [] and held["token"] is None
    assert {"id": None, "blob": _blob(gone).name, "why": "unarrived-objects",
            "collectable_at": None} in held["protected"]

    done = _collect(root, report["token"], now=T + 120)
    assert done["recycled"] == []
    assert {"id": None, "blob": _blob(gone).name, "reason": "unarrived-objects"} \
        in done["skipped"]
    assert _blob(gone).is_file()


def test_blobs_without_an_objects_folder_block(root):
    gone = _obj(1)
    blob = _blob(gone)
    shutil.rmtree(image_store.store_root() / "objects")
    os.utime(blob, (OLD, OLD))
    report = _scan(root, now=T)
    assert report["state"] == "blocked" and report["token"] is None
    assert {"path": "assets/image-store/objects", "reason": "objects-missing"} \
        in report["blocking"]
    assert blob.is_file()


def test_a_linked_cache_blocks(root, tmp_path):
    _obj(1)
    elsewhere = tmp_path / "outside" / "cache"
    elsewhere.mkdir()
    shutil.rmtree(root / ".cache", ignore_errors=True)
    (root / ".cache").symlink_to(elsewhere)
    report = _scan(root, now=T)
    assert report["state"] == "blocked"
    assert {"path": ".cache", "reason": "symlink"} in report["blocking"]


def test_a_root_flip_between_sidecar_and_blob_reports_the_sidecar(root, monkeypatch, tmp_path,
                                                                 fake_bin):
    """The blob is moved to the outbox first, then the sidecar is deleted. A
    root flip between the two stops the run with the move waiting in the
    outbox and the sidecar intact (it names a missing blob, which no
    placement reaches; the next collection releases it)."""
    first, second = _obj(1), _obj(2)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    real = image_gc._move_to_outbox
    moved: list[Path] = []

    def move(rootp, blob, name):
        out = real(rootp, blob, name)
        moved.append(out)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))     # flips after the move
        return out

    files = {o.id: (_sidecar(o), _blob(o)) for o in (first, second)}   # before the flip
    data = {o.id: _blob(o).read_bytes() for o in (first, second)}
    monkeypatch.setattr(image_gc, "_move_to_outbox", move)
    done = _collect(root, report["token"])
    assert done["state"] == "root-changed"
    assert len(moved) == 1 and moved[0].is_file()
    assert moved[0].parent == image_gc.outbox_dir(root)
    gone = next(o for o in (first, second) if not files[o.id][1].exists())
    kept = first if gone is second else second
    assert moved[0].read_bytes() == data[gone.id]
    assert files[gone.id][0].is_file(), "the sidecar is intact"
    assert done["deleted"]["objects"] == [] and done["recycled"] == []
    assert done["outbox"] == [moved[0].name]
    assert fake_bin.paths == []
    assert files[kept.id][0].is_file() and files[kept.id][1].is_file()
    assert list(other.iterdir()) == []


def test_cache_cleanup_is_best_effort(root):
    orphan = _obj(1)
    stuck = image_gc.blob_thumbnails(root, orphan.blob_sha256)[0]
    stuck.mkdir(parents=True)                      # not a file: will not unlink
    report = _ready(root)
    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert [r["id"] for r in done["deleted"]["objects"]] == [orphan.id]
    assert [r["blob"] for r in done["recycled"]] == [_blob(orphan).name]
    assert done["cache_cleanup"] == [{"path": stuck.relative_to(root).as_posix(),
                                      "reason": "_UnsafePathError"}]


def test_a_gone_blobs_thumbnails_still_go(root):
    orphan = _obj(1)
    thumb = thumbs.thumbnail(_blob(orphan), 256)
    assert thumb is not None and thumb.is_file()
    report = _ready(root)
    os.unlink(_blob(orphan))                       # a partial earlier collection
    done = _collect(root, report["token"])
    assert [r["id"] for r in done["deleted"]["objects"]] == [orphan.id]
    assert done["recycled"] == []
    assert not thumb.exists()


def test_blob_keys_are_where_thumbnails_land(root):
    obj = _obj(1)
    made = {thumbs.thumbnail(_blob(obj), w).resolve() for w in thumbs.WIDTHS}
    keys = {(root / k).resolve() for k in thumbs.blob_keys(obj.blob_sha256)}
    assert made <= keys


# ---- fix round 2 ----------------------------------------------------------------------

def test_a_blob_shared_with_an_unarrived_object_is_kept(root):
    """Object B is placed and shares A's blob, but B's sidecar has not
    synced in yet: A is protected rather than offered, so the blob B will
    point at is never at risk."""
    a = _obj(1)
    b = _share_blob(a, 2)
    d = _char_dir(root)
    image_refs.write(d, "avatar", b)
    os.unlink(image_store.object_path(b))
    report = _scan(root, now=T)
    assert report["unarrived_objects"] == [b]
    assert report["collectable"] == [] and report["token"] is None
    assert report["reclaimable_bytes"] == 0
    assert {"id": a.id, "blob": a.blob_sha256 + ".png", "why": "unarrived-objects",
            "collectable_at": None} in report["protected"]
    assert _sidecar(a).is_file() and _blob(a).is_file()


# ---- disposal: the outbox and the recycle bin -------------------------------------

SHA = "ab12cd34" + "0" * 56


def _describe(obj, text: str, **extra) -> None:
    """Give `obj`'s sidecar a description (and any other raw fields), aged."""
    raw = json.loads(_sidecar(obj).read_text(encoding="utf-8"))
    raw["description"] = text
    raw.update(extra)
    _sidecar(obj).write_text(json.dumps(raw), encoding="utf-8")
    _age(obj, OLD)


def _binned(fake_bin) -> list[str]:
    return [p.name for p in fake_bin.paths]


@pytest.mark.parametrize("description, expected", [
    ("Mara at the harbour", "grimoire - Mara at the harbour - ab12cd34.png"),
    (None, "grimoire - ab12cd34.png"),
    ("", "grimoire - ab12cd34.png"),
    ('a/b:c*d?"e<f>g|h\\i', "grimoire - abcdefghi - ab12cd34.png"),
    ("Seraphine at dusk. ", "grimoire - Seraphine at dusk - ab12cd34.png"),
    ("Seraphine at dusk...", "grimoire - Seraphine at dusk - ab12cd34.png"),
    ("  Mara\n\tat   the\x07 harbour  ", "grimoire - Mara at the harbour - ab12cd34.png"),
    ('<>:"/\\|?*', "grimoire - ab12cd34.png"),
    ("\x01\x02 . ", "grimoire - ab12cd34.png"),
    (" ".join(["Saltmarch"] * 12),
     "grimoire - " + " ".join(["Saltmarch"] * 6) + " - ab12cd34.png"),
    ("W" * 100, "grimoire - " + "W" * 60 + " - ab12cd34.png"),
    ("Mara\ud800 harbour", "grimoire - Mara harbour - ab12cd34.png"),
])
def test_bin_name(description, expected):
    name = image_gc.bin_name(description, SHA, "png")
    assert name == expected
    middle = name[len("grimoire - "):-len(" - ab12cd34.png")]
    assert len(middle) <= 60


def test_a_bin_name_fits_a_file_name_in_bytes():
    """Sixty four-byte characters are within the character cap and past the
    255 bytes a file name may take; the description is cut further."""
    name = image_gc.bin_name("\U0001F600" * 60, SHA, "webp")
    assert name.startswith("grimoire - \U0001F600") and name.endswith(" - ab12cd34.webp")
    assert len(f"{name[:-5]} (99).webp".encode()) <= 255


def test_a_collected_blob_goes_to_the_bin_named_and_its_rest_is_deleted(root, fake_bin):
    orphan, placed = _obj(1), _obj(2)
    _place(root, placed)
    _describe(orphan, "Mara at the harbour")
    thumb = thumbs.thumbnail(_blob(orphan), 128)
    idx = image_store.index_path(orphan.blob_sha256, root=root)
    assert thumb is not None and thumb.is_file() and idx.is_file()
    data = _blob(orphan).read_bytes()
    _age(orphan, OLD)
    report = _ready(root)
    assert report["recycle_bin"] == {"ok": True, "code": None, "message": None}
    done = _collect(root, report["token"])
    name = f"grimoire - Mara at the harbour - {orphan.blob_sha256[:8]}.png"
    assert done["state"] == "complete"
    assert _binned(fake_bin) == [name]
    assert fake_bin.paths[0].parent == image_gc.outbox_dir(root)
    assert fake_bin._dir is not None and (fake_bin._dir / f"0-{name}").read_bytes() == data
    assert done["recycled"] == [{"id": orphan.id, "blob": orphan.blob_sha256 + ".png",
                                 "name": name, "bytes": len(data)}]
    assert not _sidecar(orphan).exists() and not _blob(orphan).exists()
    assert not thumb.exists() and not idx.exists()
    assert [r["id"] for r in done["deleted"]["objects"]] == [orphan.id]
    assert set(done["deleted"]) == {"objects", "bytes"}
    assert done["released"] == [] and done["outbox"] == [] and done["unverified"] == []
    assert done["recycle_bin"]["ok"] is True
    assert _sidecar(placed).is_file() and _blob(placed).is_file()


def test_description_conflicts_bins_without_a_description(root, fake_bin):
    orphan = _obj(1)
    _describe(orphan, "Mara", description_conflicts=[{"text": "Seraphine"}])
    done = _collect(root, _ready(root)["token"])
    assert _binned(fake_bin) == [f"grimoire - {orphan.blob_sha256[:8]}.png"]
    assert done["recycled"][0]["name"] == _binned(fake_bin)[0]


def test_held_object_keeps_its_sidecar_at_collect(root, fake_bin):
    """A placement whose object has not arrived appears between the scan and
    the collection: the object is skipped whole, with the hold as reason."""
    orphan = _obj(1)
    _describe(orphan, "Seraphine in the rain")
    report = _ready(root)
    assert _ids(report) == {orphan.id}
    image_refs.write(_char_dir(root), "avatar", "px1-" + "12" * 32)
    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert {"id": orphan.id, "blob": orphan.blob_sha256 + ".png",
            "reason": "unarrived-objects"} in done["skipped"]
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert json.loads(_sidecar(orphan).read_text(encoding="utf-8"))["description"] \
        == "Seraphine in the rain"
    assert done["deleted"]["objects"] == [] and done["recycled"] == [] and done["released"] == []
    assert fake_bin.paths == []


def test_a_missing_blob_releases_the_sidecar(root, fake_bin):
    orphan = _obj(1)
    thumb = thumbs.thumbnail(_blob(orphan), 256)
    assert thumb is not None and thumb.is_file()
    _age(orphan, OLD)
    report = _ready(root)
    os.unlink(_blob(orphan))             # a crash after an earlier move, or a sync
    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert not _sidecar(orphan).exists()
    assert not thumb.exists()
    assert done["released"] == [{"id": orphan.id, "blob": orphan.blob_sha256 + ".png",
                                 "reason": "blob-missing"}]
    assert [r["id"] for r in done["deleted"]["objects"]] == [orphan.id]
    assert done["recycled"] == [] and fake_bin.paths == []


def test_bin_runs_after_the_ingest_lock_is_released(root, monkeypatch, fake_bin):
    a, b = _obj(1), _obj(2)
    report = _ready(root)
    free: list[bool] = []

    def send(path: str) -> None:
        def probe() -> None:
            lock = locks.image_ingest_gc_lock()
            got = lock.acquire(timeout=2)
            free.append(got)
            if got:
                lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join(10)
        fake_bin.send(path)

    monkeypatch.setattr(recycle_bin, "_send2trash", send)
    monkeypatch.setattr(image_gc, "BATCH_OBJECTS", 1)
    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert free == [True, True], "every bin call runs with the ingest/GC lock free"
    assert {r["id"] for r in done["recycled"]} == {a.id, b.id}


def _refuse(path: str) -> None:
    raise OSError("the bin said no")


def test_a_refused_bin_leaves_the_file_in_the_outbox(root, monkeypatch, fake_bin):
    orphan = _obj(1)
    _describe(orphan, "Winifred's lantern")
    size = _blob(orphan).stat().st_size
    report = _ready(root)
    name = f"grimoire - Winifred's lantern - {orphan.blob_sha256[:8]}.png"
    with monkeypatch.context() as m:
        m.setattr(recycle_bin, "_send2trash", _refuse)
        done = _collect(root, report["token"])
    assert done["state"] == image_gc.TRASH_UNAVAILABLE == "trash-unavailable"
    assert done["error"] == "OSError"
    waiting = image_gc.outbox_dir(root) / name
    assert waiting.is_file() and done["outbox"] == [name]
    assert not _blob(orphan).exists() and not _sidecar(orphan).exists()
    assert done["recycled"] == []

    again = _scan(root, now=T + 120)
    assert again["outbox"] == [name]
    assert again["collectable"] == [] and again["collectable_blobs"] == []
    assert again["token"], "waiting files can always be drained"

    final = _collect(root, again["token"], now=T + 180)
    assert final["state"] == "complete"
    assert _binned(fake_bin) == [name]
    assert final["recycled"] == [{"id": None, "blob": None, "name": name, "bytes": size}]
    assert final["outbox"] == [] and not waiting.exists()


def test_drain_refusal_stops_before_the_first_batch(root, monkeypatch, fake_bin):
    outbox = image_gc.outbox_dir(root)
    outbox.mkdir(parents=True)
    stuck = outbox / "grimoire - 0badcafe.png"
    stuck.write_bytes(_png(9))
    orphan = _obj(1)
    report = _ready(root)
    assert report["outbox"] == [stuck.name] and _ids(report) == {orphan.id}
    with monkeypatch.context() as m:
        m.setattr(recycle_bin, "_send2trash", _refuse)
        done = _collect(root, report["token"])
    assert done["state"] == "trash-unavailable" and done["error"] == "OSError"
    assert done["deleted"]["objects"] == [] and done["skipped"] == []
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert stuck.is_file() and done["outbox"] == [stuck.name]
    assert fake_bin.paths == []
    assert not image_gc.token_path(root, report["token"]).exists(), "the token is spent"


def test_cancel_after_outbox_move_drains_next_time(root, monkeypatch, fake_bin):
    _obj(1), _obj(2)
    report = _ready(root)
    real = image_gc._move_to_outbox
    moved: list[Path] = []

    def move(rootp, blob, name):
        out = real(rootp, blob, name)
        moved.append(out)
        return out

    monkeypatch.setattr(image_gc, "_move_to_outbox", move)
    done = image_gc.collect(root, report["token"], cancel=lambda: bool(moved), now=T + 60)
    assert done["state"] == "cancelled"
    assert len(moved) == 1 and moved[0].is_file()
    assert fake_bin.paths == []
    assert done["outbox"] == [moved[0].name]

    again = _scan(root, now=T + 120)
    assert again["outbox"] == [moved[0].name]
    final = _collect(root, again["token"], now=T + 180)
    assert final["state"] == "complete"
    assert fake_bin.paths[0] == moved[0]
    assert _binned(fake_bin).count(moved[0].name) == 1 and len(fake_bin.paths) == 2
    assert final["outbox"] == []


def test_another_devices_outbox_is_counted_not_touched(root, fake_bin):
    foreign = root / ".cache" / "image-store" / "recycle" / ("f" * 64)
    foreign.mkdir(parents=True)
    theirs = foreign / "grimoire - 12345678.png"
    theirs.write_bytes(_png(7))
    assert image_gc.outbox_dir(root) != foreign
    orphan = _obj(1)
    report = _ready(root)
    assert report["foreign_outboxes"] == 1 and report["outbox"] == []
    done = _collect(root, report["token"])
    assert done["state"] == "complete" and done["foreign_outboxes"] == 1
    assert theirs.read_bytes() == _png(7)
    assert [r["id"] for r in done["recycled"]] == [orphan.id]
    assert all(p.parent == image_gc.outbox_dir(root) for p in fake_bin.paths)


def test_a_symlinked_outbox_blocks(root, tmp_path, fake_bin):
    orphan = _obj(1)
    report = _ready(root)
    elsewhere = tmp_path / "outside" / "outbox"
    elsewhere.mkdir()
    planted = elsewhere / "grimoire - 0badcafe.png"
    planted.write_bytes(_png(8))
    outbox = image_gc.outbox_dir(root)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    try:
        outbox.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("no symlink rights here")
    blocked = _scan(root, now=T + 30)
    assert blocked["state"] == "blocked" and blocked["token"] is None
    assert {"path": outbox.relative_to(root).as_posix(), "reason": "symlink"} \
        in blocked["blocking"]
    done = _collect(root, report["token"])
    assert done["state"] == "blocked"
    assert done["blocking"]
    assert fake_bin.paths == [] and done["recycled"] == []
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert planted.read_bytes() == _png(8)
    assert done["outbox"] == []


def test_a_root_flip_before_a_bin_call_stops(root, monkeypatch, tmp_path, fake_bin):
    orphan = _obj(1)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    real = recycle_bin.send_batch

    def send_batch(rootp, paths, before, **kw):
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        return real(rootp, paths, before, **kw)

    monkeypatch.setattr(recycle_bin, "send_batch", send_batch)
    done = _collect(root, report["token"])
    assert done["state"] == "root-changed"
    assert fake_bin.paths == [] and done["recycled"] == []
    name = f"grimoire - {orphan.blob_sha256[:8]}.png"
    assert done["outbox"] == [name]
    assert (image_gc.outbox_dir(root) / name).is_file()
    assert list(other.iterdir()) == []


def test_collect_refuses_without_a_bin(root, monkeypatch, fake_bin):
    orphan = _obj(1)
    report = _ready(root)
    no = recycle_bin.Availability(False, "not_installed", recycle_bin.MESSAGES["not_installed"])
    monkeypatch.setattr(recycle_bin, "check", lambda r: no)
    done = _collect(root, report["token"])
    assert done["state"] == "refused" and done["error"] == "no_recycle_bin"
    assert done["recycle_bin"] == no.to_dict()
    assert image_gc.token_path(root, report["token"]).is_file(), "the token is not spent"
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert fake_bin.paths == []
    scan = _scan(root, now=T + 120)
    assert scan["recycle_bin"] == {"ok": False, "code": "not_installed",
                                   "message": recycle_bin.MESSAGES["not_installed"]}


def test_unverified_stops_the_run(root, monkeypatch, fake_bin):
    a, b = _obj(1), _obj(2)
    report = _ready(root)
    real = recycle_bin.send_batch

    def send_batch(rootp, paths, before, **kw):
        result = real(rootp, paths, before, **kw)
        result.unverified = list(result.sent[:1])
        return result

    monkeypatch.setattr(recycle_bin, "send_batch", send_batch)
    monkeypatch.setattr(image_gc, "BATCH_OBJECTS", 1)
    done = _collect(root, report["token"])
    assert done["state"] == image_gc.RECYCLE_BIN_UNVERIFIED == "recycle-bin-unverified"
    assert len(fake_bin.paths) == 1
    assert done["unverified"] == [fake_bin.paths[0].name]
    assert len(done["recycled"]) == 1
    left = [o for o in (a, b) if _sidecar(o).exists()]
    assert len(left) == 1 and _blob(left[0]).is_file(), "nothing more is collected"


def test_a_name_already_waiting_gets_a_suffix(root, monkeypatch, fake_bin):
    _obj(1), _obj(2)
    report = _ready(root)
    monkeypatch.setattr(image_gc, "bin_name", lambda description, sha, ext: "grimoire - Mara.png")
    done = _collect(root, report["token"])
    assert done["state"] == "complete"
    assert _binned(fake_bin) == ["grimoire - Mara.png", "grimoire - Mara (2).png"]
    assert [r["name"] for r in done["recycled"]] == _binned(fake_bin)


# ---- a cancel reaches the bin calls ---------------------------------------------

def _waiting(root: Path, *names: str) -> list[Path]:
    outbox = image_gc.outbox_dir(root)
    outbox.mkdir(parents=True, exist_ok=True)
    out = []
    for i, name in enumerate(names):
        (outbox / name).write_bytes(_png(40 + i))
        out.append(outbox / name)
    return out


def test_a_cancel_asked_before_the_drain_sends_nothing(root, fake_bin):
    stuck = _waiting(root, "grimoire - 0badcafe.png", "grimoire - 1badcafe.png")
    orphan = _obj(1)
    report = _ready(root)
    assert report["outbox"] == [p.name for p in stuck]
    done = image_gc.collect(root, report["token"], cancel=lambda: True, now=T + 60)
    assert done["state"] == "cancelled"
    assert fake_bin.paths == [] and done["recycled"] == []
    assert all(p.is_file() for p in stuck)
    assert done["outbox"] == [p.name for p in stuck]
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()


def test_a_cancel_mid_drain_keeps_what_was_sent_and_leaves_the_rest(root, fake_bin):
    stuck = _waiting(root, "grimoire - 0badcafe.png", "grimoire - 1badcafe.png",
                     "grimoire - 2badcafe.png")
    orphan = _obj(1)
    report = _ready(root)
    done = image_gc.collect(root, report["token"], cancel=lambda: len(fake_bin.paths) >= 1,
                            now=T + 60)
    assert done["state"] == "cancelled"
    assert fake_bin.paths == [stuck[0]]
    assert [r["name"] for r in done["recycled"]] == [stuck[0].name]
    assert done["outbox"] == [stuck[1].name, stuck[2].name]
    assert stuck[1].is_file() and stuck[2].is_file()
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()


def test_a_cancel_mid_batch_send_keeps_what_was_sent_and_leaves_the_rest(root, fake_bin):
    a, b = _obj(1), _obj(2)
    report = _ready(root)
    done = image_gc.collect(root, report["token"], cancel=lambda: len(fake_bin.paths) >= 1,
                            now=T + 60)
    assert done["state"] == "cancelled"
    assert len(fake_bin.paths) == 1
    assert [r["name"] for r in done["recycled"]] == _binned(fake_bin)
    assert len(done["outbox"]) == 1
    assert (image_gc.outbox_dir(root) / done["outbox"][0]).is_file()
    # Both moved under the lock before the send; neither blob is left in the store.
    assert not _blob(a).exists() and not _blob(b).exists()


# ---- a blocked scan can still drain ---------------------------------------------

def _block_with_a_case_variant(root: Path) -> None:
    variant = _char_dir(root) / "Image-Refs"
    variant.mkdir(parents=True)
    (variant / "avatar.json").write_text(json.dumps({"format": 1, "image": "0" * 64}),
                                         encoding="utf-8")


def test_a_blocked_scan_with_waiting_files_issues_a_drain_only_token(root, fake_bin):
    stuck = _waiting(root, "grimoire - 0badcafe.png")
    orphan = _obj(1)
    _block_with_a_case_variant(root)
    report = _scan(root, now=T)
    assert report["state"] == "blocked" and report["outbox"] == [stuck[0].name]
    assert report["token"], "waiting files can always be drained"
    rec = json.loads(image_gc.token_path(root, report["token"]).read_text(encoding="utf-8"))
    assert rec["ids"] == [] and rec["blobs"] == []
    assert report["collectable"] == [] and report["collectable_blobs"] == []

    done = _collect(root, report["token"])
    assert done["state"] == "blocked" and done["blocking"]
    assert fake_bin.paths == stuck
    assert [r["name"] for r in done["recycled"]] == [stuck[0].name]
    assert done["outbox"] == []
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()


def test_a_blocked_scan_with_an_empty_outbox_issues_no_token(root):
    _obj(1)
    _block_with_a_case_variant(root)
    report = _scan(root, now=T)
    assert report["state"] == "blocked" and report["token"] is None
