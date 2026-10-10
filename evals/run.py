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
    backend/.venv/Scripts/python.exe evals/run.py --escalation-sweep --case decide-continuity-identity
    backend/.venv/Scripts/python.exe evals/run.py --live --case decide-continuity-identity \
        --escalation '{"escalate_to": "primary", ...}' --out evals/out/esc.json
    backend/.venv/Scripts/python.exe evals/run.py --embed         # embedding rankings, replay
    backend/.venv/Scripts/python.exe evals/run.py --live --embed \
        --embed-options '{"input": "prefix", "document_prefix": "passage: "}'  # an A/B

Bootstraps sys.path the same way scripts/verify_templates.py does, so it runs
from a checkout without the package being installed.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import json
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
from evals import costs, escalation, gate, runfile, runner  # noqa: E402
from evals.embed import corpus as embed_corpus  # noqa: E402
from evals.embed import harness as embed_harness  # noqa: E402
from evals.embed import metrics as embed_metrics  # noqa: E402
from grimoire import wire  # noqa: E402
from grimoire.llm_errors import LLMError  # noqa: E402
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
    except (runner.FollowUpsRunningError, KeyboardInterrupt):
        # An interrupt is never drained (the person asked to stop now), so
        # the home is kept and the environment left as it is: a follow-up a
        # play case left running cannot reach the real library either way.
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
                                   ("--compare", bool(args.compare)),
                                   ("--escalation", bool(args.escalation)),
                                   ("--escalation-sweep", args.escalation_sweep))
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
                                   ("--out", bool(args.out)),
                                   ("--escalation", bool(args.escalation)),
                                   ("--escalation-sweep", args.escalation_sweep))
             if on]
    if clash:
        ap.error(f"--compare is offline and reads only its files; "
                 f"it takes no {', '.join(clash)}")
    try:
        docs = [(Path(name).stem, runfile.read(Path(name))) for name in args.compare]
        table = runfile.compare(docs)
    except runfile.RunFileError as exc:
        print(runner.ascii_safe(f"compare: {exc}"), file=sys.stderr)
        return 2
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        # A v1 file whose inner shape a hand edit broke: one sentence, exit 2.
        print(runner.ascii_safe(f"compare: a run file is malformed "
                                f"({type(exc).__name__}: {exc})"), file=sys.stderr)
        return 2
    print(runner.ascii_safe(table))
    return 0


def _decide_cases(ap: argparse.ArgumentParser, args: argparse.Namespace,
                  flag: str) -> tuple:
    """The `--case`s `flag` runs over, each a decide case on a decide route
    (`escalation.decide_task`), or argparse's exit 2."""
    if not args.case:
        ap.error(f"{flag} names its decide cases with --case")
    selected = tuple(case_mod.BY_ID[c] for c in args.case if c in case_mod.BY_ID)
    refused = [why for case in selected if (why := escalation.decide_task(case))]
    if refused:
        ap.error(f"{flag}: " + "; ".join(refused))
    return selected


def _policy(ap: argparse.ArgumentParser, args: argparse.Namespace, selected: tuple, *,
            sweep: bool):
    """`--escalation` parsed against every selected case's task (the same
    policy for each; argparse's exit 2 on a refusal), or None without it."""
    if not args.escalation:
        return None
    policy = None
    for case in selected:
        try:
            policy = escalation.parse(args.escalation, case.task, sweep=sweep)
        except escalation.PolicyError as exc:
            ap.error(f"{case.id}: {exc}")
    return policy


def run_sweep(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """`--escalation-sweep`: each named decide case's native recordings, and
    which items each threshold of the grid escalates (`escalation.sweep`).
    Offline: no call, and each case builds its fixture in a throwaway home."""
    clash = [flag for flag, on in (("--live", args.live), ("--record", args.record),
                                   ("--provider", bool(args.provider)),
                                   ("--model", bool(args.model)),
                                   ("--decide-backend", args.decide_backend is not None),
                                   ("--repeat", args.repeat is not None),
                                   ("--out", bool(args.out)))
             if on]
    if clash:
        ap.error(f"--escalation-sweep is offline and prints its table; "
                 f"it takes no {', '.join(clash)}")
    _unknown_cases(ap, args)
    selected = _decide_cases(ap, args, "--escalation-sweep")
    policy = _policy(ap, args, selected, sweep=True)
    for case in selected:
        with temp_home():
            print(runner.ascii_safe(escalation.sweep(case, policy)))
    return 0


def _unknown_cases(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    unknown = [c for c in args.case or () if c not in case_mod.BY_ID]
    if unknown:
        ap.error(f"unknown case(s): {', '.join(unknown)}; "
                 f"known: {', '.join(case_mod.BY_ID)}")


def run_embed(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """`--embed`: rank the synthetic corpus (evals/embed/). Offline, the
    recorded rankings are graded beside the lexical baseline; with `--live`,
    the Embedding role's model ranks it once per option set, each in a
    throwaway home, and the report carries what each set cost."""
    clash = [flag for flag, on in (("--gate", args.gate), ("--compare", bool(args.compare)),
                                   ("--case", bool(args.case)),
                                   ("--escalation", bool(args.escalation)),
                                   ("--escalation-sweep", args.escalation_sweep),
                                   ("--provider", bool(args.provider)),
                                   ("--model", bool(args.model)),
                                   ("--decide-backend", args.decide_backend is not None),
                                   ("--repeat", args.repeat is not None),
                                   ("--out", bool(args.out)))
             if on]
    if clash:
        ap.error(f"--embed ranks its own corpus; it takes no {', '.join(clash)} "
                 f"(each option set prints its own rows instead of a run file)")
    if args.embed_options and not args.embed:
        ap.error("--embed-options only means anything with --embed")
    if args.embed_options and not args.live:
        ap.error("--embed-options only means anything with --live")
    if args.record and not args.live:
        ap.error("--record only means anything with --live")
    corpus = embed_corpus.load()
    broken = embed_corpus.problems(corpus)
    if broken:
        print(runner.ascii_safe("embed: the corpus breaks its rules:\n  " + "\n  ".join(broken)),
              file=sys.stderr)
        return 2
    graded = [("lexical baseline",
               embed_metrics.grade(corpus, embed_harness.lexical_rankings(corpus)))]
    if not args.live:
        recorded = embed_harness.read_recordings()
        print(f"replay: {len(recorded)} recorded ranking(s), {len(corpus.queries)} queries, "
              f"{len(corpus.documents)} documents")
        graded += [(name, embed_metrics.grade(corpus, r)) for name, r in recorded]
        print(runner.ascii_safe(embed_metrics.table(graded)))
        return 0 if all(g.whole for _n, g in graded) else 1
    return _embed_live(ap, args, corpus, graded)


def _option_spaces(base: dict, sets: list) -> list[dict]:
    """The spaces `--live --embed` ranks: the configured one first, the
    control every option set is ranked beside, then each set's own -- a set
    that names a space already listed (the control's own options) once."""
    spaces = [base]
    for options in sets:
        space = embed_harness.with_options(base, options)
        if all(space["space"] != s["space"] for s in spaces):
            spaces.append(space)
    return spaces


def _embed_live(ap: argparse.ArgumentParser, args: argparse.Namespace,
                corpus: embed_corpus.Corpus, graded: list) -> int:
    """`--live --embed`: the space resolved in the REAL store before any
    isolate, each option set in its own throwaway home, its rows harvested
    into the report and never left in the real ledger."""
    try:
        sets = [embed_harness.options_of(json.loads(raw)) for raw in args.embed_options or ()]
    except (ValueError, TypeError) as exc:
        ap.error(f"--embed-options: {exc}")
    try:
        base = embed_harness.resolve_live()
    except RuntimeError as exc:
        print(runner.ascii_safe(f"live: {exc}"), file=sys.stderr)
        return 1
    spaces = _option_spaces(base, sets)
    size, tokens = embed_harness.estimate(corpus)
    print(runner.ascii_safe(
        f"live: embedding through {base['provider_name']} / {base['model']}: "
        f"{size} bytes, about {tokens} tokens before prefixes, per option set "
        f"(x{len(spaces)})"))
    real_home = paths.home()
    rates = usage.Rates.current()
    run_day = usage._today()
    failed = False
    for n, space in enumerate(spaces, 1):
        options = space.get("options") or wire.EmbedOptions()
        label = embed_harness.recording_name(space["model"], options)
        rankings, rows, ledger_error, wall = None, (), "", 0
        with temp_home():
            if runner._is_real_home(real_home):
                print(runner.ascii_safe(f"live: {runner.ISOLATE_ERROR}"), file=sys.stderr)
                return 1
            client = embed_harness.live_client()
            try:
                rankings, wall = embed_harness.timed(functools.partial(
                    embed_harness.embedded_rankings, corpus, space=space, client=client,
                    run_id=args.run_id))
            except LLMError as exc:
                failed = True
                code = f"/{exc.code}" if getattr(exc, "code", None) else ""
                print(runner.ascii_safe(f"  {label}: failed ({exc.kind}{code})"))
            finally:
                client.close()
            try:
                rows = runner.harvest(run_day, args.run_id, f"embed-{n}")
            except OSError as exc:
                ledger_error = str(exc)
        spent = (f"cost not reported ({ledger_error})" if ledger_error
                 else costs.bucket_line(costs.fold(rows, rates)))
        print(runner.ascii_safe(f"  {label}: {costs.seconds(wall)}  {spent}"))
        if rankings is not None:
            graded.append((label, embed_metrics.grade(corpus, rankings)))
            if args.record:
                path = embed_harness.write_recording(label, space["model"], options, rankings)
                print(runner.ascii_safe(f"  recorded {path.name}"))
    print(runner.ascii_safe(embed_metrics.table(graded)))
    return 1 if failed else 0


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
    if args.escalation:
        # The policy as given, compacted: what tells this run's config from
        # the escalation-off run it is compared with.
        axes["escalation"] = json.dumps(json.loads(args.escalation), sort_keys=True,
                                        separators=(",", ":"))
    override = "+".join(name for name, on in (("provider", bool(args.provider)),
                                              ("model", bool(args.model))) if on)
    if override:
        axes["override"] = override
    return axes


def _escalating(selected: tuple, policy) -> runner.Escalating | int | None:
    """`--escalation`'s override for the run, its escalation role resolved in
    the real store before any isolate (`runner.escalation_roles`); None
    without one, and exit 1 when the role cannot serve the hop."""
    if policy is None:
        return None
    try:
        return runner.Escalating(policy, runner.escalation_roles(selected, policy))
    except RuntimeError as exc:
        print(runner.ascii_safe(f"live: {exc}"), file=sys.stderr)
        return 1


def run_live(args: argparse.Namespace, selected: tuple,
             policy=None) -> tuple[list[runner.Result], list[dict]] | int:
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
    escalating = _escalating(selected, policy)
    if isinstance(escalating, int):
        return escalating
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
                              run_day=usage._today(), rates=rates, escalating=escalating)
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
    if args.escalation and not args.live:
        ap.error("--escalation only means anything with --live or --escalation-sweep")
    if args.escalation and set(args.decide_backend or (runner.CHAIN,)) != {runner.CHAIN}:
        ap.error("--escalation runs the chain decide() sends; it takes no forced "
                 "--decide-backend")
    if args.escalation and args.record:
        ap.error("--escalation is a comparison run; it records no baseline")
    if args.record and ((args.repeat or 1) > 1 or len(set(args.decide_backend or ())) > 1):
        ap.error("--record keeps one reply as the baseline; with --repeat over 1 or "
                 "several --decide-backend it is ambiguous which")


def _other_mode(args: argparse.Namespace):
    """The mode that is not a case run, when one is asked for: the embedding
    evals (`--embed`, or `--embed-options`, which it refuses alone), the
    decide gate, a comparison, or the escalation sweep. None for a case run."""
    for asked, mode in ((args.embed or bool(args.embed_options), run_embed),
                        (args.gate, run_gate), (bool(args.compare), run_compare),
                        (args.escalation_sweep, run_sweep)):
        if asked:
            return mode
    return None


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
    ap.add_argument("--escalation", metavar="POLICY_JSON", default="",
                    help="with --live, answer each --case's decide items under this "
                         "escalation policy (a routing.TaskPolicy's escalation fields, "
                         "as JSON) for this run only; with --escalation-sweep, the "
                         "question, triggers and answer filter the sweep reads")
    ap.add_argument("--escalation-sweep", action="store_true",
                    help="for each --case, which items of its native recordings each "
                         "margin threshold would escalate; offline, never a call")
    ap.add_argument("--embed", action="store_true",
                    help="rank the embedding corpus (evals/embed/): recall@k and MRR "
                         "against a lexical baseline; replay offline, or --live")
    ap.add_argument("--embed-options", action="append", metavar="JSON",
                    help="with --live --embed, also rank under this embedding options "
                         "block (repeatable), in memory: nothing is written")
    args = ap.parse_args(argv)
    args.run_id = str(uuid.uuid4())

    mode = _other_mode(args)
    if mode is not None:
        return mode(ap, args)

    _check_live_flags(ap, args)
    _unknown_cases(ap, args)

    selected = case_mod.CASES
    if args.case:
        selected = tuple(case_mod.BY_ID[c] for c in args.case)
    policy = None
    if args.escalation:
        selected = _decide_cases(ap, args, "--escalation")
        policy = _policy(ap, args, selected, sweep=False)

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.live:
        ran = run_live(args, selected, policy)
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
