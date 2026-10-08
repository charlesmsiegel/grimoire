"""The scene-break heuristic, its decision item and its title halves (#84).

What these pin, beyond the arithmetic: that the scorer never fires on a
transition alone (a party walking through a door is the middle of a scene, not
its end), that a rewound scene reads as "nothing new" rather than as a negative
count, that the first location and the first date are placement rather than
movement, and that an unreadable answer is a quiet "no" rather than an exception
raised over a scene somebody is playing.

The legacy reply parser's tests are gone with it: every input they handed it is
an entry of the decide gate's corpus (`evals/gate.py`, held there by
`test_decide_gate.test_every_legacy_parse_case_is_a_gate_entry`), scored by the
outcome the route acts on.
"""

from __future__ import annotations

import pytest

from grimoire import decisions, prompts
from grimoire.store import calendars, scene_break

EVERY = 10


def _posts(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Post {i}."}
            for i in range(n)]


def _score(posts: int, locations=(), times=(), watermark=None, every: int = EVERY,
           provider=None) -> dict:
    return scene_break.evaluate(_posts(posts), list(locations), list(times),
                                watermark or {"at": 0, "locs": 0, "times": 0}, every,
                                provider)


@pytest.fixture
def gregorian():
    return calendars.get_provider({"provider": "gregorian", "config": {}})


# ---- the heuristic ----
def test_a_scene_that_has_only_just_started_is_not_worth_asking_about():
    assert _score(3)["due"] is False


def test_length_alone_needs_twice_the_cadence():
    """One threshold's worth of posts is a reason to look, not a reason to ask:
    the length signal is worth 1 there and the bar is 2. A scene that simply
    runs long still gets there on its own at twice the cadence, which is what
    keeps a talky scene with no movement in it reachable at all."""
    assert _score(EVERY)["due"] is False
    assert _score(EVERY)["score"] == 1
    assert _score(EVERY * 2)["due"] is True


def test_a_move_inside_the_cadence_never_fires_on_its_own():
    """The whole point of the length gate. A location change three posts into a
    scene is a travel beat; asking there would buy a provider call every time
    the party crossed a bridge."""
    scored = _score(3, locations=["gate", "market"], times=["2026-07-05", "2026-07-06"])
    assert scored["due"] is False
    assert scored["score"] > 0        # the signals are still SEEN, just not acted on


def test_a_move_at_the_cadence_is_what_actually_fires():
    scored = _score(EVERY, locations=["gate", "market"])
    assert scored["due"] is True
    assert [s["kind"] for s in scored["signals"]] == ["length", "location"]


def test_the_opening_location_and_date_are_placement_not_movement():
    """`set_location`/`_apply_datetime` append a transition line only `if
    history` — the first of each is silent. Counting entries instead of moves
    would score every scene's opening as a move on its very first evaluation."""
    scored = _score(EVERY, locations=["gate"], times=["2026-07-05"])
    assert [s["kind"] for s in scored["signals"]] == ["length"]
    assert scored["due"] is False


def test_a_long_skip_counts_double(gregorian):
    """Six hours is where a skip stops reading as pacing. Below it the advance
    still scores, just for less — which is the difference between firing at the
    cadence and waiting for twice it."""
    short = _score(EVERY, times=["2026-07-05T09:00", "2026-07-05T11:00"], provider=gregorian)
    long = _score(EVERY, times=["2026-07-05T09:00", "2026-07-06T09:00"], provider=gregorian)
    assert short["score"] == 2 and long["score"] == 3
    assert "long skip" in long["signals"][-1]["detail"]


def test_a_skip_nobody_can_measure_still_counts_as_an_advance(gregorian):
    """A hand-edited `time_history` (or a plugin calendar that raised) loses the
    SIZE of the skip, never the fact of it: the entry is in the history either
    way, and only the extra point depends on measuring it."""
    scored = _score(EVERY, times=["2026-07-05", "not a date at all"], provider=gregorian)
    assert scored["score"] == 2
    assert scored["signals"][-1]["detail"] == "the clock moved 1 time"


def test_no_calendar_at_all_is_not_an_error(gregorian):
    scored = _score(EVERY, times=["2026-07-05", "2026-07-09"], provider=None)
    assert scored["score"] == 2 and scored["due"] is True


def test_only_the_advances_since_the_last_question_are_measured(gregorian):
    """An overnight skip early in a scene must not keep re-earning its point
    every time the scene is re-scored, or the first long skip would make every
    later question fire a threshold early for the rest of the scene."""
    history = ["2026-07-05T09:00", "2026-07-06T09:00", "2026-07-06T10:00"]
    fresh = _score(EVERY, times=history, provider=gregorian)
    asked = _score(EVERY, times=history, provider=gregorian,
                   watermark={"at": 0, "locs": 0, "times": 1})
    assert fresh["score"] == 3          # the overnight skip is in range
    assert asked["score"] == 2          # only the one-hour advance is


def test_a_clock_that_moved_BACKWARDS_is_sized_and_worded_as_such(gregorian):
    """`_apply_datetime` refuses only a repeat of the current moment, so a
    flashback and a corrected date both land as an earlier entry. Floor
    division does not survive that: `-30 // 60` is `-1`, which read out as
    "the clock advanced -1 hours" — a number the model has to ignore, in a
    sentence telling it the opposite of what happened."""
    back = _score(EVERY, times=["2026-07-08", "2026-07-05"], provider=gregorian)
    assert back["signals"][-1]["detail"] == "the clock moved back 72 hours — a long skip"
    short = _score(EVERY, times=["2026-07-05T12:00", "2026-07-05T11:30"], provider=gregorian)
    assert short["signals"][-1]["detail"] == "the clock moved back 30 minutes"


def test_the_biggest_jump_is_the_biggest_by_SIZE(gregorian):
    """A scene that cut back a week to a flashback and then on by half an hour
    has moved a week. Taking the largest SIGNED gap would report the half hour
    and call the scene calm."""
    scored = _score(EVERY, provider=gregorian,
                    times=["2026-07-12T09:00", "2026-07-05T09:00", "2026-07-05T09:30"])
    assert "a long skip" in scored["signals"][-1]["detail"]
    assert scored["score"] == 3


def test_a_move_that_survived_a_rewind_is_not_re_earned():
    """A `delete_from` BELOW the watermark keeps the covered prefix intact, so
    the watermark stays valid while `_rewound_history` trims both histories
    under it. The moves that survive are the EARLY ones — the ones the stale
    count already covered — so capping the watermark at what the scene now has
    must not turn them back into news."""
    scored = _score(30, locations=["gate", "market"], times=["2026-07-05", "2026-07-06"],
                    watermark={"at": 10, "locs": 9, "times": 9})
    assert [s["kind"] for s in scored["signals"]] == ["length"]
    # ...and the cap is what stops the subtraction going negative and flipping
    # a comparison somewhere downstream.
    assert scored["score"] == 2


def test_a_rewound_scene_reads_as_nothing_new():
    """`delete_from` trims the transcript and `_rewound_history` trims both
    histories with it, so every count can go BACKWARDS past its watermark. The
    floor at zero is what stops that from becoming a negative "posts since"."""
    scored = _score(4, locations=["gate"], times=["2026-07-05"],
                    watermark={"at": 30, "locs": 3, "times": 3})
    assert scored["posts"] == 0 and scored["signals"] == [] and scored["due"] is False


def test_the_watermark_it_reports_is_the_snapshot_it_scored():
    scored = _score(12, locations=["gate", "market", "dock"],
                    times=["2026-07-05", "2026-07-06"])
    assert scored["watermark"] == {"at": 12, "locs": 2, "times": 1}


def test_zero_turns_the_feature_off_however_much_has_happened():
    scored = _score(400, locations=["gate", "market"], times=["2026-07-05", "2026-08-05"],
                    every=0)
    assert scored["due"] is False and scored["signals"] == []


def test_moves_is_the_one_definition_both_halves_use():
    """`dismiss_scene_break` writes a watermark with this, and `evaluate`
    compares against it. Two copies that drifted would leave a dismissed
    suggestion re-earning its location point immediately."""
    assert scene_break.moves([]) == 0
    assert scene_break.moves(["gate"]) == 0
    assert scene_break.moves(["gate", "market"]) == 1


# ---- the decision item (slice F: what `decide()` is asked) ----

_FACTS = {"location": "Saltmarch", "date": "2026-07-05", "cast": ["characters/mara"]}
_SIGNALS = [{"kind": "length", "weight": 2, "detail": "40 posts since this was last considered"}]


def test_build_item_carries_the_old_user_message_as_context():
    """The decide prompt is shown exactly what the legacy user message showed
    -- head, signals, transcript (`scene_break/user.j2`) -- and asks one
    yes/no question, the old system prompt's criteria moved into its
    instructions."""
    for signals, facts, title in ((_SIGNALS, _FACTS, "The Salt Gate"), ([], None, "")):
        item = scene_break.build_item("Mara waits.", signals, facts, title)
        old = prompts.render("scene_break/user.j2", transcript="Mara waits.",
                             signals=signals, facts=facts, title=title)
        assert item.context == old
        assert item.questions == (decisions.Predicate(
            scene_break.QUESTION_ID, prompts.render("scene_break/question.j2")),)
    assert scene_break.QUESTION_ID == "over"
    assert "natural place to stop" in item.questions[0].instructions
    decisions.validate([item])
    assert "still unresolved" in scene_break.explain()


def test_the_item_carries_the_signals_as_the_reason_it_is_asking():
    item = scene_break.build_item("Mara waits.", _SIGNALS, _FACTS, "The Salt Gate")
    assert "natural place to stop" in item.questions[0].instructions
    body = item.context
    assert "40 posts since this was last considered" in body
    assert "Saltmarch" in body and "The Salt Gate" in body and "Mara waits." in body


def test_a_scene_with_no_facts_and_no_signals_renders_no_empty_head():
    body = scene_break.build_item("Mara waits.", [], None, "").context
    assert not body.startswith("\n")
    assert "You are being asked because" not in body


def _result(answer, rationale: str = "") -> decisions.ItemResult:
    reason = "unreadable" if answer is None else ""
    return decisions.ItemResult({"over": decisions.Answer(answer, reason)}, rationale)


def test_verdict_of_an_unreadable_answer_is_no_break():
    """`None` is today's safe direction: nobody is asked, nothing is raised."""
    assert scene_break.verdict_of(_result(None)) == {"break": False, "reason": "", "title": ""}
    assert scene_break.verdict_of(_result(None, "Half a thought.")) == {
        "break": False, "reason": "Half a thought.", "title": ""}
    assert scene_break.verdict_of(_result(False, "They are mid-argument.")) == {
        "break": False, "reason": "They are mid-argument.", "title": ""}
    assert scene_break.verdict_of(_result(True, "The ledger changed hands.")) == {
        "break": True, "reason": "The ledger changed hands.", "title": ""}


def test_a_multi_line_rationale_is_collapsed():
    """The reason goes to frontmatter, one line per key, and its writer does
    not escape newlines: a reason containing one would be read back as junk --
    or, if it began `---`, as the end of the frontmatter block."""
    verdict = scene_break.verdict_of(_result(True, "They parted.\n---\ndone: false"))
    assert verdict["reason"] == "They parted. --- done: false"


def test_parse_title_strips_quotes_and_punctuation_collapses_lines_and_caps_length():
    assert scene_break.parse_title("The Long Walk Back") == "The Long Walk Back"
    assert scene_break.parse_title('"The Long Walk Back."') == "The Long Walk Back"
    assert scene_break.parse_title("'The Long Walk Back'") == "The Long Walk Back"
    assert scene_break.parse_title("`The Long Walk Back`!") == "The Long Walk Back"
    assert scene_break.parse_title('"The Long Walk Back".') == "The Long Walk Back"
    assert scene_break.parse_title("\u201cThe Long Walk Back\u201d") == "The Long Walk Back"
    assert scene_break.parse_title("  The Long   Walk\tBack?:; \n") == "The Long Walk Back"
    long = scene_break.parse_title("Saltmarch " * 20)
    assert len(long) <= scene_break.TITLE_MAX == 80
    assert long == ("Saltmarch " * 20)[:80].rstrip()


def test_parse_title_strips_quotes_only_as_a_surrounding_pair():
    """An apostrophe that belongs to the title is not a quotation mark: only a
    quote that opens AND closes the whole reply is decoration."""
    for title in ("'Tis the Season", "The Sisters'", "Mara's Ledger",
                  '"The Long Walk Back" -- a quiet close', "'Tis the Sisters' Pier"):
        assert scene_break.parse_title(title) == title, title


def test_parse_title_takes_the_first_line_and_never_joins_an_explanation():
    """The title call's reply is free text; a model that explains its title on
    the next line has not made the explanation part of it."""
    reply = "\n  The Long Walk Back\n\nThis title reflects the resolved debt."
    assert scene_break.parse_title(reply) == "The Long Walk Back"
    assert scene_break.parse_title("A\nB") == "A"
    assert scene_break.parse_title('\n\n"Saltmarch at Low Tide"\nbecause the tide went out') == \
        "Saltmarch at Low Tide"


def test_parse_title_of_nothing_is_empty():
    for text in ("", "   ", "\n\n", '""', "''", "``", "...", '"."'):
        assert scene_break.parse_title(text) == "", text


def test_the_title_prompt_carries_the_head_the_reason_and_the_transcript():
    system, user = scene_break.build_title_prompt("Mara waits.", _FACTS, "The Salt Gate",
                                                  "The ledger changed hands.")
    assert system["content"] == prompts.render("scene_break_title/system.j2")
    assert "Reply with the title alone." in system["content"]
    body = user["content"]
    assert body.startswith("Scene: The Salt Gate\nLocation: Saltmarch")
    assert "Why it ended: The ledger changed hands." in body
    assert body.endswith("Mara waits.")
    bare = scene_break.build_title_prompt("Mara waits.", None, "", "")[1]["content"]
    assert "Why it ended" not in bare and not bare.startswith("\n")
