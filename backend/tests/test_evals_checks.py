"""Hard story-quality invariants on the synthetic guild benchmark."""

from fractions import Fraction

import pytest

from evals_helpers import (call, checks, contribution, load_benchmark, minimal_run,
                           minimal_scenario, mutate, parsed)
from grimoire.evals.checks import distinguishability, hard_findings, scorecard
from grimoire.evals.policy import GatePolicy
from grimoire.evals.text import longest_shared_run

SECRET = "the duke sold the guild"


@pytest.fixture
def bench():
    return load_benchmark()


def findings_for(scenario, run):
    return hard_findings(*parsed(scenario, run))


def set_text(turn, speaker, text):
    return lambda run: contribution(run, turn, speaker).update(text=text)


def remove_line(turn, speaker, *, keep_call=False):
    def change(run):
        line = contribution(run, turn, speaker)
        next(t for t in run["turns"] if t["turn"] == turn)["contributions"].remove(line)
        if not keep_call:
            run["calls"].remove(call(run, line["call_id"]))
    return change


def add_call(call_id, turn, purpose, actor_id=None, feature_flag=None):
    return lambda run: run["calls"].append({
        "call_id": call_id, "turn": turn, "purpose": purpose, "actor_id": actor_id,
        "feature_flag": feature_flag, "input_tokens": 500, "cached_input_tokens": 0,
        "output_tokens": 20, "latency_ms": 300})


def both(*changes):
    def change(run):
        for item in changes:
            item(run)
    return change


def test_shipped_baseline_passes_and_pays_one_call_per_speaking_npc(bench):
    scenario, baseline = parsed(*bench)
    assert hard_findings(scenario, baseline) == ()
    assert len(scenario.director_ids()) == 4  # four NPCs present ...
    for turn in (1, 2, 3):  # ... but only the two who speak get a generation call
        npc_calls = [c for c in baseline.calls if c.turn == turn and c.purpose == "npc_generation"]
        assert sorted(c.actor_id for c in npc_calls) == ["npc-bellamy", "npc-oda"]
    assert distinguishability(scenario, baseline) == 1


@pytest.mark.parametrize(("turn", "speaker", "leaks"), [
    (1, "npc-bellamy", True),    # before overhearing
    (2, "npc-bellamy", False),   # the turn it overhears (inclusive)
    (3, "npc-oda", True),        # never learns it
    (2, None, True),             # narration: the player character doesn't know yet
    (3, None, False),            # narration once Wren has heard the song
])
def test_knowledge_leaks_follow_each_actors_known_from_turn(bench, turn, speaker, leaks):
    scenario, run = bench
    text = f"Oh, darling, {SECRET}!" if speaker else f"Everyone now knows {SECRET}, and the sealed vault door gleams."
    if speaker is None and turn == 2:
        text = f"On the balcony a voice says {SECRET}."
    found = findings_for(scenario, mutate(run, set_text(turn, speaker, text)))
    assert checks(found) == ({"knowledge_leak"} if leaks else set())
    if leaks:
        assert found[0].evidence == "duke-betrayal#0"
        assert found[0].subject == (speaker or "narration")


def test_negation_label_and_evidence_are_independent_signals(bench):
    scenario, run = bench
    labelled = findings_for(scenario, mutate(run, lambda r: r["choice_outcomes"].update(
        {"ask-ledger": "negated"})))
    assert checks(labelled) == {"choice_negated"} and labelled[0].basis == "label"

    mislabelled = mutate(run, both(
        set_text(1, "npc-oda", "Coin's good. Book's on the hook. Read it here."),
        set_text(1, None, "Oda pushes a book across the counter. The sealed vault door gleams.")))
    found = findings_for(scenario, mislabelled)
    assert checks(found) == {"choice_negated"}
    assert [(f.basis, f.evidence) for f in found] == [("evidence", "unrealized")]

    negated = findings_for(scenario, mutate(run, set_text(
        2, None, "Wren stays at the counter while the balcony empties.")))
    assert [(f.check, f.evidence) for f in negated] == [("choice_negated", "negation#0")]


def test_effect_text_before_the_players_line_is_not_evidence(bench):
    scenario, run = bench

    def narration_first(r):
        r["turns"][0]["contributions"].insert(0, r["turns"][0]["contributions"].pop())
    changed = mutate(run, both(set_text(1, "npc-oda", "Coin's good. Book's on the hook."),
                               narration_first))
    assert [f.evidence for f in findings_for(scenario, changed)] == ["unrealized"]


@pytest.mark.parametrize(("evidence_turn", "realized"), [(2, True), (3, False)])
def test_choice_window_boundary(bench, evidence_turn, realized):
    scenario, run = bench
    scenario = mutate(scenario, lambda s: s["choices"][0].update(window=1))
    changed = mutate(run, both(
        set_text(1, "npc-oda", "Coin's good. Book's on the hook. Read it here."),
        set_text(1, None, "Oda pushes a book across the counter. The sealed vault door gleams."),
        set_text(evidence_turn, "npc-oda", "Fine. Guest ledger stays on the hook. Quickly.")))
    assert checks(findings_for(scenario, changed)) == (set() if realized else {"choice_negated"})


def test_window_past_the_last_turn_is_harmless(bench):
    scenario, run = bench
    scenario = mutate(scenario, lambda s: s["choices"][2].update(window=5))
    assert findings_for(scenario, run) == ()


def test_player_sovereignty(bench):
    scenario, run = bench
    voiced = mutate(run, both(
        add_call("t1-pc", 1, "narration"),
        lambda r: r["turns"][0]["contributions"].append(
            {"speaker_id": "pc-wren", "author": "model", "text": "I trust you.", "call_id": "t1-pc"})))
    assert checks(findings_for(scenario, voiced)) == {"player_sovereignty"}
    interior = mutate(run, set_text(2, None, "Wren feels the balcony sway beneath her."))
    assert checks(findings_for(scenario, interior)) == {"player_sovereignty"}


def test_scripted_player_input_must_be_played_verbatim(bench):
    scenario, run = bench
    altered = mutate(run, lambda r: contribution(r, 1, "pc-wren").update(text="I rob Oda."))
    # Without the scripted line nothing on its turn counts as giving it effect.
    assert checks(findings_for(scenario, altered)) == {"player_input_altered", "choice_negated"}
    extra = mutate(run, lambda r: r["turns"][0]["contributions"].append(
        {"speaker_id": "pc-wren", "author": "player", "text": "And a drink.", "call_id": None}))
    assert checks(findings_for(scenario, extra)) == {"player_input_altered"}
    missing = mutate(run, lambda r: r["turns"][0]["contributions"].pop(0))
    assert checks(findings_for(scenario, missing)) == {"player_input_altered", "choice_negated"}


def test_continuity_contradiction(bench):
    scenario, run = bench
    changed = mutate(run, set_text(2, None, "On the balcony, Wren sees the vault stands open below."))
    assert checks(findings_for(scenario, changed)) == {"continuity_contradiction"}


def test_dropping_or_folding_scripted_speech_is_caught(bench):
    scenario, run = bench
    assert checks(findings_for(scenario, mutate(run, remove_line(2, "npc-bellamy")))) == {
        "script_incomplete"}

    def drop_turn(r):
        r["turns"].pop()
        r["calls"] = [c for c in r["calls"] if c["turn"] != 3]
    assert checks(findings_for(scenario, mutate(run, drop_turn))) == {
        "script_incomplete", "player_input_altered", "choice_negated"}

    def fold(r):
        narration = contribution(r, 1, None)
        narration["text"] += " " + contribution(r, 1, "npc-bellamy")["text"]
        remove_line(1, "npc-bellamy")(r)
    assert checks(findings_for(scenario, mutate(run, fold))) == {"script_incomplete"}


@pytest.mark.parametrize(("speaker", "borrowed"), [
    ("npc-bellamy", "t1-oda"),      # one ensemble call voicing two NPCs
    ("npc-oda", "t1-narration"),    # an NPC line produced by the narrator
    (None, "t1-oda"),               # narration produced by an NPC call
])
def test_each_contribution_needs_its_own_call(bench, speaker, borrowed):
    scenario, run = bench

    def share(r):
        line = contribution(r, 1, speaker)
        r["calls"].remove(call(r, line["call_id"]))
        line["call_id"] = borrowed
    assert checks(findings_for(scenario, mutate(run, share))) == {"separate_generation"}


def test_paying_for_silent_npcs_or_unused_narration_is_orphan_generation(bench):
    scenario, run = bench
    silent = mutate(run, both(*(add_call(f"t{t}-{who}", t, "npc_generation", f"npc-{who}")
                                for t in (1, 2, 3) for who in ("quill", "hask"))))
    assert checks(findings_for(scenario, silent)) == {"orphan_generation"}
    narration = mutate(run, add_call("t2-extra", 2, "narration"))
    assert checks(findings_for(scenario, narration)) == {"orphan_generation"}


@pytest.mark.parametrize(("flag", "enabled", "expected"), [
    (None, [], {"unflagged_inference"}),
    ("planning", [], {"unflagged_inference"}),
    ("planning", ["planning"], set()),
])
def test_extra_inference_needs_an_enabled_flag(bench, flag, enabled, expected):
    scenario, run = bench
    changed = mutate(run, both(add_call("t1-plan", 1, "planning", feature_flag=flag),
                               lambda r: r.update(enabled_flags=enabled)))
    assert checks(findings_for(scenario, changed)) == expected


def test_copying_voice_samples_is_caught_at_24_characters(bench):
    scenario, run = bench
    sample = "Oh, my dear friend, what a splendid catastrophe this evening promises to be!"
    near = sample[:23] + "q, darling, marvellous!"
    assert longest_shared_run(near, sample) == 23
    assert findings_for(scenario, mutate(run, set_text(1, "npc-bellamy", near))) == ()
    copied = mutate(run, set_text(1, "npc-bellamy", sample[:24] + "q, darling!"))
    found = findings_for(scenario, copied)
    assert checks(found) == {"voice_sample_copy"} and found[0].evidence == "npc-bellamy#sample0"


def test_findings_never_echo_contribution_or_marker_text(bench):
    scenario, run = bench
    leaky = mutate(run, both(set_text(1, "npc-oda", f"Fine. {SECRET}. Quickly."),
                             set_text(2, None, "Wren feels the vault stands open.")))
    found = findings_for(scenario, leaky)
    assert checks(found) == {"knowledge_leak", "player_sovereignty", "continuity_contradiction"}
    assert list(found) == sorted(found, key=lambda f: (f.check, f.turn or 0, f.subject or "",
                                                       f.evidence or "", f.message))
    for finding in found:
        for text in (SECRET, "sold the guild", "vault stands open", "wren feels", "quickly"):
            assert text not in (finding.message + str(finding.evidence)).lower()


def test_scenario_without_choices_still_scores():
    scenario = mutate(minimal_scenario(), lambda s: s.update(choices=[]))
    run = mutate(minimal_run(), lambda r: (r.update(choice_outcomes={}),
                                           r["turns"][0]["contributions"].pop(0)))
    assert findings_for(scenario, run) == ()


def test_line_without_any_voice_overlap_counts_as_wrong():
    run = mutate(minimal_run(), lambda r: contribution(r, 1, "npc-a").update(text="Zzz qqq knock"))
    assert distinguishability(*parsed(minimal_scenario(), run)) == 0


def test_scorecard_token_categories_and_weighted_cost(bench):
    scenario, run = parsed(*bench)
    card = scorecard(scenario, run, GatePolicy())
    assert (card.uncached_input, card.cached_input, card.output) == (8700, 4200, 540)
    assert card.weighted_cost == 8700 + Fraction(4200, 10) + 540
    assert dict(card.calls_by_purpose) == {"narration": 3, "npc_generation": 6}
    assert card.latency_ms == 8700 and card.leak_rate == 0 and card.npc_lines == 6
    full_weight = scorecard(scenario, run, GatePolicy(cached_input_weight=1.0))
    assert full_weight.weighted_cost == 8700 + 4200 + 540


def test_duplicated_scripted_player_line_is_unscripted_input(bench):
    scenario, run = bench
    doubled = mutate(run, lambda r: r["turns"][0]["contributions"].insert(
        1, dict(r["turns"][0]["contributions"][0])))
    found = findings_for(scenario, doubled)
    assert checks(found) == {"player_input_altered"}
    assert found[0].message == "unscripted player contribution 1"  # real contribution index


def test_blank_model_lines_are_silence(bench):
    scenario, run = bench
    assert checks(findings_for(scenario, mutate(run, set_text(1, None, "  ")))) == {
        "orphan_generation"}
    assert checks(findings_for(scenario, mutate(run, set_text(2, "npc-oda", "...")))) == {
        "orphan_generation", "script_incomplete"}


def test_narration_copying_a_voice_sample_is_caught(bench):
    scenario, run = bench
    sample = "Darling, every song needs a villain, and tonight the hall is simply brimming"
    found = findings_for(scenario, mutate(run, set_text(3, None, f"Bellamy sings: {sample}.")))
    assert checks(found) == {"voice_sample_copy"} and found[0].subject == "narration"


def test_curly_apostrophe_leak_is_caught(bench):
    scenario, run = bench
    changed = mutate(run, set_text(1, "npc-bellamy", "Oh, darling, the duke’s betrayal!"))
    assert checks(findings_for(scenario, changed)) == {"knowledge_leak"}


def test_negation_counts_are_per_choice(bench):
    scenario, run = parsed(*mutate_pair(bench))
    card = scorecard(scenario, run, GatePolicy())
    assert (card.negated_by_label, card.negated_by_evidence, card.unrealized) == (1, 1, 0)


def mutate_pair(bench):
    scenario, run = bench
    scenario = mutate(scenario, lambda s: s["choices"][1].update(
        negation_markers=["wren stays at the counter", "ballroom instead"]))
    run = mutate(run, both(
        lambda r: r["choice_outcomes"].update({"follow-bellamy": "negated"}),
        set_text(2, "npc-oda", "Wren stays at the counter. Balcony's shut."),
        set_text(2, None, "They head for the ballroom instead.")))
    return scenario, run
