"""Command line: ``python -m grimoire.evals.story_quality SCENARIO BASELINE CANDIDATE``.

Both runs must have the same format: ``grimoire.story-quality/1``, ``/2`` or ``/3``. For ``/2``
capture runs, ``--evidence-baseline``/``--evidence-candidate`` name the capture's
``evidence.json``; only a verified one makes a run "measured". Exit status 0 = pass, 1 = gate
failed, 2 = invalid input or usage. Writes only to stdout/stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .format import FORMAT, BenchmarkFormatError, Run
from .format2 import FORMAT_V2, RunV2
from .gate import compare
from .gate2 import compare_v2
from .loader import load_any_run, load_evidence, load_scenario
from .policy import GatePolicy
from .report import render, render_v2
from .v021 import verify_evidence


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors exit 2, like format errors
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m grimoire.evals.story_quality",
                     description="Score a recorded roleplay run against a baseline.")
    parser.add_argument("scenario")
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    defaults = GatePolicy()
    parser.add_argument("--max-cost-increase", type=float, default=defaults.max_cost_increase)
    parser.add_argument("--max-voice-drop", type=float, default=defaults.max_voice_drop)
    parser.add_argument("--cached-input-weight", type=float, default=defaults.cached_input_weight)
    parser.add_argument("--max-latency-increase", type=float, default=None)
    parser.add_argument("--allow-flag", action="append", default=[], metavar="FLAG")
    parser.add_argument("--evidence-baseline", metavar="EVIDENCE",
                        help="capture evidence for a /2 baseline")
    parser.add_argument("--evidence-candidate", metavar="EVIDENCE",
                        help="capture evidence for a /2 candidate")
    return parser


def _format(run: Run | RunV2) -> str:
    return run.format if isinstance(run, RunV2) else FORMAT


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = GatePolicy(
            max_voice_drop=args.max_voice_drop, cached_input_weight=args.cached_input_weight,
            max_cost_increase=args.max_cost_increase, max_latency_increase=args.max_latency_increase,
            allowed_flags=frozenset(args.allow_flag))
        scenario = load_scenario(args.scenario)
        baseline = load_any_run(args.baseline, scenario)
        candidate = load_any_run(args.candidate, scenario)
        if _format(baseline) != _format(candidate):
            raise ValueError("baseline and candidate must have the same run format "
                             "(/1, /2 or /3)")
        evidence = {}
        for name, path, run in (("baseline", args.evidence_baseline, baseline),
                                ("candidate", args.evidence_candidate, candidate)):
            if path is None:
                continue
            if _format(run) != FORMAT_V2:  # only a 0.2.1 capture has evidence to verify
                raise ValueError(f"--evidence-{name} applies only to a /2 run")
            bundle, digest = load_evidence(path)
            evidence[name] = verify_evidence(run, bundle, digest, scenario)
    except (BenchmarkFormatError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if isinstance(baseline, RunV2):
        report_v2 = compare_v2(scenario, baseline, candidate, policy,  # type: ignore[arg-type]
                               baseline_evidence=evidence.get("baseline"),
                               candidate_evidence=evidence.get("candidate"))
        sys.stdout.write(render_v2(report_v2))
        return 0 if report_v2.passed else 1
    report = compare(scenario, baseline, candidate, policy)
    sys.stdout.write(render(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
