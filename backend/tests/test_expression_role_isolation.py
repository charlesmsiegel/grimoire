"""Image eligibility follows placements, even when the blob is shared."""

from grimoire.store import appearances, assets, image_store, image_usage, overlay, scenes, worlds
from grimoire.store.context import art
from tests.test_expressions_inheritance import campaign_target
from tests.test_expressions_store import apply, outfit
from tests.test_expressions_store import target as target


def test_expression_only_object_stays_out_of_art_context_and_avatar(target):
    image = outfit(target)["outfits"]["everyday"]["expressions"]["neutral"]["image_id"]
    image_store.merge_projection(image, {"description": "Mara beside a blue harbor."},
                                 "world:" + target.owner_id)
    local = campaign_target(target)
    sid = scenes.create_scene(local.owner_id, "Scene one")
    appearances.appear(local.owner_id, sid, target.kind, target.actor_id, target.version_id, "npc")
    cast = [{"kind": target.kind, "id": target.actor_id}]
    assert art.candidates(local.owner_id, cast, None, []) == []
    assert overlay.avatar_v(local.owner_id, target.actor_id, target.version_id) is None
    assert not any(image_usage.find(image).values())

    directory = assets.version_dir(worlds.world_root(target.owner_id), target.actor_id, target.version_id)
    assets.link_in(directory, "gallery_1", image)
    candidates = art.candidates(local.owner_id, cast, None, [])
    assert [(row["name"], row["image_id"]) for row in candidates] == [("gallery_1", image)]
    assert image_usage.find(image)["characters"]
    apply(target, {"op": "remove-expression", "outfit": "everyday", "expression": "neutral"})
    assert art.candidates(local.owner_id, cast, None, []) == candidates
    assert assets.image_path(worlds.world_root(target.owner_id), target.actor_id,
                             target.version_id, "gallery_1") is not None
