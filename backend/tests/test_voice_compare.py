"""Copy detection and flattened-voice warnings, sharing the gate's measures."""

from types import MappingProxyType

import pytest

import grimoire.evals.checks
from grimoire.core import InstanceRef
from grimoire.voice import (
    InvalidVoice, ResolvedVoice, VoiceAnchor, VoiceExample, example_copies, flattened, voice_overlap,
)
from grimoire.voice import compare


def voice(actor_id, examples=(), anchors=()):
    examples = tuple(VoiceExample(f"e{i}", text) for i, text in enumerate(examples))
    anchors = tuple(VoiceAnchor(f"a{i}", "habit", text) for i, text in enumerate(anchors))
    return ResolvedVoice(InstanceRef("c-ember", "character", actor_id), actor_id, None, None, anchors,
                         examples, MappingProxyType({i.item_id: "source" for i in (*anchors, *examples)}))


ODA = voice("npc-oda", ["Ledger's closed. Come back at dawn.", "Counts first. Talk later."])
BEL = voice("npc-bellamy", ["Darling, what a splendid catastrophe tonight!"])


def test_copy_threshold_is_the_gates():
    assert compare.SAMPLE_COPY_CHARS is grimoire.evals.checks.SAMPLE_COPY_CHARS == 24


def test_copies_of_any_scene_voice_are_reported():
    line = "Oda grins: darling, what a splendid catastrophe tonight, eh?"
    assert example_copies(line, [ODA, BEL]) == ((BEL.actor, "e0"),)
    assert example_copies("Counts first, then we talk.", [ODA, BEL]) == ()


def test_copy_length_boundary():
    sample = "abcdefghijklmnopqrstuvwxyz"
    target = voice("npc-x", [sample])
    assert example_copies("zz " + sample[:24] + " zz", [target]) == ((target.actor, "e0"),)
    assert example_copies("zz " + sample[:23] + " zz", [target]) == ()


def test_copy_results_are_sorted():
    a = voice("npc-b", ["one two three four five six seven", "eight nine ten eleven twelve thirteen"])
    b = voice("npc-a", ["one two three four five six seven"])
    line = "one two three four five six seven; eight nine ten eleven twelve thirteen"
    assert example_copies(line, [a, b]) == ((b.actor, "e0"), (a.actor, "e0"), (a.actor, "e1"))


def test_overlap_and_flattening():
    assert voice_overlap(ODA, ODA) == pytest.approx(1.0)
    assert voice_overlap(voice("x", ["abc"]), voice("y", ["xyz"])) == 0.0
    assert 0.0 <= voice_overlap(ODA, BEL) < 0.85
    flat = [voice(f"npc-{n}", ["Well, I suppose that is how it goes."], ["Neutral and polite."])
            for n in ("d", "a", "c", "b")]
    pairs = flattened(flat)
    assert len(pairs) == 6
    assert pairs == tuple(sorted(pairs)) and all(left < right for left, right in pairs)
    assert pairs[0] == ("campaign:c-ember/character/npc-a", "campaign:c-ember/character/npc-b")
    assert flattened([ODA, BEL]) == ()


@pytest.mark.parametrize("threshold", [-0.1, 1.5, float("nan"), True, "0.5"])
def test_threshold_must_be_a_fraction(threshold):
    with pytest.raises(InvalidVoice):
        flattened([ODA, BEL], threshold)
