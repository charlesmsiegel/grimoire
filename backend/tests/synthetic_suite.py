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
- with coverage's and pytest's environment stripped, because the parent may
  itself be running under the gate's coverage, or be an xdist worker, and a
  child must neither report into the one nor inherit the other's state
  (`PYTEST_XDIST_*`, `PYTEST_ADDOPTS`, `PYTEST_CURRENT_TEST`);
- with `GRIMOIRE_HOME` pointed inside the suite: nothing here touches a store,
  but no child of the suite gets to resolve the developer's real one either.
"""

from __future__ import annotations

import codecs
import io
import os
import subprocess
import sys
import tempfile
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
    """This process's environment less coverage's and pytest's variables,
    plus `extra`."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("COV_", "COVERAGE", "PYTEST_"))}
    env.update(extra)
    return env


def _capture_reader(path, flags):
    # Windows readers must share deletion with NamedTemporaryFile's writer.
    # Both handles still delete on close; a retained descendant owns the last
    # writer, rather than leaving a delete=False path for us to clean up.
    return os.open(path, flags | getattr(os, "O_TEMPORARY", 0))


def _snapshot(reader, writer):
    # A descendant may keep appending. Bound the read to the bytes present now,
    # using an independent handle so reading cannot move its write position.
    data = reader.read(os.fstat(reader.fileno()).st_size)
    # The sampled end is not EOF: a retained writer may be midway through a
    # multibyte character. Keep its complete prefix without replacing a valid
    # partial tail, while still applying its error policy to invalid bytes.
    decoder = codecs.getincrementaldecoder(writer.encoding)(errors=writer.errors)
    decoded = decoder.decode(data, final=False)
    with io.StringIO(decoded, newline=None) as text:
        return text.read()


def run(root: Path, *args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    """`pytest -q -p no:cacheprovider *args` in `root`, as a child process."""
    # A descendant can retain the output handles after this controller exits.
    # Captured pipes make communicate() wait for that writer's EOF as well as
    # the process we own. Files keep the diagnostics while letting completion
    # follow the controller's actual exit, including its real failing status.
    # Text mode keeps the same locale decoding and universal newlines as the
    # old captured strings, including the faulthandler dump on a real timeout.
    with (tempfile.NamedTemporaryFile(mode="w+") as stdout,
          tempfile.NamedTemporaryFile(mode="w+") as stderr,
          open(stdout.name, "rb", opener=_capture_reader) as out_reader,
          open(stderr.name, "rb", opener=_capture_reader) as err_reader):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                # A temp root inside a checkout must still read as no checkout
                # to the profiler's git calls, and can never use a real store.
                cwd=root, env=child_env(GRIMOIRE_HOME=str(root / "home"),
                                        GIT_CEILING_DIRECTORIES=str(root.parent)),
                stdout=stdout, stderr=stderr, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as stopped:
            stopped.stdout = _snapshot(out_reader, stdout)
            stopped.stderr = _snapshot(err_reader, stderr)
            raise
        result.stdout = _snapshot(out_reader, stdout)
        result.stderr = _snapshot(err_reader, stderr)
        return result
