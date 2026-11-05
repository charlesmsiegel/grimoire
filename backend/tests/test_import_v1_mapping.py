"""Sprint 026 mapping edge cases: what 011 marks unresolved stays out, what a campaign played is kept.

Each test changes the synthetic V1 library on purpose, reviews it again and plans: expression art
from a layer 011 could not verify is not imported, a campaign locked to an older card keeps that
card's text, a card saved with Windows line endings imports, an id with no V2 spelling is reported
instead of refusing the run, inherited art needs no campaign placement, and a link where an actor
would go leaves only that actor out.
"""

from __future__ import annotations

import json
import os

import pytest

from grimoire.core.entity_codec import instance_from_payload
from grimoire.core.identity import InstanceRef, PlacementRef, SourceRef
from grimoire.import_v1 import ImportRefused, Selection
from grimoire.store import StaleWrite, jsonfmt
from grimoire.store.layout import address_parts

from import_fixtures import ARCHIVE, CAMPAIGN_ID, SALTMARCH, SERAPHINE_DEFAULT, SERAPHINE_REF, Env
from v1_fixture import BLUE_ID, CAMPAIGN, MARA, RED_ID, SID, STAMP, card, frontmatter, placement, sha, tree_digest, write

CAMPAIGN_DIR = f"campaigns/{CAMPAIGN_ID}/"
WORLD_ONLY = Selection({"saltmarch": SALTMARCH})
WITH_CAMPAIGN = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})
WITHOUT_SERAPHINE = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID},
                              frozenset({f"v1:{ARCHIVE}:worlds/saltmarch/characters/seraphine/default.json"}))


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.store.close()
    assert tree_digest(environment.v1) == environment.v1_before


def reasons(review) -> set[str]:
    return {row["reason"] for row in review["unresolved"]}


def test_expressions_of_a_layer_011_could_not_verify_are_not_imported(env):
    path = env.v1 / MARA / "assets" / "older" / "expressions" / "manifest.json"
    manifest = json.loads(path.read_bytes())
    path.write_bytes(json.dumps({**manifest, "content_digest": "0" * 64}).encode())
    assert any(row["what"] == "expression_layer" for row in env.rescan()["unresolved"])
    review = env.plan(WITH_CAMPAIGN).review()
    neutral = "/".join(address_parts(PlacementRef(SourceRef(SALTMARCH, "character", "mara.older"),
                                                  "expr.casual.neutral")))
    assert neutral not in review["writes"] and "expression_layer_unavailable" in reasons(review)


def test_a_campaign_locked_to_an_older_card_keeps_that_card_verbatim(env):
    write(env.v1, "worlds/saltmarch/characters/seraphine/default.json",
          card("Seraphine", {"x_synthetic_note": "rewritten in the world"}))
    env.rescan()
    result = env.plan(WITH_CAMPAIGN).apply()
    ref = InstanceRef(CAMPAIGN_ID, "character", "seraphine")
    stored = env.store.read(ref)
    v1 = instance_from_payload(ref, "grimoire.entity/0", stored.envelope.payload).extras["v1"]
    assert (v1["sync"], v1["campaign_copy"]["text"]) == ("world_changed", SERAPHINE_DEFAULT)
    assert {"appearance_world_changed", "campaign_copy_preserved"} <= {row["code"] for row in result.review["flags"]}


def test_a_card_saved_with_windows_line_endings_imports(env):
    text = (env.v1 / MARA / "default.json").read_text(encoding="utf-8")
    write(env.v1, f"{MARA}/default.json", text.replace("\n", "\r\n").encode("utf-8"))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert "unhashed_portrayal" not in reasons(review)
    assert f"{CAMPAIGN_DIR}instances/character/mara.json" in review["writes"]


def test_an_id_with_no_v2_spelling_is_reported_and_the_rest_imports(env):
    write(env.v1, "worlds/Old_Coast/world.md",
          frontmatter([("name", "Old Coast"), ("created", STAMP), ("updated", STAMP)]))
    manifest = env.rescan()
    assert manifest["status"] == "ready_for_review"
    plan = env.plan()
    review = plan.review()
    assert ("no_id_candidate", "Old_Coast") in {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert any(path.startswith("sources/world/saltmarch/") for path in review["writes"])


def test_art_a_campaign_only_inherits_needs_no_campaign_placement(env):
    write(env.v1, f"{MARA}/assets/default/image-refs/gallery_2.json", placement(BLUE_ID))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert not any(row["reason"].startswith("layering_") for row in review["unresolved"])
    assert f"{CAMPAIGN_DIR}placements/character/mara/gallery_2.json" not in review["writes"]
    assert "/".join(address_parts(PlacementRef(SourceRef(SALTMARCH, "character", "mara.default"), "gallery_2"))) \
        in review["writes"]


def test_a_link_where_an_actor_would_go_leaves_only_that_actor_out(env, tmp_path):
    env.plan(WITHOUT_SERAPHINE).apply()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}\n", encoding="utf-8")
    link = env.store.root / CAMPAIGN_DIR / "instances" / "character" / "seraphine.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make a file symlink here")
    review = env.plan(WITH_CAMPAIGN).review()
    assert "actor_unreadable" in reasons(review)
    assert not any("seraphine" in path for path in review["writes"] if path.startswith(CAMPAIGN_DIR))
    assert f"{CAMPAIGN_DIR}instances/character/mara.json" not in review["writes"]  # present, not rewritten


def test_a_definition_deleted_in_v2_never_comes_back_with_a_new_v1_card(env):
    env.plan(WORLD_ONLY).apply()
    (env.store.root / "/".join(address_parts(SERAPHINE_REF))).unlink()
    write(env.v1, "worlds/saltmarch/characters/seraphine/default.json", card("Seraphine", {"x_synthetic_note": "new"}))
    env.rescan()
    plan = env.plan(WORLD_ONLY)
    review = plan.review()
    entity = "/".join(address_parts(SERAPHINE_REF)).rsplit("/", 1)[0] + "/"
    assert "entity_removed" in reasons(review)
    assert not any(path.startswith(entity) for path in review["writes"])
    assert plan.apply().receipt is None  # nothing new: the receipt keeps one revision per legacy id


def test_a_renamed_scene_keeps_its_chat(env):
    env.plan(WITH_CAMPAIGN).apply()
    scenes = env.v1 / CAMPAIGN / "scenes"
    renamed = "001--the-tide-turns"
    (scenes / f"{SID}.md").rename(scenes / f"{renamed}.md")
    path = env.v1 / CAMPAIGN / "appearances.json"
    path.write_text(path.read_text(encoding="utf-8").replace(SID, renamed), encoding="utf-8")
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert not any("/chats/" in path for path in review["writes"])
    chats = [path for path in review["outcomes"] if "/chats/" in path]
    assert len(chats) == 1 and review["outcomes"][chats[0]] == "present"
    assert "v1_changed" in reasons(review)  # reported, never a second chat


def test_a_chat_waits_for_a_cast_member_that_could_not_be_imported(env):
    path = env.v1 / "worlds/saltmarch/characters/seraphine/default.json"
    path.write_bytes(b"\xff not text")  # her portrayal cannot be read, so she cannot be imported
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert "cast_not_imported" in reasons(review)
    assert not any("/chats/" in path for path in review["writes"])


def test_a_chat_started_without_a_declined_cast_member_keeps_its_cast(env):
    env.plan(WITHOUT_SERAPHINE).apply()  # the user declined her: the scene goes on without her
    review = env.plan(WITH_CAMPAIGN).review()
    assert f"{CAMPAIGN_DIR}instances/character/seraphine.json" in review["writes"]
    assert "v1_changed" not in reasons(review)  # V1 did not change
    assert "cast_not_bound" in reasons(review)  # reported, never rebound


def test_an_unreadable_play_scope_is_reported_not_raised(env, tmp_path):
    env.plan(WITH_CAMPAIGN).apply()
    scope = env.store.root / CAMPAIGN_DIR / "scope.json"
    copy = tmp_path / "scope-copy.json"
    copy.write_bytes(scope.read_bytes())
    scope.unlink()
    try:
        os.symlink(copy, scope)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make a file symlink here")
    review = env.plan(WITH_CAMPAIGN).review()
    assert "campaign_unreadable" in reasons(review)
    assert not any(path.startswith(CAMPAIGN_DIR) for path in review["writes"])


def test_a_record_named_like_a_windows_device_is_reported_and_the_rest_imports(env):
    write(env.v1, "worlds/saltmarch/characters/con/character.md",
          frontmatter([("name", "Con"), ("default_version", "default")]))
    write(env.v1, "worlds/saltmarch/characters/con/default.json", card("Con"))
    write(env.v1, "worlds/con/world.md", frontmatter([("name", "Con"), ("created", STAMP), ("updated", STAMP)]))
    env.rescan()
    review = env.plan().review()  # the default selection, which proposes no world it cannot store
    assert ("no_id_candidate", "con") in {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert "unstorable_id" in reasons(review)
    assert f"{CAMPAIGN_DIR}instances/character/mara.json" in review["writes"]
    with pytest.raises(ImportRefused) as caught:
        env.plan(Selection({"saltmarch": "world:con"}))
    assert caught.value.reason == "unstorable_id"


def _add_second_player(env, persona: str, pid: str = "wren") -> None:
    """A second PC of Saltmarch, in the campaign's scene beside Winifred."""
    pc = frontmatter([("name", pid.title()), ("default_version", "default")])
    write(env.v1, f"worlds/saltmarch/pcs/{pid}/pc.md", pc)
    write(env.v1, f"worlds/saltmarch/pcs/{pid}/default.md", persona)
    write(env.v1, f"{CAMPAIGN}/pcs/{pid}/pc.md", pc)
    write(env.v1, f"{CAMPAIGN}/pcs/{pid}/default.md", persona)
    path = env.v1 / CAMPAIGN / "appearances.json"
    appearances = json.loads(path.read_text(encoding="utf-8"))
    appearances[f"pcs/{pid}"] = {**appearances["pcs/winifred"], "base": sha(persona)}
    write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()


def test_a_scene_with_two_player_characters_starts_with_the_first(env):
    _add_second_player(env, frontmatter([("name", "Wren"), ("pronouns", "they/them")], "A diver."))
    review = env.plan(WITH_CAMPAIGN).review()
    assert "second_persona" in reasons(review) and "cast_not_imported" not in reasons(review)
    assert any("/chats/" in path for path in review["writes"])


def test_a_second_player_that_cannot_be_imported_does_not_hold_the_scene(env):
    _add_second_player(env, frontmatter([("name", "Con")], "A diver."), "con")  # no library can store con
    review = env.plan(WITH_CAMPAIGN).review()
    assert "unstorable_id" in reasons(review) and "cast_not_imported" not in reasons(review)
    assert any("/chats/" in path for path in review["writes"])


@pytest.mark.parametrize("name", ["con", "aux", "nul"])
def test_a_picture_named_like_a_windows_device_is_left_out_alone(env, name):
    write(env.v1, f"{MARA}/assets/default/image-refs/{name}.json", placement(BLUE_ID))
    env.rescan()
    review = env.plan().review()
    assert ("unstorable_id", name) in {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert f"{CAMPAIGN_DIR}instances/character/mara.json" in review["writes"]


def test_a_scene_with_no_v1_identity_says_so(env):
    path = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    text = path.read_text(encoding="utf-8")
    write(env.v1, f"{CAMPAIGN}/scenes/{SID}.md", "".join(line for line in text.splitlines(keepends=True)
                                                       if not line.startswith("identity:")))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert "scene_no_identity" in reasons(review) and "scene_unresolved" not in reasons(review)


def test_a_card_too_large_to_store_is_reported_and_the_rest_imports(env, monkeypatch):
    write(env.v1, "worlds/saltmarch/characters/seraphine/default.json",
          json.dumps({"spec": "chara_card_v2", "spec_version": "2.0",
                      "data": {"name": "Seraphine", "description": "A long story. " * 3000}}))
    env.rescan()
    monkeypatch.setattr(jsonfmt, "MAX_FILE_BYTES", 30_000)  # a small library limit, so the card passes it
    review = env.plan(WORLD_ONLY).review()
    assert "too_large" in reasons(review)
    assert not any("seraphine" in path for path in review["writes"])
    assert any("mara.default" in path for path in review["writes"])


def test_a_receipt_too_large_to_store_refuses_the_run_cleanly(env, monkeypatch):
    monkeypatch.setattr(jsonfmt, "MAX_FILE_BYTES", 10_000)  # every record fits, the receipt does not
    before = env.v2()
    with pytest.raises(ImportRefused) as caught:
        env.plan(WITH_CAMPAIGN)
    assert caught.value.reason == "receipt_too_large" and env.v2() == before


def test_a_control_character_in_a_campaign_name_is_shown_as_a_space(env):
    path = env.v1 / CAMPAIGN / "campaign.md"
    write(env.v1, f"{CAMPAIGN}/campaign.md", path.read_text(encoding="utf-8").replace(
        "The Drowned Ledger", "The Drowned\x1bLedger"))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert f"{CAMPAIGN_DIR}scope.json" in review["writes"]


def test_a_campaign_only_picture_is_reported_not_dropped_silently(env):
    write(env.v1, f"{CAMPAIGN}/characters/mara/assets/default/image-refs/gallery_2.json", placement(BLUE_ID))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert "campaign_only_placement" in reasons(review)


def test_image_metadata_the_import_does_not_carry_is_reported(env):
    path = env.v1 / "assets/image-store/objects" / BLUE_ID[4:6] / f"{BLUE_ID}.json"
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    write(env.v1, str(path.relative_to(env.v1)), json.dumps({**sidecar, "description": "A tide pool."}, indent=2))
    env.rescan()
    review = env.plan(WORLD_ONLY).review()
    rows = [row for row in review["unresolved"] if row["reason"] == "image_metadata_not_carried"]
    assert rows and "description" in rows[0]["detail"]
    assert any("mara.default/gallery_1" in path for path in review["writes"])  # the picture still imports


def test_an_actor_re_sourced_in_v2_is_never_bound_to_its_v1_definition(env):
    scene = f"v1:{ARCHIVE}:{CAMPAIGN}/scenes/{SID}.md"
    env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, frozenset({scene}))).apply()
    actor = env.store.root / CAMPAIGN_DIR / "instances" / "character" / "mara.json"
    text = actor.read_text(encoding="utf-8")
    old = '"derived_from": "world:saltmarch/character/mara.default"'
    new = '"derived_from": "world:saltmarch/character/mara.older"'
    actor.write_text(text.replace(old, new), encoding="utf-8")
    review = env.plan(WITH_CAMPAIGN).review()
    assert "actor_changed_in_v2" in reasons(review)
    assert not any("/chats/" in path for path in review["writes"])


def test_a_persona_saved_with_windows_line_endings_keeps_its_description(env):
    path = env.v1 / "worlds/saltmarch/pcs/winifred/default.md"
    write(env.v1, "worlds/saltmarch/pcs/winifred/default.md", path.read_bytes().replace(b"\n", b"\r\n"))
    env.rescan()
    preview = env.plan(WORLD_ONLY).preview()
    winifred = next(data for key, data in preview.items() if "winifred.default/" in key)
    assert json.loads(winifred)["payload"]["description"] == "A debtor."


def test_a_path_with_no_legacy_id_leaves_only_its_record_out(env):
    scenes = env.v1 / CAMPAIGN / "scenes"
    renamed = "001--bad\u200bname"  # a zero-width space (a format character): 011 refuses it a legacy id
    (scenes / f"{SID}.md").rename(scenes / f"{renamed}.md")
    path = env.v1 / CAMPAIGN / "appearances.json"
    path.write_text(path.read_text(encoding="utf-8").replace(SID, renamed), encoding="utf-8")
    assert any(row.get("code") == "legacy_id_refused" for row in env.rescan()["findings"])
    review = env.plan(WITH_CAMPAIGN).review()
    refused = [row for row in review["unresolved"] if row["reason"] == "legacy_id_refused"]
    assert [row["path"] for row in refused] == [f"{CAMPAIGN}/scenes/{renamed}.md"]
    assert not any("/chats/" in path for path in review["writes"])
    assert f"{CAMPAIGN_DIR}scope.json" in review["writes"]  # the rest of the campaign still imports


def test_an_image_reference_in_an_imported_card_is_reported_not_rewritten(env):
    url = "/api/worlds/saltmarch/characters/mara/versions/older/images/gallery_1"
    write(env.v1, f"{MARA}/older.json", json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Mara", "description": f"A diver. ![tide]({url})"}}, indent=2))
    env.rescan()
    review = env.plan(WORLD_ONLY).review()
    rows = [row for row in review["unresolved"] if row["reason"] == "image_reference_not_translated"]
    assert [row["legacy_id"] for row in rows] == [f"v1:{ARCHIVE}:{MARA}/older.json"]
    assert any("mara.older/" in path for path in review["writes"])  # the card itself still imports


def test_a_sidecar_in_the_wrong_shard_does_not_hide_the_real_picture(env):
    objects = env.v1 / "assets/image-store/objects"
    stray = objects / "ff" / f"{RED_ID}.json"  # V1 never reads it: RED's own shard is 64
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes((objects / RED_ID[4:6] / f"{RED_ID}.json").read_bytes())
    env.rescan()
    review = env.plan(WORLD_ONLY).review()
    assert "image_bytes_unavailable" not in reasons(review)
    assert any("mara.default/avatar" in path for path in review["writes"])


def _creature_named_mara(env):
    actor = env.store.root / CAMPAIGN_DIR / "instances" / "character" / "mara.json"
    creature = actor.parent.parent / "creature" / "mara.json"
    creature.parent.mkdir(parents=True, exist_ok=True)
    creature.write_text(actor.read_text(encoding="utf-8").replace("/character/mara", "/creature/mara"),
                        encoding="utf-8")


def test_an_actor_whose_id_another_kind_also_uses_is_never_bound(env):
    scene = f"v1:{ARCHIVE}:{CAMPAIGN}/scenes/{SID}.md"
    env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, frozenset({scene}))).apply()
    _creature_named_mara(env)
    review = env.plan(WITH_CAMPAIGN).review()
    assert "instance_id_conflict" in reasons(review)
    assert not any("/chats/" in path for path in review["writes"])


def test_an_id_another_kind_takes_after_the_plan_stops_the_apply(env):
    scene = f"v1:{ARCHIVE}:{CAMPAIGN}/scenes/{SID}.md"
    env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, frozenset({scene}))).apply()
    plan = env.plan(WITH_CAMPAIGN)
    assert any("/chats/" in path for path in plan.review()["writes"])
    _creature_named_mara(env)
    with pytest.raises(StaleWrite):
        plan.apply()


def test_a_persona_with_no_front_matter_keeps_its_whole_text_as_description(env):
    write(env.v1, "worlds/saltmarch/pcs/winifred/default.md", "A debtor without front matter.\n")
    env.rescan()
    preview = env.plan(WORLD_ONLY).preview()
    winifred = next(data for key, data in preview.items() if "winifred.default/" in key)
    assert json.loads(winifred)["payload"]["description"] == "A debtor without front matter."


def test_a_selected_campaigns_own_lore_is_listed_as_not_imported(env):
    write(env.v1, f"{CAMPAIGN}/lore/the-new-vow.md", frontmatter([("name", "The New Vow")], "A vow.\n"))
    env.rescan()
    review = env.plan(WITH_CAMPAIGN).review()
    assert {"legacy_id": f"v1:{ARCHIVE}:{CAMPAIGN}/lore/the-new-vow.md", "family": "lore"} in review["not_imported"]
