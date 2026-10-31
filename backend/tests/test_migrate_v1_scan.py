"""Sprint 011 acceptance: a read-only inventory of a synthetic Grimoire 0.2.1 library.

Every scan uses an explicit synthetic home and environment, never the sentinel
home, and an explicit destination that must still not exist afterwards.
"""

import json

import pytest

from grimoire.migrate_v1 import SCHEMA, render_json, render_summary, scan
from v1_fixture import BLUE_ID, CAMPAIGN, MARA, MISSING_ID, RED_ID, SID, build_library, tree_digest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    return h


@pytest.fixture
def library(tmp_path):
    return build_library(tmp_path / "v1")


def run(library, home, **kwargs):
    return scan(library, archive_id="fixture", destination=home.parent / "v2", home=home,
                environ={}, **kwargs).manifest


@pytest.fixture
def manifest(library, home):
    return run(library, home)


def by_path(rows, path):
    return next(r for r in rows if r["path"] == path)


def test_a_one_shared_object_stays_two_distinct_placements(manifest):
    images = manifest["images"]
    assert [o["id"] for o in images["objects"]] == sorted([RED_ID, BLUE_ID])
    red = [p for p in images["placements"] if p["image"] == RED_ID]
    assert {(p["slot"], p["owner"]["version"], p["status"]) for p in red} == {
        ("avatar", "default", "resolved"), ("expression", "older", "resolved")}
    assert len({p["legacy_id"] for p in red}) == 2
    expression = next(p for p in red if p["slot"] == "expression")
    assert (expression["outfit"], expression["name"], expression["live"]) == ("casual", "neutral", True)
    avatar = by_path(images["placements"], f"{MARA}/assets/default/image-refs/avatar.json")
    assert avatar["focus"] == 40
    assert [b["objects"] for b in images["blobs"] if RED_ID in b["objects"]] == [[RED_ID]]


def test_b_same_named_characters_in_two_worlds_are_never_linked(manifest):
    candidates = {r["path"]: r for r in manifest["identity_candidates"]["definitions"]}
    salt = candidates[f"{MARA}/default.json"]["candidate"]["text"]
    realm = candidates["worlds/realm/characters/mara/default.json"]["candidate"]["text"]
    assert salt == "world:saltmarch/character/mara.default"
    assert realm == "world:realm/character/mara.default"
    links = manifest["identity_candidates"]["proposed_links"]
    assert all(realm not in (link["subject"], link["object"]) for link in links)
    groups = manifest["identity_candidates"]["same_name_groups"]
    assert groups == [{"kind": "character", "name_key": "mara", "relation": "none", "paths": [
        "worlds/realm/characters/mara/character.md", f"{MARA}/character.md"]}]


def test_c_a_pc_is_a_character_whose_control_is_a_role(manifest):
    candidates = {r["path"]: r for r in manifest["identity_candidates"]["definitions"]}
    assert candidates["worlds/saltmarch/pcs/winifred/default.md"]["candidate"]["kind"] == "character"
    instances = {i["v1_ref"]: i for i in manifest["identity_candidates"]["instances"]}
    assert instances["pcs/winifred"]["control"] == "player"
    assert instances["characters/mara"]["control"] == "director"
    assert instances["pcs/winifred"]["candidate"] == "campaign:the-drowned-ledger/character/winifred"
    assert instances["characters/mara"]["derives_from"] == "world:saltmarch/character/mara.default"


def test_d_an_alternate_portrayal_is_a_separate_entity(manifest):
    candidates = {r["path"]: r for r in manifest["identity_candidates"]["definitions"]}
    older = candidates[f"{MARA}/older.json"]
    assert older["candidate"]["entity_id"] == "mara.older"
    assert older["v1"] == {"world": "saltmarch", "base": "characters", "record": "mara", "version": "older"}
    assert {"relation": "alternate_portrayal", "subject": "world:saltmarch/character/mara.default",
            "object": "world:saltmarch/character/mara.older"} in manifest["identity_candidates"]["proposed_links"]
    actor = next(a for a in manifest["actors"] if a["path"] == MARA)
    assert {v["id"]: v["label"] for v in actor["versions"]} == {"default": "default", "older": "Older"}


def test_e_scene_posts_keep_order_authorship_and_exact_content(manifest):
    scene = manifest["scenes"][0]
    assert (scene["sid"], scene["identity"], scene["grammar"], scene["authorship"]) == (
        SID, "0" * 31 + "1", "modern", "resolved")
    posts = [(p["index"], p["speaker"], p["role"], p["post_id"]) for p in scene["posts"]]
    assert posts == [(0, "Winifred", "user", "0" * 30 + "11"), (1, "Mara", "assistant", "0" * 30 + "12")]
    import hashlib
    assert scene["posts"][0]["content_sha256"] == hashlib.sha256(b"I knock on the door.").hexdigest()


def test_f_a_hidden_inherited_picture_and_a_campaign_crop(manifest):
    layering = {r["name"]: r for r in manifest["images"]["campaign_layering"]}
    assert layering["gallery_1"]["state"] == "hidden"
    assert layering["avatar"]["state"] == "focus_override"
    campaign = manifest["campaigns"][0]
    appearances = {a["ref"]: a for a in campaign["appearances"]}
    assert appearances["characters/mara"]["provenance"] == "library"
    assert appearances["characters/mara"]["sync"] == "current"
    states = {row["ref"]: row["state"] for row in campaign["inheritance"]}
    assert states["lore/the-salt-pact"] == "inherited"
    assert states["characters/mara"] == "materialized"


def test_g_a_broken_reference_is_reported_not_substituted(manifest):
    broken = by_path(manifest["images"]["placements"],
                     "worlds/realm/characters/mara/assets/default/image-refs/avatar.json")
    assert (broken["image"], broken["status"]) == (MISSING_ID, "dangling_object")
    codes = {(f["code"], tuple(f["paths"])) for f in manifest["findings"]}
    assert ("dangling_object", (broken["path"],)) in codes
    group = next(r for r in manifest["records"] if r["kind"] == "groups")
    assert {(r["target"], r["status"]) for r in group["refs"]} == {
        ("characters:mara", "resolved"), ("characters:ghost", "dangling")}


def test_unknown_fields_are_named_never_copied(manifest):
    unknown = {tuple(f["paths"]): f["detail"] for f in manifest["findings"] if f["code"] == "unknown_fields"}
    assert unknown[(f"{MARA}/older.json",)] == "data.extensions.x_synthetic_note"
    assert unknown[("worlds/saltmarch/locations/tidewatch-keep.md",)] == "lantern_color"
    assert "amber" not in render_json(manifest).decode()


def test_greeting_image_reference_resolves_to_a_slot(manifest):
    [ref] = manifest["images"]["references"]
    assert ref["status"] == "resolved"
    assert ref["slot"]["name"] == "avatar"


def test_h_scanning_twice_is_byte_identical_and_changes_nothing(library, home, tmp_path):
    before = tree_digest(library)
    first = render_json(run(library, home))
    second = render_json(run(library, home))
    assert first == second
    assert tree_digest(library) == before
    assert not (tmp_path / "v2").exists()
    manifest = json.loads(first)
    assert manifest["schema"] == SCHEMA
    assert manifest["status"] == "ready_for_review"


def test_no_temporary_path_leaks_outside_the_roots(manifest, tmp_path):
    roots = manifest.pop("roots")
    assert str(tmp_path) in roots["source"]
    assert str(tmp_path) not in render_json(manifest).decode()
    assert str(tmp_path) not in render_summary({**manifest, "roots": roots})


def test_i_zero_inference(manifest):
    assert manifest["inference"] == {"provider_calls": 0}


def test_every_entry_has_exactly_one_disposition_and_interpreted_files_were_read(manifest):
    dispositions = {e["disposition"] for e in manifest["entries"]}
    assert dispositions <= {"directory", "interpreted", "hashed", "recognized_unsupported", "excluded",
                            "ignored", "unknown", "refused", "unreadable"}
    assert not [f for f in manifest["findings"] if f["code"] == "uninterpreted"]
    assert len({e["path"] for e in manifest["entries"]}) == len(manifest["entries"])


def test_the_fingerprint_follows_content(library, home):
    first = run(library, home)["source_fingerprint"]
    (library / "worlds" / "saltmarch" / "lore" / "the-salt-pact.md").write_text(
        "---\nname: The Salt Pact\n---\n\nRevised.\n", encoding="utf-8")
    assert run(library, home)["source_fingerprint"] != first


GOLDEN = """\
Grimoire 0.2.1 inventory (grimoire.v1-inventory/1)
archive: fixture
status: ready_for_review
provider calls: 0

counts:
  worlds: 2
  campaigns: 1
  actors: 5
  portrayals: 6
  records: 4
  scenes: 1
  posts: 2
  image_objects: 2
  image_blobs: 2
  legacy_images: 0
  losses: 0
  unresolved: 0
  entries: directory 43, hashed 2, interpreted 31
  placements: dangling_object 1, focus_only 1, resolved 3
  expression_layers: available 1
  image_references: resolved 1
  findings: blocking 0, warning 2, info 2

blocking findings: 0
warnings: dangling_object 1, dangling_ref 1
info: unknown_fields 2

next step: review warnings, losses and unresolved items in the report; nothing has been imported.
"""


def test_golden_summary(manifest):
    assert render_summary(manifest) == GOLDEN


def test_the_path_walk_used_on_windows_sees_the_same_library(library, home):
    from grimoire.migrate_v1 import Options
    assert render_json(run(library, home, _options=Options(path_walk=True))) == render_json(run(library, home))
