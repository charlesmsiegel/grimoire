"""Logical resource addresses: spelling is identity, and nothing escapes."""

import pytest

from grimoire.security import InvalidResourcePath, ResourcePath


@pytest.mark.parametrize("text", [
    "worlds/harbor/characters/wren",
    "wörld/x",
    "a.b/c-d_e",
    "worlds/harbor/characters/m\u0430ra",  # Cyrillic a: valid, flagged in descriptions
])
def test_valid_addresses_round_trip(text):
    assert str(ResourcePath.parse(text)) == text


@pytest.mark.parametrize("text", [
    "", "/worlds", "worlds/", "worlds//x", ".", "worlds/..",
    "worlds/harbor/../../secrets/provider-key",
    "worlds\\x", "c:/x", "worlds/a:b",
    "wor\x00ld", "wor\u200bld", "wor\u202eld", "wor\u2028ld", "wor\ud800ld",
    "\ue000", "\U000e0080",
    " worlds", "worlds ", "worlds.", ".cache/x", "~/x",
    "Worlds/rival", "\uff0e\uff0e/x", "worlds\uff0fx", "\uff53\uff45\uff43\uff52\uff45\uff54\uff53/x",
    "con", "worlds/nul.txt",
    "a" * 1025, "/".join(["a"] * 33), "a" * 129, "\u0905" * 86,
    "/".join(["a" * 100] * 11),
    123, None,
])
def test_invalid_addresses_rejected(text):
    with pytest.raises(InvalidResourcePath) as caught:
        ResourcePath.parse(text)
    if isinstance(text, str) and len(text) >= 4:  # shorter inputs collide with fixed wording
        assert text not in str(caught.value)


def test_direct_construction_is_validated():
    with pytest.raises(InvalidResourcePath):
        ResourcePath(("worlds", ".."))
    with pytest.raises(InvalidResourcePath):
        ResourcePath(())
    with pytest.raises(InvalidResourcePath):
        ResourcePath(["worlds"])  # type: ignore[arg-type]


def test_is_within_is_segmentwise():
    harbor = ResourcePath.parse("worlds/harbor")
    assert ResourcePath.parse("worlds/harbor/x").is_within(harbor)
    assert ResourcePath.parse("worlds/harbor/x/y/z").is_within(ResourcePath.parse("worlds"))
    assert harbor.is_within(harbor)
    assert not ResourcePath.parse("worlds/harbor2").is_within(harbor)
    assert not ResourcePath.parse("worlds").is_within(harbor)


def test_reserved_roots():
    assert ResourcePath.parse("secrets").reserved
    assert ResourcePath.parse("secrets/provider-key").reserved
    assert not ResourcePath.parse("worlds/secrets").reserved
