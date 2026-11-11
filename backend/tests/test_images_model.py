"""Pure image values, codec and resolver (Sprint 018): no files, no store, no provider."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core.identity import ImageRef, InstanceRef, OwnerRef, PlacementRef, SourceRef
from grimoire.images import (
    MEDIA_TYPES, BlobFact, BlobInfo, Crop, Expression, Facts, Focus, Hidden, ImageCodecError, ImageEntry,
    ImageModelError, ImageObject, Placement, PlacementEntry, Problem, Shown, Unavailable, expression_slot,
    image_from_payload, image_to_payload, placement_from_payload, placement_to_payload, resolve, slot_role,
    validate_placement,
)

SRC = Path(__file__).resolve().parents[1] / "src"
HARBOR = "world:harbor"
SAILOR = SourceRef(HARBOR, "character", "wren-sailor")
WREN_A = InstanceRef("c1", "character", "wren-a")
IMG = ImageRef(HARBOR, "wren")
LOCAL = ImageRef("campaign:c1", "scarred")
FOREIGN = ImageRef("catalog:other", "x")
SHA, SHA2 = "a" * 64, "b" * 64
BLOB = BlobInfo(SHA, "image/png", 10)


def test_media_types_equal_the_stores():
    from grimoire.store import MEDIA
    assert MEDIA_TYPES == MEDIA


# -- values and codec --------------------------------------------------------------------------

def test_image_payload_round_trip_keeps_unknown_fields_and_open_provenance():
    payload = {"blob": {"sha256": SHA, "media_type": "image/png", "size": 10},
               "provenance": {"legacy_id": "v1:arch:assets/x.json", "origin": "card", "x-charx": {"path": "a"}},
               "x-caption": "kept"}
    image = image_from_payload(payload)
    assert image.blob == BLOB and image.extras == {"x-caption": "kept"}
    assert image_to_payload(image) == payload


@pytest.mark.parametrize("payload", [
    None, [], {"blob": None},
    {"blob": {"sha256": SHA, "media_type": "image/png"}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": 10, "x": 1}},
    {"blob": {"sha256": SHA.upper(), "media_type": "image/png", "size": 10}},
    {"blob": {"sha256": SHA, "media_type": "image/svg+xml", "size": 10}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": True}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": 0}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": 1}, "provenance": {"legacy_id": "nope"}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": 1}, "provenance": {"note": "x" * 2001}},
    {"blob": {"sha256": SHA, "media_type": "image/png", "size": 1}, "provenance": "text"},
])
def test_bad_image_payloads_are_refused(payload):
    with pytest.raises(ImageCodecError):
        image_from_payload(payload)


def test_placement_payload_round_trip():
    payload = {"mode": "set", "role": "expression", "image": str(IMG), "crop": {"x": 0, "y": 500, "w": 10000, "h": 6000},
               "focus": {"x": 5000, "y": 3000}, "expression": {"outfit": "default", "name": "smile"},
               "provenance": {"note": "n", "x-extra": 1}, "x-future": [1, 2]}
    placement = placement_from_payload(payload)
    assert placement.expression == Expression("default", "smile") and placement.extras == {"x-future": [1, 2]}
    assert placement_to_payload(placement) == payload


@pytest.mark.parametrize("payload, reason", [
    ({"mode": "show", "role": "avatar", "image": str(IMG)}, "invalid_placement"),
    ({"mode": "set", "role": "banner", "image": str(IMG)}, "invalid_placement"),
    ({"mode": 1}, "invalid_placement"),
    ({"mode": "set", "role": "avatar"}, "invalid_field"),
    ({"mode": "set", "image": str(IMG)}, "invalid_field"),
    ({"mode": "adjust"}, "invalid_field"),
    ({"mode": "hide", "image": str(IMG)}, "invalid_field"),
    ({"mode": "hide", "crop": {"x": 0, "y": 0, "w": 1, "h": 1}}, "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": "world:harbor/character/x"}, "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "crop": {"x": 0.5, "y": 0, "w": 1, "h": 1}},
     "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "crop": {"x": 9000, "y": 0, "w": 2000, "h": 1}},
     "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "crop": {"x": 0, "y": 0, "w": 0, "h": 1}},
     "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "crop": {"x": 0, "y": 0, "w": 1, "h": 1, "z": 1}},
     "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "focus": {"x": True, "y": 0}}, "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "focus": {"x": 10001, "y": 0}}, "invalid_field"),
    ({"mode": "set", "role": "expression", "image": str(IMG)}, "invalid_field"),
    ({"mode": "set", "role": "avatar", "image": str(IMG), "expression": {"outfit": "a", "name": "b"}},
     "invalid_field"),
    ({"mode": "set", "role": "expression", "image": str(IMG), "expression": {"outfit": "a.b", "name": "c"}},
     "invalid_field"),
])
def test_bad_placement_payloads_are_refused_with_a_reason(payload, reason):
    with pytest.raises(ImageCodecError) as caught:
        placement_from_payload(payload)
    assert caught.value.reason == reason


@pytest.mark.parametrize("slot, role", [
    ("avatar", "avatar"), ("portrait", "portrait"), ("gallery_1", "gallery"), ("expr.default.smile", "expression"),
    ("expr.a.b.c", None), ("expr.a", None), ("expr.a.B", None), ("expr..x", None),
])
def test_the_slot_decides_the_role(slot, role):
    assert slot_role(slot) == role


def test_expression_slots_are_bounded():
    assert expression_slot("default", "smile") == "expr.default.smile"
    with pytest.raises(ImageModelError):
        expression_slot("o" * 60, "n" * 60)
    with pytest.raises(ImageModelError):
        expression_slot("a.b", "c")


def _set(role="avatar", image=IMG, **kwargs):
    return Placement("set", role, image, **kwargs)


@pytest.mark.parametrize("subject, slot, placement, reason", [
    (SAILOR, "avatar", Placement("inherit"), "mode_not_allowed"),
    (SAILOR, "avatar", Placement("adjust", image=IMG), "mode_not_allowed"),
    (OwnerRef(HARBOR), "cover", Placement("inherit"), "mode_not_allowed"),  # an owner inherits nothing either
    (OwnerRef("campaign:c1"), "cover", Placement("adjust", image=IMG), "mode_not_allowed"),
    (SAILOR, "gallery_1", _set("avatar"), "role_slot_mismatch"),
    (SAILOR, "avatar", _set("portrait"), "role_slot_mismatch"),
    (SAILOR, "expr.a.b", _set("expression", expression=Expression("a", "c")), "role_slot_mismatch"),
    (SAILOR, "expr.a.b.c", _set("gallery"), "role_slot_mismatch"),
    (SAILOR, "Avatar", _set(), "invalid_field"),
    (SAILOR, "g" * 124, _set("gallery"), "invalid_field"),
    (WREN_A, "portrait", Placement("hide", role="avatar"), "role_slot_mismatch"),
])
def test_placement_rules(subject, slot, placement, reason):
    assert validate_placement(subject, slot, placement)[0] == reason


def test_valid_placements_pass():
    assert validate_placement(SAILOR, "expr.a.b", _set("expression", expression=Expression("a", "b"))) is None
    assert validate_placement(WREN_A, "avatar", Placement("hide")) is None
    assert validate_placement(WREN_A, "avatar", Placement("adjust", image=IMG, focus=Focus(1, 2))) is None


# -- resolver ----------------------------------------------------------------------------------

def _entry(subject, slot, placement=None, reason=None):
    return PlacementEntry(PlacementRef(subject, slot), placement, reason, f"{subject}#placement/{slot}")


FACTS = Facts(
    images={IMG: ImageEntry(IMG, ImageObject(BLOB)),
            LOCAL: ImageEntry(LOCAL, ImageObject(BlobInfo(SHA2, "image/gif", 5))),
            FOREIGN: ImageEntry(FOREIGN, ImageObject(BLOB))},
    blobs={(SHA, "png"): BlobFact("present", 10), (SHA2, "gif"): BlobFact("present", 5)})
C1 = Crop(0, 0, 5000, 5000)
F1 = Focus(5000, 2000)
SOURCE = {"avatar": _entry(SAILOR, "avatar", _set(crop=C1, focus=F1)),
          "portrait": _entry(SAILOR, "portrait", _set("portrait"))}


def _instance(campaign, facts=FACTS, source=SOURCE, derivation=HARBOR):
    return resolve(WREN_A, source, {k: _entry(WREN_A, k, v) if isinstance(v, Placement) else v
                                    for k, v in campaign.items()}, facts, derivation_source=derivation)


def test_a_definition_shows_its_own_placements():
    result = resolve(SAILOR, SOURCE, {}, FACTS, derivation_source=HARBOR)
    assert [type(s) for s in result.slots] == [Shown, Shown]
    avatar = result.slot("avatar")
    assert (avatar.image, avatar.blob, avatar.crop, avatar.focus, avatar.layer) == (IMG, BLOB, C1, F1, "source")


def test_an_instance_inherits_hides_sets_and_adjusts():
    result = _instance({
        "avatar": Placement("hide"),
        "portrait": Placement("adjust", image=IMG, crop=Crop(100, 100, 100, 100)),
        "gallery_1": _set("gallery", LOCAL),
        "gallery_2": _set("gallery", IMG),
    })
    assert result.slot("avatar") == Hidden("avatar")
    portrait = result.slot("portrait")
    assert portrait.layer == "campaign_adjusted" and portrait.crop == Crop(100, 100, 100, 100) and portrait.image == IMG
    assert result.slot("gallery_1").layer == "campaign" and result.slot("gallery_1").image == LOCAL
    assert result.slot("gallery_2").image == IMG  # the derivation source's image is in scope


def test_adjust_merges_whole_fields_and_keeps_the_rest():
    result = _instance({"avatar": Placement("adjust", image=IMG, focus=Focus(5000, 7000))})
    avatar = result.slot("avatar")
    assert avatar.crop == C1 and avatar.focus == Focus(5000, 7000)


def test_hide_never_falls_back_even_to_a_same_named_image():
    named = ImageRef("campaign:c1", "avatar")
    facts = Facts({**FACTS.images, named: ImageEntry(named, ImageObject(BLOB))}, FACTS.blobs)
    assert _instance({"avatar": Placement("hide")}, facts).slot("avatar") == Hidden("avatar")
    assert _instance({"gallery_9": Placement("hide")}).slot("gallery_9") == Hidden("gallery_9")


@pytest.mark.parametrize("campaign, slot, reason", [
    ({"gallery_1": _set("gallery", FOREIGN)}, "gallery_1", "wrong_scope"),
    ({"gallery_1": _set("gallery", ImageRef("campaign:c2", "x"))}, "gallery_1", "wrong_scope"),
    ({"gallery_1": _set("gallery", ImageRef(HARBOR, "absent"))}, "gallery_1", "missing_image"),
    ({"gallery_1": Placement("adjust", image=IMG)}, "gallery_1", "nothing_to_adjust"),
    ({"avatar": Placement("adjust", image=LOCAL)}, "avatar", "stale_adjust"),
    ({"avatar": PlacementEntry(PlacementRef(WREN_A, "avatar"), None, "unsupported_format", "t")}, "avatar",
     "unsupported_format"),
    ({"avatar": PlacementEntry(PlacementRef(WREN_A, "avatar"), None, "invalid_placement", "t")}, "avatar",
     "invalid_placement"),
    ({"avatar": _set("portrait")}, "avatar", "invalid_placement"),
])
def test_broken_campaign_slots_are_unavailable_and_never_fall_back(campaign, slot, reason):
    found = _instance(campaign).slot(slot)
    assert isinstance(found, Unavailable) and found.reason == reason


def test_source_layer_problems_fail_closed():
    stranger = {"avatar": _entry(SAILOR, "avatar", _set(image=FOREIGN)),
                "portrait": _entry(SAILOR, "portrait", Placement("inherit")),  # hand-edited: it inherits nothing
                "gallery_1": _entry(SAILOR, "gallery_1", None, "invalid_placement")}
    result = resolve(SAILOR, stranger, {}, FACTS, derivation_source=HARBOR)
    assert {(s.slot, s.reason) for s in result.slots} == {
        ("avatar", "wrong_scope"), ("portrait", "invalid_placement"), ("gallery_1", "invalid_placement")}


@pytest.mark.parametrize("blob, reason", [
    (BlobFact("absent"), "missing_blob"), (BlobFact("lookalike"), "missing_blob"),
    (BlobFact("present", 11), "blob_size_mismatch"),
])
def test_blob_facts(blob, reason):
    facts = Facts(FACTS.images, {(SHA, "png"): blob})
    assert resolve(SAILOR, SOURCE, {}, facts, derivation_source=HARBOR).slot("avatar").reason == reason


def test_an_unknown_derivation_inherits_nothing_and_allows_only_campaign_images():
    result = _instance({"gallery_1": _set("gallery", IMG), "gallery_2": _set("gallery", LOCAL)}, source={},
                       derivation=None)
    assert result.slot("gallery_1").reason == "wrong_scope"
    assert result.slot("gallery_2").image == LOCAL and result.slot("avatar") is None


def test_resolution_is_deterministic_and_order_independent():
    campaign = {"portrait": Placement("hide"), "gallery_1": _set("gallery", LOCAL)}
    problems = [Problem("b", "x", ""), Problem("a", "y", "")]
    one = resolve(WREN_A, SOURCE, {k: _entry(WREN_A, k, v) for k, v in campaign.items()}, FACTS,
                  derivation_source=HARBOR, problems=problems)
    two = resolve(WREN_A, dict(reversed(SOURCE.items())),
                  {k: _entry(WREN_A, k, v) for k, v in reversed(campaign.items())}, FACTS,
                  derivation_source=HARBOR, problems=reversed(problems))
    assert one == two and [p.path for p in one.problems] == ["a", "b"]


def test_a_definition_has_no_campaign_layer():
    with pytest.raises(ValueError):
        resolve(SAILOR, SOURCE, {"avatar": _entry(WREN_A, "avatar", Placement("hide"))}, FACTS,
                derivation_source=HARBOR)


def test_the_package_loads_no_store_and_no_provider():
    code = "import sys, grimoire.images; print('\\n'.join(sorted(sys.modules)))"
    loaded = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(SRC)}).stdout.split()
    assert not [m for m in loaded if m.startswith(("grimoire.store", "grimoire.providers", "grimoire.imagestore"))]


def test_an_adjust_over_an_unavailable_base_keeps_the_base_reason():
    facts = Facts(FACTS.images, {(SHA, "png"): BlobFact("absent")})
    found = _instance({"avatar": Placement("adjust", image=IMG)}, facts).slot("avatar")
    assert found == Unavailable("avatar", "missing_blob", str(IMG))
