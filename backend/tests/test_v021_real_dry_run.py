"""Opt-in: drive the real, unmodified 0.2.1 app through a dry-run capture.

Skipped unless GRIMOIRE_V021_CHECKOUT (a clean checkout of the pinned commit) and
GRIMOIRE_V021_PYTHON (an interpreter with that checkout installed) are set. Never billed.
"""

import json
import os
from pathlib import Path

import pytest

from grimoire.capture import v021 as capture
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.loader import benchmark_file, load_scenario

CHECKOUT = os.environ.get("GRIMOIRE_V021_CHECKOUT")
PYTHON = os.environ.get("GRIMOIRE_V021_PYTHON")
pytestmark = pytest.mark.skipif(not (CHECKOUT and PYTHON),
                                reason="set GRIMOIRE_V021_CHECKOUT and GRIMOIRE_V021_PYTHON")


def real_state() -> tuple:
    import pwd  # POSIX only; 0.2.1's lock directory comes from the account's home
    state = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "grimoire"
    return state.exists(), sorted(p.name for p in state.rglob("*")) if state.exists() else []


@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_real_dry_run(kind, tmp_path):
    before = real_state()
    out = tmp_path / "out"
    assert capture.main(["--scenario", "guild_betrayal", "--v1-checkout", CHECKOUT,
                         "--v1-python", PYTHON, "--dry-run", "--provider", kind,
                         "--out", str(out)]) == 0
    assert real_state() == before
    evidence = json.loads((out / "evidence.json").read_text())
    assert len([r for r in evidence["usage_rows"] if r.get("kind") == "llm"]) == 6
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    run = parse_run_v2(json.loads((out / "run.json").read_text()), scenario)
    assert len(run.calls) == 6 and run.source.mode == "dry_run"
    assert "findings (0):" in (out / "capture-report.txt").read_text()


def test_real_dry_run_with_a_retry_is_captured_as_incomplete(tmp_path, monkeypatch):
    # 0.2.1 fails the first call once and retries it: the capture still succeeds, the retried
    # call books an unreported failed attempt, and the run is "incomplete", never complete.
    monkeypatch.setattr(capture, "DRIVER_EXTRA_ARGS", ("--fault", "retry-first"))
    out = tmp_path / "out"
    assert capture.main(["--scenario", "guild_betrayal", "--v1-checkout", CHECKOUT,
                         "--v1-python", PYTHON, "--dry-run", "--provider", "openrouter",
                         "--out", str(out)]) == 0
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    run = parse_run_v2(json.loads((out / "run.json").read_text()), scenario)
    assert run.usage.total.attempts == 7
    assert [a.outcome for a in run.calls[0].attempts] == ["failed", "completed"]
    assert not run.usage.complete
    assert "incomplete" in (out / "capture-report.txt").read_text()


@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_real_dry_run_names_why_a_turn_failed(kind, tmp_path, monkeypatch):
    # Turn 1's second speaker answers only under the PC's label: 0.2.1's authority check drops
    # it, its empty-response check fails the turn, and the failure says so in kinds and ids.
    monkeypatch.setattr(capture, "DRIVER_EXTRA_ARGS", ("--fault", "second-speaks-as-pc"))
    out = tmp_path / "out"
    with pytest.raises(capture.CaptureFailed) as failed:
        capture.run_capture(capture.CaptureOptions(
            scenario="guild_betrayal", v1_checkout=Path(CHECKOUT), v1_python=Path(PYTHON),
            mode="dry_run", provider=kind, out=out))
    message = str(failed.value)
    assert "turn 1 failed: HTTP 200; 0.2.1 error empty_response" in message
    assert "npc-oda's reply: 54 characters streamed" in message
    assert "npc-bellamy's reply: " in message
    assert ("provider reply 1: 54 characters, 54 of prose, no fence, prose under no label"
            in message)
    assert ("provider reply 2: 40 characters, 40 of prose, no fence, prose under the label "
            "pc-wren") in message
    assert "ledger" not in message.split("partial ledger")[0]  # no reply text
    assert not out.exists() or not any(out.iterdir())


@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_real_dry_run_reads_a_fenced_reply_as_0_2_1_does(kind, tmp_path, monkeypatch):
    # Turn 1's second reply holds only the perception block and the handoff fence 0.2.1's prompt
    # asks for: 0.2.1 hides both, keeps no prose and fails the turn, and the report says so.
    monkeypatch.setattr(capture, "DRIVER_EXTRA_ARGS", ("--fault", "second-only-fences"))
    with pytest.raises(capture.CaptureFailed) as failed:
        capture.run_capture(capture.CaptureOptions(
            scenario="guild_betrayal", v1_checkout=Path(CHECKOUT), v1_python=Path(PYTHON),
            mode="dry_run", provider=kind, out=tmp_path / "out"))
    message = str(failed.value)
    assert "0.2.1 error empty_response" in message
    assert "provider reply 2: " in message
    assert "0 of prose, fences perception, handoff, prose under no label" in message
    assert "ledger is on the hook" not in message


def capture_with(tmp_path, monkeypatch, fault, *, kind="openrouter", retry=True):
    monkeypatch.setattr(capture, "DRIVER_EXTRA_ARGS", ("--fault", fault) if fault else ())
    out = tmp_path / "out"
    capture.run_capture(capture.CaptureOptions(
        scenario="guild_betrayal", v1_checkout=Path(CHECKOUT), v1_python=Path(PYTHON),
        mode="dry_run", provider=kind, out=out, retry_empty=retry))
    return out


@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_real_dry_run_retries_a_rejected_reply_with_0_2_1_s_own_retry(kind, tmp_path,
                                                                       monkeypatch):
    # Turn 1's second reply holds only fences; 0.2.1 rejects it, the driver presses 0.2.1's
    # Retry, 0.2.1 asks the same prompt again under the same response id and keeps the reply.
    out = capture_with(tmp_path, monkeypatch, "second-rejected-once", kind=kind)
    evidence = json.loads((out / "evidence.json").read_text())
    assert evidence["format"] == "grimoire.v021-capture-evidence/2"
    (retry,) = evidence["retries"]
    assert retry["turn"] == 1 and retry["kind"] == "empty_response"
    rows = [r for r in evidence["usage_rows"] if r.get("kind") == "llm"]
    assert len(rows) == 7 and sum(r["response_id"] == retry["response_id"] for r in rows) == 2
    scenario = load_scenario(benchmark_file("guild_betrayal", "scenario.json"))
    run = parse_run_v2(json.loads((out / "run.json").read_text()), scenario)
    assert len(run.calls) == 6 and run.usage.total.attempts == 6 and run.usage.complete
    report = (out / "capture-report.txt").read_text()
    assert "0.2.1 rejected 1 reply as empty and Retry asked again: 1 request," in report
    assert "findings (0):" in report
    kept = [c.text for t in run.turns for c in t.contributions if c.call_id == "t1-npc-bellamy"]
    assert kept and "ledger is on the hook" not in kept[0]


def test_real_dry_run_with_retry_and_nothing_rejected_is_a_v1_play(tmp_path, monkeypatch):
    out = capture_with(tmp_path, monkeypatch, None)
    evidence = json.loads((out / "evidence.json").read_text())
    assert evidence["format"] == "grimoire.v021-capture-evidence/2" and evidence["retries"] == []
    assert "rejected no reply as empty" in (out / "capture-report.txt").read_text()


def test_real_dry_run_retries_stop_at_the_request_ceiling(tmp_path, monkeypatch):
    # Every Retry is rejected too: the driver stops while one request is left for each of the
    # five replies still owed (18 requests in all), and nothing is written.
    with pytest.raises(capture.CaptureFailed) as failed:
        capture_with(tmp_path, monkeypatch, "second-always-rejected")
    message = str(failed.value)
    assert "turn 1 failed after 12 Retries: HTTP 200; 0.2.1 error empty_response" in message
    assert "no Retry left: 14 of 18 requests sent and 5 replies still owed" in message
    assert "ledger is on the hook" not in message
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_real_dry_run_without_the_flag_still_fails_a_rejected_reply(tmp_path, monkeypatch):
    with pytest.raises(capture.CaptureFailed, match="0.2.1 error empty_response"):
        capture_with(tmp_path, monkeypatch, "second-rejected-once", retry=False)
