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

from grimoire.store import (characters, entities, greetings, tags, world_bundle,
                            worlds)

# A one-pixel PNG: real binary that must survive verbatim (deflate on an
# already-compressed asset is exactly what the export must not corrupt).
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
       b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _seed_world(name: str = "Saltmarch") -> str:
    """A world exercising every corner the bundle has to carry: entities of two
    kinds, a character with a localized avatar URL in its card, a greeting with
    a localized image in its body, tags, a plotmap and a calendar."""
    wid = worlds.create_world(name)
    root = worlds.world_root(wid)

    entities.create_entity(root, "locations", "The Drowned Library")
    entities.create_entity(root, "lore", "The Tide Accord")

    cid = characters.create_character(root, "Seraphine", "default",
                                      characters.blank_card("Seraphine"))[0]
    vid = "default"
    card = characters.read_card(root, cid, vid)
    avatar = f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/embed-abc123"
    card["data"]["description"] = f"A tidewitch.\n\n![]({avatar})\n"
    characters.update_version(root, cid, vid, card)
    assets_dir = root / "characters" / cid / "assets" / vid
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "embed-abc123.png").write_bytes(PNG)

    gid = greetings.create_greeting(root, "The Gala", cid, vid, body="Come in.")
    greetings.update_greeting(
        root, gid,
        body=f"Come in.\n\n![](/api/worlds/{wid}/greetings/{gid}/images/embed-def456)\n")
    gassets = root / "greetings" / gid / "assets" / "default"
    gassets.mkdir(parents=True, exist_ok=True)
    (gassets / "embed-def456.png").write_bytes(PNG)

    tags.add_tag(root, "Coastal")
    greetings.set_edges(root, gid, leads_to=[])
    (root / "calendar.json").write_text(json.dumps({"primary": "gregorian"}), encoding="utf-8")
    return wid


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


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
    wid = _seed_world()
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
    wid = _seed_world()
    root = worlds.world_root(wid)
    with zipfile.ZipFile(_export(wid, tmp_path)) as z:
        packed = {n[len(world_bundle.WORLD_PREFIX) + 1:]: z.read(n)
                  for n in z.namelist() if n != world_bundle.MANIFEST_NAME}
    assert packed == _tree(root)


def test_export_stores_already_compressed_assets_uncompressed(monkeypatch, tmp_path):
    """Deflating a PNG costs CPU on a gigabyte-scale world and saves nothing."""
    _home(monkeypatch, tmp_path)
    wid = _seed_world()
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
    old = _seed_world()
    bundle = _export(old, tmp_path)

    new = world_bundle.import_bundle(bundle)
    assert new != old                      # importing beside the original dedupes
    assert worlds.read_world(new)["counts"] == worlds.read_world(old)["counts"]
    assert worlds.read_world(new)["meta"]["name"] == "Saltmarch"

    before, after = _tree(worlds.world_root(old)), _tree(worlds.world_root(new))
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
    old = _seed_world()
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
    old = _seed_world()
    bundle = _export(old, tmp_path)
    before = _tree(worlds.world_root(old))
    worlds.delete_world(old)

    new = world_bundle.import_bundle(bundle)
    assert new == old
    assert _tree(worlds.world_root(new)) == before


def test_import_twice_makes_two_independent_worlds(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    old = _seed_world()
    bundle = _export(old, tmp_path)
    a = world_bundle.import_bundle(bundle)
    b = world_bundle.import_bundle(bundle)
    assert len({old, a, b}) == 3
    assert len(worlds.list_worlds()) == 3
    for wid in (a, b):
        for data in _tree(worlds.world_root(wid)).values():
            assert f"/api/worlds/{old}/".encode() not in data


def test_import_does_not_touch_the_source_world(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    old = _seed_world()
    bundle = _export(old, tmp_path)
    before = _tree(worlds.world_root(old))
    world_bundle.import_bundle(bundle)
    assert _tree(worlds.world_root(old)) == before


def test_manifest_is_not_extracted_into_the_world(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    new = world_bundle.import_bundle(_export(_seed_world(), tmp_path))
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


def test_a_rejected_import_leaves_no_trace(monkeypatch, tmp_path):
    """A half-extracted world in the library would be worse than a failed
    import: the staging tree is published by one rename or discarded whole."""
    _home(monkeypatch, tmp_path)
    _reject(tmp_path, "traversal", {world_bundle.MANIFEST_NAME: _manifest(),
                                    **GOOD_WORLD, "world/../evil.txt": "x"})
    assert worlds.list_worlds() == []
    assert not (tmp_path / "evil.txt").exists()
    staging = world_bundle._staging_root()
    assert not staging.is_dir() or not any(staging.iterdir())


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
