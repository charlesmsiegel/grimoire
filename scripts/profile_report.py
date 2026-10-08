"""Read the phase profiles `pytest --phase-profile=PATH` writes.

The plugin (`backend/tests/phase_profile.py`) records one run, node by node.
This turns one or more of those files into the numbers the test-suite
acceleration work argues from (`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`
§4.3, §13): where the time went, how concentrated it is, which fixtures and
operations it went to, and -- between two runs -- which nodes appeared,
disappeared or changed outcome. Standard library only.

    python scripts/profile_report.py summary PROFILE.json [--top N] [--exit-with-run]
    python scripts/profile_report.py manifest PROFILE.json
    python scripts/profile_report.py compare BEFORE.json AFTER.json
    python scripts/profile_report.py budget PROFILE.json BUDGET.json [--previous P.json ...]

`summary --exit-with-run` prints the summary and then exits with the status
pytest recorded for the profiled run, so a target that profiles a failing
selection still prints where the time went and still fails.

`manifest` prints what the run collected -- a node that never reported (`-x`,
an interrupt, a crash) is still in it -- falling back to the reported nodes
for a profile that recorded no collection.

`compare` exits 1 when a node in BEFORE is missing from AFTER, when a node's
outcome changed (per phase, so a teardown error cannot mask a call that began
failing), or when AFTER collected a node it never reported on: a run that
quietly ran less is the failure every other number here would hide. Nodes
AFTER added are listed and do not fail it.

`budget` is the lightweight performance budget CI runs after the backend
suite (spec §13): it compares a run against a committed, versioned reference
and prints GitHub `::warning::` lines -- fewer tests collected, more skipped,
a single test over the slow-test line, and a session slower than the
tolerance -- plus a short Markdown summary. It always exits 0. Session time is
judged on the median of five comparable runs, never on one: `--previous`
names earlier runs' profiles, newest first, and those with the same Python
minor version, worker count, scheduler and coverage measurement as this one
make up the window. With fewer than five the summary shows the median so far
and warns of nothing, since one run on a shared runner is noise, not a
regression. The reference moves deliberately, in the commit that moves it.
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
    return sorted(doc.get("collected") or doc["tests"])


def compare(before: dict, after: dict) -> dict:
    """Node-level differences between two profiles.

    Outcomes are compared per phase as well as in summary, so a call that went
    from passed to failed is seen even where a teardown error makes both runs'
    one-word outcome the same. `unreported` is what AFTER collected and never
    reported on -- a crash, `-x`, an interrupt -- which a report-only manifest
    could not tell from a node that was never collected.
    """
    b, a = before["tests"], after["tests"]
    b_ids = set(before.get("collected") or b)
    a_ids = set(after.get("collected") or a)
    missing = sorted(b_ids - a_ids)
    added = sorted(a_ids - b_ids)
    changed = sorted(
        (nid, _outcome_key(b[nid]), _outcome_key(a[nid]))
        for nid in set(b) & set(a) if _outcome_key(b[nid]) != _outcome_key(a[nid]))
    unreported = sorted(set(after.get("collected") or ()) - set(a))
    return {
        "missing": missing,
        "added": added,
        "outcome_changed": changed,
        "unreported": unreported,
        "node_seconds": (sum(map(total, b.values())), sum(map(total, a.values()))),
        "wall_s": (before.get("wall_s"), after.get("wall_s")),
    }


def _outcome_key(rec: dict) -> str:
    phases = rec.get("phases")
    detail = ",".join(f"{k}={v}" for k, v in sorted(phases.items())) if phases else ""
    return f"{rec.get('outcome')}" + (f" ({detail})" if detail else "")


def failed(diff: dict) -> bool:
    """Whether `compare`'s result is a failure: anything that went missing,
    changed outcome, or was collected and never reported."""
    return bool(diff["missing"] or diff["outcome_changed"] or diff["unreported"])


WINDOW = 5


def _settings(doc: dict) -> tuple:
    """What makes two runs' session times comparable."""
    minor = ".".join(str(doc.get("python") or "").split(".")[:2])
    return (minor, doc.get("workers"), doc.get("distribution"), bool(doc.get("coverage")))


def session_window(doc: dict, previous: list[dict] = ()) -> list[float]:
    """This run's session seconds, then those of up to WINDOW - 1 comparable
    earlier runs (`previous` is newest first). An earlier run counts only if it
    ran to the end -- pytest's 0 or 1 -- since an interrupted session's time
    says nothing about the suite's."""
    mine = _settings(doc)
    earlier = [p["wall_s"] for p in previous
               if _settings(p) == mine and p.get("wall_s") is not None
               and p.get("exitstatus") in (0, 1)]
    return [doc.get("wall_s") or 0.0, *earlier[:WINDOW - 1]]


def budget_findings(doc: dict, ref: dict, previous: list[dict] = ()) -> list[tuple[str, str]]:
    """(kind, message) for each way `doc` departs from the budget `ref`.

    `kind` is one of slower, fewer-tests, more-tests, more-skips, slow-test;
    `more-tests` is a notice (tests were added), every other kind a warning.
    `slower` is judged on the median of a full window of comparable runs.
    """
    found = []
    tolerance = ref.get("tolerance", 0.2)
    window, budget = session_window(doc, previous), ref["wall_s"]
    median = statistics.median(window)
    if len(window) >= WINDOW and median > budget * (1 + tolerance):
        found.append(("slower", (f"the median session of the last {len(window)} comparable "
                                 f"runs took {median:.0f}s against a budget of {budget:.0f}s "
                                 f"(+{median / budget - 1:.0%}); check the runner and "
                                 f"dependency versions before calling it a regression")))
    collected = len(doc.get("collected") or doc["tests"])
    if collected < ref["collected"]:
        found.append(("fewer-tests", (f"{ref['collected'] - collected} fewer tests collected "
                                      f"than the budget's {ref['collected']}; a removal should "
                                      f"be deliberate, and move the budget in the same commit")))
    elif collected > ref["collected"]:
        found.append(("more-tests", (f"{collected - ref['collected']} more tests collected "
                                     f"than the budget's {ref['collected']}")))
    skipped = sum(1 for rec in doc["tests"].values() if rec.get("outcome") == "skipped")
    if skipped > ref["skipped"]:
        found.append(("more-skips", (f"{skipped} tests skipped against the budget's "
                                     f"{ref['skipped']}")))
    line = ref.get("slow_test_s", 30.0)
    for nid, rec in sorted(doc["tests"].items()):
        if total(rec) > line:
            found.append(("slow-test", f"{nid} took {total(rec):.1f}s (the line is {line:.0f}s)"))
    return found


def _print_budget(doc: dict, ref: dict, found: list, window: list[float]) -> None:
    for kind, message in found:
        level = "notice" if kind == "more-tests" else "warning"
        print(f"::{level} title=test budget ({kind})::{message}")
    median = statistics.median(window)
    judged = ("" if len(window) >= WINDOW
              else f"; {WINDOW} are needed before it is judged")
    print("\n### Backend test budget\n")
    print(f"| | this run | budget |\n|---|---|---|\n"
          f"| session | {doc.get('wall_s', 0):.0f}s | {ref['wall_s']:.0f}s "
          f"(+{ref.get('tolerance', 0.2):.0%} allowed) |\n"
          f"| session, median of {len(window)} comparable run(s){judged} "
          f"| {median:.0f}s | {ref['wall_s']:.0f}s |\n"
          f"| collected | {len(doc.get('collected') or doc['tests'])} | {ref['collected']} |\n"
          f"| skipped | {sum(1 for r in doc['tests'].values() if r.get('outcome') == 'skipped')}"
          f" | {ref['skipped']} |")
    print(f"\n{len(found)} finding(s); this step never fails the job.")


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


def _summary_command(args: argparse.Namespace) -> int:
    s = summarize(load(args.profile), top=max(args.top, 100) if args.json else args.top)
    if args.json:
        print(json.dumps(s, indent=1, sort_keys=True))
    else:
        _print_summary(s, args.top)
    if args.exit_with_run:
        status = s["header"].get("exitstatus")
        return 1 if status is None else int(status)
    return 0


def _budget_command(args: argparse.Namespace) -> int:
    """Always 0: see the module docstring."""
    if not pathlib.Path(args.profile).exists():
        print(f"::warning title=test budget::no profile at {args.profile} -- "
              "the suite stopped before writing one; its own step says why")
        return 0
    doc, ref = load(args.profile), load(args.budget)
    previous = []
    for path in args.previous:
        if not pathlib.Path(path).exists():
            continue
        try:
            previous.append(load(path))
        except (OSError, ValueError):
            print(f"(skipped an unreadable earlier profile: {path})")
    _print_budget(doc, ref, budget_findings(doc, ref, previous), session_window(doc, previous))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("summary")
    p.add_argument("profile")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.add_argument("--exit-with-run", action="store_true",
                   help="exit with the status pytest recorded for the profiled run")
    p = sub.add_parser("manifest")
    p.add_argument("profile")
    p = sub.add_parser("compare")
    p.add_argument("before")
    p.add_argument("after")
    p = sub.add_parser("budget")
    p.add_argument("profile")
    p.add_argument("budget")
    p.add_argument("--previous", nargs="*", default=[],
                   help="earlier runs' profiles, newest first; missing paths are skipped")
    args = parser.parse_args(argv)

    if args.cmd == "summary":
        return _summary_command(args)
    if args.cmd == "budget":
        return _budget_command(args)
    if args.cmd == "manifest":
        print("\n".join(manifest(load(args.profile))))
        return 0
    diff = compare(load(args.before), load(args.after))
    for nid in diff["missing"]:
        print(f"MISSING  {nid}")
    for nid in diff["added"]:
        print(f"added    {nid}")
    for nid, was, now in diff["outcome_changed"]:
        print(f"OUTCOME  {nid}: {was} -> {now}")
    for nid in diff["unreported"]:
        print(f"UNREPORTED {nid}")
    (bs, as_), (bw, aw) = diff["node_seconds"], diff["wall_s"]
    print(f"node-seconds {bs:.1f} -> {as_:.1f}; wall {bw} -> {aw}")
    return 1 if failed(diff) else 0


if __name__ == "__main__":
    sys.exit(main())
