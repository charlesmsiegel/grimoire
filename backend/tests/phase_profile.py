"""An opt-in, per-phase timing profile of the backend suite.

`pytest --durations` answers "which phases were slow" for one run, as text. The
acceleration work (`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`
§4.2) needs the same answer as data -- every node, every phase, which fixture's
own body the setup time went to, and how many apps, lifespans, reloads,
servers and child processes a test cost -- so that a later change can be
compared against it node by node rather than by a total that hides where the
time moved.

Wired by `conftest.py` through `add_option` and `register`, and registered only
when `--phase-profile=PATH` is given. Without the flag this module is imported
and nothing else happens: no hook registered, no counter installed, no output.
The self-tests wire a synthetic suite through the same two functions, so what
they prove is what the gate runs.

What it records, and its reach:

- **Per-phase seconds** are pytest's own `report.duration` for setup, call and
  teardown, read where reports arrive. Under xdist that is the controller,
  which is handed every worker's reports, so exactly one process writes the
  file and no two workers race for it.
- **Fixture seconds** are *exclusive*: the time inside one fixture's own body
  up to its `yield`, not the fixtures it requested (pytest has set those up
  before it calls this one). Teardown is not split by fixture. A fixture of
  wider scope is charged to the test that happened to trigger it.
- **Operation counts** come from wrapping class-level entry points, so a name
  a test imported by value is still seen: `FastAPI.__init__` (an app built --
  `create_app` and an ad-hoc `FastAPI()` alike, so a sub-app counts as one),
  `TestClient.__enter__` (a lifespan entered), `importlib.reload`,
  `uvicorn.Server.startup` and `subprocess.Popen.__init__`. Counted on the
  process that runs the test, per phase. Approximate by construction.

Both annotations ride the test's reports as plain attributes, which pytest's
report serialization copies, so they cross the xdist wire with the report.

Never recorded: a path outside the node ID, a response body, a prompt, a
fixture's value. Node IDs are repo-relative and carry no store content.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import platform
import subprocess
import sys
import time

import fastapi
import pytest
import uvicorn
from starlette import testclient

SCHEMA_VERSION = 1

#: Rounding for every duration written: microseconds. Fixed, so two writes of
#: the same data are byte-identical.
DIGITS = 6

PHASES = ("setup", "call", "teardown")

#: The operation counters, in the order they are reported.
OPS = ("app_built", "lifespan_entered", "module_reloaded", "uvicorn_started",
       "subprocess_spawned")


def _git_sha(rootdir) -> str | None:
    """The checkout's HEAD, or None where git or a checkout is absent."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rootdir,
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _round(value):
    if isinstance(value, float):
        return round(value, DIGITS)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in sorted(value.items())}
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
        self.ops = _Ops()
        self._t0 = time.perf_counter()
        self.collect_s: float | None = None

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

    # -- the process running the test: annotate its reports -------------------

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        start = time.perf_counter()
        try:
            return (yield)
        finally:
            item = getattr(request, "_pyfuncitem", None)
            if item is not None:
                spent = item.__dict__.setdefault("_phase_profile_fixtures", {})
                name = fixturedef.argname
                spent[name] = spent.get(name, 0.0) + (time.perf_counter() - start)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        item._phase_profile_mark = self.ops.snapshot()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if call.when == "setup":
            fixtures = item.__dict__.get("_phase_profile_fixtures")
            if fixtures:
                report.phase_profile_fixtures = dict(fixtures)
        now = self.ops.snapshot()
        mark = getattr(item, "_phase_profile_mark", now)
        delta = {k: now[k] - mark.get(k, 0) for k in now if now[k] != mark.get(k, 0)}
        item._phase_profile_mark = now
        if delta:
            report.phase_profile_ops = delta
        return report

    # -- the reporting process ------------------------------------------------

    def pytest_runtest_logreport(self, report):
        if report.when not in PHASES:
            return
        rec = self.tests.setdefault(report.nodeid, {})
        key = f"{report.when}_s"
        rec[key] = rec.get(key, 0.0) + report.duration
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
        rec["outcome"] = _outcome(rec.get("outcome"), report)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.is_worker:
            return
        workers = getattr(self.config.option, "numprocesses", None) or 0
        doc = {
            "schema_version": SCHEMA_VERSION,
            "git_sha": _git_sha(self.config.rootpath),
            "python": platform.python_version(),
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "coverage": bool(getattr(self.config.option, "cov_source", None)),
            "workers": workers,
            "distribution": getattr(self.config.option, "dist", "no") if workers else "serial",
            "exitstatus": int(exitstatus),
            "wall_s": time.perf_counter() - self._t0,
            # Under xdist the controller collects nothing itself; the workers'
            # collection is not on this process's clock.
            "collect_s": None if workers else self.collect_s,
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


def _outcome(previous: str | None, report) -> str:
    """The outcome a reader means by "how did this test go".

    The call's outcome, unless setup never let the call happen; an error in
    setup or teardown is `error` (pytest's own word for it), and overrides a
    passed call. An xfail is reported as `xfailed`/`xpassed`, never as the
    `skipped`/`passed` pytest uses internally for it.
    """
    if report.when == "call":
        if hasattr(report, "wasxfail"):
            return "xfailed" if report.skipped else "xpassed"
        return report.outcome
    if report.when == "setup":
        if report.failed:
            return "error"
        if report.skipped:
            return "xfailed" if hasattr(report, "wasxfail") else "skipped"
        return previous or "passed"
    # teardown
    if report.failed:
        return "error"
    return previous or report.outcome


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
