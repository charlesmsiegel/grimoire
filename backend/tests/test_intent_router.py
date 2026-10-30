"""Typed-utterance routing into command, in-character and director lanes."""

import pytest

from grimoire.intent.commands import Command
from grimoire.intent.model import Utterance, RoutingContext
from grimoire.intent.router import route

CTX = RoutingContext("pc-1", True)
MODE = {"ui_command": "command", "pc_speech": "speech", "pc_action": "action",
        "director_instruction": "director"}


def r(text, mode="auto", context=CTX):
    return route(Utterance(text, mode=mode), context)


def kinds(intent):
    return [candidate.kind for candidate in intent.candidates]


def test_proving_scenario():
    inventory = r("Open the inventory")
    assert (inventory.kind, inventory.reason) == ("ui_command", "bare_command")
    assert inventory.command == Command("navigate", "inventory")
    assert inventory.command.gate == "instant" and "Command" in inventory.feedback

    chest = r("I open the chest")
    assert (chest.kind, chest.reason, chest.actor_id) == ("pc_action", "first_person", "pc-1")
    assert chest.text == "I open the chest"

    tense = r("Director, make this tense")
    assert (tense.kind, tense.reason, tense.text) == (
        "director_instruction", "director_marker", "make this tense")

    delete = r("Delete the scene")
    assert delete.command == Command("delete_scene") and delete.command.gate == "confirm"
    assert "needs confirmation" in delete.feedback


def test_explicit_mode_beats_wording():
    speech = r("Delete the scene", "speech")
    assert (speech.kind, speech.reason, speech.text) == ("pc_speech", "explicit_mode", "Delete the scene")
    assert r("Hello", "action").kind == "pc_action"
    assert r('"Hello"', "speech").text == "Hello"
    assert r("*waves*", "action").text == "waves"
    director = r("make it rain", "director")
    assert (director.kind, director.text) == ("director_instruction", "make it rain")
    assert r("Director, make it rain", "director").text == "make it rain"
    unknown = r("Hello there", "command")
    assert (unknown.kind, unknown.reason, unknown.candidates) == ("uncertain", "unknown_command", ())
    assert r("Grimoire, pause", "command").command == Command("pause")
    assert r("Could you pause?", "command").kind == "uncertain"


@pytest.mark.parametrize("text, mode", [
    ("Director, make this tense", "speech"), ("Grimoire, pause", "speech"),
    ("OOC: hi", "action"), ("Grimoire: pause", "director"), ("OOC: hi", "command"),
    ("Director, pause", "command"),
])
def test_lane_conflict_for_separated_markers(text, mode):
    intent = r(text, mode)
    assert (intent.kind, intent.reason, intent.candidates) == ("uncertain", "lane_conflict", ())


def test_unseparated_markers_are_words_in_explicit_modes():
    assert r("director make this tense", "speech").text == "director make this tense"
    assert r("Director Vance, you're late", "speech").kind == "pc_speech"


@pytest.mark.parametrize("text", ["Grimoire, pause", "grimoire pause", "  Grimoire ,  pause ",
                                  "GRIMOIRE: PAUSE"])
def test_wake_word(text):
    intent = r(text)
    assert (intent.kind, intent.reason, intent.command) == ("ui_command", "wake_word", Command("pause"))


def test_wake_word_never_falls_back_to_speech():
    for text in ("Grimoire, frobnicate", "grimoire frobnicate", "Grimoire"):
        intent = r(text)
        assert (intent.kind, intent.reason) == ("uncertain", "unknown_command")
    glow = r("Grimoire's pages glow")
    assert (glow.kind, glow.reason) == ("pc_speech", "default_speech")


@pytest.mark.parametrize("text, payload", [
    ("director make this tense", "make this tense"), ("ooc make it rain", "make it rain"),
])
def test_unseparated_director_marker(text, payload):
    intent = r(text)
    assert (intent.kind, intent.reason) == ("uncertain", "marker_no_separator")
    assert kinds(intent) == ["director_instruction", "pc_speech"]
    assert intent.candidates[0].text == payload
    assert intent.candidates[1].text == text


def test_quotes_and_asterisks():
    for text in ('"Run!"', "“Run!”"):
        intent = r(text)
        assert (intent.kind, intent.reason, intent.text) == ("pc_speech", "quoted", "Run!")
    escaped = r('"Wait. Grimoire, help us!"')
    assert (escaped.kind, escaped.text) == ("pc_speech", "Wait. Grimoire, help us!")
    sword = r("*draws sword*")
    assert (sword.kind, sword.reason, sword.text) == ("pc_action", "asterisks", "draws sword")
    assert (r("I’m hiding").kind, r("I’m hiding").reason) == ("pc_action", "first_person")
    assert r("I'd rather wait").kind == "pc_action"
    assert r("My lord, please sit.").kind == "pc_speech"
    assert r("Ice is slippery").kind == "pc_speech"


@pytest.mark.parametrize("text", [
    "I open the chest. Grimoire, pause.", "I open the chest, Grimoire pause",
    "Hello there. Delete the scene.", "Grimoire, pause. Director, slow down.",
    "Director, slow down. Grimoire, pause.", "I wave. Director, make it rain.",
    'I say "hi". Grimoire, stop.',
])
def test_mixed_utterances(text):
    intent = r(text)
    assert (intent.kind, intent.reason, intent.candidates) == ("uncertain", "mixed_utterance", ())


def test_same_lane_sentences_are_not_mixed():
    director = r("Director, raise the stakes. OOC: keep it short")
    assert (director.kind, director.text) == ("director_instruction", "raise the stakes. OOC: keep it short")
    assert r("I open the chest. I draw my sword.").kind == "pc_action"
    assert r('"Stop. Grimoire, help!"').kind == "pc_speech"


def test_repeated_and_shouted_commands():
    for text in ("stop. stop!", "stop stop", "STOP", "Stop! Stop! Stop!"):
        assert r(text).command == Command("stop"), text
    assert r("DELETE THE SCENE").command == Command("delete_scene")
    twice = r("Delete the scene. Delete the scene.")
    assert twice.command == Command("delete_scene") and twice.command.gate == "confirm"
    assert twice.text == "Delete the scene"
    assert r("Undo. Undo.").text == "Undo"


def test_polite_command():
    undo = r("Could you undo that?")
    assert (undo.kind, undo.reason) == ("uncertain", "polite_command")
    assert kinds(undo) == ["ui_command", "pc_speech"]
    assert undo.candidates[0].command == Command("undo") and undo.candidates[0].text == "undo that"
    mara = r("Could you open character Mara?")
    assert mara.candidates[0].command == Command("open_character", "Mara")
    assert mara.candidates[0].text == "open character Mara"
    assert kinds(r("Let's pause.")) == ["ui_command", "pc_speech"]
    delete = r("Could you delete the scene?")
    assert (delete.kind, kinds(delete)) == ("uncertain", ["pc_speech"])


@pytest.mark.parametrize("text", ["Delete everything", "option 12", "option 0", "option ten",
                                  "open character sheet", "Open the door"])
def test_command_like(text):
    intent = r(text)
    assert (intent.kind, intent.reason, kinds(intent)) == ("uncertain", "command_like", ["pc_speech"])
    assert intent.candidates[0].text == text


def test_default_speech():
    intent = r("Hello, stranger.")
    assert (intent.kind, intent.reason, intent.text, intent.actor_id) == (
        "pc_speech", "default_speech", "Hello, stranger.", "pc-1")


@pytest.mark.parametrize("text", ["Choose option two", "pick the second option", "option 2"])
def test_choose_option_phrases(text):
    assert r(text).command == Command("choose_option", "2")


def test_authority():
    nobody = RoutingContext(None, True)
    rejected = r("I open the chest", context=nobody)
    assert (rejected.kind, rejected.reason, rejected.text) == ("reject", "no_active_pc", "I open the chest")
    assert "Ignored" in rejected.feedback
    assert r("Open the inventory", context=nobody).kind == "ui_command"
    player = RoutingContext("pc-1", False)
    assert (r("Director, x", context=player).reason) == "not_director"
    assert r("x", "director", context=player).kind == "reject"
    assert kinds(r("director make this tense", context=player)) == ["pc_speech"]
    neither = r("director make this tense", context=RoutingContext(None, False))
    assert (neither.kind, neither.candidates) == ("uncertain", ())
    for text in ("", "   "):
        empty = r(text)
        assert (empty.kind, empty.reason) == ("reject", "empty")
        assert r(text, "speech").reason == "empty"


REENTRY_INPUTS = [
    "director make this tense", "ooc make it rain", "Could you undo that?",
    "Could you open character Mara?", "Could you delete the scene?", "Let's pause.",
    "Delete everything", "option 12", "open character sheet",
]


@pytest.mark.parametrize("text", REENTRY_INPUTS)
def test_reentry_contract(text):
    intent = r(text)
    assert intent.candidates
    for candidate in intent.candidates:
        again = r(candidate.text, MODE[candidate.kind])
        assert (again.kind, again.command, again.actor_id) == (
            candidate.kind, candidate.command, candidate.actor_id)


def test_repeated_command_text_reenters():
    assert r(r("Undo. Undo.").text, "command").command == Command("undo")


def test_feedback_names_lane():
    assert "Command" in r("pause").feedback
    assert "In character" in r("Hello").feedback
    assert "In character" in r("I wave").feedback
    assert "Director" in r("Director, x").feedback
    assert "Not sure" in r("Delete everything").feedback
    assert "Ignored" in r("").feedback
    assert "inventory" in r("Open the inventory").feedback


@pytest.mark.parametrize("text", ["Grimoire,pause", "grimoire:pause", "Grimoire,delete the scene"])
def test_wake_word_without_space_is_a_command(text):
    intent = r(text)
    assert (intent.kind, intent.reason) == ("ui_command", "wake_word")


@pytest.mark.parametrize("text", ["Grimoire;pause", "Grimoire—pause", "Grimoire...pause",
                                  "Grimoire… pause"])
def test_wake_word_with_odd_punctuation_never_becomes_speech(text):
    intent = r(text)
    assert intent.kind in ("ui_command", "uncertain")
    assert intent.command in (None, Command("pause"))


@pytest.mark.parametrize("text, mode", [
    ('""', "auto"), ("“”", "auto"), ('" "', "auto"), ("**", "auto"), ("* *", "auto"),
    ("(())", "auto"), ("(( ))", "auto"), ('""', "speech"), ("**", "action"), ("(())", "director"),
])
def test_empty_wrappers_are_empty(text, mode):
    intent = r(text, mode)
    assert (intent.kind, intent.reason) == ("reject", "empty")


@pytest.mark.parametrize("text", ["Director", "ooc", "gm", "director ", "Director.", "Director!"])
def test_lone_unseparated_marker_offers_no_empty_director_note(text):
    intent = r(text)
    assert intent.kind == "uncertain"
    assert all(c.kind != "director_instruction" for c in intent.candidates)
    for candidate in intent.candidates:
        again = r(candidate.text, MODE[candidate.kind])
        assert (again.kind, again.command) == (candidate.kind, candidate.command)
