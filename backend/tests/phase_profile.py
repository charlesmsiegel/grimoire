"""An opt-in, per-phase timing profile of the backend suite.

`pytest --durations` answers "which phases were slow" for one run, as text. The
acceleration work (`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`
§4.2) needs the same answer as data -- every node, every phase, which fixture's
own body the time went to, and how many apps, lifespans, reloads, servers and
child processes a test cost -- so that a later change can be compared against
it node by node rather than by a total that hides where the time moved.

Wired by `conftest.py` through `add_option` and `register`, and registered only
when `--phase-profile=PATH` is given. Without the flag this module is imported
and nothing else happens: no hook registered, no counter installed, no output.
The self-tests wire a synthetic suite through the same two functions, so what
they prove is what the gate runs.

What it records, and its reach:

- **Per-phase seconds and outcomes** are pytest's own `report.duration` and
  `report.outcome` for setup, call and teardown, read where reports arrive.
  Under xdist that is the controller, which is handed every worker's reports,
  so exactly one process writes the file. A report for any other phase is
  xdist saying a worker died under the test, and records the node `crashed`.
- **The collected manifest** is every node ID collection produced, recorded
  apart from the reports: a node that was collected and never reported (a
  crash, `-x`, an interrupt) is then distinguishable from one that was never
  collected at all.
- **Fixture seconds** are *exclusive*: the time inside one fixture's own body,
  less the time of every fixture it caused to be set up from inside it (a
  `request.getfixturevalue`, or pytest-asyncio's wrapper fetching its runner),
  so the per-fixture totals add up to no more than was spent. Charged to the
  phase that set the fixture up, call included. Teardown is not split by
  fixture. A fixture of wider scope is charged to the test that triggered it.
- **Operation counts** come from wrapping class-level entry points, so a name
  a test imported by value is still seen: `FastAPI.__init__` (an app built --
  `create_app` and an ad-hoc `FastAPI()` alike, so a sub-app counts as one),
  `TestClient.__enter__` (a lifespan entered), `importlib.reload`,
  `uvicorn.Server.startup` and `subprocess.Popen.__init__`. Counted on the
  process that runs the test, per phase. Approximate by construction; file
  copies and replay evaluations, which the spec also names, are not counted.
- **The run's environment**: interpreter, package versions, CPU and memory,
  the checkout's SHA and whether the tree was dirty, whether `grimoire` was
  imported from this checkout's `src`, and the process's own CPU time and peak
  memory. `wall_s` starts when the plugin is registered, so it leaves out
  interpreter start-up and the conftest import; an outer timer is the
  measure for those.

The fixture and operation annotations ride the test's reports as plain
attributes, which pytest's report serialization copies, so they cross the
xdist wire with the report. That path is exercised only once xdist is a
dependency of the suite; until then it is designed, not proven.

Never recorded: a path outside the repo, a response body, a prompt, a
fixture's value. Node IDs are repo-relative and carry no store content; the
command line is recorded with the repo root and the home directory masked.
"""

from __future__ import annotations

import functools
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import fastapi
import pytest
import uvicorn
from starlette import testclient

try:                                   # POSIX only; Windows has no `resource`
    import resource
except ImportError:                    # pragma: no cover -- exercised on Windows
    resource = None  # type: ignore[assignment]

SCHEMA_VERSION = 1

#: Rounding for every duration written: microseconds. Fixed, so two writes of
#: the same data are byte-identical.
DIGITS = 6

PHASES = ("setup", "call", "teardown")

#: The operation counters, in the order they are reported.
OPS = ("app_built", "lifespan_entered", "module_reloaded", "uvicorn_started",
       "subprocess_spawned")

#: Distributions whose versions decide what a run measures.
PACKAGES = ("pytest", "pytest-cov", "coverage", "pytest-xdist", "pytest-asyncio",
            "pydantic", "fastapi", "starlette", "anyio", "httpx")


def _git(rootdir, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=rootdir, capture_output=True,
                             text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _versions() -> dict:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


def _machine() -> dict:
    try:
        usable = len(os.sched_getaffinity(0))
    except AttributeError:             # pragma: no cover -- not Linux
        usable = os.cpu_count()
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):  # pragma: no cover -- Windows
        memory = None
    return {"platform": sys.platform, "machine": platform.machine(),
            "cpu_count": os.cpu_count(), "cpu_usable": usable,
            "memory_gib": round(memory / 2**30, 1) if memory else None}


def _usage() -> dict:
    """This process's CPU seconds and peak RSS, and its reaped children's."""
    times = os.times()
    out = {"cpu_user_s": times.user, "cpu_sys_s": times.system,
           "children_cpu_s": times.children_user + times.children_system}
    if resource is not None:
        # ru_maxrss is KiB on Linux, bytes on macOS.
        scale = 1 if sys.platform == "darwin" else 1024
        out["max_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 2**20
        out["children_max_rss_mib"] = (
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale / 2**20)
    return out


def _masked_args(args, roots: list[Path]) -> list[str]:
    """The command line, with the prefixes that would say where it ran -- the
    checkout (deepest first) and the home directory -- replaced by
    placeholders."""
    out = []
    for arg in args:
        text = str(arg)
        for root in sorted(roots, key=lambda r: -len(str(r))):
            text = text.replace(str(root), "<repo>")
        out.append(text.replace(str(Path.home()), "<home>"))
    return out


def _round(value):
    if isinstance(value, float):
        return round(value, DIGITS)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def dumps(doc: dict) -> str:
    """The canonical serialization: sorted keys, fixed rounding, one trailing
    newline. Public so the report tools and the tests share it."""
    return json.dumps(_round(doc), indent=1, sort_keys=True) + "\n"


class _Ops:
    """Session-long counters for the expensive operations.

    Each wrapper only increments and delegates; `uninstall` puts back exactly
    what `install` replaced, so a profiled run leaves the classes as it found
    them.
    """

    def __init__(self):
        self.counts = dict.fromkeys(OPS, 0)
        self._restore: list = []

    def _wrap(self, owner, attr, name):
        original = getattr(owner, attr)

        @functools.wraps(original)
        def counted(*args, **kwargs):
            self.counts[name] += 1
            return original(*args, **kwargs)

        setattr(owner, attr, counted)
        self._restore.append((owner, attr, original))

    def install(self):
        self._wrap(importlib, "reload", "module_reloaded")
        self._wrap(subprocess.Popen, "__init__", "subprocess_spawned")
        self._wrap(fastapi.FastAPI, "__init__", "app_built")
        self._wrap(testclient.TestClient, "__enter__", "lifespan_entered")
        self._wrap(uvicorn.Server, "startup", "uvicorn_started")

    def uninstall(self):
        while self._restore:
            owner, attr, original = self._restore.pop()
            setattr(owner, attr, original)

    def snapshot(self) -> dict:
        return dict(self.counts)


class PhaseProfile:
    """The plugin. One instance per process; only the process that receives
    the reports (serial: the one process; xdist: the controller) writes."""

    def __init__(self, config, path: str):
        self.config = config
        self.path = path
        self.is_worker = hasattr(config, "workerinput")
        self.tests: dict[str, dict] = {}
        self.collected: list[str] | None = None
        self.collection_agrees: bool | None = None
        self.ops = _Ops()
        self._t0 = time.perf_counter()
        self.collect_s: float | None = None
        # [start, seconds spent in fixtures set up from inside this one]
        self._fixture_stack: list[list[float]] = []

    # -- lifecycle ------------------------------------------------------------

    def pytest_configure(self, config):
        self.ops.install()

    def pytest_unconfigure(self, config):
        self.ops.uninstall()

    @pytest.hookimpl(wrapper=True)
    def pytest_collection(self, session):
        start = time.perf_counter()
        try:
            return (yield)
        finally:
            self.collect_s = time.perf_counter() - start

    def pytest_collection_finish(self, session):
        # Serial: this process collected what will run. Under xdist the
        # controller collects nothing; its workers report below.
        if not getattr(self.config.option, "numprocesses", None):
            self.collected = [item.nodeid for item in session.items]

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        """Each worker's collection. They must agree, and a run where they do
        not is recorded as such rather than as whichever arrived first."""
        ids = list(ids)
        if self.collected is None:
            self.collected, self.collection_agrees = ids, True
        elif ids != self.collected:
            self.collection_agrees = False

    # -- the process running the test: annotate its reports -------------------

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        frame = [time.perf_counter(), 0.0]
        self._fixture_stack.append(frame)
        try:
            return (yield)
        finally:
            self._fixture_stack.pop()
            elapsed = time.perf_counter() - frame[0]
            if self._fixture_stack:                  # charge the parent less
                self._fixture_stack[-1][1] += elapsed
            item = getattr(request, "_pyfuncitem", None)
            if item is not None:
                spent = item.__dict__.setdefault("_phase_profile_fixtures", {})
                name = fixturedef.argname
                spent[name] = spent.get(name, 0.0) + (elapsed - frame[1])

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        item._phase_profile_mark = self.ops.snapshot()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        # What this phase set up: handed over and cleared, so each phase's
        # report carries only its own.
        fixtures = item.__dict__.pop("_phase_profile_fixtures", None)
        if fixtures:
            report.phase_profile_fixtures = fixtures
        now = self.ops.snapshot()
        mark = getattr(item, "_phase_profile_mark", now)
        delta = {k: now[k] - mark.get(k, 0) for k in now if now[k] != mark.get(k, 0)}
        item._phase_profile_mark = now
        if delta:
            report.phase_profile_ops = delta
        return report

    # -- the reporting process ------------------------------------------------

    def pytest_runtest_logreport(self, report):
        rec = self.tests.setdefault(report.nodeid, {})
        phases = rec.setdefault("phases", {})
        if report.when in PHASES:
            key = f"{report.when}_s"
            rec[key] = rec.get(key, 0.0) + report.duration
            phases[report.when] = _phase_outcome(report)
        else:
            # xdist's crash report (`when="???"`): the worker died under this
            # node. Never let an earlier passed setup stand for it.
            phases["crash"] = report.outcome
        for attr, field in (("phase_profile_fixtures", "fixtures_s"),
                            ("phase_profile_ops", "ops")):
            extra = getattr(report, attr, None)
            if extra:
                mine = rec.setdefault(field, {})
                for name, value in extra.items():
                    mine[name] = mine.get(name, 0) + value
        node = getattr(report, "node", None)              # xdist: the worker
        worker = getattr(getattr(node, "gateway", None), "id", None)
        if worker:
            rec["worker"] = worker
        rec["outcome"] = summary_outcome(phases)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.is_worker:
            return
        root = Path(str(self.config.rootpath))
        workers = getattr(self.config.option, "numprocesses", None) or 0
        status = _git(root, "status", "--porcelain")
        sha = _git(root, "rev-parse", "HEAD")
        top = _git(root, "rev-parse", "--show-toplevel")
        roots = [root] + ([Path(top.strip())] if top else [])
        grimoire = sys.modules.get("grimoire")
        source = getattr(grimoire, "__file__", None)
        doc = {
            "schema_version": SCHEMA_VERSION,
            "git_sha": sha.strip() if sha else None,
            "git_dirty": None if status is None else bool(status.strip()),
            # The worktree trap: an editable install from another checkout
            # imports *its* sources and passes without seeing this tree's.
            "grimoire_from_this_checkout": None if source is None
            else Path(source).resolve().is_relative_to((root / "src").resolve()),
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "packages": _versions(),
            "machine": _machine(),
            "coverage": bool(getattr(self.config.option, "cov_source", None)),
            "coverage_core": os.environ.get("COVERAGE_CORE"),
            "args": _masked_args(self.config.invocation_params.args, roots),
            "workers": workers,
            "distribution": getattr(self.config.option, "dist", "no") if workers else "serial",
            "exitstatus": int(exitstatus),
            "wall_s": time.perf_counter() - self._t0,
            # Under xdist the controller collects nothing itself; the workers'
            # collection is not on this process's clock.
            "collect_s": None if workers else self.collect_s,
            "usage": _usage(),
            "collected": sorted(self.collected) if self.collected is not None else None,
            "collection_agrees": self.collection_agrees,
            "tests": self.tests,
        }
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps(doc))

    def pytest_terminal_summary(self, terminalreporter):
        if not self.is_worker:
            terminalreporter.write_line(
                f"phase profile: {len(self.tests)} nodes written to {self.path}")


def _phase_outcome(report) -> str:
    """One phase's outcome, with an xfail named for what it is rather than the
    `skipped`/`passed` pytest uses internally for it."""
    if hasattr(report, "wasxfail"):
        return "xfailed" if report.skipped else "xpassed"
    return report.outcome


def summary_outcome(phases: dict) -> str:
    """The one word a reader means by "how did this test go", from the
    per-phase record (which `compare` reads as well, so nothing is hidden).

    A crash beats everything. A failed call is `failed` even when teardown
    errored too -- pytest reports that pair as one failure *and* one error,
    and collapsing it to `error` would hide the failure. Otherwise a failed
    setup or teardown is `error`; a skip or xfail at setup stands; else the
    call's outcome.
    """
    if "crash" in phases:
        return "crashed"
    call = phases.get("call")
    if call == "failed":
        return "failed"
    if phases.get("setup") == "failed" or phases.get("teardown") == "failed":
        return "error"
    if call is None:
        setup = phases.get("setup")
        return setup if setup in ("skipped", "xfailed") else (setup or "unknown")
    return call


def add_option(parser) -> None:
    """The `--phase-profile` flag. Called from `pytest_addoption`."""
    parser.addoption(
        "--phase-profile", default=None, metavar="PATH",
        help="write per-node setup/call/teardown seconds, fixture seconds and "
             "operation counts to PATH as JSON (tests/phase_profile.py); off "
             "unless given")


def register(config) -> None:
    """Register the plugin when the flag was given, and do nothing otherwise.

    Called from `pytest_configure`. `pytest_configure` is a historic hook, so
    the plugin's own `pytest_configure` still runs for a plugin registered
    from inside it.
    """
    path = config.getoption("--phase-profile")
    if path:
        config.pluginmanager.register(PhaseProfile(config, path), "phase-profile")
