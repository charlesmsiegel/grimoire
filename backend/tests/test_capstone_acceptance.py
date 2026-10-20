"""Continuity acceptance scenarios exercise the intended replay graders.

These inputs used to be extracted from an acceptance table in a design document.
They are test fixtures: moving or rewriting human documentation must not change
which mistakes the replay suite can detect. The general recording tests check
every declared outcome; this matrix keeps the intended continuity scenarios tied
to the checks that distinguish them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402

# Each fixture names the case, recording, checks and expected check outcome.
# A compliant anchor-on batch is deliberate: its date comes from the anchor,
# so the useful assertion is that the date check runs and accepts that result.
SCENARIOS = (
    ("decide-continuity-identity", "unknown-id", ("identity.same_obligation",), False),
    ("decide-continuity-reconcile", "merged", ("reconcile.distinct",), False),
    ("decide-continuity-identity", "merged", ("identity.distinct",), False),
    ("decide-continuity-reconcile", "merged", ("reconcile.continuation",), False),
    ("decide-continuity-identity", "merged", ("identity.continuation",), False),
    ("decide-continuity-reconcile", "timid", ("reconcile.cross_type",), False),
    ("decide-continuity-reconcile", "timid", ("reconcile.close",), False),
    ("decide-continuity-reconcile", "eager", ("reconcile.keep_open",), False),
    ("decide-continuity-reconcile", "timid", ("reconcile.fulfilled",), False),
    ("decide-continuity-reconcile", "eager", ("reconcile.unproven",), False),
    ("scene-suggestions", "cloned", ("suggest.focus_coverage", "suggest.distinct"), False),
    ("scene-suggestions", "one-premise", ("suggest.distinct", "suggest.focus_spread"), False),
    ("scene-suggestions", "bad-date", ("suggest.date_consistent",), False),
    ("scene-suggestions-anchor-on", "compliant", ("suggest.on_derived",), True),
)


@pytest.mark.parametrize("case_id,variant,checks,expected", SCENARIOS,
                         ids=[f"{case}.{variant}:{'+'.join(checks)}"
                              for case, variant, checks, _ in SCENARIOS])
def test_continuity_acceptance_scenarios_are_graded(
        monkeypatch, tmp_path, case_id, variant, checks, expected):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID[case_id]
    recording = next(r for r in case.recordings if r.variant == variant)
    assert recording.expect_pass is expected

    # replay() summarizes a counterexample as one pass/fail check. score()
    # retains the individual grader outcomes this acceptance matrix names.
    output = recording.path(case.id).read_text(encoding="utf-8")
    result = runner.score(case, variant, output, recording.native)
    assert not result.error, result.error
    graded = {check.name: check.ok for check in result.checks}
    for name in checks:
        assert graded.get(name) is expected, f"{case_id}.{variant}: {name} = {graded.get(name)}"
