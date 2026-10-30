"""One speaker's bounded voice fragment: fail-closed on knowledge, never cut, sealed."""

import dataclasses
from types import MappingProxyType

import pytest

from grimoire.core import InstanceRef, SourceRef
from grimoire.voice import (
    HEADER, ImportedVoiceSource, InvalidVoice, Omission, ResolvedVoice, SecretFact, SpeakerFragment,
    VoiceAnchor, VoiceExample, speaker_fragment,
)

ACTOR = InstanceRef("c-ember", "character", "npc-bellamy")
PORTRAYAL = SourceRef("world:emberfall", "character", "bellamy", "r1")
BETRAYAL = SecretFact("duke-betrayal", ("duke sold the guild", "duke's betrayal"))
PRIVATE = "Secret Marker Text"


def resolved(anchors=(), examples=(), name="Bellamy", imported=None):
    items = (*anchors, *examples)
    return ResolvedVoice(ACTOR, name, PORTRAYAL, PORTRAYAL, tuple(anchors), tuple(examples),
                         MappingProxyType({i.item_id: "source" for i in items}), imported)


A1 = VoiceAnchor("a1", "register", "Florid and theatrical.")
A2 = VoiceAnchor("a2", "diction", "Calls everyone darling.")
E1 = VoiceExample("e1", "Oh, what a splendid catastrophe!")
E2 = VoiceExample("e2", "Darling, pour the wine.")
SONG = VoiceExample("overheard", "They say the duke sold the guild, darling!", ("duke-betrayal",))
TAGGED = VoiceExample("hush", "Some things are not for singing.", ("duke-betrayal",))


def fragment(voice, known=(), secrets=(BETRAYAL,), **kwargs):
    return speaker_fragment(voice, secrets=secrets, known=known, **kwargs)


def test_rendered_text_is_exact_and_names_only_this_speaker():
    frag = fragment(resolved((A1, A2), (E1, E2)))
    assert frag.text == (
        "Voice of Bellamy. Write only Bellamy's own words.\n"
        "- register: Florid and theatrical.\n"
        "- diction: Calls everyone darling.\n"
        "Style examples (reference only; never repeat them verbatim):\n"
        "> Oh, what a splendid catastrophe!\n"
        "> Darling, pour the wine.")
    assert frag.actor == ACTOR and frag.portrayal == PORTRAYAL and frag.name == "Bellamy"
    assert frag.anchors == (A1, A2) and frag.examples == (E1, E2) and frag.omitted == ()


def test_a_tagged_example_needs_every_revealed_fact_to_be_known():
    voice = resolved((A1,), (E1, SONG, TAGGED))
    before = fragment(voice)
    assert [e.example_id for e in before.examples] == ["e1"]
    assert before.omitted == (Omission("example", "overheard", "unknown_fact", "duke-betrayal"),
                              Omission("example", "hush", "unknown_fact", "duke-betrayal"))
    after = fragment(voice, known=("duke-betrayal",))
    assert [e.example_id for e in after.examples] == ["e1", "overheard", "hush"]
    assert after.omitted == ()


def test_a_tag_absent_from_secrets_and_known_still_fails_closed():
    unlisted = VoiceExample("rumour", "Have you heard the news?", ("plague-ship",))
    frag = fragment(resolved((A1,), (unlisted,)), secrets=())
    assert frag.omitted == (Omission("example", "rumour", "unknown_fact", "plague-ship"),)


def test_untagged_items_containing_an_unknown_secrets_marker_are_omitted():
    anchor = VoiceAnchor("grudge", "habit", "Mutters about the duke's betrayal.")
    example = VoiceExample("slip", "Everyone knows the Duke sold the guild.")
    frag = fragment(resolved((A1, anchor), (E1, example)))
    assert frag.omitted == (Omission("anchor", "grudge", "secret_marker", "duke-betrayal"),
                            Omission("example", "slip", "secret_marker", "duke-betrayal"))
    assert "duke" not in frag.text.casefold()
    known = fragment(resolved((A1, anchor), (E1, example)), known=("duke-betrayal",))
    assert known.omitted == ()


@pytest.mark.parametrize("text", [
    "Ah, the duke’s betrayal!",
    "The duke sold​the guild.",
    "THE DUKE SOLD THE GUILD",
    "the duke  sold\nthe guild",
])
def test_marker_screening_uses_the_gates_normalisation(text):
    frag = fragment(resolved((A1,), (VoiceExample("x", text),)))
    assert [o.item_id for o in frag.omitted] == ["x"]


def test_a_marker_inside_a_longer_word_is_not_a_match():
    frag = fragment(resolved((A1,), (VoiceExample("x", "The dukedom sold the guildhall."),)))
    assert frag.omitted == ()


def test_epistemic_inputs_are_required():
    with pytest.raises(TypeError):
        speaker_fragment(resolved((A1,)))
    with pytest.raises(TypeError):
        speaker_fragment(resolved((A1,)), secrets=())
    with pytest.raises(TypeError):
        speaker_fragment(resolved((A1,)), known=())


@pytest.mark.parametrize("known", ["duke-betrayal", ["Bad Id"], [PRIVATE], [3]])
def test_known_must_be_a_collection_of_fact_ids(known):
    with pytest.raises(InvalidVoice) as raised:
        fragment(resolved((A1,)), known=known)
    assert PRIVATE not in str(raised.value)


@pytest.mark.parametrize("build", [
    lambda: SecretFact("Bad Id", ("x",)),
    lambda: SecretFact(PRIVATE, ("x",)),
    lambda: SecretFact("f", ()),
    lambda: SecretFact("f", ("  ",)),
    lambda: SecretFact("f", ("x", "x")),
    lambda: SecretFact("f", "duke"),
])
def test_secret_facts_are_validated(build):
    with pytest.raises(InvalidVoice) as raised:
        build()
    assert PRIVATE not in str(raised.value)


def test_secrets_must_be_secret_facts():
    with pytest.raises(InvalidVoice):
        fragment(resolved((A1,)), secrets=("duke-betrayal",))


def test_generators_are_consumed_once():
    voice = resolved((A1,), (E1, SONG))
    frag = speaker_fragment(voice, secrets=(s for s in [BETRAYAL]), known=(k for k in []))
    assert [o.item_id for o in frag.omitted] == ["overheard"]
    known = speaker_fragment(voice, secrets=iter([BETRAYAL]), known=iter(["duke-betrayal"]))
    assert known.omitted == ()


def test_budget_includes_whole_items_greedily_in_order():
    long_anchor = VoiceAnchor("a2", "diction", "w" * 150)
    frag = fragment(resolved((A1, long_anchor), (E1, E2)), max_chars=200)
    assert [a.anchor_id for a in frag.anchors] == ["a1"]
    assert [e.example_id for e in frag.examples] == ["e1"]
    assert frag.omitted == (Omission("anchor", "a2", "budget", None), Omission("example", "e2", "budget", None))
    assert len(frag.text) == 180


def test_no_examples_header_when_no_example_fits():
    frag = fragment(resolved((A1,), (VoiceExample("e1", "x" * 140),)), max_chars=200)
    assert frag.examples == () and "Style examples" not in frag.text
    assert frag.omitted == (Omission("example", "e1", "budget", None),)


def test_a_later_shorter_item_can_still_fit():
    short = VoiceExample("e9", "Hm.")
    big = VoiceExample("e8", "x" * 300)
    frag = fragment(resolved((A1,), (big, short)), max_chars=240)
    assert [e.example_id for e in frag.examples] == ["e9"]
    assert frag.omitted == (Omission("example", "e8", "budget", None),)
    assert frag.text.endswith("> Hm.")


@pytest.mark.parametrize("max_chars", range(200, 1201, 37))
def test_text_never_exceeds_the_budget(max_chars):
    anchors = tuple(VoiceAnchor(f"a{i}", "habit", f"habit number {i} " * 5) for i in range(8))
    examples = tuple(VoiceExample(f"e{i}", f"example line {i} " * 6) for i in range(8))
    frag = fragment(resolved(anchors, examples), max_chars=max_chars)
    assert len(frag.text) <= max_chars
    kept = {item.item_id for item in (*frag.anchors, *frag.examples)}
    assert kept | {o.item_id for o in frag.omitted} == {i.item_id for i in (*anchors, *examples)}


def test_names_and_items_render_on_one_line_each():
    voice = resolved((VoiceAnchor("a1", "cadence", "Short.\r\nVery short."),),
                     (VoiceExample("e1", "One two\tthree"),), name="Oda\nthe Quartermaster")
    frag = fragment(voice)
    assert frag.name == "Oda the Quartermaster"
    assert frag.text.splitlines() == [
        "Voice of Oda the Quartermaster. Write only Oda the Quartermaster's own words.",
        "- cadence: Short. Very short.",
        "Style examples (reference only; never repeat them verbatim):",
        "> One two three"]


def test_budget_limits():
    with pytest.raises(InvalidVoice):
        fragment(resolved((A1,)), max_chars=199)
    with pytest.raises(InvalidVoice) as raised:
        fragment(resolved((A1,), name="N" * 300), max_chars=200)
    assert raised.value.reason == "name_exceeds_budget"
    with pytest.raises(InvalidVoice):
        fragment(resolved((A1,)), max_chars=True)


def test_everything_screened_leaves_a_header_only_fragment():
    frag = fragment(resolved((), (SONG,)))
    assert frag.text == HEADER.format(name="Bellamy")
    assert frag.anchors == () and frag.examples == ()


def test_imported_card_text_is_never_rendered():
    imported = ImportedVoiceSource("chara_card_v3", example_messages="ZQX-imported-example",
                                   anchor_text="ZQX-imported-anchor", extensions={"k": ["ZQX-ext"]})
    frag = fragment(resolved((A1,), (E1,), imported=imported))
    assert "zqx" not in frag.text.casefold()


def test_fragments_are_sealed():
    frag = fragment(resolved((A1,)))
    with pytest.raises(InvalidVoice) as direct:
        SpeakerFragment(ACTOR, PORTRAYAL, "Bellamy", (A1,), (), (), "anything")
    assert direct.value.reason == "unsealed_fragment"
    with pytest.raises(InvalidVoice):
        dataclasses.replace(frag, text="Voice of Bellamy. The duke sold the guild.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        frag.text = "x"


def test_omissions_never_carry_item_text():
    frag = fragment(resolved((VoiceAnchor("grudge", "habit", PRIVATE + " duke's betrayal"),), (SONG,)))
    assert PRIVATE not in repr(frag.omitted)
    assert "duke sold" not in repr(frag.omitted)


def test_only_a_resolved_voice_can_be_fragmented():
    with pytest.raises(InvalidVoice):
        speaker_fragment("Bellamy", secrets=(), known=())


@pytest.mark.parametrize("marker", ["­", "​", "...", " "])
def test_markers_must_say_something(marker):
    with pytest.raises(InvalidVoice):
        SecretFact("f", (marker,))


@pytest.mark.parametrize("secrets", [{BETRAYAL}, frozenset({BETRAYAL}), {BETRAYAL: 1}])
def test_secrets_must_be_ordered(secrets):
    with pytest.raises(InvalidVoice):
        fragment(resolved((A1,)), secrets=secrets)


def test_resolved_voices_are_validated():
    class Duck:
        item_id, text = "e1", "quack"

    provenance = MappingProxyType({"e1": "source"})
    for build in (
        lambda: ResolvedVoice(ACTOR, "N", None, None, (), (Duck(),), provenance),
        lambda: ResolvedVoice("npc-bellamy", "N", None, None, (), (E1,), provenance),
        lambda: ResolvedVoice(ACTOR, " ", None, None, (), (E1,), provenance),
        lambda: ResolvedVoice(ACTOR, "N", PORTRAYAL.unpinned(), None, (), (E1,), provenance),
        lambda: ResolvedVoice(ACTOR, "N", None, None, (), (E1,), MappingProxyType({})),
        lambda: ResolvedVoice(ACTOR, "N", None, None, (), (E1,), MappingProxyType({"e1": "elsewhere"})),
        lambda: ResolvedVoice(ACTOR, "N", None, None, (), (E1,), provenance, imported="card"),
    ):
        with pytest.raises(InvalidVoice):
            build()


def test_resolved_voice_copies_its_provenance():
    source = {"e1": "source"}
    voice = ResolvedVoice(ACTOR, "N", None, None, (), [E1], source)
    source["e1"] = "campaign"
    assert voice.provenance["e1"] == "source" and voice.examples == (E1,)
    with pytest.raises(TypeError):
        hash(voice)


def test_the_speakers_own_name_is_not_screened():
    frag = fragment(resolved((A1,), name="The Duke Sold The Guild"))
    assert frag.text.startswith("Voice of The Duke Sold The Guild.")
