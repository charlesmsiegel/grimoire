"""080's speech-to-draft revision (``grimoire.speech.dictation``): what one dictated utterance does to the
composer's unsent text. Pure: invented sentences and the fixtures' placeholder names only.

The proving scenario is here at the revision level (an invented name dictated, then "no, I meant the west
gate"); test_app_dictation.py runs it through the app and the real chat page."""

from __future__ import annotations

import pytest

from grimoire.speech.dictation import MAX_DRAFT, revise, suggest

NAMES = ("Mara Vell", "Anya Rook")


def test_what_is_said_is_added_as_said_with_only_sentence_capitals():
    first = revise("", None, "we head for the east gate")
    assert (first.kind, first.text, first.passage) == ("added", "We head for the east gate", "We head for the east gate")
    after_typing = revise("Quietly,", None, "i think we're followed")
    assert after_typing.text == "Quietly, I think we're followed"  # mid-sentence: no capital, one space
    new_sentence = revise("We wait. ", "x", "then i'm going in")
    assert new_sentence.text == "We wait. Then I'm going in"
    assert new_sentence.alternative is None and new_sentence.heard == "then i'm going in"


def test_an_invented_name_then_a_spoken_correction_changes_only_what_it_corrects():
    said = revise("", None, "we meet quill on at the east gate", names=NAMES)
    assert said.text == "We meet quill on at the east gate" and said.suggestions == ()  # an invented name: not guessed
    fixed = revise(said.text, said.passage, "no i meant the west gate", names=NAMES)
    assert (fixed.kind, fixed.before, fixed.after) == ("corrected", "the east gate", "the west gate")
    assert fixed.text == "We meet quill on at the west gate" and fixed.passage == fixed.text
    # the other reading keeps the words as said, in case they were a character's line
    assert fixed.alternative.kind == "added"
    assert fixed.alternative.text == "We meet quill on at the east gate no I meant the west gate"
    # a second correction acts on the corrected passage
    again = revise(fixed.text, fixed.passage, "i meant the north gate")
    assert again.text == "We meet quill on at the north gate"


def test_a_correction_only_reaches_the_last_dictated_passage():
    typed = "The east gate is shut. "
    said = revise(typed, None, "we take the stairs")
    fixed = revise(said.text, said.passage, "no i meant the west gate")
    # nothing in "we take the stairs" lines up with "the west gate", and the typed text is never touched
    assert fixed.kind == "added" and fixed.text.startswith(typed + "We take the stairs ")
    assert fixed.alternative.kind == "corrected" and fixed.alternative.before == "We take the stairs"
    assert fixed.alternative.text == typed + "The west gate"
    # no dictated passage at all, or one the player has since deleted: the words are kept as said
    assert revise("we take the stairs", None, "i meant the west gate").kind == "added"
    assert revise("We go up", "we take the stairs", "i meant the west gate").kind == "added"


def test_a_capital_is_kept_where_the_correction_starts_a_sentence():
    said = revise("", None, "east gate now")
    assert revise(said.text, said.passage, "correction west gate").text == "West gate now"


def test_dialogue_that_sounds_like_a_correction_keeps_a_one_press_way_back():
    said = revise("", None, "you know what i said")
    line = revise(said.text, said.passage, "no i meant what i said")
    # "what i said" is already there: replacing it with itself is no correction, so the words stay as said
    assert line.kind == "added" and line.text.endswith("no I meant what I said")


def test_scratch_that_removes_the_last_passage_and_nothing_else():
    said = revise("Typed first.", None, "and then we run")
    scratched = revise(said.text, said.passage, "scratch that")
    assert (scratched.kind, scratched.text, scratched.before, scratched.passage) == \
        ("scratched", "Typed first.", "And then we run", None)
    assert scratched.alternative.text == said.text + " scratch that"
    assert revise("Typed.", None, "delete that").kind == "added"  # nothing dictated to scratch


@pytest.mark.parametrize("words", ["post it", "send it please", "submit", "no post my turn", "Send that."])
def test_asking_voice_to_post_never_posts_or_changes_the_draft(words):
    said = revise("", None, "i draw my sword")
    asked = revise(said.text, said.passage, words)
    assert (asked.kind, asked.text, asked.passage) == ("not_posted", said.text, said.passage)
    assert asked.alternative.kind == "added"


def test_post_inside_a_sentence_is_only_words():
    assert revise("", None, "we post a guard at it").kind == "added"


def test_silence_and_an_overlong_draft_change_nothing():
    assert revise("Typed", "x", "  ").kind == "nothing"
    full = "x" * (MAX_DRAFT - 3)
    assert revise(full, None, "we run").kind == "too_long" and revise(full, None, "we run").text == full


def test_low_confidence_flags_the_passage_and_unknown_confidence_does_not():
    assert revise("", None, "we run", confidence=0.2).uncertain
    assert not revise("", None, "we run", confidence=0.9).uncertain
    assert not revise("", None, "we run").uncertain


def test_a_scene_name_that_sounds_like_what_was_heard_is_only_suggested():
    said = revise("", None, "tell mara bell i'm coming", names=NAMES)
    assert said.text == "Tell mara bell I'm coming"  # not replaced by itself
    assert [(s.heard, s.name) for s in said.suggestions] == [("mara bell", "Mara Vell")]
    assert suggest("anya brooke waits", NAMES)[0].name == "Anya Rook"
    # common words that merely share consonants, a name already spelled out, and stop words suggest nothing
    assert suggest("i want more of it and look there", NAMES) == ()
    assert suggest("Mara Vell nods", NAMES) == ()
    assert suggest("the a to", NAMES) == ()
