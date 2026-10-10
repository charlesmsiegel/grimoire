"""CLI for the eval suite. See evals/README.md.

    backend/.venv/Scripts/python.exe evals/run.py                 # replay
    backend/.venv/Scripts/python.exe evals/run.py --live          # one real call per case
    backend/.venv/Scripts/python.exe evals/run.py --live --record # ...and save as baseline
    backend/.venv/Scripts/python.exe evals/run.py --live --provider ID --model NAME \
        --decide-backend native                                   # force one decide backend
    backend/.venv/Scripts/python.exe evals/run.py --live --decide-backend native \
        --decide-backend structured --repeat 3 --out evals/out/run.json  # several configs
    backend/.venv/Scripts/python.exe evals/run.py --compare evals/out/a.json evals/out/b.json
    backend/.venv/Scripts/python.exe evals/run.py --case roll-fence
    backend/.venv/Scripts/python.exe evals/run.py --gate          # the decide gate, offline

Bootstraps sys.path the same way scripts/verify_templates.py does, so it runs
from a checkout without the package being installed.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))                      # for `evals`
sys.path.insert(0, str(REPO / "backend" / "src"))  # for `grimoire`

from evals import cases as case_mod  # noqa: E402
from evals import gate, runfile, runner  # noqa: E402
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
                                   ("--decide-backend", args.decide_backend is not None),
                                   ("--repeat", args.repeat is not None),
                                   ("--out", bool(args.out)),
                                   ("--compare", bool(args.compare)))
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


def run_compare(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """`--compare FILE...`: one table from saved run files, offline -- no
    store, no settings, no rates are read. A file this reader does not know
    is one sentence and exit 2."""
    clash = [flag for flag, on in (("--live", args.live), ("--record", args.record),
                                   ("--case", bool(args.case)),
                                   ("--provider", bool(args.provider)),
                                   ("--model", bool(args.model)),
                                   ("--decide-backend", args.decide_backend is not None),
                                   ("--repeat", args.repeat is not None),
                                   ("--out", bool(args.out)))
             if on]
    if clash:
        ap.error(f"--compare is offline and reads only its files; "
                 f"it takes no {', '.join(clash)}")
    try:
        docs = [(Path(name).stem, runfile.read(Path(name))) for name in args.compare]
    except runfile.RunFileError as exc:
        print(runner.ascii_safe(f"compare: {exc}"), file=sys.stderr)
        return 2
    print(runner.ascii_safe(runfile.compare(docs)))
    return 0


def _label(conns: dict, backend: str) -> str:
    """A config's label: `<connection kind> / <model> [<backend>]` when every
    case resolved to one model, else `routed [<backend>]`."""
    primaries = {(t.chain.primary.kind, t.chain.primary.model or "(default)")
                 for t in conns.values() if t.chain is not None}
    if len(primaries) == 1:
        kind, model = next(iter(primaries))
        return f"{kind} / {model} [{backend}]"
    return f"routed [{backend}]"


def _axes(args: argparse.Namespace, backend: str) -> dict:
    axes = {"backend": backend}
    override = "+".join(name for name, on in (("provider", bool(args.provider)),
                                              ("model", bool(args.model))) if on)
    if override:
        axes["override"] = override
    return axes


def run_live(args: argparse.Namespace,
             selected: tuple) -> tuple[list[runner.Result], list[dict]] | int:
    """`--live`: resolve every case in the REAL store, build every decide
    case's chain for every `--decide-backend`, then run each case in a
    throwaway one. The results and the run's configs, or an exit code when
    the run is refused before anything is sent."""
    backends = tuple(dict.fromkeys(args.decide_backend or [runner.CHAIN]))
    # Before any temp_home(): the settings live in the REAL store.
    try:
        conns = runner.resolve_connections(selected, provider=args.provider,
                                           model=args.model)
    except RuntimeError as exc:
        print(runner.ascii_safe(f"live: {exc}"), file=sys.stderr)
        return 1
    # Every decide case's chain is built, for every backend, before anything
    # is sent, so one backend the model cannot take refuses the whole run.
    modes: dict[str, str] = {}
    for case in selected:
        key = runner.conn_key(case)
        target = conns[key]
        if key in modes or runner.operation(case) != "decide":
            continue
        shown = []
        for backend in backends:
            try:
                stages = runner.chain(target, backend)
            except runner.BackendRefusedError as exc:
                print(runner.ascii_safe(f"live: {key}: {exc}"), file=sys.stderr)
                return 2
            shown.append(f"[{' then '.join(s.mode for s in stages)}]")
        modes[key] = " " + " | ".join(shown)
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
    configs = [runfile.config(cid, _label(conns, backend), _axes(args, backend))
               for cid, backend in zip(runner.config_ids(backends), backends, strict=True)]
    results = runner.live_all(selected, conns, temp_home, record=args.record,
                              backends=backends, repeat=args.repeat or 1,
                              real_home=real_home, run_id=args.run_id,
                              run_day=usage._today(), rates=rates)
    return results, configs


def _check_live_flags(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse (argparse's exit 2) a live-only flag without `--live`, a
    `--repeat` out of range, and a `--record` that could not say which reply
    becomes the baseline."""
    if args.record and not args.live:
        ap.error("--record only means anything with --live")
    live_only = [flag for flag, on in (("--provider", bool(args.provider)),
                                       ("--model", bool(args.model)),
                                       ("--decide-backend", args.decide_backend is not None),
                                       ("--repeat", args.repeat is not None))
                 if on]
    if live_only and not args.live:
        ap.error(f"{', '.join(live_only)} only means anything with --live")
    if args.repeat is not None and not 1 <= args.repeat <= runner.MAX_REPEAT:
        ap.error(f"--repeat takes 1 to {runner.MAX_REPEAT}")
    if args.record and ((args.repeat or 1) > 1 or len(set(args.decide_backend or ())) > 1):
        ap.error("--record keeps one reply as the baseline; with --repeat over 1 or "
                 "several --decide-backend it is ambiguous which")


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
                    action="append",
                    help="with --live, how a decide case is answered: 'chain' (the "
                         "default) is what the app sends; 'native' or 'structured' "
                         "forces that backend on the resolved model alone. Repeat "
                         "it to run each decide case once per backend (a config "
                         "each) and print a comparison table")
    ap.add_argument("--repeat", type=int, default=None, metavar="N",
                    help=f"with --live, run each (config, case) N times "
                         f"(1-{runner.MAX_REPEAT}); multiplies what the run spends")
    ap.add_argument("--out", metavar="PATH", default="",
                    help="write the run's eval-run v1 file here (suggested: "
                         "evals/out/, which git ignores); nothing is written without it")
    ap.add_argument("--compare", nargs="+", metavar="FILE",
                    help="print one table from saved run files; offline, never a call")
    args = ap.parse_args(argv)
    args.run_id = str(uuid.uuid4())

    if args.gate:
        return run_gate(ap, args)
    if args.compare:
        return run_compare(ap, args)

    _check_live_flags(ap, args)

    selected = case_mod.CASES
    if args.case:
        unknown = [c for c in args.case if c not in case_mod.BY_ID]
        if unknown:
            ap.error(f"unknown case(s): {', '.join(unknown)}; "
                     f"known: {', '.join(case_mod.BY_ID)}")
        selected = tuple(case_mod.BY_ID[c] for c in args.case)

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.live:
        ran = run_live(args, selected)
        if isinstance(ran, int):
            return ran
        results, configs = ran
    else:
        print(f"replay: {sum(len(c.recordings) for c in selected)} recordings")
        results = [dataclasses.replace(r, configs=("c1",))
                   for r in runner.replay_all(selected, temp_home)]
        configs = [runfile.config("c1", "replay", {"mode": "replay"})]

    print(runner.report(results))
    doc = runfile.build(results, configs, run_id=args.run_id, started=started)
    if len(configs) > 1:
        print()
        print(runner.ascii_safe(runfile.compare([("run", doc)])))
    if args.out:
        runfile.write(Path(args.out), doc)
        print(runner.ascii_safe(f"wrote {args.out}"))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
