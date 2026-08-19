"""CLI for the eval suite. See evals/README.md.

    backend/.venv/Scripts/python.exe evals/run.py                 # replay
    backend/.venv/Scripts/python.exe evals/run.py --live          # one real call per case
    backend/.venv/Scripts/python.exe evals/run.py --live --record # ...and save as baseline
    backend/.venv/Scripts/python.exe evals/run.py --case roll-fence

Bootstraps sys.path the same way scripts/verify_templates.py does, so it runs
from a checkout without the package being installed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))                      # for `evals`
sys.path.insert(0, str(REPO / "backend" / "src"))  # for `grimoire`

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402


@contextlib.contextmanager
def temp_home():
    """A throwaway GRIMOIRE_HOME for one case, restored afterwards."""
    previous = os.environ.get("GRIMOIRE_HOME")
    path = tempfile.mkdtemp(prefix="grimoire-eval-")
    os.environ["GRIMOIRE_HOME"] = path
    try:
        yield Path(path)
    finally:
        if previous is None:
            os.environ.pop("GRIMOIRE_HOME", None)
        else:
            os.environ["GRIMOIRE_HOME"] = previous
        shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score grimoire's LLM output against the eval suite.")
    ap.add_argument("--live", action="store_true",
                    help="make one real LLM call per case using the active connection")
    ap.add_argument("--record", action="store_true",
                    help="with --live, overwrite each case's baseline recording")
    ap.add_argument("--case", action="append", metavar="ID",
                    help="run only this case (repeatable); default is all")
    args = ap.parse_args(argv)

    if args.record and not args.live:
        ap.error("--record only means anything with --live")

    selected = case_mod.CASES
    if args.case:
        unknown = [c for c in args.case if c not in case_mod.BY_ID]
        if unknown:
            ap.error(f"unknown case(s): {', '.join(unknown)}; "
                     f"known: {', '.join(case_mod.BY_ID)}")
        selected = tuple(case_mod.BY_ID[c] for c in args.case)

    if args.live:
        # Before any temp_home(): the connection lives in the REAL store.
        conn = runner.resolve_connection()
        # ascii_safe: the model id is user-configured free text and may not
        # encode in the console's code page (see runner.report).
        print(runner.ascii_safe(
            f"live: {conn['kind']} / {conn.get('model') or '(default)'}"
            + ("  [recording baselines]" if args.record else "")))
        results = runner.live_all(selected, conn, temp_home, record=args.record)
    else:
        print(f"replay: {sum(len(c.recordings) for c in selected)} recordings")
        results = runner.replay_all(selected, temp_home)

    print(runner.report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
