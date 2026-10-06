"""Image garbage collection (stage 4, spec section 12 and rulings M12-M15).

`image_gc.scan` is the dry run: it walks every root strictly, records this
device's sightings of unreachable objects and blobs, and -- when something is
collectable and nothing blocked the walk -- hands back a single-use token.
`image_gc.collect(token)` deletes at most what that scan reported, intersected
with a fresh strict walk, in batches under the ingest/GC lock.

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
    thumbs,
)
from grimoire.store.module_edit import staging as module_staging
from grimoire.store.worlds import staging as world_staging

DAY = 24 * 3600.0
T = 1_900_000_000.0          # a fixed "now" for the first scan of each test
OLD = T - 40 * DAY           # an mtime well past the grace period at T
LATER = T + 31 * DAY         # a second scan, a grace period after the first


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
    """Two scans a grace period apart: whatever is unreachable and old is
    collectable on the second, which is returned."""
    _scan(root, now=T)
    return _scan(root, now=LATER)


def _collect(root, token, now=LATER + 3600, **kw) -> dict:
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
    _ready(root)
    report = _scan(root, now=LATER + DAY)
    assert report["state"] == "complete"
    assert report["counts"]["placements"] == 3
    assert report["counts"]["objects"] == 3
    assert _ids(report) == {orphan.id}
    assert report["token"]

    done = _collect(root, report["token"], now=LATER + DAY + 60)
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

    blocked = _scan(root, now=LATER + 60)
    assert blocked["state"] == "blocked"
    assert blocked["blocking"], "a blocked scan names what blocked it"
    assert blocked["token"] is None
    assert blocked["collectable"] == []

    # And the token an earlier, clean scan issued deletes nothing now.
    done = _collect(root, token, now=LATER + 120)
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

    _scan(root, now=T)
    report = _scan(root, now=LATER)
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
    _scan(root, now=T)
    report = _scan(root, now=LATER)
    assert report["state"] == "blocked"
    assert report["token"] is None
    assert any(b["path"] == p.relative_to(root).as_posix() for b in report["blocking"])


def test_an_old_staging_dir_is_walked_and_a_young_one_aborts(root):
    staged, orphan = _obj(1), _obj(2)
    old = root / ".module-staging" / ("d" * 32)
    image_refs.write(old / "world" / "lore" / "rumor" / "assets" / "default", "map", staged.id)
    for p in [old, *old.rglob("*")]:
        os.utime(p, (T - DAY, T - DAY))
    _scan(root, now=T)
    report = _scan(root, now=LATER)
    assert report["state"] == "complete"
    assert _ids(report) == {orphan.id}

    young = root / ".world-staging" / ("e" * 32) / "world"
    young.mkdir(parents=True)
    os.utime(young, (LATER + 3000, LATER + 3000))
    blocked = _scan(root, now=LATER + 3600)
    assert blocked["state"] == "blocked"
    assert blocked["token"] is None
    assert any(b["reason"] == "staging-in-progress" for b in blocked["blocking"])
    done = _collect(root, report["token"], now=LATER + 3600)
    assert done["state"] == "blocked" and done["deleted"]["objects"] == []
    assert _sidecar(orphan).is_file()


# ---- grace, sightings and clock skew -------------------------------------------

def test_recent_orphans_are_grace_protected(root):
    fresh = _obj(1, mtime=LATER - DAY)
    report = _ready(root)
    assert report["collectable"] == []
    row = next(r for r in report["protected"] if r["id"] == fresh.id)
    assert row["why"] == "grace"
    assert row["collectable_at"] == pytest.approx(LATER - DAY + 30 * DAY)
    assert report["token"] is None


def test_collectable_only_after_grace_old_first_sighting_and_a_second_scan(root):
    orphan = _obj(1)
    first = _scan(root, now=T)
    assert first["collectable"] == []
    row = next(r for r in first["protected"] if r["id"] == orphan.id)
    assert row["why"] == "first-sighting"
    assert row["collectable_at"] == pytest.approx(T + 30 * DAY)
    assert first["token"] is None

    almost = _scan(root, now=T + 29 * DAY)
    assert almost["collectable"] == []
    assert next(r for r in almost["protected"] if r["id"] == orphan.id)["why"] == "sighting"

    due = _scan(root, now=T + 30 * DAY)
    assert _ids(due) == {orphan.id}
    assert due["token"]


def test_the_grace_period_has_a_floor(root):
    with pytest.raises(ValueError):
        _scan(root, grace_days=image_gc.GRACE_DAYS_MIN - 1, run_id="5" * 32)
    refused = maintenance_reports.read(root, "5" * 32)
    assert refused["state"] == "failed" and refused["error"] == "ValueError"
    orphan = _obj(1)
    _scan(root, now=T, grace_days=image_gc.GRACE_DAYS_MIN)
    report = _scan(root, now=T + 7 * DAY, grace_days=image_gc.GRACE_DAYS_MIN)
    assert _ids(report) == {orphan.id}


def test_a_reachable_sighting_resets_the_clock(root):
    orphan = _obj(1)
    _scan(root, now=T)
    d = _place(root, orphan)
    seen = _scan(root, now=T + 10 * DAY)
    assert orphan.id not in {r["id"] for r in seen["protected"]}
    image_refs.delete(d, "avatar")
    again = _scan(root, now=LATER)
    assert again["collectable"] == []
    assert next(r for r in again["protected"] if r["id"] == orphan.id)["why"] == "first-sighting"
    assert _ids(_scan(root, now=LATER + 30 * DAY)) == {orphan.id}


def test_future_mtimes_are_never_collectable(root):
    ahead = _obj(1, mtime=T + 400 * DAY)
    blob_ahead = _obj(2)
    os.utime(_blob(blob_ahead), (T + 400 * DAY, T + 400 * DAY))
    report = _ready(root)
    assert report["collectable"] == []
    skewed = {r["id"] for r in report["protected"] if r["why"] == "clock-skew"}
    assert skewed == {ahead.id, blob_ahead.id}
    assert {r["id"] for r in report["clock_skew"]} >= {ahead.id, blob_ahead.id}


def test_a_future_sighting_is_flagged_and_restarts(root, monkeypatch):
    orphan = _obj(1)
    _scan(root, now=LATER + 400 * DAY)      # a clock that ran a year ahead
    report = _scan(root, now=LATER)
    assert report["collectable"] == []
    assert orphan.id in {r["id"] for r in report["clock_skew"]}
    assert _ids(_scan(root, now=LATER + 30 * DAY)) == {orphan.id}


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
    assert done["deleted"]["blobs"] == []
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
    assert _ids(report) == {orphan.id}
    assert report["collectable_blobs"] == []
    assert report["unreadable_sidecars"] == [_sidecar(garbled).relative_to(root).as_posix()]
    assert report["reclaimable_bytes"] == _sidecar_size(orphan.id)
    done = _collect(root, report["token"])
    assert done["kept_blobs"] == [{"id": orphan.id, "blob": orphan.blob_sha256 + ".png",
                                   "reason": "unreadable-sidecars"}]
    assert not _sidecar(orphan).exists()
    assert _blob(orphan).is_file() and _blob(stray_blob).is_file() and _blob(garbled).is_file()
    assert _sidecar(garbled).is_file()


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
    assert [r["blob"] for r in done["deleted"]["blobs"]] == [blob.name]
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

def test_a_concurrent_ingest_is_never_left_dangling(root, monkeypatch):
    orphan = _obj(1)
    report = _ready(root)
    assert _ids(report) == {orphan.id}

    real = image_gc._delete_file
    started = threading.Event()
    result: dict = {}

    def ingest():
        started.set()
        result["obj"] = image_store.ingest(_png(1), "png")

    worker = threading.Thread(target=ingest)

    def delete_file(rootp, path):
        # Inside the batch, holding the ingest/GC lock: start an ingest of the
        # same bytes and give it time to queue on that lock.
        if not worker.is_alive() and "obj" not in result:
            worker.start()
            started.wait(5)
            time.sleep(0.3)
        return real(rootp, path)

    monkeypatch.setattr(image_gc, "_delete_file", delete_file)
    _collect(root, report["token"])
    worker.join(10)
    assert not worker.is_alive()
    got = result["obj"]
    assert got.id == orphan.id
    assert _sidecar(orphan).is_file() and _blob(orphan).is_file()
    assert image_store.read_fresh(orphan.id) is not None
    assert image_refs.resolve_ref(image_refs.Ref("x", orphan.id, None)) is not None


def test_an_ingest_that_touched_the_object_after_the_scan_is_skipped(root):
    orphan = _obj(1)
    report = _ready(root)
    image_store.ingest(_png(1), "png")       # touches the sidecar's mtime ...
    os.utime(_sidecar(orphan), (LATER + 1800, LATER + 1800))   # ... after the scan
    done = _collect(root, report["token"], now=LATER + 3600)
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
    late = _collect(root, token, now=LATER + 25 * 3600)
    assert late["state"] == "refused" and late["deleted"]["objects"] == []

    report = _scan(root, now=LATER + 2 * DAY)
    token = report["token"]
    # Another device: the same synced .cache, read by a machine with its own id.
    other = tmp_path_factory.mktemp("machine-b")
    with monkeypatch.context() as m:
        m.setattr(proclock, "lock_dir", lambda: other)
        elsewhere = _collect(root, token, now=LATER + 2 * DAY + 60)
    assert elsewhere["state"] == "refused" and elsewhere["deleted"]["objects"] == []

    done = _collect(root, token, now=LATER + 2 * DAY + 120)
    assert done["state"] == "complete" and len(done["deleted"]["objects"]) == 3
    again = _collect(root, token, now=LATER + 2 * DAY + 180)
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
    # After the scan: a third orphan, as old and as sighted, and `b` placed.
    c = _obj(3)
    state = image_gc.sightings_path(root)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["objects"][c.id] = T - 60 * DAY
    state.write_text(json.dumps(raw), encoding="utf-8")
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
    assert done["deleted"]["objects"] == [] and done["deleted"]["blobs"] == []
    after = _tree(root, skip=(".cache/image-store",)), _tree(other)
    assert after[0] == {k: v for k, v in before[0].items()
                        if not k.startswith(".cache/image-store/")}
    assert after[1] == before[1]


def test_a_root_that_is_not_the_live_one_is_refused(root, tmp_path):
    orphan = _obj(1)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    assert image_gc.scan(other.resolve(), cancel=_never, now=LATER)["state"] == "root-changed"
    done = image_gc.collect(other.resolve(), report["token"], cancel=_never, now=LATER)
    assert done["state"] == "root-changed"
    assert _sidecar(orphan).is_file()


def test_nothing_outside_image_store_is_deleted(root, tmp_path):
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

    report = _scan(root, now=LATER)
    done = image_gc.collect(root, report["token"], cancel=lambda: True,
                            now=LATER + 60, run_id="4" * 32)
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
    _scan(root, now=T)
    report = _scan(root, now=LATER)
    assert report["state"] == "blocked" and report["token"] is None
    assert {"path": variant.relative_to(root).as_posix(), "reason": "case-variant"} \
        in report["blocking"]
    assert _sidecar(hidden).is_file() and _sidecar(orphan).is_file()


def test_an_unarrived_object_keeps_every_orphan_blob(root):
    gone = _obj(1)
    os.unlink(_sidecar(gone))                     # an orphan blob, old enough
    os.utime(_blob(gone), (OLD, OLD))
    report = _ready(root)
    assert [r["blob"] for r in report["collectable_blobs"]] == [_blob(gone).name]

    # A placement synced in whose object has not: its blob may be that one.
    unarrived = "px1-" + "ab" * 32
    image_refs.write(_char_dir(root), "avatar", unarrived)
    held = _scan(root, now=LATER + 60)
    assert held["state"] == "complete"
    assert held["unarrived_objects"] == [unarrived]
    assert held["collectable_blobs"] == [] and held["token"] is None
    assert {"id": None, "blob": _blob(gone).name, "why": "unarrived-objects",
            "collectable_at": None} in held["protected"]

    done = _collect(root, report["token"], now=LATER + 120)
    assert done["deleted"]["blobs"] == []
    assert {"id": None, "blob": _blob(gone).name, "reason": "unarrived-objects"} \
        in done["skipped"]
    assert _blob(gone).is_file()


def test_blobs_without_an_objects_folder_block(root):
    gone = _obj(1)
    blob = _blob(gone)
    shutil.rmtree(image_store.store_root() / "objects")
    os.utime(blob, (OLD, OLD))
    _scan(root, now=T)
    report = _scan(root, now=LATER)
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


def test_a_root_flip_between_sidecar_and_blob_reports_the_sidecar(root, monkeypatch, tmp_path):
    first, second = _obj(1), _obj(2)
    report = _ready(root)
    other = tmp_path / "other-home"
    other.mkdir()
    real = image_gc._delete_file
    deleted: list[Path] = []

    def delete_file(rootp, path):
        freed = real(rootp, path)
        deleted.append(path)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))     # flips after the sidecar
        return freed

    files = {o.id: (_sidecar(o), _blob(o)) for o in (first, second)}   # before the flip
    monkeypatch.setattr(image_gc, "_delete_file", delete_file)
    done = _collect(root, report["token"])
    assert done["state"] == "root-changed"
    assert len(deleted) == 1
    gone = next(o for o in (first, second) if not files[o.id][0].exists())
    kept = first if gone is second else second
    assert deleted == [files[gone.id][0]]
    assert done["deleted"]["objects"] == [
        {"id": gone.id, "blob": gone.blob_sha256 + ".png", "bytes": done["deleted"]["bytes"]}]
    assert done["deleted"]["blobs"] == []
    assert files[gone.id][1].is_file()
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
    assert [r["blob"] for r in done["deleted"]["blobs"]] == [_blob(orphan).name]
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
    assert done["deleted"]["blobs"] == []
    assert not thumb.exists()


def test_blob_keys_are_where_thumbnails_land(root):
    obj = _obj(1)
    made = {thumbs.thumbnail(_blob(obj), w).resolve() for w in thumbs.WIDTHS}
    keys = {(root / k).resolve() for k in thumbs.blob_keys(obj.blob_sha256)}
    assert made <= keys
