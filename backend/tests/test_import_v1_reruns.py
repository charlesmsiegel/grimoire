"""Sprint 026 re-runs: what the manifest leaves open, V2 edits and deletions, and the receipts.

Each test imports part of the synthetic V1 library, changes V2 (or, on purpose, V1 and its review),
and plans again: nothing unresolved is applied, nothing the user deleted comes back, an edit is
never a reason to refuse the rest, and the receipt keeps mapping every run.
"""

from __future__ import annotations

import json
import shutil

import pytest

from grimoire.core.identity import ImageRef, InstanceRef, LegacyId, PlacementRef, ScopeRef, SourceRef
from grimoire.images import Placement
from grimoire.imagestore import ImageLibrary
from grimoire.import_v1 import ImportRefused, Selection, image_id_of, legacy_map
from grimoire.migrate_v1 import ManifestMismatch
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import StaleWrite, etag
from grimoire.store.layout import address_parts, blob_parts

from image_fixtures import handle
from import_fixtures import ARCHIVE, CAMPAIGN_ID, MARA_DEFAULT_REF, REALM, SALTMARCH, SERAPHINE_REF, Env
from ledger_helpers import open_authority
from store_helpers import OWNER
from v1_fixture import BLUE_ID, CAMPAIGN, RED, RED_ID, SID, sha, tree_digest

WORLD_ONLY = Selection({"saltmarch": SALTMARCH})
WITH_CAMPAIGN = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})
WITHOUT_SERAPHINE = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID},
                              frozenset({f"v1:{ARCHIVE}:worlds/saltmarch/characters/seraphine/default.json"}))
CAMPAIGN_DIR = f"campaigns/{CAMPAIGN_ID}/"
SCOPE = "/".join(address_parts(ScopeRef(CAMPAIGN_ID)))
SERAPHINE = InstanceRef(CAMPAIGN_ID, "character", "seraphine")


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.store.close()
    assert tree_digest(environment.v1) == environment.v1_before  # V1 changes only where a test says so


def reasons(review) -> set[tuple[str, str | None]]:
    return {(row["reason"], row.get("detail")) for row in review["unresolved"]}


def test_a_scene_the_manifest_could_not_attribute_is_not_imported(env):
    (env.v1 / CAMPAIGN / "scenes" / f"{SID}.md").write_bytes(b"\xff\xfe not text")
    manifest = env.rescan()
    assert manifest["status"] == "ready_for_review"
    review = env.plan(WITH_CAMPAIGN).review()
    assert ("scene_unresolved", "unresolved") in reasons(review)
    assert not any("/chats/" in path for path in review["writes"])
    assert f"{CAMPAIGN_DIR}instances/character/mara.json" in review["writes"]  # the rest still imports


def test_a_deleted_play_scope_takes_the_campaign_out_of_later_runs(env):
    env.plan(WITHOUT_SERAPHINE).apply()
    (env.store.root / SCOPE).unlink()
    review = env.plan(WITH_CAMPAIGN).review()
    assert review["outcomes"][SCOPE] == "removed"
    assert ("campaign_scope_removed", CAMPAIGN_ID) in reasons(review)
    assert not any(path.startswith(CAMPAIGN_DIR) for path in review["writes"])


def test_a_campaign_deleted_between_plan_and_apply_gets_nothing(env):
    env.plan(WITHOUT_SERAPHINE).apply()
    plan = env.plan(WITH_CAMPAIGN)
    assert any(path.startswith(CAMPAIGN_DIR) for path in plan.review()["writes"])
    shutil.rmtree(env.store.root / "campaigns" / CAMPAIGN_ID)
    with pytest.raises(StaleWrite):
        plan.apply()
    assert not (env.store.root / "campaigns" / CAMPAIGN_ID).exists()


def test_a_v2_art_edit_never_blocks_the_rest_of_a_later_run(env):
    env.plan(WORLD_ONLY).apply()
    subject = MARA_DEFAULT_REF.unpinned()
    path = "/".join(address_parts(PlacementRef(subject, "avatar")))
    images = ImageLibrary(env.store, handle(), owner=OWNER)
    blue = ImageRef(SALTMARCH, image_id_of(BLUE_ID))
    images.place(subject, "avatar", Placement("set", "avatar", blue), etag(env.store.read_exact(path))).commit()
    review = env.plan(WITH_CAMPAIGN).review()
    assert review["outcomes"][path] == "kept"
    # the campaign's re-crop was of the V1 avatar, which is no longer the one shown: it is left out
    recrop = f"{CAMPAIGN_DIR}placements/character/mara/avatar.json"
    assert recrop not in review["writes"]
    assert any(row["reason"] == "placement_needs_not_imported" for row in review["unresolved"])
    assert SCOPE in review["writes"] and f"{CAMPAIGN_DIR}instances/character/mara.json" in review["writes"]


def test_a_campaign_hide_needs_the_world_art_it_hides(env):
    env.plan(WORLD_ONLY).apply()
    gallery = "/".join(address_parts(PlacementRef(MARA_DEFAULT_REF.unpinned(), "gallery_1")))
    (env.store.root / gallery).unlink()
    review = env.plan(WITH_CAMPAIGN).review()
    assert review["outcomes"][gallery] == "removed"
    assert f"{CAMPAIGN_DIR}placements/character/mara/gallery_1.json" not in review["writes"]


def test_a_manifest_edited_to_equal_but_different_values_is_refused(env):
    edited = json.loads(json.dumps(env.manifest))
    rows = [row for row in edited["images"]["placements"] if row.get("focus") == 40]
    assert rows
    for row in rows:
        row["focus"] = 40.0
    with pytest.raises(ManifestMismatch) as caught:
        env.plan(manifest=edited)
    assert caught.value.reason == "manifest_changed"


def _reserve(env, instance_id: str):
    """A grant names a campaign individual's file, as one outlives a hand delete; the caller closes it.

    Once the library has a security ledger every write needs its authority, so the owner is granted
    the three top-level families too, which name no individual and reserve nothing."""
    authority = open_authority(env.store)
    for number, scope in enumerate(("sources", "campaigns", "assets")):
        authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse(scope),
                                  OWNER))
    authority.add_grant(Grant("g3", "app:other", frozenset({Permission.APPLY}),
                              ResourcePath.parse(f"{CAMPAIGN_DIR}instances/character/{instance_id}.json"), OWNER))
    return authority


def test_a_taken_instance_id_is_never_given_to_an_imported_actor(env):
    env.plan(WITHOUT_SERAPHINE).apply()
    authority = _reserve(env, "seraphine")
    review = env.plan(WITH_CAMPAIGN, grant=authority.handle_for(OWNER, trusted=True)).review()
    assert ("actor_id_taken", str(SERAPHINE)) in reasons(review)
    assert not any("seraphine" in path for path in review["writes"] if path.startswith(CAMPAIGN_DIR))
    authority.close()


def test_a_campaign_whose_taken_ids_cannot_be_listed_imports_no_actor_into_it(env, tmp_path):
    """A placements folder that is a link hides which instance ids are taken, so no id is guessed: the
    review names the gap and plans no actor write into that campaign (Wave 4 audit)."""
    env.plan(WITHOUT_SERAPHINE).apply()
    folder = env.store.root / CAMPAIGN_DIR / "placements"
    elsewhere = tmp_path / "elsewhere"
    shutil.move(folder, elsewhere)
    try:
        folder.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this filesystem cannot hold a link")
    review = env.plan(WITH_CAMPAIGN).review()
    assert "taken_ids_unlisted" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if path.startswith(f"{CAMPAIGN_DIR}instances/")]


def test_an_instance_id_taken_after_the_plan_refuses_the_apply(env):
    env.plan(WITHOUT_SERAPHINE).apply()
    authority = _reserve(env, "nobody")  # the ledger first, so the plan already runs under its authority
    plan = env.plan(WITH_CAMPAIGN, grant=authority.handle_for(OWNER, trusted=True))
    assert f"{CAMPAIGN_DIR}instances/character/seraphine.json" in plan.review()["writes"]
    authority.add_grant(Grant("g4", "app:other", frozenset({Permission.APPLY}),
                              ResourcePath.parse(f"{CAMPAIGN_DIR}instances/character/seraphine.json"), OWNER))
    before = env.v2()
    with pytest.raises(StaleWrite):
        plan.apply()
    assert env.v2() == before
    authority.close()


def test_v1_content_changed_since_import_is_reported_not_rewritten(env):
    env.plan(WITH_CAMPAIGN).apply()
    scene = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    scene.write_text(scene.read_text(encoding="utf-8") + '\n**Mara:** <!-- grimoire-response {"post_id": '
                     '"00000000000000000000000000000014"} -->\nThe tide is in.\n', encoding="utf-8")
    review = env.plan(WITH_CAMPAIGN, manifest=env.rescan()).review()
    chats = [path for path in review["outcomes"] if "/chats/" in path]
    assert chats and all(review["outcomes"][path] == "present" for path in chats)
    assert {("v1_changed", path) for path in chats} <= reasons(review)
    assert review["writes"] == []


def test_lost_receipts_refuse_a_rerun_instead_of_restoring_deletions(env):
    env.plan(WITH_CAMPAIGN).apply()
    (env.store.root / CAMPAIGN_DIR / "placements" / "character" / "mara" / "gallery_1.json").unlink()
    shutil.rmtree(env.store.root / "sources" / "import")
    with pytest.raises(ImportRefused) as caught:
        env.plan(WITH_CAMPAIGN)
    assert caught.value.reason == "receipt_missing"


def test_the_newest_receipt_maps_every_run(env):
    env.plan(WORLD_ONLY).apply()
    result = env.plan(Selection({"realm": REALM})).apply()
    mapping = legacy_map(env.store.read(result.receipt).envelope.payload)
    assert mapping.resolve(LegacyId(ARCHIVE, "worlds/saltmarch/characters/seraphine/default.json")) == SERAPHINE_REF
    realm = mapping.resolve(LegacyId(ARCHIVE, "worlds/realm/characters/mara/default.json"))
    assert realm.unpinned() == SourceRef(REALM, "character", "mara.default")


def test_a_world_imported_once_is_never_imported_again_under_another_id(env):
    env.plan(WORLD_ONLY).apply()
    review = env.plan(Selection({"saltmarch": "world:harbor"})).review()
    assert ("already_imported_as", SALTMARCH) in reasons(review)
    assert not any(path.startswith("sources/world/harbor/") for path in review["writes"])


def test_a_campaign_imported_once_is_never_imported_again_under_another_id(env):
    env.plan(WITH_CAMPAIGN).apply()
    review = env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": "another-ledger"})).review()
    assert ("already_imported_as", CAMPAIGN_ID) in reasons(review)
    assert not any(path.startswith("campaigns/another-ledger/") for path in review["writes"])


def test_a_second_v1_campaign_is_never_merged_into_an_imported_one(env):
    shutil.copytree(env.v1 / CAMPAIGN, env.v1 / "campaigns" / "second-ledger")
    env.rescan()
    env.plan(WITH_CAMPAIGN).apply()
    review = env.plan(Selection({"saltmarch": SALTMARCH}, {"second-ledger": CAMPAIGN_ID})).review()
    assert ("campaign_exists", CAMPAIGN_ID) in reasons(review)
    assert not any(path.startswith(CAMPAIGN_DIR) for path in review["writes"])


def test_an_imported_definition_deleted_after_the_plan_refuses_the_apply(env):
    env.plan(WORLD_ONLY).apply()
    plan = env.plan(WITH_CAMPAIGN)
    definition = "/".join(address_parts(MARA_DEFAULT_REF))
    assert plan.review()["outcomes"][definition] == "present"  # the campaign's Mara is bound to it
    (env.store.root / definition).unlink()
    before = env.v2()
    with pytest.raises(StaleWrite):
        plan.apply()
    assert env.v2() == before


def test_a_damaged_blob_in_v2_leaves_out_only_its_picture(env):
    path = env.store.root.joinpath(*blob_parts(sha(RED), "png"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"X" * len(RED))  # the same size, other bytes
    review = env.plan(WORLD_ONLY).review()
    assert "image_unavailable" in {reason for reason, _ in reasons(review)}
    assert "/".join(address_parts(MARA_DEFAULT_REF)) in review["writes"]  # the rest still imports
    images = "/".join(address_parts(ImageRef(SALTMARCH, image_id_of(BLUE_ID)))), \
        "/".join(address_parts(ImageRef(SALTMARCH, image_id_of(RED_ID))))
    assert images[0] in review["writes"] and images[1] not in review["writes"]  # Blue's picture still imports
    gallery = "/".join(address_parts(PlacementRef(MARA_DEFAULT_REF.unpinned(), "gallery_1")))
    avatar = "/".join(address_parts(PlacementRef(MARA_DEFAULT_REF.unpinned(), "avatar")))
    assert gallery in review["writes"] and avatar not in review["writes"]


def test_a_file_that_is_not_an_inventory_is_refused(env):
    with pytest.raises(ManifestMismatch) as caught:
        env.plan(manifest={"schema": "something-else"})
    assert caught.value.reason == "not_an_inventory"
