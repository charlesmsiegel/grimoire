"""Prove the prompt builders and templates/ agree byte-for-byte.

The store modules render prompts from templates/ — this harness verifies the
WIRING (each builder passes the documented variables to the right template)
and the DATA CONTRACT (the gather() mirror in `template_checks/mirror.py`
assembles the same data from public store reads that context._assemble
gathers; templates/README.md documents that contract). It builds a throwaway
store (GRIMOIRE_HOME -> temp dir) exercising every context section, then
compares direct template renders against the live builders. It never pins
template text to literals, so editing a prompt in templates/ cannot fail it.
Run after touching either side:

    backend/.venv/Scripts/python.exe scripts/verify_templates.py

The checks live in the `template_checks` package beside this script, one
module per family; this entry runs them in order: the storeless families,
then the fixture store and the checks over it, then the reach check.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The package sits beside this script. Run as a script, Python already puts
# this directory first on the path; run through `runpy` (as the gate's own test
# runs it), nothing does, so it is put there either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from template_checks import (
    continuity,
    decisions,
    fixture,
    items,
    prompts,
    reach,
    scene_checks,
    suggest_absorb,
)
from template_checks.harness import REPORT

#: The checks that need no store, in their running order.
STORELESS = (*prompts.CHECKS, *items.CHECKS, *suggest_absorb.CHECKS, *continuity.CHECKS,
             *decisions.CHECKS)


def main() -> int:
    for run in STORELESS:
        run()
    fx = fixture.build()
    for run_over in scene_checks.CHECKS:
        run_over(fx)
    for run in reach.CHECKS:
        run()
    print(REPORT.verdict())
    return 1 if REPORT.failures else 0


if __name__ == "__main__":
    sys.exit(main())
