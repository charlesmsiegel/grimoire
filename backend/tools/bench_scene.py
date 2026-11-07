"""Print 048's representative benchmarks: a played turn, cold and warm, over a growing history, and two imports.

Run from the repository root: ``python backend/tools/bench_scene.py [--history 10 100 1000] [--no-v1]
[--no-imports] [--untraced]``. Everything it plays is synthetic (``backend/tests/scene_bench.py``): fixture
libraries in a temporary folder, scripted provider replies, no real library and no paid call. The numbers are
wall times on this machine and say nothing about a real provider's latency, which the cost lines mark as
``synthetic``.

``--untraced`` times the calls from outside only, so the same table can be taken from a build whose runner has
no tracing hook and compared. A checkout from before 048 also lacks ``grimoire.tracing``, which this tool and
``scene_bench`` import: copy that package (it is pure and stands alone), ``scene_bench.py`` and this file into
the checkout and run it there with its own ``src`` on ``PYTHONPATH``, as ``docs/PERFORMANCE.md``'s baseline was.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from grimoire.tracing import call_costs

TESTS = Path(__file__).resolve().parents[1] / "tests"


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:8.1f}"


def _usage(totals) -> str:
    parts = []
    for name in ("input_tokens", "output_tokens", "cache_read_tokens"):
        value = totals.sums.get(name, 0)
        missing = totals.unreported.get(name, 0)
        parts.append(f"{name.split('_')[0]}={value}{'+?' if missing else ''}")
    return " ".join(parts)


def _turn_table(title: str, measured) -> None:
    print(f"\n{title}")
    print(f"{'history':>8} {'phase':>5} {'call':>9} {'wall ms':>8} {'files':>6} {'decoded':>8} {'reused':>7}"
          "  slowest self time")
    for each in measured:
        line = f"{each.history:>8} {each.phase:>5} {each.operation:>9} {_ms(each.wall_s)}"
        trace = each.trace
        if trace is None:
            print(line)
            continue
        reads = trace.counts("read.turns")
        top = ", ".join(f"{op.operation} {op.self_seconds * 1000:.1f}" for op in trace.breakdown()[:3])
        print(f"{line} {reads.get('files', 0):>6} {reads.get('decoded', 0):>8} {reads.get('reused', 0):>7}  {top}")
        if each.operation == "generate" and trace.calls:
            for role in call_costs(trace).roles:
                print(f"{'':>34}{role.stage} ({role.actor_id or 'scene'}): calls={role.calls} "
                      f"retries={role.retries} {_usage(role.usage)} latency={role.latency} cost={role.cost_state}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", type=int, nargs="+", default=[10, 100, 1000],
                        help="kept turns written before the measured turns (default: 10 100 1000)")
    parser.add_argument("--no-v1", action="store_true", help="skip the migrated two-NPC campaign")
    parser.add_argument("--no-imports", action="store_true", help="skip the import timings")
    parser.add_argument("--untraced", action="store_true", help="time from outside only (no tracing hook)")
    args = parser.parse_args(argv)
    if any(size < 1 for size in args.history):
        parser.error("a history is at least one turn")
    sys.path.insert(0, str(TESTS))
    import scene_bench  # noqa: PLC0415  (a test helper, importable only once its folder is on the path)

    traced = not args.untraced
    histories = tuple(sorted(set(args.history)))
    with tempfile.TemporaryDirectory(prefix="grimoire-bench-") as scratch:
        root = Path(scratch)
        _turn_table("Card chat (one NPC, lorebook)", scene_bench.card_turns(root / "card", histories,
                                                                             traced=traced))
        if not args.no_v1:
            _turn_table("Migrated V1 campaign (two NPCs answer)", scene_bench.v1_turns(root / "v1", histories,
                                                                                        traced=traced))
        if not args.no_imports:
            print("\nImports")
            for trace in scene_bench.import_traces(root / "imports"):
                phases = ", ".join(f"{trace.spans[i].operation} {trace.spans[i].seconds * 1000:.1f}"
                                   for i in trace.children(0))
                print(f"{trace.operation:>12} {_ms(trace.seconds)} ms  {phases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
