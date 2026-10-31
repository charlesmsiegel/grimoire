"""The 0.2.1 driver's request accounting (its pure part; the driver imports 0.2.1 lazily)."""

import importlib.util
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "tools" / "v021_driver.py"


def driver():
    spec = importlib.util.spec_from_file_location("v021_driver_under_test", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(*attempts):
    return [{"kind": "llm", "task": "chat", **({"attempts": a} if a != 1 else {})}
            for a in attempts] + [{"kind": "rename"}]


def test_a_retried_call_is_accounted_not_fatal():
    d = driver()
    d.check_accounting(rows(1, 2, 1), forwarded=4, planned=3, budget=9)


@pytest.mark.parametrize("forwarded, attempts, says", [
    (2, (1, 1), "fewer"),            # a planned call never went out
    (4, (1, 1, 1), "do not add up"),  # a request no row accounts for
    (10, (4, 3, 3), "budget"),
])
def test_unaccounted_requests_fail(forwarded, attempts, says):
    d = driver()
    with pytest.raises(d.DriverError, match=says):
        d.check_accounting(rows(*attempts), forwarded=forwarded, planned=3, budget=9)


def test_partial_ledger_is_counts_only():
    d = driver()
    summary = d.partial_ledger([{"kind": "llm", "task": "chat", "response_id": "r1",
                                 "prompt_tokens": 10, "completion_tokens": 2, "status": "ok",
                                 "model": "m", "content": "secret text"}])
    assert summary == [{"task": "chat", "response_id": "r1", "attempts": 1, "status": "ok",
                        "prompt_tokens": 10, "completion_tokens": 2, "cache_read_tokens": None,
                        "cache_write_tokens": None, "cost_usd": None}]


def test_any_driver_crash_reports_the_partial_ledger(tmp_path, monkeypatch, capsys):
    d = driver()
    usage = tmp_path / "usage"
    usage.mkdir()
    (usage / "2026-10.jsonl").write_text('{"kind": "llm", "task": "chat", "prompt_tokens": 5}\n')
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def crash(argv):
        raise KeyError("unexpected")
    monkeypatch.setattr(d, "main", crash)
    with pytest.raises(SystemExit) as exit_:
        d.run(["--whatever"])
    assert exit_.value.code == 1
    err = capsys.readouterr().err
    assert "partial ledger" in err and '"prompt_tokens": 5' in err
