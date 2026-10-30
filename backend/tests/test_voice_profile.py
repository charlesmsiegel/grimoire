"""Portrayal-scoped voice values (sprint 014): authored evidence, not campaign state."""

import pickle
from dataclasses import fields

import pytest

from grimoire.core import LegacyId, SourceRef
from grimoire.voice import (
    ImportedVoiceSource,
    InvalidVoice,
    MissingVoice,
    VoiceAnchor,
    VoiceCatalog,
    VoiceExample,
    VoiceMismatch,
    VoiceProfile,
)

ODA = SourceRef("world:emberfall", "character", "oda", "r1")
PRIVATE = "Secret Marker Text"


def anchor(anchor_id="a1", aspect="cadence", text="Clipped. Never more than six words."):
    return VoiceAnchor(anchor_id, aspect, text)


def example(example_id="e1", text="Ledger's closed. Come back at dawn.", reveals=()):
    return VoiceExample(example_id, text, reveals)


def profile(**overrides):
    values = {"portrayal": ODA, "anchors": (anchor(),), "examples": (example(),)}
    values.update(overrides)
    return VoiceProfile(**values)


def test_a_profile_is_authored_voice_for_one_pinned_portrayal():
    voice = profile()
    assert voice.portrayal == ODA
    assert [item.anchor_id if isinstance(item, VoiceAnchor) else item.example_id
            for item in voice.items()] == ["a1", "e1"]


def test_a_profile_has_no_campaign_or_epistemic_fields():
    assert {f.name for f in fields(VoiceProfile)} == {"portrayal", "anchors", "examples", "imported"}


def test_sequences_are_frozen_to_tuples():
    voice = profile(anchors=[anchor()], examples=[example(reveals=["duke-betrayal"])])
    assert isinstance(voice.anchors, tuple) and isinstance(voice.examples, tuple)
    assert voice.examples[0].reveals == ("duke-betrayal",)


@pytest.mark.parametrize("build", [
    lambda: anchor(text="   "),
    lambda: anchor(text=None),
    lambda: anchor(text="x" * 401),
    lambda: example(text="x" * 601),
    lambda: anchor(aspect="timbre"),
    lambda: anchor(anchor_id="Bad Id"),
    lambda: example(example_id=""),
    lambda: example(reveals=("duke-betrayal", "duke-betrayal")),
    lambda: example(reveals=("Not A Fact",)),
    lambda: profile(anchors=(), examples=()),
    lambda: profile(anchors=tuple(anchor(f"a{i}") for i in range(33))),
    lambda: profile(examples=tuple(example(f"e{i}") for i in range(33))),
    lambda: profile(anchors=(anchor("same"),), examples=(example("same"),)),
    lambda: profile(anchors=(anchor("a1"), anchor("a1"))),
    lambda: profile(portrayal=ODA.unpinned()),
    lambda: profile(portrayal=SourceRef("world:emberfall", "item", "lantern", "r1")),
    lambda: profile(portrayal="world:emberfall/character/oda@r1"),
    lambda: profile(anchors=("not an anchor",)),
])
def test_invalid_voice_values_are_refused(build):
    with pytest.raises(InvalidVoice):
        build()


def test_limits_are_inclusive():
    profile(anchors=tuple(anchor(f"a{i}", text="x" * 400) for i in range(32)),
            examples=tuple(example(f"e{i}", text="y" * 600) for i in range(32)))


def test_catalog_is_keyed_by_exact_revision_and_refuses_duplicates():
    catalog = VoiceCatalog([profile()])
    assert catalog.get(ODA) == profile()
    assert catalog.get(ODA.unpinned()) is None
    assert catalog.get(ODA.at("r2")) is None
    assert list(catalog) == [profile()]
    with pytest.raises(InvalidVoice):
        VoiceCatalog([profile(), profile()])
    with pytest.raises(InvalidVoice):
        VoiceCatalog(["not a profile"])


def test_imported_card_fields_are_kept_exactly():
    raw = "<START>\r\n{{user}}: hi\r\n{{char}}: Counts first.  "
    imported = ImportedVoiceSource(
        "chara_card_v3", card_version=LegacyId("arch1", "worlds/w/characters/oda/v2.json"),
        example_messages=raw, anchor_text="Clipped.\n", extensions={})
    assert imported.example_messages == raw
    assert imported.anchor_text == "Clipped.\n"
    assert profile(imported=imported).imported is imported


def test_extensions_round_trip_losslessly_in_order():
    data = {"b": [1, {"z": None, "y": True}], "a": 2.5, "c": "text", "d": []}
    imported = ImportedVoiceSource("chara_card_v3", extensions=data)
    assert imported.thaw() == data
    assert list(imported.thaw()) == ["b", "a", "c", "d"]
    assert list(imported.thaw()["b"][1]) == ["z", "y"]
    with pytest.raises(TypeError):
        imported.extensions["b"] = 1
    assert isinstance(imported.extensions["b"], tuple)


def test_tuple_extension_values_thaw_to_lists():
    assert ImportedVoiceSource("chara_card_v3", extensions={"k": (1, 2)}).thaw() == {"k": [1, 2]}


def test_imported_values_are_independent_of_the_caller_dict():
    data = {"k": [1]}
    imported = ImportedVoiceSource("chara_card_v3", extensions=data)
    data["k"].append(2)
    data["new"] = 1
    assert imported.thaw() == {"k": [1]}


@pytest.mark.parametrize("extensions, path", [
    ({"ok": 1, PRIVATE: float("nan")}, "extensions[1]"),
    ({"ok": [0, float("inf")]}, "extensions[0][1]"),
    ({"ok": {3: "x"}}, "extensions[0][0]"),
    ({"ok": [{PRIVATE: {1, 2}}]}, "extensions[0][0][0]"),
    ({PRIVATE: object()}, "extensions[0]"),
])
def test_bad_extensions_are_located_by_index_path_without_key_text(extensions, path):
    with pytest.raises(InvalidVoice) as raised:
        ImportedVoiceSource("chara_card_v3", extensions=extensions)
    assert path in str(raised.value)
    assert PRIVATE not in str(raised.value) and PRIVATE not in repr(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("build", [
    lambda: ImportedVoiceSource("chara_card_v2"),
    lambda: ImportedVoiceSource("chara_card_v3", card_version="v1:a:p"),
    lambda: ImportedVoiceSource("chara_card_v3", example_messages=None),
    lambda: ImportedVoiceSource("chara_card_v3", anchor_text=3),
    lambda: ImportedVoiceSource("chara_card_v3", extensions=[]),
])
def test_imported_source_shape_is_validated(build):
    with pytest.raises(InvalidVoice):
        build()


@pytest.mark.parametrize("build", [
    lambda: anchor(anchor_id=PRIVATE),
    lambda: example(example_id=PRIVATE),
    lambda: example(reveals=(PRIVATE,)),
])
def test_bad_ids_do_not_echo_their_text(build):
    with pytest.raises(InvalidVoice) as raised:
        build()
    assert PRIVATE not in str(raised.value)
    assert raised.value.__cause__ is None


def test_errors_carry_reasons_and_pickle():
    for error in (InvalidVoice("bad_id", "m"), VoiceMismatch("other_actor", "m"),
                  MissingVoice("missing_definition", "m", detail="missing_entity")):
        clone = pickle.loads(pickle.dumps(error))
        assert type(clone) is type(error) and clone.reason == error.reason
    assert pickle.loads(pickle.dumps(MissingVoice("x", "m", detail="d"))).detail == "d"
    assert issubclass(InvalidVoice, ValueError) and issubclass(VoiceMismatch, ValueError)
    assert issubclass(MissingVoice, LookupError)
