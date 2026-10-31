"""Every reported state of the V0.2.1 inventory, pinned on variations of the synthetic fixture."""

import hashlib
import json

import pytest

from grimoire.migrate_v1 import scan
from v1_fixture import (BLUE, CAMPAIGN, EXPRESSIONS, MARA, MISSING_ID, NEUTRAL, RED_ID, REV, SID,
                        build_library, card, expression_manifest, frontmatter, placement, sha)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    return h


@pytest.fixture
def library(tmp_path):
    return build_library(tmp_path / "v1")


def run(library, home, **kwargs):
    kwargs.setdefault("destination", home.parent / "v2")
    return scan(library, archive_id="fixture", home=home, environ=kwargs.pop("environ", {}), **kwargs).manifest


def codes(manifest):
    return {(f["code"], tuple(f["paths"])) for f in manifest["findings"]}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def ledger(library, name, value):
    write(library / CAMPAIGN / name, json.dumps(value, indent=2) + "\n")


# ---- campaign image layering (acceptance F: inherited, overridden, hidden, library) -------------

def test_layering_reports_inherited_overridden_and_hidden_pictures(library, home):
    library_refs = library / "worlds" / "saltmarch" / "assets" / "images" / "image-refs"
    write(library_refs / "coast.json", placement(RED_ID))
    write(library_refs / "dock.json", placement(RED_ID))
    write(library / CAMPAIGN / "characters" / "mara" / "assets" / "default" / "image-refs" / "avatar.json",
          placement(RED_ID, focus=10))
    ledger(library, "deleted.json", ["assets/characters/mara/default/gallery_1", "assets/library/coast"])
    states = {r["name"]: r["state"] for r in run(library, home)["images"]["campaign_layering"]}
    assert states == {"avatar": "overridden", "gallery_1": "hidden", "coast": "hidden", "dock": "inherited"}


def test_without_a_campaign_placement_an_avatar_is_inherited(library, home):
    (library / CAMPAIGN / "characters" / "mara" / "assets" / "default" / "image-refs" / "avatar.json").unlink()
    states = {r["name"]: r["state"] for r in run(library, home)["images"]["campaign_layering"]}
    assert states["avatar"] == "inherited"


# ---- inheritance states and sync -------------------------------------------------------------

def test_every_inheritance_state(library, home):
    for name in ("quay", "pier", "dune"):
        write(library / "worlds" / "saltmarch" / "locations" / f"{name}.md", f"---\nname: {name}\n---\n")
        write(library / CAMPAIGN / "locations" / f"{name}.md", f"---\nname: {name}\n---\n")
    write(library / "worlds" / "saltmarch" / "locations" / "pier.md", "---\nname: pier, rebuilt\n---\n")
    write(library / CAMPAIGN / "characters" / "gull" / "character.md", frontmatter([("name", "Gull")]))
    write(library / CAMPAIGN / "characters" / "gull" / "default.json", card("Gull"))
    write(library / CAMPAIGN / "sync.md", "---\n" + "".join(
        f"locations/{n}: {sha(f'---{chr(10)}name: {n}{chr(10)}---{chr(10)}')}\n" for n in ("quay", "pier")) + "---\n\n")
    ledger(library, "deleted.json", ["assets/characters/mara/default/gallery_1", "lore/the-salt-pact"])
    ledger(library, "detached.json", ["characters/gull", "items/lost-ring"])
    rows = {r["ref"]: r for r in run(library, home)["campaigns"][0]["inheritance"]}
    assert rows["lore/the-salt-pact"]["state"] == "tombstoned"
    assert rows["characters/gull"]["state"] == "detached"
    assert rows["items/lost-ring"]["state"] == "detached_absent"
    assert rows["locations/tidewatch-keep"]["state"] == "inherited"
    assert (rows["locations/quay"]["state"], rows["locations/quay"]["sync"]) == ("materialized", "in_sync")
    assert rows["locations/quay"]["local_edit"] is False
    assert rows["locations/pier"]["sync"] == "diverged"
    assert rows["locations/dune"]["sync"] == "no_base"


def test_appearance_provenance_and_sync_are_separate_axes(library, home):
    write(library / CAMPAIGN / "characters" / "mara" / "default.json", card("Mara the Elder"))
    write(library / CAMPAIGN / "pcs" / "winifred" / "default.md", "---\nname: Winifred\n---\n\nUnchanged.")
    write(library / MARA / "default.json", card("Mara, revised"))
    rows = {a["ref"]: a for a in run(library, home)["campaigns"][0]["appearances"]}
    assert (rows["characters/mara"]["provenance"], rows["characters/mara"]["sync"]) == ("override", "world_changed")
    assert (rows["pcs/winifred"]["provenance"], rows["pcs/winifred"]["sync"]) == ("override", "current")


def test_a_case_variant_world_leaves_inheritance_unresolved(library, home):
    meta = library / CAMPAIGN / "campaign.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace("world: saltmarch", "world: Saltmarch"),
                    encoding="utf-8")
    manifest = run(library, home)
    campaign = manifest["campaigns"][0]
    assert (campaign["world_status"], campaign["semantics"]) == ("case_variant", "unresolved")
    assert {a["provenance"] for a in campaign["appearances"]} == {"unresolved"}
    assert ("world_case_variant", (f"{CAMPAIGN}/campaign.md",)) in codes(manifest)


def test_a_full_copy_or_missing_world_campaign_is_warned_about(library, home):
    meta = library / CAMPAIGN / "campaign.md"
    text = meta.read_text(encoding="utf-8")
    meta.write_text(text.replace("world_copy: overlay", "world_copy: full").replace("world: saltmarch", "world: gone"),
                    encoding="utf-8")
    found = codes(run(library, home))
    assert ("legacy_full_copy_campaign", (f"{CAMPAIGN}/campaign.md",)) in found
    assert ("world_missing", (f"{CAMPAIGN}/campaign.md",)) in found


def test_a_campaign_with_a_newer_settings_format_blocks(library, home):
    meta = library / CAMPAIGN / "campaign.md"
    meta.write_text(meta.read_text(encoding="utf-8").replace("inference_format: 2", "inference_format: 3"),
                    encoding="utf-8")
    manifest = run(library, home)
    assert ("newer_format", (f"{CAMPAIGN}/campaign.md",)) in codes(manifest)
    assert manifest["status"] == "blocked"


# ---- expressions -------------------------------------------------------------------------------

def test_a_campaign_expression_layer_overrides_and_hides_inherited_sprites(library, home):
    layer = library / CAMPAIGN / "characters" / "mara" / "assets" / "older" / "expressions"
    rev = "f" * 32
    write(layer / "revisions" / rev / "casual" / "image-refs" / "neutral.json", NEUTRAL)
    value = {**EXPRESSIONS, "default_outfit": None, "outfits": {"casual": {}}, "revision": rev,
             "hidden_expressions": {"casual": ["joy"]}}
    write(layer / "manifest.json", expression_manifest(value, {"casual/image-refs/neutral.json": NEUTRAL}))
    layers = {x["path"]: x for x in run(library, home)["images"]["expression_layers"]}
    mine = layers[f"{CAMPAIGN}/characters/mara/assets/older/expressions/manifest.json"]
    assert mine["status"] == "available"
    assert mine["over"] == f"{MARA}/assets/older/expressions/manifest.json"
    assert (mine["overrides"], mine["hides"]) == (["casual/neutral"], ["casual/joy"])


def test_a_sprite_in_an_old_revision_is_stale_not_live(library, home):
    old = library / MARA / "assets" / "older" / "expressions" / "revisions" / ("e" * 32)
    write(old / "casual" / "image-refs" / "joy.json", NEUTRAL)
    manifest = run(library, home)
    [layer] = manifest["images"]["expression_layers"]
    assert layer["status"] == "available" and layer["sprites"] == ["casual/neutral"]
    stale = f"{MARA}/assets/older/expressions/revisions/{'e' * 32}/casual/image-refs/joy.json"
    assert ("stale_expression_revision", (stale,)) in codes(manifest)


# ---- image store ---------------------------------------------------------------------------------

def test_a_missing_blob_leaves_its_object_unarrived(library, home):
    red_blob = next(p for p in (library / "assets" / "image-store" / "blobs").rglob("*.png")
                    if p.read_bytes() != BLUE)
    red_blob.unlink()
    manifest = run(library, home)
    [red] = [o for o in manifest["images"]["objects"] if o["id"] == RED_ID]
    assert red["blob"] == "unarrived"
    assert {p["status"] for p in manifest["images"]["placements"] if p["image"] == RED_ID} == {"unarrived_blob"}


def test_a_blob_no_object_names_is_an_orphan(library, home):
    data = b"orphan bytes"
    digest = hashlib.sha256(data).hexdigest()
    write(library / "assets" / "image-store" / "blobs" / digest[:2] / f"{digest}.png", data)
    assert ("orphan_blob", (f"assets/image-store/blobs/{digest[:2]}/{digest}.png",)) in codes(run(library, home))


def test_collections_resolve_members_by_format(library, home):
    write(library / "worlds" / "saltmarch" / "assets" / "images" / "image-refs" / "coast.json", placement(RED_ID))
    folder = library / "worlds" / "saltmarch" / "assets" / "image-collections"
    write(folder / ("a" * 32 + ".json"), json.dumps({"format": 1, "members": ["coast", "nope"]}))
    write(folder / ("b" * 32 + ".json"), json.dumps({"format": 2, "members": [RED_ID, MISSING_ID, "x"]}))
    rows = {r["id"]: [(m["member"], m["status"]) for m in r["members"]]
            for r in run(library, home)["images"]["collections"]}
    assert rows["a" * 32] == [("coast", "resolved"), ("nope", "missing")]
    assert rows["b" * 32] == [(RED_ID, "resolved"), (MISSING_ID, "dangling_object"), ("x", "invalid")]


# ---- scenes and losses --------------------------------------------------------------------------

def test_scene_identity_and_name_findings(library, home):
    scenes = library / CAMPAIGN / "scenes"
    write(scenes / "002--copy.md", (scenes / f"{SID}.md").read_bytes())
    write(scenes / "2025-01-01-old.md", frontmatter([("title", "Old")], "**Mara:** Then.\n"))
    found = codes(run(library, home))
    assert ("duplicate_scene_identity", (f"{CAMPAIGN}/scenes/{SID}.md", f"{CAMPAIGN}/scenes/002--copy.md")) in found
    assert ("missing_scene_identity", (f"{CAMPAIGN}/scenes/2025-01-01-old.md",)) in found
    assert ("legacy_scene_name", (f"{CAMPAIGN}/scenes/2025-01-01-old.md",)) in found


def test_unsupported_state_is_counted_as_a_loss(library, home):
    write(library / CAMPAIGN / "scenes" / f"{SID}.alts.json", json.dumps({"anchor": 1, "runs": [{}, {}]}))
    write(library / CAMPAIGN / "responses.json", json.dumps(
        {"scenes": {"x": {"responses": {"r": {"variants": [{}, {}, {}]}}}}}))
    write(library / CAMPAIGN / "knowledge.json", "{}")
    losses = {x["path"]: (x["what"], x["detail"]) for x in run(library, home)["losses"]}
    assert losses[f"{CAMPAIGN}/scenes/{SID}.alts.json"] == ("scene_alts", "2 reroll runs")
    assert losses[f"{CAMPAIGN}/responses.json"] == ("campaign_state", "3 response variants")
    assert losses[f"{CAMPAIGN}/knowledge.json"] == ("campaign_state", "")


# ---- roots -------------------------------------------------------------------------------------

def test_a_linked_source_root_is_recorded(library, home, tmp_path):
    link = tmp_path / "chosen"
    try:
        link.symlink_to(library, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks cannot be created here")
    roots = run(link, home)["roots"]
    assert roots["source_root_was_link"] is True
    assert run(library, home)["roots"]["source_root_was_link"] is False


def test_the_default_destination_is_checked_and_never_created(library, home, tmp_path):
    target = tmp_path / "v2-default"
    manifest = run(library, home, destination=None, environ={"GRIMOIRE_V2_HOME": str(target)})
    assert manifest["roots"]["destination"] == str(target.resolve())
    assert not target.exists()


def test_a_greeting_version_resolves_against_its_character(library, home):
    greeting = library / "worlds" / "saltmarch" / "greetings" / "harbour-dawn.md"
    greeting.write_text(greeting.read_text(encoding="utf-8").replace("version: default", "version: young"),
                        encoding="utf-8")
    [row] = [r for r in run(library, home)["records"] if r["kind"] == "greetings"]
    assert {"field": "version", "target": "characters:mara/young", "status": "dangling"} in row["refs"]
