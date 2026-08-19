"""The ratchet: gates that block *new* findings without first demanding the old
ones be fixed.

Three of the gates this repo wants -- mypy, the type-checked half of
typescript-eslint, and the wider ruff selection -- surface debt that no single
change can clear. The usual answer is to land them "report-only", but a job
that reports and never fails is exactly the vacuous check `ci.yml` already
argues against in two other places: nobody reads it, and the number goes up.

So they land blocking instead, against a committed baseline of how many
findings each (file, rule) pair has *today*. The gate fails if a pair gains a
finding -- new code is held to the full rule set from the first commit -- and it
also fails if a pair loses one without the baseline being regenerated, which is
what makes the file a ratchet rather than a licence. `make baseline` rewrites
them; the improvement and the smaller baseline land in the same commit, the way
the frozen campaign's `snapshot.json` does.

Keyed by (file, rule), not by file: a file carrying 85 `B904`s must still fail
on its first new `F401`. Not keyed by line, because then every insertion above a
finding would be a "new" one.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINES = ROOT / "lint-baselines"

#: (file, rule) -> count. `file` is repo-relative and always forward-slashed,
#: so a baseline written on Windows and one written on Linux are the same file.
Findings = collections.Counter


class ToolError(RuntimeError):
    """The tool did not run to completion, so its findings are not evidence.

    Separated from "the tool ran and found things" on purpose: a mypy that
    died on a syntax error and a mypy that found nothing both print no
    findings, and treating them alike would turn a broken checker green.
    """


def _rel(path: str) -> str:
    """Repo-relative, forward-slashed. Tools report absolute paths (ruff,
    eslint) or paths relative to their own working directory (mypy, eslint)."""
    p = pathlib.Path(path)
    p = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        # Outside the checkout entirely -- a stdlib stub mypy chased into
        # site-packages, say. Keep it verbatim rather than inventing a path;
        # the baseline guard will notice it does not exist.
        return path.replace("\\", "/")
    return rel.as_posix()


def _run(argv: list[str], cwd: pathlib.Path, ok_codes: tuple[int, ...]) -> str:
    try:
        # check=False: a nonzero exit is how each of these tools says "I found
        # something", so it is the caller that decides which codes are news.
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"{argv[0]} is not installed: {exc}") from exc
    if proc.returncode not in ok_codes:
        raise ToolError(
            f"`{' '.join(argv)}` exited {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc.stdout


# --------------------------------------------------------------------------
# Tool adapters. Each returns the findings; each raises ToolError rather than
# returning an empty Counter when the tool itself broke.
# --------------------------------------------------------------------------

def collect_ruff() -> Findings:
    """`ruff check --output-format json`. Exit 1 means "found violations"."""
    out = _run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json",
         "--no-cache"],
        cwd=ROOT, ok_codes=(0, 1),
    )
    found: Findings = collections.Counter()
    for item in json.loads(out):
        # `code` is null for a syntax error, which ruff reports but cannot
        # attribute to a rule. Those must never be ratchetable.
        code = item.get("code")
        if code is None:
            raise ToolError(
                f"ruff reported an unattributed error (a syntax error?) in "
                f"{item.get('filename')}: {item.get('message')}"
            )
        found[(_rel(item["filename"]), code)] += 1
    return found


#: `path:line:col: error: message  [error-code]` -- mypy's default line format.
#: Only `error` counts; `note` lines elaborate on the error above them.
_MYPY = re.compile(r"^(?P<path>.+?):\d+:(?:\d+:)? error: .*\[(?P<code>[a-z0-9-]+)\]\s*$")


def collect_mypy() -> Findings:
    """mypy over whatever `mypy.ini` says to check. Exit 1 means "found errors".

    No path argument on purpose: `files =` in `mypy.ini` is the one place that
    decides what is checked, so the baseline cannot be regenerated against a
    different set than the gate reads.
    """
    out = _run(
        [sys.executable, "-m", "mypy", "--no-error-summary", "--no-pretty",
         "--show-error-codes"],
        cwd=ROOT, ok_codes=(0, 1),
    )
    found: Findings = collections.Counter()
    for line in out.splitlines():
        m = _MYPY.match(line.strip())
        if m:
            found[(_rel(m["path"]), m["code"])] += 1
        elif " error: " in line:
            # An error mypy printed without an error code is one this ratchet
            # cannot key, so it would silently vanish from the gate.
            raise ToolError(f"mypy error with no error code: {line}")
    return found


#: Stands in for eslint's rule-less finding; not a real rule id, so it can
#: never collide with one.
UNUSED_DISABLE = "(unused-eslint-disable)"


def collect_eslint() -> Findings:
    """eslint over `frontend/`, which needs its `node_modules` installed."""
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules" / ".bin").is_dir():
        raise ToolError(
            "frontend/node_modules is missing -- run `npm ci` in frontend/ "
            "first (`make check-eslint` does)."
        )
    npx = "npx.cmd" if os.name == "nt" else "npx"
    out = _run(
        [npx, "--no-install", "eslint", ".", "--format", "json"],
        cwd=frontend, ok_codes=(0, 1),
    )
    found: Findings = collections.Counter()
    for file_report in json.loads(out):
        for msg in file_report["messages"]:
            if msg.get("fatal"):
                # A parse error. eslint linted nothing in this file, so its
                # zero findings are not evidence of anything.
                raise ToolError(
                    f"eslint could not parse {file_report['filePath']}: "
                    f"{msg.get('message')}"
                )
            # An unused `eslint-disable` is reported with no rule id, because
            # the rule it names is precisely the one that found nothing. It is
            # still a finding, so give it a name and let it ratchet like the
            # rest -- crashing on it would turn a stale suppression into an
            # unreadable harness error.
            rule = msg.get("ruleId") or UNUSED_DISABLE
            found[(_rel(file_report["filePath"]), rule)] += 1
    return found


COLLECTORS = {"ruff": collect_ruff, "mypy": collect_mypy, "eslint": collect_eslint}


# --------------------------------------------------------------------------
# Baseline I/O. One canonical serialization, so `--update` is idempotent and a
# hand edit that reflows the file shows up as a diff.
# --------------------------------------------------------------------------

def baseline_path(tool: str) -> pathlib.Path:
    return BASELINES / f"{tool}.json"


def dumps(found: Findings) -> str:
    nested: dict[str, dict[str, int]] = {}
    for (path, rule), count in found.items():
        nested.setdefault(path, {})[rule] = count
    ordered = {p: dict(sorted(rules.items())) for p, rules in sorted(nested.items())}
    return json.dumps(ordered, indent=2, sort_keys=False) + "\n"


def loads(text: str) -> Findings:
    found: Findings = collections.Counter()
    for path, rules in json.loads(text).items():
        for rule, count in rules.items():
            found[(path, rule)] = count
    return found


def read_baseline(tool: str) -> Findings:
    path = baseline_path(tool)
    if not path.exists():
        return collections.Counter()
    return loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The comparison. Pure, so the guard test can exercise it without a toolchain.
# --------------------------------------------------------------------------

def compare(found: Findings, baseline: Findings) -> tuple[list[str], list[str]]:
    """(regressions, improvements) as human-readable lines.

    Both are failures, for different reasons: a regression is new debt, an
    improvement is debt the baseline still claims. Reporting them separately
    matters because the fix differs -- one is "go fix your code", the other is
    "run `make baseline` and commit it".
    """
    regressions, improvements = [], []
    for key in sorted(set(found) | set(baseline)):
        path, rule = key
        now, was = found[key], baseline[key]
        if now > was:
            regressions.append(f"  {path}  {rule}  {was} -> {now}")
        elif now < was:
            improvements.append(f"  {path}  {rule}  {was} -> {now}")
    return regressions, improvements


#: Lines of detail printed per direction before the rest is summarized. A
#: deleted baseline would otherwise print eight thousand lines and bury the
#: sentence that says what to do about it.
MAX_REPORTED = 40


def _report(lines: list[str]) -> str:
    if len(lines) <= MAX_REPORTED:
        return "\n".join(lines)
    rest = len(lines) - MAX_REPORTED
    return "\n".join([*lines[:MAX_REPORTED], f"  ... and {rest} more"])


def check(tool: str) -> int:
    found = COLLECTORS[tool]()
    baseline = read_baseline(tool)
    regressions, improvements = compare(found, baseline)
    if not regressions and not improvements:
        print(f"{tool}: {sum(found.values())} finding(s), all at baseline.")
        return 0
    if regressions:
        print(f"{tool}: {len(regressions)} (file, rule) pair(s) above the "
              f"baseline -- fix these:", file=sys.stderr)
        print(_report(regressions), file=sys.stderr)
    if improvements:
        print(
            f"\n{tool}: the baseline records findings the tree no longer has "
            f"({len(improvements)} pair(s)). That is good news the baseline has "
            f"to be told about:\n"
            f"    make baseline\n"
            f"then commit {baseline_path(tool).relative_to(ROOT).as_posix()} "
            f"with the change that earned it.",
            file=sys.stderr,
        )
        print(_report(improvements), file=sys.stderr)
    return 1


def update(tool: str) -> int:
    found = COLLECTORS[tool]()
    BASELINES.mkdir(exist_ok=True)
    baseline_path(tool).write_text(dumps(found), encoding="utf-8")
    print(f"{tool}: wrote {sum(found.values())} finding(s) to "
          f"{baseline_path(tool).relative_to(ROOT).as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tool", choices=sorted(COLLECTORS))
    parser.add_argument(
        "--update", action="store_true",
        help="rewrite the baseline from the current tree instead of checking it",
    )
    args = parser.parse_args(argv)
    try:
        return update(args.tool) if args.update else check(args.tool)
    except ToolError as exc:
        print(f"{args.tool}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
