"""Campaign sprite patches inherit by actor lineage and resolved version."""

import pytest

from grimoire.store import campaigns, overlay, pcs, worlds
from grimoire.store.expression_library import contracts, read, write
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


def campaign_target(target):
    cid = campaigns.create_campaign("Saltmarch", target.owner_id)
    return contracts.ExpressionTarget("campaign", cid, target.kind, target.actor_id, target.version_id)


def test_override_one_sprite_keeps_other_world_sprites(target):
    outfit(target, "armor")
    apply(target, {"op": "put-expression", "outfit": "armor", "expression": "anger", "data": png(), "ext": "png"})
    local = campaign_target(target)
    original = read.effective(local)
    apply(local, {"op": "put-expression", "outfit": "armor", "expression": "anger", "data": png((80, 30, 20, 90)), "ext": "png"})
    sprites = read.effective(local)["outfits"]["armor"]["expressions"]
    assert sprites["neutral"]["image_id"] == original["outfits"]["armor"]["expressions"]["neutral"]["image_id"]
    assert sprites["neutral"]["source"] == "inherited"
    assert sprites["anger"]["source"] == "overridden"


def test_hide_differs_from_remove_local_override(target):
    outfit(target)
    local = campaign_target(target)
    apply(local, {"op": "put-expression", "outfit": "everyday", "expression": "neutral", "data": png((99, 0, 0, 255)), "ext": "png"})
    apply(local, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"})
    assert read.effective(local)["outfits"]["everyday"]["expressions"]["neutral"]["source"] == "inherited"
    apply(local, {"op": "hide-expression", "outfit": "everyday", "expression": "neutral"})
    assert read.effective(local)["outfits"]["everyday"]["expressions"] == {}
    apply(local, {"op": "restore-expression", "outfit": "everyday", "expression": "neutral"})
    assert "neutral" in read.effective(local)["outfits"]["everyday"]["expressions"]


def test_detached_same_id_does_not_inherit(target):
    outfit(target)
    local = campaign_target(target)
    overlay.materialize_actor(local.owner_id, "characters", target.actor_id)
    overlay.add_detached(local.owner_id, "characters/" + target.actor_id)
    assert read.effective(local)["outfits"] == {}


def test_world_change_invalidates_campaign_save(target):
    outfit(target)
    local = campaign_target(target)
    before = read.effective(local)
    apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"label": "Casual"}})
    with pytest.raises(contracts.ExpressionConflictError):
        write.apply_effective(local, [{"op": "hide-outfit", "outfit": "everyday"}],
                              expected_effective_revision=before["effective_revision"])


def test_metadata_empty_aliases_and_duplicate_are_independent(target):
    outfit(target)
    apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"clothing_aliases": ["coat"], "presentation": {"scale": 2}}})
    local = campaign_target(target)
    apply(local, {"op": "patch-outfit", "outfit": "everyday", "metadata": {"clothing_aliases": [], "presentation": {"offset_x": .2}}},
          {"op": "duplicate-outfit", "outfit": "everyday", "new_outfit": "copy"})
    merged = read.effective(local)["outfits"]
    assert merged["everyday"]["clothing_aliases"] == []
    assert merged["everyday"]["presentation"]["scale"] == 2
    apply(target, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"})
    assert "neutral" in read.effective(local)["outfits"]["copy"]["expressions"]


def test_pc_inherits_its_own_version_collection(target):
    root = worlds.world_root(target.owner_id)
    actor, version = pcs.create_pc(root, "Winifred", [])
    pc = contracts.ExpressionTarget("world", target.owner_id, "pcs", actor, version)
    outfit(pc)
    local = campaign_target(pc)
    assert read.effective(local)["outfits"]["everyday"]["expressions"]["neutral"]["source"] == "inherited"


def test_duplicate_uses_prior_mutations_in_the_same_batch(target):
    outfit(target)
    after = apply(target, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"},
                  {"op": "duplicate-outfit", "outfit": "everyday", "new_outfit": "copy"})
    assert after["outfits"]["copy"]["expressions"] == {}
