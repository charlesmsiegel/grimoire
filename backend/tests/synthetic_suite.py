"""A throwaway pytest suite, written into `tmp_path` and run as a subprocess.

The harness self-tests (`test_phase_profile.py`, `test_parallel_harness.py`)
need suites whose answers are known in advance -- a test that fails on purpose,
a worker that dies, two tests that cover disjoint branches -- and none of that
may live in this tree, where the gate would collect it. So each self-test
writes its suite here and runs it in a child `pytest`:

- with its own `pytest.ini`, so the child never walks up into a config it was
  not given;
- with a `conftest.py` that wires `tests/phase_profile.py` through the same
  `add_option`/`register` pair the real `conftest.py` calls, so what passes
  here is what the gate runs;
- with coverage's environment stripped, because the parent may itself be
  running under the gate's coverage and a child must not report into it;
- with `GRIMOIRE_HOME` pointed inside the suite: nothing here touches a store,
  but no child of the suite gets to resolve the developer's real one either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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

INI = "[pytest]\nasyncio_mode = auto\n"


def write(root: Path, files: dict[str, str]) -> Path:
    """`root` holding the ini, the profiler-wiring conftest and `files`."""
    root.mkdir(parents=True)
    (root / "pytest.ini").write_text(INI, encoding="utf-8")
    (root / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def child_env(**extra: str) -> dict[str, str]:
    """This process's environment less coverage's variables, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COV_", "COVERAGE"))}
    env.update(extra)
    return env


def run(root: Path, *args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    """`pytest -q -p no:cacheprovider *args` in `root`, as a child process."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=root, env=child_env(GRIMOIRE_HOME=str(root / "home")),
        capture_output=True, text=True, timeout=timeout, check=False)
