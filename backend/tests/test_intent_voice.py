"""Speech-recognition uncertainty never yields a destructive or fiction-entering intent."""

import pytest

from grimoire.intent.commands import Command
from grimoire.intent.model import RoutingContext, Utterance
from grimoire.intent.router import route
from intent_phrases import CHARACTER_CASES, CHOOSE_CASES, NAV_CASES, SIMPLE_CASES

CTX = RoutingContext("pc-1", True)
MODE = {"ui_command": "command", "pc_speech": "speech", "pc_action": "action",
        "director_instruction": "director"}


def v(text, confidence, *alternatives, context=CTX, mode="auto"):
    return route(Utterance(text, mode=mode, origin="voice", confidence=confidence,
                           alternatives=alternatives), context)


def r(text, mode="auto"):
    return route(Utterance(text, mode=mode), CTX)


def test_voice_confident_matches_keyboard():
    assert v("Open the inventory", 0.9) == r("Open the inventory")
    assert v("I open the chest", 0.9, "I open the chest") == r("I open the chest")


@pytest.mark.parametrize("phrase", [case[0] for case in
                                    NAV_CASES + SIMPLE_CASES + CHOOSE_CASES + CHARACTER_CASES])
def test_gate_parity_for_catalog(phrase):
    spoken, typed = v(phrase, 0.95), r(phrase)
    assert spoken.command is not None and spoken.command == typed.command
    assert spoken.command.gate == typed.command.gate


def test_low_confidence():
    reroll = v("reroll", 0.4)
    assert (reroll.kind, reroll.reason) == ("uncertain", "low_confidence")
    assert [c.command for c in reroll.candidates] == [Command("reroll")]
    delete = v("delete the scene", 0.5)
    assert (delete.kind, delete.command, delete.candidates) == ("uncertain", None, ())
    unknown = v("I open the chest", None)
    assert (unknown.kind, unknown.reason) == ("uncertain", "low_confidence")
    assert [c.kind for c in unknown.candidates] == ["pc_action"]
    assert v("I open the chest", 0.75).kind == "pc_action"  # threshold is inclusive
    strict = RoutingContext("pc-1", True, min_confidence=0.95)
    assert v("I open the chest", 0.9, context=strict).reason == "low_confidence"
    assert v("Hello", 0.4, mode="speech").reason == "low_confidence"


@pytest.mark.parametrize("text, confidence, alternatives", [
    ("stop", 0.4, ()), ("stop", 0.9, ("shop",)), ("pause", 0.1, ()), ("cancel", None, ()),
    ("Grimoire, stop", 0.2, ("grim war stop",)),
])
def test_safe_stop_bypass(text, confidence, alternatives):
    intent = v(text, confidence, *alternatives)
    assert intent.kind == "ui_command" and intent.command.name in {"stop", "pause", "cancel"}


def test_asr_disagreement():
    delete = v("delete the scene", 0.95, "delete the scenery")
    assert (delete.kind, delete.reason) == ("uncertain", "asr_disagreement")
    assert all(c.command is None or c.command.gate != "confirm" for c in delete.candidates)
    assert [(c.kind, c.text) for c in delete.candidates] == [("pc_speech", "delete the scenery")]
    king = v("I kill the king", 0.95, "I kiss the king")
    assert (king.kind, king.reason) == ("uncertain", "asr_disagreement")
    assert [c.text for c in king.candidates] == ["I kill the king", "I kiss the king"]
    assert v("I open the chest.", 0.9, "i open the chest").kind == "pc_action"
    assert v("open character Mara", 0.9, "open character mara").command == Command(
        "open_character", "Mara")
    lanes = v("Director, make it rain", 0.95, "Director make it rain")
    assert (lanes.kind, lanes.reason) == ("uncertain", "asr_disagreement")
    assert [c.kind for c in lanes.candidates] == ["director_instruction", "pc_speech"]
    duplicates = v("I wave", 0.9, "I wave", "I wade", "I wade.")
    assert [c.text for c in duplicates.candidates] == ["I wave", "I wade"]


def test_voice_reentry():
    for intent in (v("I kill the king", 0.95, "I kiss the king"), v("reroll", 0.4),
                   v("Director, make it rain", 0.95, "Director make it rain")):
        assert intent.candidates
        for candidate in intent.candidates:
            again = r(candidate.text, MODE[candidate.kind])
            assert (again.kind, again.command, again.actor_id) == (
                candidate.kind, candidate.command, candidate.actor_id)


def test_voice_reject_and_uncertain_passthrough():
    empty = v("", 0.9, "delete the scene")
    assert (empty.kind, empty.reason) == ("reject", "empty")
    marker = v("director make this tense", 0.9)
    assert (marker.kind, marker.reason) == ("uncertain", "marker_no_separator")
    nobody = v("I open the chest", 0.95, context=RoutingContext(None, True))
    assert (nobody.kind, nobody.reason) == ("reject", "no_active_pc")
    mixed = v("Could you undo that?", 0.9, "Could you undo hat?")
    assert (mixed.kind, mixed.reason) == ("uncertain", "polite_command")
    assert [c.kind for c in mixed.candidates] == ["ui_command", "pc_speech", "pc_speech"]
    assert v("Hello there", 0.95, "Hello there", mode="command").reason == "unknown_command"
