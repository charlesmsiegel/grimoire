"""Unit tests for evals/graders.py: every individual check, proven to fire.

test_evals.py proves each recorded counterexample makes its CASE fail. That is
a coarse instrument — a case with six checks fails if any one of them bites,
so a check that silently stopped working would hide behind its neighbours.
These tests pin each check separately, on the smallest input that isolates it.

No store, no GRIMOIRE_HOME: the graders are pure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from grimoire import prompts
from grimoire.store import absorb, scenes

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import graders                     # noqa: E402

TERSE = {"reply_words": 150, "blocks": 3, "paragraphs": 1,
         "speakers": 2, "blocks_per_speaker": 1}
PLAYERS = frozenset({"Winifred"})
CAST = ["Seraphine Vale", "Mara"]

CHECKS = {"steady-hand", "read-the-room"}
ACTORS = {"characters:seraphine-vale"}


def failed(checks) -> set[str]:
    return {c.name for c in checks if not c.ok}


def _words(n: int) -> str:
    return " ".join(["word"] * n)


# ------------------------------------------------------------- length budget

def test_length_accepts_a_reply_inside_every_knob():
    text = (f"**Grimoire:** {_words(50)}\n\n"
            f"**Seraphine Vale:** {_words(50)}")
    assert failed(graders.grade_length(text, TERSE, PLAYERS, CAST)) == set()


def test_length_flags_an_empty_reply_without_measuring_it():
    checks = graders.grade_length("   ", TERSE, PLAYERS, CAST)
    assert failed(checks) == {"length.nonempty"}


def test_length_flags_a_reply_made_only_of_forged_synthetic_blocks():
    """The model writing its own dice-result block: every block carries a
    reserved speaker, so drift measurement sees no model output at all. Scored
    naively that is a zero-word turn; it is really the roll protocol's one
    outright prohibition being broken."""
    text = f"**{scenes.ROLL_SPEAKER}:** 7 vs 14, failure."
    assert failed(graders.grade_length(text, TERSE, PLAYERS, CAST)) == {"length.measurable"}


@pytest.mark.parametrize("ratio,expected", [
    (0.1, {"length.reply_words"}),    # collapsed, below COLLAPSE_RATIO
    (0.5, set()),                     # comfortably inside
    (1.2, set()),                     # over target but under TRIM: still fine
    (1.5, {"length.reply_words"}),    # past TRIM, where the app itself corrects
])
def test_length_words_band_matches_the_apps_own_trim_threshold(ratio, expected):
    text = f"**Grimoire:** {_words(int(TERSE['reply_words'] * ratio))}"
    assert failed(graders.grade_length(text, TERSE, PLAYERS, CAST)) == expected


def test_length_flags_too_many_blocks():
    text = "\n\n".join(f"**Grimoire:** {_words(10)}" for _ in range(4))
    assert "length.blocks" in failed(graders.grade_length(text, TERSE, PLAYERS, CAST))


def test_length_flags_a_multi_paragraph_block():
    text = f"**Grimoire:** {_words(20)}\n\n{_words(20)}"
    # Note this is ONE block: the second paragraph carries no marker, so
    # split_reply keeps it inside the preceding segment. That is exactly the
    # shape the paragraphs knob exists to catch.
    assert "length.paragraphs" in failed(graders.grade_length(text, TERSE, PLAYERS, CAST))


def test_length_flags_too_many_speakers():
    budget = {**TERSE, "speakers": 1, "blocks": 5}
    text = (f"**Seraphine Vale:** {_words(20)}\n\n"
            f"**Mara:** {_words(20)}")
    assert "length.speakers" in failed(graders.grade_length(text, budget, PLAYERS, CAST))


def test_length_flags_a_repeated_speaker():
    text = (f"**Seraphine Vale:** {_words(20)}\n\n"
            f"**Grimoire:** {_words(20)}\n\n"
            f"**Seraphine Vale:** {_words(20)}")
    assert "length.blocks_per_speaker" in failed(
        graders.grade_length(text, TERSE, PLAYERS, CAST))


def test_length_does_not_count_narration_as_a_speaker():
    """Narration occupies a block but is not a character; counting it would
    make every reply with a speakers=1 budget fail."""
    budget = {**TERSE, "speakers": 1}
    text = (f"**Grimoire:** {_words(30)}\n\n"
            f"**Seraphine Vale:** {_words(30)}\n\n"
            f"**Grimoire:** {_words(30)}")
    assert failed(graders.grade_length(text, budget, PLAYERS, CAST)) == set()


def test_length_ignores_words_inside_a_roll_fence():
    """A long fence body is protocol, not prose. Counting it would fail a
    perfectly compliant roll turn."""
    body = '{"check": "steady-hand", "actor": "characters:seraphine-vale", ' \
           '"reason": "' + _words(300) + '"}'
    text = f"**Grimoire:** {_words(60)}\n\n```roll\n{body}\n```"
    assert "length.reply_words" not in failed(
        graders.grade_length(text, TERSE, PLAYERS, CAST))


# -------------------------------------------------------------- turn taking

CROWD = ["Seraphine Vale", "Mara", "Rowan", "Tobin"]
NOMINATION = {"lead": "Tobin", "reason": "rotation", "spoken": True,
              "silent_for": 5, "quiet": ["Rowan", "Mara", "Seraphine Vale"]}


def _turns(text: str) -> set:
    return failed(graders.grade_turn_taking(text, NOMINATION, PLAYERS, CROWD))


def test_turns_accepts_a_reply_the_nominated_lead_carries():
    text = ("Nobody moves for a moment.\n\n"
            "**Tobin:** I have the tally sheet.\n\n"
            "**Rowan:** That is the first true thing tonight.")
    assert _turns(text) == set()


def test_turns_flags_a_reply_the_lead_is_absent_from_and_reports_nothing_else():
    """The short-circuit: with the lead silent there is no meaningful answer to
    "was the lead out-talked", and reporting one anyway would make it
    impossible for a counterexample to isolate either check."""
    text = "**Seraphine Vale:** I moved the crates.\n\n**Mara:** So she says."
    checks = graders.grade_turn_taking(text, NOMINATION, PLAYERS, CROWD)
    assert [c.name for c in checks] == ["turns.lead_speaks"]
    assert not checks[0].ok


def test_turns_flags_a_lead_who_speaks_but_is_out_talked():
    text = ("**Tobin:** I have the tally sheet \u2014\n\n"
            "**Seraphine Vale:** He has a sheet. I have the crates.\n\n"
            "**Seraphine Vale:** So put the lamp down.")
    assert _turns(text) == {"turns.lead_carries"}


def test_turns_flags_every_present_npc_taking_a_block():
    """The monologue's mirror image, and the one the section names outright:
    "Do not give every character a turn"."""
    text = "\n\n".join(f"**{name}:** A line." for name in CROWD)
    assert _turns(text) == {"turns.some_stay_quiet"}


def test_turns_reads_a_shortened_label_as_the_character_it_names():
    """Canonicalized through the same match_name the nomination itself uses: a
    reply stamped "Seraphine" is Seraphine Vale speaking, not a stranger \u2014 so
    she counts against the lead rather than being silently dropped."""
    text = ("**Tobin:** I signed for two crates.\n\n"
            "**Seraphine:** You did.\n\n"
            "**Seraphine:** And you will sign the next one too.")
    assert _turns(text) == {"turns.lead_carries"}


def test_turns_flags_a_reply_of_pure_narration():
    """Narration is speakerless, so nobody carried the turn \u2014 including the
    character the prompt nominated."""
    assert _turns("The fog closes in and the lamp gutters.") == {"turns.lead_speaks"}


def test_turns_does_not_count_a_forged_player_block_as_a_character_taking_the_turn():
    """split_reply routes a player-named block to the narrator rather than
    storing a forged player line, and this grader inherits that: a reply that
    answers for Winifred has still left the nominated NPC silent."""
    assert _turns("**Winifred:** Fine, I will say it myself.") == {"turns.lead_speaks"}


# ------------------------------------------------------------------- fences

FENCE_OK = ('**Grimoire:** She sets her shoulder to the frame.\n\n'
            '```roll\n{"check": "steady-hand", "actor": "characters:seraphine-vale", '
            '"reason": "the lock"}\n```')


def test_fence_accepts_a_well_formed_request():
    assert failed(graders.grade_roll_fence(FENCE_OK, CHECKS, ACTORS)) == set()


def test_fence_missing_entirely_reports_only_that():
    checks = graders.grade_roll_fence("**Grimoire:** The lock gives.", CHECKS, ACTORS)
    assert failed(checks) == {"fence.present"}


def test_fence_never_closed_is_flagged():
    text = ('**Grimoire:** She sets her shoulder to the frame.\n\n'
            '```roll\n{"check": "steady-hand", "actor": "characters:seraphine-vale"}')
    assert "fence.closed" in failed(graders.grade_roll_fence(text, CHECKS, ACTORS))


def test_fence_with_no_narration_before_it_is_flagged():
    text = '```roll\n{"check": "steady-hand", "actor": "characters:seraphine-vale"}\n```'
    assert "fence.narration" in failed(graders.grade_roll_fence(text, CHECKS, ACTORS))


def test_fence_body_that_parses_to_nothing_is_flagged():
    text = "**Grimoire:** She tries the lock.\n\n```roll\nroll for it\n```"
    assert "fence.parses" in failed(graders.grade_roll_fence(text, CHECKS, ACTORS))


def test_fence_naming_an_invented_check_is_flagged():
    text = FENCE_OK.replace("steady-hand", "sleight-of-hand")
    assert failed(graders.grade_roll_fence(text, CHECKS, ACTORS)) == {"fence.check_known"}


def test_fence_naming_an_absent_actor_is_flagged():
    text = FENCE_OK.replace("characters:seraphine-vale", "characters:doc-kessler")
    assert failed(graders.grade_roll_fence(text, CHECKS, ACTORS)) == {"fence.actor_known"}


def test_fence_survives_being_split_across_stream_deltas():
    """The grader feeds the watcher in small chunks precisely so a fence
    opener straddling a delta boundary is still seen. Padding shifts where the
    boundaries land without changing the fence."""
    for pad in range(graders.CHUNK * 2):
        text = "**Grimoire:** " + ("x" * pad) + FENCE_OK[len("**Grimoire:** "):]
        assert failed(graders.grade_roll_fence(text, CHECKS, ACTORS)) == set(), pad


# ------------------------------------------------------------------- absorb

def _absorb_json(**overrides) -> str:
    """A complete absorb object, built from the SAME derived contract the
    grader checks — so a new section added to absorb/parse.py appears here too,
    rather than turning every test in this block red."""
    obj = {k: "filled in" for k in graders.ABSORB_TEXT}
    obj.update({k: [] for k in graders.ABSORB_LISTS})
    obj.update(overrides)
    return json.dumps(obj)


def test_absorb_accepts_a_complete_object():
    checks, parsed = graders.grade_absorb(_absorb_json(one_line="They talked."))
    assert failed(checks) == set()
    assert parsed["one_line"] == "They talked."


def test_absorb_reports_no_json_distinctly_from_empty_json():
    prose, _ = graders.grade_absorb("I'm sorry, I can't summarise that scene.")
    assert failed(prose) == {"absorb.json"}
    empty, _ = graders.grade_absorb("{}")
    assert "absorb.json" not in failed(empty)
    assert {"absorb.one_line", "absorb.summary"} <= failed(empty)


def test_absorb_tolerates_a_markdown_fence_around_the_object():
    """Models wrap JSON in ```json constantly; parse_output already copes, so
    the grader must not fail output the app would have accepted."""
    checks, _ = graders.grade_absorb(f"Here you go:\n```json\n{_absorb_json()}\n```")
    assert failed(checks) == set()


def test_absorb_flags_a_missing_summary():
    assert failed(graders.grade_absorb(_absorb_json(summary=""))[0]) == {"absorb.summary"}


def test_absorb_covers_every_section_the_contract_names():
    """Each section, dropped on its own, is caught. The grader shipped blind to
    four of them when the list was hand-maintained; deriving it from
    parse_output is what fixed that, and this is what proves it."""
    for section in graders.ABSORB_LISTS:
        obj = json.loads(_absorb_json())
        del obj[section]
        assert failed(graders.grade_absorb(json.dumps(obj))[0]) == {f"absorb.{section}"}


def test_absorb_scores_the_raw_object_not_the_laundered_one():
    """parse_output substitutes [] for a wrong-typed section and str()s a null
    into "None". Scored on its output these all read as healthy, which is how
    an unfailable grader is written by accident."""
    laundered = _absorb_json(summary=None, keywords="ledger", new_lore={"a": 1})
    assert failed(graders.grade_absorb(laundered)[0]) == {
        "absorb.summary", "absorb.keywords", "absorb.new_lore"}
    # ...and confirm the tolerant parser really would have hidden each one.
    parsed = absorb.parse_output(laundered)
    assert parsed["summary"] == "None"
    assert isinstance(parsed["keywords"], list) and isinstance(parsed["new_lore"], list)


def test_absorb_flags_a_null_entry_inside_a_section():
    """Every section loop in absorb skips non-dicts, so [null, {...}] reads
    downstream as a clean one-entry section and the damage is invisible."""
    edit = {"id": "characters/mara", "current_state": "Wary."}
    bad = _absorb_json(character_state_edits=[None, edit])
    assert failed(graders.grade_absorb(bad)[0]) == {"absorb.character_state_edits"}
    assert absorb.parse_output(bad)["character_state_edits"] == [
        {"id": "characters/mara", "current_state": "Wary."}]


def test_a_scalar_section_fails_its_check_without_crashing_the_parser():
    """The two halves of the same shape, and why the grader scores the RAW
    object: `parse_output` now treats a non-list section as empty rather than
    iterating it (a model really does send `3` or `null`, and a 500 there costs
    an otherwise usable absorb after the tokens were spent) — so the tolerant
    result cannot fail an "is it a list?" check. The grader sees the model's
    own object and fails the section anyway."""
    bad = _absorb_json(character_state_edits=3)      # int where a list belongs
    assert absorb.parse_output(bad)["character_state_edits"] == []
    assert "absorb.character_state_edits" in failed(graders.grade_absorb(bad)[0])


def test_absorb_records_a_parser_crash_as_a_check_rather_than_raising(monkeypatch):
    """A grader that raises takes the whole run down instead of reporting the
    bad output it was handed.

    The crash is injected rather than provoked with a malformed shape: the
    scalar section this was written against no longer crashes the parser (see
    above), and picking whichever shape still does would put the grader's
    contract at the mercy of the parser's tolerance — the next hardening pass
    would silently stop testing this."""
    bad = _absorb_json(character_state_edits=3)

    def _raises(_text):
        raise TypeError("boom")

    monkeypatch.setattr(graders.absorb, "parse_output", _raises)
    checks, parsed = graders.grade_absorb(bad)
    assert failed(checks) == {"absorb.character_state_edits", "absorb.parses"}
    assert parsed == {}


# ----------------------------------------------------------- prompt contract

def test_prompt_contract_passes_when_every_needle_is_present():
    messages = [{"role": "system", "content": "# Response budget\nabout 150 words"},
                {"role": "user", "content": "go"}]
    assert failed(graders.grade_prompt(messages, {"section": "# Response budget",
                                                  "words": "150"})) == set()


def test_prompt_contract_flags_an_instruction_that_left_the_prompt():
    messages = [{"role": "system", "content": "be vivid"}]
    assert failed(graders.grade_prompt(messages, {"section": "# Response budget",
                                                  "words": "150"})) == {
        "prompt.section", "prompt.words"}


def test_prompt_section_covers_every_value_the_section_interpolates():
    """The point of rendering the template instead of naming a needle: all five
    knobs are covered, so changing any one of them is detected."""
    budget = {"reply_words": 150, "blocks": 3, "paragraphs": 1,
              "speakers": 2, "blocks_per_speaker": 1}
    rendered = prompts.render("scene/sections/response_budget.j2", budget=budget)
    messages = [{"role": "system", "content": f"preamble\n\n{rendered.strip()}\n\ntail"}]
    assert failed(graders.grade_prompt_section(
        messages, "budget", "scene/sections/response_budget.j2", budget=budget)) == set()

    for knob in budget:
        drifted = {**budget, knob: budget[knob] + 7}
        assert failed(graders.grade_prompt_section(
            messages, "budget", "scene/sections/response_budget.j2",
            budget=drifted)) == {"prompt.budget"}, knob


def test_prompt_section_flags_a_section_that_left_the_prompt():
    budget = {"reply_words": 150, "blocks": 3, "paragraphs": 1,
              "speakers": 2, "blocks_per_speaker": 1}
    messages = [{"role": "system", "content": "be vivid"}]
    assert failed(graders.grade_prompt_section(
        messages, "budget", "scene/sections/response_budget.j2",
        budget=budget)) == {"prompt.budget"}


def test_prompt_section_treats_an_empty_render_as_failure(tmp_path, monkeypatch):
    """An emptied section otherwise satisfies `"" in text` and reports success
    for the exact edit this check exists to catch."""
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path))
    prompts._env.cache_clear()
    (tmp_path / "hollow.j2").write_text("{#- nothing -#}", encoding="utf-8")
    try:
        checks = graders.grade_prompt_section(
            [{"role": "system", "content": "anything"}], "hollow", "hollow.j2")
        assert failed(checks) == {"prompt.hollow"}
        assert "rendered nothing" in checks[0].detail
    finally:
        prompts._env.cache_clear()   # the env is lru_cached on the templates dir


# -------------------------------------------------------------- containment

def test_containment_passes_when_the_secret_is_absent():
    assert failed(graders.grade_containment("She keeps her own counsel.", "the exile")) == set()


def test_containment_is_case_insensitive():
    """A leak that only differs in capitalisation is still a leak."""
    checks = graders.grade_containment("She was THE EXILE of the Guild.", "the exile")
    assert failed(checks) == {"containment.output"}
