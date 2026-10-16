"""The migration's writes: place, verify, fold, then clean up (stage 4, M8-M10).

`image_migration.run(root, dry_run=False, ...)` is the only code in the app
that deletes a legacy image, so every test here is about what it must NOT
lose: a crash at each step leaves legacy-only, both, or new-only; a root that
moves deletes nothing in either tree; an existing placement is never
overwritten; a file or a key changed under the run is never the one deleted.

Fixtures are synthetic (Pillow-drawn) and every name is invented. Every test
runs against a temp `GRIMOIRE_HOME`; none of them touches the frozen campaign
(`test_no_test_migrates_the_frozen_campaign` holds the whole suite to that).
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from grimoire.main import create_app
from grimoire.routes.common import THUMB_BUCKETS
from grimoire.store import (
    assets,
    campaigns,
    characters,
    greeting_images,
    greetings,
    image_collection_imports,
    image_collections,
    image_descriptions,
    image_migration,
    image_refs,
    image_store,
    image_subjects,
    locks,
    paths,
    revision,
    thumbs,
    world_images,
    worlds,
)
from tests.collection_fixtures import format1_journal, place

#: The temp store every test here runs against; `_run` refuses any other.
_HOME: list[Path] = []


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Every run here is against a temp store under `tmp_path`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    _HOME[:] = [home.resolve()]
    yield
    _HOME.clear()


def _img(seed: int = 0, size=(24, 16)) -> Image.Image:
    im = Image.new("RGB", size)
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y + seed) % 256)
                for y in range(size[1]) for x in range(size[0])])
    return im


def _png(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    _img(seed).save(buf, "PNG")
    return buf.getvalue()


def _world(name: str = "Realm") -> tuple[str, Path, str, Path]:
    wid = worlds.create_world(name)
    wroot = worlds.world_root(wid)
    char, vid = characters.create_character(wroot, "Seraphine", "default")
    return wid, wroot, char, assets.version_dir(wroot, char, vid)


def _campaign(wid: str) -> tuple[str, Path]:
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, campaigns.campaign_root(cid)


def _legacy(d: Path, filename: str, data: bytes) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_bytes(data)
    return p


def _sidecar(d: Path, filename: str, payload: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _root() -> Path:
    return paths.home().resolve()


def _run(**kw) -> dict:
    root = _root()
    assert _HOME and root == _HOME[0], "a run here only ever migrates the test's own store"
    return image_migration.run(root, dry_run=kw.pop("dry_run", False), **kw)


def _id(data: bytes) -> str:
    return image_store.identify(data, "png")


def _tree(top: Path) -> dict[str, tuple[int, int]]:
    """Every file under `top` with its size and mtime: what "nothing changed
    here" is compared against."""
    out: dict[str, tuple[int, int]] = {}
    for dirpath, _dirs, files in os.walk(top):
        for f in files:
            p = Path(dirpath) / f
            st = p.lstat()
            out[p.relative_to(top).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def _texts(image_id: str) -> set[str]:
    raw = image_store.read_fresh(image_id).raw
    got = {c["text"] for c in raw.get("description_conflicts", [])}
    if isinstance(raw.get("description"), str):
        got.add(raw["description"])
    return got


def _subjects_of(image_id: str, scope: str) -> set[str]:
    raw = image_store.read_fresh(image_id).raw
    return {a["id"] for a in raw.get("associations", []) if a.get("scope") == scope}


# ---- the whole pass -----------------------------------------------------------

def test_migration_places_verifies_folds_and_cleans():
    wid, wroot, char, wchar = _world()
    _cid, croot = _campaign(wid)
    avatar = _legacy(wchar, "avatar.png", _png(1))
    _sidecar(wchar, "descriptions.json", {"avatar": "A tall figure in grey"})
    _sidecar(wchar, "focus.json", {"avatar": 40})
    lib = croot / "assets" / "images"
    quay = _legacy(lib, "quay.png", _png(2))
    _sidecar(lib, "descriptions.json", {"quay": "A wet quay at dusk"})

    rep = _run()

    assert rep["outcome"] == "done" and rep["errors"] == []
    assert rep["placed"] == 2 and rep["legacy_deleted"] == 2
    assert not avatar.exists() and not quay.exists()
    ref = image_refs.read(wchar, "avatar")
    assert ref is not None and ref.image == _id(_png(1)) and ref.focus == 40
    assert image_refs.read(lib, "quay").image == _id(_png(2))
    assert image_store.read_fresh(ref.image).raw["description"] == "A tall figure in grey"
    assert image_store.read_fresh(_id(_png(2))).raw["description"] == "A wet quay at dusk"
    # Every sidecar emptied by the fold is gone, and so is the legacy crop.
    assert not (wchar / "descriptions.json").exists()
    assert not (wchar / "focus.json").exists()
    assert not (lib / "descriptions.json").exists()
    assert rep["focus_moved"] == 1 and rep["descriptions_folded"] == 2
    # What a reader sees is unchanged.
    assert assets.read_focus(wroot, char, "default") == 40
    assert image_descriptions.text_in(lib, "quay") == "A wet quay at dusk"
    assert not image_migration.work_map_path(_root()).exists()


def test_a_dry_run_writes_nothing_and_has_the_real_runs_shape(tmp_path):
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    before = _tree(tmp_path)

    dry = _run(dry_run=True)

    assert _tree(tmp_path) == before
    assert dry["dry_run"] is True and dry["outcome"] == "done" and dry["placed"] == 0
    real = _run()
    assert set(real) == set(dry)


# ---- crashes and the root ------------------------------------------------------

class _CrashError(OSError):
    pass


def _crash(*_a, **_k):
    raise _CrashError("injected")


def test_crash_before_ref_write_leaves_legacy_only(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    _sidecar(wchar, "descriptions.json", {"avatar": "Grey"})
    real = image_migration._write_placement
    monkeypatch.setattr(image_migration, "_write_placement", _crash)

    rep = _run()

    assert [e["path"] for e in rep["errors"]] == [image_migration.MigrationPlan(_root()).rel(
        avatar.resolve())]
    assert avatar.read_bytes() == _png(1)
    assert image_refs.read(wchar, "avatar") is None
    assert _json(wchar / "descriptions.json") == {"avatar": "Grey"}

    monkeypatch.setattr(image_migration, "_write_placement", real)
    assert _run()["legacy_deleted"] == 1
    assert not avatar.exists() and image_refs.read(wchar, "avatar") is not None


def test_crash_after_ref_write_leaves_both_and_rerun_cleans(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    _sidecar(wchar, "descriptions.json", {"avatar": "Grey"})
    real = image_migration._unlink_same
    monkeypatch.setattr(image_migration, "_unlink_same", _crash)

    rep = _run()

    assert rep["errors"]
    assert avatar.exists()
    assert image_refs.read(wchar, "avatar").image == _id(_png(1))
    assert _json(wchar / "descriptions.json") == {"avatar": "Grey"}

    monkeypatch.setattr(image_migration, "_unlink_same", real)
    again = _run()
    assert again["already_placed"] == 1 and again["legacy_deleted"] == 1
    assert not avatar.exists() and not (wchar / "descriptions.json").exists()
    assert _texts(_id(_png(1))) == {"Grey"}


def test_crash_after_cleanup_is_new_only(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    _sidecar(wchar, "descriptions.json", {"avatar": "Grey"})
    real = image_migration._fold_description
    monkeypatch.setattr(image_migration, "_fold_description", _crash)

    rep = _run()

    assert rep["errors"] and rep["legacy_deleted"] == 1
    assert not avatar.exists()
    # The key still answers for the picture (R1) until a rerun folds it.
    assert image_descriptions.text_in(wchar, "avatar") == "Grey"

    monkeypatch.setattr(image_migration, "_fold_description", real)
    again = _run()
    assert again["metadata_only"]["c"] == 1 and again["descriptions_folded"] == 1
    assert not (wchar / "descriptions.json").exists()
    assert _texts(_id(_png(1))) == {"Grey"}


def _other_root(tmp_path: Path) -> tuple[Path, Path]:
    """A second store with a legacy file of its own."""
    other = tmp_path / "other"
    d = other / "worlds" / "elsewhere" / "characters" / "mara" / "assets" / "default"
    return other.resolve(), _legacy(d, "avatar.png", _png(7))


def test_a_root_flip_deletes_nothing_in_either_root(tmp_path, monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    other, theirs = _other_root(tmp_path)
    before_other = _tree(other)
    real = image_store.ingest

    def flip(data, ext, **kw):
        obj = real(data, ext, **kw)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        return obj
    monkeypatch.setattr(image_migration.image_store, "ingest", flip)
    root = _root()

    rep = image_migration.run(root, dry_run=False)

    assert rep["outcome"] == "root-changed" and rep["stopped_at"] == "after-ingest"
    assert avatar.read_bytes() == _png(1)
    assert image_refs.read(wchar, "avatar") is None
    assert theirs.read_bytes() == _png(7) and _tree(other) == before_other


def test_a_flip_and_flip_back_between_ingest_and_delete_deletes_nothing(tmp_path, monkeypatch):
    """The ingest lands in the other tree and the root comes back before the
    next check: the pinned verify finds no object under its own root."""
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    other, theirs = _other_root(tmp_path)
    home = str(_root())
    real = image_store.ingest

    def flip_and_back(data, ext, **kw):
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        try:
            return real(data, ext, **kw)
        finally:
            monkeypatch.setenv("GRIMOIRE_HOME", home)
    monkeypatch.setattr(image_migration.image_store, "ingest", flip_and_back)

    rep = _run()

    assert rep["legacy_deleted"] == 0
    assert {s["reason"] for s in rep["skipped"]} == {"not-under-root"}
    assert avatar.read_bytes() == _png(1)
    assert image_refs.read(wchar, "avatar") is None
    assert theirs.read_bytes() == _png(7)


def test_the_run_stops_when_the_root_is_not_the_live_one(tmp_path):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    other, theirs = _other_root(tmp_path)

    rep = image_migration.run(other, dry_run=False)

    assert rep["outcome"] == "root-changed"
    assert avatar.exists() and theirs.exists()


# ---- locks ----------------------------------------------------------------------

def test_world_library_migration_takes_the_collection_lock(monkeypatch):
    wid, wroot, _char, _wchar = _world()
    lib = wroot / "assets" / world_images.DIRNAME
    _legacy(lib, "harbour.png", _png(3))
    held = []
    real = image_migration._write_placement

    def spy(d, name, image_id, focus):
        held.append(locks.image_collection_lock(wid)._is_owned())
        return real(d, name, image_id, focus)
    monkeypatch.setattr(image_migration, "_write_placement", spy)

    assert _run()["placed"] == 1
    assert held == [True]


def test_focus_beside_a_placement_is_dropped_not_folded():
    _wid, wroot, char, wchar = _world()
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    assets.write_focus(wroot, char, "default", 10)
    _sidecar(wchar, "focus.json", {"avatar": 70})

    rep = _run()

    assert rep["metadata_only"]["d"] == 1 and rep["focus_dropped"] == 1
    assert not (wchar / "focus.json").exists()
    assert image_refs.read(wchar, "avatar").focus == 10


def test_campaign_bare_focus_becomes_an_override_under_the_avatar_lock(monkeypatch):
    wid, wroot, char, _wchar = _world()
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    cid, croot = _campaign(wid)
    cdir = assets.version_dir(croot, char, "default")
    _sidecar(cdir, "focus.json", {"avatar": 30})
    seen = []
    real = image_refs.write

    def spy(d, name, image, *, focus=None):
        if Path(d) == cdir:
            seen.append((locks.image_name_lock(cdir, "avatar")._is_owned(),
                         locks.campaign_lock(cid)._is_owned(), image, focus))
        return real(d, name, image, focus=focus)
    monkeypatch.setattr(image_migration.image_refs, "write", spy)

    rep = _run()

    assert rep["overrides_written"] == 1
    assert seen == [(True, True, None, 30)]
    assert image_refs.read(cdir, "avatar") == image_refs.Ref("avatar", None, 30)
    assert not (cdir / "focus.json").exists()
    assert assets.read_focus(croot, char, "default") == 30


def test_focus_moves_to_its_placement_and_campaign_focus_to_an_override():
    wid, wroot, char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    _sidecar(wchar, "focus.json", {"avatar": 25})
    _cid, croot = _campaign(wid)
    cdir = assets.version_dir(croot, char, "default")
    _sidecar(cdir, "focus.json", {"avatar": 60})

    rep = _run()

    assert rep["focus_moved"] == 1 and rep["overrides_written"] == 1
    assert image_refs.read(wchar, "avatar").focus == 25
    assert image_refs.read(cdir, "avatar") == image_refs.Ref("avatar", None, 60)
    assert not (wchar / "focus.json").exists() and not (cdir / "focus.json").exists()
    assert assets.read_focus(wroot, char, "default") == 25
    assert assets.read_focus(croot, char, "default") == 60


# ---- existing data is never overwritten ----------------------------------------

def test_a_legacy_file_differing_from_an_existing_placement_is_kept_and_reported():
    _wid, wroot, char, wchar = _world()
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    stale = _legacy(wchar, "avatar.png", _png(2))

    rep = _run()

    assert stale.read_bytes() == _png(2)
    assert image_refs.read(wchar, "avatar").image == _id(_png(1))
    assert [d["reason"] for d in rep["legacy_differs"]] == ["differs"]
    assert rep["legacy_deleted"] == 0


def test_an_existing_unarrived_placement_is_never_overwritten():
    _wid, _wroot, _char, wchar = _world()
    absent = "px1-" + "ab" * 32
    image_refs.write(wchar, "gallery_1", absent)
    legacy = _legacy(wchar, "gallery_1.png", _png(2))

    rep = _run()

    assert legacy.read_bytes() == _png(2)
    assert image_refs.read(wchar, "gallery_1").image == absent
    assert [d["reason"] for d in rep["legacy_differs"]] == ["not-available"]
    assert rep["placed"] == 0


# ---- concurrent changes ----------------------------------------------------------

def test_a_file_changed_after_hashing_is_skipped(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    real = image_migration.plan

    def plan_then_edit(root, **kw):
        got = real(root, **kw)
        avatar.write_bytes(_png(5))
        os.utime(avatar, ns=(1, 1))
        return got
    monkeypatch.setattr(image_migration, "plan", plan_then_edit)

    rep = _run()

    assert {s["reason"] for s in rep["skipped"]} == {"changed-since-hashing"}
    assert avatar.read_bytes() == _png(5)
    assert image_refs.read(wchar, "avatar") is None


def test_an_edit_during_the_fold_is_not_lost(monkeypatch):
    """A description and a subject list rewritten while their fold is in
    flight: the compare-and-delete sees the new value and folds it too."""
    _wid, wroot, char, _wchar = _world()
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body="Come in.")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    _legacy(gdir, "art_1.png", _png(4))
    _sidecar(gdir, "descriptions.json", {"art_1": "Original"})
    _sidecar(gdir, "subjects.json", {"art_1": ["seraphine"]})
    calls = {"text": 0, "subjects": 0}
    real_text, real_subjects = image_migration._with_text, image_migration._with_subjects

    # Each edit lands inside ITS OWN fold's `update` -- after the key was
    # read, before its compare-and-delete -- as a sync client writing the
    # sidecar behind the lock would.
    def text_edited(raw, value, label, state):
        calls["text"] += 1
        if calls["text"] == 1:
            _sidecar(gdir, "descriptions.json", {"art_1": "Edited"})
        return real_text(raw, value, label, state)

    def subjects_edited(raw, scope, cids):
        calls["subjects"] += 1
        if calls["subjects"] == 1:
            _sidecar(gdir, "subjects.json", {"art_1": ["seraphine", "mara"]})
        return real_subjects(raw, scope, cids)
    monkeypatch.setattr(image_migration, "_with_text", text_edited)
    monkeypatch.setattr(image_migration, "_with_subjects", subjects_edited)

    rep = _run()

    image_id = _id(_png(4))
    assert rep["errors"] == []
    assert "Edited" in _texts(image_id)
    assert _subjects_of(image_id, f"world:{wroot.name}") >= {"seraphine", "mara"}
    for f in ("descriptions.json", "subjects.json"):
        left = _json(gdir / f) if (gdir / f).exists() else {}
        assert "art_1" not in left
    # Both compare-and-deletes saw the edit and folded again.
    assert calls == {"text": 2, "subjects": 2}


def test_a_concurrent_upload_during_migration_is_kept(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "gallery_1.png", _png(1))
    real = image_store.ingest
    uploaded = []

    def upload_meanwhile(data, ext, **kw):
        obj = real(data, ext, **kw)
        if not uploaded:
            uploaded.append(True)
            assets.put_in(wchar, "gallery_1", _png(9), "png")
        return obj
    monkeypatch.setattr(image_migration.image_store, "ingest", upload_meanwhile)

    rep = _run()

    assert image_refs.read(wchar, "gallery_1").image == _id(_png(9))
    assert rep["placed"] == 0
    assert rep["skipped"] or rep["legacy_differs"]


def test_rerun_is_idempotent_and_keeps_existing_conflicts():
    wid, wroot, _char, wchar = _world()
    _cid, croot = _campaign(wid)
    other = assets.version_dir(wroot, characters.create_character(wroot, "Mara", "default")[0],
                               "default")
    _legacy(wchar, "gallery_1.png", _png(3))
    _sidecar(wchar, "descriptions.json", {"gallery_1": "A lantern"})
    _legacy(other, "gallery_2.png", _png(3))
    _sidecar(other, "descriptions.json", {"gallery_2": "A lamp"})

    first = _run()
    image_id = _id(_png(3))
    raw = image_store.read_fresh(image_id).raw
    assert first["errors"] == [] and _texts(image_id) == {"A lantern", "A lamp"}
    assert "description" not in raw and len(raw["description_conflicts"]) == 2

    again = _run()

    assert again["placed"] == 0 and again["legacy_deleted"] == 0 and again["errors"] == []
    assert image_store.read_fresh(image_id).raw == raw
    assert croot.exists()


# ---- metadata-only folds -----------------------------------------------------------

def test_overcap_and_non_string_keys_stay():
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    _legacy(wchar, "gallery_1.png", _png(2))
    long = "x" * (image_store.MAX_DESCRIPTION + 1)
    _sidecar(wchar, "descriptions.json", {"avatar": ["not", "text"], "gallery_1": long})

    rep = _run()

    assert rep["legacy_deleted"] == 2
    assert _json(wchar / "descriptions.json") == {"avatar": ["not", "text"], "gallery_1": long}
    assert sorted(k["reason"] for k in rep["keys_kept"]) == ["not-a-string", "too-long"]


def test_metadata_only_keys_fold_only_into_verified_targets():
    wid, _wroot, char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    absent = "px1-" + "cd" * 32
    image_refs.write(wchar, "gallery_2", absent)
    _sidecar(wchar, "descriptions.json", {"gallery_2": "Never arrived"})
    _cid, croot = _campaign(wid)
    cdir = assets.version_dir(croot, char, "default")
    _sidecar(cdir, "descriptions.json", {"avatar": "Seen from the campaign"})

    rep = _run()

    # (a): the world avatar migrated and verified, so the campaign key folds.
    assert _texts(_id(_png(1))) == {"Seen from the campaign"}
    assert not (cdir / "descriptions.json").exists()
    # (c): the placement never arrived, so its key stays where it is.
    assert _json(wchar / "descriptions.json") == {"gallery_2": "Never arrived"}
    assert {"reason": "target-not-verified", "key": "gallery_2"}.items() <= next(
        k for k in rep["keys_kept"] if k["key"] == "gallery_2").items()


def test_cross_world_subject_keys_stay_in_the_sidecar():
    wid, wroot, char, _wchar = _world()
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body="Come in.")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    _legacy(gdir, "art_1.png", _png(4))
    cross = "/api/worlds/winifred/images/harbour"
    same = f"/api/worlds/{wid}/images/quay"
    _sidecar(gdir, "subjects.json", {"art_1": [char], cross: [char], same: [char]})

    rep = _run()

    assert _json(gdir / "subjects.json") == {cross: [char], same: [char]}
    assert _subjects_of(_id(_png(4)), f"world:{wroot.name}") == {char}
    assert rep["url_subject_keys_kept"] == 1


def test_campaigns_written_are_revision_bumped():
    wid, _wroot, _char, _wchar = _world()
    cid, croot = _campaign(wid)
    _legacy(croot / "assets" / "images", "quay.png", _png(2))
    before = revision.current(cid)

    rep = _run()

    assert rep["campaigns_written"] == 1
    assert revision.current(cid) != before


def test_a_campaign_write_stopped_part_way_still_bumps_its_revision(monkeypatch):
    """The placement is written, then the step is stopped (a busy lock before
    the legacy file goes): the campaign's files changed, so its token must
    move even though the step never returned."""
    wid, _wroot, _char, _wchar = _world()
    cid, croot = _campaign(wid)
    library = croot / "assets" / "images"
    quay = _legacy(library, "quay.png", _png(2))
    before = revision.current(cid)

    def busy(*_a, **_k):
        raise locks.StoreBusy("image")
    monkeypatch.setattr(image_migration, "_unlink_same", busy)

    rep = _run()

    assert [e["error"] for e in rep["errors"]] == ["StoreBusy"]
    assert image_refs.read(library, "quay") is not None and quay.exists()
    assert revision.current(cid) != before
    assert rep["campaigns_written"] == 1


def test_legacy_thumbnails_are_removed():
    assert thumbs.WIDTHS == THUMB_BUCKETS
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    made = thumbs.thumbnail(avatar, 128)
    assert made is not None and made.exists()
    st = avatar.stat()
    rel = avatar.relative_to(paths.home()).as_posix()
    keys = thumbs.legacy_keys(rel, st)
    assert made.relative_to(paths.home()) in keys
    # The other encoder's entry, as another device would have written it.
    sibling = paths.home() / next(k for k in keys if "jpeg" in k.parts[2])
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(b"thumb")

    rep = _run()

    assert rep["thumbnails_removed"] == 2
    assert not made.exists() and not sibling.exists()


@pytest.mark.parametrize("cache_operation", ["stat", "unlink"])
def test_an_undeletable_thumbnail_does_not_block_migration(monkeypatch, cache_operation):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    made = thumbs.thumbnail(avatar, 128)
    _sidecar(wchar, "descriptions.json", {"avatar": "A portrait."})
    rel = avatar.relative_to(_root()).as_posix()
    keys = thumbs.legacy_keys(rel, avatar.stat())
    sibling = _root() / next(k for k in keys if "jpeg" in k.parts[2])
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(b"thumb")
    cache_api = os if cache_operation == "stat" else Path
    real = getattr(cache_api, cache_operation)
    real_is_file = Path.is_file

    def inaccessible_is_file(p, *a, **kw):
        # 3.14 predicates hide access errors, and the Windows fast path can
        # bypass os.stat/Path.stat entirely. Model the same denied entry at
        # both interfaces: the predicate says false; an explicit stat fails.
        return False if p == made else real_is_file(p, *a, **kw)

    def deny_one_cache_entry(p, *a, **kw):
        if isinstance(p, (str, os.PathLike)) and Path(p) == made:
            raise PermissionError(errno.EACCES, "cache entry cannot be accessed")
        return real(p, *a, **kw)

    with monkeypatch.context() as cache:
        cache.setattr(cache_api, cache_operation, deny_one_cache_entry)
        if cache_operation == "stat":
            cache.setattr(Path, "is_file", inaccessible_is_file)
        rep = _run()

    assert rep["outcome"] == "done" and rep["legacy_deleted"] == 1
    assert made.exists() and not sibling.exists() and not avatar.exists()
    assert rep["thumbnails_removed"] == 1
    assert _texts(_id(_png(1))) == {"A portrait."}
    assert rep["errors"] == [{"path": made.relative_to(_root()).as_posix(),
                              "error": "PermissionError [EACCES]"}]


# ---- collections and journals ----------------------------------------------------------

_COLLECTION = "0123456789abcdef0123456789abcdef"


def _member_file(lib: Path, data: bytes) -> str:
    name = image_collections.MEMBER_PREFIX + hashlib.sha256(data).hexdigest()
    _legacy(lib, f"{name}.png", data)
    return name


def _manifest(wroot: Path, members: list[str]) -> Path:
    p = wroot / "assets" / "image-collections" / f"{_COLLECTION}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"format": 1, "members": members}), encoding="utf-8")
    return p


def test_format_1_manifest_converts_keeping_indices_library_urls_and_tags():
    wid, wroot, char, _wchar = _world()
    lib = wroot / "assets" / world_images.DIRNAME
    names = [_member_file(lib, _png(11)), _member_file(lib, _png(12))]
    manifest = _manifest(wroot, names)
    pool = f"![Pool](/api/worlds/{wid}/image-collections/{_COLLECTION}/image)"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=pool)
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    _sidecar(gdir, "subjects.json", {f"/api/worlds/{wid}/images/{names[0]}": [char]})
    # Another world shows the same pool: its tag stays with its own greeting.
    wid2, wroot2, char2, _ = _world("Winifred")
    gid2 = greetings.create_greeting(wroot2, "The Fair", char2, "default", body=pool)
    gdir2 = assets.version_dir(wroot2, gid2, "default", base="greetings")
    _sidecar(gdir2, "subjects.json", {f"/api/worlds/{wid}/images/{names[1]}": [char2]})

    rep = _run()

    ids = [_id(_png(11)), _id(_png(12))]
    assert _json(manifest) == {"format": 2, "members": ids}
    assert rep["collections_converted"] == [manifest.resolve().relative_to(_root()).as_posix()]
    # Library placements stay, so library URLs keep serving.
    for name, image_id in zip(names, ids, strict=True):
        assert image_refs.read(lib, name).image == image_id
        assert assets.path_in(lib, name, supported_only=True) is not None
    member = [f"/api/worlds/{wid}/image-collections/{_COLLECTION}/members/{n}" for n in (0, 1)]
    assert image_subjects.read_subjects(wroot, gid) == {member[0]: [char]}
    assert image_subjects.read_subjects(wroot2, gid2) == {member[1]: [char2]}
    assert _json(gdir2 / "subjects.json") == {member[1]: [char2]}
    assert not (gdir / "subjects.json").exists()
    assert not image_collections.has_format1(wid) and wid2


def test_a_run_stopped_after_a_conversion_is_settled_by_a_rerun(monkeypatch):
    """The manifest is format 2 and the greeting key still names the library
    URL: a rerun, which no longer sees a format-1 manifest, still drops it."""
    wid, wroot, char, _wchar = _world()
    lib = wroot / "assets" / world_images.DIRNAME
    names = [_member_file(lib, _png(11))]
    manifest = _manifest(wroot, names)
    pool = f"![Pool](/api/worlds/{wid}/image-collections/{_COLLECTION}/image)"
    wid2, wroot2, char2, _ = _world("Winifred")
    gid2 = greetings.create_greeting(wroot2, "The Fair", char2, "default", body=pool)
    gdir2 = assets.version_dir(wroot2, gid2, "default", base="greetings")
    lib_key = f"/api/worlds/{wid}/images/{names[0]}"
    member_key = f"/api/worlds/{wid}/image-collections/{_COLLECTION}/members/0"
    _sidecar(gdir2, "subjects.json", {lib_key: [char2]})
    real = image_migration._settle_converted
    monkeypatch.setattr(image_migration, "_settle_converted", _crash)

    first = _run()

    assert first["errors"] and _json(manifest)["format"] == 2
    assert _json(gdir2 / "subjects.json") == {lib_key: [char2], member_key: [char2]}
    assert image_subjects.read_subjects(wroot2, gid2) == {member_key: [char2]}

    monkeypatch.setattr(image_migration, "_settle_converted", real)
    again = _run()

    assert again["errors"] == [] and again["collections_converted"] == [] and wid2
    assert _json(gdir2 / "subjects.json") == {member_key: [char2]}
    assert char


def test_a_member_whose_sha_mismatches_its_name_keeps_format_1():
    _wid, wroot, _char, _wchar = _world()
    lib = wroot / "assets" / world_images.DIRNAME
    good = _member_file(lib, _png(11))
    bad = image_collections.MEMBER_PREFIX + hashlib.sha256(b"other bytes").hexdigest()
    stray = _legacy(lib, f"{bad}.png", _png(12))
    manifest = _manifest(wroot, [good, bad])

    rep = _run()

    assert _json(manifest)["format"] == 1
    assert stray.read_bytes() == _png(12)
    assert {"path": manifest.resolve().relative_to(_root()).as_posix(),
            "reason": "member-not-placed"} in rep["collections_kept"]
    assert any(u["reason"] == "member-differs-from-name" for u in rep["untouched"])


def test_journals_convert_or_retire_out_of_the_glob():
    wid, _wroot, _char, _wchar = _world()
    names = place(wid, _png(21))
    converts = format1_journal(wid, "https://example.test/a", "a" * 32, names)
    retires = format1_journal(wid, "https://example.test/b", "b" * 32,
                              [image_collections.MEMBER_PREFIX + "0" * 64])
    stays = format1_journal(wid, "https://example.test/c", "c" * 32,
                            [image_collections.MEMBER_PREFIX + "1" * 64], accepted=True)

    rep = _run()

    assert rep["journals"] == {"converted": 1, "retired": 1, "kept": 1}
    assert _json(converts)["format"] == 2
    assert not retires.exists()
    assert retires.with_name(retires.name + image_collection_imports.RETIRED_SUFFIX).exists()
    assert _json(stays)["format"] == 1


def test_a_cancel_while_journals_settle_keeps_the_rest_and_the_map():
    """Codex review, P2c: the journal pass is asked between journals, and a
    cancel there is a cancelled run -- the work map stays and the journals not
    yet reached are untouched."""
    wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    journals = [format1_journal(wid, f"https://example.test/{n}", n * 32,
                                [image_collections.MEMBER_PREFIX + n * 64])
                for n in ("a", "b", "c")]
    before = {p: p.read_bytes() for p in journals}

    def cancel() -> bool:
        return any(not p.exists() for p in journals)

    rep = _run(cancel=cancel)

    assert rep["outcome"] == "cancelled"
    assert rep["journals"]["retired"] == 1
    assert sum(not p.exists() for p in journals) == 1
    assert all(p.read_bytes() == before[p] for p in journals if p.exists())
    assert image_migration.work_map_path(_root()).exists()


# ---- sidecars, the work map, reports ---------------------------------------------------------

def test_emptied_sidecars_are_deleted():
    _wid, wroot, char, _wchar = _world()
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body="Come in.")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    _legacy(gdir, "art_1.png", _png(4))
    _sidecar(gdir, "descriptions.json", {"art_1": "Lanterns"})
    _sidecar(gdir, "subjects.json", {"art_1": [char]})

    rep = _run()

    assert rep["subjects_folded"] == 1 and rep["descriptions_folded"] == 1
    assert not (gdir / "descriptions.json").exists()
    assert not (gdir / "subjects.json").exists()


def test_a_symlinked_sidecar_is_left_and_reported(tmp_path):
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"avatar": "Elsewhere"}), encoding="utf-8")
    (wchar / "descriptions.json").symlink_to(outside)

    rep = _run()

    assert (wchar / "descriptions.json").is_symlink()
    assert _json(outside) == {"avatar": "Elsewhere"}
    assert any(k["reason"] == "symlinked-sidecar" for k in rep["keys_kept"])


class _Died(BaseException):
    """A process that dies mid-item: nothing in `run` may catch it."""


def test_the_work_map_holds_only_pending_entries_and_is_removed_on_completion(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    _legacy(wchar, "gallery_1.png", _png(2))
    seen = []
    real = image_migration._write_placement

    def spy(d, name, image_id, focus):
        seen.append(image_migration.pending_ids(_root()))
        return real(d, name, image_id, focus)
    monkeypatch.setattr(image_migration, "_write_placement", spy)

    _run()

    # Each write saw exactly its own image pending; the verified one was gone.
    assert sorted(seen, key=sorted) == sorted([{_id(_png(1))}, {_id(_png(2))}], key=sorted)
    assert not image_migration.work_map_path(_root()).exists()
    assert image_migration.pending_ids(_root()) == set()


def test_a_run_that_dies_mid_item_leaves_its_ingested_object_pending(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))

    def die(*_a, **_k):
        raise _Died
    monkeypatch.setattr(image_migration, "_write_placement", die)

    with pytest.raises(_Died):
        _run()

    assert avatar.exists()
    assert image_migration.pending_ids(_root()) == {_id(_png(1))}


def test_a_garbled_work_map_is_an_error_for_its_readers():
    p = image_migration.work_map_path(_root())
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        image_migration.pending_ids(_root())


def test_a_failed_run_still_reports(monkeypatch):
    _world()

    def boom(*_a, **_k):
        raise RuntimeError("planner fell over")
    monkeypatch.setattr(image_migration, "plan", boom)

    rep = _run()

    # The type only: a message is free text, and an OS error's names a path.
    assert rep["outcome"] == "failed" and rep["error"] == "RuntimeError"
    assert "legacy_files" in rep and rep["placed"] == 0


def test_a_failure_is_reported_by_type_and_never_by_its_path(monkeypatch):
    """M2: a report holds relative paths only. An OS error's message carries the
    absolute path it failed on, so neither a step's error nor a run's says
    more than the exception's type and errno."""
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))

    def denied(*_a, **_k):
        raise PermissionError(errno.EACCES, "Permission denied", str(avatar.resolve()))
    monkeypatch.setattr(image_migration, "_write_placement", denied)

    rep = _run()

    assert [e["error"] for e in rep["errors"]] == ["PermissionError [EACCES]"]
    assert str(_root()) not in json.dumps(rep)

    monkeypatch.setattr(image_migration, "plan", denied)
    failed = _run()
    assert failed["outcome"] == "failed" and failed["error"] == "PermissionError [EACCES]"
    assert str(_root()) not in json.dumps(failed)


def test_a_cancelled_run_still_reports():
    _wid, _wroot, _char, wchar = _world()
    first = _legacy(wchar, "avatar.png", _png(1))
    second = _legacy(wchar, "gallery_1.png", _png(2))
    asked = []

    def cancel() -> bool:
        asked.append(True)
        return image_refs.read(wchar, "avatar") is not None or image_refs.read(
            wchar, "gallery_1") is not None

    rep = _run(cancel=cancel)

    assert rep["outcome"] == "cancelled" and rep["placed"] == 1
    assert first.exists() != second.exists()


def test_a_real_run_reports_the_bytes_it_freed_not_the_plan():
    """Codex review, P2d: three copies of one picture plan to free two of them.
    A run stopped after the first is placed freed one copy's bytes less the
    blob that replaced it -- fewer than planned -- and a whole run frees what
    was planned."""
    _wid, _wroot, _char, wchar = _world()
    copies = [_legacy(wchar, f"{n}.png", _png(1)) for n in ("avatar", "gallery_1", "gallery_2")]
    planned = _run(dry_run=True)["bytes_reclaimed"]
    assert planned > 0

    def cancel() -> bool:
        return sum(not p.exists() for p in copies) >= 1

    partial = _run(cancel=cancel)

    assert partial["outcome"] == "cancelled" and partial["legacy_deleted"] == 1
    assert partial["bytes_reclaimed"] < planned
    assert partial["bytes_reclaimed"] == max(0, len(_png(1)) - len(image_store.blob_path(
        image_store.read_fresh(_id(_png(1))).blob_sha256, "png").read_bytes()))

    rest = _run()
    assert rest["outcome"] == "done" and rest["legacy_deleted"] == 2
    # The blob was already there, so the rerun frees both remaining copies whole.
    assert rest["bytes_reclaimed"] == 2 * len(_png(1))


@pytest.mark.parametrize("linked", ["refs-folder", "ref-file"])
def test_a_symlinked_placement_is_never_written_through(tmp_path, linked):
    """`image-refs/` (or the placement file in it) linked outside the pinned
    root: the placement would land there and the legacy file go behind it."""
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked == "refs-folder":
        (wchar / image_refs.REFS_DIR).symlink_to(outside, target_is_directory=True)
    else:
        (wchar / image_refs.REFS_DIR).mkdir()
        (wchar / image_refs.REFS_DIR / "avatar.json").symlink_to(outside / "avatar.json")

    rep = _run()

    assert list(outside.iterdir()) == []
    assert avatar.read_bytes() == _png(1)
    assert rep["legacy_deleted"] == 0 and rep["placed"] == 0
    assert {s["reason"] for s in rep["skipped"]} == {"symlinked-directory"}


def test_an_unreadable_legacy_focus_is_kept(monkeypatch):
    """The crop could not be read when the placement was written: the file
    is its only copy, so it stays."""
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))
    focus = _sidecar(wchar, "focus.json", {"avatar": 40})
    monkeypatch.setattr(image_migration, "_focus_file", lambda d: None)   # EACCES, say

    rep = _run()

    assert not avatar.exists() and image_refs.read(wchar, "avatar").focus is None
    assert _json(focus) == {"avatar": 40}
    assert any(k["reason"] == "focus-not-moved" for k in rep["keys_kept"])
    assert rep["focus_moved"] == 0 and rep["focus_dropped"] == 0


def test_a_root_flip_during_a_conversion_leaves_the_manifest_format_1(tmp_path, monkeypatch):
    wid, wroot, _char, _wchar = _world()
    lib = wroot / "assets" / world_images.DIRNAME
    manifest = _manifest(wroot, [_member_file(lib, _png(11))])
    other, _theirs = _other_root(tmp_path)
    real = image_migration._rekey

    def flip(ex, *a, **kw):
        real(ex, *a, **kw)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
    monkeypatch.setattr(image_migration, "_rekey", flip)

    rep = _run()

    assert rep["outcome"] == "root-changed" and rep["stopped_at"] == "before-manifest"
    assert _json(manifest)["format"] == 1 and wid


def test_a_root_flip_before_a_journal_retires_keeps_it(tmp_path, monkeypatch):
    wid, _wroot, _char, _wchar = _world()
    journal = format1_journal(wid, "https://example.test/b", "b" * 32,
                              [image_collections.MEMBER_PREFIX + "0" * 64])
    other, _theirs = _other_root(tmp_path)

    def unmappable(*_a, **_k):
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        raise image_collections.CollectionInvalidError("collection member is missing")
    monkeypatch.setattr(image_collection_imports, "_read", unmappable)

    rep = _run()

    assert rep["outcome"] == "root-changed" and rep["stopped_at"] == "before-journal-retire"
    assert journal.exists()


# ---- deferred stage-4 edges (#478) ---------------------------------------------------


@pytest.mark.parametrize("legacy", ["unsupported", "undecodable", "different-pixels"])
def test_untouched_siblings_do_not_hide_placement_metadata(legacy):
    _wid, _wroot, _char, wchar = _world()
    assets.put_in(wchar, "gallery_1", _png(1), "png")
    suffix, data = {"unsupported": ("txt", b"text"),
                    "undecodable": ("png", b"garbage"),
                    "different-pixels": ("png", _png(2))}[legacy]
    sibling = _legacy(wchar, f"gallery_1.{suffix}", data)
    side = _sidecar(wchar, "descriptions.json", {"gallery_1": "A portrait."})

    rep = _run()

    assert _texts(_id(_png(1))) == {"A portrait."}
    assert not side.exists() and sibling.read_bytes() == data
    assert rep["legacy_deleted"] == 0


@pytest.mark.parametrize("target", ["library", "record"])
def test_same_world_url_subject_keys_fold_into_verified_placements(target):
    wid, wroot, char, wchar = _world()
    if target == "library":
        d = wroot / "assets" / world_images.DIRNAME
        name = "quay"
        url = f"/api/worlds/{wid}/images/{name}"
    else:
        d, name = wchar, "avatar"
        url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/{name}"
    _legacy(d, f"{name}.png", _png(1))
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})

    rep = _run()

    assert _subjects_of(_id(_png(1)), f"world:{wid}") == {char}
    assert not side.exists() and rep["url_subject_keys_kept"] == 0
    assert image_subjects.read_subjects(wroot, gid)[url] == [char]


@pytest.mark.parametrize("spelling", ["avatar", "Avatar"])
def test_url_subjects_wait_for_an_unfinished_promotion(spelling):
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    assets.put_in(wchar, "gallery_1", _png(2), "png")
    before = {n: image_refs.read(wchar, n).image for n in ("avatar", "gallery_1")}
    if spelling != "avatar" and not image_refs.ref_path(wchar, spelling).exists():
        pytest.skip("case-sensitive filesystem: Avatar is another slot")
    journal = {"name": "gallery_1", "pre": before,
               "post": {"avatar": before["gallery_1"], "gallery_1": before["avatar"]},
               "desc": {"avatar": None, "gallery_1": None}}
    image_refs.write_journal(wchar, journal)
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/{spelling}"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    snapshot = _tree(_root())

    _run(dry_run=True)
    assert _tree(_root()) == snapshot
    rep = _run()

    assert _json(side) == {url: [char]}
    assert rep["url_subject_keys_kept"] == 1
    assert _subjects_of(before["avatar"], f"world:{wid}") == set()
    assert image_refs.read_journal(wchar) == journal
    assets.recover_promotion(wchar)
    _run()
    assert not side.exists()
    assert _subjects_of(before["gallery_1"], f"world:{wid}") == {char}
    assert _subjects_of(before["avatar"], f"world:{wid}") == set()


@pytest.mark.parametrize("alias", ["case", "percent"])
def test_same_world_url_alias_uses_filesystem_identity(monkeypatch, alias):
    wid, wroot, char, _wchar = _world()
    if alias == "case" and not (wroot.parent / wid.upper()).exists():
        pytest.skip("case-sensitive filesystem: REALM is another world")
    d = wroot / "assets" / world_images.DIRNAME
    assets.put_in(d, "quay", _png(1), "png")
    spelling = wid.upper() if alias == "case" else f"%{ord(wid[0]):02X}{wid[1:]}"
    url = f"/api/worlds/{spelling}/images/quay"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    # Keyed as the app keys it (`image_key`, what `write_subjects` accepts and
    # `read_subjects` answers): the alias is the body's URL spelling.
    side = _sidecar(gdir, "subjects.json", {greeting_images.image_key(wroot, gid, url): [char]})

    def live_root_forbidden(*_a, **_kw):
        raise AssertionError("inventory must resolve aliases under its pinned root")

    with monkeypatch.context() as pinned:
        pinned.setattr(worlds.paths, "world_root", live_root_forbidden)
        rep = _run()

    assert _subjects_of(_id(_png(1)), f"world:{wid}") == {char}
    assert not side.exists() and rep["url_subject_keys_kept"] == 0
    # PosixPath containment is lexical even on a case-insensitive macOS
    # volume, so the catalog must preserve this world's root spelling.
    assert greeting_images._world_target(wroot, url)[0].name == wid
    key = greeting_images.image_key(wroot, gid, url)
    assert image_subjects.read_subjects(wroot, gid)[key] == [char]


def test_distinct_world_with_different_case_keeps_url_subjects():
    wid, wroot, char, _wchar = _world()
    if (wroot.parent / wid.upper()).exists():
        pytest.skip("case-insensitive filesystem: REALM is the same world")
    other = worlds.world_root(worlds.create_world("Saltmarch"))
    other = other.rename(wroot.parent / wid.upper())
    assets.put_in(wroot / "assets" / world_images.DIRNAME, "quay", _png(1), "png")
    assets.put_in(other / "assets" / world_images.DIRNAME, "quay", _png(2), "png")
    url = f"/api/worlds/{wid.upper()}/images/quay"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})

    _run()

    assert _json(side) == {url: [char]}
    assert _subjects_of(_id(_png(1)), f"world:{wid}") == set()
    assert _subjects_of(_id(_png(2)), f"world:{wid}") == set()


@pytest.mark.parametrize("route", ["characters/mara/versions/default/images/avatar",
                                   "characters/seraphine/versions/mara/images/avatar",
                                   "pcs/mara/versions/default/images/avatar",
                                   "greetings/mara/images/avatar",
                                   "locations/mara/images/avatar"])
def test_url_subjects_for_missing_records_remain_untouched(route):
    wid, wroot, char, _wchar = _world()
    url = f"/api/worlds/{wid}/{route}"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    before = _tree(_root())

    assert _run(dry_run=True)["outcome"] == "done"
    assert _tree(_root()) == before
    rep = _run()

    assert _json(side) == {url: [char]}
    assert rep["outcome"] == "done" and not rep["errors"]
    assert rep["url_subject_keys_kept"] == 1


def test_invalid_decoded_world_url_keeps_its_key_without_blocking_migration():
    _wid, wroot, char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    url = "/api/worlds/%00/images/quay"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    before = _tree(_root())

    assert _run(dry_run=True)["outcome"] == "done"
    assert _tree(_root()) == before
    rep = _run()

    assert rep["outcome"] == "done" and not rep["errors"]
    assert image_refs.read(wchar, "avatar").image == _id(_png(1))
    assert _json(side) == {url: [char]}


def test_url_target_replaced_after_verification_keeps_its_subject_key(monkeypatch):
    wid, wroot, char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    real = image_migration._metadata_one

    def replace_before_fold(ex, occ, image_id):
        if occ.target_name is not None:
            assert (wchar, "avatar") in ex.verified
            assets.put_in(wchar, "avatar", _png(2), "png")
        return real(ex, occ, image_id)

    with monkeypatch.context() as concurrent:
        concurrent.setattr(image_migration, "_metadata_one", replace_before_fold)
        rep = _run()

    assert _json(side) == {url: [char]}
    assert _subjects_of(_id(_png(1)), f"world:{wid}") == set()
    assert _subjects_of(_id(_png(2)), f"world:{wid}") == set()
    assert any(k["reason"] == "target-not-verified" for k in rep["keys_kept"])
    _run()
    assert not side.exists()
    assert image_subjects.read_subjects(wroot, gid)[url] == [char]
    assert _subjects_of(_id(_png(2)), f"world:{wid}") == {char}


def test_url_target_synced_during_fold_keeps_its_subject_key(monkeypatch):
    """A sync client takes no name lock, so the placement can move while the
    fold runs. The key then names the new picture's URL and must stay, not be
    deleted behind the picture the fold already tagged (#478)."""
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    assets.put_in(wchar, "gallery_9", _png(2), "png")
    replacement = image_refs.read(wchar, "gallery_9").image
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    real = image_store.update
    synced: list[bool] = []

    def update_then_sync(image_id, fn, *a, **kw):
        result = real(image_id, fn, *a, **kw)
        if not synced:
            synced.append(True)
            image_refs.write(wchar, "avatar", replacement)
        return result

    with monkeypatch.context() as concurrent:
        concurrent.setattr(image_store, "update", update_then_sync)
        rep = _run()

    assert synced and _json(side) == {url: [char]}
    assert any(k["reason"] == "target-changed-during-fold" for k in rep["keys_kept"])
    _run()
    assert not side.exists()
    assert _subjects_of(replacement, f"world:{wid}") == {char}


def test_url_target_synced_right_after_the_key_delete_restores_it(monkeypatch):
    """The re-check before the delete leaves a window; the one after it closes
    it, putting back a key whose placement a sync client moved meanwhile."""
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    assets.put_in(wchar, "gallery_9", _png(2), "png")
    replacement = image_refs.read(wchar, "gallery_9").image
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    real = image_migration._delete_key_if
    synced: list[bool] = []

    def delete_then_sync(ex, side_, key, value):
        done = real(ex, side_, key, value)
        if done and key == url and not synced:
            synced.append(True)
            image_refs.write(wchar, "avatar", replacement)
        return done

    with monkeypatch.context() as concurrent:
        concurrent.setattr(image_migration, "_delete_key_if", delete_then_sync)
        rep = _run()

    assert synced and _json(side) == {url: [char]}
    assert any(k["reason"] == "target-changed-during-fold" for k in rep["keys_kept"])
    _run()
    assert not side.exists()
    assert _subjects_of(replacement, f"world:{wid}") == {char}


def test_url_subject_key_the_greeting_no_longer_shows_is_left_alone():
    """`read_subjects` answers only keys the greeting references, so a stale
    one is invisible; folding it would surface its tags through every other
    placement of the picture (#478)."""
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body="No art here.")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})

    _run()

    assert _json(side) == {url: [char]}
    assert _subjects_of(_id(_png(1)), f"world:{wid}") == set()


def test_url_key_unreferenced_after_the_inventory_is_left_alone(monkeypatch):
    """The inventory admits a URL key the greeting references; an edit before
    the fold drops the reference, so the fold asks again and keeps it (#478)."""
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    real = image_migration._metadata_one
    edited: list[bool] = []

    def edit_then_fold(ex, occ, image_id):
        if occ.target_name is not None and not edited:
            edited.append(True)
            greetings.update_greeting(wroot, gid, body="No art any more.")
        return real(ex, occ, image_id)

    monkeypatch.setattr(image_migration, "_metadata_one", edit_then_fold)
    rep = _run()

    assert edited and _json(side) == {url: [char]}
    assert _subjects_of(_id(_png(1)), f"world:{wid}") == set()
    assert any(k["reason"] == "url-no-longer-shown" for k in rep["keys_kept"])


def test_url_subject_fold_holds_its_target_name_lock(monkeypatch):
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    held = []
    real = image_migration._fold_subjects

    def fold_under_lock(*a, **kw):
        held.append(locks.image_name_lock(wchar, "avatar")._is_owned())
        result = real(*a, **kw)
        held.append(locks.image_name_lock(wchar, "avatar")._is_owned())
        return result

    monkeypatch.setattr(image_migration, "_fold_subjects", fold_under_lock)
    _run()

    assert held == [True, True]
    assert not side.exists()
    assert image_subjects.read_subjects(wroot, gid)[url] == [char]



@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
def test_url_subjects_keep_a_junctioned_record_target(tmp_path):
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    url = f"/api/worlds/{wid}/characters/{char}/versions/default/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    owner = wroot / "characters" / char
    saved = tmp_path / "linked-character"
    assert owner.resolve().is_relative_to(tmp_path.resolve())
    assert saved.resolve().is_relative_to(tmp_path.resolve())
    owner.rename(saved)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(owner), str(saved)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        saved.rename(owner)
        pytest.skip("junction creation unavailable")
    before = _tree(saved)
    try:
        rep = _run()
        assert _json(side) == {url: [char]}
        assert rep["url_subject_keys_kept"] == 1
        assert _subjects_of(_id(_png(1)), f"world:{wid}") == set()
        assert _tree(saved) == before
    finally:
        os.rmdir(owner)  # remove only the junction, never its target
        saved.rename(owner)


def test_record_directory_alias_locks_the_canonical_upload_target(monkeypatch):
    wid, wroot, char, wchar = _world()
    assets.put_in(wchar, "avatar", _png(1), "png")
    alias = wroot / "characters" / char.upper() / "assets" / "DEFAULT"
    if not alias.exists():
        pytest.skip("case-sensitive filesystem: the aliases are different records")
    url = f"/api/worlds/{wid}/characters/{char.upper()}/versions/DEFAULT/images/avatar"
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body=f"![Art]({url})")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    side = _sidecar(gdir, "subjects.json", {url: [char]})
    real_metadata = image_migration._metadata_one
    real_fold = image_migration._fold_subjects
    seen = []

    def check_target(ex, occ, image_id):
        if occ.target_name is not None:
            # Windows path equality hides casing differences; compare the
            # actual spelling that becomes the lock key on POSIX/macOS.
            assert occ.target.parts == wchar.parts
        return real_metadata(ex, occ, image_id)

    def replace_if_unlocked():
        lock = locks.image_name_lock(wchar, "avatar")
        if not lock.acquire(blocking=False):
            return False
        try:
            assets.put_in(wchar, "avatar", _png(2), "png")
            return True
        finally:
            lock.release()

    with ThreadPoolExecutor(max_workers=1) as upload:
        def fold_while_upload_attempts(*a, **kw):
            seen.append(upload.submit(replace_if_unlocked).result(timeout=10))
            return real_fold(*a, **kw)

        monkeypatch.setattr(image_migration, "_metadata_one", check_target)
        monkeypatch.setattr(image_migration, "_fold_subjects", fold_while_upload_attempts)
        rep = _run()

    assert rep["outcome"] == "done" and not rep["errors"]
    assert seen == [False] and not side.exists()
    assert image_refs.read(wchar, "avatar").image == _id(_png(1))
    assert image_subjects.read_subjects(wroot, gid)[url] == [char]


def test_a_corrupt_publication_does_not_retire_a_convertible_journal():
    wid, _wroot, _char, _wchar = _world()
    names = place(wid, _png(1))
    journal = format1_journal(wid, "https://example.test/rotate", _COLLECTION, names)
    before = journal.read_bytes()
    manifest = image_collections.manifest_path(wid, _COLLECTION)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{", encoding="utf-8")

    rep = _run()

    assert journal.read_bytes() == before
    assert rep["journals"]["kept"] == 1
    assert not journal.with_name(journal.name + image_collection_imports.RETIRED_SUFFIX).exists()
    manifest.write_text(json.dumps({"format": 1, "members": names}), encoding="utf-8")
    _run()
    assert _json(journal)["format"] == 2


def test_a_previously_verified_target_that_becomes_linked_keeps_its_key(tmp_path, monkeypatch):
    wid, _wroot, char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    _cid, croot = _campaign(wid)
    cdir = assets.version_dir(croot, char, "default")
    side = _sidecar(cdir, "descriptions.json", {"avatar": "A portrait."})
    real = image_migration._unlink_same

    def link_after_verify(ex, p, snap):
        result = real(ex, p, snap)
        ref = image_refs.ref_path(wchar, "avatar")
        outside = tmp_path / "avatar.json"
        ref.replace(outside)
        ref.symlink_to(outside)
        return result

    monkeypatch.setattr(image_migration, "_unlink_same", link_after_verify)
    rep = _run()

    assert _json(side) == {"avatar": "A portrait."}
    assert _texts(_id(_png(1))) == set()
    assert any(k["reason"] == "target-not-verified" for k in rep["keys_kept"])


def test_promotion_recovery_never_replaces_a_different_linked_slot(tmp_path):
    _wid, _wroot, _char, wchar = _world()
    legacy = _legacy(wchar, "gallery_2.png", _png(3))
    assets.put_in(wchar, "avatar", _png(1), "png")
    assets.put_in(wchar, "gallery_1", _png(2), "png")
    before = {n: image_refs.read(wchar, n).image for n in ("avatar", "gallery_1")}
    journal = {"name": "gallery_1", "pre": before,
               "post": {"avatar": before["gallery_1"], "gallery_1": before["avatar"]},
               "desc": {"avatar": None, "gallery_1": None}}
    image_refs.write_journal(wchar, journal)
    slot = image_refs.ref_path(wchar, "gallery_1")
    outside = tmp_path / "gallery_1.json"
    slot.replace(outside)
    slot.symlink_to(outside)

    _run()

    assert slot.is_symlink()
    assert image_refs.read(wchar, "avatar").image == before["avatar"]
    assert image_refs.read_journal(wchar) == journal
    assert legacy.read_bytes() == _png(3)


@pytest.mark.parametrize("sidecar", ["descriptions.json", "focus.json"])
def test_promotion_recovery_never_rewrites_a_linked_sidecar(tmp_path, sidecar):
    """Recovery rewrites `descriptions.json` and unlinks `focus.json` beside the
    two slots, so a linked one stops the item before recovery runs (#478)."""
    _wid, _wroot, _char, wchar = _world()
    legacy = _legacy(wchar, "gallery_2.png", _png(3))
    assets.put_in(wchar, "avatar", _png(1), "png")
    assets.put_in(wchar, "gallery_1", _png(2), "png")
    before = {n: image_refs.read(wchar, n).image for n in ("avatar", "gallery_1")}
    journal = {"name": "gallery_1", "pre": before,
               "post": {"avatar": before["gallery_1"], "gallery_1": before["avatar"]},
               "desc": {"avatar": None, "gallery_1": None}}
    image_refs.write_journal(wchar, journal)
    outside = tmp_path / f"outside-{sidecar}"
    outside.write_text(json.dumps({"gallery_1": "Kept."} if sidecar == "descriptions.json"
                                  else {"x": 0.5, "y": 0.5}))
    link = wchar / sidecar
    link.symlink_to(outside)
    content = outside.read_bytes()

    _run()

    assert link.is_symlink() and outside.read_bytes() == content
    assert image_refs.read(wchar, "avatar").image == before["avatar"]
    assert image_refs.read_journal(wchar) == journal
    assert legacy.read_bytes() == _png(3)


def test_a_root_flip_before_pending_map_write_creates_no_map(tmp_path, monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    legacy = _legacy(wchar, "avatar.png", _png(1))
    root = _root()
    other, _theirs = _other_root(tmp_path)
    real = image_migration._reread

    def flip(ex, item):
        data = real(ex, item)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        return data

    monkeypatch.setattr(image_migration, "_reread", flip)
    rep = _run()

    assert rep["outcome"] == "root-changed"
    assert not image_migration.work_map_path(root).exists()
    assert not image_store.store_root(other).exists()
    assert legacy.read_bytes() == _png(1)


def test_a_root_flip_before_journal_conversion_keeps_format_1(tmp_path, monkeypatch):
    wid, _wroot, _char, _wchar = _world()
    names = place(wid, _png(1))
    journal = format1_journal(wid, "https://example.test/rotate", _COLLECTION, names)
    before = journal.read_bytes()
    other, _theirs = _other_root(tmp_path)
    real = image_collections.format1_member_id

    def flip(*a, **kw):
        image_id = real(*a, **kw)
        monkeypatch.setenv("GRIMOIRE_HOME", str(other))
        return image_id

    monkeypatch.setattr(image_collections, "format1_member_id", flip)
    rep = _run()

    assert rep["outcome"] == "root-changed"
    assert journal.read_bytes() == before


def test_an_accepted_journal_settles_after_its_manifest_migrates():
    wid, wroot, _char, _wchar = _world()
    names = place(wid, _png(1), _png(2))
    _manifest(wroot, names)
    journal = format1_journal(wid, "https://example.test/rotate", _COLLECTION,
                              names, accepted=True)

    rep = _run()

    assert rep["journals"]["converted"] == 1
    assert _json(journal)["format"] == 2
    job = image_collection_imports.accept(wid, journal.stem)
    assert job["accepted"] and job["members"] == [_id(_png(1)), _id(_png(2))]


def test_a_crash_before_thumbnail_cleanup_keeps_the_legacy_file(monkeypatch):
    _wid, _wroot, _char, wchar = _world()
    legacy = _legacy(wchar, "avatar.png", _png(1))
    thumb = thumbs.thumbnail(legacy, 128)
    with monkeypatch.context() as patch:
        patch.setattr(image_migration, "_drop_thumbs", _crash)
        rep = _run()
    assert rep["errors"]
    assert legacy.exists() and thumb.exists()

    _run()

    assert not legacy.exists() and not thumb.exists()

# ---- what may never migrate ---------------------------------------------------------


def test_no_test_migrates_the_frozen_campaign():
    """M17: the frozen campaign's `home/` is never handed to the migration or
    the collector -- its value is being old. A test file that names the
    fixture and either module is refused outright."""
    here = Path(__file__).resolve()
    tests = here.parent
    offenders = []
    for p in tests.rglob("*.py"):
        if p == here:
            continue
        text = p.read_text(encoding="utf-8")
        if "frozen_campaign" in text and re.search(r"\bimage_(?:migration|gc)\b", text):
            offenders.append(p.relative_to(tests).as_posix())
    assert offenders == []


def test_startup_never_migrates():
    _wid, _wroot, _char, wchar = _world()
    avatar = _legacy(wchar, "avatar.png", _png(1))

    with TestClient(create_app()) as client:
        assert client.get("/api/worlds").status_code == 200

    assert avatar.read_bytes() == _png(1)
    assert image_refs.read(wchar, "avatar") is None
    assert not image_migration.work_map_path(_root()).exists()
