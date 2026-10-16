"""The template gate's own tests.

`make check-templates` runs `scripts/verify_templates.py` on every push, and
nothing tested the harness itself: a change that made it compare less -- or
compare nothing -- would have kept CI green. These run it the way the Makefile
does, in a fresh interpreter (it points GRIMOIRE_HOME at a throwaway store of
its own at import, so it never reads this suite's), and hold it to three
things: it passes and has not quietly started checking less; a builder that
drifts from its template fails it, naming that builder; and a template no check
renders fails it unless the harness says why.

The breaks are planted in the child process before the harness runs, never in
the tree: the suite runs in parallel, and a file planted under `templates/`
would fail every other worker's run of the same gate.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_templates.py"

#: A floor, not a target, the way the Makefile's `COV_FLOOR` is: the number of
#: comparisons a passing run made when this was written, so a harness that
#: stops making some fails here rather than passing on fewer. Raise it when
#: checks are added; lowering it needs the same justification as any other
#: gate being weakened.
CHECKS_FLOOR = 553

#: The harness, run the way `make check-templates` runs it, after `plant` -- a
#: few lines that break one thing in the child process -- has run first.
_PLANTED = "{plant}\nimport runpy, sys\nrunpy.run_path(sys.argv[1], run_name='__main__')\n"

#: A builder that drifts from its template: the rolling-summary builder stops
#: passing the summary it folds onto, so its template renders the from-scratch
#: branch. StrictUndefined already fails a variable that is missing outright;
#: the drift the byte-for-byte comparison exists for is one that still renders.
_DROPS_PRIOR = """
from grimoire.store import rolling_summary
built = rolling_summary.build_prompt
rolling_summary.build_prompt = lambda prior, transcript, facts=None: built("", transcript, facts)
"""

#: A template nothing renders: the loader lists one more than the directory
#: holds, which is what a new template with no check looks like to the gate.
_ADDS_A_TEMPLATE = """
import jinja2
listed = jinja2.FileSystemLoader.list_templates
jinja2.FileSystemLoader.list_templates = lambda self: [*listed(self), "planted/unrendered.j2"]
"""


def _run(tmp_path: Path, plant: str | None = None) -> subprocess.CompletedProcess:
    # None of this suite's own settings: `conftest.py` sets GRIMOIRE_* switches
    # at import that `make check-templates` never sees, and the gate should be
    # tested as CI runs it. TMPDIR is where the harness's throwaway store lands,
    # so it is cleaned up with the test's own directory.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRIMOIRE_")}
    env.update(PYTHONPATH=str(REPO / "backend" / "src"), PYTHONIOENCODING="utf-8",
               TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    argv = ([sys.executable, str(SCRIPT)] if plant is None else
            [sys.executable, "-c", _PLANTED.format(plant=plant), str(SCRIPT)])
    return subprocess.run(argv, env=env, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)


def test_the_gate_passes_and_has_not_started_checking_less(tmp_path):
    run = _run(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    verdict = run.stdout.strip().splitlines()[-1]
    passed = re.match(r"all (\d+) checks passed", verdict)
    assert passed, verdict
    assert int(passed[1]) >= CHECKS_FLOOR, (
        f"the harness made {passed[1]} comparisons, under its floor of {CHECKS_FLOOR}")


def test_a_builder_that_drifts_from_its_template_fails_the_gate(tmp_path):
    run = _run(tmp_path, _DROPS_PRIOR)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "checks FAILED" in run.stdout
    failed = re.findall(r"^(.+): mismatch at char \d+$", run.stdout, re.MULTILINE)
    # Only the folds that had a summary to drop, and nothing else: a gate that
    # failed everywhere would prove it can fail, not that it can say where.
    assert failed, run.stdout
    assert all(label.startswith("rolling summary user (prior=True") for label in failed), failed


def test_a_template_no_check_renders_fails_the_gate(tmp_path):
    run = _run(tmp_path, _ADDS_A_TEMPLATE)
    assert run.returncode == 1, run.stdout + run.stderr
    assert ("reach (planted/unrendered.j2): no check renders it, and UNRENDERED does not "
            "say why") in run.stdout
    assert re.search(r"^1/\d+ checks FAILED$", run.stdout, re.MULTILINE), run.stdout
