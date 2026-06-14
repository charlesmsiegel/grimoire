"""Unit tests for grimoire.util helpers."""

from __future__ import annotations

import pytest

from grimoire.util import (
    canonicalize_character_ref,
    deserialize_vector,
    extract_json_object,
    serialize_vector,
)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        # Emergent (campaign-local) — every spelling collapses to the canonical form.
        ("campaign:emergent/character/ghost", "campaign:emergent/character/ghost"),
        ("emergent/character/ghost", "campaign:emergent/character/ghost"),
        ("emergent/ghost", "campaign:emergent/character/ghost"),
        ("campaign:emergent/ghost", "campaign:emergent/character/ghost"),
        # Library — full, scheme-less, singular ``character``, and bare ``<w>/<id>``.
        ("library:worlds/harbor/characters/reyes", "library:worlds/harbor/characters/reyes"),
        ("library:worlds/harbor/character/reyes", "library:worlds/harbor/characters/reyes"),
        ("worlds/harbor/characters/reyes", "library:worlds/harbor/characters/reyes"),
        ("harbor/reyes", "library:worlds/harbor/characters/reyes"),
        # Over-qualified: a world-stored PC ref double-prefixed with its world
        # id collapses to the canonical form (was shown raw in the HUD cast).
        ("harbor/worlds/harbor/characters/reyes", "library:worlds/harbor/characters/reyes"),
        (
            "library:harbor/worlds/harbor/characters/reyes",
            "library:worlds/harbor/characters/reyes",
        ),
        # Unrecognized / degenerate inputs pass through untouched.
        ("just-an-id", "just-an-id"),
        ("", ""),
    ],
)
def test_canonicalize_character_ref(ref: str, expected: str) -> None:
    assert canonicalize_character_ref(ref) == expected


def test_canonicalize_character_ref_is_idempotent() -> None:
    for ref in (
        "emergent/ghost",
        "campaign:emergent/character/ghost",
        "harbor/reyes",
        "library:worlds/harbor/characters/reyes",
        "harbor/worlds/harbor/characters/reyes",
    ):
        once = canonicalize_character_ref(ref)
        assert canonicalize_character_ref(once) == once


def test_extract_json_object_handles_fenced_block() -> None:
    text = 'Here you go:\n```json\n{"facts": []}\n```'
    assert extract_json_object(text) == {"facts": []}


def test_extract_json_object_handles_bare_object() -> None:
    text = '{"facts": [{"text": "Hi", "confidence": 0.9}]}'
    payload = extract_json_object(text)
    assert payload is not None and payload["facts"][0]["text"] == "Hi"


def test_extract_json_object_returns_none_for_garbage() -> None:
    assert extract_json_object("totally not json") is None


def test_vector_roundtrip_is_little_endian() -> None:
    vec = [1.5, -2.0, 0.0, 3.25]
    blob = serialize_vector(vec)
    # Little-endian f32, four bytes per element, independent of host byte order.
    assert len(blob) == len(vec) * 4
    assert deserialize_vector(blob) == pytest.approx(vec)
