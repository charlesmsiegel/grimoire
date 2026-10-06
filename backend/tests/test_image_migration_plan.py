"""The migration's inventory and its dry-run plan (stage 4, M6/M7).

`image_surfaces.occurrences(root)` lists every legacy image file a migration
could place, every file it must leave alone, and the four kinds of
metadata-only occurrence; `image_migration.plan(root, ...)` hashes and groups
them, and `report(plan)` is what a dry run hands back. Nothing here writes a
placement, an object or a blob: a plan is read-only, which the last test pins.

Fixtures are synthetic (Pillow-drawn) and every name is invented.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from grimoire.store import (
    assets,
    campaigns,
    characters,
    greetings,
    image_migration,
    image_refs,
    image_store,
    image_surfaces,
    overlay,
    paths,
    world_images,
    worlds,
)
from tests.collection_fixtures import format1


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _img(seed: int = 0, size=(24, 16)) -> Image.Image:
    im = Image.new("RGB", size)
    im.putdata([((x * 9 + seed) % 256, (y * 17 + seed) % 256, (x * y + seed) % 256)
                for y in range(size[1]) for x in range(size[0])])
    return im


def _png(seed: int = 0, **kw) -> bytes:
    buf = io.BytesIO()
    _img(seed).save(buf, "PNG", **kw)
    return buf.getvalue()


def _webp(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    _img(seed).save(buf, "WEBP", lossless=True)
    return buf.getvalue()


def _world() -> tuple[str, Path, str, Path]:
    """A world with one character, and that character's version directory."""
    wid = worlds.create_world("Realm")
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


def _sidecar(d: Path, filename: str, payload: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(json.dumps(payload), encoding="utf-8")


def _root() -> Path:
    return paths.home().resolve()


def _occurrences() -> list[image_surfaces.Occurrence]:
    return list(image_surfaces.occurrences(_root()))


def _plan() -> image_migration.MigrationPlan:
    return image_migration.plan(_root(), cancel=lambda: False)


def _rel(p: Path) -> str:
    return p.resolve().relative_to(_root()).as_posix()


def _untouched(report: dict) -> dict[str, str]:
    return {u["path"]: u["reason"] for u in report["untouched"]}


# ---- the pinned root -------------------------------------------------------

def test_path_builders_pin_an_explicit_root(tmp_path):
    """M4: a run builds every path from the root it captured, so the store's
    path builders take that root instead of asking `paths.home()` again."""
    other = tmp_path / "elsewhere"
    image_id = image_store.ingest(_png(1), "png").id
    obj = image_store.read(image_id)
    assert obj is not None
    assert image_store.object_path(image_id, root=other) == (
        other / "assets" / "image-store" / "objects" / image_id[4:6] / f"{image_id}.json")
    assert image_store.blob_path(obj.blob_sha256, obj.ext, root=other).is_relative_to(
        other / "assets" / "image-store" / "blobs")
    # The default is still the live root, and a pinned read sees only its own.
    assert image_store.object_path(image_id) == image_store.object_path(image_id,
                                                                        root=paths.home())
    assert image_store.read_fresh(image_id, root=paths.home()) is not None
    assert image_store.read_fresh(image_id, root=other) is None


# ---- inventory --------------------------------------------------------------

def test_every_surface_directory_is_walked(tmp_path):
    """One legacy file on every rostered surface, world and campaign: each is
    an occurrence of its surface, and the collection manifest is listed."""
    wid, wroot, char, wchar = _world()
    cid, croot = _campaign(wid)
    files = [
        _legacy(wchar, "avatar.png", _png(1)),
        _legacy(assets.version_dir(croot, char, "default"), "gallery_1.png", _png(2)),
        _legacy(wroot / "assets" / world_images.DIRNAME, "harbour.png", _png(3)),
        _legacy(croot / "assets" / "images", "quay.png", _png(4)),
        _legacy(wroot / "assets", "cover.png", _png(5)),
        _legacy(croot / "assets", "cover.png", _png(6)),
        _legacy(assets.version_dir(wroot, "the-gala", "default", base="greetings"),
                "embed-1.png", _png(7)),
        _legacy(assets.version_dir(wroot, "the-docks", "default", base="locations"),
                "map.png", _png(8)),
        _legacy(assets.version_dir(croot, "the-crew", "default", base="groups"),
                "banner.png", _png(9)),
        _legacy(assets.version_dir(wroot, "mara", "default", base="pcs"), "avatar.png",
                _png(10)),
    ]
    found = {o.path: o for o in _occurrences() if o.path is not None}
    for f in files:
        assert f in found, f
        assert found[f].metadata_only is None and found[f].untouched is None
    assert found[files[0]].kind == "characters"
    assert found[files[0]].scope == f"world:{wid}"
    assert found[files[1]].scope == f"campaign:{cid}"
    assert found[files[2]].kind == found[files[3]].kind == "library"
    assert found[files[4]].kind == "cover" and found[files[4]].name == "cover"


def test_exact_and_metadata_only_variants_collapse():
    """A card avatar carries its JSON in a `chara` chunk; the same pixels
    without it are the same sanitized stream, so one exact duplicate and one
    picture. Another encoding of those pixels is a pixel variant of it."""
    _wid, _wroot, _char, wchar = _world()
    info = PngInfo()
    info.add_text("chara", "eyJuYW1lIjogIlNlcmFwaGluZSJ9")
    carded = _png(1, pnginfo=info)
    plain = _png(1)
    assert carded != plain
    _legacy(wchar, "avatar.png", carded)
    _legacy(wchar, "gallery_1.png", plain)
    _legacy(wchar, "gallery_2.webp", _webp(1))
    _legacy(wchar, "gallery_3.png", _png(2))
    rep = image_migration.report(_plan())
    assert rep["legacy_files"] == 4
    assert rep["unique_streams"] == 3
    assert rep["unique_images"] == 2
    assert rep["exact_duplicates"] == 1
    assert rep["pixel_variants"] == 1
    assert rep["untouched"] == []


def test_all_four_metadata_only_occurrence_kinds_are_inventoried():
    wid, wroot, char, wchar = _world()
    cid, croot = _campaign(wid)
    # World: an avatar and a gallery placement; a crop file beside the avatar
    # placement (d) and a key for the gallery placement (c).
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    assets.put_image(wroot, char, "default", "gallery_1", _png(2), "png")
    _sidecar(wchar, "focus.json", {"avatar": 40})
    _sidecar(wchar, "descriptions.json", {"gallery_1": "Rain on the keep."})
    # A greeting's subject key for its own placement (c).
    gid = greetings.create_greeting(wroot, "The Gala", char, "default", body="Come in.")
    gdir = assets.version_dir(wroot, gid, "default", base="greetings")
    assets.put_in(gdir, "embed-1", _png(3), "png")
    _sidecar(gdir, "subjects.json", {"embed-1": [char]})
    # Campaign: a key for the inherited gallery image (a) and a bare crop
    # over the inherited avatar (b), with no image of its own.
    cchar = assets.version_dir(croot, char, "default")
    _sidecar(cchar, "descriptions.json", {"gallery_1": "Fog on the keep."})
    _sidecar(cchar, "focus.json", {"avatar": 60})
    # The campaign library inherits the world's, and describes it (a).
    world_images.put_image(wid, "harbour", _png(4), "png")
    _sidecar(croot / "assets" / "images", "descriptions.json", {"harbour": "Boats."})

    meta = {(o.metadata_only, o.dir, o.name, o.sidecar)
            for o in _occurrences() if o.metadata_only}
    wlib = wroot / "assets" / world_images.DIRNAME
    assert meta == {
        ("a", cchar, "gallery_1", "descriptions.json"),
        ("a", croot / "assets" / "images", "harbour", "descriptions.json"),
        ("b", cchar, "avatar", "focus.json"),
        ("c", wchar, "gallery_1", "descriptions.json"),
        ("c", gdir, "embed-1", "subjects.json"),
        ("d", wchar, "avatar", "focus.json"),
    }
    by_kind = {o.metadata_only: o for o in _occurrences() if o.metadata_only}
    assert by_kind["a"].path is None and by_kind["a"].scope == f"campaign:{cid}"
    targets = {(o.metadata_only, o.name): o.target for o in _occurrences() if o.metadata_only}
    assert targets[("a", "gallery_1")] == wchar
    assert targets[("a", "harbour")] == wlib
    assert targets[("b", "avatar")] == wchar
    assert targets[("d", "avatar")] == wchar

    plan = _plan()
    rep = image_migration.report(plan)
    assert rep["metadata_only"] == {"a": 2, "b": 1, "c": 2, "d": 1}
    # (a) and (c) disagree about the gallery picture: a conflict, not a merge.
    gallery = image_refs.read(wchar, "gallery_1")
    assert gallery is not None
    assert plan.groups[gallery.image].description["conflicts"] == [
        {"text": "Fog on the keep.", "from": _rel(cchar) + "/gallery_1"},
        {"text": "Rain on the keep.", "from": _rel(wchar) + "/gallery_1"},
    ]
    assert rep["description_conflicts"] == 1


def test_a_tombstoned_or_hidden_campaign_key_is_not_an_occurrence():
    wid, wroot, char, _wchar = _world()
    cid, croot = _campaign(wid)
    assets.put_image(wroot, char, "default", "gallery_1", _png(1), "png")
    assets.put_image(wroot, char, "default", "avatar", _png(2), "png")
    world_images.put_image(wid, "harbour", _png(3), "png")
    cchar = assets.version_dir(croot, char, "default")
    _sidecar(cchar, "descriptions.json", {"gallery_1": "Hidden text.", "avatar": "Kept."})
    _sidecar(croot / "assets" / "images", "descriptions.json", {"harbour": "Hidden too."})
    overlay.add_deleted(cid, f"assets/characters/{char}/default/gallery_1")
    overlay.add_deleted(cid, overlay.library_ref("harbour"))
    meta = {(o.metadata_only, o.name) for o in _occurrences() if o.metadata_only}
    assert meta == {("a", "avatar")}

    # A detached record inherits nothing at all.
    overlay.add_detached(cid, f"characters/{char}")
    assert not [o for o in _occurrences() if o.metadata_only == "a" and o.dir == cchar]


def test_a_corrupt_file_is_untouched_and_the_rest_plan():
    _wid, _wroot, _char, wchar = _world()
    good = _legacy(wchar, "avatar.png", _png(1))
    garbage = _legacy(wchar, "gallery_1.png", b"not a picture at all")
    truncated = _legacy(wchar, "gallery_2.png", _png(2)[:40])
    plan = _plan()
    rep = image_migration.report(plan)
    untouched = _untouched(rep)
    assert _rel(garbage) in untouched and _rel(truncated) in untouched
    assert _rel(good) not in untouched
    assert [i.occurrence.path for i in plan.items] == [good]
    assert rep["unique_images"] == 1


def test_case_aliases_against_refs_and_files_place_nothing():
    _wid, wroot, _char, wchar = _world()
    # Two legacy files whose names alias (a case-sensitive filesystem holds
    # both), and in another directory a placement aliasing a legacy name.
    a = _legacy(wchar, "gallery_1.png", _png(1))
    b = _legacy(wchar, "Gallery_1.png", _png(2))
    other = assets.version_dir(wroot, "the-docks", "default", base="locations")
    image_refs.write(other, "Map", image_store.ingest(_png(3), "png").id)
    c = _legacy(other, "map.png", _png(4))
    fine = _legacy(wchar, "gallery_2.png", _png(5))
    plan = _plan()
    rep = image_migration.report(plan)
    untouched = _untouched(rep)
    for p in (a, b, c):
        assert untouched[_rel(p)] == "case-alias"
    assert [i.occurrence.path for i in plan.items] == [fine]
    assert sorted(sorted(entry["names"]) for entry in rep["case_aliases"]) == [
        ["Gallery_1", "gallery_1"], ["Map", "map"]]


def test_extra_siblings_and_unsupported_extensions_are_untouched():
    _wid, wroot, _char, wchar = _world()
    old = _legacy(wchar, "avatar.png", _png(1))
    new = _legacy(wchar, "avatar.webp", _webp(2))
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    bmp = _legacy(wchar, "gallery_1.bmp", b"BM not ours")
    lib = wroot / "assets" / world_images.DIRNAME
    harbour = _legacy(lib, "harbour.png", _png(3))
    note = _legacy(lib, "harbour.txt", b"a note")
    stray = _legacy(lib, "notes.txt", b"another note")
    cover = _legacy(wroot / "assets", "cover.png", _png(4))
    cover_note = _legacy(wroot / "assets", "cover.txt", b"cover note")
    plan = _plan()
    rep = image_migration.report(plan)
    untouched = _untouched(rep)
    assert untouched[_rel(old)] == "extra-sibling"
    assert untouched[_rel(bmp)] == "unsupported-extension"
    for p in (note, stray, cover_note):
        assert untouched[_rel(p)] == "unsupported-extension"
    assert sorted(i.occurrence.path for i in plan.items) == sorted([new, harbour, cover])


def test_retained_blob_choice_for_new_objects_only():
    """A new object keeps the stream most placements use, then the smaller
    file, then the lowest byte hash. An object already in the store keeps its
    own blob, whatever the plan would have picked."""
    _wid, _wroot, _char, wchar = _world()
    big, small = _png(1, compress_level=0), _png(1, compress_level=9)
    assert len(big) > len(small)
    # Most placements wins over size.
    _legacy(wchar, "avatar.png", big)
    _legacy(wchar, "gallery_1.png", big)
    _legacy(wchar, "gallery_2.png", small)
    # One placement each: the smaller wins.
    other_big, other_small = _png(2, compress_level=0), _png(2, compress_level=9)
    _legacy(wchar, "gallery_3.png", other_big)
    _legacy(wchar, "gallery_4.png", other_small)
    plan = _plan()
    def sha(data: bytes) -> str:
        return image_store.prepare(data, "png").sha

    first = plan.groups[image_store.identify(big, "png")]
    second = plan.groups[image_store.identify(other_big, "png")]
    assert not first.existing and first.retained == sha(big)
    assert not second.existing and second.retained == sha(other_small)
    # Ties on count and size fall to the lowest hash.
    assert image_migration.choose_retained({"b" * 64: (1, 10), "a" * 64: (1, 10)}) == "a" * 64

    # The same picture already stored under its large encoding keeps it.
    stored = image_store.ingest(other_big, "png")
    plan = _plan()
    again = plan.groups[stored.id]
    assert again.existing and again.retained == stored.blob_sha256
    # The retained stream is the first of its group to be placed, so the
    # write that runs the plan ingests it first.
    order = [i.stream for i in plan.items if i.image_id == first.image_id]
    assert order[0] == first.retained


def test_a_legacy_file_beside_its_own_placement_is_planned_and_a_different_one_is_not():
    _wid, wroot, char, wchar = _world()
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    same = _legacy(wchar, "avatar.png", _png(1))
    assets.put_image(wroot, char, "default", "gallery_1", _png(2), "png")
    differs = _legacy(wchar, "gallery_1.png", _png(3))
    plan = _plan()
    rep = image_migration.report(plan)
    assert [i.occurrence.path for i in plan.items] == [same]
    assert [(d["path"], d["reason"]) for d in rep["legacy_differs"]] == [
        (_rel(differs), "differs")]


def test_description_merge_rules_and_overcap_keys():
    _wid, wroot, _char, wchar = _world()
    loc = assets.version_dir(wroot, "the-docks", "default", base="locations")
    # One text beside "" -> that text; only "" -> ""; a list or an over-long
    # value is left where it is.
    _legacy(wchar, "avatar.png", _png(1))
    _legacy(loc, "map.png", _png(1))
    _legacy(wchar, "gallery_1.png", _png(2))
    _legacy(wchar, "gallery_2.png", _png(3))
    _legacy(wchar, "gallery_3.png", _png(4))
    _sidecar(wchar, "descriptions.json", {
        "avatar": "", "gallery_1": "", "gallery_2": ["not", "text"],
        "gallery_3": "x" * (image_store.MAX_DESCRIPTION + 1)})
    _sidecar(loc, "descriptions.json", {"map": "A harbour chart."})
    plan = _plan()
    rep = image_migration.report(plan)
    avatar = plan.groups[image_store.identify(_png(1), "png")]
    assert avatar.description == {"text": "A harbour chart."}
    assert plan.groups[image_store.identify(_png(2), "png")].description == {"text": ""}
    assert plan.groups[image_store.identify(_png(3), "png")].description == {}
    assert sorted((k["path"], k["key"]) for k in rep["overcap_keys"]) == [
        (_rel(wchar) + "/descriptions.json", "gallery_2"),
        (_rel(wchar) + "/descriptions.json", "gallery_3")]
    assert rep["description_conflicts"] == 0
    assert rep["descriptions_merged"] == 3


def test_conflicts_past_the_cap_are_reported():
    _wid, wroot, _char, _wchar = _world()
    n = image_migration.MAX_CONFLICTS + 1
    for i in range(n):
        d = assets.version_dir(wroot, f"lore-{i}", "default", base="lore")
        _legacy(d, "map.png", _png(1))
        _sidecar(d, "descriptions.json", {"map": f"Text number {i}."})
    rep = image_migration.report(_plan())
    assert rep["description_conflicts"] == 1
    assert rep["conflicts_capped"] == [
        {"image_id": image_store.identify(_png(1), "png"), "count": n}]


def test_subject_disagreements_are_reported():
    wid, wroot, char, _wchar = _world()
    other, _vid = characters.create_character(wroot, "Mara", "default")
    dirs = []
    for name in ("The Gala", "The Wake"):
        gid = greetings.create_greeting(wroot, name, char, "default", body="Come in.")
        d = assets.version_dir(wroot, gid, "default", base="greetings")
        _legacy(d, "embed-1.png", _png(1))
        dirs.append(d)
    _sidecar(dirs[0], "subjects.json", {"embed-1": [char]})
    _sidecar(dirs[1], "subjects.json", {"embed-1": [char, other]})
    plan = _plan()
    rep = image_migration.report(plan)
    group = plan.groups[image_store.identify(_png(1), "png")]
    assert group.subjects == {f"world:{wid}": sorted([char, other])}
    assert [(s["image_id"], s["scope"]) for s in rep["subject_disagreements"]] == [
        (group.image_id, f"world:{wid}")]


def test_format_1_collections_are_listed():
    wid, wroot, _char, _wchar = _world()
    format1(wid, "0123456789abcdef0123456789abcdef", _png(1))
    rep = image_migration.report(_plan())
    assert rep["format1_collections"] == [
        _rel(wroot / "assets" / "image-collections" / "0123456789abcdef0123456789abcdef.json")]


def test_a_cancelled_plan_says_so():
    _wid, _wroot, _char, wchar = _world()
    _legacy(wchar, "avatar.png", _png(1))
    plan = image_migration.plan(_root(), cancel=lambda: True)
    assert plan.cancelled and plan.items == []
    assert image_migration.report(plan)["cancelled"] is True


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (
            st.st_mtime_ns, st.st_size, p.read_bytes() if p.is_file() else b"")
    return out


def test_dry_run_writes_nothing_but_its_report(tmp_path):
    wid, wroot, char, wchar = _world()
    _cid, croot = _campaign(wid)
    assets.put_image(wroot, char, "default", "avatar", _png(1), "png")
    _legacy(wchar, "gallery_1.png", _png(2))
    _legacy(wchar, "gallery_2.png", b"garbage")
    _sidecar(wchar, "focus.json", {"avatar": 30})
    _sidecar(wchar, "descriptions.json", {"gallery_1": "One.", "avatar": "Two."})
    _sidecar(assets.version_dir(croot, char, "default"), "descriptions.json",
             {"gallery_1": "Three."})
    _legacy(wroot / "assets" / world_images.DIRNAME, "harbour.png", _png(3))
    format1(wid, "0123456789abcdef0123456789abcdef", _png(4))
    before = _snapshot(tmp_path)
    rep = image_migration.report(_plan())
    assert _snapshot(tmp_path) == before
    # The report is the run's only product, and it is plain JSON.
    assert json.loads(json.dumps(rep)) == rep
    assert rep["legacy_files"] >= 3 and rep["bytes_before"] > 0
    assert rep["bytes_reclaimed"] == rep["bytes_before"] - rep["bytes_after"]
