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

from evals import cases, graders, slop  # noqa: E402

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
            "**Tobin:** I have the tally sheet in my coat, and it is not a good "
            "sheet, because the only signature under those two crates is mine.\n\n"
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


def test_turns_flags_a_lead_who_gets_a_block_but_not_the_turn():
    """The reply the block count cannot see, and the whole reason this is
    measured in words: the nomination is honoured with one obliging line and
    the character who has been talking all scene keeps the floor. One block
    each ties 1-1 and would score green."""
    text = ("**Tobin:** I have the tally sheet \u2014\n\n"
            "**Seraphine Vale:** He has a tally sheet. He also has a signature "
            "on it, which is more than the rest of you brought tonight, and I "
            "am not going to stand here while a clerk reads it out to me.")
    assert _turns(text) == {"turns.lead_carries"}


def test_turns_flags_a_lead_out_talked_across_several_blocks():
    text = ("**Tobin:** I have the tally sheet.\n\n"
            "**Seraphine Vale:** He has a sheet. I have the crates.\n\n"
            "**Seraphine Vale:** So put the lamp down and stop asking.")
    assert _turns(text) == {"turns.lead_carries"}


def test_turns_accepts_a_lead_who_takes_fewer_blocks_but_more_of_the_turn():
    """The direction a block count gets backwards: three clipped reactions do
    not out-talk the character actually carrying the scene, and scoring them
    3-1 against the lead would red a reply that did exactly what was asked."""
    text = ("**Tobin:** The sheet is in my coat and I will read it out. Two "
            "crates, off this pier, on the eleventh, signed for by me because "
            "I was handed the pen and told to sign.\n\n"
            "**Rowan:** Huh.\n\n"
            "**Seraphine Vale:** Read it, then.\n\n"
            "**Seraphine Vale:** Slowly.")
    assert _turns(text) == set()


def test_turns_flags_every_present_npc_taking_a_block():
    """The monologue's mirror image, and the one the section names outright:
    "Do not give every character a turn"."""
    text = "\n\n".join(f"**{name}:** A line." for name in CROWD)
    assert _turns(text) == {"turns.some_stay_quiet"}


def test_turns_reads_a_shortened_label_as_the_character_it_names():
    """Canonicalized through the same match_name the nomination itself uses: a
    reply stamped "Seraphine" is Seraphine Vale speaking, not a stranger \u2014 so
    her words count against the lead rather than being silently dropped."""
    text = ("**Tobin:** I signed for two crates.\n\n"
            "**Seraphine:** You did, and you will sign the next one too, and "
            "the one after that, and you will not ask me what is in them.")
    assert _turns(text) == {"turns.lead_carries"}


def test_turns_does_not_credit_a_roll_fence_to_the_speaker_it_landed_in():
    """Words come from length_drift._words, which subtracts the fence. Counting
    it would let a mechanical block out-talk the nominated lead on the strength
    of dice notation nobody wrote."""
    fenced = ("**Seraphine Vale:** She weighs it.\n\n"
              "```roll\ncheck: steady-hand\nactor: characters:seraphine-vale\n"
              "reason: prying the crate open without waking the pier\n```")
    text = "**Tobin:** I signed for two crates that were never here.\n\n" + fenced
    assert _turns(text) == set()


def test_turns_tells_a_format_failure_apart_from_an_invented_speaker():
    """Both leave the nominated lead with no block, and they send a live run to
    completely different places: one is the reply format coming apart, the
    other is the model answering as somebody who is not in the scene. A report
    that read the same for both would have #82 answered on the wrong
    evidence."""
    none_at_all = graders.grade_turn_taking("The fog closes in.", NOMINATION,
                                            PLAYERS, CROWD)
    assert "no **Name:** blocks at all" in none_at_all[0].detail

    stray = graders.grade_turn_taking("**Harbourmaster:** Nobody logged those.",
                                      NOMINATION, PLAYERS, CROWD)
    assert "name nobody present: Harbourmaster" in stray[0].detail


def test_turns_accepts_a_reply_only_the_nominated_lead_speaks_in():
    """No rival to be out-talked by. The check passes and says nothing, rather
    than reporting a comparison against a speaker who does not exist."""
    checks = graders.grade_turn_taking("**Tobin:** Two crates, and my name on "
                                       "both of them.", NOMINATION, PLAYERS, CROWD)
    assert failed(checks) == set()
    assert next(c for c in checks if c.name == "turns.lead_carries").detail == ""


def test_turns_flags_a_reply_of_pure_narration():
    """Narration is speakerless, so nobody carried the turn \u2014 including the
    character the prompt nominated."""
    assert _turns("The fog closes in and the lamp gutters.") == {"turns.lead_speaks"}


def test_turns_does_not_count_a_forged_player_block_as_a_character_taking_the_turn():
    """split_reply routes a player-named block to the narrator rather than
    storing a forged player line, and this grader inherits that: a reply that
    answers for Winifred has still left the nominated NPC silent."""
    assert _turns("**Winifred:** Fine, I will say it myself.") == {"turns.lead_speaks"}


def test_turns_reports_a_missing_nomination_instead_of_raising():
    """`nominate` returns None below two present NPCs. A grader that indexed
    the nomination blind would take the whole run down on a fixture that lost a
    cast member, instead of reporting the input it was handed \u2014 grade_absorb's
    rule, applied here."""
    for empty in (None, {}):
        checks = graders.grade_turn_taking("**Tobin:** Anything.", empty,
                                           PLAYERS, CROWD)
        assert [c.name for c in checks] == ["turns.nominated"]
        assert not checks[0].ok


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
    obj = dict.fromkeys(graders.ABSORB_TEXT, "filled in")
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


def test_normalize_returns_bodies_and_speaker_labels_separately():
    """split_reply moves the marker into `speaker` and out of `content`, so a
    stock name used as a speaker label is invisible to any body-only detector.
    normalize hands both back."""
    text = "**Elara:** Evening.\n\n**Seraphine Vale:** You're late."
    prose_text, names = slop.normalize(text, frozenset({"Winifred"}))
    assert "Elara" not in prose_text
    assert "Elara" in names
    assert "Evening." in prose_text


def test_normalize_routes_player_blocks_to_the_narrator():
    """A player-named block is narrator content, per split_reply. Its label is
    not a speaker name and must not be offered as one."""
    _, names = slop.normalize("**Winifred:** I step out of the fog.",
                              frozenset({"Winifred"}))
    assert names == []


def test_normalize_strips_fences_and_images_via_production_parser():
    text = ("**Seraphine Vale:** Mine.\n\n"
            "```roll\ncheck: nerve\nactor: Seraphine Vale\n```\n\n"
            "![crates](/api/worlds/realm/art/crates.png)")
    prose_text, _ = slop.normalize(text, frozenset())
    assert "check: nerve" not in prose_text
    assert "crates.png" not in prose_text


def test_sentences_splits_on_terminators_and_respects_closing_quotes():
    """`"Go." Mara left.` is two sentences: the terminator precedes the closing
    quote. Getting this wrong miscounts every line of dialogue."""
    assert slop.sentences('"Go." Mara left.') == ['"Go."', 'Mara left.']


def test_sentences_splits_on_curly_quotes():
    r"""LLM prose routinely uses typographic (curly) quotes rather than
    straight ones. Written with explicit \uXXXX escapes -- not typed curly
    characters -- so a future edit cannot silently straighten this test's
    input back to straight quotes and have it keep passing for the wrong
    reason: if _SENTENCE_BREAK's character classes ever drop the curly
    codepoints again, the curly-quoted "Go." and "Mara left." below would
    silently merge into one sentence and this fails."""
    text = "\u201cGo.\u201d Mara left."
    assert slop.sentences(text) == ["\u201cGo.\u201d", "Mara left."]


def test_sentences_does_not_split_on_a_known_abbreviation():
    assert slop.sentences("Dr. Rowan waited. Nobody came.") == [
        "Dr. Rowan waited.", "Nobody came."]


def test_sentences_recognises_an_abbreviation_inside_a_quotation():
    """The common dialogue case. The abbreviation check has to strip the
    LEADING quote as well as the trailing terminator, or `"Dr.` is not
    recognised as `dr` and the line splits mid-quotation."""
    assert slop.sentences('"Dr. Rowan waited." Nobody came.') == [
        '"Dr. Rowan waited."', "Nobody came."]


def test_paragraphs_splits_on_blank_lines_and_drops_empty_ones():
    assert slop.paragraphs("One.\n\n  \n\nTwo.\n") == ["One.", "Two."]


def _rendered_block() -> str:
    return prompts.render("scene/sections/natural_prose.j2")


@pytest.mark.parametrize("entry", slop.ALL_ENTRIES, ids=lambda e: e.source[:40])
def test_every_entry_source_is_still_in_the_template(entry):
    """The one-way drift guard, per entry so a failure names the culprit.

    Grading a phrase the app has stopped banning is the failure this catches.
    An entry ADDED to the template is not graded until it is mirrored here --
    a stated limitation, and the direction that actually gets exercised, since
    the pink-elephant remedy on record is trimming the ban list.

    Compared whitespace-flat: the template is hard-wrapped, so seven of these
    sources span a line break in the render."""
    assert slop._flat(entry.source) in slop._flat(_rendered_block())


def test_missing_sources_reports_what_left_the_template():
    assert slop.missing_sources("nothing here")
    assert slop.missing_sources(_rendered_block()) == []


def test_every_graded_check_family_has_a_drift_source():
    """An instruction cannot be deleted from the template while its grader
    keeps scoring replies against it."""
    for family in (slop.LITERAL_PHRASES, slop.STOCK_NAMES, slop.BEAT_WORDS,
                   slop.NOT_X_BUT_Y, slop.RHYTHM_SOURCES):
        assert family, "every graded family carries at least one drift source"


def test_judgment_only_phrases_are_never_matched():
    """Their qualifier is the whole test: the template bans the reflexive use,
    not the phrase. They are carried as drift sources only."""
    matched = {e.source for e in slop.LITERAL_PHRASES}
    for entry in slop.JUDGMENT_ONLY:
        assert entry.source not in matched


@pytest.mark.parametrize("entry", slop.LITERAL_PHRASES, ids=lambda e: e.source[:40])
def test_every_literal_phrase_entry_actually_fires(entry):
    """Per-entry coverage: a dead entry cannot hide behind a named check some
    other entry already makes fail."""
    probe = {
        "heart pounding or hammering": "Her heart pounding in her chest, she ran.",
        "a tapestry, symphony, or dance of anything": "It was a tapestry of light.",
        "spreading across her face": "A grin spreading across her face.",
        "shivers down the spine": "Shivers down her spine.",
        "knuckles whitening": "Knuckles whitening on the rail.",
        "a smile playing on": "A smile playing on her lips.",
    }.get(entry.source, entry.source)
    assert slop.found_phrases(probe), f"{entry.source!r} never fires"


@pytest.mark.parametrize("probe", [
    # Each ALTERNATION inside a multi-form pattern, not just one arm of it.
    # Exercising `tapestry` alone would leave `symphony` and `dance` dead.
    "It was a symphony of rope and water.",
    "It was a dance of lantern light.",
    "His heart hammering against ribs, he waited.",
    "Her heart pounding against her ribs, she waited.",
    "A shiver down his spine.",
    "Knuckles whitened on the rail.",
])
def test_phrase_alternations_each_fire(probe):
    assert slop.found_phrases(probe), f"{probe!r} should have matched"


@pytest.mark.parametrize("entry", slop.BEAT_WORDS, ids=lambda e: e.source)
def test_every_beat_group_actually_fires(entry):
    """Per-entry coverage for the beat cap. Without this, a dead regex for any
    of the fifteen groups stays invisible behind whichever one the recording
    happens to trip."""
    # The template's own spelling, repeated past the cap.
    word = entry.source.lower()
    text = " ".join([f"She {word}."] * (slop.BEAT_REPEAT_MAX + 1))
    assert any(source == entry.source
               for source, _ in slop.overused_beats(text)), \
        f"{entry.source!r} never fires"


@pytest.mark.parametrize("entry", slop.STOCK_NAMES, ids=lambda e: e.source)
def test_every_stock_name_entry_actually_fires(entry):
    assert slop.found_stock_names(f"{entry.source} waited.", [], frozenset())


@pytest.mark.parametrize("entry", slop.NOT_X_BUT_Y, ids=lambda e: e.source[:30])
def test_every_construction_entry_actually_fires(entry):
    probe = {
        '"Not X, but Y" in every disguise': "It was not fear, but fury.",
        "it wasn't just X — it was Y":
            "It wasn't just a warning — it was a promise.",
        "she didn't X; she Y'd":
            "She didn't walk; she prowled.",
        "no longer X; now Y": "He was no longer a guest; now a debt.",
    }[entry.source]
    assert slop.found_constructions(probe), f"{entry.source!r} never fires"


@pytest.mark.parametrize("probe", [
    # The curly apostrophe a model is at least as likely to type as the
    # straight one the template uses. Built via chr() rather than a typed
    # curly character -- see evals/slop.py's module docstring for why.
    "It wasn" + chr(0x2019) + "t just a warning — it was a promise.",
    "She didn" + chr(0x2019) + "t walk; she prowled.",
])
def test_constructions_match_the_curly_apostrophe_too(probe):
    assert slop.found_constructions(probe)


def test_constructions_do_not_match_across_a_sentence_boundary():
    """A length bound alone would let this match. The span class excludes
    sentence terminators for exactly this reason."""
    assert not slop.found_constructions("She was not there. But Rowan was.")


def test_stock_name_is_exempt_when_established_as_a_single_token():
    assert not slop.found_stock_names("Selene shrugged.", [],
                                      slop.established_tokens(["Selene"]))


def test_stock_name_is_exempt_when_established_inside_a_multiword_name():
    """Exemption is per token: a cast that includes `Elara Vale` exempts the
    token `Elara` everywhere, including alone. Requiring the full name at the
    match site would flag a reply for obeying the template's own rule that
    established names are reproduced exactly."""
    established = slop.established_tokens(["Elara Vale"])
    assert not slop.found_stock_names("Elara shrugged.", [], established)


def test_stock_name_in_a_speaker_label_is_caught():
    """The blind spot split_reply creates: the label never reaches the body."""
    assert slop.found_stock_names("", ["Elara"], frozenset())


def test_beat_words_cap_counts_inflections_together():
    text = ("She murmured. He was murmuring. They murmur. "
            "The wind murmurs.")
    assert ("murmured", 4) in slop.overused_beats(text)


def test_beat_words_below_the_cap_do_not_fire():
    assert slop.overused_beats("She nodded. He nodded.") == []


_FLAT = "\n\n".join(
    ["The lamp was lit and the room was warm and the door was shut."] * 6
    + ["The chair was old and the rug was worn and the clock was slow."] * 6)

# 15 sentences over 8 paragraphs: comfortably past MIN_SENTENCES (12) and
# MIN_PARAGRAPHS (4), so the variance assertions below actually measure rather
# than short-circuiting on sample size. Sentence lengths run 1 to 33 words on
# purpose. No em dash appears at all, so em_dash_adjacent has nothing to find.
_VARIED = (
    "Rain.\n\n"
    "It came in off the water the way it always did at this hour, slow at "
    "first and then all at once, and Winifred pulled her coat tighter and "
    "swore at nobody in particular.\n\n"
    "Seraphine Vale did not move. She had been standing at the rail since "
    "before the fog closed in, and she had the look of somebody who intended "
    "to be standing there long after it lifted.\n\n"
    "\"You waited,\" Winifred said.\n\n"
    "\"I had nothing better on.\" The smuggler tipped her chin at the crates, "
    "stacked three high and sheeted against the weather, and let the silence "
    "do the asking for her. Somewhere below, the water knocked at the "
    "pilings.\n\n"
    "Winifred counted them. Twelve. That was four more than the manifest "
    "admitted to, and the manifest was the only honest thing she had been "
    "given all week.\n\n"
    "\"Well?\"\n\n"
    "Rowan came up the steps behind her with his bad shoulder set against the "
    "wind, and he did not answer until he had looked at every crate in the "
    "stack. \"Eight,\" he said. \"On paper.\"")


def test_measurable_fails_on_undersized_output():
    """Without this gate the whole case passes on an empty reply: no banned
    phrase occurs in nothing, and both variance checks have no sample."""
    ok, _ = slop.is_measurable("")
    assert not ok


def test_measurable_passes_on_a_full_reply():
    ok, _ = slop.is_measurable(_VARIED)
    assert ok


def test_variance_checks_pass_when_the_sample_is_too_small():
    """MANDATORY, not permitted. A one-paragraph reply has a paragraph
    coefficient of variation of exactly 0, which is below the threshold -- so a
    check that measured anyway would fail here too, and the `terse` recording
    could not declare slop.measurable alone."""
    ok, detail = slop.paragraph_variance("One short line.")
    assert ok
    assert "sample" in detail.lower()
    assert slop.sentence_variance("One short line.")[0]


def test_flat_prose_trips_both_variance_checks():
    assert not slop.sentence_variance(_FLAT)[0]
    assert not slop.paragraph_variance(_FLAT)[0]


def test_varied_prose_trips_neither_variance_check():
    assert slop.sentence_variance(_VARIED)[0]
    assert slop.paragraph_variance(_VARIED)[0]


def test_em_dash_in_consecutive_paragraphs_is_caught():
    assert slop.em_dash_adjacent("She \u2014 wait.\n\nHe \u2014 no.")


def test_em_dash_spaced_out_is_fine():
    assert not slop.em_dash_adjacent(
        "She \u2014 wait.\n\nNothing here.\n\nHe \u2014 no.")


# ------------------------------------------------------- the negative corpus
#
# Legitimate prose every detector must leave alone. It prevents regression on
# these exact fixtures and nothing more -- it is not an independent
# distribution and yields no statistical false-positive bound. A threshold
# tightened until it trips one of these has gone too far.

# Every passage clears MIN_SENTENCES and MIN_PARAGRAPHS. That is the whole
# point: a passage below the floor short-circuits both variance checks to a
# pass, and would place no constraint on VARIANCE_MIN at all -- a corpus that
# looks like protection and is not.
_LEGITIMATE = {
    "dialogue-heavy": (
        "\"Whose?\" Winifred asked.\n\n"
        "\"Mine.\"\n\n"
        "\"Since when?\"\n\n"
        "\"Since the tide turned and the harbourmaster stopped counting, which "
        "was a good while before you started asking me questions on my own "
        "pier in the rain.\"\n\n"
        "\"That is not an answer.\"\n\n"
        "\"It is the one you get.\" Seraphine Vale crouched, worked a nail "
        "loose from the nearest crate, and held it up to what light there "
        "was.\n\n"
        "\"Ship's iron.\"\n\n"
        "\"So?\"\n\n"
        "\"So it came off a hull, and hulls that lose their nails on my pier "
        "have generally lost something else first, which is the part you are "
        "going to want to hear about before the harbourmaster does.\"\n\n"
        "Winifred took the nail. It was cold. She turned it over twice, "
        "thinking about the manifest and the four crates that were not on it, "
        "and then she put it in her pocket without asking whether she could."),
    "deliberate fragments": (
        "Fog. Rope. The slap of water on stone.\n\n"
        "Winifred went down the steps counting, because counting was the only "
        "thing that had ever kept her steady, and she had needed steadying "
        "since the moment the letter came.\n\n"
        "Twelve steps. Then the boards.\n\n"
        "Somewhere out past the breakwater a bell went, once, and did not go "
        "again, and she stood in the dark a while listening for it anyway.\n\n"
        "Nothing. Wind. The creak of a mooring taking up slack.\n\n"
        "She had been told the pier was quiet at this hour and had believed "
        "it, which she was beginning to understand had been the point of "
        "telling her.\n\n"
        "A light, far out. Then not."),
    "incantatory refrain": (
        "By the salt she swore it. By the keel she swore it. By the cold black "
        "water under the boards she swore it, and meant every word of it, "
        "which was more than she could say for most of the promises she had "
        "made that season.\n\n"
        "Rowan listened the way people listen to weather.\n\n"
        "By the salt. By the keel. By the water.\n\n"
        "The old words had been said on this pier for longer than either of "
        "them had been alive, and they would go on being said here long after "
        "the two of them were done with it, which was rather the point of "
        "them.\n\n"
        "He said them back. Badly. She let it stand, because a promise said "
        "badly is still a promise, and because the tide was not going to wait "
        "for either of them to get the words right.\n\n"
        "By the salt. By the keel. By the water. That was the whole of it, and "
        "it had never needed to be more."),
    "terse action": (
        "The crate went over.\n\n"
        "Winifred caught the edge, took the weight badly, and felt something "
        "give in her shoulder that she would be paying for by morning.\n\n"
        "Rowan swore.\n\n"
        "Then he had the other side, and between them they walked it back "
        "from the drop, one careful pace at a time, until the boards stopped "
        "complaining underfoot and the thing sat where it was meant to sit.\n\n"
        "Her arm was shaking. She let it.\n\n"
        "\"Again?\"\n\n"
        "\"No.\"\n\n"
        "They stood there in the wet with the stack between them and the "
        "water, and neither of them said the obvious thing, which was that "
        "whatever was in it had been worth somebody's while to load in "
        "the dark.\n\n"
        "Rowan sat down on the boards. He rubbed the shoulder. Winifred "
        "watched the fog come apart over the breakwater and put together, for "
        "the first time that week, an order of events that actually "
        "accounted for the four crates nobody would admit to.\n\n"
        "It was not a comfortable order of events. She kept it anyway."),
}


@pytest.mark.parametrize("label", sorted(_LEGITIMATE))
def test_negative_corpus_trips_nothing(label):
    text = _LEGITIMATE[label]
    assert slop.found_phrases(text) == []
    assert slop.found_stock_names(text, [], frozenset()) == []
    assert slop.overused_beats(text) == []
    assert slop.found_constructions(text) == []
    assert not slop.em_dash_adjacent(text)
    assert slop.sentence_variance(text)[0]
    assert slop.paragraph_variance(text)[0]


def _grade(text, established=frozenset()):
    return {c.name: c for c in graders.grade_slop(
        text, frozenset({"Winifred"}), established, _rendered_block())}


def test_grade_slop_names_all_nine_checks():
    checks = _grade(_VARIED)
    assert set(checks) == {
        "slop.list_current", "slop.measurable", "slop.phrases",
        "slop.stock_names", "slop.beat_words", "slop.not_x_but_y",
        "slop.sentence_variance", "slop.paragraph_uniformity",
        "slop.em_dash_spacing"}


def test_grade_slop_passes_clean_varied_prose():
    assert all(c.ok for c in _grade(_VARIED).values())


def test_grade_slop_fails_only_measurable_on_a_collapsed_reply():
    """The set-equality property the `terse` recording depends on."""
    failed = {n for n, c in _grade("She nodded.").items() if not c.ok}
    assert failed == {"slop.measurable"}


def test_grade_slop_catches_a_stock_name_in_a_speaker_label():
    text = _VARIED + "\n\n**Elara:** Evening."
    assert not _grade(text)["slop.stock_names"].ok


# ------------------------------------------------------- natural-prose case

def test_natural_prose_case_is_registered():
    """Red before Step 3. Without it, forgetting the builder, the grader or the
    CASES entry leaves the whole suite green -- the case simply would not run,
    and nothing else in this file would notice."""
    case = cases.BY_ID["natural-prose"]
    assert case.grade is cases.grade_natural_prose
    assert case.build is cases.build_natural_prose
    assert {r.variant for r in case.recordings} == {
        "compliant", "slop", "flat", "terse"}


def test_natural_prose_case_declares_the_right_failure_sets():
    """The set-equality property the whole case rests on, asserted here as well
    as by replay so a silently widened declaration is caught in one place."""
    by_variant = {r.variant: set(r.expect_fail)
                  for r in cases.BY_ID["natural-prose"].recordings}
    assert by_variant["compliant"] == set()
    assert by_variant["slop"] == {"slop.phrases", "slop.stock_names",
                                  "slop.beat_words", "slop.not_x_but_y"}
    assert by_variant["flat"] == {"slop.sentence_variance",
                                  "slop.paragraph_uniformity",
                                  "slop.em_dash_spacing"}
    assert by_variant["terse"] == {"slop.measurable"}


def test_prompt_natural_prose_fails_when_the_section_is_absent():
    """The check that closes the content hole verify_templates structurally
    cannot: that harness keeps an independent section-order mirror, so a
    DELETED SECTIONS entry already fails there -- but it never pins template
    text, so an emptied template renders to nothing on both sides and passes."""
    messages = [{"role": "system", "content": "Nothing of the sort."}]
    checks = graders.grade_prompt_section(
        messages, "natural_prose", "scene/sections/natural_prose.j2")
    assert not checks[0].ok
    assert checks[0].name == "prompt.natural_prose"
