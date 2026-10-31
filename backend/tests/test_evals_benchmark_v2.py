"""The shipped /2 synthetic baseline, the 0.2.1 capture status, and no checked-in measurement."""

import json
from pathlib import Path

from evals_helpers import guild_run_v2, load_benchmark
from grimoire.evals.format import parse_scenario
from grimoire.evals.format2 import FORMAT_V2, parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import benchmark_file, load_json
from grimoire.evals.v021 import EVIDENCE_FORMAT

ROOT = Path(__file__).resolve().parents[2]
STATUS = "baseline capture pending"


def shipped_v2():
    raw_scenario, _ = load_benchmark()
    scenario = parse_scenario(raw_scenario)
    return scenario, parse_run_v2(load_json(benchmark_file("guild_betrayal", "baseline.v2.json")),
                                  scenario)


def test_shipped_v2_baseline_passes_against_itself():
    scenario, run = shipped_v2()
    report = compare_v2(scenario, run, run)
    assert report.passed, report.findings
    assert report.baseline.status == "synthetic"
    assert len(run.calls) == 6


def test_v2_baseline_is_the_v1_baseline_without_narration():
    _, expected = guild_run_v2()
    assert load_json(benchmark_file("guild_betrayal", "baseline.v2.json")) == expected
    _, v1 = load_benchmark()
    _, run = shipped_v2()
    v1_calls = {c["call_id"]: c for c in v1["calls"] if c["purpose"] == "npc_generation"}
    for call in run.calls:
        (attempt,) = call.attempts
        source = v1_calls[call.call_id]
        assert attempt.usage.input_tokens == source["input_tokens"]
        assert attempt.usage.cache_read_tokens == source["cached_input_tokens"]
        assert attempt.usage.output_tokens == source["output_tokens"]
        assert call.latency_ms == source["latency_ms"]


def _checked_in_json():
    for base in (ROOT / "backend" / "src" / "grimoire", ROOT / "backend" / "tests"):
        for path in sorted(base.rglob("*.json")):
            yield path, json.loads(path.read_text("utf-8"))


def _measurements(data):
    if isinstance(data, dict):
        if "measurement" in data:
            yield data["measurement"]
        for value in data.values():
            yield from _measurements(value)
    elif isinstance(data, list):
        for value in data:
            yield from _measurements(value)


def test_no_checked_in_v2_run_is_live_or_provider_reported():
    seen = 0
    for path, data in _checked_in_json():
        if not isinstance(data, dict) or data.get("format") not in (FORMAT_V2, EVIDENCE_FORMAT):
            continue
        seen += 1
        mode = data.get("mode") or (data.get("source") or {}).get("mode")
        assert mode != "live", path
        assert "provider_reported" not in set(_measurements(data)), path
    assert seen >= 1


def test_status_string_pinned():
    for doc in ("docs/evals/V021_BASELINE.md", "docs/roadmap/sprints/v1-cost-and-voice-regression.md"):
        assert STATUS in (ROOT / doc).read_text("utf-8"), doc
