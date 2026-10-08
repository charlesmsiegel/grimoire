"""CLI for the eval suite. See evals/README.md.

    backend/.venv/Scripts/python.exe evals/run.py                 # replay
    backend/.venv/Scripts/python.exe evals/run.py --live          # one real call per case
    backend/.venv/Scripts/python.exe evals/run.py --live --record # ...and save as baseline
    backend/.venv/Scripts/python.exe evals/run.py --case roll-fence
    backend/.venv/Scripts/python.exe evals/run.py --gate          # the decide gate, offline

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
from evals import gate, runner  # noqa: E402


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


def run_gate(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """`--gate`: every decide conversion judged offline (evals/gate.py)."""
    # Offline and whole: anything that would spend money or narrow the gate to
    # some of its conversions is refused before anything runs.
    clash = [flag for flag, on in (("--live", args.live), ("--record", args.record),
                                   ("--case", bool(args.case))) if on]
    if clash:
        ap.error(f"--gate is offline and judges every conversion; "
                 f"it takes no {', '.join(clash)}")
    # A throwaway store, as replay has: no conversion's builder or mapping can
    # read the user's real one.
    results = []
    for conv in gate.GATES:
        with temp_home():
            results.append(gate.judge(conv))
    print(runner.ascii_safe(gate.report(results)))
    return 0 if all(r.passed for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score grimoire's LLM output against the eval suite.")
    ap.add_argument("--live", action="store_true",
                    help="make one real LLM call per case, on the model the app routes it to")
    ap.add_argument("--record", action="store_true",
                    help="with --live, overwrite each case's baseline recording")
    ap.add_argument("--case", action="append", metavar="ID",
                    help="run only this case (repeatable); default is all")
    ap.add_argument("--gate", action="store_true",
                    help="compare each decide conversion's structured parse with "
                         "today's on its recorded corpus; offline, never a call")
    args = ap.parse_args(argv)

    if args.gate:
        return run_gate(ap, args)

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
        # Before any temp_home(): the settings live in the REAL store.
        try:
            conns = runner.resolve_connections(selected)
        except RuntimeError as exc:
            print(runner.ascii_safe(f"live: {exc}"), file=sys.stderr)
            return 1
        # ascii_safe: the model id is user-configured free text and may not
        # encode in the console's code page (see runner.report).
        for key, conn in conns.items():
            print(runner.ascii_safe(
                f"live: {key} -> {conn['kind']} / {conn.get('model') or '(default)'}"))
        if args.record:
            print("  [recording baselines]")
        results = runner.live_all(selected, conns, temp_home, record=args.record)
    else:
        print(f"replay: {sum(len(c.recordings) for c in selected)} recordings")
        results = runner.replay_all(selected, temp_home)

    print(runner.report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
