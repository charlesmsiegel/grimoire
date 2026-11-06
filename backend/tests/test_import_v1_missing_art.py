"""V1 art that is missing, or that a V1 campaign hid, never shows another picture (Sprints 026 and 038).

038's rule is that a missing picture stays an explicit ``unresolved`` placement and never falls back:
a V1 campaign's own picture whose bytes did not arrive must not show the world picture it replaced,
and a world picture whose bytes did not arrive is a missing slot, not an empty one. A V1 campaign's
expression layer is listed, never dropped in silence, and the sprites it hid stay hidden on the
campaign's actors. Synthetic fixture only.
"""

from __future__ import annotations

import json

import pytest

from grimoire.core.identity import InstanceRef, PlacementRef, SourceRef
from grimoire.images import Expression, Hidden, Shown, Unavailable
from grimoire.import_v1 import Selection
from grimoire.imagestore import ImageLibrary

from image_fixtures import handle
from import_fixtures import ARCHIVE, CAMPAIGN_ID, MARA_DEFAULT_REF, REALM, SALTMARCH, Env
from store_helpers import OWNER
from v1_fixture import (
    BLUE_ID, CAMPAIGN, EXPRESSIONS, MARA, MISSING_ID, NEUTRAL, REV, expression_manifest, placement, sidecar, tree_digest,
    write,
)

WITH_CAMPAIGN = Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})
CAMPAIGN_MARA = InstanceRef(CAMPAIGN_ID, "character", "mara")
MARA_SUBJECT = MARA_DEFAULT_REF.unpinned()
CAMPAIGN_AVATAR = f"{CAMPAIGN}/characters/mara/assets/default/image-refs/avatar.json"
WORLD_AVATAR = f"{MARA}/assets/default/image-refs/avatar.json"
CAMPAIGN_LAYER = f"{CAMPAIGN}/characters/mara/assets/default/expressions/manifest.json"
DANGLING = "a V1 picture whose image record is not in the V1 library"
UNARRIVED = "a V1 picture whose bytes did not arrive or do not match"
#: a picture V1 knows (its sidecar) whose blob never arrived
GREEN = b"\x89PNG\r\n\x1a\n" + b"synthetic green, never arrived"
GREEN_ID = "px1-" + "1" * 64


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.store.close()
    assert tree_digest(environment.v1) == environment.v1_before


def _images(env: Env) -> ImageLibrary:
    return ImageLibrary(env.store, handle(), owner=OWNER)


def _reasons(review) -> set[tuple[str, str | None]]:
    return {(row["reason"], row.get("detail")) for row in review["unresolved"]}


def _placements(review) -> dict[str, str]:
    return {row["ref"]: row["mode"] for row in review["mapping"]["placements"]}


def _unarrived(env: Env) -> None:
    write(env.v1, f"assets/image-store/objects/{GREEN_ID[4:6]}/{GREEN_ID}.json", sidecar(GREEN_ID, GREEN))


# -- B1: a missing picture never falls back --------------------------------------------------------

def test_a_campaign_override_whose_picture_is_missing_never_shows_the_world_picture(env):
    write(env.v1, CAMPAIGN_AVATAR, placement(MISSING_ID, focus=70))
    env.rescan()
    result = env.plan(WITH_CAMPAIGN).apply()
    assert ("dangling_object", MISSING_ID) in _reasons(result.review)  # still reported
    images = _images(env)
    assert isinstance(images.effective_placements(MARA_SUBJECT).slot("avatar"), Shown)  # the world keeps its own
    assert images.effective_placements(CAMPAIGN_MARA).slot("avatar") == Unavailable("avatar", "unresolved", DANGLING)
    placement_, _ = images.read_placement(PlacementRef(CAMPAIGN_MARA, "avatar"))
    assert (placement_.mode, placement_.role, placement_.image) == ("unresolved", "avatar", None)
    assert placement_.provenance == {"legacy_id": f"v1:{ARCHIVE}:{CAMPAIGN_AVATAR}", "note": DANGLING}
    assert _placements(result.review)[str(PlacementRef(CAMPAIGN_MARA, "avatar"))] == "unresolved"


def test_a_campaign_override_whose_bytes_did_not_arrive_is_unresolved_too(env):
    _unarrived(env)
    write(env.v1, CAMPAIGN_AVATAR, placement(GREEN_ID))
    env.rescan()
    result = env.plan(WITH_CAMPAIGN).apply()
    assert "image_bytes_unavailable" in {reason for reason, _ in _reasons(result.review)}
    shown = _images(env).effective_placements(CAMPAIGN_MARA).slot("avatar")
    assert shown == Unavailable("avatar", "unresolved", UNARRIVED)


def test_a_world_picture_whose_bytes_are_missing_is_an_unresolved_slot(env):
    _unarrived(env)
    write(env.v1, f"{MARA}/assets/default/image-refs/gallery_2.json", placement(GREEN_ID))
    env.rescan()
    result = env.plan(Selection({"saltmarch": SALTMARCH, "realm": REALM})).apply()
    images = _images(env)
    assert images.effective_placements(MARA_SUBJECT).slot("gallery_2") == Unavailable("gallery_2", "unresolved",
                                                                                     UNARRIVED)
    # Realm's Mara names a picture V1 has no record of: her avatar is missing, not empty
    realm_mara = images.effective_placements(SourceRef(REALM, "character", "mara.default"))
    assert realm_mara.slot("avatar") == Unavailable("avatar", "unresolved", DANGLING)
    assert ("dangling_object", MISSING_ID) in _reasons(result.review)  # still reported


def test_a_campaign_crop_of_a_missing_world_picture_is_reported_and_the_slot_stays_missing(env):
    write(env.v1, WORLD_AVATAR, placement(MISSING_ID, focus=40))  # the campaign re-crops this avatar
    env.rescan()
    result = env.plan(WITH_CAMPAIGN).apply()
    assert ("layering_base_unresolved", "focus_override") in _reasons(result.review)
    assert _images(env).effective_placements(CAMPAIGN_MARA).slot("avatar") == Unavailable("avatar", "unresolved",
                                                                                         DANGLING)


def test_a_campaign_picture_over_a_missing_world_picture_is_shown(env):
    write(env.v1, WORLD_AVATAR, placement(MISSING_ID))
    write(env.v1, CAMPAIGN_AVATAR, placement(BLUE_ID))
    env.rescan()
    result = env.plan(WITH_CAMPAIGN).apply()
    assert not any(reason.startswith("layering_") for reason, _ in _reasons(result.review))
    images = _images(env)
    assert isinstance(images.effective_placements(MARA_SUBJECT).slot("avatar"), Unavailable)
    shown = images.effective_placements(CAMPAIGN_MARA).slot("avatar")
    assert isinstance(shown, Shown) and shown.layer == "campaign"
    assert images.read_image(shown.image)[0].provenance["v1_object"] == BLUE_ID


def test_a_rerun_over_unresolved_art_writes_nothing(env):
    write(env.v1, CAMPAIGN_AVATAR, placement(MISSING_ID, focus=70))
    env.rescan()
    env.plan(WITH_CAMPAIGN).apply()
    again = env.plan(WITH_CAMPAIGN)
    assert again.review()["writes"] == [] and again.preview() == {}
    assert "v1_changed" not in {reason for reason, _ in _reasons(again.review())}
    # the picture is there by a later scan: the run's own unresolved placement is reported, not replaced
    write(env.v1, CAMPAIGN_AVATAR, placement(BLUE_ID, focus=70))
    env.rescan()
    later = env.plan(WITH_CAMPAIGN).review()
    path = next(path for path in later["outcomes"] if path.startswith(f"campaigns/{CAMPAIGN_ID}/placements/")
                and "avatar" in path)
    assert later["outcomes"][path] == "present" and path not in later["writes"]
    assert "v1_changed" in {reason for reason, _ in _reasons(later)}


# -- B2: a campaign's expression layer -------------------------------------------------------------

def _sprites(env: Env, campaign_manifest: dict) -> None:
    """Mara's default portrayal with a casual/neutral sprite, and a campaign layer over it."""
    write(env.v1, f"{MARA}/assets/default/expressions/revisions/{REV}/casual/image-refs/neutral.json", NEUTRAL)
    write(env.v1, f"{MARA}/assets/default/expressions/manifest.json",
          expression_manifest(EXPRESSIONS, {"casual/image-refs/neutral.json": NEUTRAL}))
    write(env.v1, CAMPAIGN_LAYER, expression_manifest({**EXPRESSIONS, **campaign_manifest}, {}))
    env.rescan()


@pytest.mark.parametrize("hidden", [{"hidden_expressions": {"casual": ["neutral"]}}, {"hidden_outfits": ["casual"]}],
                         ids=["expression", "outfit"])
def test_a_campaign_expression_layer_is_listed_and_its_hidden_sprites_stay_hidden(env, hidden):
    _sprites(env, hidden)
    result = env.plan(WITH_CAMPAIGN).apply()
    assert {"legacy_id": f"v1:{ARCHIVE}:{CAMPAIGN_LAYER}", "family": "expression_settings"} \
        in result.review["not_imported"]
    images = _images(env)
    assert isinstance(images.effective_placements(MARA_SUBJECT).slot("expr.casual.neutral"), Shown)
    assert images.effective_placements(CAMPAIGN_MARA).slot("expr.casual.neutral") == Hidden("expr.casual.neutral")
    placement_, _ = images.read_placement(PlacementRef(CAMPAIGN_MARA, "expr.casual.neutral"))
    assert placement_.provenance == {"legacy_id": f"v1:{ARCHIVE}:{CAMPAIGN_LAYER}"}


def test_a_campaign_layer_that_hides_nothing_the_world_placed_writes_no_placement(env):
    _sprites(env, {"hidden_expressions": {"casual": ["laughing"]}})
    review = env.plan(WITH_CAMPAIGN).review()
    assert {"legacy_id": f"v1:{ARCHIVE}:{CAMPAIGN_LAYER}", "family": "expression_settings"} in review["not_imported"]
    assert not any(ref.startswith(f"{CAMPAIGN_ID}/") and "expr." in ref for ref in _placements(review))


def test_a_campaign_layer_of_an_unselected_campaign_is_not_listed(env):
    _sprites(env, {"hidden_expressions": {"casual": ["neutral"]}})
    review = env.plan(Selection({"saltmarch": SALTMARCH})).review()
    assert CAMPAIGN_LAYER not in json.dumps(review)


def test_a_missing_world_sprite_is_unresolved_and_a_campaign_hide_over_it_still_hides_it(env):
    _unarrived(env)
    sprite = placement(GREEN_ID).encode()
    write(env.v1, f"{MARA}/assets/default/expressions/revisions/{REV}/casual/image-refs/neutral.json", sprite)
    write(env.v1, f"{MARA}/assets/default/expressions/manifest.json",
          expression_manifest(EXPRESSIONS, {"casual/image-refs/neutral.json": sprite}))
    write(env.v1, CAMPAIGN_LAYER, expression_manifest({**EXPRESSIONS, "hidden_outfits": ["casual"]}, {}))
    env.rescan()
    env.plan(WITH_CAMPAIGN).apply()
    images = _images(env)
    slot = "expr.casual.neutral"
    assert images.effective_placements(MARA_SUBJECT).slot(slot) == Unavailable(slot, "unresolved", UNARRIVED)
    world, _ = images.read_placement(PlacementRef(MARA_SUBJECT, slot))
    assert (world.mode, world.role, world.expression) == ("unresolved", "expression", Expression("casual", "neutral"))
    assert images.effective_placements(CAMPAIGN_MARA).slot(slot) == Hidden(slot)
