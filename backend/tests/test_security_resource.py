"""Logical resource addresses: spelling is identity, and nothing escapes."""

import pytest

from grimoire.security import InvalidResourcePath, ResourcePath


@pytest.mark.parametrize("text", [
    "worlds/harbor/characters/wren",
    "w\u00f6rld/x",
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
    assert ResourcePath.parse("chores.json").reserved
    assert ResourcePath.parse("llm_connections/default").reserved
    assert not ResourcePath.parse("worlds/w/regex.json").reserved
    assert not ResourcePath.parse("worlds/secrets").reserved


@pytest.mark.parametrize("text", [
    "worlds/harbor/\u034f",          # combining grapheme joiner alone
    "worlds/harbor\ufe0f",           # variation selector: renders like worlds/harbor
    "worlds/\u115f", "worlds/\u1160", "worlds/\u3164",  # Hangul fillers
    "worlds/\u0301", "worlds/\u20dd",  # segments that start with a combining mark
    "worlds/a\U000e0101",
])
def test_invisible_or_mark_led_segments_rejected(text):
    with pytest.raises(InvalidResourcePath):
        ResourcePath.parse(text)


def test_combining_marks_inside_a_segment_are_allowed():
    assert str(ResourcePath.parse("worlds/\u0915\u093f")) == "worlds/\u0915\u093f"


def test_string_subclasses_are_rejected():
    class Segment(str):
        def __iter__(self):
            return iter("a")

        def __ne__(self, other):
            return False

        def startswith(self, *args):
            return False

        endswith = startswith

        def split(self, *args, **kwargs):
            return ["x"]

    class Address(str):
        def split(self, *args, **kwargs):
            return ["worlds", "harbor", Segment(".."), Segment("secrets")]

    with pytest.raises(InvalidResourcePath):
        ResourcePath.parse(Address("x"))
    with pytest.raises(InvalidResourcePath):
        ResourcePath(("worlds", Segment("..")))
