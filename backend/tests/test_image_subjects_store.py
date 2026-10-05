from urllib.parse import quote

import pytest

from grimoire.store import assets, characters, entities, greetings, image_subjects, pcs


def _world(tmp_path, images=("art_1", "art_2")):
    cid, vid = characters.create_character(tmp_path, "Mira", "main")
    gid = greetings.create_greeting(tmp_path, "Opener", cid, vid, "body")
    for name in images:
        assets.put_image(tmp_path, gid, "default", name, b"png", "png", base="greetings")
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
    real_catalog = image_subjects.greeting_images.catalog
    first = True

    def restore_after_inventory(root, greeting):
        nonlocal first
        images = real_catalog(root, greeting)
        if first:
            first = False
            greetings.update_greeting(root, greeting, body=f"![Art]({restored})")
        return images

    monkeypatch.setattr(image_subjects.greeting_images, "catalog", restore_after_inventory)
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
            assets.put_image(tmp_path, gid, "default", n, b"p", "png", base="greetings")
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
