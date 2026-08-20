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
import tomllib

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


def _pin(package: str) -> str:
    """The exact version `backend/pyproject.toml` pins this tool to.

    The baselines are counts, so they are only meaningful against one version
    of the tool that produced them. Both are pinned exactly in the `dev`
    extra for that reason; this reads the pin back rather than repeating it,
    since a second copy would be the thing that goes stale.
    """
    with (ROOT / "backend" / "pyproject.toml").open("rb") as fh:
        deps = tomllib.load(fh)["project"]["optional-dependencies"]["dev"]
    for dep in deps:
        name, sep, version = dep.partition("==")
        if sep and name.strip() == package:
            return version.strip()
    raise ToolError(
        f"backend/pyproject.toml's `dev` extra does not pin {package} exactly. "
        f"The baselines are counts of what one version of it reported, so an "
        f"unpinned one makes them unreproducible."
    )


def _require_version(package: str, reported: str) -> None:
    """Fail before the findings are collected, not after they disagree.

    Without this, a contributor whose environment has a different ruff runs
    `make baseline`, commits a file CI cannot reproduce, and gets a diff full
    of counts as the explanation.
    """
    want = _pin(package)
    # A whole version token, not a substring: `"1.0" in "11.0.3"` is true, and
    # a check that passes on the wrong tool is worse than no check.
    if want not in re.findall(r"\d+(?:\.\d+)*", reported):
        raise ToolError(
            f"{package} {want} is what the baselines were built with, and this "
            f"is:\n    {reported.strip()}\n"
            f"Install the pin (`pip install -e \"./backend[dev]\"`) before "
            f"running the gate; a different version reports a different count "
            f"and every branch would go red."
        )


def _json(out: str, tool: str) -> object:
    """`json.loads`, but a tool that printed something else is a broken tool
    rather than a stack trace. npx prints its own diagnostics on stdout when
    the binary is missing, which is exactly this case."""
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"{tool} did not print JSON ({exc}). It said:\n{out[:2000]}"
        ) from exc


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
    _require_version("ruff", _run([sys.executable, "-m", "ruff", "--version"],
                                  cwd=ROOT, ok_codes=(0,)))
    out = _run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json",
         "--no-cache"],
        cwd=ROOT, ok_codes=(0, 1),
    )
    found: Findings = collections.Counter()
    for item in _json(out, "ruff"):
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
    _require_version("mypy", _run([sys.executable, "-m", "mypy", "--version"],
                                  cwd=ROOT, ok_codes=(0,)))
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


def _node_modules_present() -> bool:
    return (ROOT / "frontend" / "node_modules" / ".bin").is_dir()


def collect_eslint() -> Findings:
    """eslint over `frontend/`, which needs its `node_modules` installed.

    eslint's version is pinned by `frontend/package-lock.json` and installed by
    `npm ci`, so there is no `_require_version` call to match the other two.
    """
    frontend = ROOT / "frontend"
    if not _node_modules_present():
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
    for file_report in _json(out, "eslint"):
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


def _shown(path: pathlib.Path) -> str:
    """Repo-relative for the message a contributor reads; absolute if it is not
    under the checkout, which is how the tests point `BASELINES` elsewhere."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


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
    """The committed counts. Missing is an error rather than an empty
    baseline: every finding in the tree would read as brand new, and the
    contributor would get a wall of "fix these" for code they did not touch
    instead of the one sentence that is true."""
    path = baseline_path(tool)
    if not path.exists():
        raise ToolError(
            f"{_shown(path)} is missing. It is a committed file, not a cache -- "
            f"restore it (`git checkout {_shown(path)}`), or, if this tool is "
            f"genuinely new here, create it with "
            f"`--update --accept-regressions`."
        )
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
            f"then commit {_shown(baseline_path(tool))} "
            f"with the change that earned it.",
            file=sys.stderr,
        )
        print(_report(improvements), file=sys.stderr)
    return 1


def update(tool: str, *, accept_regressions: bool = False) -> int:
    """Rewrite the baseline -- but by default only downward.

    This is the half that makes the word "ratchet" true. Without it the file
    is a ratchet in the check direction and a free pass in the update one:
    `make baseline` would be the button that turns any red gate green, and
    the only thing standing between a regression and the main branch would be
    a reviewer noticing a number went up inside an 861-line generated file.

    So counts may fall, and pairs may disappear, with no ceremony. A count
    that would *rise* stops the write and names every pair, because the
    reasons for one are all worth saying out loud: a regression that should be
    fixed instead, a rename, a widened rule set, or code arriving from a
    branch that predates the gate. `--accept-regressions` says it, and leaves
    the word in the shell history and the CI log.
    """
    found = COLLECTORS[tool]()
    # An absent baseline is not an exemption: every finding counts as a rise,
    # so bootstrapping a fourth tool needs `--accept-regressions` like any
    # other widening. One rule with no exceptions, so `rm` cannot become the
    # short way round the one above.
    prior = read_baseline(tool) if baseline_path(tool).exists() else collections.Counter()
    grew, _ = compare(found, prior)
    if grew and not accept_regressions:
        print(
            f"{tool}: refusing to write a baseline that permits more than the "
            f"current one. {len(grew)} (file, rule) pair(s) would go up:",
            file=sys.stderr,
        )
        print(_report(grew), file=sys.stderr)
        print(
            "\nFix them, or -- if this is a rename, a deliberate widening of "
            "the rule set, or a merge bringing code the gate has not seen -- "
            "say so:\n"
            f"    {pathlib.Path(sys.argv[0]).name} {tool} --update "
            f"--accept-regressions",
            file=sys.stderr,
        )
        return 1
    BASELINES.mkdir(exist_ok=True)
    # newline="\n" explicitly: text mode would translate to CRLF on Windows,
    # so the same tree would produce a different file depending on who ran
    # `make baseline`, and every regeneration on the other platform would
    # land as a whole-file diff. `.gitattributes` pins the checkout to match.
    baseline_path(tool).write_text(dumps(found), encoding="utf-8", newline="\n")
    print(f"{tool}: wrote {sum(found.values())} finding(s) to "
          f"{_shown(baseline_path(tool))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tool", choices=sorted(COLLECTORS))
    parser.add_argument(
        "--update", action="store_true",
        help="rewrite the baseline from the current tree instead of checking it",
    )
    parser.add_argument(
        "--accept-regressions", action="store_true",
        help="with --update, allow counts to rise (a rename, a widened rule "
             "set, or a merge bringing code the gate has not seen). Without it "
             "--update only ever lowers them.",
    )
    args = parser.parse_args(argv)
    if args.accept_regressions and not args.update:
        parser.error("--accept-regressions only means anything with --update")
    try:
        if args.update:
            return update(args.tool, accept_regressions=args.accept_regressions)
        return check(args.tool)
    except ToolError as exc:
        print(f"{args.tool}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
