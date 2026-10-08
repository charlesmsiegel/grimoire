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
import subprocess
from pathlib import Path

import fastapi
import uvicorn
from starlette import testclient

from tests import phase_profile, synthetic_suite

BACKEND = synthetic_suite.BACKEND

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
def inner():
    time.sleep(SLOW)
    return 2


@pytest.fixture
def outer(request):
    time.sleep(SLOW)
    return request.getfixturevalue("inner") + 1


@pytest.fixture
async def async_value():
    time.sleep(SLOW)
    return 3


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


def test_nested(outer):
    assert outer == 3


def test_lazy(request):
    assert request.getfixturevalue("inner") == 2


async def test_async(async_value):
    assert async_value == 3


def test_fail_then_teardown_error(bad_teardown):
    assert False


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
    "test_synthetic.py::test_nested": "passed",
    "test_synthetic.py::test_lazy": "passed",
    "test_synthetic.py::test_async": "passed",
    # pytest reports this pair as one failure *and* one error; the failure
    # must not be hidden behind the error.
    "test_synthetic.py::test_fail_then_teardown_error": "failed",
}


def _suite(tmp_path: Path) -> Path:
    return synthetic_suite.write(tmp_path / "suite", {"test_synthetic.py": SUITE})


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return synthetic_suite.run(root, *args)


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
    # No checkout around the synthetic suite, so no SHA and no dirty flag --
    # rather than a wrong one.
    assert doc["git_sha"] is None and doc["git_dirty"] is None
    assert doc["packages"]["pytest"] and doc["packages"]["coverage"]
    assert doc["machine"]["cpu_count"] >= 1
    assert doc["usage"]["cpu_user_s"] > 0
    assert doc["args"][-1].startswith("--phase-profile=")


def test_the_collected_manifest_is_recorded_apart_from_the_reports(tmp_path):
    """Collection, not reports: under `-x` the run stops at the first failure,
    and the nodes it never reached are still in the manifest."""
    root = _suite(tmp_path)
    out = root / "profile.json"
    _run(root, f"--phase-profile={out}", "-x")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["collected"] == sorted(OUTCOMES)
    assert set(doc["tests"]) < set(doc["collected"])


def test_a_failed_call_survives_a_teardown_error(tmp_path):
    _, _, doc = _profiled(tmp_path)
    rec = doc["tests"]["test_synthetic.py::test_fail_then_teardown_error"]
    assert rec["phases"] == {"setup": "passed", "call": "failed", "teardown": "failed"}


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
    # Nothing else is charged the sleep: what every fixture claims adds up to
    # no more than the phase took. Relational on purpose -- an upper bound in
    # seconds is a coin flip on a loaded machine (#351, #314).
    assert sum(rec["fixtures_s"].values()) <= rec["setup_s"] + 1e-6


def test_a_fixture_fetched_from_inside_another_is_not_charged_twice(tmp_path):
    """`outer` sleeps once and fetches `inner`, which sleeps once: each owns one
    sleep, and together they do not claim more than setup took. Charging
    `outer` inclusively would claim `inner`'s sleep twice, and the sum would
    exceed the phase by exactly that -- however loaded the machine is."""
    _, _, doc = _profiled(tmp_path)
    rec = doc["tests"]["test_synthetic.py::test_nested"]
    spent = rec["fixtures_s"]
    assert spent["outer"] >= 0.05 and spent["inner"] >= 0.05, spent
    assert sum(spent.values()) <= rec["setup_s"] + 1e-6


def test_a_fixture_fetched_during_the_call_is_charged_to_it(tmp_path):
    _, _, doc = _profiled(tmp_path)
    assert doc["tests"]["test_synthetic.py::test_lazy"]["fixtures_s"]["inner"] >= 0.05


def test_an_async_fixture_owns_its_time_and_not_its_runner(tmp_path):
    """pytest-asyncio's wrapper fetches its event-loop runner from inside the
    fixture's setup (a private fixture whose name varies by release); the
    runner's start-up is its own, not the fixture's, and nothing is charged
    twice."""
    _, _, doc = _profiled(tmp_path)
    rec = doc["tests"]["test_synthetic.py::test_async"]
    spent = rec["fixtures_s"]
    assert spent["async_value"] >= 0.05, spent
    assert sum(spent.values()) <= rec["setup_s"] + 1e-6


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
