"""The parallel runner cannot turn a red suite green, or a full one partial.

Running the backend suite across pytest-xdist workers is only a speed-up if
everything a serial run would catch it still catches (spec
`docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md` §8, §12).
Each property is held here on a synthetic suite whose answer is known in
advance (`tests/synthetic_suite.py`: written into `tmp_path`, run as a child
process, coverage's environment stripped):

- a failure on one worker fails the whole run, whatever the others did;
- a worker that dies under a test fails the run, and the profile names the
  node `crashed` rather than letting an earlier passed setup stand for it;
- the workers collect exactly the node IDs a serial run collects;
- coverage combined across workers is the union of what each executed, still
  lists a module no test imported, and still fails a floor it misses;
- the privacy floor (`conftest.py`'s session `DEFAULT_HOME` and bootstrap
  pointer) holds in every worker, including after `monkeypatch.undo()`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import CoverageData

from grimoire.store import paths
from tests import synthetic_suite

REPO = synthetic_suite.BACKEND.parent
sys.path.insert(0, str(REPO / "scripts"))

import profile_report  # noqa: E402 -- the sys.path line above is what makes it importable

MIXED = '''
import pytest


@pytest.mark.parametrize("n", range(6))
def test_passes(n):
    assert n >= 0


def test_fails():
    assert False, "the one red test"
'''

CRASH = '''
import os


def test_before():
    pass


def test_dies():
    os._exit(3)


def test_after():
    pass
'''

PACKAGE = {
    "pkg/__init__.py": "",
    "pkg/branchy.py": (
        "def pick(flag):\n"
        "    if flag:\n"
        "        return 'yes'\n"
        "    return 'no'\n"),
    "pkg/never_imported.py": "VALUE = 1\n",
    "test_yes.py": "from pkg import branchy\n\n\ndef test_yes():\n"
                   "    assert branchy.pick(True) == 'yes'\n",
    "test_no.py": "from pkg import branchy\n\n\ndef test_no():\n"
                  "    assert branchy.pick(False) == 'no'\n",
}


def _profile(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_a_failure_on_one_worker_fails_the_run(tmp_path):
    root = synthetic_suite.write(tmp_path / "s", {"test_mixed.py": MIXED})
    proc = synthetic_suite.run(root, "-n", "2", "--phase-profile=p.json")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    doc = _profile(root, "p.json")
    assert (doc["workers"], doc["distribution"]) == (2, "load")
    assert doc["tests"]["test_mixed.py::test_fails"]["outcome"] == "failed"
    assert doc["exitstatus"] == 1
    # The reports came from workers, and the controller wrote them down.
    assert {rec.get("worker") for rec in doc["tests"].values()} <= {"gw0", "gw1"}
    assert all(rec.get("worker") for rec in doc["tests"].values())


def test_a_worker_that_dies_fails_the_run_and_names_the_node(tmp_path):
    root = synthetic_suite.write(tmp_path / "s", {"test_crash.py": CRASH})
    proc = synthetic_suite.run(root, "-n", "2", "--phase-profile=p.json")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    doc = _profile(root, "p.json")
    assert doc["tests"]["test_crash.py::test_dies"]["outcome"] == "crashed"
    # The rest of the suite still ran: a replacement worker took it over.
    assert doc["tests"]["test_crash.py::test_after"]["outcome"] == "passed"


def test_the_workers_collect_exactly_what_a_serial_run_collects(tmp_path):
    root = synthetic_suite.write(tmp_path / "s", {"test_mixed.py": MIXED,
                                                  "test_crash_free.py": MIXED})
    synthetic_suite.run(root, "--phase-profile=serial.json")
    synthetic_suite.run(root, "-n", "2", "--phase-profile=parallel.json")
    serial, parallel = _profile(root, "serial.json"), _profile(root, "parallel.json")
    assert parallel["collection_agrees"] is True
    assert parallel["collected"] == serial["collected"]
    assert len(serial["collected"]) == 14
    diff = profile_report.compare(serial, parallel)
    assert not profile_report.failed(diff), diff


def _coverage_run(root: Path, *args: str) -> subprocess.CompletedProcess:
    for stale in root.glob(".coverage*"):
        stale.unlink()
    return synthetic_suite.run(
        root, "--cov=pkg", "--cov-branch", "--cov-report=xml:cov.xml", *args)


def _xml_files(root: Path) -> dict[str, dict]:
    tree = ET.parse(root / "cov.xml")
    # Keyed by file name: Cobertura paths are relative to the source root.
    return {Path(c.get("filename")).name: {"line-rate": float(c.get("line-rate")),
                                "branch-rate": float(c.get("branch-rate"))}
            for c in tree.iter("class")}


def _arcs(root: Path, module: str) -> set:
    data = CoverageData(basename=str(root / ".coverage"))
    data.read()
    (path,) = [f for f in data.measured_files() if f.endswith(module)]
    return set(data.arcs(path) or ())


def test_coverage_across_workers_is_the_union_and_keeps_unimported_modules(tmp_path):
    """`test_yes` and `test_no` take opposite arms of one branch, in separate
    files so `loadfile` puts them on separate workers; only the combination
    covers the branch. A module nobody imports must still be in the report,
    at zero -- the same honesty `source = ["grimoire"]` buys the real one."""
    root = synthetic_suite.write(tmp_path / "s", PACKAGE)
    serial = _coverage_run(root)
    assert serial.returncode == 0, serial.stdout + serial.stderr
    serial_arcs = _arcs(root, "branchy.py")

    proc = _coverage_run(root, "-n", "2", "--dist=loadfile", "--phase-profile=p.json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    workers = {rec["worker"] for rec in _profile(root, "p.json")["tests"].values()}
    assert workers == {"gw0", "gw1"}, "both arms ran on one worker; the union is untested"
    assert _arcs(root, "branchy.py") == serial_arcs
    files = _xml_files(root)
    assert files["branchy.py"] == {"line-rate": 1.0, "branch-rate": 1.0}
    assert files["never_imported.py"]["line-rate"] == 0.0


def test_a_missed_coverage_floor_fails_a_parallel_run(tmp_path):
    root = synthetic_suite.write(tmp_path / "s", PACKAGE)
    met = _coverage_run(root, "-n", "2", "--cov-fail-under=50")
    missed = _coverage_run(root, "-n", "2", "--cov-fail-under=100")
    assert met.returncode == 0, met.stdout
    assert missed.returncode != 0 and "FAIL Required test coverage" in missed.stdout


# ------------------------------------------------- the privacy floor, per worker

def test_the_privacy_floor_holds_in_this_process(monkeypatch):
    """Neither the default root nor the bootstrap pointer is the developer's
    real one -- before and after an undo of this test's own patches, which is
    the case the session-scoped floor in `conftest.py` exists for."""
    real_home = Path.home() / ".grimoire"
    real_pointer = Path.home() / ".grimoire.json"

    def check():
        monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
        assert real_home != paths.DEFAULT_HOME
        assert paths.pointer_path() != real_pointer
        assert paths.home() != real_home

    check()
    monkeypatch.undo()
    check()


def test_the_privacy_floor_holds_in_every_worker(tmp_path):
    """`--dist=each` runs the test above on every worker, under the real
    `conftest.py` -- so each worker's own session fixture is what is tested."""
    env = synthetic_suite.child_env()
    env.pop("GRIMOIRE_HOME", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2",
         "--dist=each",
         "tests/test_parallel_harness.py::test_the_privacy_floor_holds_in_this_process"],
        cwd=synthetic_suite.BACKEND, env=env, capture_output=True, text=True,
        timeout=300, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout, proc.stdout
