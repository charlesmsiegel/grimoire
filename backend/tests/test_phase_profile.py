"""The phase profiler (`tests/phase_profile.py`) reports what a run did.

Every later step of the test-suite acceleration work is measured against the
file this plugin writes, so a profiler that silently mis-attributes a phase, a
fixture or an outcome would make every one of those comparisons wrong in a
direction nobody could see. These tests hold its contract on a synthetic suite
whose answers are known in advance.

The synthetic suite runs as a **subprocess** over files written into
`tmp_path`, never as test files in this tree: it contains a test that fails on
purpose, which in-tree would fail the gate, and this suite already runs under
the gate's own coverage, which a nested in-process session would fight over.
The child's environment has coverage's variables stripped for the same reason.
It is wired through the same `add_option`/`register` pair `conftest.py` calls,
so what passes here is what the gate runs.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import fastapi
import uvicorn
from starlette import testclient

from tests import phase_profile

BACKEND = Path(__file__).resolve().parents[1]

CONFTEST = f'''
import sys
sys.path.insert(0, {str(BACKEND)!r})
from tests import phase_profile


def pytest_addoption(parser):
    phase_profile.add_option(parser)


def pytest_configure(config):
    phase_profile.register(config)
'''

SUITE = '''
import subprocess
import sys
import time

import fastapi
import pytest

SLOW = 0.05


@pytest.fixture
def slow():
    time.sleep(SLOW)
    yield 1


@pytest.fixture
def broken():
    raise RuntimeError("setup fails")


@pytest.fixture
def bad_teardown():
    yield
    raise RuntimeError("teardown fails")


def test_pass(slow):
    assert slow == 1


def test_fail():
    assert False


@pytest.mark.skip(reason="synthetic")
def test_skip():
    pass


@pytest.mark.xfail(strict=True)
def test_xfail():
    assert False


@pytest.mark.parametrize("n", [1, 2])
def test_param(n):
    assert n


def test_setup_error(broken):
    pass


def test_teardown_error(bad_teardown):
    pass


def test_counts_operations():
    fastapi.FastAPI()
    fastapi.FastAPI()
    subprocess.run([sys.executable, "-c", "pass"], check=True)
'''

#: What every synthetic node must come out as.
OUTCOMES = {
    "test_synthetic.py::test_pass": "passed",
    "test_synthetic.py::test_fail": "failed",
    "test_synthetic.py::test_skip": "skipped",
    "test_synthetic.py::test_xfail": "xfailed",
    "test_synthetic.py::test_param[1]": "passed",
    "test_synthetic.py::test_param[2]": "passed",
    "test_synthetic.py::test_setup_error": "error",
    "test_synthetic.py::test_teardown_error": "error",
    "test_synthetic.py::test_counts_operations": "passed",
}


def _suite(tmp_path: Path) -> Path:
    root = tmp_path / "suite"
    root.mkdir()
    # Its own ini, so the child never walks up into a config it was not given.
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (root / "test_synthetic.py").write_text(SUITE, encoding="utf-8")
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("COV_", "COVERAGE"))}
    # Belt and braces: nothing here touches a store, but a child of the suite
    # never gets to resolve the developer's real one either.
    env["GRIMOIRE_HOME"] = str(root / "home")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=120, check=False)


def _profiled(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path, dict]:
    root = _suite(tmp_path)
    out = root / "profile.json"
    proc = _run(root, f"--phase-profile={out}")
    assert out.exists(), proc.stdout + proc.stderr
    return proc, out, json.loads(out.read_text(encoding="utf-8"))


def test_one_record_per_node_with_the_outcome_a_reader_means(tmp_path):
    proc, _, doc = _profiled(tmp_path)
    assert proc.returncode == 1, proc.stdout       # the synthetic failures fail it
    assert {nid: rec["outcome"] for nid, rec in doc["tests"].items()} == OUTCOMES
    assert doc["exitstatus"] == 1


def test_the_header_names_the_run(tmp_path):
    _, _, doc = _profiled(tmp_path)
    assert doc["schema_version"] == phase_profile.SCHEMA_VERSION
    assert doc["python"].count(".") == 2
    assert doc["coverage"] is False
    assert (doc["workers"], doc["distribution"]) == (0, "serial")
    assert doc["wall_s"] > 0 and doc["collect_s"] > 0
    # No checkout around the synthetic suite, so no SHA -- rather than a wrong one.
    assert doc["git_sha"] is None


def test_every_phase_that_ran_is_timed(tmp_path):
    _, _, doc = _profiled(tmp_path)
    passed = doc["tests"]["test_synthetic.py::test_pass"]
    assert {"setup_s", "call_s", "teardown_s"} <= passed.keys()
    # A test skipped by a mark never reaches its call.
    assert "call_s" not in doc["tests"]["test_synthetic.py::test_skip"]
    # Nor does one whose fixture raised.
    assert "call_s" not in doc["tests"]["test_synthetic.py::test_setup_error"]


def test_fixture_seconds_land_on_the_fixture_that_spent_them(tmp_path):
    _, _, doc = _profiled(tmp_path)
    rec = doc["tests"]["test_synthetic.py::test_pass"]
    assert rec["fixtures_s"]["slow"] >= 0.05
    assert rec["setup_s"] >= rec["fixtures_s"]["slow"]
    # Exclusive: pytest's own built-ins this test did not ask for are absent,
    # and nothing else is charged the sleep.
    others = {k: v for k, v in rec["fixtures_s"].items() if k != "slow"}
    assert all(v < 0.05 for v in others.values()), others


def test_operations_are_counted_per_test(tmp_path):
    _, _, doc = _profiled(tmp_path)
    ops = doc["tests"]["test_synthetic.py::test_counts_operations"]["ops"]
    assert ops["app_built"] == 2
    assert ops["subprocess_spawned"] == 1
    # A test that did none of it carries no counters at all.
    assert "ops" not in doc["tests"]["test_synthetic.py::test_param[1]"]


def test_the_file_is_canonical(tmp_path):
    """Sorted keys and fixed rounding: re-serialising what was read gives the
    same bytes, so two profiles diff by content and never by key order."""
    _, out, doc = _profiled(tmp_path)
    assert phase_profile.dumps(doc) == out.read_text(encoding="utf-8")


def test_no_absolute_path_reaches_the_file(tmp_path):
    """Node IDs are relative; nothing in the file names where the run lived."""
    _, out, _ = _profiled(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert str(BACKEND) not in text
    assert str(Path.home()) not in text


def test_without_the_flag_nothing_is_written_or_said(tmp_path):
    root = _suite(tmp_path)
    proc = _run(root)
    assert proc.returncode == 1
    assert "phase profile" not in proc.stdout + proc.stderr
    written = {p.name for p in root.iterdir()} - {"__pycache__"}
    assert written == {"conftest.py", "pytest.ini", "test_synthetic.py"}


def test_register_does_nothing_without_the_flag():
    class _Manager:
        def register(self, *a, **k):
            raise AssertionError("registered without --phase-profile")

    class _Config:
        pluginmanager = _Manager()

        @staticmethod
        def getoption(name):
            return {"--phase-profile": None}[name]

    phase_profile.register(_Config())


def test_the_counters_put_back_exactly_what_they_wrapped():
    """A profiled run leaves the wrapped classes as it found them."""
    def targets():
        return (importlib.reload, subprocess.Popen.__init__, fastapi.FastAPI.__init__,
                testclient.TestClient.__enter__, uvicorn.Server.startup)

    before = targets()
    ops = phase_profile._Ops()
    ops.install()
    try:
        assert all(a is not b for a, b in zip(targets(), before, strict=True))
    finally:
        ops.uninstall()
    assert all(a is b for a, b in zip(targets(), before, strict=True))
