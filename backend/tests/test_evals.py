"""The eval suite, run in replay mode as part of the ordinary test run.

This repo has no CI, so pytest IS the gate — an eval suite that only ran when
someone remembered to invoke it would not catch the prompt edit it exists to
catch. Replay is offline and deterministic, so it belongs here; the live mode
(`evals/run.py --live`) costs money and is never a test.

The suite lives at the repo root rather than under backend/src, because the
Android build packages backend/src verbatim into the APK.
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

PAIRS = [(case, rec) for case in case_mod.CASES for rec in case.recordings]


@pytest.mark.parametrize("case,recording", PAIRS,
                         ids=[f"{c.id}.{r.variant}" for c, r in PAIRS])
def test_recording_scores_as_declared(monkeypatch, tmp_path, case, recording):
    """A must-PASS recording passes; a must-FAIL one still fails.

    The second half is the load-bearing one. A grader that quietly stopped
    detecting anything would leave every must-PASS case green, and only the
    counterexamples notice.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(case, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(
        f"{c.name}: {c.detail}" for c in result.failures)


def test_every_case_has_a_recordable_baseline():
    """`--record` writes to the baseline variant; a case without one would
    silently record nothing on a live run."""
    for case in case_mod.CASES:
        assert any(r.variant == case_mod.BASELINE for r in case.recordings), case.id
        assert any(not r.expect_pass for r in case.recordings), \
            f"{case.id} has no counterexample — its graders are unproven"


def test_no_orphan_recordings():
    """Every file in recordings/ is claimed by a case. Renaming a case without
    deleting its old files would otherwise leave dead fixtures behind that no
    longer score anything."""
    declared = {rec.path(case.id).name
                for case in case_mod.CASES for rec in case.recordings}
    on_disk = {p.name for p in case_mod.RECORDINGS.iterdir() if p.is_file()}
    assert on_disk == declared, f"orphaned: {sorted(on_disk - declared)}"


def test_every_case_names_a_routed_task():
    """A live run sends each case where the app sends that generation, so each
    case names the task the app meters it under, and a route claims it."""
    from grimoire.store import routing
    for case in case_mod.CASES:
        assert case.task in routing.TASK_ROUTE, (case.id, case.task)
    assert case_mod.BY_ID["absorb"].task == "absorb"
    assert case_mod.BY_ID["scene-length"].task == "chat"


def test_a_live_run_resolves_through_the_seam_not_the_active_connection(
        monkeypatch, tmp_path):
    """On a migrated store `active_connection_id` is frozen for older builds;
    a live pass must be scored on the model the app plays on now."""
    from grimoire.store import config, llm_connections
    from grimoire.store.inference import migrate
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    llm_connections.create_connection("openai_compatible", "Winifred Local",
                                      base_url="http://localhost:5678/v1")
    assert migrate.ensure().state == "done"
    config.write_config(role_primary_provider="mara-local", role_primary_model="big",
                        role_fast_provider="winifred-local", role_fast_model="small")

    conns = runner.resolve_connections(case_mod.CASES)

    assert (conns["chat"]["id"], conns["chat"]["model"]) == ("mara-local", "big")
    # Absorb's route defaults to the Fast role.
    assert (conns["absorb"]["id"], conns["absorb"]["model"]) == ("winifred-local", "small")

    # A refusal is the seam's own reason.
    config.write_config(role_primary_provider="openrouter", role_primary_model="vendor/m")
    with pytest.raises(RuntimeError, match="OpenRouter key not set"):
        runner.resolve_connections((case_mod.BY_ID["scene-length"],))
