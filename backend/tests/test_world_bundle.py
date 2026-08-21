"""World bundles: zip a world directory, import it back as a new world (#54).

The round-trip is the whole point, so the tests assert on *bytes*, not on a
summary: every file the export walked comes back byte-identical except the
localized image URLs, which must be repointed at the new world id or every
image in the imported world 404s.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from grimoire.store import (
    assets,
    characters,
    greetings,
    image_descriptions,
    world_bundle,
    worlds,
)

from .world_fixtures import PNG, seed_world, tree


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _zip_bytes(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


def _manifest(world_id: str = "saltmarch", name: str = "Saltmarch", fmt: int = 1) -> str:
    return json.dumps({"format": fmt, "kind": "world", "world_id": world_id,
                       "name": name, "exported": "2026-08-13T00:00:00Z"})


def _export(wid: str, tmp_path: Path, label: str = "bundle") -> Path:
    dest = tmp_path / f"{label}.zip"
    world_bundle.write_bundle(wid, dest)
    return dest


# ---- export ----

def test_export_writes_manifest_and_prefixed_members(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        names = z.namelist()
        manifest = json.loads(z.read(world_bundle.MANIFEST_NAME))
    assert manifest["format"] == world_bundle.FORMAT
    assert manifest["kind"] == "world"
    assert manifest["world_id"] == wid
    assert manifest["name"] == "Saltmarch"
    assert manifest["exported"].endswith("Z")
    assert f"{world_bundle.WORLD_PREFIX}/world.md" in names
    # Everything except the manifest sits under the world prefix, so an import
    # can tell bundle metadata from world content without guessing.
    assert all(n == world_bundle.MANIFEST_NAME or n.startswith(f"{world_bundle.WORLD_PREFIX}/")
               for n in names)


def test_export_carries_every_file(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    root = worlds.world_root(wid)
    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        packed = {n[len(world_bundle.WORLD_PREFIX) + 1:]: z.read(n)
                  for n in z.namelist() if n != world_bundle.MANIFEST_NAME}
    assert packed == tree(root)


def test_export_stores_already_compressed_assets_uncompressed(monkeypatch, tmp_path):
    """Deflating a PNG costs CPU on a gigabyte-scale world and saves nothing."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        by_name = {i.filename: i for i in z.infolist()}
    png = next(i for n, i in by_name.items() if n.endswith(".png"))
    md = next(i for n, i in by_name.items() if n.endswith("world.md"))
    assert png.compress_type == zipfile.ZIP_STORED
    assert md.compress_type == zipfile.ZIP_DEFLATED


def test_export_unknown_world_raises(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        world_bundle.write_bundle("nope", tmp_path / "x.zip")


# ---- round trip ----

def test_round_trip_preserves_content_and_repoints_urls(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    old = seed_world()
    bundle = _export(old, tmp_path)

    new = world_bundle.import_bundle(bundle)
    assert new != old                      # importing beside the original dedupes
    # Concrete counts, not just equality with the source: two empty worlds are
    # equal too, and that is exactly the bug this is meant to catch.
    counts = worlds.read_world(new)["counts"]
    assert (counts["locations"], counts["lore"], counts["characters"],
            counts["greetings"]) == (1, 1, 1, 1)
    assert counts == worlds.read_world(old)["counts"]
    assert worlds.read_world(new)["meta"]["name"] == "Saltmarch"

    before, after = tree(worlds.world_root(old)), tree(worlds.world_root(new))
    assert set(before) == set(after)
    for rel, data in before.items():
        if f"/api/worlds/{old}/".encode() in data:
            assert after[rel] == data.replace(f"/api/worlds/{old}/".encode(),
                                              f"/api/worlds/{new}/".encode())
        else:
            assert after[rel] == data, rel     # binary assets verbatim

    # The rewrite actually happened somewhere, in both a card and a greeting --
    # a round trip that silently rewrote nothing would pass the loop above.
    rewritten = [rel for rel, data in after.items() if f"/api/worlds/{new}/".encode() in data]
    assert any("characters/" in rel for rel in rewritten)
    assert any(rel.startswith("greetings/") for rel in rewritten)
    assert not any(f"/api/worlds/{old}/".encode() in data for data in after.values())


def test_imported_image_urls_resolve(monkeypatch, tmp_path):
    """The repointed URLs are not just textually right -- they name files that
    exist under the new world."""
    _home(monkeypatch, tmp_path)
    old = seed_world()
    new = world_bundle.import_bundle(_export(old, tmp_path))
    root = worlds.world_root(new)

    cid = characters.list_characters(root)[0]["id"]
    card = characters.read_card(root, cid, "default")
    url = card["data"]["description"].split("](")[1].split(")")[0]
    assert url.startswith(f"/api/worlds/{new}/")
    # /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}
    _, _, _, _, ch_cid, _, vid, _, name = url.strip("/").split("/")
    assert (root / "characters" / ch_cid / "assets" / vid / f"{name}.png").read_bytes() == PNG

    gid = greetings.list_greetings(root)[0]["id"]
    body = greetings.read_greeting(root, gid)["body"]
    gurl = body.split("](")[1].split(")")[0]
    assert gurl.startswith(f"/api/worlds/{new}/greetings/{gid}/images/")
    gname = gurl.rsplit("/", 1)[1]
    assert (root / "greetings" / gid / "assets" / "default" / f"{gname}.png").read_bytes() == PNG


def test_import_into_empty_store_keeps_the_original_id(monkeypatch, tmp_path):
    """Nothing to collide with, so the world lands under its own id and no URL
    rewriting is needed at all."""
    _home(monkeypatch, tmp_path)
    old = seed_world()
    bundle = _export(old, tmp_path)
    before = tree(worlds.world_root(old))
    worlds.delete_world(old)

    new = world_bundle.import_bundle(bundle)
    assert new == old
    assert tree(worlds.world_root(new)) == before


def test_import_twice_makes_two_independent_worlds(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    old = seed_world()
    bundle = _export(old, tmp_path)
    a = world_bundle.import_bundle(bundle)
    b = world_bundle.import_bundle(bundle)
    assert len({old, a, b}) == 3
    assert len(worlds.list_worlds()) == 3
    for wid in (a, b):
        for data in tree(worlds.world_root(wid)).values():
            assert f"/api/worlds/{old}/".encode() not in data


def test_import_does_not_touch_the_source_world(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    old = seed_world()
    bundle = _export(old, tmp_path)
    before = tree(worlds.world_root(old))
    world_bundle.import_bundle(bundle)
    assert tree(worlds.world_root(old)) == before


def test_manifest_is_not_extracted_into_the_world(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    new = world_bundle.import_bundle(_export(seed_world(), tmp_path))
    assert not (worlds.world_root(new) / world_bundle.MANIFEST_NAME).exists()


# ---- import rejections ----

def _reject(tmp_path: Path, label: str, entries: dict[str, str | bytes]) -> None:
    zpath = tmp_path / f"{label}.zip"
    zpath.write_bytes(_zip_bytes(entries))
    with pytest.raises(world_bundle.BundleError):
        world_bundle.import_bundle(zpath)


GOOD_WORLD = {"world/world.md": "---\nname: Saltmarch\n---\n"}


def test_import_rejects_unsafe_and_malformed_archives(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    cases: dict[str, dict[str, str | bytes]] = {
        "no-manifest": GOOD_WORLD,
        "no-world-meta": {world_bundle.MANIFEST_NAME: _manifest()},
        "bad-manifest-json": {world_bundle.MANIFEST_NAME: "{not json", **GOOD_WORLD},
        "manifest-not-an-object": {world_bundle.MANIFEST_NAME: "[]", **GOOD_WORLD},
        "future-format": {world_bundle.MANIFEST_NAME: _manifest(fmt=world_bundle.FORMAT + 1),
                          **GOOD_WORLD},
        "wrong-kind": {world_bundle.MANIFEST_NAME:
                       json.dumps({"format": 1, "kind": "module", "world_id": "x", "name": "X"}),
                       **GOOD_WORLD},
        "no-world-id": {world_bundle.MANIFEST_NAME:
                        json.dumps({"format": 1, "kind": "world", "name": "X"}), **GOOD_WORLD},
        "unsafe-world-id": {world_bundle.MANIFEST_NAME:
                            json.dumps({"format": 1, "kind": "world", "world_id": "../evil",
                                        "name": "X"}), **GOOD_WORLD},
        "stray-top-level": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                            "elsewhere/x.md": "x"},
        "traversal": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                      "world/../evil.txt": "x"},
        "absolute": {world_bundle.MANIFEST_NAME: _manifest(), "/abs/world.md": "x"},
        "double-slash": {world_bundle.MANIFEST_NAME: _manifest(), "world//world.md": "x"},
        "dot-segment": {world_bundle.MANIFEST_NAME: _manifest(), "world/./world.md": "x"},
        "drive": {world_bundle.MANIFEST_NAME: _manifest(), "C:/world/world.md": "x"},
        "unc": {world_bundle.MANIFEST_NAME: _manifest(), "//srv/share/world.md": "x"},
        "component-drive": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                            "world/C:evil.txt": "x"},
        "case-collision": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                           "world/World.md": "duplicate"},
    }
    for label, entries in cases.items():
        _reject(tmp_path, label, entries)
    assert worlds.list_worlds() == []


def test_import_rejects_a_non_zip(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(world_bundle.BundleError):
        world_bundle.import_bundle(junk)


def test_import_rejects_a_symlink_member(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(world_bundle.MANIFEST_NAME, _manifest())
        z.writestr("world/world.md", "---\nname: X\n---\n")
        info = zipfile.ZipInfo("world/link.md")
        info.external_attr = (0o120777 << 16)      # S_IFLNK
        z.writestr(info, "../../../etc/passwd")
    zpath = tmp_path / "link.zip"
    zpath.write_bytes(buf.getvalue())
    with pytest.raises(world_bundle.BundleError):
        world_bundle.import_bundle(zpath)


def test_import_enforces_the_uncompressed_size_cap(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(world_bundle, "MAX_UNCOMPRESSED", 32)
    _reject(tmp_path, "too-big",
            {world_bundle.MANIFEST_NAME: _manifest(), "world/world.md": "x" * 4096})


def test_import_enforces_the_member_cap(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(world_bundle, "MAX_MEMBERS", 2)
    _reject(tmp_path, "too-many",
            {world_bundle.MANIFEST_NAME: _manifest(), "world/world.md": "x",
             "world/a.md": "a", "world/b.md": "b"})


def test_a_successful_import_leaves_no_staging_tree(monkeypatch, tmp_path):
    """The staging directory is cleaned on the way out, not just on failure.

    It was not: the name holding it was reassigned to the world's slug halfway
    through, so the cleanup pointed somewhere else entirely and every import
    leaked its tree.
    """
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    assert world_bundle.import_bundle(_export(wid, tmp_path)) != wid
    staging = worlds.staging.staging_root()
    assert not staging.exists() or not any(staging.iterdir())


def test_an_import_cannot_delete_a_directory_outside_the_store(monkeypatch, tmp_path):
    """The consequence of that reassignment, and the reason it is worth a test
    of its own: the cleanup rmtree'd a bare relative name, resolved against the
    PROCESS WORKING DIRECTORY. Importing a world called "Saltmarch" from a
    shell sitting beside a `saltmarch/` deleted it -- a directory that is not
    the store's, holding files grimoire never wrote.
    """
    _home(monkeypatch, tmp_path)
    wid = seed_world("Saltmarch")
    bundle = _export(wid, tmp_path)

    elsewhere = tmp_path / "cwd"
    (elsewhere / "saltmarch").mkdir(parents=True)
    (elsewhere / "saltmarch" / "notes.txt").write_text("not ours", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    assert world_bundle.import_bundle(bundle) != wid
    assert (elsewhere / "saltmarch" / "notes.txt").read_text(encoding="utf-8") == "not ours"


def test_a_rejected_import_leaves_no_trace(monkeypatch, tmp_path):
    """A half-extracted world in the library would be worse than a failed
    import: the staging tree is published by one rename or discarded whole."""
    _home(monkeypatch, tmp_path)
    _reject(tmp_path, "traversal", {world_bundle.MANIFEST_NAME: _manifest(),
                                    **GOOD_WORLD, "world/../evil.txt": "x"})
    assert worlds.list_worlds() == []
    assert not (tmp_path / "evil.txt").exists()
    staging = worlds.staging.staging_root()
    assert not staging.is_dir() or not any(staging.iterdir())


def test_import_rejects_names_that_alias_another_member(monkeypatch, tmp_path):
    """Silent-corruption names, not escapes, and the reason each is refused
    rather than sanitized (Codex review):

    - a trailing dot or space is trimmed by Win32, so `item.md.` and `item.md`
      are one file and the second member quietly overwrites the first;
    - a reserved device name swallows its member whole -- opening `NUL` for
      writing succeeds and discards every byte, so the file just is not there.

    Both are rejected on every platform, for the reason `safe_id` gives for the
    same rule: a store is synced between them and a name must mean one thing.
    """
    _home(monkeypatch, tmp_path)
    cases: dict[str, dict[str, str | bytes]] = {
        "trailing-dot": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                         "world/lore/tide.md": "a", "world/lore/tide.md.": "b"},
        "trailing-space": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                           "world/lore/tide.md ": "b"},
        "device-name": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                        "world/NUL": "x"},
        "device-name-with-suffix": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                                    "world/lore/con.md": "x"},
        "dir-case-collision": {world_bundle.MANIFEST_NAME: _manifest(), **GOOD_WORLD,
                               "world/Lore/a.md": "a", "world/lore/b.md": "b"},
    }
    for label, entries in cases.items():
        _reject(tmp_path, label, entries)
    assert worlds.list_worlds() == []


def test_import_counts_directory_entries_against_the_member_cap(monkeypatch, tmp_path):
    """A cap applied after directories are filtered out would wave through the
    archive it exists to stop: a million empty directories holds two files."""
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(world_bundle, "MAX_MEMBERS", 4)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(world_bundle.MANIFEST_NAME, _manifest())
        z.writestr("world/world.md", "---\nname: X\n---\n")
        for n in range(8):
            z.writestr(f"world/pad{n}/", b"")      # directory entries only
    zpath = tmp_path / "dirbomb.zip"
    zpath.write_bytes(buf.getvalue())
    with pytest.raises(world_bundle.BundleError):
        world_bundle.import_bundle(zpath)
    assert worlds.list_worlds() == []


def test_import_leaves_text_assets_byte_identical(monkeypatch, tmp_path):
    """`.svg` is a text suffix and an image format at once. Rewriting by suffix
    alone edited real asset bytes; asset subtrees are excluded by path."""
    _home(monkeypatch, tmp_path)
    old = seed_world()
    root = worlds.world_root(old)
    cid = characters.list_characters(root)[0]["id"]
    svg = (f'<svg><desc>/api/worlds/{old}/characters/{cid}/versions/default/'
           f'images/embed-abc123</desc></svg>').encode()
    (root / "characters" / cid / "assets" / "default" / "sigil.svg").write_bytes(svg)

    new = world_bundle.import_bundle(_export(old, tmp_path))
    assert new != old
    copied = worlds.world_root(new) / "characters" / cid / "assets" / "default" / "sigil.svg"
    assert copied.read_bytes() == svg          # untouched, old id and all
    # ...while the card beside it, which is a record rather than an asset, did
    # get repointed -- so this is not just "the rewrite never ran".
    assert f"/api/worlds/{new}/".encode() in (
        worlds.world_root(new) / "characters" / cid / "default.json").read_bytes()


def test_export_keeps_a_file_that_merely_looks_like_a_write_temp(monkeypatch, tmp_path):
    """`.notes.tmp` is a world file; `.world.md.a1b2c3d4.tmp` is store.atomic
    mid-write. Only the second may be dropped from the bundle."""
    _home(monkeypatch, tmp_path)
    wid = seed_world()
    root = worlds.world_root(wid)
    (root / ".notes.tmp").write_bytes(b"mine")
    (root / ".world.md.a1b2c3d4.tmp").write_bytes(b"half-written")

    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        names = z.namelist()
    assert f"{world_bundle.WORLD_PREFIX}/.notes.tmp" in names
    assert f"{world_bundle.WORLD_PREFIX}/.world.md.a1b2c3d4.tmp" not in names


def test_a_failure_partway_through_extraction_also_leaves_no_trace(monkeypatch, tmp_path):
    """The rejection tests above all fail during *scanning*, before staging
    exists -- so none of them would notice the cleanup disappearing. This one
    fails after files have already been written (Codex review)."""
    _home(monkeypatch, tmp_path)
    bundle = _export(seed_world(), tmp_path)
    real_open = zipfile.ZipFile.open

    def boom(self, name, *a, **k):
        target = name.filename if isinstance(name, zipfile.ZipInfo) else str(name)
        if target.endswith("tide-accord.md"):     # not the first member extracted
            raise zipfile.BadZipFile("Bad CRC-32")
        return real_open(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "open", boom)
    with pytest.raises(world_bundle.BundleError):
        world_bundle.import_bundle(bundle)
    monkeypatch.setattr(zipfile.ZipFile, "open", real_open)

    assert [w["id"] for w in worlds.list_worlds()] == ["saltmarch"]   # only the source
    staging = worlds.staging.staging_root()
    assert not staging.is_dir() or not any(staging.iterdir())


def test_only_record_extensions_are_rewritten(monkeypatch, tmp_path):
    """#54 scoped the rewrite to `.md` and `.json`. Two files decide whether
    that is what happens: an `.svg` outside any assets directory (a text format
    that is also an image -- it must NOT be touched) and an `.md` inside one (a
    record that must be, whatever directory it sits in)."""
    _home(monkeypatch, tmp_path)
    old = seed_world()
    root = worlds.world_root(old)
    url = f"/api/worlds/{old}/greetings/x/images/y".encode()
    (root / "maps").mkdir()
    (root / "maps" / "crest.svg").write_bytes(b"<svg><desc>" + url + b"</desc></svg>")
    (root / "characters" / "seraphine" / "assets" / "default" / "notes.md").write_bytes(
        b"see " + url + b"\n")

    new = world_bundle.import_bundle(_export(old, tmp_path))
    assert new != old
    nroot = worlds.world_root(new)
    assert url in (nroot / "maps" / "crest.svg").read_bytes()          # image: verbatim
    assert url not in (
        nroot / "characters" / "seraphine" / "assets" / "default" / "notes.md").read_bytes()


def test_export_does_not_follow_a_symlink_out_of_the_world(monkeypatch, tmp_path):
    """`is_file()` follows links, so a link inside the world would be packed as
    a copy of whatever it points at -- and the bundle is a file the user hands
    to someone else (Codex review)."""
    _home(monkeypatch, tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("private", encoding="utf-8")
    wid = seed_world()
    try:
        (worlds.world_root(wid) / "lore" / "leak.md").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create symlinks")

    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        names = z.namelist()
        assert not any(b"private" in z.read(n) for n in names)
    assert f"{world_bundle.WORLD_PREFIX}/lore/leak.md" not in names


def test_publish_retries_when_the_chosen_id_is_taken(monkeypatch, tmp_path):
    """Losing an id race is not a reason to reject a good bundle: the id is
    re-picked, the URLs re-pointed at it, and the import succeeds."""
    _home(monkeypatch, tmp_path)
    bundle = _export(seed_world(), tmp_path)
    worlds.create_world("Occupied")          # the id the first pick will collide with

    real_uniquify = world_bundle.uniquify
    picks: list[str] = []

    def racing(base, exists):
        picks.append(base)
        # First pick lands on a live world, exactly as a concurrent import that
        # published between our uniquify and our rename would leave it.
        return "occupied" if len(picks) == 1 else real_uniquify(base, exists)

    monkeypatch.setattr(world_bundle, "uniquify", racing)
    new = world_bundle.import_bundle(bundle)
    monkeypatch.setattr(world_bundle, "uniquify", real_uniquify)

    assert new not in ("occupied", "saltmarch")
    assert worlds.read_world("occupied")["meta"]["name"] == "Occupied"   # untouched
    for data in tree(worlds.world_root(new)).values():
        assert b"/api/worlds/occupied/" not in data
        assert b"/api/worlds/saltmarch/" not in data
    assert any(f"/api/worlds/{new}/".encode() in d
               for d in tree(worlds.world_root(new)).values())


def test_manifest_carries_the_app_version(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with zipfile.ZipFile(_export(seed_world(), tmp_path)) as z:
        manifest = json.loads(z.read(world_bundle.MANIFEST_NAME))
    assert manifest["app_version"] == world_bundle.app_version()
    assert manifest["app_version"]


def test_import_rejects_a_boolean_format(monkeypatch, tmp_path):
    """JSON `true` is a Python bool, bool subclasses int, and `True == 1` --
    so a naive equality check reads `{"format": true}` as format 1."""
    _home(monkeypatch, tmp_path)
    _reject(tmp_path, "bool-format", {
        world_bundle.MANIFEST_NAME: json.dumps(
            {"format": True, "kind": "world", "world_id": "saltmarch", "name": "S"}),
        **GOOD_WORLD})


def test_import_accepts_a_hand_built_minimal_bundle(monkeypatch, tmp_path):
    """The store layout is the format: a bundle assembled by hand (or by an
    older grimoire) imports as long as the manifest and world.md are there."""
    _home(monkeypatch, tmp_path)
    zpath = tmp_path / "hand.zip"
    zpath.write_bytes(_zip_bytes({
        world_bundle.MANIFEST_NAME: _manifest(world_id="elsewhere", name="Ignored"),
        "world/world.md": "---\nname: Hand Built\n---\n\nA world.\n",
        "world/lore/tide.md": "---\nname: Tide\n---\n\nSalt.\n",
    }))
    wid = world_bundle.import_bundle(zpath)
    assert wid == "hand-built"                       # the world's own name wins
    assert worlds.read_world(wid)["meta"]["name"] == "Hand Built"
    assert worlds.read_world(wid)["counts"]["lore"] == 1


def test_image_descriptions_survive_a_round_trip(monkeypatch, tmp_path):
    """A description lives in a sidecar under `assets/`, so it travels with the
    bundle only because the export carries every file. Pinned because the
    failure mode is silent: an exclusion filter added to `_world_members` later
    would lose every description in the world and leave the pictures behind,
    with nothing to show for it but art nobody can search or offer any more.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    cid, vid = characters.create_character(wroot, "Seraphine", "main")
    assets.put_image(wroot, cid, vid, "gallery_1", b"\x89PNG\r\n\x1a\n", "png")
    image_descriptions.set_description(wroot, cid, vid, "gallery_1", "A grey quay at dusk.")
    # ...and the reviewed-empty marker, which is a decision and not an absence
    assets.put_image(wroot, cid, vid, "gallery_2", b"\x89PNG\r\n\x1a\n", "png")
    image_descriptions.set_description(wroot, cid, vid, "gallery_2", "")

    imported = world_bundle.import_bundle(_export(wid, tmp_path))
    nroot = worlds.world_root(imported)
    assert image_descriptions.read_all(nroot, cid, vid) == {
        "gallery_1": "A grey quay at dusk.", "gallery_2": ""}
