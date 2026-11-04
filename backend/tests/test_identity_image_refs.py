"""ImageRef and PlacementRef (Sprint 018): strict text forms, never returned by parse_ref."""

from __future__ import annotations

import pytest

from grimoire.core.identity import TEXT_MAX, ImageRef, InstanceRef, PlacementRef, SourceRef, parse_ref

WREN = SourceRef("world:harbor", "character", "wren")
WREN_A = InstanceRef("c1", "character", "wren-a")


@pytest.mark.parametrize("ref", [
    ImageRef("world:harbor", "wren"), ImageRef("card:mira", "px-1.v2"), ImageRef("campaign:c1", "scarred"),
])
def test_image_refs_round_trip(ref):
    assert ImageRef.parse(str(ref)) == ref


def test_image_ref_scopes():
    assert ImageRef("campaign:c1", "a").campaign_id == "c1" and ImageRef("campaign:c1", "a").source is None
    assert ImageRef("world:harbor", "a").source == "world:harbor" and ImageRef("world:harbor", "a").campaign_id is None


@pytest.mark.parametrize("text", [
    "world:harbor#image/Wren", "World:harbor#image/wren", "v1:arch#image/x", "campaign:c1:c2#image/x",
    "campaign:#image/x", "world:harbor#image/", "world:harbor/character/wren", "world:harbor#chat/x",
    " world:harbor#image/x", "world:harbor#image/x/y", "world:harbor#image/_x",
])
def test_image_ref_rejections(text):
    with pytest.raises(ValueError):
        ImageRef.parse(text)


@pytest.mark.parametrize("ref", [
    PlacementRef(WREN, "avatar"), PlacementRef(WREN_A, "expr.default.smile"), PlacementRef(WREN, "gallery_1"),
])
def test_placement_refs_round_trip(ref):
    assert PlacementRef.parse(str(ref)) == ref


@pytest.mark.parametrize("text", [
    "world:harbor/character/wren@r1#placement/avatar",  # pinned subject
    "world:harbor/character/wren#placement/",
    "world:harbor/character/wren#placement/Avatar",
    "world:harbor/character/wren#placement/a/b",
    "campaign:c1#placement/avatar",
    "world:harbor/character/wren",
    "v1:a:b#placement/avatar",
])
def test_placement_ref_rejections(text):
    with pytest.raises(ValueError):
        PlacementRef.parse(text)


def test_placement_constructor_refuses_pinned_subjects_and_long_slots():
    with pytest.raises(ValueError):
        PlacementRef(WREN.at("r1"), "avatar")
    with pytest.raises(ValueError):
        PlacementRef(WREN, "a" * 124)
    assert PlacementRef(WREN, "a" * 123).slot == "a" * 123
    with pytest.raises(ValueError):
        PlacementRef("world:harbor/character/wren", "avatar")  # type: ignore[arg-type]


def test_text_max_applies_to_the_whole_text():
    long_source = ":".join(["s" * 60] * 7)
    with pytest.raises(ValueError):
        ImageRef(long_source, "i" * 100)
    subject = SourceRef(":".join(["s" * 100] * 3), "character", "c" * 100)
    with pytest.raises(ValueError):
        PlacementRef(subject, "x" * 123)
    assert len(str(PlacementRef(subject, "x" * 50))) <= TEXT_MAX


@pytest.mark.parametrize("text", ["world:harbor#image/wren", "world:harbor/character/wren#placement/avatar"])
def test_parse_ref_never_returns_them(text):
    with pytest.raises(ValueError):
        parse_ref(text)


def test_image_ids_fit_the_store_file_name():
    assert ImageRef("catalog:a", "i" * 123).image_id == "i" * 123
    with pytest.raises(ValueError):
        ImageRef("catalog:a", "i" * 124)
