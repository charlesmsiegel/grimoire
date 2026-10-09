"""Unit tests for evals/graders.py: every individual check, proven to fire.

test_evals.py proves each recorded counterexample makes its CASE fail. That is
a coarse instrument — a case with six checks fails if any one of them bites,
so a check that silently stopped working would hide behind its neighbours.
These tests pin each check separately, on the smallest input that isolates it.

No store, no GRIMOIRE_HOME: the graders are pure. The one exception builds the
decide-continuity-reconcile case in a throwaway store, to tie this file's constants
to it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from grimoire import decisions, prompts
from grimoire.store import absorb, calendars, scenes, suggest
from grimoire.store.continuity import reconcile

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases, graders, runner, slop  # noqa: E402

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


# ----------------------------------------------------------------- identity

#: The decide-continuity-identity case's three rows, as the grader is handed them:
#: what each row should be decided as, and which check carries that verdict.
IDENTITY_EXPECTED = {
    "r1": {"decision": "existing", "id": "find-the-ledger", "check": "same_obligation"},
    "r2": {"decision": "new", "id": "", "check": "distinct"},
    "r3": {"decision": "new", "id": "", "check": "continuation"},
}


# ------------------------------------------------- identity, as decision items

def _identity_decision_rows() -> list[dict]:
    """The decide-continuity-identity case's three rows, `prompt_rows()`-shaped,
    each offered the one record its case is about."""
    signals = {"title_equal": False, "slug_equal": False, "tokens": 0.4, "chars": 0.5,
               "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical"}
    rows = []
    for n, (title, rid) in enumerate((("Recover the harbour ledger", "find-the-ledger"),
                                      ("Who bribes the harbourmaster",
                                       "the-saltmarch-smuggling"),
                                      ("Seraphine's debt to Mara comes due",
                                       "seraphines-debts")), start=1):
        rows.append({"key": f"r{n}", "kind": "thread", "title": title, "beat": "b",
                     "status": "open", "commitment_kind": "", "due": "", "quote": "",
                     "speaker": "", "certainty": None, "why_new": "",
                     "distinguished_from": [],
                     "candidates": [{"id": rid, "title": rid, "status": "open", "kind": "",
                                     "due": "", "latest_beat": "", "earlier": [],
                                     "signals": dict(signals)}]})
    return rows


_D1 = {"decision": "existing:find-the-ledger"}
_D2 = {"decision": "new"}
_D3 = {"decision": "new"}
_WHY = ("Same ledger.", "A different question.", "It grew out of the debts.")


def _identity_decided(*answers: dict, rationales=_WHY) -> set[str]:
    from grimoire.store.continuity import identity
    from tests.llm_fakes import decision_reply

    rows = _identity_decision_rows()
    items = identity.build_items(rows, {})
    return failed(graders.grade_identity_decision(
        decision_reply(*answers, rationales=rationales), items, rows, IDENTITY_EXPECTED))


def test_identity_decision_compliant_passes():
    from grimoire.store.continuity import identity
    from tests.llm_fakes import decision_reply

    rows = _identity_decision_rows()
    items = identity.build_items(rows, {})
    checks = graders.grade_identity_decision(decision_reply(_D1, _D2, _D3, rationales=_WHY),
                                             items, rows, IDENTITY_EXPECTED)
    assert failed(checks) == set()
    assert [c.name for c in checks] == [
        "identity.json", "identity.covers_rows", "identity.enum", "identity.known_ids",
        "identity.same_obligation", "identity.distinct", "identity.continuation"]
    # The reason is display-only: a reply with no rationale is still graded clean.
    assert _identity_decided(_D1, _D2, _D3, rationales=()) == set()


def test_identity_decision_undecodable_fails_json_only():
    from grimoire.store.continuity import identity

    rows = _identity_decision_rows()
    items = identity.build_items(rows, {})
    for text in ("Row r1 looks like the ledger thread.",
                 '{"0": {"answers": {"decision": "existing:find-the-le'):
        checks = graders.grade_identity_decision(text, items, rows, IDENTITY_EXPECTED)
        assert [(c.name, c.ok) for c in checks] == [("identity.json", False)], text


def test_identity_decision_merged_rows_fail_their_own_verdicts():
    merged = (_D1, {"decision": "existing:the-saltmarch-smuggling"},
              {"decision": "existing:seraphines-debts"})
    assert _identity_decided(*merged) == {"identity.distinct", "identity.continuation"}


def test_identity_decision_unoffered_id_fails_known_ids():
    # An `existing` folded with an id offered nowhere, or with none, is no
    # option: unread, and told from an unknown word by its raw spelling, so
    # it fails `known_ids` rather than `enum`, and the row it was given on
    # gets the wrong verdict.
    for unoffered in ("existing:maras-map", "existing", "Existing: maras-map"):
        assert _identity_decided({"decision": unoffered}, _D2, _D3) == {
            "identity.known_ids", "identity.same_obligation"}, unoffered
    # The ref form and a cased spelling are the offered id, as the app reads them.
    for named in ("thread:find-the-ledger", "Find The Ledger"):
        assert _identity_decided({"decision": f"existing:{named}"}, _D2, _D3) == set()


def test_identity_decision_unknown_word_fails_enum():
    assert _identity_decided(_D1, {"decision": "maybe"}, _D3) == {
        "identity.enum", "identity.distinct"}


def test_identity_decision_missing_item_fails_covers_rows_alone():
    from grimoire.store.continuity import identity
    from tests.llm_fakes import decision_reply

    rows = _identity_decision_rows()
    items = identity.build_items(rows, {})
    text = decision_reply(_D1, _D2, rationales=_WHY)   # item 2 never answered
    checks = graders.grade_identity_decision(text, items, rows, IDENTITY_EXPECTED)
    assert failed(checks) == {"identity.covers_rows"}
    assert "identity.continuation" not in {c.name for c in checks}


# ---------------------------------------------------------------- reconcile

#: The decide-continuity-reconcile case's seven candidates as the grader is handed
#: them. `build_payload` keys candidates c1... in the order they are sent, so
#: §28.10 case N is sent as ``c<N-1>``: c1 and c2 are thread pairs, c3 a cross
#: pair (A the commitment, B the thread, as refs sort), c4 and c5 thread
#: closures, c6 and c7 commitment resolutions.
RECONCILE_VOCAB = {"c1": reconcile.DECISIONS["same_thread"],
                   "c2": reconcile.DECISIONS["same_thread"],
                   "c3": reconcile.DECISIONS["cross"],
                   "c4": reconcile.DECISIONS["thread"],
                   "c5": reconcile.DECISIONS["thread"],
                   "c6": reconcile.DECISIONS["commitment"],
                   "c7": reconcile.DECISIONS["commitment"]}
RECONCILE_KNOWN = {"001--saltmarch-docks", "002--realm-road", "003--the-pier-at-dusk"}
RECONCILE_EXPECTED = {
    "c1": {"check": "distinct", "decisions": ("distinct", "related")},
    "c2": {"check": "continuation", "decisions": ("continuation", "subthread"),
           "from": {"continuation": "B", "subthread": "B"}},
    "c3": {"check": "cross_type", "decisions": ("pays_off", "related"),
           "from": {"pays_off": "B"}},
    "c4": {"check": "close", "decisions": ("close",)},
    "c5": {"check": "keep_open", "decisions": ("keep_open",)},
    "c6": {"check": "fulfilled", "decisions": ("fulfilled",)},
    "c7": {"check": "unproven", "decisions": ("keep_open", "uncertain")},
}

def test_reconcile_decision_scores_every_unmerged_answer_the_prompt_allows():
    """§28.10's point for cases 2 and 3 is that neither pair is merged, so the
    case's own verdicts take every such answer ("related" whenever two records
    bear on each other, "continuation" or "subthread" with no rule between
    them), and the concrete record still has to be the ``from`` of either
    directed word."""
    assert _reconcile_decided(c1={"decision": "related"}) == set()
    assert _reconcile_decided(c2={"decision": "subthread_b_of_a"}) == set()
    assert _reconcile_decided(c2={"decision": "subthread_a_of_b"}) == {
        "reconcile.continuation"}


def test_reconcile_decision_case_is_what_these_tests_grade_and_the_app_keeps(
        tmp_path, monkeypatch):
    """The constants above are the case's own, and a reply the grader passes
    is one the app keeps word for word: the compliant recording, read by
    `reconcile.proposals_of` against the payload the case built, downgrades
    nothing to ``uncertain`` that it did not already say."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = cases.BY_ID["decide-continuity-reconcile"]
    ctx = runner.prepare(case)
    assert ctx["vocab"] == RECONCILE_VOCAB
    assert ctx["known"] == RECONCILE_KNOWN
    assert ctx["expected"] == {k: {**v, "from": v.get("from", {})}
                               for k, v in RECONCILE_EXPECTED.items()}

    text = case.baseline.path(case.id).read_text(encoding="utf-8")
    results = decisions.parse(text, ctx["items"], explain=True)
    kept = reconcile.proposals_of(ctx["payload"], results)
    said = {c["id"]: reconcile.unfolded(result.answers[reconcile.DECISION_ID].answer)[0]
            for c, result in zip(ctx["payload"]["candidates"], results, strict=True)}
    assert {key: kept[key]["decision"] for key in kept} == said


# ------------------------------------------------ reconcile, as decision items

#: The case's seven candidates, `build_payload`-shaped with no store: every
#: scene is a recent chronicle line, as in the case, so every item shows all
#: three and asks three evidence questions.
_RECONCILE_REFS = {
    "c1": ("same_thread", ("thread:the-saltmarch-smuggling",
                           "thread:who-bribes-the-saltmarch-harbourmaster")),
    "c2": ("same_thread", ("thread:seraphines-debts",
                           "thread:what-seraphines-debt-to-mara-costs-her")),
    "c3": ("cross", ("commitment:pay-mara-for-finding-the-ledger", "thread:find-the-ledger")),
    "c4": ("thread", ("thread:maras-map",)),
    "c5": ("thread", ("thread:winifreds-chart",)),
    "c6": ("commitment", ("commitment:maras-oath",)),
    "c7": ("commitment", ("commitment:seraphines-berth",)),
}


def _reconcile_decision_payload() -> dict:
    known = sorted(RECONCILE_KNOWN)
    cands = []
    for key, (vocab, refs) in _RECONCILE_REFS.items():
        records = [{"letter": letter, "ref": ref, "type": "", "line": ref, "beats": [],
                    "pressure": "", "links": [], "actors": []}
                   for letter, ref in zip("AB", refs, strict=False)]
        cands.append({"key": key, "id": f"candidate-{key}", "vocabulary": vocab,
                      "records": records, "signal_text": ""})
    return {"now": "", "chronicle": [{"id": sid, "one_line": sid} for sid in known],
            "recent": known, "candidates": cands, "known_scenes": known}


_RD = {
    "c1": {"decision": "distinct"},
    "c2": {"decision": "continuation_b_of_a"},
    "c3": {"decision": "pays_off_b_to_a"},
    "c4": {"decision": "close", "evidence_scene": "003--the-pier-at-dusk"},
    "c5": {"decision": "keep_open"},
    "c6": {"decision": "fulfilled", "evidence_scene": "002--realm-road"},
    "c7": {"decision": "uncertain"},
}


def _reconcile_decided(*, skip: str = "", **over: dict) -> set[str]:
    from tests.llm_fakes import decision_reply

    payload = _reconcile_decision_payload()
    items = reconcile.build_items(payload)
    answers = []
    for item, cand in zip(items, payload["candidates"], strict=True):
        if cand["key"] == skip:
            break
        answer = {q.id: None for q in item.questions}
        answers.append({**answer, **_RD[cand["key"]], **over.get(cand["key"], {})})
    return failed(graders.grade_reconcile_decision(decision_reply(*answers), items, payload,
                                                   RECONCILE_EXPECTED))


def test_reconcile_decision_compliant_passes():
    from tests.llm_fakes import decision_reply

    payload = _reconcile_decision_payload()
    items = reconcile.build_items(payload)
    assert all(len([q for q in item.questions if q.id in reconcile.EVIDENCE_IDS]) == 3
               for item in items)
    answers = [{**{q.id: None for q in item.questions}, **_RD[c["key"]]}
               for item, c in zip(items, payload["candidates"], strict=True)]
    checks = graders.grade_reconcile_decision(decision_reply(*answers), items, payload,
                                              RECONCILE_EXPECTED)
    assert failed(checks) == set()
    assert [c.name for c in checks] == [
        "reconcile.json", "reconcile.covers", "reconcile.enum", "reconcile.evidence",
        "reconcile.distinct", "reconcile.continuation", "reconcile.cross_type",
        "reconcile.close", "reconcile.keep_open", "reconcile.fulfilled",
        "reconcile.unproven"]
    # No rationale is required of a status word (I4): none was given above.
    assert '"rationale"' not in decision_reply(*answers)


def test_reconcile_decision_undecodable_fails_json_only():
    payload = _reconcile_decision_payload()
    items = reconcile.build_items(payload)
    for text in ("Mara's map looks finished to me.",
                 '{"0": {"answers": {"decision": "distinct", "evidence_scene": nu'):
        checks = graders.grade_reconcile_decision(text, items, payload, RECONCILE_EXPECTED)
        assert [(c.name, c.ok) for c in checks] == [("reconcile.json", False)], text


def test_reconcile_decision_merged_pairs_fail_their_own_verdicts():
    merged = {"decision": "duplicate_b_into_a"}
    assert _reconcile_decided(c1=merged, c2=merged) == {"reconcile.distinct",
                                                        "reconcile.continuation"}
    # The concrete record must still be the `from` of a continuation.
    assert _reconcile_decided(c2={"decision": "continuation_a_of_b"}) == {
        "reconcile.continuation"}


def test_reconcile_decision_eager_lifecycle_fails_keep_open_and_unproven():
    cited = {"evidence_scene": "001--saltmarch-docks"}
    assert _reconcile_decided(c5={"decision": "close", **cited},
                              c7={"decision": "fulfilled", **cited}) == {
        "reconcile.keep_open", "reconcile.unproven"}


def test_reconcile_decision_unfounded_closure_fails_evidence_alone():
    """The verdict reads the raw word, so an unfounded closure trips the
    evidence check alone; a scene in any slot founds it, and a scene the item
    does not offer is no scene."""
    assert _reconcile_decided(c4={"evidence_scene": None}) == {"reconcile.evidence"}
    assert _reconcile_decided(c4={"evidence_scene": "999--nowhere"}) == {
        "reconcile.evidence"}
    assert _reconcile_decided(c4={"evidence_scene": None,
                                  "evidence_scene_3": "003--the-pier-at-dusk"}) == set()
    assert _reconcile_decided(c7={"decision": "broken"}) == {"reconcile.evidence",
                                                            "reconcile.unproven"}


def test_reconcile_decision_timid_fails_the_three_verdicts_nothing_else_reaches():
    assert _reconcile_decided(c3={"decision": "distinct"},
                              c4={"decision": "keep_open", "evidence_scene": None},
                              c6={"decision": "keep_open", "evidence_scene": None}) == {
        "reconcile.cross_type", "reconcile.close", "reconcile.fulfilled"}


def test_reconcile_decision_unknown_word_fails_enum():
    assert _reconcile_decided(c3={"decision": "duplicate"}) == {"reconcile.enum",
                                                               "reconcile.cross_type"}
    # A direction the item does not offer is no option either: pays_off runs
    # from the thread (B) to the commitment (A) alone.
    assert _reconcile_decided(c3={"decision": "pays_off_a_to_b"}) == {
        "reconcile.enum", "reconcile.cross_type"}


def test_reconcile_decision_missing_item_fails_covers_alone():
    checks = _reconcile_decided(skip="c7")
    assert checks == {"reconcile.covers"}


# --------------------------------------------------------- scene suggestions
#
# Pure over a hand-built snapshot and the real Gregorian provider: two focused
# threads, a third unfocused one, and a batch anchor `before` the coronation,
# ten days after NOW.

GREG = calendars.get_provider({"provider": "gregorian", "region": "", "custom_holidays": [],
                               "anchor": None})
SUGGEST_NOW = "2026-05-10"
MAP, LEDGER, DEBTS = "thread:maras-map", "thread:find-the-ledger", "thread:seraphines-debts"
CORONATION = "event:the-coronation"
CORONATION_DAY = "2026-05-20"


def _suggest_driver(ref: str, label: str, state: str = "ok",
                    in_days: int | None = None) -> dict:
    return {"ref": ref, "kind": ref.split(":", 1)[0], "label": label, "summary": "",
            "actors": [], "status": "", "time_anchors": [], "links": [], "dormancy": None,
            "pressure": {"state": state, "in_days": in_days, "friendly": ""}}


def _suggest_snapshot() -> dict:
    fixed = calendars.fixed_of(GREG, CORONATION_DAY)
    now = calendars.fixed_of(GREG, SUGGEST_NOW)
    return {"now": SUGGEST_NOW, "fixed": now, "near_days": 7,
            "open_threads": [{"ref": r, "aliases": []} for r in (MAP, LEDGER, DEBTS)],
            "commitments": [], "timeline": [], "links": [],
            "driver_index": [_suggest_driver(MAP, "Mara's map"),
                             _suggest_driver(LEDGER, "Find the ledger"),
                             _suggest_driver(DEBTS, "Seraphine's debts"),
                             _suggest_driver(CORONATION, "The coronation", "upcoming", 10)],
            "anchors": [{"ref": CORONATION, "kind": "event", "label": "The coronation",
                         "native": CORONATION_DAY, "friendly": "20 May 2026",
                         "fixed": fixed, "in_days": fixed - now, "precision": "exact"}]}


SUGGEST_BEFORE = suggest.Controls(focus=(MAP, LEDGER), time_mode="anchor",
                                  anchor=CORONATION, relation="before")
SUGGEST_ON = suggest.Controls(time_mode="anchor", anchor=CORONATION, relation="on")


def _suggestion(title: str, date, *drivers: tuple[str, str], anchor: str = CORONATION,
                relation: str = "before") -> dict:
    out = {"title": title, "premise": f"{title}, played out.", "cast": [], "location": "",
           "drivers": [{"ref": r, "action": a} for r, a in drivers],
           "time_anchor": {"ref": anchor, "relation": relation}}
    if date is not None:
        out["date"] = date
    return out


def _compliant(**dates: str) -> list[dict]:
    return [_suggestion("Mara's map", dates.get("map", "2026-05-12"), (MAP, "advance")),
            _suggestion("Find the ledger", dates.get("ledger", "2026-05-15"),
                        (LEDGER, "advance")),
            _suggestion("A quiet night at Saltmarch", dates.get("quiet", "2026-05-18"))]


def _suggest(rows, controls=SUGGEST_BEFORE) -> set[str]:
    text = json.dumps({"suggestions": rows})
    return failed(graders.grade_scene_suggestions(text, _suggest_snapshot(), controls, GREG))


def test_suggest_compliant_passes():
    checks = graders.grade_scene_suggestions(json.dumps({"suggestions": _compliant()}),
                                             _suggest_snapshot(), SUGGEST_BEFORE, GREG)
    assert failed(checks) == set()
    # `on_derived` is asked only of a batch anchored `on`
    assert {c.name for c in checks} == {
        "suggest.json", "suggest.known_refs", "suggest.anchor_known",
        "suggest.focus_coverage", "suggest.focus_spread", "suggest.distinct",
        "suggest.date_consistent"}
    # a bare array is the app's tolerated deviation, and so the grader's
    assert failed(graders.grade_scene_suggestions(
        json.dumps(_compliant()), _suggest_snapshot(), SUGGEST_BEFORE, GREG)) == set()


def test_suggest_prose_fails_json_only():
    """Nothing decodes, or what decodes holds no suggestion: the rest is not
    reported rather than failed."""
    for text in ("Mara's map would make a fine scene.", '{"suggestions": []}'):
        checks = graders.grade_scene_suggestions(text, _suggest_snapshot(),
                                                 SUGGEST_BEFORE, GREG)
        assert [(c.name, c.ok) for c in checks] == [("suggest.json", False)], text


def test_suggest_unknown_ref_fails_known_refs():
    """Scored raw: `claim` drops an unknown ref or a wrong action, so the
    claimed set alone could never show the miss."""
    rows = _compliant()
    rows[0]["drivers"].append({"ref": "thread:maras-compass", "action": "advance"})
    assert _suggest(rows) == {"suggest.known_refs"}
    rows = _compliant()
    rows[1]["drivers"].append({"ref": DEBTS, "action": "anchor"})     # wrong for a thread
    assert _suggest(rows) == {"suggest.known_refs"}
    rows = _compliant()
    rows[2]["drivers"] = "thread:maras-map"                           # not a list
    assert _suggest(rows) == {"suggest.known_refs"}


def test_suggest_unknown_anchor_fails_anchor_known():
    """The batch anchor overrides the model's, so nothing downstream sees the
    invented one: only the raw reply can."""
    rows = _compliant()
    rows[2]["time_anchor"] = {"ref": "event:the-debt", "relation": "before"}
    assert _suggest(rows) == {"suggest.anchor_known"}
    rows = _compliant()
    rows[0]["time_anchor"] = None                                     # absent is fine
    del rows[1]["time_anchor"]
    assert _suggest(rows) == set()


def test_suggest_clone_fails_coverage_and_distinct():
    clones = [_suggestion(f"Mara's map, take {n}", f"2026-05-1{n}", (MAP, "advance"))
              for n in (2, 3, 4)]
    assert _suggest(clones) == {"suggest.focus_coverage", "suggest.distinct"}
    # one suggestion is no spread, whatever it claims
    assert _suggest(_compliant()[:1]) == {"suggest.focus_coverage", "suggest.distinct"}
    # differing claims under one casefolded title are still a clone
    rows = _compliant()
    rows[1]["title"] = "MARA'S MAP"
    assert _suggest(rows) == {"suggest.distinct"}
    # the batch anchor's auto-added entry does not make two claims differ
    rows = [_suggestion("Mara's map", "2026-05-12", (MAP, "advance")),
            _suggestion("Mara's map again", "2026-05-13", (MAP, "advance"),
                        (CORONATION, "anchor"))]
    assert "suggest.distinct" in _suggest(rows)


def test_suggest_one_premise_fails_distinct_and_spread():
    """§28.10 case 9 is about premises and spread, not titles and claim sets:
    a batch of one premise under three titles, its one focused card holding
    both drivers, covers every focus ref and differs in every title and
    claim, and is still a clone."""
    premise = "Mara spreads her torn map across a crate and asks after the ledger."
    rows = [_suggestion("Mara's map", "2026-05-12", (MAP, "advance"), (LEDGER, "advance")),
            _suggestion("Mara's map, by lamplight", "2026-05-13"),
            _suggestion("Mara's map, at dawn", "2026-05-14")]
    for row in rows:
        row["premise"] = premise
    assert _suggest(rows) == {"suggest.distinct", "suggest.focus_spread"}
    # both focus drivers on one card, every premise its own: the spread alone
    rows = _compliant()
    rows[0]["drivers"].append({"ref": LEDGER, "action": "advance"})
    rows[1]["drivers"] = []
    assert _suggest(rows) == {"suggest.focus_spread"}
    # focus spread over two cards, two premises one reworded clause apart
    rows = _compliant()
    rows[0]["premise"] = "Mara spreads her torn map across a crate on the docks."
    rows[1]["premise"] = "Mara spreads her torn map across a barrel on the docks."
    assert _suggest(rows) == {"suggest.distinct"}
    # premise text is compared casefolded and by word, not by spelling
    rows = _compliant()
    rows[1]["premise"] = rows[0]["premise"].upper() + "!"
    assert _suggest(rows) == {"suggest.distinct"}
    # a card that claims one focus ref beside a card that claims both is spread
    rows = _compliant()
    rows[0]["drivers"].append({"ref": LEDGER, "action": "advance"})
    assert _suggest(rows) == set()
    # with one focus ref there is nothing to spread
    one = suggest.Controls(focus=(MAP,), time_mode="anchor", anchor=CORONATION,
                           relation="before")
    rows = _compliant()
    rows[1]["drivers"] = []
    assert _suggest(rows, one) == set()


def test_suggest_dropped_card_does_not_cover_focus():
    """A card the app drops (blank title or premise) is no card the player
    sees, so it serves no focus and makes no batch distinct -- the grader
    reads the set `suggest.parse_output` keeps, through the same predicate."""
    rows = _compliant()
    rows[1]["premise"] = ""                                 # the only ledger card
    assert _suggest(rows) == {"suggest.focus_coverage"}
    rows = _compliant()
    rows[1]["title"] = "   "
    assert _suggest(rows) == {"suggest.focus_coverage"}
    # two of three dropped leaves a single card: no spread either
    rows = _compliant()
    rows[1]["premise"] = rows[2]["premise"] = ""
    assert _suggest(rows) == {"suggest.focus_coverage", "suggest.distinct"}
    # every card dropped: nothing the app would show
    rows = _compliant()
    for row in rows:
        row["premise"] = ""
    checks = graders.grade_scene_suggestions(json.dumps({"suggestions": rows}),
                                             _suggest_snapshot(), SUGGEST_BEFORE, GREG)
    assert [(c.name, c.ok) for c in checks] == [("suggest.json", False)]
    # a dropped card's raw content is not graded: it never reaches the player
    rows = _compliant()
    rows.append(_suggestion("", "2026-05-30", ("thread:maras-compass", "advance"),
                            anchor="event:the-debt"))
    assert _suggest(rows) == set()


def test_suggest_is_card_matches_what_parse_output_keeps():
    assert suggest.is_card({"title": "Mara's map", "premise": "Played out."})
    for entry in ({"title": "", "premise": "x"}, {"title": "x", "premise": "  "},
                  {"title": "x"}, "Mara's map", None, ["x"]):
        assert not suggest.is_card(entry), entry


def test_suggest_bad_date_fails_date_consistent():
    """After D under `before` is rejected; so is no date at all, since a
    `before` batch has nothing to derive one from."""
    assert _suggest(_compliant(map="2026-05-22", ledger="2026-05-25",
                               quiet="2026-05-20")) == {"suggest.date_consistent"}
    rows = _compliant()
    del rows[2]["date"]
    assert _suggest(rows) == {"suggest.date_consistent"}
    assert _suggest(_compliant(quiet="2026-05-09")) == {"suggest.date_consistent"}


def test_suggest_friendly_form_fails_notation():
    """The tolerant parser reads the date the way the prompt displays it, and
    the anchor rule accepts the day it reads -- so only the raw string can say
    it was not written in the calendar's own notation."""
    snap = _suggest_snapshot()
    n = suggest.normalize_date(GREG, SUGGEST_NOW, "12 May 2026")
    assert suggest.check_date(GREG, snap, SUGGEST_BEFORE,
                              {"ref": CORONATION, "relation": "before"}, n) == (
        "2026-05-12", False)
    assert _suggest(_compliant(map="12 May 2026")) == {"suggest.date_consistent"}


def test_suggest_on_derives_whatever_the_model_wrote(monkeypatch):
    """Under `on` the date is the anchor's own, so a wrong date, none and the
    friendly form all pass -- and `on_derived` holds the derivation itself."""
    rows = [_suggestion("Mara's map", "2026-05-12", (MAP, "advance"), relation="on"),
            _suggestion("Find the ledger", None, (LEDGER, "advance"), relation="on"),
            _suggestion("Seraphine's debts", "20 May 2026", (DEBTS, "advance"),
                        relation="on")]
    checks = graders.grade_scene_suggestions(json.dumps({"suggestions": rows}),
                                             _suggest_snapshot(), SUGGEST_ON, GREG)
    assert failed(checks) == set()
    assert "suggest.on_derived" in {c.name for c in checks}

    # A parser that kept the model's date instead of deriving one: the
    # wrong date and the missing one both show.
    monkeypatch.setattr(graders.suggest, "check_date",
                        lambda _p, _s, _c, _a, date: (date, False))
    assert _suggest(rows, SUGGEST_ON) == {"suggest.on_derived", "suggest.date_consistent"}


@pytest.mark.parametrize("case_id,variant,dates", [
    ("scene-suggestions", "compliant", [("5-Thaw-09", False), ("5-Thaw-12", False),
                                        ("5-Thaw-15", False)]),
    ("scene-suggestions", "bad-date", [("", True)] * 3),
    ("scene-suggestions-anchor-on", "compliant", [("5-Thaw-17", False)] * 3),
])
def test_suggest_cases_grade_what_the_app_parses(tmp_path, monkeypatch, case_id, variant,
                                                 dates):
    """The recordings, read by the production `parse_output` against the
    capture the case built: what the grader passes the app keeps, in the
    plugin's own notation, and what it fails the app blanks as rejected."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = cases.BY_ID[case_id]
    ctx = runner.prepare(case)
    text = next(r for r in case.recordings if r.variant == variant).path(case_id).read_text(
        encoding="utf-8")
    rows = suggest.parse_output(text, ctx["cid"], snapshot=ctx["snapshot"],
                                controls=ctx["controls"])
    assert sorted((r["date"], r["date_rejected"]) for r in rows) == dates
    assert all(r["time_anchor"]["ref"] == cases.SUGGEST_ANCHOR for r in rows)


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
    """The prompt check tracks both current contribution targets."""
    budget = {"words": 150, "paragraphs": 1}
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
    budget = {"words": 150, "paragraphs": 1}
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
    return prompts.render("styles/natural-prose-legacy.md")


@pytest.mark.parametrize("entry", slop.ALL_ENTRIES, ids=lambda e: e.source[:40])
def test_every_entry_source_is_still_in_the_template(entry):
    """The one-way drift guard, per entry so a failure names the culprit.

    Grading a phrase the selected guide has stopped banning is the failure this catches.
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
    assert slop.is_measurable(text)[0], (
        "a corpus passage below the sample floor makes the two variance "
        "assertions below vacuous -- they short-circuit to a pass")
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


@pytest.mark.parametrize("count,passes", [(1, True), (150, True), (151, False), (0, False)])
def test_actor_length_scores_exact_prose_ceiling(count, passes):
    ctx = {"budget": {"words": 150, "paragraphs": 1}, "messages": []}
    checks = cases.grade_scene_length(ctx, _words(count))
    assert next(c.ok for c in checks if c.name == "length.words") is passes


def test_actor_length_excludes_preparation_and_trailing_controls():
    ctx = {"budget": {"words": 150, "paragraphs": 1}, "messages": []}
    output = ('```perception\n' + _words(70) + '\n```\n' + _words(140)
              + '\n```state\n{}\n```\n```handoff\n{"next":null}\n```')
    checks = cases.grade_scene_length(ctx, output)
    assert next(c.ok for c in checks if c.name == "length.words")
    assert next(c.ok for c in checks if c.name == "length.paragraphs")


# --------------------------------------------------------------- decisions

def _decision_items():
    return (decisions.Item("Seraphine pays Mara on the Saltmarch pier.",
                           (decisions.Predicate("over", "Is the scene over?"),)),)


def _decision(over, rationale="The debt is paid.") -> str:
    return json.dumps({"0": {"answers": {"over": over}, "rationale": rationale}})


def test_grade_decision_passes_a_right_answer_with_a_reason():
    checks = graders.grade_decision(_decision(True), _decision_items(),
                                    explain=True, question="over", expected=True)
    assert [c.name for c in checks] == ["decide.json", "decide.answer", "decide.rationale"]
    assert failed(checks) == set()


def test_grade_decision_short_circuits_when_nothing_decodes():
    for text in ("", "no idea", '{"0": {"answers": {"over": true}, "rationale": "The de'):
        checks = graders.grade_decision(text, _decision_items(), explain=True,
                                        question="over", expected=True)
        assert [(c.name, c.ok) for c in checks] == [("decide.json", False)], text


def test_grade_decision_fails_the_wrong_answer_alone():
    checks = graders.grade_decision(_decision(False), _decision_items(),
                                    explain=True, question="over", expected=True)
    assert failed(checks) == {"decide.answer"}


def test_grade_decision_reads_an_unreadable_answer_as_wrong_not_as_a_default():
    """The string "true" is not a predicate's answer, and `1` is not `True`:
    the parser reads both as `None`, and the check compares types as well."""
    for over in ("true", 1, None):
        checks = graders.grade_decision(_decision(over), _decision_items(),
                                        explain=True, question="over", expected=True)
        assert failed(checks) == {"decide.answer"}, over


def test_grade_decision_fails_a_missing_rationale_alone():
    for rationale in ("", "   "):
        checks = graders.grade_decision(_decision(True, rationale), _decision_items(),
                                        explain=True, question="over", expected=True)
        assert failed(checks) == {"decide.rationale"}, rationale
    bare = json.dumps({"0": {"answers": {"over": True}}})
    assert failed(graders.grade_decision(bare, _decision_items(), explain=True,
                                         question="over", expected=True)) == {"decide.rationale"}


def test_grade_decision_reads_a_native_items_rationale_as_not_applicable():
    """A native endpoint is asked for no rationale: its item's
    `decide.rationale` passes, visibly, as `NATIVE_RATIONALE` -- and the
    answer is still graded. A cap (voice drift's) is not applied to nothing."""
    bare = json.dumps({"0": {"answers": {"over": True}}})
    for max_rationale in (None, 10):
        checks = graders.grade_decision(bare, _decision_items(), explain=True,
                                        question="over", expected=True,
                                        max_rationale=max_rationale, native=True)
        assert [c.name for c in checks] == ["decide.json", "decide.answer",
                                            "decide.rationale"]
        assert failed(checks) == set()
        assert checks[-1].detail == graders.NATIVE_RATIONALE
    wrong = graders.grade_decision(_decision(False, ""), _decision_items(), explain=True,
                                   question="over", expected=True, native=True)
    assert failed(wrong) == {"decide.answer"}


def test_grade_decision_asks_no_rationale_when_none_was_asked_for():
    """A decision asked with no rationale (the speaker pick) is graded on its
    object and its answer alone: there is no `decide.rationale` to fail."""
    bare = json.dumps({"0": {"answers": {"over": True}}})
    checks = graders.grade_decision(bare, _decision_items(), explain=False,
                                    question="over", expected=True)
    assert [c.name for c in checks] == ["decide.json", "decide.answer"]
    assert failed(checks) == set()
    assert failed(graders.grade_decision(_decision(False), _decision_items(), explain=False,
                                         question="over", expected=True)) == {"decide.answer"}


def test_grade_decision_holds_the_rationale_to_a_cap_when_given_one():
    """Voice drift's rationale is the stored corrective, which may not exceed
    `MAX_NOTE`: a reply over the cap fails `decide.rationale` alone."""
    at_cap = _decision(True, "x" * 20)
    over = _decision(True, "x" * 21)
    for text, want in ((at_cap, set()), (over, {"decide.rationale"})):
        checks = graders.grade_decision(text, _decision_items(), explain=True,
                                        question="over", expected=True, max_rationale=20)
        assert failed(checks) == want
    # Without a cap a long rationale is still fine.
    assert failed(graders.grade_decision(over, _decision_items(), explain=True,
                                         question="over", expected=True)) == set()
