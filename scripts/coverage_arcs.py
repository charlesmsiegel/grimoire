"""The executed line and arc sets of a backend coverage run, as data.

A coverage *percentage* cannot say whether a change to the suite lost a
behaviour: two tests merged into one can keep 93.4% while one branch that only
the dropped test reached goes dark and another one lights up. What can say it
is the set of production lines and arcs (branch edges) the suite executed,
compared as sets. That is what this writes and compares
(`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md` §4.4).

    python scripts/coverage_arcs.py dump .coverage OUT.json --profile PROFILE.json [--src WORKTREE/backend/src]
    python scripts/coverage_arcs.py stable OUT.json RUN1.json RUN2.json RUN3.json
    python scripts/coverage_arcs.py diff BEFORE.json AFTER.json
    python scripts/coverage_arcs.py contexts .coverage OUT.json --match tests/test_x.py::

**dump** reads a coverage data file through coverage.py's `CoverageData` API,
keys every file by its path under `backend/src` (so two checkouts' dumps
compare), and lists every `.py` file of the package -- one no test imported
appears with empty sets rather than not at all, which is the same honesty
`source = ["grimoire"]` buys the percentage. A measured file that does not
belong to *this* checkout's `backend/src` is an error, not a skip: it means the
run imported another tree (an editable install from a different worktree), and
its numbers say nothing about this one. `--src` names a different tree on
purpose -- a worktree the run was made in -- and the same rule then holds
against that tree.

Each file's entry carries the SHA-256 of the source the *run* measured, which
`dump` takes from the phase profile the same pytest invocation wrote
(`--phase-profile`, which records it before any test runs) and checks the tree
against: a coverage file kept across an edit or a rebase and dumped afterwards
is refused rather than labelled with the new text's hash. Line and arc numbers
mean something only against the text they were measured on, so **stable** and
**diff** in turn refuse two dumps whose shared files differ in content, rather
than intersecting numbers that point at different code.

**stable** is the intersection of several dumps, plus the remainder that some
runs executed and others did not. Some production branches here depend on
timing (a wall-clock second boundary, a persist retry, a held thread), so one
run's set is not a reference anything can be held to; the intersection is.

**diff** exits 1 when AFTER is missing a file, line or arc BEFORE had. It
prints what was gained too, but only a loss fails. Either command exits 2 on
dumps it refuses to compare.

**contexts** reads a run measured with `--cov-context=test` and writes, for
each test context whose name contains `--match`, the arcs it executed. Like
**dump**, it refuses data recorded without branch measurement, which has no
arcs to report and would read as tests that executed nothing. That is
the per-test half of a consolidation's evidence: which arcs only the tests
being merged reached. Narrow it with `--match`; every context of a whole
suite is a large file and a slow read.

Never compare a dump from one interpreter against another's: coverage.py's
arcs for the same source differ between Python versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

from coverage import CoverageData

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
PACKAGE = SRC / "grimoire"
SCHEMA_VERSION = 2


def _key(path: pathlib.Path, src: pathlib.Path) -> str:
    return path.relative_to(src).as_posix()


def _source_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_with_arcs(data_file: str | pathlib.Path) -> CoverageData:
    data = CoverageData(basename=str(data_file))
    data.read()
    if not data.has_arcs():
        raise SystemExit(f"{data_file}: no arc data -- was the run measured with branch = true?")
    return data


def dump(data_file: str | pathlib.Path, recorded: dict[str, str] | None,
         src: pathlib.Path = SRC) -> dict:
    """Every package file's executed lines and arcs, keyed under `src`, each
    with the hash of the source the run measured (`recorded`, from its phase
    profile) -- refused if `src` no longer holds exactly that source."""
    data = _read_with_arcs(data_file)
    if not recorded:
        raise SystemExit(f"{data_file}: no recorded sources -- dump needs the phase profile "
                         "the same run wrote (--profile), to know what text it measured")
    now = {_key(p, src): _source_hash(p) for p in sorted((src / "grimoire").rglob("*.py"))}
    moved = sorted(k for k in set(now) | set(recorded) if now.get(k) != recorded.get(k))
    if moved:
        raise SystemExit(f"{data_file}: {len(moved)} source file(s) differ from what the run "
                         f"measured, e.g. {moved[0]} -- the tree changed since the run")
    files: dict[str, dict] = {
        key: {"sha256": digest, "lines": [], "arcs": []} for key, digest in sorted(now.items())
    }
    foreign = []
    for measured in sorted(data.measured_files()):
        path = pathlib.Path(measured).resolve()
        try:
            key = _key(path, src.resolve())
        except ValueError:
            foreign.append(measured)
            continue
        if key not in now:
            foreign.append(measured)
            continue
        files[key] = {
            "sha256": now[key],
            "lines": sorted(data.lines(measured) or []),
            "arcs": sorted([a, b] for a, b in (data.arcs(measured) or [])),
        }
    if foreign:
        raise SystemExit(
            f"{data_file}: {len(foreign)} measured file(s) outside {src} -- the run "
            f"imported another checkout's sources, e.g. {foreign[0]}")
    return {"schema_version": SCHEMA_VERSION, "files": files}


def contexts(data_file: str | pathlib.Path, match: str, src: pathlib.Path = SRC) -> dict:
    """{context: {file: arcs}} for every test context containing `match`."""
    data = _read_with_arcs(data_file)
    root = src.resolve()
    files = {}
    for measured in data.measured_files():
        try:
            files[measured] = _key(pathlib.Path(measured).resolve(), root)
        except ValueError:
            raise SystemExit(f"{data_file}: {measured} is outside {src}") from None
    out: dict[str, dict] = {}
    for ctx in sorted(c for c in data.measured_contexts() if match in c):
        data.set_query_contexts([f"^{re.escape(ctx)}$"])
        arcs = {key: sorted(list(a) for a in data.arcs(measured) or [])
                for measured, key in sorted(files.items(), key=lambda kv: kv[1])}
        out[ctx] = {k: v for k, v in arcs.items() if v}
    data.set_query_contexts(None)
    return out


def fingerprint(doc: dict) -> dict:
    """Counts and a content hash, small enough to commit beside a baseline."""
    files = doc["files"]
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "files": len(files),
        "files_executed": sum(1 for f in files.values() if f["lines"]),
        "lines": sum(len(f["lines"]) for f in files.values()),
        "arcs": sum(len(f["arcs"]) for f in files.values()),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _sets(entry: dict) -> tuple[set[int], set[tuple[int, int]]]:
    return set(entry["lines"]), {tuple(a) for a in entry["arcs"]}


def _entry(lines, arcs, sha256: str | None = None) -> dict:
    entry = {"lines": sorted(lines), "arcs": sorted(list(a) for a in arcs)}
    return {"sha256": sha256, **entry} if sha256 else entry


def _same_source(docs: list[dict], keys) -> None:
    """Refuse dumps whose files in `keys` were not measured on the same text."""
    for key in sorted(keys):
        hashes = [d["files"][key].get("sha256") for d in docs]
        if None in hashes:
            raise ValueError(f"{key}: a dump carries no source hash -- it predates "
                             f"schema {SCHEMA_VERSION}; dump the run again")
        if len(set(hashes)) > 1:
            raise ValueError(f"{key}: the dumps were measured on different source text -- "
                             "line and arc numbers do not compare across it")


def stable(docs: list[dict]) -> dict:
    """The intersection of `docs`, and what only some of them executed."""
    if not docs:
        raise ValueError("stable needs at least one dump")
    keys = set(docs[0]["files"])
    for d in docs[1:]:
        if set(d["files"]) != keys:
            raise ValueError("the dumps list different files -- not the same source snapshot")
    _same_source(docs, keys)
    files, variable = {}, {}
    for key in sorted(keys):
        sets = [_sets(d["files"][key]) for d in docs]
        lines_all = set.intersection(*(s[0] for s in sets))
        arcs_all = set.intersection(*(s[1] for s in sets))
        lines_any = set.union(*(s[0] for s in sets))
        arcs_any = set.union(*(s[1] for s in sets))
        files[key] = _entry(lines_all, arcs_all, docs[0]["files"][key]["sha256"])
        if lines_any - lines_all or arcs_any - arcs_all:
            variable[key] = _entry(lines_any - lines_all, arcs_any - arcs_all)
    return {"schema_version": SCHEMA_VERSION, "runs": len(docs), "files": files,
            "variable": variable}


def diff(before: dict, after: dict) -> dict:
    """What AFTER lost and gained relative to BEFORE, over the same source."""
    _same_source([before, after], set(before["files"]) & set(after["files"]))
    lost, gained = {}, {}
    lost_files = sorted(set(before["files"]) - set(after["files"]))
    for key, entry in sorted(before["files"].items()):
        if key not in after["files"]:
            continue
        bl, ba = _sets(entry)
        al, aa = _sets(after["files"][key])
        if bl - al or ba - aa:
            lost[key] = _entry(bl - al, ba - aa)
        if al - bl or aa - ba:
            gained[key] = _entry(al - bl, aa - ba)
    return {"lost_files": lost_files, "lost": lost, "gained": gained}


def _load(path: str) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _write(path: str, doc: dict) -> None:
    pathlib.Path(path).write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dump")
    p.add_argument("data_file")
    p.add_argument("out")
    p.add_argument("--profile", required=True,
                   help="the phase profile the same run wrote; its `sources` are what was measured")
    p.add_argument("--src", type=pathlib.Path, default=SRC,
                   help="the backend/src the run imported (default: this checkout's)")
    p = sub.add_parser("stable")
    p.add_argument("out")
    p.add_argument("dumps", nargs="+")
    p = sub.add_parser("diff")
    p.add_argument("before")
    p.add_argument("after")
    p = sub.add_parser("contexts")
    p.add_argument("data_file")
    p.add_argument("out")
    p.add_argument("--match", required=True)
    p.add_argument("--src", type=pathlib.Path, default=SRC,
                   help="the backend/src the run imported (default: this checkout's)")
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.cmd == "dump":
        doc = dump(args.data_file, _load(args.profile).get("sources"), src=args.src)
        _write(args.out, doc)
        print(json.dumps(fingerprint(doc), sort_keys=True))
        return 0
    if args.cmd == "stable":
        doc = stable([_load(p) for p in args.dumps])
        _write(args.out, doc)
        varied = doc["variable"]
        print(json.dumps({"stable": fingerprint(doc), "runs": doc["runs"],
                          "variable_files": len(varied),
                          "variable_lines": sum(len(v["lines"]) for v in varied.values()),
                          "variable_arcs": sum(len(v["arcs"]) for v in varied.values())},
                         sort_keys=True))
        return 0
    if args.cmd == "contexts":
        found = contexts(args.data_file, args.match, src=args.src)
        _write(args.out, found)
        print(f"{len(found)} context(s) matching {args.match!r}")
        return 0
    result = diff(_load(args.before), _load(args.after))
    for key in result["lost_files"]:
        print(f"LOST FILE  {key}")
    for key, entry in result["lost"].items():
        print(f"LOST       {key}: lines {entry['lines']} arcs {entry['arcs']}")
    print(f"gained in {len(result['gained'])} file(s)")
    return 1 if result["lost_files"] or result["lost"] else 0


if __name__ == "__main__":
    sys.exit(main())
