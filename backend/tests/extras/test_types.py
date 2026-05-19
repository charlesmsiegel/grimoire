"""Validation tests for ``types/extras.py``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.types.characters import Character, CharacterRole
from grimoire.types.extras import (
    ExtraScope,
    ExtraValue,
    ExtrasCapError,
    ExtrasKeyError,
    validate_extras_dict,
    validate_extras_key,
    validate_extras_value,
)


def _make_extra(value):
    return ExtraValue(
        value=value,
        set_at=datetime.now(UTC),
        set_by="user",
        scope=ExtraScope.LIBRARY,
    )


def test_validate_key_accepts_snake_case():
    validate_extras_key("favorite_drink")
    validate_extras_key("scars")
    validate_extras_key("a")
    validate_extras_key("voice_register_2")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "_internal_secret",
        "mechanics_hp",
        "system_id",
        "CamelCase",
        "kebab-case",
        "with space",
        "a" * 41,
    ],
)
def test_validate_key_rejects_bad(bad: str):
    with pytest.raises(ExtrasKeyError):
        validate_extras_key(bad)


def test_validate_value_scalars_lists_dicts():
    validate_extras_value("a string")
    validate_extras_value(42)
    validate_extras_value(3.14)
    validate_extras_value(True)
    validate_extras_value(None)
    validate_extras_value(["one", "two"])
    validate_extras_value({"hue": "amber", "temp_c": 18})


def test_validate_value_rejects_nested_dict():
    with pytest.raises(TypeError):
        validate_extras_value({"outer": {"inner": "x"}})


def test_validate_value_rejects_oversize_string():
    with pytest.raises(ExtrasCapError):
        validate_extras_value("x" * 1001)


def test_default_extras_is_empty_dict():
    c = Character(id="x", name="X", role=CharacterRole.MAJOR_NPC)
    assert c.extras == {}


def test_extras_roundtrip_through_model_dump():
    extra = _make_extra("Glenfarclas 25")
    c = Character(
        id="x",
        name="X",
        role=CharacterRole.MAJOR_NPC,
        extras={"favorite_drink": extra},
    )
    dumped = c.model_dump(mode="json")
    restored = Character.model_validate(dumped)
    assert restored.extras["favorite_drink"].value == "Glenfarclas 25"


def test_reserved_prefix_rejected_on_character_construction():
    extra = _make_extra("x")
    with pytest.raises(Exception):
        Character(
            id="x",
            name="X",
            role=CharacterRole.MAJOR_NPC,
            extras={"_internal_secret": extra},
        )


def test_validate_extras_dict_coerces_none_to_empty():
    assert validate_extras_dict(None) == {}


def test_validate_extras_dict_rejects_non_dict():
    with pytest.raises(TypeError):
        validate_extras_dict(["not a dict"])
