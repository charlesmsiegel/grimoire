"""`evals/runfile.py` and `--compare`: the eval-run v1 file, read back and
compared offline from synthetic files alone (spec 01a, section 8; section 11
tests 11 and 22)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import costs, run, runfile  # noqa: E402


def _bucket(cost: float | None = None, prompt: int | None = None) -> dict:
    row: dict = {"task": "chat", "status": "ok"}
    if cost is not None:
        row["cost_usd"] = cost
    if prompt is not None:
        row.update(prompt_tokens=prompt, completion_tokens=1)
    return costs.fold([row])


def _entry(case: str, configs: list[str], *, passed: bool = True, wall: int | None = 1000,
           bucket: dict | None = None, items: list | None = None,
           calls: list | None = None, repeat: int = 0) -> dict:
    return {"configs": configs, "case": case, "variant": "compliant", "repeat": repeat,
            "task": "chat", "operation": "decide" if items else "generate",
            "passed": passed, "checks": [], "wall_ms": wall, "error": None,
            "partial": False, "ledger_unreadable": False, "items": items or [],
            "calls": calls or [], "rows": [], "bucket": bucket}


def _doc(configs: list[dict], cases: list[dict], **extra) -> dict:
    return {"format": runfile.FORMAT, "version": runfile.VERSION, "run": "r",
            "started": "2026-10-10T00:00:00Z", "configs": configs, "cases": cases,
            "aggregates": {"by_route_backend_hop": []}, **extra}


def _write(path: Path, doc: dict) -> Path:
    runfile.write(path, doc)
    return path


def _native_call(cost: float) -> dict:
    return {"stage": 0, "mode": "native", "items": [0], "hop": "", "row": None,
            "error": None, "bucket": _bucket(cost)}


def test_compare_reads_two_files_and_leaves_missing_cells_blank(tmp_path, capsys):
    """Test 11: one table across both files' configs; a case one file lacks
    is a blank cell, never a zero. Native item money is the call's own; a
    structured chunk's is `(chunk)`."""
    item = {"index": 0, "backend": "native", "stage": 0, "reasons": [],
            "distribution": True, "escalated": False, "call": 0}
    first = _doc([runfile.config("c1", "openrouter / vendor/m [native]",
                                 {"backend": "native"})],
                 [_entry("decide-speaker", ["c1"], bucket=_bucket(0.0006),
                         items=[item], calls=[_native_call(0.0006)]),
                  _entry("scene-length", ["c1"], bucket=_bucket(0.01, prompt=40))])
    chunk = {**item, "backend": "structured"}
    second = _doc([runfile.config("c1", "openrouter / vendor/m [structured]",
                                  {"backend": "structured", "escalation": "on"})],
                  [_entry("decide-speaker", ["c1"], passed=False, bucket=_bucket(0.0009),
                          items=[chunk], calls=[{**_native_call(0.0009),
                                                 "mode": "structured"}])],
                  unknown_top_level_key=True)
    a, b = _write(tmp_path / "a.json", first), _write(tmp_path / "b.json", second)
    assert run.main(["--compare", str(a), str(b)]) == 0
    table = capsys.readouterr().out.splitlines()
    header, speaker, item_row, length, totals = table
    assert "a:c1 openrouter / vendor/m [native]" in header
    assert "b:c1 openrouter / vendor/m [structured]" in header
    assert "1/1 ok" in speaker and "0/1 FAIL" in speaker
    assert "$0.0006" in item_row and "(chunk)" in item_row
    cells = [cell.strip() for cell in length.split("|")]
    assert cells[0] == "scene-length" and cells[1].endswith("billed $0.01")
    assert cells[2] == ""                                # blank, never zero
    assert [c.strip() for c in totals.split("|")] == ["totals", "billed $0.01", "billed $0.0009"]


def test_an_unknown_version_is_refused_with_one_sentence(tmp_path, capsys):
    newer = _write(tmp_path / "newer.json", {**_doc([], []), "version": 2})
    assert run.main(["--compare", str(newer)]) == 2
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1 and "eval-run v1" in err
    garbled = tmp_path / "garbled.json"
    garbled.write_text("{not json", encoding="utf-8")
    assert run.main(["--compare", str(garbled)]) == 2


@pytest.mark.parametrize("flags", [["--live"], ["--gate"], ["--out", "x.json"],
                                   ["--repeat", "2"], ["--case", "scene-length"]])
def test_compare_refuses_every_other_mode_flag(tmp_path, flags):
    path = _write(tmp_path / "a.json", _doc([], []))
    with pytest.raises(SystemExit) as exc:
        run.main(["--compare", str(path), *flags])
    assert exc.value.code == 2


def test_a_generate_case_fills_every_config_it_stands_for(tmp_path, capsys):
    """Test 22: one generate case listed under `["c1", "c2"]` fills both
    columns; an `axes` key this reader has never heard of is read."""
    doc = _doc([runfile.config("c1", "x [native]", {"backend": "native", "feature": "z"}),
                runfile.config("c2", "x [structured]", {"backend": "structured"})],
               [_entry("scene-length", ["c1", "c2"], bucket=_bucket(0.002, prompt=8))])
    path = _write(tmp_path / "run.json", doc)
    assert run.main(["--compare", str(path)]) == 0
    (_header, row, _totals) = capsys.readouterr().out.splitlines()
    assert row.count("billed $0.0020") == 2


def test_repeats_are_summed_and_their_wall_is_the_median(tmp_path, capsys):
    doc = _doc([runfile.config("c1", "x [chain]", {"backend": "chain"})],
               [_entry("scene-length", ["c1"], wall=w, bucket=_bucket(0.001, prompt=10),
                       repeat=n, passed=n != 1)
                for n, w in enumerate((1000, 3000, 2000))])
    assert run.main(["--compare", str(_write(tmp_path / "r.json", doc))]) == 0
    row = capsys.readouterr().out.splitlines()[1]
    assert "2/3 FAIL  2.00s  30 / 3  billed $0.0030" in row


def test_a_replay_file_compares_with_no_money(tmp_path, capsys):
    """Open question 2: a replay run's file has no rows; its cells say `-`."""
    out = tmp_path / "replay.json"
    assert run.main(["--case", "scene-length", "--out", str(out)]) == 0
    capsys.readouterr()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["format"] == "eval-run" and doc["version"] == 1
    assert {tuple(c["configs"]) for c in doc["cases"]} == {("c1",)}
    assert run.main(["--compare", str(out)]) == 0
    assert "1/1 ok  -  - / -  -" in capsys.readouterr().out
