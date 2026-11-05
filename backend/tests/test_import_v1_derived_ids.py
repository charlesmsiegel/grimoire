"""Sprint 026 follow-up: a world whose V1 id has no V2 spelling still imports once it is mapped.

011 proposes ``world:<id>/<kind>/<entity>`` only when ``world:<id>`` is a valid source, so a V1 world
called ``Old_Coast`` gets no candidate for anything in it. When the reviewer maps that world to a
source, the importer derives each id by 011's own rule under that source. These tests rename the
fixture's ``saltmarch`` to ``Old_Coast`` and check that the remapped world imports exactly what the
spelled one does, that re-runs neither duplicate, overwrite nor resurrect anything, that two records
that would share an id both lose it, and that an id existing in V2 is never taken over.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from grimoire.core.identity import SourceRef
from grimoire.store import SourceManifest
from grimoire.import_v1 import Selection
from grimoire.migrate_v1 import definition_candidate, world_source
from grimoire.store.layout import address_parts

from image_fixtures import handle
from import_fixtures import ARCHIVE, CAMPAIGN_ID, SALTMARCH, SERAPHINE_REF, Env
from store_helpers import OWNER
from v1_fixture import CAMPAIGN, PERSONA, frontmatter, tree_digest, write

OLD = "Old_Coast"
REMAPPED = Selection({OLD: SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})
SPELLED = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})


def _rename_world(root: Path) -> None:
    """Move the fixture's ``saltmarch`` to ``Old_Coast``, with every V1 reference to it."""
    os.rename(root / "worlds" / "saltmarch", root / "worlds" / OLD)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".json"):
            data = path.read_bytes()
            changed = data.replace(b"world: saltmarch", f"world: {OLD}".encode()) \
                .replace(b"/api/worlds/saltmarch/", f"/api/worlds/{OLD}/".encode())
            if changed != data:
                path.write_bytes(changed)


def _env(tmp: Path, *, renamed: bool) -> Env:
    tmp.mkdir()
    environment = Env(tmp)
    if renamed:
        _rename_world(environment.v1)
        environment.rescan()
    return environment


@pytest.fixture
def env(tmp_path):
    environment = _env(tmp_path / "old", renamed=True)
    yield environment
    environment.store.close()
    assert tree_digest(environment.v1) == environment.v1_before  # V1 changes only where a test says so


def reasons(review) -> set[tuple[str, str | None]]:
    return {(row["reason"], row.get("detail")) for row in review["unresolved"]}


def legacy(path: str) -> str:
    return f"v1:{ARCHIVE}:{path}"


def _comparable(review) -> dict:
    """What a review maps where, with the world's V1 name taken out of legacy ids."""
    mapping = {key: sorted(sorted((k, str(v).replace(OLD, "saltmarch")) for k, v in row.items())
                           for row in rows) for key, rows in review["mapping"].items()}
    return {"writes": review["writes"], "mapping": mapping,
            "unresolved": sorted((row["reason"], row.get("detail")) for row in review["unresolved"])}


def test_011_spells_nothing_in_the_world_and_its_rule_is_what_the_importer_uses(env):
    assert world_source(OLD) is None and world_source("saltmarch") == SALTMARCH
    rows = [row for row in env.manifest["identity_candidates"]["definitions"]
            if (row.get("v1") or {}).get("world") == OLD and row["family"] != "world"]
    assert rows and all(row["status"] == "no_id_candidate" and row["candidate"] is None for row in rows)
    assert definition_candidate(SALTMARCH, "characters", "mara", "older") == SourceRef(SALTMARCH, "character",
                                                                                         "mara.older")
    assert definition_candidate(SALTMARCH, "characters", "Mara", "older") is None  # outside the grammar
    assert definition_candidate(SALTMARCH, "unknown", "mara", None) is None


def test_a_remapped_world_imports_exactly_what_the_spelled_world_does(env, tmp_path):
    spelled = _env(tmp_path / "spelled", renamed=False)
    try:
        expected = spelled.plan(SPELLED).review()
        review = env.plan(REMAPPED).review()
        assert _comparable(review) == _comparable(expected)
        assert {"code": "ids_derived", "world": OLD, "source": SALTMARCH} in review["flags"]
        assert not any(flag["code"] == "ids_derived" for flag in expected["flags"])
        # portrayals, their alternate_portrayal link, campaign actors on them and the scene all import
        refs = {row["ref"] for row in review["mapping"]["definitions"]}
        assert {str(SERAPHINE_REF)} <= refs and any("/mara.older@" in ref for ref in refs)
        actors = {row["v1_ref"]: row["derives_from"] for row in review["mapping"]["actors"]}
        assert set(actors) == {"characters/mara", "characters/seraphine", "pcs/winifred"}
        assert actors["characters/seraphine"] == str(SERAPHINE_REF)
        assert len(review["mapping"]["scenes"]) == 1
        result = env.plan(REMAPPED).apply()
        links = env.store.load().sources[SALTMARCH].manifest.links
        assert [(link.relation, link.object.split("@")[0]) for link in links] == [
            ("alternate_portrayal", "world:saltmarch/character/mara.older")]
        assert result.review["writes"] == review["writes"]
    finally:
        spelled.store.close()


def test_a_rerun_derives_the_same_ids_and_writes_nothing(env):
    env.plan(REMAPPED).apply()
    review = env.plan(REMAPPED, clock="2027-01-01T00:00:00Z").review()
    assert review["writes"] == []
    assert set(review["outcomes"].values()) == {"present"}


def test_a_derived_definition_deleted_in_v2_never_comes_back(env):
    env.plan(REMAPPED).apply()
    path = "/".join(address_parts(SERAPHINE_REF))
    (env.store.root / path).unlink()
    review = env.plan(REMAPPED).review()
    assert review["outcomes"][path] == "removed"
    assert path not in review["writes"]


def test_a_remapped_world_is_never_imported_again_under_another_source(env):
    env.plan(REMAPPED).apply()
    review = env.plan(Selection({OLD: "world:harbor"})).review()
    assert ("already_imported_as", SALTMARCH) in reasons(review)
    assert not any(path.startswith("sources/world/harbor/") for path in review["writes"])


def test_two_records_that_would_share_a_derived_id_both_lose_it(env):
    write(env.v1, f"worlds/{OLD}/pcs/mara/pc.md", frontmatter([("name", "Mara"), ("default_version", "default")]))
    write(env.v1, f"worlds/{OLD}/pcs/mara/default.md", PERSONA)
    env.rescan()
    review = env.plan(Selection({OLD: SALTMARCH})).review()
    found = reasons(review)
    for path in (f"worlds/{OLD}/characters/mara/default.json", f"worlds/{OLD}/pcs/mara/default.md"):
        assert {"legacy_id": legacy(path), "reason": "id_candidate_collision", "detail": "mara.default"} \
            in review["unresolved"]
    refs = {row["ref"] for row in review["mapping"]["definitions"]}
    assert not any("/mara.default@" in ref for ref in refs)
    assert any("/mara.older@" in ref for ref in refs)  # the other portrayal of the record still imports
    assert ("no_candidate", None) not in found


def test_a_record_whose_own_id_has_no_v2_spelling_still_gets_no_candidate(env):
    write(env.v1, f"worlds/{OLD}/characters/Bad_Name/character.md",
          frontmatter([("name", "Bad"), ("default_version", "default")]))
    write(env.v1, f"worlds/{OLD}/characters/Bad_Name/default.json",
          (env.v1 / f"worlds/{OLD}/characters/mara/default.json").read_bytes())
    env.rescan()
    review = env.plan(Selection({OLD: SALTMARCH})).review()
    assert {"legacy_id": legacy(f"worlds/{OLD}/characters/Bad_Name/default.json"), "reason": "no_candidate"} \
        in review["unresolved"]
    assert str(SERAPHINE_REF) in {row["ref"] for row in review["mapping"]["definitions"]}


def test_a_declined_derived_portrayal_lets_its_scenes_go_on_without_it(env):
    skip = frozenset({legacy(f"worlds/{OLD}/characters/seraphine/default.json")})
    review = env.plan(Selection({OLD: SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, skip)).review()
    assert ("appearance_base_not_imported", "characters/seraphine") in reasons(review)
    assert not any(reason == "cast_not_imported" for reason, _ in reasons(review))
    assert len(review["mapping"]["scenes"]) == 1


def test_a_source_this_archive_did_not_write_is_never_taken_over(env):
    txn = env.store.transaction()
    txn.put(SourceManifest(SALTMARCH, OWNER, "Someone's Saltmarch"), None)
    txn.commit(handle())
    review = env.plan(REMAPPED).review()
    assert ("source_exists", SALTMARCH) in reasons(review)
    assert not any(path.startswith(("sources/world/saltmarch/", "campaigns/")) for path in review["writes"])


def test_an_unselected_world_with_no_spelling_is_only_reported(env):
    review = env.plan().review()
    assert ("no_id_candidate", OLD) in reasons(review)
    assert not any(flag["code"] == "ids_derived" for flag in review["flags"])
    assert not any(OLD in str(row) for row in review["mapping"]["definitions"])



def test_a_record_of_another_family_sharing_a_derived_id_withholds_both(env):
    write(env.v1, f"worlds/{OLD}/lore/seraphine.default.md",
          frontmatter([("name", "A note"), ("keys", "note")], "A note.\n"))
    env.rescan()
    review = env.plan(Selection({OLD: SALTMARCH})).review()
    assert {"legacy_id": legacy(f"worlds/{OLD}/characters/seraphine/default.json"),
            "reason": "id_candidate_collision", "detail": "seraphine.default"} in review["unresolved"]
    assert str(SERAPHINE_REF) not in {row["ref"] for row in review["mapping"]["definitions"]}


def test_a_collision_011_withheld_in_a_spelled_world_is_reported_the_same_way(tmp_path):
    spelled = _env(tmp_path / "spelled", renamed=False)
    try:
        write(spelled.v1, "worlds/saltmarch/pcs/mara/pc.md", frontmatter([("name", "Mara"), ("default_version", "default")]))
        write(spelled.v1, "worlds/saltmarch/pcs/mara/default.md", PERSONA)
        spelled.rescan()
        review = spelled.plan(Selection({"saltmarch": SALTMARCH})).review()
        assert {"legacy_id": legacy("worlds/saltmarch/pcs/mara/default.md"), "reason": "id_candidate_collision",
                "detail": "mara.default"} in review["unresolved"]
    finally:
        spelled.store.close()


def test_case_twin_worlds_each_import_under_their_own_source(env):
    os.rename(env.v1 / "worlds" / "realm", env.v1 / "worlds" / "old_coast")
    env.rescan()
    selection = Selection({OLD: "world:old_coast", "old_coast": "world:other"})
    review = env.plan(selection).review()
    refs = {row["ref"].split("@")[0] for row in review["mapping"]["definitions"]}
    assert {"world:old_coast/character/mara.older", "world:old_coast/character/seraphine.default",
            "world:other/character/mara.default"} <= refs
    assert "world:other/character/mara.older" not in refs  # realm's Mara has one portrayal
    env.plan(selection).apply()
    assert env.plan(selection).review()["writes"] == []


def test_a_mapped_world_is_not_reported_as_having_no_id(env):
    proposed = Selection.proposed(env.manifest)
    assert ("world", OLD) in proposed.unproposed
    review = env.plan(Selection({**proposed.worlds, OLD: "world:old-coast"}, proposed.campaigns,
                                proposed.skip, proposed.unproposed)).review()
    assert not any(reason == "no_id_candidate" for reason, _ in reasons(review))
    assert any(path.startswith("sources/world/old-coast/") for path in review["writes"])


def test_an_npc_is_never_based_on_a_pc_portrayal_that_shares_its_name(env):
    _mara_locked_at_a_pc_version(env, OLD)
    review = env.plan(REMAPPED).review()
    assert ("appearance_base_mismatch", "characters/mara") in reasons(review)
    assert "characters/mara" not in {row["v1_ref"] for row in review["mapping"]["actors"]}


def _mara_locked_at_a_pc_version(env, world: str) -> None:
    write(env.v1, f"worlds/{world}/pcs/mara/pc.md", frontmatter([("name", "Mara"), ("default_version", "ghost")]))
    write(env.v1, f"worlds/{world}/pcs/mara/ghost.md", PERSONA)
    path = env.v1 / CAMPAIGN / "appearances.json"
    appearances = json.loads(path.read_text(encoding="utf-8"))
    appearances["characters/mara"]["version"] = "ghost"
    write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()


def test_declining_another_family_s_portrayal_never_drops_an_appearance_from_its_scenes(env):
    _mara_locked_at_a_pc_version(env, OLD)
    skip = frozenset({legacy(f"worlds/{OLD}/pcs/mara/ghost.md")})
    review = env.plan(Selection({OLD: SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, skip)).review()
    assert ("cast_not_imported", "characters/mara") in reasons(review)
    assert review["mapping"]["scenes"] == []


def test_declining_a_collided_portrayal_lets_its_scenes_go_on_without_it(tmp_path):
    spelled = _env(tmp_path / "spelled", renamed=False)
    try:
        write(spelled.v1, "worlds/saltmarch/lore/seraphine.default.md",
              frontmatter([("name", "A note"), ("keys", "note")], "A note.\n"))
        spelled.rescan()
        skip = frozenset({legacy("worlds/saltmarch/characters/seraphine/default.json")})
        review = spelled.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID},
                                        skip)).review()
        assert not any(reason == "cast_not_imported" for reason, _ in reasons(review))
        assert len(review["mapping"]["scenes"]) == 1
    finally:
        spelled.store.close()


def test_declining_a_world_portrayal_never_drops_a_campaign_s_detached_actor(env):
    write(env.v1, f"{CAMPAIGN}/detached.json", json.dumps(["characters/mara"], indent=2) + "\n")
    env.rescan()
    skip = frozenset({legacy(f"worlds/{OLD}/characters/mara/default.json")})
    review = env.plan(Selection({OLD: SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, skip)).review()
    assert ("cast_not_imported", "characters/mara") in reasons(review)
    assert review["mapping"]["scenes"] == []


def test_declining_a_world_portrayal_never_drops_an_actor_the_world_does_not_list(env):
    (env.v1 / "worlds" / OLD / "characters" / "seraphine" / "character.md").unlink()
    env.rescan()
    skip = frozenset({legacy(f"worlds/{OLD}/characters/seraphine/default.json")})
    review = env.plan(Selection({OLD: SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, skip)).review()
    assert ("cast_not_imported", "characters/seraphine") in reasons(review)
    assert review["mapping"]["scenes"] == []
