from urllib.parse import quote

import pytest

from grimoire.store import (
    assets,
    characters,
    entities,
    greetings,
    image_refs,
    image_store,
    image_subjects,
    pcs,
    worlds,
)


def _world(tmp_path, images=("art_1", "art_2")):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "body")
    for name in images:
        # Distinct bytes: identical ones are one image object, and an answer
        # on the object answers every placement of it in the world.
        assets.put_image(tmp_path, gid, "default", name, f"png-{name}".encode(), "png",
                         base="greetings")
    return cid, gid


def test_subjects_roundtrip_and_missing_file(tmp_path):
    cid, gid = _world(tmp_path)
    assert image_subjects.read_subjects(tmp_path, gid) == {}
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid]})
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_referenced_character_art_is_reviewed_independently_of_its_owner(tmp_path):
    owner, gid = _world(tmp_path, images=("art_1",))
    subject, _ = characters.create_character(tmp_path, "Mara", "main")
    assets.put_image(tmp_path, owner, "main", "embed-art", b"png", "png")
    url = f"/api/worlds/{tmp_path.name}/characters/{owner}/versions/main/images/embed-art"
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url}?v=old)")
    assert {a["name"] for a in image_subjects.untagged(tmp_path)} == {"art_1", url}
    image_subjects.set_image_subjects(tmp_path, gid, url, [subject])
    assert image_subjects.read_subjects(tmp_path, gid) == {url: [subject]}
    assert image_subjects.appearances(tmp_path, owner) == []
    assert image_subjects.appearances(tmp_path, subject)[0]["name"] == url
    # An older, greeting-owned writer still works alongside referenced tags.
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [])
    assert image_subjects.untagged(tmp_path) == []
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url}?v=new)")
    assert image_subjects.read_subjects(tmp_path, gid)[url] == [subject]
    assets.delete_image(tmp_path, owner, "main", "embed-art")
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": []}
    assert image_subjects.untagged(tmp_path) == []
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [subject])


def test_referenced_image_inventory_matches_rendered_markdown_not_code_or_html(tmp_path):
    _owner, gid = _world(tmp_path, images=())
    url = "https://example.test/art.png?variant=1&size=2"
    greetings.update_greeting(tmp_path, gid, body=(
        f"![Art][art]\n\n[art]: <{url}>\n\n"
        f"![Again]({url})\n\n"
        "`![Inline](https://example.test/inline.png)`\n\n"
        "```markdown\n![Code](https://example.test/code.png)\n```\n\n"
        '<img src="https://example.test/html.png">\n\n'
        "[Link](https://example.test/link.png)"
    ))
    assert [a["name"] for a in image_subjects.untagged(tmp_path)] == [url]
    image_subjects.set_image_subjects(tmp_path, gid, url, [])
    assert image_subjects.read_subjects(tmp_path, gid) == {url: []}
    assert image_subjects.untagged(tmp_path) == []
    greetings.update_greeting(tmp_path, gid, body="No image")
    assert image_subjects.read_subjects(tmp_path, gid) == {}
    with pytest.raises(ValueError):
        image_subjects.set_image_subjects(tmp_path, gid, url, [])


@pytest.mark.parametrize("kind", ["characters", "pcs", "lore", "greetings", "world"])
def test_local_references_use_existing_serving_records(tmp_path, kind):
    owner, gid = _world(tmp_path, images=())
    if kind == "characters":
        rid, vid = owner, "main"
        route = f"characters/{rid}/versions/{vid}/images/avatar"
    elif kind == "pcs":
        rid, vid = pcs.create_pc(tmp_path, "Mara", [], "main")
        route = f"pcs/{rid}/versions/{vid}/images/avatar"
    elif kind == "lore":
        rid, vid = entities.create_entity(tmp_path, "lore", "Saltmarch"), "default"
        route = f"lore/{rid}/images/avatar"
    elif kind == "greetings":
        rid, vid = greetings.create_greeting(tmp_path, "Saltmarch", owner, "main"), "default"
        route = f"greetings/{rid}/images/avatar"
    else:
        rid, vid = "", ""
        route = "images/art"
    if kind == "world":
        assets.put_in(tmp_path / "assets" / "images", "art", b"png", "png", supported_only=True)
    else:
        assets.put_image(tmp_path, rid, vid, "avatar", b"png", "png", base=kind)
    url = f"/api/worlds/{tmp_path.name}/{route}"
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url})")
    assert any(a["name"] == url for a in image_subjects.untagged(tmp_path))
    image_subjects.set_image_subjects(tmp_path, gid, url, [])
    assert image_subjects.read_subjects(tmp_path, gid) == {url: []}
    if kind == "characters":
        (tmp_path / "characters" / rid / "character.md").unlink()
        assert image_subjects.read_subjects(tmp_path, gid) == {}
    elif kind == "pcs":
        pcs.require_version(tmp_path, rid, vid).unlink()
        assert image_subjects.read_subjects(tmp_path, gid) == {}


@pytest.mark.parametrize("name", ["art#1", "art%1", "portrait-é"])
def test_reference_identity_preserves_escaped_filename_characters(tmp_path, name):
    owner, gid = _world(tmp_path, images=())
    assets.put_image(tmp_path, owner, "main", name, b"png", "png")
    url = f"/api/worlds/{tmp_path.name}/characters/{owner}/versions/main/images/{quote(name, safe='')}"
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url})")
    assert image_subjects.untagged(tmp_path)[0]["url"] == url
    image_subjects.set_image_subjects(tmp_path, gid, url, [])
    assert image_subjects.read_subjects(tmp_path, gid) == {url: []}


@pytest.mark.parametrize("body,visible", [
    ("- item\n\n    ```markdown\n\n    ![Code](https://example.test/code.png)\n\n    ```", False),
    ("> ```markdown\n> ![Code](https://example.test/code.png)\n> ```", False),
    ("<div>\n\n![Art](https://example.test/code.png)\n\n</div>", True),
])
def test_inventory_follows_commonmark_for_nested_code_and_html_containers(tmp_path, body, visible):
    _owner, gid = _world(tmp_path, images=())
    greetings.update_greeting(tmp_path, gid, body=body)
    assert bool(image_subjects.untagged(tmp_path)) is visible


@pytest.mark.parametrize("url,key", [
    ("https://example.test/a b.png?variant=1", "https://example.test/a%20b.png?variant=1"),
    ("HTTPS://example.test/art.png?variant=1", "https://example.test/art.png?variant=1"),
])
def test_remote_reference_identity_normalizes_rendered_uri_without_dropping_query(tmp_path, url, key):
    _owner, gid = _world(tmp_path, images=())
    greetings.update_greeting(tmp_path, gid, body=f"![Art](<{url}>)")
    assert image_subjects.untagged(tmp_path)[0]["name"] == key
    image_subjects.set_image_subjects(tmp_path, gid, key, [])
    assert image_subjects.read_subjects(tmp_path, gid) == {key: []}


@pytest.mark.parametrize("route", [
    "characters/%2e%2e/versions/main/images/avatar",
    "characters/mara/versions/%2e%2e/images/avatar",
    "characters/mara/versions/missing/images/avatar",
    "lore/%2e%2e/images/avatar",
    "unknown/mara/images/avatar",
    "config/images/avatar",
    "images/%2e%2e%2fsecret",
    "image-collections/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/image",
])
def test_broken_or_unsafe_local_references_do_not_create_unresolvable_chores(tmp_path, monkeypatch, route):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    _owner, gid = _world(tmp_path, images=())
    greetings.update_greeting(tmp_path, gid, body=f"![Art](/api/worlds/{tmp_path.name}/{route})")
    assert image_subjects.untagged(tmp_path) == []


def test_missing_cross_world_reference_never_substitutes_same_world_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    owner, gid = _world(tmp_path, images=())
    assets.put_image(tmp_path, owner, "main", "avatar", b"png", "png")
    url = f"/api/worlds/realm/characters/{owner}/versions/main/images/avatar"
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url})")
    assert image_subjects.untagged(tmp_path) == []


def test_appearances_tolerates_a_reference_restored_during_the_read(tmp_path, monkeypatch):
    owner, gid = _world(tmp_path, images=())
    old, restored = "https://example.test/old.png", "https://example.test/art.png"
    for url in (restored, old):
        greetings.update_greeting(tmp_path, gid, body=f"![Art]({url})")
        image_subjects.set_image_subjects(tmp_path, gid, url, [owner])
    real_catalog = image_subjects.greeting_images.catalog_with_slots
    first = True

    def restore_after_inventory(root, greeting):
        nonlocal first
        images = real_catalog(root, greeting)
        if first:
            first = False
            greetings.update_greeting(root, greeting, body=f"![Art]({restored})")
        return images

    monkeypatch.setattr(image_subjects.greeting_images, "catalog_with_slots", restore_after_inventory)
    assert image_subjects.appearances(tmp_path, owner) == []
    assert image_subjects.appearances(tmp_path, owner)[0]["name"] == restored


def test_write_rejects_unknown_image_and_persists_empty(tmp_path):
    cid, gid = _world(tmp_path)
    with pytest.raises(ValueError):
        image_subjects.write_subjects(tmp_path, gid, {"nope": [cid]})
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid], "art_2": []})
    # explicit [] persists: "reviewed, nobody in it"
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid], "art_2": []}


def test_read_drops_vanished_images_and_characters(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid, "ghost"], "art_2": [cid]})
    assets.delete_image(tmp_path, gid, "default", "art_2", base="greetings")
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid]}


def test_read_tolerates_garbled_sidecar(tmp_path):
    _cid, gid = _world(tmp_path)
    image_subjects.subjects_path(tmp_path, gid).write_text("{not json", encoding="utf-8")
    assert image_subjects.read_subjects(tmp_path, gid) == {}


def test_set_image_subjects_updates_one_entry(tmp_path):
    cid, gid = _world(tmp_path)
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_2", [cid])
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [])
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [], "art_2": [cid]}


def test_untagged_lists_only_unreviewed_images(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "One", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "Two", cid, "main", "x")
    for gid, names in ((g1, ("a_tagged", "b_reviewed", "c_new")), (g2, ("d_new",))):
        for n in names:
            assets.put_image(tmp_path, gid, "default", n, n.encode(), "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "a_tagged", [cid])
    image_subjects.set_image_subjects(tmp_path, g1, "b_reviewed", [])  # reviewed, none
    got = image_subjects.untagged(tmp_path)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {(a["gid"], a["name"]) for a in got} == {(g1, "c_new"), (g2, "d_new")}


def test_appearances_scans_across_greetings_in_order(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "B scene", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "A scene", cid, "main", "x")
    for gid in (g1, g2):
        assets.put_image(tmp_path, gid, "default", "art_1", b"p", "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "art_1", [cid])
    image_subjects.set_image_subjects(tmp_path, g2, "art_1", [cid])
    got = image_subjects.appearances(tmp_path, cid)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {a["gid"] for a in got} == {g1, g2}
    assert image_subjects.appearances(tmp_path, "nobody") == []


def test_sweeps_skip_full_character_enumeration(tmp_path, monkeypatch):
    """appearances/untagged scan every greeting; enumerating full character
    detail per greeting made them O(greetings x characters) in disk reads."""
    cid, gid = _world(tmp_path)
    image_subjects.set_image_subjects(tmp_path, gid, "art_1", [cid])

    def boom(root):
        raise AssertionError("list_characters must not run during sweeps")
    monkeypatch.setattr(image_subjects.characters, "list_characters", boom)
    real_refs = image_subjects.characters.character_refs
    refs_calls = []
    monkeypatch.setattr(image_subjects.characters, "character_refs",
                        lambda root: (refs_calls.append(1), real_refs(root))[1])

    got = image_subjects.appearances(tmp_path, cid)
    assert {(a["gid"], a["name"]) for a in got} == {(gid, "art_1")}
    assert len(refs_calls) <= 1

    refs_calls.clear()
    got = image_subjects.untagged(tmp_path)
    assert {(a["gid"], a["name"]) for a in got} == {(gid, "art_2")}
    assert len(refs_calls) <= 1


def test_copy_to_character_gallery_numbers_and_avatar(tmp_path):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")
    assets.put_image(tmp_path, cid, vid, "gallery_1", b"old", "png")  # occupy slot 1

    n1 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery")
    assert n1 == "gallery_2"
    p = assets.image_path(tmp_path, cid, vid, "gallery_2")
    assert p is not None and p.read_bytes() == b"artbytes"

    assets.write_focus(tmp_path, cid, vid, 30)
    n2 = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "avatar")
    assert n2 == "avatar"
    assert assets.image_path(tmp_path, cid, vid, "avatar").read_bytes() == b"artbytes"
    assert assets.read_focus(tmp_path, cid, vid) is None  # avatar semantics reset the crop

    with pytest.raises(FileNotFoundError):
        image_subjects.copy_to_character(tmp_path, gid, "missing", cid, vid, "gallery")
    with pytest.raises(ValueError):
        image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "banner")


def test_copy_to_character_honors_taken_names_override(tmp_path):
    """A campaign-side caller passes the overlay-resolved union of gallery
    names so an inherited world gallery image can't be shadowed by a reused
    gallery_N name — even though nothing occupies that slot in `root` itself."""
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")

    n = image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery",
                                         taken_names={"gallery_1"})
    assert n == "gallery_2"


def _no_ingest(monkeypatch):
    def ingest(*args, **kwargs):
        raise AssertionError("bytes were re-ingested")
    monkeypatch.setattr(image_store, "ingest", ingest)


def _blobs():
    root = image_store.store_root() / "blobs"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_copy_from_greeting_writes_no_blob(tmp_path, monkeypatch):
    """Copying a ref-backed greeting image is a reference operation: the
    character's slot names the same image, and no bytes are written."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    assets.put_image(tmp_path, gid, "default", "art_1", b"artbytes", "png", base="greetings")
    src_id = assets.image_id(tmp_path, gid, "default", "art_1", base="greetings")
    assert src_id is not None
    before = _blobs()
    _no_ingest(monkeypatch)

    assert image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery") == "gallery_1"
    assets.write_focus(tmp_path, cid, vid, 30)
    assert image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "avatar") == "avatar"

    assert _blobs() == before
    assert assets.image_id(tmp_path, cid, vid, "gallery_1") == src_id
    assert assets.image_id(tmp_path, cid, vid, assets.AVATAR) == src_id
    assert assets.read_focus(tmp_path, cid, vid) is None


def test_copy_of_a_referenced_world_image_links_it(tmp_path, monkeypatch):
    """A greeting that embeds another record's art by URL resolves to that
    record's placement, so the copy links the same image too."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    owner, gid = _world(tmp_path, images=())
    subject, svid = characters.create_character(tmp_path, "Mara", "main")
    assets.put_image(tmp_path, owner, "main", "embed-art", b"embedded", "png")
    url = f"/api/worlds/{tmp_path.name}/characters/{owner}/versions/main/images/embed-art"
    greetings.update_greeting(tmp_path, gid, body=f"![Art]({url})")
    before = _blobs()
    _no_ingest(monkeypatch)

    assert image_subjects.copy_to_character(tmp_path, gid, url, subject, svid, "gallery") == "gallery_1"
    assert _blobs() == before
    assert (assets.image_id(tmp_path, subject, svid, "gallery_1")
            == assets.image_id(tmp_path, owner, "main", "embed-art"))


def test_copy_of_a_legacy_greeting_image_ingests_it_once(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "x")
    src_dir = tmp_path / "greetings" / gid / "assets" / "default"
    src_dir.mkdir(parents=True)
    (src_dir / "art_1.png").write_bytes(b"legacy-art")
    before = _blobs()

    assert image_subjects.copy_to_character(tmp_path, gid, "art_1", cid, vid, "gallery") == "gallery_1"
    assert len(_blobs()) == len(before) + 1
    assert (src_dir / "art_1.png").read_bytes() == b"legacy-art"    # the source is left as it was
    assert assets.image_path(tmp_path, cid, vid, "gallery_1").read_bytes() == b"legacy-art"
    assert assets.image_id(tmp_path, cid, vid, "gallery_1") is not None


# ---- subjects on the image object (stage 2, spec section 9) ----

def _scoped_world(monkeypatch, tmp_path, name="Realm"):
    """A real world under a real store home, so its root names its scope."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world(name)
    root = worlds.world_root(wid)
    cid, vid = characters.create_character(root, "Seraphine", "main")
    return root, cid, vid


def _greeting_with(root, cid, vid, images, body="body"):
    gid = greetings.create_greeting(root, "Opener", cid, vid, body)
    for name, data in images.items():
        assets.put_image(root, gid, "default", name, data, "png", base="greetings")
    return gid


def _object(root, gid, name):
    image_id = assets.image_id(root, gid, "default", name, base="greetings")
    assert image_id is not None
    return image_store.read(image_id).raw


def test_tagging_a_placement_writes_scoped_associations(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    assert root.name == "realm" and cid == "seraphine"
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})

    image_subjects.set_image_subjects(root, gid, "art_1", [cid, cid])

    raw = _object(root, gid, "art_1")
    assert raw["associations"] == [
        {"kind": "character", "relation": "subject", "scope": "world:realm", "id": "seraphine"}]
    assert raw["reviews"] == {"subjects": ["world:realm"]}
    assert "art_1" not in image_subjects._read_raw(root, gid)
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid]}
    # A retag REPLACES this scope's associations, it does not add to them.
    image_subjects.set_image_subjects(root, gid, "art_1", [])
    raw = _object(root, gid, "art_1")
    assert raw["associations"] == []
    assert raw["reviews"] == {"subjects": ["world:realm"]}
    assert image_subjects.read_subjects(root, gid) == {"art_1": []}


def test_a_tag_shows_in_every_greeting_of_the_world_that_places_the_picture(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    first = _greeting_with(root, cid, vid, {"art_1": b"shared-art"})
    second = _greeting_with(root, cid, vid, {"scene": b"shared-art"})
    # ...and a third greeting that shows it by reference to a character's slot.
    assets.put_image(root, cid, vid, "gallery_1", b"shared-art", "png")
    url = f"/api/worlds/{root.name}/characters/{cid}/versions/{vid}/images/gallery_1"
    third = _greeting_with(root, cid, vid, {}, body=f"![Art]({url})")
    assert len(image_subjects.untagged(root)) == 3

    image_subjects.set_image_subjects(root, first, "art_1", [cid])

    assert image_subjects.read_subjects(root, second) == {"scene": [cid]}
    assert image_subjects.read_subjects(root, third) == {url: [cid]}
    assert image_subjects.untagged(root) == []
    assert {(a["gid"], a["name"]) for a in image_subjects.appearances(root, cid)} == {
        (first, "art_1"), (second, "scene"), (third, url)}


def test_a_tag_is_absent_in_another_world(tmp_path, monkeypatch):
    """Review Focus 3: the same bytes in two worlds are one object, and a
    subject tag stays in the world it was made in."""
    realm, cid, vid = _scoped_world(monkeypatch, tmp_path)
    salt = worlds.world_root(worlds.create_world("Saltmarch"))
    # The same character id exists in both, so a leaked tag would not be
    # filtered out as an unknown character -- it would show.
    assert characters.create_character(salt, "Seraphine", "main") == (cid, vid)
    here = _greeting_with(realm, cid, vid, {"art_1": b"same-bytes"})
    there = _greeting_with(salt, cid, vid, {"art_1": b"same-bytes"})
    assert _object(realm, here, "art_1")["id"] == _object(salt, there, "art_1")["id"]

    image_subjects.set_image_subjects(realm, here, "art_1", [cid])

    assert image_subjects.read_subjects(realm, here) == {"art_1": [cid]}
    assert image_subjects.read_subjects(salt, there) == {}
    assert image_subjects.reviewed_names(salt, there) == set()
    assert [(a["gid"], a["name"]) for a in image_subjects.untagged(salt)] == [(there, "art_1")]
    assert image_subjects.appearances(salt, cid) == []


def test_reviewed_empty_on_the_object_leaves_the_queue(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1", "art_2": b"png-2"})

    image_subjects.set_image_subjects(root, gid, "art_1", [])

    assert not image_subjects.subjects_path(root, gid).exists()
    assert image_subjects.reviewed_names(root, gid) == {"art_1"}
    assert image_subjects.read_subjects(root, gid) == {"art_1": []}
    assert [a["name"] for a in image_subjects.untagged(root)] == ["art_2"]


def test_a_legacy_key_wins_until_retagged(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    mara, _ = characters.create_character(root, "Mara", "main")
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1", "art_2": b"png-2"})
    other = _greeting_with(root, cid, vid, {"copy": b"png-1"})
    image_subjects.write_subjects(root, gid, {"art_1": [mara], "art_2": []})
    # The object behind art_1 gets a different answer from another greeting.
    image_subjects.set_image_subjects(root, other, "copy", [cid])

    assert image_subjects.read_subjects(root, gid) == {"art_1": [mara], "art_2": []}
    assert image_subjects.read_subjects(root, other) == {"copy": [cid]}

    image_subjects.set_image_subjects(root, gid, "art_1", [mara, cid])

    assert image_subjects._read_raw(root, gid) == {"art_2": []}     # only the retagged key went
    assert image_subjects.read_subjects(root, gid) == {"art_1": sorted([mara, cid]), "art_2": []}
    assert image_subjects.read_subjects(root, other) == {"copy": sorted([mara, cid])}


def test_remote_and_cross_world_keys_stay_in_the_sidecar(tmp_path, monkeypatch):
    """R11: a reference to a picture placed in another world keeps its tag in
    this greeting's sidecar, so the tag travels with this world's bundle."""
    realm, cid, vid = _scoped_world(monkeypatch, tmp_path)
    salt = worlds.world_root(worlds.create_world("Saltmarch"))
    characters.create_character(salt, "Seraphine", "main")
    assets.put_image(salt, cid, vid, "avatar", b"their-art", "png")
    remote = "https://example.test/art.png"
    cross = f"/api/worlds/{salt.name}/characters/{cid}/versions/{vid}/images/avatar"
    gid = _greeting_with(realm, cid, vid, {}, body=f"![A]({remote})\n![B]({cross})")
    # A legacy file with no placement is a legacy name: the sidecar too.
    legacy_dir = assets.version_dir(realm, gid, "default", base="greetings")
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "old.png").write_bytes(b"legacy")

    for key in (remote, cross, "old"):
        image_subjects.set_image_subjects(realm, gid, key, [cid])

    assert image_subjects._read_raw(realm, gid) == {remote: [cid], cross: [cid], "old": [cid]}
    raw = image_store.read(assets.image_id(salt, cid, vid, "avatar")).raw
    assert "associations" not in raw and "reviews" not in raw
    assert image_subjects.read_subjects(realm, gid) == {remote: [cid], cross: [cid], "old": [cid]}


def test_scope_uses_the_canonical_world_id(tmp_path, monkeypatch):
    """R10: a root reached under another spelling of its id (`REALM` for
    `realm` on a case-insensitive filesystem) still writes `world:realm`.
    Simulated here, on whatever filesystem the suite runs on."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    root = tmp_path / "home" / "worlds" / "REALM"
    cid, vid = characters.create_character(root, "Seraphine", "main")
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})
    monkeypatch.setattr(worlds.paths, "canonical_id",
                        lambda wid: "realm" if wid == "REALM" else wid)

    image_subjects.set_image_subjects(root, gid, "art_1", [cid])

    raw = _object(root, gid, "art_1")
    assert raw["reviews"] == {"subjects": ["world:realm"]}
    assert [a["scope"] for a in raw["associations"]] == ["world:realm"]
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid]}


def test_an_unconfirmed_object_write_keeps_the_sidecar_entry(tmp_path, monkeypatch):
    """R8: nothing written to the object, so the answer goes to the sidecar
    -- and an earlier legacy answer is replaced there, never dropped."""
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})
    image_subjects.write_subjects(root, gid, {"art_1": []})
    monkeypatch.setattr(image_subjects.image_store, "update", lambda image_id, change: False)

    image_subjects.set_image_subjects(root, gid, "art_1", [cid])

    assert image_subjects._read_raw(root, gid) == {"art_1": [cid]}
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid]}


def test_appearances_finds_object_tags_without_a_sidecar(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1", "art_2": b"png-2"})

    image_subjects.set_image_subjects(root, gid, "art_2", [cid])

    assert not image_subjects.subjects_path(root, gid).exists()
    assert [(a["gid"], a["name"]) for a in image_subjects.appearances(root, cid)] == [(gid, "art_2")]


@pytest.mark.parametrize("raw_edit", [
    {"associations": "garbled", "reviews": {"subjects": ["world:realm"]}},
    {"associations": [{"kind": "character", "relation": "subject", "scope": "world:realm",
                       "id": ["not", "a", "string"]}, "not-a-dict"],
     "reviews": {"subjects": ["world:realm"]}},
])
def test_a_malformed_object_answer_reads_as_reviewed_nobody(tmp_path, monkeypatch, raw_edit):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})
    image_id = assets.image_id(root, gid, "default", "art_1", base="greetings")
    assert image_store.update(image_id, lambda raw: {**raw, **raw_edit})

    assert image_subjects.read_subjects(root, gid) == {"art_1": []}
    assert image_subjects.untagged(root) == []
    # ...and a write over it still lands.
    image_subjects.set_image_subjects(root, gid, "art_1", [cid])
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid]}


@pytest.mark.parametrize("reviews", ["garbled", {"subjects": "world:realm"}, {"subjects": [["world:realm"]]}])
def test_a_malformed_review_list_reads_as_unreviewed(tmp_path, monkeypatch, reviews):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})
    image_id = assets.image_id(root, gid, "default", "art_1", base="greetings")
    assert image_store.update(image_id, lambda raw: {**raw, "reviews": reviews})

    assert image_subjects.read_subjects(root, gid) == {}
    assert [a["name"] for a in image_subjects.untagged(root)] == ["art_1"]
    image_subjects.set_image_subjects(root, gid, "art_1", [])
    assert image_subjects.read_subjects(root, gid) == {"art_1": []}


def _trap_placement_reads(monkeypatch):
    """Make `image_refs.read` raise once a catalog has been built: whatever
    answers after that must use the ids the catalog carried (the listing
    already read every placement it lists)."""
    armed = False
    real_catalog = image_subjects.greeting_images.catalog_with_slots
    real_read = image_refs.read

    def catalog(root, gid):
        nonlocal armed
        armed = False
        try:
            return real_catalog(root, gid)
        finally:
            armed = True

    def read(d, name):
        if armed:
            raise AssertionError(f"placement {name!r} read again after the catalog")
        return real_read(d, name)

    monkeypatch.setattr(image_subjects.greeting_images, "catalog_with_slots", catalog)
    monkeypatch.setattr(image_refs, "read", read)


def test_answers_never_reread_a_placement_the_catalog_read(tmp_path, monkeypatch):
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    assets.put_image(root, cid, vid, "gallery_1", b"ref-art", "png")
    url = f"/api/worlds/{root.name}/characters/{cid}/versions/{vid}/images/gallery_1"
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1", "art_2": b"png-2"},
                         body=f"![Art]({url})")
    image_subjects.set_image_subjects(root, gid, "art_1", [cid])
    image_subjects.set_image_subjects(root, gid, url, [])
    assert not image_subjects.subjects_path(root, gid).exists()

    _trap_placement_reads(monkeypatch)

    assert [a["name"] for a in image_subjects.untagged(root)] == ["art_2"]
    assert image_subjects.read_subjects(root, gid) == {"art_1": [cid], url: []}
    assert image_subjects.reviewed_names(root, gid) == {"art_1", url}
    assert [a["name"] for a in image_subjects.appearances(root, cid)] == ["art_1"]


def test_an_unarrived_placement_beside_a_legacy_file_answers_from_the_sidecar(tmp_path, monkeypatch):
    """A placement whose blob has not arrived is not the picture anybody sees:
    the legacy file of the same name is (`assets.path_in`'s fallback). So the
    key answers from the sidecar alone -- the object's answer, made for the
    picture that has not arrived, is not read -- and a write goes to the
    sidecar, leaving the object as it was (R2: not arrived, legacy path)."""
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    mara, _ = characters.create_character(root, "Mara", "main")
    gid = _greeting_with(root, cid, vid, {"art_1": b"png-1"})
    image_subjects.set_image_subjects(root, gid, "art_1", [cid])
    image_id = assets.image_id(root, gid, "default", "art_1", base="greetings")
    obj = image_store.read(image_id)
    image_store.blob_path(obj.blob_sha256, obj.ext).unlink()
    d = assets.version_dir(root, gid, "default", base="greetings")
    (d / "art_1.png").write_bytes(b"legacy-art")
    assert assets.resolve(d, "art_1") is None and image_refs.read(d, "art_1").image == image_id

    assert image_subjects.read_subjects(root, gid) == {}
    assert [a["name"] for a in image_subjects.untagged(root)] == ["art_1"]

    image_subjects.set_image_subjects(root, gid, "art_1", [mara])

    assert image_subjects._read_raw(root, gid) == {"art_1": [mara]}
    assert image_subjects.read_subjects(root, gid) == {"art_1": [mara]}
    raw = image_store.read(image_id).raw
    assert [a["id"] for a in raw["associations"]] == [cid]


def test_a_reference_into_a_half_promoted_record_rolls_the_promotion_forward(tmp_path, monkeypatch):
    """A promote crashed mid-swap: the journal is there, the avatar already
    holds the post-state, the promoted slot still the pre-state. A greeting
    referencing that slot must see the picture the slot ends up with -- the
    catalog finishes the promotion before resolving it, as `image_path` does
    -- so the answer lands on THAT object, not on the one leaving."""
    root, cid, vid = _scoped_world(monkeypatch, tmp_path)
    d = assets.version_dir(root, cid, vid)
    assets.put_image(root, cid, vid, assets.AVATAR, b"avatar-art", "png")
    assets.put_image(root, cid, vid, "gallery_1", b"gallery-art", "png")
    a = assets.image_id(root, cid, vid, assets.AVATAR)
    g = assets.image_id(root, cid, vid, "gallery_1")
    image_refs.write_journal(d, {"name": "gallery_1",
                                 "pre": {"avatar": a, "gallery_1": g},
                                 "post": {"avatar": g, "gallery_1": a},
                                 "desc": {"avatar": None, "gallery_1": None}})
    image_refs.write(d, assets.AVATAR, g)          # the swap's first write landed; then the crash
    url = f"/api/worlds/{root.name}/characters/{cid}/versions/{vid}/images/gallery_1"
    gid = _greeting_with(root, cid, vid, {}, body=f"![Art]({url})")

    image_subjects.set_image_subjects(root, gid, url, [cid])

    assert "reviews" not in image_store.read(g).raw
    assert image_store.read(a).raw["reviews"] == {"subjects": ["world:realm"]}
    assert image_refs.read_journal(d) is None
    assert image_refs.read(d, "gallery_1").image == a
    assert image_subjects.read_subjects(root, gid) == {url: [cid]}
