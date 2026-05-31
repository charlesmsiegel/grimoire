"""``_parse_character_ref`` must resolve over-qualified world refs.

Regression: a world-stored PC could be registered with a double-prefixed ref
(``<world>/worlds/<world>/characters/<id>``). The resolver only matched
``worlds/...`` at the start of the path, so it raised and the PC's card was
silently dropped from the assembled context.
"""

from __future__ import annotations

import pytest

from grimoire.characters.service import _parse_character_ref


@pytest.mark.parametrize(
    ("ref", "world_id", "asset_id"),
    [
        ("library:worlds/sakura-high/characters/haruto-takeda", "sakura-high", "haruto-takeda"),
        ("worlds/sakura-high/characters/haruto-takeda", "sakura-high", "haruto-takeda"),
        # Over-qualified (double world prefix) — previously raised.
        ("sakura-high/worlds/sakura-high/characters/haruto-takeda", "sakura-high", "haruto-takeda"),
    ],
)
def test_parse_world_ref(ref: str, world_id: str, asset_id: str) -> None:
    view = _parse_character_ref(ref)
    assert view.is_emergent is False
    assert view.world_id == world_id
    assert view.asset_id == asset_id


def test_parse_emergent_ref() -> None:
    view = _parse_character_ref("emergent/shia")
    assert view.is_emergent is True
    assert view.asset_id == "shia"
