"""080's speech-to-draft revision (``grimoire.speech.dictation``): what one dictated utterance does to the
composer's unsent text. Pure: invented sentences and the fixtures' placeholder names only.

The proving scenario is here at the revision level (an invented name dictated, then "no, I meant the west
gate"); test_app_dictation.py runs it through the app and the real chat page."""

from __future__ import annotations

import time

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


@pytest.mark.parametrize(("passage", "said", "expected"), [
    ("We ride to the gate", "no, I meant the west gate", "We ride to the west gate"),  # a word put in
    ("I give the key to the guard at the gate", "no I meant the west gate", "I give the key to the guard at the west gate"),
    ("We go to the west gate", "I meant the gate", "We go to the gate"),  # a word taken out
    ("We hire three guards", "no I meant four guards", "We hire four guards"),  # a word swapped
    ("We draw swords and charge the line", "no I meant we draw bows", "We draw bows and charge the line"),
    ("We head to the east gate", "no, I meant the west gate please", "We head to the west gate"),  # not the courtesy
    ("I attack the orc and flee", "I meant I attack the goblin chief", "I attack the goblin chief and flee"),
    ("We ride to the gate", "I meant a gate", "We ride to a gate"),
    ("We go to the big west gate", "I meant the gate", "We go to the gate"),  # two words taken out
    ("East gate now", "correction west gate", "West gate now"),  # swapped, not put in beside "east"
    ("Gate is shut", "I meant the west gate", "The west gate is shut"),
    ("We ride to an old gate", "I meant the west gate", "We ride to the west gate"),  # no "an the"
    ("Bring the rope and the lamp", "I meant the rope and a torch", "Bring the rope and a torch"),
    ("Mara waits and I go", "I meant we go", "Mara waits and we go"),  # no capital mid-sentence
])
def test_a_correction_that_puts_in_takes_out_or_swaps_words_keeps_its_neighbours(passage, said, expected):
    fixed = revise(passage, passage, said)
    assert (fixed.kind, fixed.text) == ("corrected", expected)


@pytest.mark.parametrize(("passage", "said", "whole"), [
    ("We ride to a gate", "I meant the west gate", "The west gate"),  # "a the west gate" would be wrong
    ("We draw swords and charge", "no I meant we draw our bows", "We draw our bows"),
    ("We leave at dawn", "no I meant we leave now", "We leave now"),  # "now" is meant, not a courtesy
    ("We run then I hide", "I meant we go", "We go"),  # nothing lines up: no clause is dropped by itself
    ("the guard is asleep", "no I meant the guards", "The guards"),  # never "the the guards asleep"
    ("I draw my sword", "no I meant my swords", "My swords"),
    ("the captain and the guard leave", "no I meant the captain and guard", "The captain and guard"),
    ("we wait for the dawn", "no I meant for dawn", "For dawn"),  # never "for for dawn"
    ("west my at road we", "no I meant a road", "A road"),  # never "my a road"
    ("we open the west gate", "no I meant open east gate", "Open east gate"),  # never "open the open east gate"
    ("I pick the lock on the iron chest", "no I meant pick wooden chest", "Pick wooden chest"),
    ("We hire three guards and two archers", "I meant three archers", "Three archers"),  # "three" is already there
    ("We pass the east tower and west gate", "no I meant the north gate", "The north gate"),  # keeps its "and"
    ("We take the stairs to west gate", "no I meant the north gate", "The north gate"),  # keeps its "to"
])
def test_a_correction_that_is_not_plainly_one_edit_is_kept_as_words_with_the_whole_passage_offered(passage, said, whole):
    kept = revise(passage, passage, said)
    assert kept.kind == "added" and kept.text.startswith(passage + " ")
    assert (kept.alternative.kind, kept.alternative.text) == ("corrected", whole)


def test_a_misheard_name_is_corrected_by_its_sound_and_only_past_its_last_words_nothing_is():
    assert revise("ask Marabel for help", "ask Marabel for help", "no I meant Mara Vell").text == "ask Mara Vell for help"
    long = "we pass the west gate " + "walk " * 199
    assert revise(long, long, "no I meant the east gate").kind == "added"  # the gate is out of reach, not "found"


def test_words_kept_by_a_correction_keep_the_passage_spelling():
    passage = "we meet Mara Vell at the west gate"
    assert revise(passage, passage, "no I meant mara vell at the east gate").text == "we meet Mara Vell at the east gate"


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


def test_a_reading_that_would_not_fit_the_message_box_is_not_offered():
    said = revise("x" * (MAX_DRAFT - 10), None, "we run")
    assert said.kind == "added"
    for words in ("post it", "scratch that"):  # keeping these words as said would pass the limit
        assert revise(said.text, said.passage, words).alternative is None


def test_a_long_draft_or_recording_costs_about_what_a_sentence_does():
    words = ["the", "west", "gate", "east", "north", "stairs", "ride", "run", "mara", "bell"]
    long = " ".join(words[(i * 7) % len(words)] for i in range(3000))[:MAX_DRAFT - 100]
    names = tuple(" ".join(words[(i + j) % len(words)] for j in range(6)) for i in range(64))
    began = time.monotonic()
    revise(long, long, "correction " + " ".join(words[(i * 3) % len(words)] for i in range(1200)), names=names)
    revise("", None, long[:16000], names=names)
    assert time.monotonic() - began < 5


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
    # a name is offered only when it both sounds and is spelled close enough
    assert [s.name for s in suggest("I tell brannock and go", ("Brannoc", "Thane Corvin"))] == ["Brannoc"]
    assert suggest("we drank ale", ("Dorn Ashe",)) == ()


@pytest.mark.parametrize(("draft", "last", "heard"), [
    ("", None, "tell mara bell while mara bellows"),  # the same letters later on are not the span
    ("We hide. ", None, "tell mara bell"),
    ("We meet at the east gate. ", "We meet at the east gate.", "no i meant mara bell at the west gate"),
    ("Go to the east gate", "Go to the east gate", "no i meant mara bells gate"),
])
def test_a_suggestion_says_where_its_words_are_in_the_draft_and_the_passage(draft, last, heard):
    said = revise(draft, last, heard, names=NAMES)
    found = [(revision, s) for revision in (said, said.alternative) if revision is not None for s in revision.suggestions]
    assert found
    for revision, s in found:
        assert revision.text[s.at:s.at + len(s.heard)] == s.heard
        assert revision.passage[s.offset:s.offset + len(s.heard)] == s.heard
        assert revision.text.index(revision.passage) + s.offset == s.at
    first = revise("", None, "tell mara bell while mara bellows", names=NAMES).suggestions[0]
    assert (first.heard, first.at) == ("mara bell", 5)
