"""Continuity acceptance evidence and eval counterexamples.

Appendix B records each acceptance criterion and maps §28.10's eval cases to
their counterexample recordings. These tests check the tables, completion
records, and declared grader outcomes, including a replay of case 10's
compliant anchor-on recording.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402

SPEC = REPO / "docs" / "superpowers" / "specs" / "2026-10-04-continuity-capstone-design.md"
PHASE_STATUS = REPO / "docs" / "superpowers" / "PHASE-STATUS.md"
HEADING = "# Appendix B. Acceptance evidence"
CRITERIA = [f"AC{n}" for n in range(1, 20)] + ["§33"]

BACKEND = re.compile(r"`(test_\w+\.py)::(test_\w+)`")
FRONTEND = re.compile(r'`(frontend/src/[\w./-]+\.test\.tsx?)` "([^"]+)"')
EVAL = re.compile(r"eval `([\w-]+)`")
TICKED = re.compile(r"`([^`]+)`")


def _appendix() -> str:
    """The text from Appendix B's heading to the next `# Appendix` heading or
    the end of the spec."""
    text = SPEC.read_text(encoding="utf-8")
    start = text.find("\n" + HEADING + "\n")
    assert start >= 0, f"the spec has no {HEADING!r} heading"
    body = text[start + 1:]
    end = body.find("\n# Appendix", len(HEADING))
    return body if end < 0 else body[:end]


def _rows(header: str) -> list[list[str]]:
    """The cells of each body row of the table whose header row starts with
    `| <header> |`."""
    lines = _appendix().splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"| {header} |")]
    assert len(starts) == 1, f"expected one table headed {header!r}, found {len(starts)}"
    rows = []
    for line in lines[starts[0] + 2:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _criteria() -> dict[str, str]:
    return {cells[0]: cells[2] for cells in _rows("Criterion")}


def test_every_criterion_has_a_row():
    rows = _criteria()
    assert sorted(rows) == sorted(CRITERIA)
    for name, evidence in rows.items():
        if name == "AC19":
            for needed in ("`make check`", "`evals/run.py`", "Slice G"):
                assert needed in evidence, f"AC19 does not name {needed}"
            continue
        cited = BACKEND.findall(evidence) + FRONTEND.findall(evidence) + EVAL.findall(evidence)
        assert cited, f"{name} cites no test or eval case"


def test_ac19s_gate_bullet_and_the_completion_records_exist():
    text = SPEC.read_text(encoding="utf-8")
    start = text.find("\n### Slice G")
    end = text.find("\n# 32.", start)
    assert 0 <= start < end, "the spec has no §31 Slice G section before §32"
    bullets = [line for line in text[start:end].splitlines() if line.startswith("- ")]
    assert any("`/codex:review`" in line and "`/codex:adversarial-review`" in line
               for line in bullets), "§31 Slice G has no gate bullet naming both final reviews"
    status = next(line for line in text.splitlines() if line.startswith("**Status:**"))
    assert "Ready for implementation planning" not in status
    assert status.rstrip().endswith("Implemented in Slices A\u2013G (§31); acceptance evidence "
                                     "in Appendix B; continuity is parked (§33).")
    phase = PHASE_STATUS.read_text(encoding="utf-8")
    assert "\n## Continuity capstone (2026-10)\n" in phase
    assert ("Continuity is parked (spec §33): further work here is correctness defects and "
            "small usability fixes; the next major investment is mechanics.") in phase


def test_cited_eval_cases_exist():
    cited = EVAL.findall(_appendix())
    assert cited
    assert sorted(set(cited) - set(case_mod.BY_ID)) == []


def _clauses(cells: list[str]) -> list[tuple[str, list[str], str]]:
    """A §28.10 row's `(eval case, checks, variant)` clauses. Each of the three
    cells lists its clauses separated by `;`, in the same order."""
    split = [cell.split(";") for cell in cells[1:4]]
    assert len({len(parts) for parts in split}) == 1, f"row {cells[0]}: clauses do not align"
    out = []
    for case, checks, variant in zip(*split, strict=True):
        cases, names, variants = TICKED.findall(case), TICKED.findall(checks), \
            TICKED.findall(variant)
        assert len(cases) == 1 and names and len(variants) == 1, f"row {cells[0]}: {cells}"
        out.append((cases[0], names, variants[0]))
    return out


def test_section_28_10_cases_each_have_a_tripping_counterexample(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    rows = _rows("§28.10 case")
    assert [cells[0] for cells in rows] == [str(n) for n in range(1, 11)]
    for cells in rows:
        for case_id, checks, variant in _clauses(cells):
            assert case_id in case_mod.BY_ID, f"case {cells[0]}: no eval case {case_id}"
            case = case_mod.BY_ID[case_id]
            recording = next((r for r in case.recordings if r.variant == variant), None)
            assert recording is not None, f"case {cells[0]}: {case_id} has no {variant!r}"
            if recording.expect_pass:
                # Only case 10's anchor-on clause may stand on a compliant
                # recording: show the check is graded there and passes.
                assert cells[0] == "10", f"case {cells[0]}: {variant!r} trips nothing"
                result = runner.replay(case, recording)
                graded = {c.name: c.ok for c in result.checks}
                for check in checks:
                    assert graded.get(check) is True, f"case 10: {check} not graded or failing"
                continue
            for check in checks:
                assert check in recording.expect_fail, \
                    f"case {cells[0]}: {case_id}.{variant} does not trip {check}"

