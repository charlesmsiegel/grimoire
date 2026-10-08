"""Read the phase profiles `pytest --phase-profile=PATH` writes.

The plugin (`backend/tests/phase_profile.py`) records one run, node by node.
This turns one or more of those files into the numbers the test-suite
acceleration work argues from (`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`
§4.3, §13): where the time went, how concentrated it is, which fixtures and
operations it went to, and -- between two runs -- which nodes appeared,
disappeared or changed outcome. Standard library only.

    python scripts/profile_report.py summary PROFILE.json [--top N]
    python scripts/profile_report.py manifest PROFILE.json
    python scripts/profile_report.py compare BEFORE.json AFTER.json

`compare` exits 1 when a node in BEFORE is missing from AFTER: a run that
quietly collected less is the failure every other number here would hide.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

PHASES = ("setup", "call", "teardown")


def load(path: str | pathlib.Path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def total(rec: dict) -> float:
    """A node's seconds across the three phases."""
    return sum(rec.get(f"{p}_s", 0.0) for p in PHASES)


def module_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def concentration(seconds: list[float], shares=(0.01, 0.05, 0.10)) -> dict[str, float]:
    """The fraction of all node time held by the slowest `share` of nodes.

    Rounded *up* to at least one node, so a small suite still reports its
    slowest node rather than an empty slice.
    """
    ordered = sorted(seconds, reverse=True)
    whole = sum(ordered) or 1.0
    out = {}
    for share in shares:
        n = max(1, -(-len(ordered) * share // 1)) if ordered else 0
        out[f"top_{share:.0%}"] = sum(ordered[: int(n)]) / whole
    return out


def summarize(doc: dict, top: int = 100) -> dict:
    """Everything the baseline document reports, from one profile."""
    tests = doc["tests"]
    outcomes = collections.Counter(rec.get("outcome", "unknown") for rec in tests.values())

    def ranked(key) -> list[tuple[str, float]]:
        rows = [(nid, key(rec)) for nid, rec in tests.items()]
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows[:top]

    modules: dict[str, dict] = collections.defaultdict(
        lambda: {"nodes": 0, "seconds": 0.0, "setup_s": 0.0, "call_s": 0.0, "teardown_s": 0.0})
    fixtures: dict[str, dict] = collections.defaultdict(lambda: {"uses": 0, "seconds": 0.0})
    ops: collections.Counter = collections.Counter()
    ops_by_module: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for nid, rec in tests.items():
        m = modules[module_of(nid)]
        m["nodes"] += 1
        m["seconds"] += total(rec)
        for p in PHASES:
            m[f"{p}_s"] += rec.get(f"{p}_s", 0.0)
        for name, sec in rec.get("fixtures_s", {}).items():
            fixtures[name]["uses"] += 1
            fixtures[name]["seconds"] += sec
        for name, n in rec.get("ops", {}).items():
            ops[name] += n
            ops_by_module[module_of(nid)][name] += n

    by_module = sorted(modules.items(), key=lambda kv: (-kv[1]["seconds"], kv[0]))
    by_fixture = sorted(fixtures.items(), key=lambda kv: (-kv[1]["seconds"], kv[0]))
    node_seconds = [total(rec) for rec in tests.values()]
    return {
        "header": {k: v for k, v in doc.items() if k != "tests"},
        "nodes": len(tests),
        "outcomes": dict(sorted(outcomes.items())),
        "node_seconds": sum(node_seconds),
        "phase_seconds": {p: sum(rec.get(f"{p}_s", 0.0) for rec in tests.values())
                          for p in PHASES},
        "median_node_s": statistics.median(node_seconds) if node_seconds else 0.0,
        "concentration": concentration(node_seconds),
        "top_total": ranked(total),
        "top_call": ranked(lambda r: r.get("call_s", 0.0)),
        "top_setup": ranked(lambda r: r.get("setup_s", 0.0)),
        "top_teardown": ranked(lambda r: r.get("teardown_s", 0.0)),
        "modules": by_module[:30],
        "fixtures": by_fixture,
        "ops": dict(sorted(ops.items())),
        "ops_by_module": {
            m: dict(sorted(c.items()))
            for m, c in sorted(ops_by_module.items(),
                               key=lambda kv: (-sum(kv[1].values()), kv[0]))[:30]},
    }


def manifest(doc: dict) -> list[str]:
    return sorted(doc["tests"])


def compare(before: dict, after: dict) -> dict:
    """Node-level differences between two profiles."""
    b, a = before["tests"], after["tests"]
    missing = sorted(set(b) - set(a))
    added = sorted(set(a) - set(b))
    changed = sorted(
        (nid, b[nid].get("outcome"), a[nid].get("outcome"))
        for nid in set(b) & set(a) if b[nid].get("outcome") != a[nid].get("outcome"))
    return {
        "missing": missing,
        "added": added,
        "outcome_changed": changed,
        "node_seconds": (sum(map(total, b.values())), sum(map(total, a.values()))),
        "wall_s": (before.get("wall_s"), after.get("wall_s")),
    }


def _print_summary(s: dict, top: int) -> None:
    h = s["header"]
    print(f"python {h.get('python')}  sha {h.get('git_sha')}  coverage {h.get('coverage')}  "
          f"workers {h.get('workers')} ({h.get('distribution')})  exit {h.get('exitstatus')}")
    print(f"wall {h.get('wall_s', 0):.1f}s  collect {h.get('collect_s')}  "
          f"nodes {s['nodes']}  node-seconds {s['node_seconds']:.1f}  "
          f"median node {s['median_node_s'] * 1000:.1f}ms")
    print("outcomes:", ", ".join(f"{k} {v}" for k, v in s["outcomes"].items()))
    print("phases:", ", ".join(f"{p} {v:.1f}s" for p, v in s["phase_seconds"].items()))
    print("concentration:", ", ".join(f"{k} {v:.1%}" for k, v in s["concentration"].items()))
    for title, key in (("total", "top_total"), ("call", "top_call"),
                       ("setup", "top_setup"), ("teardown", "top_teardown")):
        print(f"\ntop {min(top, len(s[key]))} by {title}:")
        for nid, sec in s[key][:top]:
            print(f"  {sec:8.3f}  {nid}")
    print("\nslowest modules:")
    for mod, m in s["modules"]:
        print(f"  {m['seconds']:8.1f}s  {m['nodes']:5d} nodes  "
              f"(setup {m['setup_s']:.1f} call {m['call_s']:.1f} "
              f"teardown {m['teardown_s']:.1f})  {mod}")
    print("\nfixtures (exclusive setup seconds):")
    for name, f in s["fixtures"][:top]:
        print(f"  {f['seconds']:8.2f}s  {f['uses']:6d} uses  {name}")
    print("\noperations:", ", ".join(f"{k} {v}" for k, v in s["ops"].items()) or "none")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("summary")
    p.add_argument("profile")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p = sub.add_parser("manifest")
    p.add_argument("profile")
    p = sub.add_parser("compare")
    p.add_argument("before")
    p.add_argument("after")
    args = parser.parse_args(argv)

    if args.cmd == "summary":
        s = summarize(load(args.profile), top=max(args.top, 100) if args.json else args.top)
        if args.json:
            print(json.dumps(s, indent=1, sort_keys=True))
        else:
            _print_summary(s, args.top)
        return 0
    if args.cmd == "manifest":
        print("\n".join(manifest(load(args.profile))))
        return 0
    diff = compare(load(args.before), load(args.after))
    for nid in diff["missing"]:
        print(f"MISSING  {nid}")
    for nid in diff["added"]:
        print(f"added    {nid}")
    for nid, was, now in diff["outcome_changed"]:
        print(f"outcome  {nid}: {was} -> {now}")
    (bs, as_), (bw, aw) = diff["node_seconds"], diff["wall_s"]
    print(f"node-seconds {bs:.1f} -> {as_:.1f}; wall {bw} -> {aw}")
    return 1 if diff["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
