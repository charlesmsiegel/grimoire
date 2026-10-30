"""Command line: ``python -m grimoire.evals.story_quality SCENARIO BASELINE CANDIDATE``.

Exit status 0 = pass, 1 = gate failed, 2 = invalid input or usage. Writes only to stdout/stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .format import BenchmarkFormatError
from .gate import compare
from .loader import load_run, load_scenario
from .policy import GatePolicy
from .report import render


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = GatePolicy(
            max_voice_drop=args.max_voice_drop, cached_input_weight=args.cached_input_weight,
            max_cost_increase=args.max_cost_increase, max_latency_increase=args.max_latency_increase,
            allowed_flags=frozenset(args.allow_flag))
        scenario = load_scenario(args.scenario)
        baseline = load_run(args.baseline, scenario)
        candidate = load_run(args.candidate, scenario)
    except (BenchmarkFormatError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    report = compare(scenario, baseline, candidate, policy)
    sys.stdout.write(render(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
