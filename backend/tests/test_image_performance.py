"""Filesystem-work regressions from #477, against real isolated image stores."""

import io
import json
import os
from unittest.mock import patch

import pytest
from PIL import Image

from grimoire.routes import characters as character_routes
from grimoire.routes import greetings as greeting_routes
from grimoire.routes import world_images as image_routes
from grimoire.store import (
    assets,
    campaigns,
    characters,
    fork,
    greeting_images,
    greetings,
    image_collections,
    image_descriptions,
    image_refs,
    image_scopes,
    image_store,
    image_subjects,
    image_usage,
    overlay,
    pcs,
    statcache,
    world_images,
    worlds,
)
from tests.collection_fixtures import format1, format2

COLLECTION = "0123456789abcdef0123456789abcdef"
WORLD_SCOPE = "world:realm"
CAMPAIGN_SCOPE = "campaign:saltmarch"


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return wid, worlds.world_root(wid)


@pytest.mark.parametrize("reader", ["gallery", "filter", "appearances"])
def test_greeting_subject_read_builds_one_catalog(world, reader):
    wid, root = world
    actor, vid = characters.create_character(root, "Seraphine", "main")
    gid = greetings.create_greeting(root, "Opener", actor, vid, "body")
    assets.put_image(root, gid, "default", "art_1", png(), "png", base="greetings")
    image_subjects.set_image_subjects(root, gid, "art_1", [actor])
    with patch.object(greeting_images, "catalog_with_slots", wraps=greeting_images.catalog_with_slots) as calls:
        if reader == "gallery":
            rows = character_routes.list_world_gallery(wid)
            assert next(r for r in rows if r["kind"] == "greetings")["subjects"] == [actor]
        elif reader == "filter":
            assert character_routes._GreetingSubjects(root).lookup(gid, "art_1") == [actor]
        else:
            assert image_subjects.appearances(root, actor) == [{"gid": gid, "name": "art_1"}]
        assert calls.call_count == 1


@pytest.mark.parametrize("source", ["member", "format1", "library", "own"])
def test_greeting_url_resolves_local_image_once(world, source):
    wid, root = world
    if source == "member":
        [ident] = format2(wid, COLLECTION, png())
        row = {"url": f"/api/worlds/{wid}/image-collections/{COLLECTION}/members/0"}
    elif source == "format1":
        [name] = format1(wid, COLLECTION, png())
        ident = assets.resolve(world_images.images_dir(wid), name).image_id
        row = {"url": f"/api/worlds/{wid}/image-collections/{COLLECTION}/members/0"}
    elif source == "library":
        world_images.put_image(wid, "art_1", png(), "png")
        ident = assets.resolve(world_images.images_dir(wid), "art_1").image_id
        row = {"url": f"/api/worlds/{wid}/images/art_1"}
    else:
        actor, vid = characters.create_character(root, "Seraphine", "main")
        gid = greetings.create_greeting(root, "Opener", actor, vid, "body")
        assets.put_image(root, gid, "default", "art_1", png(), "png", base="greetings")
        ident = assets.resolve(assets.version_dir(root, gid, "default", "greetings"), "art_1").image_id
        row = {"gid": gid, "name": "art_1"}
    with patch.object(image_refs, "resolve_ref", wraps=image_refs.resolve_ref) as resolutions, \
            patch.object(image_collections, "read", wraps=image_collections.read) as manifests:
        got = greeting_routes._greeting_image_urls(root, wid, row)
    assert got["image_id"] == ident
    assert "?v=" in got["url"] and "thumb" in got
    assert resolutions.call_count == 1
    assert manifests.call_count == (1 if source in ("member", "format1") else 0)


@pytest.mark.parametrize("base", ["characters", "pcs"])
def test_overlay_detail_reuses_caption_listing(world, base):
    wid, root = world
    actor, vid = (characters.create_character(root, "Seraphine", "main") if base == "characters"
                  else pcs.create_pc(root, "Mara", [], "main"))
    assets.put_image(root, actor, vid, "gallery_1", png(), "png", base=base)
    image_descriptions.set_description(root, actor, vid, "gallery_1", "The quay.", base=base)
    camp = campaigns.create_campaign("Saltmarch", wid)
    read = overlay.read_character if base == "characters" else overlay.read_pc
    with patch.object(overlay, "_image_halves", wraps=overlay._image_halves) as calls:
        detail = read(camp, actor)
    assert detail["versions"][0]["image_descriptions"] == {"gallery_1": "The quay."}
    assert calls.call_count == 1


def test_collection_response_reads_one_manifest(world):
    wid, _ = world
    format2(wid, COLLECTION, png())
    with patch.object(image_collections, "read", wraps=image_collections.read) as calls:
        response = image_routes.get_world_image_collection(wid, COLLECTION)
    assert json.loads(response.body)["format"] == 2
    assert calls.call_count == 1


def test_usage_reads_world_manifests_once(world):
    wid, _ = world
    [name] = format1(wid, COLLECTION, png())
    ident = assets.resolve(world_images.images_dir(wid), name).image_id
    with patch.object(image_collections, "read", wraps=image_collections.read) as calls:
        usage = image_usage.find(ident)
    assert usage["collections"] == [{"wid": wid, "collection": COLLECTION}]
    assert calls.call_count == 1


def test_usage_skips_manifest_reader_without_directory(world):
    world_images.put_image(world[0], "art_1", png(), "png")
    ident = assets.resolve(world_images.images_dir(world[0]), "art_1").image_id
    with patch.object(image_usage, "_manifests", wraps=image_usage._manifests) as calls:
        assert len(image_usage.find(ident)["world_images"]) == 1
    assert calls.call_count == 0


@pytest.mark.parametrize("fmt", [1, 2])
def test_library_upload_checks_legacy_state_once(world, fmt):
    wid, _ = world
    (format1 if fmt == 1 else format2)(wid, COLLECTION, png())
    with patch.object(image_collections, "has_format1", wraps=image_collections.has_format1) as calls:
        world_images.put_image(wid, "art_1", png("blue"), "png")
    assert len(world_images.list_images(wid)) == (2 if fmt == 1 else 1)
    assert calls.call_count == 1


def test_equal_shared_description_cleans_legacy_without_rewrite(world):
    wid, _ = world
    world_images.put_image(wid, "art_1", png(), "png")
    d = world_images.images_dir(wid)
    image_descriptions.set_in(d, "art_1", "The quay.")
    image_descriptions.carry_legacy(d, "art_1", "Old text.")
    with patch.object(image_store, "_write_sidecar", wraps=image_store._write_sidecar) as writes:
        image_descriptions.set_in(d, "art_1", "The quay.")
    assert image_descriptions.read_raw(d) == {}
    assert image_descriptions.read_in(d) == {"art_1": "The quay."}
    assert writes.call_count == 0


def test_warm_scope_sweep_does_not_read_unrelated_sidecars(world, monkeypatch):
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    ident = image_store.ingest(png(), "png").id
    image_store.update(ident, lambda raw: {**raw, "reviews": {"subjects": ["world:realm"]}})
    assert image_scopes.strip(CAMPAIGN_SCOPE) == 0
    with patch.object(image_store, "read_fresh", wraps=image_store.read_fresh) as reads:
        assert image_scopes.strip(CAMPAIGN_SCOPE) == 0
    assert reads.call_count == 0


def test_early_failed_fork_does_not_strip_scope(world, monkeypatch):
    camp = campaigns.create_campaign("Saltmarch", world[0])
    def failed_copy(*args):
        raise OSError("copy stopped")
    monkeypatch.setattr(fork, "_copy", failed_copy)
    with patch.object(image_scopes, "strip", wraps=image_scopes.strip) as strips, \
            pytest.raises(OSError, match="copy stopped"):
        fork.fork_campaign(camp, "Realm")
    assert strips.call_count == 0
    assert len(campaigns.list_campaigns()) == 1


@pytest.mark.parametrize("text", ["", "The quay."])
def test_equal_description_noop_still_resolves_conflicts(world, text):
    wid, _ = world
    world_images.put_image(wid, "art_1", png(), "png")
    d = world_images.images_dir(wid)
    image_descriptions.set_in(d, "art_1", text)
    ident = assets.resolve(d, "art_1").image_id
    image_store.update(ident, lambda raw: {**raw, "description_conflicts": [{"text": "Other"}]})
    image_descriptions.set_in(d, "art_1", text)
    assert "description_conflicts" not in image_store.read_fresh(ident).raw
    with patch.object(image_store, "_write_sidecar", wraps=image_store._write_sidecar) as writes:
        image_descriptions.set_in(d, "art_1", text)
    assert image_descriptions.read_in(d) == {"art_1": text}
    assert writes.call_count == 0

@pytest.mark.parametrize("field", ["associations", "reviews"])
@pytest.mark.parametrize("write", ["replace", "inplace"])
def test_scope_candidates_notice_external_replacement(world, monkeypatch, field, write):
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    ident = image_store.ingest(png(), "png").id
    value = ([{"scope": "world:Realm"}] if field == "associations"
             else {"subjects": ["world:Realm"]})
    image_store.update(ident, lambda raw: {**raw, field: value})
    assert image_scopes.strip(WORLD_SCOPE) == 0
    path = image_store.object_path(ident)
    before = path.stat()
    # Same bytes/size/mtime, differing only in the corrected scope's case.
    # Atomic replacement changes the inode; in-place editing needs ChangeTime.
    changed = path.read_bytes().replace(b"world:Realm", b"world:realm")
    if write == "replace":
        replacement = path.with_suffix(".new")
        replacement.write_bytes(changed)
        replacement.replace(path)
    else:
        path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert image_scopes.strip(WORLD_SCOPE) == 1
    cleaned = image_store.read_fresh(ident).raw
    assert cleaned[field] == ([] if field == "associations" else {"subjects": []})


def test_scope_candidate_cache_is_store_specific(world, monkeypatch, tmp_path):
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    ident = image_store.ingest(png(), "png").id
    assert image_scopes.strip(WORLD_SCOPE) == 0
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "other"))
    assert image_store.ingest(png(), "png").id == ident
    image_store.update(ident, lambda raw: {**raw, "reviews": {"subjects": ["world:realm"]}})
    assert image_scopes.strip(WORLD_SCOPE) == 1
    assert image_store.read_fresh(ident).raw["reviews"] == {"subjects": []}


@pytest.mark.parametrize("trust", ["recent", "unvouched"])
def test_scope_candidates_bypass_untrusted_stamps(world, monkeypatch, trust):
    ident = image_store.ingest(png(), "png").id
    if trust == "recent":
        monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 10**20)
    else:
        stamp = statcache.stamp
        def unvouched(path):
            value = stamp(path)
            return (value[0], value[1], statcache.UNVOUCHED, value[3], value[4]) if value else None
        monkeypatch.setattr(statcache, "stamp", unvouched)
    with patch.object(image_store, "read_fresh", wraps=image_store.read_fresh) as reads:
        assert image_scopes.strip(WORLD_SCOPE) == 0
        assert image_scopes.strip(WORLD_SCOPE) == 0
    assert reads.call_count == 2
    assert image_store.read_fresh(ident) is not None


def test_collection_response_preserves_404_when_legacy_member_vanishes(world, monkeypatch):
    wid, _ = world
    [name] = format1(wid, COLLECTION, png())
    directory = world_images.images_dir(wid)
    image_refs.delete(directory, name)
    (directory / f"{name}.png").write_bytes(png())
    version = assets.image_version

    def vanish(path):
        path.unlink()
        return version(path)

    monkeypatch.setattr(assets, "image_version", vanish)
    with pytest.raises(image_routes.HTTPException) as exc:
        image_routes.get_world_image_collection(wid, COLLECTION)
    assert exc.value.status_code == 404


def test_scope_candidate_cache_never_pairs_one_root_with_another(world, monkeypatch, tmp_path):
    # A root switch between the stamp and the read must not cache library B's
    # scopes under library A's stamp: A's next sweep would then skip a strip.
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    home_a = os.environ["GRIMOIRE_HOME"]
    home_b = str(tmp_path / "other")
    ident = image_store.ingest(png(), "png").id
    image_store.update(ident, lambda raw: {**raw, "reviews": {"subjects": [WORLD_SCOPE]}})
    monkeypatch.setenv("GRIMOIRE_HOME", home_b)
    assert image_store.ingest(png(), "png").id == ident
    monkeypatch.setenv("GRIMOIRE_HOME", home_a)

    stamp = statcache.stamp
    switched = []

    def switching(path):
        value = stamp(path)
        if not switched:
            switched.append(path)
            monkeypatch.setenv("GRIMOIRE_HOME", home_b)
        return value

    monkeypatch.setattr(statcache, "stamp", switching)
    image_scopes._memberships(ident)
    monkeypatch.setattr(statcache, "stamp", stamp)
    monkeypatch.setenv("GRIMOIRE_HOME", home_a)
    assert image_scopes.strip(WORLD_SCOPE) == 1
    assert image_store.read_fresh(ident).raw["reviews"] == {"subjects": []}
