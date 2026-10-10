"""CLI for the eval suite. See evals/README.md.

    backend/.venv/Scripts/python.exe evals/run.py                 # replay
    backend/.venv/Scripts/python.exe evals/run.py --live          # one real call per case
    backend/.venv/Scripts/python.exe evals/run.py --live --record # ...and save as baseline
    backend/.venv/Scripts/python.exe evals/run.py --live --provider ID --model NAME \
        --decide-backend native                                   # force one decide backend
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
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))                      # for `evals`
sys.path.insert(0, str(REPO / "backend" / "src"))  # for `grimoire`

from evals import cases as case_mod  # noqa: E402
from evals import gate, runner  # noqa: E402
from grimoire.store import paths, usage  # noqa: E402


@contextlib.contextmanager
def temp_home():
    """A throwaway GRIMOIRE_HOME for one case, restored afterwards -- except
    when the case's follow-ups are still running (`runner.FollowUpsRunningError`):
    then the home is kept and the environment is NOT restored, because a
    straggler that resolved `paths.home()` after the restore would file its
    row in the real library."""
    previous = os.environ.get("GRIMOIRE_HOME")
    path = tempfile.mkdtemp(prefix="grimoire-eval-")
    os.environ["GRIMOIRE_HOME"] = path
    kept = False
    try:
        yield Path(path)
    except runner.FollowUpsRunningError:
        kept = True
        raise
    finally:
        if not kept:
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
                                   ("--case", bool(args.case)),
                                   ("--provider", bool(args.provider)),
                                   ("--model", bool(args.model)),
                                   ("--decide-backend", args.decide_backend is not None))
             if on]
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


def run_live(args: argparse.Namespace, selected: tuple) -> list[runner.Result] | int:
    """`--live`: resolve every case in the REAL store, build every decide
    case's chain, then run each case in a throwaway one. An exit code instead
    of results when the run is refused before anything is sent."""
    backend = args.decide_backend or runner.CHAIN
    # Before any temp_home(): the settings live in the REAL store.
    try:
        conns = runner.resolve_connections(selected, provider=args.provider,
                                           model=args.model)
    except RuntimeError as exc:
        print(runner.ascii_safe(f"live: {exc}"), file=sys.stderr)
        return 1
    # Every decide case's chain is built before anything is sent, so a forced
    # backend the model cannot take refuses the whole run unsent.
    modes: dict[str, str] = {}
    for case in selected:
        key = runner.conn_key(case)
        target = conns[key]
        if key in modes or runner.operation(case) != "decide":
            continue
        try:
            stages = runner.chain(target, backend)
        except runner.BackendRefusedError as exc:
            print(runner.ascii_safe(f"live: {key}: {exc}"), file=sys.stderr)
            return 2
        modes[key] = f" [{' then '.join(s.mode for s in stages)}]"
    # ascii_safe: the model id is user-configured free text and may not
    # encode in the console's code page (see runner.report).
    for key, target in conns.items():
        chain = target.chain
        if chain is None:   # the seam refuses a resolution of nothing; never sent
            print(runner.ascii_safe(f"live: {key} resolved to no connection"),
                  file=sys.stderr)
            return 1
        print(runner.ascii_safe(
            f"live: {key} -> {chain.primary.kind} / {chain.primary.model or '(default)'}"
            f"{modes.get(key, '')}"))
    if args.record:
        print("  [recording baselines]")
    # Still in the real store: the tripwire's reference, and the one set of
    # rates every eval row is priced against (as a rollup has one).
    real_home = paths.home()
    rates = usage.Rates.current()
    return runner.live_all(selected, conns, temp_home, record=args.record, backend=backend,
                           real_home=real_home, run_id=str(uuid.uuid4()),
                           run_day=usage._today(), rates=rates)


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
    ap.add_argument("--provider", metavar="ID", default="",
                    help="with --live, run on this provider instead of the routed one "
                         "(a per-run override, as a reroll's; nothing is saved)")
    ap.add_argument("--model", metavar="NAME", default="",
                    help="with --live, run on this model instead of the routed one")
    ap.add_argument("--decide-backend", choices=runner.DECIDE_BACKENDS, default=None,
                    help="with --live, how a decide case is answered: 'chain' (the "
                         "default) is what the app sends; 'native' or 'structured' "
                         "forces that backend on the resolved model alone")
    args = ap.parse_args(argv)

    if args.gate:
        return run_gate(ap, args)

    if args.record and not args.live:
        ap.error("--record only means anything with --live")
    live_only = [flag for flag, on in (("--provider", bool(args.provider)),
                                       ("--model", bool(args.model)),
                                       ("--decide-backend", args.decide_backend is not None))
                 if on]
    if live_only and not args.live:
        ap.error(f"{', '.join(live_only)} only means anything with --live")

    selected = case_mod.CASES
    if args.case:
        unknown = [c for c in args.case if c not in case_mod.BY_ID]
        if unknown:
            ap.error(f"unknown case(s): {', '.join(unknown)}; "
                     f"known: {', '.join(case_mod.BY_ID)}")
        selected = tuple(case_mod.BY_ID[c] for c in args.case)

    if args.live:
        results = run_live(args, selected)
        if isinstance(results, int):
            return results
    else:
        print(f"replay: {sum(len(c.recordings) for c in selected)} recordings")
        results = runner.replay_all(selected, temp_home)

    print(runner.report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
