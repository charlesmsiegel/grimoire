"""068's one-step V2-vs-0.2.1 comparison (``python -m grimoire.capture.v2``). Nothing here is billed.

The baselines are 015's committed dry-run evidence bundles, converted as the 0.2.1 capture would write
them. The dry run answers from a scripted transport; the "live" path is exercised against a loopback
server only, with a made-up key, so the real HTTP transport is tested without any provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from provider_servers import base_url, messages_lines, sse_lines, sse_server, stream

from grimoire.capture import v2
from grimoire.evals.loader import (
    benchmark_file,
    evidence_digest,
    load_json,
    load_scenario,
)
from grimoire.evals.v021 import convert

FIXTURES = Path(__file__).parent / "fixtures" / "v021"
SCENARIO = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))


def baseline(tmp_path, provider="anthropic", *, live=False, tamper=None, vary=None, name="") -> Path:
    bundle = load_json(FIXTURES / f"guild_betrayal_dry_run_{provider}.evidence.json")
    if live:  # a live capture's choices are never labelled by the harness
        bundle = {**bundle, "mode": "live",
                  "choice_outcomes": {key: "unlabelled" for key in bundle["choice_outcomes"]}}
    if vary:  # another capture of the same play: its evidence still verifies
        vary(bundle)
    raw = convert(bundle, SCENARIO, digest=evidence_digest(bundle), run_id=f"v021-{provider}{name}",
                  label="0.2.1")
    if tamper:
        tamper(bundle)
    directory = tmp_path / f"base-{provider}{'-live' if live else ''}{name}"
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps(raw), "utf-8")
    (directory / "evidence.json").write_text(json.dumps(bundle), "utf-8")
    return directory


def run(*args) -> int:
    return v2.main(["--scenario", "guild_betrayal", *map(str, args)])


def outputs(out: Path) -> dict[str, str]:
    return {path.name: path.read_text("utf-8") for path in sorted(out.iterdir())}


def test_a_dry_run_plays_one_call_per_speaking_npc_under_the_baselines_conditions(tmp_path):
    base, out = baseline(tmp_path), tmp_path / "out"
    assert run("--baseline", base, "--out", out, "--dry-run") == 0
    files = outputs(out)
    assert set(files) == {"v2-baseline.run.json", "comparison-report.txt"}
    played = json.loads(files["v2-baseline.run.json"])
    captured = json.loads((base / "run.json").read_text("utf-8"))
    assert played["format"] == captured["format"] == "grimoire.story-quality/2"
    assert played["source"]["kind"] == "v2-runtime" and played["conditions"] == captured["conditions"]
    assert played["policy"] == {"per_turn_call_caps": {}, "max_attempts_per_call": 3}
    calls = [(c["turn"], c["stage"], c["actor_id"]) for c in played["calls"]]
    assert calls == [(t, "npc_generation", a) for t in (1, 2, 3) for a in ("npc-oda", "npc-bellamy")]
    assert all(a["measurement"] == "synthetic" for c in played["calls"] for a in c["attempts"])
    report = files["comparison-report.txt"]
    assert "Dry run: scripted replies and synthetic numbers; nothing was sent." in report
    assert "== V2 (one call per speaking NPC) against 0.2.1 ==\nstory-quality gate: PASS" in report
    assert "planning" not in report


def test_planning_is_a_separate_opt_in_run_its_suggestions_are_never_recorded(tmp_path):
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path), "--out", out, "--dry-run", "--with-planning") == 0
    files = outputs(out)
    planned = json.loads(files["v2-planning.run.json"])
    assert planned["policy"]["per_turn_call_caps"] == {"planning": 2}
    stages = [(c["turn"], c["stage"]) for c in planned["calls"]]
    assert stages == [(t, s) for t in (1, 2, 3) for s in ("planning",) * 2 + ("npc_generation",) * 2]
    lines = [c for turn in planned["turns"] for c in turn["contributions"] if c["author"] == "model"]
    assert len(lines) == 6 and all(c["call_id"].split(":")[2] == "npc_generation" for c in lines)
    assert not any(v2.DRY_RUN_SUGGESTION in text for text in files.values())
    report = files["comparison-report.txt"]
    assert "== V2 with optional planning against plain V2 (an opt-in billed extra) ==" in report
    assert "cost_regression" in report and "flag_not_allowed" not in report
    loaded = v2.load_baseline(baseline(tmp_path, "openrouter"), SCENARIO)
    assert v2.planned_requests(SCENARIO, loaded, with_planning=False) == 6 * 3  # six calls, three attempts each
    assert v2.planned_requests(SCENARIO, loaded, with_planning=True) == (6 + 12) * 3


def test_an_unset_0_2_1_budget_is_reported_as_incomparable_never_matched(tmp_path):
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path, "openrouter"), "--out", out, "--dry-run") == 0
    files = outputs(out)
    assert json.loads(files["v2-baseline.run.json"])["conditions"]["budget"] == "max_tokens=400"
    report = files["comparison-report.txt"]
    assert "sent no output cap" in report and "story-quality gate: FAIL" in report
    assert "incomparable (comparison) budget: recording condition differs from the baseline" in report


@pytest.mark.parametrize("args, message", [
    (("--live", "--api-key-env", "CAPTURE_TEST_KEY"), "add --confirm-billing"),
    (("--live", "--confirm-billing"), "--api-key-env"),
    (("--live", "--confirm-billing", "--api-key-env", "GRIMOIRE_KEY"), "plain upper-case"),
    (("--live", "--confirm-billing", "--api-key-env", "CAPTURE_TEST_KEY"), "needs a live 0.2.1 baseline"),
])
def test_billing_is_never_implied(tmp_path, capsys, monkeypatch, args, message):
    monkeypatch.setenv("CAPTURE_TEST_KEY", "sk-not-a-real-key")
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path), "--out", out, *args) == 2
    assert message in capsys.readouterr().err and not out.exists()


def test_a_baseline_that_does_not_verify_or_an_unsafe_out_is_refused(tmp_path, capsys):
    def tamper(bundle):
        bundle["turns"][0]["responses"][0]["text"] = "Something else entirely."

    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path, tamper=tamper), "--out", out, "--dry-run") == 2
    assert "evidence does not verify" in capsys.readouterr().err and not out.exists()
    good = baseline(tmp_path, "openrouter")
    out.mkdir()
    (out / "keep.txt").write_text("x", "utf-8")
    assert run("--baseline", good, "--out", out, "--dry-run") == 2
    assert "new or empty directory" in capsys.readouterr().err
    assert run("--baseline", good, "--out", v2.forbidden_roots(good)[0] / "capture-out", "--dry-run") == 2
    assert "is inside" in capsys.readouterr().err
    assert run("--baseline", good, "--out", good / "out", "--dry-run") == 2  # never into the baseline
    assert "is inside" in capsys.readouterr().err


def test_a_live_run_uses_the_real_transport_reads_only_the_named_key_and_never_writes_it(
        tmp_path, capsys, monkeypatch):
    key = "sk-loopback-only-0000"
    monkeypatch.setenv("CAPTURE_TEST_KEY", key)

    def reply(handler):
        stream(sse_lines(deltas=("Hm, a quiet evening at the hall.",), model="synthetic/rp-model",
                         usage={"prompt_tokens": 100, "completion_tokens": 10,
                                "prompt_tokens_details": {"cached_tokens": 0}}))(handler)

    out = tmp_path / "out"
    with sse_server(reply) as server:
        monkeypatch.setitem(v2.ENDPOINTS, "openrouter", base_url(server))
        assert run("--baseline", baseline(tmp_path, "openrouter", live=True), "--out", out, "--live",
                   "--confirm-billing", "--api-key-env", "CAPTURE_TEST_KEY") == 0
    assert "at most 18 billed requests to openrouter (synthetic/rp-model)" in capsys.readouterr().err
    assert len(server.requests) == 6
    for path, headers, body in server.requests:
        sent = json.loads(body)
        assert path.endswith("/chat/completions") and sent["model"] == "synthetic/rp-model"
        assert sent["max_tokens"] == 400 and headers["Authorization"] == f"Bearer {key}"
        assert key not in body.decode("utf-8")
    files = outputs(out)
    assert not any(key in text for text in files.values())
    played = json.loads(files["v2-baseline.run.json"])
    assert all(a["measurement"] == "provider_reported" for c in played["calls"] for a in c["attempts"])
    assert "provider-reported (declared by capture, unverified)" in files["comparison-report.txt"]


def test_an_anthropic_baseline_is_played_over_the_messages_api_with_cache_counts_reported(
        tmp_path, monkeypatch):
    key = "sk-loopback-only-0001"
    monkeypatch.setenv("CAPTURE_TEST_KEY", key)
    reply = stream(messages_lines(deltas=("Hm, a quiet evening at the hall.",),
                                  model="synthetic-rp-model"))
    out = tmp_path / "out"
    with sse_server(reply) as server:
        monkeypatch.setitem(v2.ENDPOINTS, "anthropic", base_url(server))
        assert run("--baseline", baseline(tmp_path, "anthropic", live=True), "--out", out, "--live",
                   "--confirm-billing", "--api-key-env", "CAPTURE_TEST_KEY") == 0
    assert len(server.requests) == 6
    for path, headers, body in server.requests:
        sent = json.loads(body)
        assert path == "/v1/messages" and headers["x-api-key"] == key
        assert "Authorization" not in headers and headers["anthropic-version"] == "2023-06-01"
        assert sent["max_tokens"] == 16000 and sent["stream"] is True and sent["system"]
        assert [m["role"] for m in sent["messages"]] == ["user"]
        assert "stream_options" not in sent and key not in body.decode("utf-8")
    files = outputs(out)
    assert not any(key in text for text in files.values())
    played = json.loads(files["v2-baseline.run.json"])
    usage = [a["usage"] for c in played["calls"] for a in c["attempts"]]
    assert all(u["input_tokens"] == 100 and u["cache_read_tokens"] == 10 and u["output_tokens"] == 10
               for u in usage)
    report = files["comparison-report.txt"]
    assert v2.PROTOCOL_NOTES["anthropic"] in report and "usage_incomplete" not in report


def test_a_reply_from_another_model_is_recorded_as_served_and_never_compared_as_matched(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TEST_KEY", "sk-loopback-only-0002")

    def reply(handler):  # the provider resolved the request to another model
        stream(sse_lines(deltas=("Hm, a quiet evening at the hall.",), model="entirely-different-model",
                         usage={"prompt_tokens": 100, "completion_tokens": 10,
                                "prompt_tokens_details": {"cached_tokens": 0}}))(handler)

    out = tmp_path / "out"
    with sse_server(reply) as server:
        monkeypatch.setitem(v2.ENDPOINTS, "openrouter", base_url(server))
        assert run("--baseline", baseline(tmp_path, "openrouter", live=True), "--out", out, "--live",
                   "--confirm-billing", "--api-key-env", "CAPTURE_TEST_KEY") == 0
    files = outputs(out)
    assert json.loads(files["v2-baseline.run.json"])["conditions"]["model"] == "entirely-different-model"
    report = files["comparison-report.txt"]
    assert "incomparable (comparison) model: recording condition differs from the baseline" in report


def another_reply(bundle):
    bundle["turns"][0]["responses"][0]["text"] += " Mind the coin."


def test_several_captures_and_samples_are_gated_together_with_the_ceiling_multiplied(tmp_path):
    first = baseline(tmp_path)
    second = baseline(tmp_path, vary=another_reply, name="-2")
    out = tmp_path / "out"
    assert run("--baseline", first, "--baseline", second, "--out", out, "--dry-run",
               "--samples", 3, "--with-planning") == 0
    files = outputs(out)
    assert set(files) == {"comparison-report.txt",
                          *(f"v2-{kind}-{i}.run.json" for kind in ("baseline", "planning") for i in (1, 2, 3))}
    assert [json.loads(files[f"v2-baseline-{i}.run.json"])["run_id"] for i in (1, 2, 3)] == [
        f"v2-dry-run-baseline-{i}" for i in (1, 2, 3)]
    report = files["comparison-report.txt"]
    assert "Samples: 2 0.2.1 captures, 3 V2 plays of each kind." in report
    assert "== V2 (one call per speaking NPC) against 0.2.1 ==\nstory-quality gate: PASS\n" \
           "samples: 2 baseline, 3 candidate" in report
    assert "samples: 3 baseline, 3 candidate" in report  # planning against the plain samples
    digests = [load_json(directory / "run.json")["source"]["evidence_sha256"] for directory in (first, second)]
    assert f"baseline evidence sha256 {digests[0]}, {digests[1]}" in report
    loaded = v2.load_baseline(first, SCENARIO)
    assert v2.planned_requests(SCENARIO, loaded, with_planning=True, samples=3) == (6 + 12) * 3 * 3


def test_one_sample_of_one_capture_keeps_the_one_run_report(tmp_path):
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path), "--out", out, "--dry-run", "--samples", 1) == 0
    report = outputs(out)["comparison-report.txt"]
    assert "Samples:" not in report and "strict majority" not in report


@pytest.mark.parametrize("samples", [0, 11, -1])
def test_a_sample_count_out_of_range_is_refused(tmp_path, capsys, samples):
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path), "--out", out, "--dry-run", "--samples", samples) == 2
    assert "--samples must be a whole number from 1 to 10" in capsys.readouterr().err
    assert not out.exists()


def test_captures_that_are_the_same_or_of_different_plays_are_refused(tmp_path, capsys):
    first = baseline(tmp_path)
    out = tmp_path / "out"
    assert run("--baseline", first, "--baseline", first, "--out", out, "--dry-run") == 2
    assert "the same capture more than once" in capsys.readouterr().err

    def other_budget(bundle):
        bundle["budget"] = "max_tokens=8000"

    other = baseline(tmp_path, vary=other_budget, name="-other")
    assert run("--baseline", first, "--baseline", other, "--out", out, "--dry-run") == 2
    assert "differ from the first capture's" in capsys.readouterr().err
    live = baseline(tmp_path, live=True)
    assert run("--baseline", first, "--baseline", live, "--out", out, "--dry-run") == 2
    assert "differ from the first capture's" in capsys.readouterr().err
    assert run("--baseline", first, "--baseline", other, "--out", other / "out", "--dry-run") == 2
    assert not out.exists()


def test_no_output_goes_inside_any_baseline(tmp_path, capsys):
    first, second = baseline(tmp_path), baseline(tmp_path, vary=another_reply, name="-2")
    assert run("--baseline", first, "--baseline", second, "--out", second / "out", "--dry-run") == 2
    assert "is inside" in capsys.readouterr().err


def test_a_live_sampled_run_sends_each_sample_and_prints_the_multiplied_ceiling(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CAPTURE_TEST_KEY", "sk-loopback-only-0003")
    reply = stream(messages_lines(deltas=("Hm, a quiet evening at the hall.",), model="synthetic-rp-model"))
    out = tmp_path / "out"
    with sse_server(reply) as server:
        monkeypatch.setitem(v2.ENDPOINTS, "anthropic", base_url(server))
        assert run("--baseline", baseline(tmp_path, live=True), "--out", out, "--live", "--samples", 2,
                   "--confirm-billing", "--api-key-env", "CAPTURE_TEST_KEY") == 0
    assert "at most 36 billed requests to anthropic (synthetic-rp-model) over 2 samples" in capsys.readouterr().err
    assert len(server.requests) == 12
    files = outputs(out)
    assert {"v2-baseline-1.run.json", "v2-baseline-2.run.json"} <= set(files)
    assert "samples: 1 baseline, 2 candidate" in files["comparison-report.txt"]


def a_retried_reply(bundle):
    """0.2.1 rejected turn 1's first reply once and kept the reply its Retry got."""
    rid = bundle["turns"][0]["responses"][0]["response_id"]
    row = next(r for r in bundle["usage_rows"] if r.get("response_id") == rid)
    at = bundle["usage_rows"].index(row)
    bundle["usage_rows"].insert(at, {**row, "prompt_tokens": 700, "completion_tokens": 9,
                                     "cache_read_tokens": 0})
    bundle["format"] = "grimoire.v021-capture-evidence/2"
    bundle["retries"] = [{"turn": 1, "response_id": rid, "kind": "empty_response"}]


def test_a_capture_with_retries_is_a_baseline_and_the_report_says_what_was_left_out(tmp_path):
    first = baseline(tmp_path)
    retried = baseline(tmp_path, vary=a_retried_reply, name="-retried")
    out = tmp_path / "out"
    assert run("--baseline", first, "--baseline", retried, "--out", out, "--dry-run") == 0
    report = outputs(out)["comparison-report.txt"]
    assert ("0.2.1 rejected 1 reply as empty in 1 of the 2 captures, and its own Retry asked "
            "again. Those replies took 1 request, input tokens 700, output tokens 9, cache reads 0. "
            "They are not in the 0.2.1 side") in report
    assert "story-quality gate: PASS" in report


def test_captures_without_retries_say_nothing_about_them(tmp_path):
    out = tmp_path / "out"
    assert run("--baseline", baseline(tmp_path), "--out", out, "--dry-run") == 0
    assert "Retry" not in outputs(out)["comparison-report.txt"]


def compared(tmp_path, *, samples=3):
    first = baseline(tmp_path)
    second = baseline(tmp_path, vary=another_reply, name="-2")
    earlier = tmp_path / "earlier"
    assert run("--baseline", first, "--baseline", second, "--out", earlier, "--dry-run",
               "--samples", samples, "--with-planning") == 0
    return first, second, earlier


def test_a_rescore_reads_an_earlier_comparison_back_and_sends_nothing(tmp_path, capsys, monkeypatch):
    first, second, earlier = compared(tmp_path)
    monkeypatch.setattr(v2, "play", lambda *a, **k: pytest.fail("a rescore must not play"))
    out = tmp_path / "rescored"
    assert run("--baseline", first, "--baseline", second, "--rescore", earlier, "--out", out) == 0
    assert "rescored comparison written to" in capsys.readouterr().out
    files = outputs(out)
    assert set(files) == {"comparison-report.txt"}
    before = outputs(earlier)["comparison-report.txt"]
    after = files["comparison-report.txt"]
    note = next(part for part in after.split("\n\n") if part.startswith("Rescored: "))
    assert "Nothing was sent and nothing was billed." in note and "earlier dry_run comparison" in note
    assert after.replace(note + "\n\n", "") == before  # same runs, same scenario: same verdict


def test_a_rescore_scores_with_the_current_markers(tmp_path, monkeypatch):
    import dataclasses
    first, second, earlier = compared(tmp_path)
    narrower = dataclasses.replace(SCENARIO, choices=tuple(
        dataclasses.replace(choice, effect_markers=("no reply says this",))
        if choice.choice_id == "ask-ledger" else choice for choice in SCENARIO.choices))
    monkeypatch.setattr(v2, "load_scenario", lambda _source: narrower)
    out = tmp_path / "rescored"
    assert run("--baseline", first, "--baseline", second, "--rescore", earlier, "--out", out) == 0
    report = outputs(out)["comparison-report.txt"]
    assert "story-quality gate: FAIL" in report
    assert "  - ask-ledger: 0/2 / 0/3" in report


def test_a_single_sample_comparison_can_be_rescored(tmp_path):
    first = baseline(tmp_path)
    earlier = tmp_path / "earlier"
    assert run("--baseline", first, "--out", earlier, "--dry-run") == 0
    out = tmp_path / "rescored"
    assert run("--baseline", first, "--rescore", earlier, "--out", out) == 0
    assert "story-quality gate: PASS" in outputs(out)["comparison-report.txt"]


@pytest.mark.parametrize("extra, message", [
    (["--samples", 3], "drop --samples"),
    (["--with-planning"], "drop --with-planning"),
    (["--api-key-env", "ANTHROPIC_API_KEY", "--confirm-billing"], "drop --api-key-env, --confirm-billing"),
])
def test_a_rescore_takes_nothing_a_play_would(tmp_path, capsys, extra, message):
    first, second, earlier = compared(tmp_path, samples=2)
    assert run("--baseline", first, "--baseline", second, "--rescore", earlier,
               "--out", tmp_path / "rescored", *extra) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "rescored").exists()


def test_a_rescore_needs_the_same_captures_in_the_same_order(tmp_path, capsys):
    first, second, earlier = compared(tmp_path, samples=2)
    for baselines in ([second, first], [first], [first, baseline(tmp_path, name="-3", vary=lambda b: b[
            "turns"][1]["responses"][0].__setitem__("text", b["turns"][1]["responses"][0]["text"] + " Hm."))]):
        flags = [arg for directory in baselines for arg in ("--baseline", directory)]
        assert run(*flags, "--rescore", earlier, "--out", tmp_path / "rescored") == 2
        assert "the captures the earlier comparison used" in capsys.readouterr().err
    assert not (tmp_path / "rescored").exists()


def test_a_rescore_refuses_what_is_not_a_comparison_and_writes_nowhere_inside_it(tmp_path, capsys):
    first, second, earlier = compared(tmp_path, samples=2)
    flags = ["--baseline", first, "--baseline", second]
    assert run(*flags, "--rescore", tmp_path / "missing", "--out", tmp_path / "rescored") == 2
    assert "not a comparison's output directory" in capsys.readouterr().err
    assert run(*flags, "--rescore", earlier, "--out", earlier / "again") == 2
    capsys.readouterr()
    (earlier / "v2-planning-2.run.json").unlink()
    assert run(*flags, "--rescore", earlier, "--out", tmp_path / "rescored") == 2
    assert "do not pair up" in capsys.readouterr().err
    (earlier / "v2-baseline-1.run.json").unlink()
    assert run(*flags, "--rescore", earlier, "--out", tmp_path / "rescored") == 2
    assert "not numbered 1 to 1" in capsys.readouterr().err
    (earlier / "comparison-report.txt").write_text("something else\n", "utf-8")
    assert run(*flags, "--rescore", earlier, "--out", tmp_path / "rescored") == 2
    assert "names no 0.2.1 baselines" in capsys.readouterr().err
    assert not (tmp_path / "rescored").exists()


def test_a_rescore_copies_the_earlier_reports_account_of_its_run(tmp_path, capsys):
    first, second, earlier = compared(tmp_path, samples=2)
    report = earlier / "comparison-report.txt"
    flags = ["--baseline", first, "--baseline", second, "--rescore", earlier]
    text = report.read_text("utf-8")
    report.write_text(text.replace("Dry run: scripted", "Dry run, as written then: scripted"), "utf-8")
    assert run(*flags, "--out", tmp_path / "rescored") == 0
    rescored = outputs(tmp_path / "rescored")["comparison-report.txt"]
    assert "Dry run, as written then: scripted" in rescored  # copied, never regenerated
    report.write_text(text.replace(": dry_run, provider", ": live, provider", 1), "utf-8")
    assert run(*flags, "--out", tmp_path / "again") == 2
    assert "its report says live, but the captures are dry_run" in capsys.readouterr().err
    assert not (tmp_path / "again").exists()


def test_a_rescore_reads_no_linked_files(tmp_path, capsys):
    first, second, earlier = compared(tmp_path, samples=2)
    elsewhere = tmp_path / "elsewhere.run.json"
    (earlier / "v2-baseline-2.run.json").rename(elsewhere)
    (earlier / "v2-baseline-2.run.json").symlink_to(elsewhere)
    assert run("--baseline", first, "--baseline", second, "--rescore", earlier,
               "--out", tmp_path / "rescored") == 2
    assert "v2-baseline-2.run.json is a link" in capsys.readouterr().err
