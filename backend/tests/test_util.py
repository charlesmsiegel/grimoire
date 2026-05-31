"""Unit tests for grimoire.util helpers."""

from __future__ import annotations

import pytest

from grimoire.util import canonicalize_character_ref


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
