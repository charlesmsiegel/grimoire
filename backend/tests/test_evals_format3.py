"""grimoire.story-quality/3: /2 plus the opt-in, scene-level director reply (027), and its gate."""

import json
from pathlib import Path

import pytest

from evals_helpers import (
    attempt_v2, contribution, director_call, guild_run_v2, guild_run_v3, load_benchmark, live_source,
    minimal_run_v2, minimal_run_v3, minimal_scenario, mutate, narration_line,
)
from grimoire.core.inference import InferenceStage
from grimoire.evals.format import BenchmarkFormatError, parse_run, parse_scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.format3 import FORMAT_V3, RunV3, parse_run_v3
from grimoire.evals.gate import compare
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import benchmark_file, load_any_run, load_json
from grimoire.evals.policy import GatePolicy
from grimoire.evals.report import render_v2
from grimoire.evals.story_quality import main

SCENARIO = parse_scenario(minimal_scenario())
ROOT = Path(__file__).resolve().parents[2]


def parse(run: dict, scenario=SCENARIO) -> RunV3:
    return parse_run_v3(run, scenario)


def rejects(run: dict, where: str, scenario=SCENARIO, *, says: str = "") -> str:
    with pytest.raises(BenchmarkFormatError) as error:
        parse(run, scenario)
    message = str(error.value)
    assert message.startswith(where), message
    assert says in message, message
    return message


def narrated(r: dict) -> None:
    """Turn 1 of the minimal run, answered by Ana and then by the director."""
    r["calls"].append(director_call())
    r["turns"][0]["contributions"].append(narration_line())


def checks(report) -> set[str]:
    return {finding.check for finding in report.findings}


# --- the format ----------------------------------------------------------------------------------

def test_a_director_line_is_scene_narration_from_its_own_call():
    run = parse(mutate(minimal_run_v3(), narrated))
    assert isinstance(run, RunV3) and run.format == FORMAT_V3
    npc, director = run.calls
    assert director.stage is InferenceStage.DIRECTOR_GENERATION and director.actor_id is None
    assert run.turns[0].contributions[-1].speaker_id is None
    (summary,) = run.summaries
    assert summary.baseline.attempts == 1 and summary.optional.attempts == 1
    assert summary.by_actor[None].sums["input_tokens"] == 60
    view = run.scoring_view()
    assert [(c.purpose, c.actor_id, c.feature_flag) for c in view.calls] == [
        ("npc_generation", "npc-a", None), ("narration", None, None)]


def test_a_director_call_the_policy_does_not_enable_is_refused():
    def change(r):
        narrated(r)
        r["policy"]["per_turn_call_caps"] = {}
    rejects(mutate(minimal_run_v3(), change), "run.calls[1].stage", says="disabled")


def test_an_implicit_director_call_beside_two_npcs_is_refused():
    raw_scenario, raw_run = guild_run_v3()
    scenario = parse_scenario(raw_scenario)
    run = parse(raw_run, scenario)
    assert [c.stage.value for c in run.calls if c.turn == 1] == [
        "npc_generation", "npc_generation", "director_generation"]
    implicit = mutate(raw_run, lambda r: r["policy"].update(per_turn_call_caps={}))
    rejects(implicit, "run.calls[2].stage", scenario, says="disabled")


def test_a_director_call_never_names_an_actor():
    def change(r):
        narrated(r)
        r["calls"][1]["actor_id"] = "npc-a"
    rejects(mutate(minimal_run_v3(), change), "run.calls[1].actor_id", says="scene")


def test_one_director_reply_per_turn_whatever_the_cap():
    def change(r):
        narrated(r)
        r["policy"]["per_turn_call_caps"] = {"director_generation": 2}
        r["calls"].append(director_call("n2"))
    rejects(mutate(minimal_run_v3(), change), "run.calls[2].stage", says="one director_generation")


def test_narration_must_name_a_completed_director_call():
    def on_npc_call(r):
        r["turns"][0]["contributions"].append(narration_line(call_id="k1"))
    rejects(mutate(minimal_run_v3(), on_npc_call), "run.turns[0].contributions[2].speaker_id",
            says="director_generation")

    def no_call(r):
        r["turns"][0]["contributions"].append(narration_line(call_id=None))
    rejects(mutate(minimal_run_v3(), no_call), "run.turns[0].contributions[2].call_id")

    def failed(r):
        narrated(r)
        r["calls"][1]["attempts"] = [attempt_v2(60, None, outcome="failed")]
    rejects(mutate(minimal_run_v3(), failed), "run.turns[0].contributions[2].call_id",
            says="completed")


def test_every_model_call_contributes_one_line():
    def director_twice(r):
        narrated(r)
        r["turns"][0]["contributions"].append(narration_line("The lamp gutters."))
    rejects(mutate(minimal_run_v3(), director_twice), "run.turns[0].contributions[3].call_id",
            says="already produced its one line")

    def npc_twice(r):
        r["turns"][0]["contributions"].append(dict(r["turns"][0]["contributions"][1], text="Wait."))
    rejects(mutate(minimal_run_v3(), npc_twice), "run.turns[0].contributions[2].call_id",
            says="already produced its one line")
    with pytest.raises(BenchmarkFormatError, match="already produced its one line"):
        parse_run_v2(mutate(minimal_run_v2(), npc_twice), SCENARIO)


def test_a_director_call_cannot_voice_an_npc_or_a_player():
    for speaker in ("npc-a", "pc"):
        def change(r, speaker=speaker):
            r["calls"].append(director_call())
            r["turns"][0]["contributions"].append(
                dict(narration_line(), speaker_id=speaker))
        rejects(mutate(minimal_run_v3(), change), "run.turns[0].contributions[2].speaker_id",
                says="must be null")


def test_other_optional_stages_still_produce_no_contribution():
    def change(r):
        r["policy"]["per_turn_call_caps"]["decision"] = 1
        r["calls"].append({"call_id": "d1", "turn": 1, "stage": "decision", "actor_id": None,
                           "purpose": "choose who answers", "latency_ms": 20,
                           "attempts": [attempt_v2(30, 2)]})
        r["turns"][0]["contributions"].append(narration_line(call_id="d1"))
    rejects(mutate(minimal_run_v3(), change), "run.turns[0].contributions[2].call_id",
            says="director_generation")


def test_sources_are_synthetic_or_v2_runtime():
    runtime = parse(mutate(minimal_run_v3(), lambda r: r.update(
        source={"kind": "v2-runtime", "harness": "grimoire.runtime/1"})))
    assert runtime.source.kind == "v2-runtime"
    rejects(mutate(minimal_run_v3(), lambda r: r.update(source=live_source())), "run.source.kind")


def test_v2_and_v3_documents_are_not_interchangeable():
    rejects(minimal_run_v2(), "run.format", says="story-quality/3")
    with pytest.raises(BenchmarkFormatError, match=r"^run\.format"):
        parse_run_v2(minimal_run_v3(), SCENARIO)
    with pytest.raises(BenchmarkFormatError):  # /1 refuses the /3 keys before the format
        parse_run(minimal_run_v3(), SCENARIO)


# --- the gate ------------------------------------------------------------------------------------

def test_hard_checks_read_narration_like_an_npc_line():
    def authoring(r):
        narrated(r)
        r["turns"][0]["contributions"][-1]["text"] = "Pat feels the door give."
    run = parse(mutate(minimal_run_v3(), authoring))
    assert "player_sovereignty" in checks(compare_v2(SCENARIO, run, run))

    def leaking(r):
        narrated(r)
        r["turns"][0]["contributions"][-1]["text"] = "Under the mat lies the hidden key."
    run = parse(mutate(minimal_run_v3(), leaking))
    assert "knowledge_leak" in checks(compare_v2(SCENARIO, run, run))


def test_a_silent_director_reply_is_an_orphan():
    run = parse(mutate(minimal_run_v3(), lambda r: r["calls"].append(director_call())))
    assert "orphan_generation" in checks(compare_v2(SCENARIO, run, run))


def test_opting_into_the_director_reply_is_a_gated_flag():
    baseline = parse(mutate(minimal_run_v3(), lambda r: r["policy"].update(per_turn_call_caps={})))
    candidate = parse(mutate(minimal_run_v3(), narrated))
    report = compare_v2(SCENARIO, baseline, candidate, GatePolicy(max_cost_increase=10))
    assert "flag_not_allowed" in checks(report)
    allowed = GatePolicy(max_cost_increase=10, allowed_flags=frozenset({"director_generation"}))
    assert compare_v2(SCENARIO, baseline, candidate, allowed).passed


def test_mixing_v2_and_v3_is_refused():
    v2 = parse_run_v2(minimal_run_v2(), SCENARIO)
    v3 = parse(minimal_run_v3())
    for baseline, candidate in ((v2, v3), (v3, v2)):
        with pytest.raises(ValueError, match="same run format"):
            compare_v2(SCENARIO, baseline, candidate)


def scene_only_scenario() -> dict:
    return mutate(minimal_scenario(), lambda s: (
        s.update(actors=s["actors"][:1], facts=[], script=[{"turn": 1, "speakers": []}])))


def test_a_scenario_only_run_is_gated_without_a_cast_to_compare():
    scenario = parse_scenario(scene_only_scenario())

    def only_narration(r):
        r["calls"] = [director_call()]
        r["turns"][0]["contributions"][1:] = [narration_line()]
    run = parse(mutate(minimal_run_v3(), only_narration), scenario)
    report = compare_v2(scenario, run, run)
    assert report.passed, report.findings
    assert report.candidate.npc_lines == 0 and report.candidate.narration_lines == 1
    assert report.candidate.distinguishability is None
    assert "narration lines" in render_v2(report)

    def silent(r):
        r["calls"] = []
        r["turns"][0]["contributions"][1:] = []
    nothing = parse(mutate(minimal_run_v3(), silent), scenario)
    # Nothing said at all is never "scene only": the empty side still fails closed.
    assert "no_npc_contributions" in checks(compare_v2(scenario, run, nothing))
    assert "baseline_invalid" in checks(compare_v2(scenario, nothing, run))


def test_a_cast_that_falls_silent_for_narration_still_fails():
    def narration_only(r):
        r["calls"] = [director_call()]
        r["turns"][0]["contributions"][1:] = [narration_line("You knock. The door opens.")]
    report = compare_v2(SCENARIO, parse(minimal_run_v3()),
                        parse(mutate(minimal_run_v3(), narration_only)))
    assert {"no_npc_contributions", "script_incomplete"} <= checks(report)


def test_v2_report_has_no_narration_row():
    run = parse_run_v2(minimal_run_v2(), SCENARIO)
    assert "narration lines" not in render_v2(compare_v2(SCENARIO, run, run))


# --- the shipped /3 benchmark baseline -----------------------------------------------------------

def shipped_v3():
    raw_scenario, _ = load_benchmark()
    scenario = parse_scenario(raw_scenario)
    return scenario, parse(load_json(benchmark_file("guild_betrayal", "baseline.v3.json")), scenario)


def test_shipped_v3_baseline_passes_against_itself():
    scenario, run = shipped_v3()
    report = compare_v2(scenario, run, run)
    assert report.passed, report.findings
    assert report.baseline.status == "synthetic"
    assert report.baseline.calls_by_stage == {"director_generation": 3, "npc_generation": 6}
    assert report.baseline.narration_lines == 3


def test_v3_baseline_is_the_v1_baseline_with_narration_as_director_reply():
    _, expected = guild_run_v3()
    assert load_json(benchmark_file("guild_betrayal", "baseline.v3.json")) == expected
    raw_scenario, v1 = load_benchmark()
    scenario, run = shipped_v3()
    v1_run = parse_run(v1, scenario)
    # The same calls and lines, so the same weighted cost and the same hard-check verdict as /1.
    v1_report = compare(scenario, v1_run, v1_run)
    v3_report = compare_v2(scenario, run, run)
    assert v3_report.baseline.weighted_cost == v1_report.baseline.weighted_cost
    narration = {c["call_id"] for c in v1["calls"] if c["purpose"] == "narration"}
    assert {c.call_id for c in run.calls if c.stage is InferenceStage.DIRECTOR_GENERATION} == narration


def test_dropping_the_director_reply_passes_against_the_v3_baseline():
    scenario, baseline = shipped_v3()
    _, v2 = guild_run_v2()
    candidate = parse(mutate(v2, lambda r: r.update(format=FORMAT_V3)), scenario)
    report = compare_v2(scenario, baseline, candidate)
    assert report.passed, report.findings
    assert report.candidate.weighted_cost < report.baseline.weighted_cost


def test_no_checked_in_v3_run_is_live_or_provider_reported():
    seen = 0
    for base in (ROOT / "backend" / "src" / "grimoire", ROOT / "backend" / "tests"):
        for path in sorted(base.rglob("*.json")):
            data = json.loads(path.read_text("utf-8"))
            if not isinstance(data, dict) or data.get("format") != FORMAT_V3:
                continue
            seen += 1
            assert data["source"]["kind"] == "synthetic", path
            assert all(each["measurement"] == "synthetic"
                       for call in data["calls"] for each in call["attempts"]), path
    assert seen >= 1


# --- narration on the shipped benchmark: the director describes, it never speaks for an NPC --------

def gate_narration(turn: int, text: str, *, line: tuple[str, str] | None = None, scenario_change=None):
    """The shipped baseline against itself with turn ``turn``'s narration (and one NPC line) set."""
    raw_scenario, raw_run = guild_run_v3()
    if scenario_change is not None:
        raw_scenario = mutate(raw_scenario, scenario_change)
    scenario = parse_scenario(raw_scenario)

    def change(r):
        contribution(r, turn, None)["text"] = text
        if line is not None:
            contribution(r, turn, line[0])["text"] = line[1]
    baseline = parse(raw_run, scenario)
    return compare_v2(scenario, baseline, parse(mutate(raw_run, change), scenario))


def found(report) -> set[tuple]:
    return {(f.check, f.turn, f.subject, f.evidence) for f in report.findings}


LEAK = ("knowledge_leak", 3, "narration", "duke-betrayal#0")


def test_an_npc_secret_put_in_narration_fails_as_it_would_in_the_npcs_own_line():
    # In guild-betrayal Oda never learns the duke's betrayal. Said in Oda's own line, it leaks ...
    raw_scenario, raw_run = guild_run_v3()
    scenario = parse_scenario(raw_scenario)
    own = mutate(raw_run, lambda r: contribution(r, 3, "npc-oda").update(
        text="Singing. Fine. They say the duke sold the guild. Keep it short."))
    report = compare_v2(scenario, parse(raw_run, scenario), parse(own, scenario))
    assert ("knowledge_leak", 3, "npc-oda", "duke-betrayal#0") in found(report)
    # ... and given to Oda in the director's line instead, it still fails, twice over.
    report = gate_narration(3, "Bellamy sings softly. Oda leans over the counter and mutters, "
                               "'They say the duke sold the guild.' The tune echoes on.")
    assert not report.passed
    assert found(report) == {LEAK, ("narration_voices_actor", 3, "npc-oda", "speech_verb")}
    assert "npc-oda" in next(f.message for f in report.findings if f.check == "knowledge_leak")


@pytest.mark.parametrize("text", [
    # the quotation follows a sentence whose subject is Oda, and names no other speaker
    "Oda leans over the counter. 'They say the duke sold the guild,' comes a low voice.",
    'Oda leans in and goes on. "The duke sold the guild."',
    # the speaker named after the quotation
    '"The duke sold the guild," mutters Oda.',
])
def test_a_quotation_is_the_npc_s_when_the_narration_attributes_it(text):
    report = gate_narration(3, text)
    assert found(report) == {LEAK, ("narration_voices_actor", 3, "npc-oda", "quotation")}


def test_any_word_of_a_multi_word_name_names_the_npc():
    def rename(s):
        next(a for a in s["actors"] if a["actor_id"] == "npc-oda")["name"] = "Oda Venn"
    for who in ("Oda", "Venn", "Oda Venn", "npc-oda"):
        report = gate_narration(3, f"{who} leans over the counter and mutters, 'They say the duke "
                                   f"sold the guild.'", scenario_change=rename)
        assert found(report) == {LEAK, ("narration_voices_actor", 3, "npc-oda", "speech_verb")}, who


def test_a_silent_npc_voiced_in_narration_fails():
    report = gate_narration(3, "Bellamy sings softly. From the corner Hask growls: "
                               "'Everyone knows the duke sold the guild.'")
    assert found(report) == {LEAK, ("narration_voices_actor", 3, "npc-hask", "speech_verb")}
    # Without the secret it is still Hask's speech, which only Hask's own call may produce.
    report = gate_narration(3, "Bellamy sings softly. Hask growls a warning at the door.")
    assert found(report) == {("narration_voices_actor", 3, "npc-hask", "speech_verb")}


@pytest.mark.parametrize("verb", [
    "mutters", "confides", "blurts", "admits", "reveals", "adds", "breathes", "croons", "lets slip",
    "begs", "jokes", "boasts", "pleads", "threatens", "promises", "says",
])
def test_an_npc_line_moved_into_narration_fails_though_the_npc_knows_the_fact(verb):
    report = gate_narration(3, f"Bellamy {verb} that the river barons now own every last brick.",
                            line=("npc-bellamy", "Oh darling, very well, it is a song."))
    assert found(report) == {("narration_voices_actor", 3, "npc-bellamy", "speech_verb")}


@pytest.mark.parametrize(("turn", "text"), [
    (3, "Bellamy sings softly, and the tune echoes off the sealed vault door."),  # the shipped line
    (1, "Wren asks for the guest ledger, and Oda pushes it across the counter."),  # a PC's action
    (2, "Oda says nothing and wipes the counter."),
    (2, "Oda does not answer; the balcony door creaks."),
    (2, "Oda never mutters a word as the balcony door creaks."),
    (2, "Oda bars the balcony door 'til dawn."),
    (1, 'Oda taps a sign reading "No credit" beside the sealed vault door, guest ledger in hand.'),
    (2, "Talk in the hall dies as Oda locks the till."),
    (2, "Outside, a wren sings on the balcony rail."),
    (1, "A quill scratches in the guest ledger as Bellamy hums."),
    (1, "Oda finds the answer in the guest ledger."),
    (1, "Oda hears a shout from the courtyard and closes the guest ledger."),
    (1, "Oda's ledger lies open, and the guards' lamps gleam on the guest ledger."),
    (1, "Oda pushes the guest ledger to Bellamy, who chuckles."),
    (2, "Oda pushes the ledger aside. Somewhere a voice says the balcony is cold."),
])
def test_narration_describing_actors_without_giving_an_npc_speech_passes(turn, text):
    report = gate_narration(turn, text)
    assert report.passed, report.findings


@pytest.mark.parametrize("text", [
    "The song of the duke's betrayal fades, and Oda keeps polishing glasses, none the wiser.",
    "Wren keeps the duke's betrayal from Oda.",
])
def test_naming_an_npc_beside_a_fact_it_does_not_know_is_not_a_leak(text):
    # Wren knows it by turn 3; Oda is only named, not given the fact to say.
    assert gate_narration(3, text).passed


def test_v1_narration_keeps_its_v1_checks():
    # The same words in a /1 narration line: /1 has no attribution rule, and no NPC-knowledge rule.
    raw_scenario, raw_run = load_benchmark()
    scenario = parse_scenario(raw_scenario)
    v1 = mutate(raw_run, lambda r: contribution(r, 3, None).update(
        text="Bellamy croons that the river barons now own every last brick."))
    assert compare(scenario, parse_run(raw_run, scenario), parse_run(v1, scenario)).passed


# --- the command line ----------------------------------------------------------------------------

def shipped(filename):
    return str(benchmark_file("guild_betrayal", filename))


def test_cli_v3_baseline_passes_and_loads_as_v3(capsys):
    scenario, run = shipped_v3()
    assert load_any_run(benchmark_file("guild_betrayal", "baseline.v3.json"), scenario) == run
    assert main([shipped("scenario.json"), shipped("baseline.v3.json"),
                 shipped("baseline.v3.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("story-quality gate: PASS") and "narration lines" in out


@pytest.mark.parametrize("other", ["baseline.json", "baseline.v2.json"])
def test_cli_mixing_v3_with_another_format_exits_2(capsys, other):
    for pair in ((other, "baseline.v3.json"), ("baseline.v3.json", other)):
        assert main([shipped("scenario.json"), *map(shipped, pair)]) == 2
        assert "same run format" in capsys.readouterr().err


def test_cli_evidence_is_refused_for_a_v3_run(tmp_path, capsys):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", "utf-8")
    assert main([shipped("scenario.json"), shipped("baseline.v3.json"), shipped("baseline.v3.json"),
                 "--evidence-baseline", str(evidence)]) == 2
    assert "only to a /2 run" in capsys.readouterr().err
