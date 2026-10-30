"""Intent envelope invariants and input validation."""

import pytest

from grimoire.intent.commands import Command
from grimoire.intent.model import REASONS, Intent, RoutingContext, Utterance, signature


@pytest.mark.parametrize("kwargs", [
    {"text": "x", "mode": "shout"},
    {"text": "x", "origin": "psychic"},
    {"text": "x", "confidence": 0.9},
    {"text": "x", "alternatives": ("y",)},
    {"text": "x", "origin": "voice", "confidence": float("nan")},
    {"text": "x", "origin": "voice", "confidence": float("inf")},
    {"text": "x", "origin": "voice", "confidence": 1.5},
    {"text": "x", "origin": "voice", "confidence": -0.1},
    {"text": "x", "origin": "voice", "confidence": True},
    {"text": "x", "origin": "voice", "alternatives": ["y"]},
    {"text": "x", "origin": "voice", "alternatives": (None,)},
    {"text": None},
])
def test_utterance_validation(kwargs):
    with pytest.raises(ValueError):
        Utterance(**kwargs)


def test_valid_voice_utterance():
    utterance = Utterance("stop", origin="voice", confidence=0.4, alternatives=("shop",))
    assert utterance.mode == "auto" and utterance.confidence == 0.4


def test_routing_context_requires_authority():
    with pytest.raises(TypeError):
        RoutingContext()
    for kwargs in ({"min_confidence": 0}, {"min_confidence": float("nan")},
                   {"min_confidence": 1.1}):
        with pytest.raises(ValueError):
            RoutingContext("pc-1", True, **kwargs)
    with pytest.raises(ValueError):
        RoutingContext("", True)
    with pytest.raises(ValueError):
        RoutingContext("pc-1", "yes")
    assert RoutingContext(None, False).min_confidence == 0.75


def _speech(text="hi"):
    return Intent("pc_speech", text, "default_speech", "f", actor_id="pc-1")


@pytest.mark.parametrize("build", [
    lambda: Intent("ui_command", "x", "bare_command", "f"),
    lambda: Intent("pc_speech", "x", "default_speech", "f", command=Command("pause"), actor_id="a"),
    lambda: Intent("pc_action", "x", "first_person", "f"),
    lambda: Intent("director_instruction", "x", "director_marker", "f", actor_id="a"),
    lambda: Intent("pc_speech", "x", "default_speech", "f", actor_id="a", candidates=(_speech(),)),
    lambda: Intent("uncertain", "x", "command_like", "f",
                   candidates=(Intent("uncertain", "x", "command_like", "f"),)),
    lambda: Intent("uncertain", "x", "command_like", "f",
                   candidates=(Intent("reject", "x", "empty", "f"),)),
    lambda: Intent("uncertain", "x", "command_like", "f", candidates=(
        Intent("ui_command", "delete the scene", "bare_command", "f",
               command=Command("delete_scene")),)),
    lambda: Intent("pc_speech", "x", "because", "f", actor_id="a"),
    lambda: Intent("shout", "x", "default_speech", "f"),
    lambda: Intent("uncertain", "x", "command_like", "f", candidates=["not a tuple"]),
])
def test_intent_invariants(build):
    with pytest.raises(ValueError):
        build()


def test_reason_codes_are_the_closed_set():
    assert REASONS == {
        "empty", "explicit_mode", "lane_conflict", "unknown_command", "quoted", "asterisks",
        "mixed_utterance", "wake_word", "bare_command", "director_marker",
        "marker_no_separator", "first_person", "polite_command", "command_like",
        "default_speech", "no_active_pc", "not_director", "asr_disagreement", "low_confidence",
    }


def test_signature_ignores_case_and_punctuation():
    def action(text):
        return Intent("pc_action", text, "first_person", "f", actor_id="pc-1")

    def character(name):
        return Intent("ui_command", "x", "bare_command", "f", command=Command("open_character", name))

    assert signature(action("I open the chest.")) == signature(action("i open the chest"))
    assert signature(action("I kill the king")) != signature(action("I kiss the king"))
    assert signature(character("Mara")) == signature(character("mara"))
    assert signature(character("Mara")) != signature(character("Vell"))
