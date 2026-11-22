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
