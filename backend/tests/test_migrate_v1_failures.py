"""Sprint 011 failure scenarios: the scanner fails closed and never silently skips."""

import errno
import json
import os
import socket
import sys

import pytest

from grimoire.migrate_v1 import NotAV1Library, Options, SourceChanged, render_json, render_summary, scan
from grimoire.migrate_v1._report import BLOCKING_SHOWN
from v1_fixture import CAMPAIGN, MARA, SID, build_library, card, frontmatter, tree_digest

POSIX = os.name == "posix"


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


def codes(manifest, severity=None):
    return {(f["code"], tuple(f["paths"])) for f in manifest["findings"]
            if severity is None or f["severity"] == severity}


def entry(manifest, path):
    return next(e for e in manifest["entries"] if e["path"] == path)


def symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("symlinks cannot be created here")


@pytest.mark.parametrize("where", ["outside", "inside"])
def test_f1_links_are_refused_and_never_followed(library, home, tmp_path, where):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"SENTINEL-LINK-TARGET")
    target = secret if where == "outside" else library / "worlds" / "saltmarch" / "world.md"
    symlink(library / "worlds" / "saltmarch" / "lore" / "linked.md", target)
    manifest = run(library, home)
    row = entry(manifest, "worlds/saltmarch/lore/linked.md")
    assert (row["disposition"], row["reason"], row["sha256"]) == ("refused", "link", None)
    assert manifest["status"] == "blocked"
    assert ("refused_entry", ("worlds/saltmarch/lore/linked.md",)) in codes(manifest, "blocking")
    import hashlib
    assert hashlib.sha256(b"SENTINEL-LINK-TARGET").hexdigest() not in render_json(manifest).decode()


def test_f1_a_linked_directory_is_not_descended(library, home, tmp_path):
    outside = tmp_path / "elsewhere"
    (outside / "inner").mkdir(parents=True)
    (outside / "inner" / "x.md").write_text("x", encoding="utf-8")
    symlink(library / "worlds" / "saltmarch" / "locations" / "far", outside, directory=True)
    manifest = run(library, home)
    assert entry(manifest, "worlds/saltmarch/locations/far")["disposition"] == "refused"
    assert not [e for e in manifest["entries"] if e["path"].startswith("worlds/saltmarch/locations/far/")]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
def test_f1_a_junction_is_refused(library, home, tmp_path):
    import _winapi
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(library / "worlds" / "saltmarch" / "j"))
    manifest = run(library, home)
    assert entry(manifest, "worlds/saltmarch/j")["disposition"] == "refused"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_a_fifo_is_refused_without_blocking(library, home):
    os.mkfifo(library / "worlds" / "saltmarch" / "pipe.md")
    manifest = run(library, home)
    assert entry(manifest, "worlds/saltmarch/pipe.md")["reason"] == "fifo"
    assert manifest["status"] == "blocked"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs UNIX sockets")
def test_a_socket_is_refused(library, home, monkeypatch):
    monkeypatch.chdir(library / "worlds" / "saltmarch")
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind("sock")
        manifest = run(library, home)
    finally:
        server.close()
    assert entry(manifest, "worlds/saltmarch/sock")["reason"] == "socket"


def test_f2_a_malformed_card_is_unresolved_and_the_rest_scans(library, home):
    (library / "worlds" / "realm" / "characters" / "mara" / "default.json").write_text("{not json", encoding="utf-8")
    manifest = run(library, home)
    assert ("malformed_card", ("worlds/realm/characters/mara/default.json",)) in codes(manifest)
    realm = next(a for a in manifest["actors"] if a["path"] == "worlds/realm/characters/mara")
    assert realm["versions"][0]["malformed"] is True
    assert any(a["path"] == MARA for a in manifest["actors"])


@pytest.mark.parametrize("payload", ["[" * 100000, "1" * 5000, "﻿{}"])
def test_hostile_json_is_malformed_not_a_crash(library, home, payload):
    (library / "worlds" / "realm" / "characters" / "mara" / "default.json").write_text(payload, encoding="utf-8")
    manifest = run(library, home)
    assert ("malformed_card", ("worlds/realm/characters/mara/default.json",)) in codes(manifest)


def test_f3_a_format_character_name_is_listed_not_normalized(library, home):
    odd = library / "worlds" / "saltmarch" / "lore" / "salt‍pact"
    odd.mkdir()
    (odd / "note.txt").write_text("x", encoding="utf-8")
    manifest = run(library, home)
    refused = {f["paths"][0] for f in manifest["findings"] if f["code"] == "legacy_id_refused"}
    assert "worlds/saltmarch/lore/salt‍pact" in refused
    assert entry(manifest, "worlds/saltmarch/lore/salt‍pact/note.txt")["legacy_id"] is None


def test_f3_normalization_twins_are_both_kept(library, home):
    lore = library / "worlds" / "saltmarch" / "lore"
    try:
        (lore / "café.md").write_text("---\nname: A\n---\n", encoding="utf-8")
        (lore / "café.md").write_text("---\nname: B\n---\n", encoding="utf-8")
    except OSError:
        pytest.skip("this filesystem refuses one spelling")
    if len([p for p in os.listdir(lore) if p.startswith("caf")]) < 2:
        pytest.skip("this filesystem normalizes names")
    manifest = run(library, home)
    groups = [f["paths"] for f in manifest["findings"] if f["code"] == "normalization_collision"]
    assert sorted(["worlds/saltmarch/lore/café.md", "worlds/saltmarch/lore/café.md"]) in groups
    assert len([r for r in manifest["records"] if r["id"].startswith("caf")]) == 2


def test_case_twins_are_reported(library, home):
    lore = library / "worlds" / "saltmarch" / "lore"
    (lore / "Pact.md").write_text("---\nname: Pact\n---\n", encoding="utf-8")
    (lore / "pact.md").write_text("---\nname: pact\n---\n", encoding="utf-8")
    if len([p for p in os.listdir(lore) if p.lower() == "pact.md"]) < 2:
        pytest.skip("this filesystem folds case")
    manifest = run(library, home)
    assert ["worlds/saltmarch/lore/Pact.md", "worlds/saltmarch/lore/pact.md"] in [
        f["paths"] for f in manifest["findings"] if f["code"] == "case_collision"]


@pytest.mark.skipif(not POSIX, reason="Windows cannot store these names")
def test_ids_v1_would_not_list_are_reported(library, home):
    (library / "worlds" / "saltmarch" / "lore" / "trailing .md").write_text("---\nname: T\n---\n", encoding="utf-8")
    manifest = run(library, home)
    assert ("invalid_v1_id", ("worlds/saltmarch/lore/trailing .md",)) in codes(manifest)


@pytest.mark.skipif(not POSIX or sys.platform == "darwin", reason="needs byte filenames")
def test_an_undecodable_name_is_listed_and_printed_safely(library, home):
    try:
        with open(os.fsencode(str(library / "worlds" / "saltmarch" / "lore")) + b"/bad\xff.md", "wb") as f:
            f.write(b"---\nname: Bad\n---\n")
    except OSError:
        pytest.skip("this filesystem refuses undecodable names")
    manifest = run(library, home)
    assert ("legacy_id_refused", ("worlds/saltmarch/lore/bad\udcff.md",)) in codes(manifest)
    render_summary(manifest).encode("utf-8")  # no lone surrogate reaches the terminal
    render_json(manifest)


@pytest.mark.skipif(not POSIX, reason="Windows cannot store an ESC in a name")
def test_terminal_escapes_in_names_are_neutralized(library, home):
    os.mkfifo(library / "worlds" / "saltmarch" / "\x1b[31mred") if hasattr(os, "mkfifo") else pytest.skip()
    summary = render_summary(run(library, home))
    assert "\x1b" not in summary
    assert "\\u001b[31mred" in summary


def test_f4_an_unreadable_file_blocks(library, home):
    target = "worlds/saltmarch/lore/the-salt-pact.md"
    fail = lambda path, op: errno.EACCES if (path, op) == (target, "open") else None  # noqa: E731
    manifest = run(library, home, _options=Options(fail=fail))
    assert entry(manifest, target)["disposition"] == "unreadable"
    assert ("unreadable", (target,)) in codes(manifest, "blocking")


@pytest.mark.skipif(not POSIX or not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs non-root POSIX")
def test_f4_a_permission_denied_directory_blocks(library, home):
    locked = library / "worlds" / "saltmarch" / "lore"
    locked.chmod(0)
    try:
        manifest = run(library, home)
    finally:
        locked.chmod(0o755)
    assert entry(manifest, "worlds/saltmarch/lore")["disposition"] == "unreadable"
    assert manifest["status"] == "blocked"


def test_a_mount_point_blocks_and_is_not_descended(library, home):
    def device_of(path, info):
        return info.st_dev + 1 if path == "worlds/realm" else info.st_dev
    manifest = run(library, home, _options=Options(device_of=device_of))
    assert entry(manifest, "worlds/realm")["reason"] == "mount_point"
    assert not [e for e in manifest["entries"] if e["path"].startswith("worlds/realm/")]
    assert manifest["status"] == "blocked"


def test_f7_a_change_during_the_scan_is_fatal(library, home):
    def edit():
        (library / "worlds" / "saltmarch" / "world.md").write_text("---\nname: Edited\n---\n", encoding="utf-8")
    with pytest.raises(SourceChanged):
        run(library, home, _options=Options(after_first_pass=edit))


def test_f8_a_directory_without_worlds_or_campaigns_is_not_a_library(tmp_path, home):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("x", encoding="utf-8")
    with pytest.raises(NotAV1Library):
        run(other, home)


def test_f9_newer_settings_format_blocks(library, home):
    (library / "config.md").write_text("---\ninference_format: 3\n---\n\n", encoding="utf-8")
    manifest = run(library, home)
    assert ("newer_format", ("config.md",)) in codes(manifest, "blocking")


def test_f9_a_full_copy_campaign_is_not_given_overlay_semantics(library, home):
    meta = library / CAMPAIGN / "campaign.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace("world_copy: overlay", "world_copy: full"),
                    encoding="utf-8")
    manifest = run(library, home)
    campaign = manifest["campaigns"][0]
    assert (campaign["layout"], campaign["semantics"], campaign["inheritance"]) == (
        "legacy_full_copy", "unresolved", [])
    assert manifest["images"]["campaign_layering"] == []
    assert any(u["what"] == "campaign_inheritance" for u in manifest["unresolved"])


def test_f10_credentials_are_never_read_or_reported(library, home):
    (library / "llm_connections").mkdir()
    (library / "llm_connections" / "x.md").write_text("---\napi_key: SENTINEL-SECRET-1\n---\n", encoding="utf-8")
    (library / "config.md").write_text(
        "---\ninference_format: 2\nopenrouter_key: SENTINEL-SECRET-2\n---\n\n", encoding="utf-8")
    manifest = run(library, home)
    text = render_json(manifest).decode() + render_summary(manifest)
    assert "SENTINEL-SECRET" not in text
    assert entry(manifest, "llm_connections")["disposition"] == "excluded"
    assert not [e for e in manifest["entries"] if e["path"].startswith("llm_connections/")]
    assert manifest["source_format"]["markers"] == {"inference_format": "2"}


def test_an_excluded_link_is_informational(library, home, tmp_path):
    (tmp_path / "far-backups").mkdir()
    symlink(library / "backups", tmp_path / "far-backups", directory=True)
    manifest = run(library, home)
    assert ("excluded_link", ("backups",)) in codes(manifest, "info")
    assert manifest["status"] == "ready_for_review"


def test_f11_an_incompletely_synced_expression_layer_is_unavailable_not_empty(library, home):
    manifest_path = library / MARA / "assets" / "older" / "expressions" / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["content_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    manifest = run(library, home)
    [layer] = manifest["images"]["expression_layers"]
    assert layer["status"] == "unavailable"
    assert "synchronized" in layer["reason"]


def test_f11_an_undeclared_outfit_makes_the_whole_layer_unavailable(library, home):
    revision = library / MARA / "assets" / "older" / "expressions" / "revisions"
    rev = next(revision.iterdir())
    (rev / "formal" / "image-refs").mkdir(parents=True)
    (rev / "formal" / "image-refs" / "joy.json").write_text((rev / "casual" / "image-refs" / "neutral.json")
                                                             .read_text(encoding="utf-8"), encoding="utf-8")
    [layer] = run(library, home)["images"]["expression_layers"]
    assert layer["status"] == "unavailable" and "undeclared outfit" in layer["reason"]


def test_f12_a_blob_whose_bytes_do_not_match_its_name_blocks(library, home):
    blob = next((library / "assets" / "image-store" / "blobs").rglob("*.png"))
    blob.write_bytes(blob.read_bytes() + b"tampered")
    manifest = run(library, home)
    assert ("blob_hash_mismatch", (blob.relative_to(library).as_posix(),)) in codes(manifest, "blocking")
    assert any(p["status"] == "unarrived_blob" for p in manifest["images"]["placements"])


def test_a_file_over_the_read_cap_blocks_but_is_still_hashed(library, home):
    manifest = run(library, home, _options=Options(read_cap=64))
    big = [f for f in manifest["findings"] if f["code"] == "too_large"]
    assert big and all(entry(manifest, f["paths"][0])["sha256"] for f in big)
    assert manifest["status"] == "blocked"


def test_a_pc_and_a_character_sharing_an_id_get_no_candidates(library, home):
    pcs = library / "worlds" / "saltmarch" / "pcs" / "mara"
    pcs.mkdir()
    (pcs / "pc.md").write_text(frontmatter([("name", "Mara"), ("default_version", "default")]), encoding="utf-8")
    (pcs / "default.md").write_text(frontmatter([("name", "Mara")], "A namesake."), encoding="utf-8")
    manifest = run(library, home)
    rows = {r["path"]: r for r in manifest["identity_candidates"]["definitions"]}
    for path in (f"{MARA}/default.json", "worlds/saltmarch/pcs/mara/default.md"):
        assert rows[path]["status"] == "collision" and rows[path]["candidate"] is None
    assert any(f["code"] == "id_candidate_collision" for f in manifest["findings"])


def test_instances_that_would_share_an_identity_are_withheld(library, home):
    data = json.loads((library / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    data["characters/winifred"] = {**data["pcs/winifred"], "role": "npc"}
    (library / CAMPAIGN / "appearances.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = run(library, home)
    rows = [i for i in manifest["identity_candidates"]["instances"] if i["v1_ref"].endswith("/winifred")]
    assert {r["status"] for r in rows} == {"collision"}
    assert any(f["code"] == "instance_candidate_collision" for f in manifest["findings"])


def test_a_corrupt_tombstone_ledger_blocks_instead_of_resurrecting_pictures(library, home):
    (library / CAMPAIGN / "deleted.json").write_text("{corrupt", encoding="utf-8")
    manifest = run(library, home)
    assert ("invalid_ledger", (f"{CAMPAIGN}/deleted.json",)) in codes(manifest, "blocking")
    assert manifest["campaigns"][0]["semantics"] == "unresolved"
    assert manifest["images"]["campaign_layering"] == []
    assert all(r["state"] != "inherited" for r in manifest["campaigns"][0]["inheritance"])


def test_an_invalid_appearance_unresolves_only_its_scenes(library, home):
    scenes = library / CAMPAIGN / "scenes"
    (scenes / "002--elsewhere.md").write_text(
        frontmatter([("title", "Elsewhere"), ("identity", "0" * 31 + "2")], "**Mara:** Hello.\n"),
        encoding="utf-8")
    data = json.loads((library / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    data["pcs/winifred"]["role"] = "spectator"
    (library / CAMPAIGN / "appearances.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = run(library, home)
    authorship = {s["sid"]: s["authorship"] for s in manifest["scenes"]}
    assert authorship == {SID: "unresolved", "002--elsewhere": "resolved"}


def test_unreadable_scene_membership_unresolves_every_scene(library, home):
    scenes = library / CAMPAIGN / "scenes"
    (scenes / "002--elsewhere.md").write_text(
        frontmatter([("title", "Elsewhere"), ("identity", "0" * 31 + "2")], "**Mara:** Hello.\n"),
        encoding="utf-8")
    data = json.loads((library / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    data["pcs/winifred"]["scenes"] = "not a list"
    (library / CAMPAIGN / "appearances.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = run(library, home)
    assert {s["authorship"] for s in manifest["scenes"]} == {"unresolved"}


def test_a_malformed_locked_player_copy_unresolves_its_scene(library, home):
    (library / CAMPAIGN / "pcs" / "winifred" / "default.md").write_bytes(b"\xff\xfe")
    manifest = run(library, home)
    assert manifest["scenes"][0]["authorship"] == "unresolved"


def test_a_campaign_whose_world_is_gone_inherits_nothing(library, home):
    meta = library / CAMPAIGN / "campaign.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace("world: saltmarch", "world: vanished"),
                    encoding="utf-8")
    manifest = run(library, home)
    campaign = manifest["campaigns"][0]
    assert campaign["world_status"] == "not_found"
    assert {r["state"] for r in campaign["inheritance"]} == {"campaign_origin"}
    assert {a["provenance"] for a in campaign["appearances"]} == {"emergent"}


def test_crlf_text_hashes_as_v1_hashes_it(library, home):
    path = library / CAMPAIGN / "characters" / "mara" / "default.json"
    path.write_bytes(card("Mara").replace("\n", "\r\n").encode("utf-8"))
    appearances = {a["ref"]: a for a in run(library, home)["campaigns"][0]["appearances"]}
    assert appearances["characters/mara"]["provenance"] == "library"


def test_several_legacy_files_for_one_name_are_ambiguous(library, home):
    folder = library / "worlds" / "saltmarch" / "locations" / "tidewatch-keep" / "assets" / "default"
    folder.mkdir(parents=True)
    (folder / "avatar.png").write_bytes(b"one")
    (folder / "avatar.webp").write_bytes(b"two")
    manifest = run(library, home)
    assert {r["ambiguous"] for r in manifest["images"]["legacy_images"]} == {True}
    assert any(f["code"] == "legacy_image_ambiguous" for f in manifest["findings"])


def test_many_blocking_findings_are_capped_in_the_summary(library, home):
    for n in range(BLOCKING_SHOWN):
        (library / "worlds" / "saltmarch" / "lore" / f"note-{n}.md").write_text("---\n---\n", encoding="utf-8")
    fail = lambda path, op: errno.EACCES if op == "open" else None  # noqa: E731
    summary = render_summary(run(library, home, _options=Options(fail=fail)))
    assert "+" in summary and " more" in summary
    assert summary.count("  unreadable: ") == BLOCKING_SHOWN


def test_counts_mode_names_no_paths(library, home):
    fail = lambda path, op: errno.EACCES if op == "open" else None  # noqa: E731
    summary = render_summary(run(library, home, _options=Options(fail=fail)), mode="counts")
    assert "saltmarch" not in summary and "unreadable: " in summary


def test_the_source_is_unchanged_after_a_blocked_scan(library, home):
    symlink(library / "worlds" / "saltmarch" / "lore" / "linked.md", library / "config.md")
    before = tree_digest(library)
    run(library, home)
    assert tree_digest(library) == before


# ---- regressions from the implementation review ------------------------------------------------

def _object_path(library, image_id):
    return library / "assets" / "image-store" / "objects" / image_id[4:6] / f"{image_id}.json"


def test_a_lone_surrogate_in_a_description_is_hashed_not_a_crash(library, home):
    from v1_fixture import RED_ID
    path = _object_path(library, RED_ID)
    path.write_text(path.read_text(encoding="utf-8")[:-2] + ', "description": "\\ud800"}\n', encoding="utf-8")
    [red] = [o for o in run(library, home)["images"]["objects"] if o["id"] == RED_ID]
    assert red["has_description"] and red["description_sha256"]


@pytest.mark.parametrize("value", ["\u00b2", "9" * 5000, "abc"])
def test_unrecognized_settings_formats_fail_closed_without_crashing(library, home, value):
    (library / "config.md").write_text(f"---\ninference_format: {value}\n---\n", encoding="utf-8")
    assert ("newer_format", ("config.md",)) in codes(run(library, home), "blocking")


def test_a_deeply_nested_promotion_journal_is_walked_not_recursed(library, home):
    from v1_fixture import RED_ID
    deep = "[" * 5000 + f'"{RED_ID}"' + "]" * 5000
    (library / MARA / "assets" / "default" / "image-refs" / ".promote.json").write_text(deep, encoding="utf-8")
    manifest = run(library, home)
    assert manifest["status"] == "ready_for_review" or ("malformed_json", (
        f"{MARA}/assets/default/image-refs/.promote.json",)) in codes(manifest)


def test_an_object_outside_its_shard_is_not_the_object(library, home):
    from v1_fixture import RED_ID
    path = _object_path(library, RED_ID)
    moved = path.parent.parent / "zz"
    moved.mkdir()
    path.rename(moved / path.name)
    manifest = run(library, home)
    assert {p["status"] for p in manifest["images"]["placements"] if p["image"] == RED_ID} == {"dangling_object"}
    assert [o["addressable"] for o in manifest["images"]["objects"] if o["id"] == RED_ID] == [False]


@pytest.mark.parametrize("outfit, meta", [("con", {"label": "Console"}), ("casual", {"label": 7}),
                                          ("casual", {"presentation": {"scale": 9}}),
                                          ("casual", {"fallback_expression": "Neutral"})])
def test_expression_metadata_is_validated_as_v1_validates_it(library, home, outfit, meta):
    from v1_fixture import EXPRESSIONS, NEUTRAL, REV, expression_manifest
    expressions = library / MARA / "assets" / "older" / "expressions"
    if outfit != "casual":
        (expressions / "revisions" / REV / "casual").rename(expressions / "revisions" / REV / outfit)
    value = {**EXPRESSIONS, "default_outfit": outfit, "outfits": {outfit: meta}}
    (expressions / "manifest.json").write_bytes(
        expression_manifest(value, {f"{outfit}/image-refs/neutral.json": NEUTRAL}))
    [layer] = run(library, home)["images"]["expression_layers"]
    assert layer["status"] == "unavailable"


def test_an_unparseable_version_is_unresolved_and_gets_no_candidate(library, home):
    (library / "worlds" / "realm" / "characters" / "mara" / "default.json").write_text("[]", encoding="utf-8")
    (library / "worlds" / "saltmarch" / "lore" / "the-salt-pact.md").write_bytes(b"\xff\xfe")
    manifest = run(library, home)
    rows = {r["path"]: r for r in manifest["identity_candidates"]["definitions"]}
    for path in ("worlds/realm/characters/mara/default.json", "worlds/saltmarch/lore/the-salt-pact.md"):
        assert (rows[path]["status"], rows[path]["candidate"]) == ("unresolved", None)
        assert path in {u["path"] for u in manifest["unresolved"]}


def test_a_corrupt_tombstone_ledger_also_unresolves_scene_authorship(library, home):
    (library / CAMPAIGN / "deleted.json").write_text("{corrupt", encoding="utf-8")
    assert run(library, home)["scenes"][0]["authorship"] == "unresolved"


def test_a_copy_whose_world_record_is_gone_is_materialized_with_world_missing(library, home):
    keep = library / CAMPAIGN / "lore"
    keep.mkdir()
    (keep / "old-oath.md").write_text("---\nname: Old Oath\n---\n", encoding="utf-8")
    (library / CAMPAIGN / "sync.md").write_text(f"---\nlore/old-oath: {'a' * 64}\n---\n\n", encoding="utf-8")
    rows = {r["ref"]: r for r in run(library, home)["campaigns"][0]["inheritance"]}
    assert (rows["lore/old-oath"]["state"], rows["lore/old-oath"]["sync"]) == ("materialized", "world_missing")


def test_a_player_not_seated_in_this_scene_speaks_as_an_npc(library, home):
    scene = library / CAMPAIGN / "scenes" / "002--alone.md"
    scene.write_text(frontmatter([("title", "Alone"), ("identity", "0" * 31 + "2")], "**Winifred:** Hello.\n"),
                     encoding="utf-8")
    [alone] = [s for s in run(library, home)["scenes"] if s["sid"] == "002--alone"]
    assert [(p["speaker"], p["role"]) for p in alone["posts"]] == [("Winifred", "assistant")]


def test_a_case_variant_ledger_name_blocks(library, home):
    (library / CAMPAIGN / "Detached.json").write_text("[]\n", encoding="utf-8")
    if (library / CAMPAIGN / "detached.json").exists():
        pytest.skip("this filesystem folds case")
    assert ("case_variant_name", (f"{CAMPAIGN}/Detached.json",)) in codes(run(library, home), "blocking")


def test_a_linked_worlds_directory_blocks_instead_of_failing(library, home, tmp_path):
    import shutil
    shutil.move(str(library / "worlds"), str(tmp_path / "real-worlds"))
    symlink(library / "worlds", tmp_path / "real-worlds", directory=True)
    manifest = run(library, home)
    assert entry(manifest, "worlds")["disposition"] == "refused"
    assert manifest["status"] == "blocked"


def test_a_campaign_reference_to_a_tombstoned_world_record_dangles(library, home):
    (library / CAMPAIGN / "deleted.json").write_text(json.dumps(
        ["assets/characters/mara/default/gallery_1", "locations/tidewatch-keep"]), encoding="utf-8")
    groups = library / CAMPAIGN / "groups"
    groups.mkdir()
    (groups / "watch.md").write_text("---\nname: Watch\nheadquarters: 'locations:tidewatch-keep'\n---\n",
                                     encoding="utf-8")
    [row] = [r for r in run(library, home)["records"] if r["scope"] == "campaign"]
    assert row["refs"] == [{"field": "headquarters", "target": "locations:tidewatch-keep", "status": "dangling"}]


@pytest.mark.skipif(not POSIX, reason="Windows cannot store these names")
def test_a_version_name_v1_would_not_list_is_reported(library, home):
    (library / MARA / "odd .json").write_text(card("Mara"), encoding="utf-8")
    assert ("invalid_v1_id", (f"{MARA}/odd .json",)) in codes(run(library, home))


# ---- regressions from the Codex review --------------------------------------------------------

def test_a_non_text_blob_extension_is_an_invalid_object(library, home):
    from v1_fixture import RED_ID
    path = _object_path(library, RED_ID)
    path.write_text(path.read_text(encoding="utf-8").replace('"ext": "png"', '"ext": []'), encoding="utf-8")
    [red] = [o for o in run(library, home)["images"]["objects"] if o["id"] == RED_ID]
    assert red["valid"] is False and "blob.ext" in red["problems"]


def test_an_unterminated_sync_ledger_is_invalid_not_empty(library, home):
    (library / CAMPAIGN / "sync.md").write_text(f"---\nlore/the-salt-pact: {'a' * 64}\n", encoding="utf-8")
    manifest = run(library, home)
    assert ("invalid_ledger", (f"{CAMPAIGN}/sync.md",)) in codes(manifest, "blocking")
    assert manifest["campaigns"][0]["semantics"] == "unresolved"


def test_a_case_variant_credential_directory_is_never_read(library, home):
    folder = library / "LLM_Connections"
    folder.mkdir()
    (folder / "provider.md").write_text("api_key: SENTINEL-SECRET-3", encoding="utf-8")
    manifest = run(library, home)
    assert entry(manifest, "LLM_Connections")["disposition"] == "excluded"
    assert not [e for e in manifest["entries"] if e["path"].startswith("LLM_Connections/")]
    assert "SENTINEL-SECRET-3" not in render_json(manifest).decode()


def test_an_instance_never_derives_from_a_withheld_portrayal(library, home):
    pcs = library / "worlds" / "saltmarch" / "pcs" / "mara"
    pcs.mkdir()
    (pcs / "pc.md").write_text(frontmatter([("name", "Mara"), ("default_version", "default")]), encoding="utf-8")
    (pcs / "default.md").write_text(frontmatter([("name", "Mara")], "A namesake."), encoding="utf-8")
    manifest = run(library, home)
    [mara] = [i for i in manifest["identity_candidates"]["instances"] if i["v1_ref"] == "characters/mara"]
    assert mara["derives_from"] is None and mara["derives_from_unresolved"] is True
    assert any(u["what"] == "instance_derivation" for u in manifest["unresolved"])


def test_campaign_card_image_links_resolve_through_the_campaign_overlay(library, home):
    url = "/api/campaigns/the-drowned-ledger/characters/mara/versions/default/images/avatar"
    hidden = "/api/worlds/saltmarch/characters/mara/versions/default/images/gallery_1"
    copy = library / CAMPAIGN / "characters" / "mara" / "default.json"
    data = json.loads(copy.read_text(encoding="utf-8"))
    data["data"]["description"] = f"![a]({url}) ![g]({hidden})"
    copy.write_text(json.dumps(data), encoding="utf-8")
    refs = {r["slot"]["name"]: r["status"] for r in run(library, home)["images"]["references"]
            if r["source"] == f"{CAMPAIGN}/characters/mara/default.json"}
    assert refs == {"avatar": "resolved", "gallery_1": "unresolved"}
