"""The story-quality command line and its plain-text report."""

import json

import pytest

from evals_helpers import contribution, load_benchmark, mutate, parsed
from grimoire.evals.format import parse_run
from grimoire.evals.gate import compare
from grimoire.evals.loader import benchmark_file
from grimoire.evals.policy import GatePolicy
from grimoire.evals.report import SYNTHETIC_NOTICE, render
from grimoire.evals.story_quality import main

SECRET = "the duke sold the guild"


def shipped(filename):
    return str(benchmark_file("guild_betrayal", filename))


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), "utf-8")
    return str(path)


def leaky_candidate():
    _, baseline = load_benchmark()
    return mutate(baseline, lambda run: contribution(run, 1, "npc-oda").update(
        text=f"Fine. {SECRET}. Quickly."))


def test_shipped_baseline_passes_with_synthetic_notice(capsys):
    assert main([shipped("scenario.json"), shipped("baseline.json"), shipped("baseline.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("story-quality gate: PASS") and SYNTHETIC_NOTICE in out


def test_failing_candidate_exits_1_without_echoing_text(tmp_path, capsys):
    candidate = write(tmp_path, "candidate.json", leaky_candidate())
    assert main([shipped("scenario.json"), shipped("baseline.json"), candidate]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "knowledge_leak" in out and "duke-betrayal#0" in out
    assert "sold the guild" not in out and "Quickly" not in out
    assert SYNTHETIC_NOTICE not in out


@pytest.mark.parametrize("text", ['{"format": 1, "format": 2}', '{"a": NaN}', "not json", "[]"])
def test_invalid_documents_exit_2_without_traceback(tmp_path, capsys, text):
    candidate = write(tmp_path, "candidate.json", text)
    assert main([shipped("scenario.json"), shipped("baseline.json"), candidate]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error:") and "Traceback" not in captured.err
    assert captured.out == ""


def test_missing_file_and_bad_options_exit_2(tmp_path, capsys):
    assert main([shipped("scenario.json"), shipped("baseline.json"),
                 str(tmp_path / "absent.json")]) == 2
    assert "absent.json" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exit_info:
        main([shipped("scenario.json")])
    assert exit_info.value.code == 2
    assert main([shipped("scenario.json"), shipped("baseline.json"), shipped("baseline.json"),
                 "--max-cost-increase", "-1"]) == 2


def test_allow_flag_option_opts_into_optional_inference(tmp_path, capsys):
    _, baseline = load_benchmark()
    planned = mutate(baseline, lambda run: (run.update(enabled_flags=["planning"]), run["calls"].append({
        "call_id": "t1-plan", "turn": 1, "purpose": "planning", "feature_flag": "planning",
        "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 1, "latency_ms": 5})))
    candidate = write(tmp_path, "candidate.json", planned)
    args = [shipped("scenario.json"), shipped("baseline.json"), candidate]
    assert main(args) == 1
    assert main([*args, "--allow-flag", "planning"]) == 0
    assert "allowed_flags=['planning']" in capsys.readouterr().out


def test_render_shows_policy_costs_and_separate_negation_counts():
    scenario_raw, baseline_raw = load_benchmark()
    scenario, baseline = parsed(scenario_raw, baseline_raw)
    candidate = parse_run(mutate(baseline_raw, lambda run: (
        run.update(provenance="measured", judge_scores={"fluency": 0.9}),
        run["choice_outcomes"].update({"ask-ledger": "negated"}),
        contribution(run, 2, "npc-oda").update(text="Shut after dark. Quickly."),
        contribution(run, 2, "npc-bellamy").update(text="Come, darling, hush now, simply dripping!"),
        contribution(run, 2, None).update(text="Voices drift up, too faint to make out."),
    )), scenario)
    text = render(compare(scenario, baseline, candidate, GatePolicy(max_cost_increase=0.2)))
    assert "FAIL" in text and "policy (non-default): max_cost_increase=0.2" in text
    assert "judge scores (informational)" in text and "fluency=0.9" in text
    label = next(line for line in text.splitlines() if line.startswith("choices negated by label"))
    unrealized = next(line for line in text.splitlines() if line.startswith("choices unrealized"))
    assert label.split()[-1] == "1" and unrealized.split()[-1] == "1"
    assert "cost by purpose" in text and "npc_generation=6300" in text
    assert "note: at least one run is synthetic" in text  # synthetic baseline, measured candidate


def test_deeply_nested_json_is_invalid_input(tmp_path, capsys):
    candidate = write(tmp_path, "candidate.json", "[" * 200000)
    assert main([shipped("scenario.json"), shipped("baseline.json"), candidate]) == 2
    assert "nested too deeply" in capsys.readouterr().err
