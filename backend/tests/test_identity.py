"""Reference text forms: strict, unambiguous and exactly round-tripping."""

import unicodedata

import pytest

from grimoire.core.identity import (
    IdentityConflict, InstanceRef, LegacyId, SourceRef, parse_ref,
)

SOURCE_REFS = [
    "world:emberfall/character/vessa",
    "world:emberfall/character/vessa@r2",
    "catalog:lanternworks.pack:x/item/lamp-1@v1.0_a",
    "solo/location/0",
]


@pytest.mark.parametrize("text", SOURCE_REFS)
def test_source_ref_round_trip(text):
    ref = SourceRef.parse(text)
    assert str(ref) == text
    assert SourceRef.parse(str(ref)) == ref
    assert parse_ref(text) == ref


def test_source_ref_fields():
    ref = SourceRef.parse("world:emberfall/character/vessa@r2")
    assert ref == SourceRef("world:emberfall", "character", "vessa", "r2")


def test_instance_ref_round_trip():
    text = "campaign:cmp-1/character/vessa-a"
    ref = InstanceRef.parse(text)
    assert ref == InstanceRef("cmp-1", "character", "vessa-a")
    assert str(ref) == text
    assert parse_ref(text) == ref


NFC_PATH = unicodedata.normalize("NFC", "worlds/café/characters/vessa.md")
NFD_PATH = unicodedata.normalize("NFD", NFC_PATH)
LEGACY_TEXTS = [
    "v1:arch-1:worlds/emberfall/characters/vessa/character.md",
    "v1:arch-1:worlds/emberfall/notes:2024/Vessa Old.md",
    "v1:arch-1:" + NFC_PATH,
    "v1:arch-1:" + NFD_PATH,
]


@pytest.mark.parametrize("text", LEGACY_TEXTS)
def test_legacy_round_trip(text):
    legacy = LegacyId.parse(text)
    assert str(legacy) == text
    assert parse_ref(text) == legacy


def test_legacy_path_kept_exactly():
    assert LegacyId.parse(LEGACY_TEXTS[1]).path == "worlds/emberfall/notes:2024/Vessa Old.md"
    assert LegacyId("arch-1", NFC_PATH) != LegacyId("arch-1", NFD_PATH)


def test_parse_ref_dispatch():
    assert isinstance(parse_ref("world:a/character/x"), SourceRef)
    assert isinstance(parse_ref("campaign:c/character/x"), InstanceRef)
    assert isinstance(parse_ref("v1:arch:a.md"), LegacyId)


MALFORMED = [
    "", " world:a/character/x", "world:a/character/x ", "world:a/character/x\n",
    "World:a/character/x", "world:a/character/x/", "world:a//x",
    "world:a/character/x@", "world:a/character/x@r1@r2", "world::a/character/x",
    "wörld/character/x", "v1/character/x", "campaign/character/x",
    "campaign:c/character/x@r1", "campaign:/character/x", "campaign:a:b/character/x",
    "world:a@x/character/y", "world:a/char:x/y", "world:a/character/x:y",
    "world:a/b/c/d", "world:a/character/x@r/1", "-a/character/x",
    "a" * 129 + "/character/x", ":".join(["s" * 100] * 5) + "/character/x",
    "v1:arch", "v1:arch:", "v1:arch:/abs", "v1:arch:a/../b", "v1:arch:./a",
    "v1:arch:a//b", "v1:arch:a\\b", "v1:arch:a/", "v1:arch:a\x00b",
    "v1:arch:a\nb", "v1:arch:a\x7fb", "v1:arch:a\ud800b", "v1:arch:a\u2028b",
    "v1:arch:a\u200bb", "v1:Arch:a", "v1::a",
    None, 5,
]


@pytest.mark.parametrize("text", MALFORMED)
def test_malformed_rejected(text):
    with pytest.raises(ValueError):
        parse_ref(text)


@pytest.mark.parametrize("args", [
    ("World:A", "character", "x"),
    ("v1:x", "character", "y"),
    ("campaign:x", "character", "y"),
    ("world:a", "character", "x", ""),
    ("world:a", "Character", "x"),
    ("world:a", "character", "x y"),
    (":".join(["s" * 120] * 5), "character", "x"),
    (None, "character", "x"),
])
def test_source_ref_constructor_enforces_grammar(args):
    with pytest.raises(ValueError):
        SourceRef(*args)


@pytest.mark.parametrize("args", [
    ("c", "Character", "x"), ("c:d", "character", "x"), ("c", "character", ""),
])
def test_instance_ref_constructor_enforces_grammar(args):
    with pytest.raises(ValueError):
        InstanceRef(*args)


def test_pin_helpers():
    ref = SourceRef("world:emberfall", "character", "vessa")
    assert not ref.pinned
    pinned = ref.at("r3")
    assert pinned.pinned and pinned.revision == "r3"
    assert pinned.unpinned() == ref
    assert pinned.entity_key == ref.entity_key == ("world:emberfall", "character", "vessa")


def test_identity_conflict_is_value_error_with_sources():
    error = IdentityConflict("clash", ("world:b", "world:a"))
    assert isinstance(error, ValueError)
    assert error.sources == ("world:a", "world:b")


def test_reference_text_length_boundary():
    kind_and_id = "/character/x"
    source = ":".join(["s" * 100] * 4) + ":" + "t" * (512 - len(kind_and_id) - 404)
    assert len(str(SourceRef(source, "character", "x"))) == 512
    with pytest.raises(ValueError):
        SourceRef(source + "t", "character", "x")
