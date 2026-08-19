"""The two installers and the docs that stand in for them agree with the repo.

Four fresh-clone failures (#207), each of which the repo already had the right
answer for somewhere else:

- a **version floor** stated in an install script but nowhere checked, so too
  old a python or node got past `venv`/PATH detection and failed minutes later
  inside `pip install`, naming a wheel instead of the cause;
- **no word of where the library lands** -- the store is created lazily by the
  first API call, so an installer that says nothing leaves the path (and the
  chance to repoint it) undiscoverable until after first run;
- **a Windows-only interpreter path given as *the* command** in `CLAUDE.md`,
  which is simply wrong on a Unix clone;
- **`install.sh` committed non-executable**, so the one line the README tells a
  fresh clone to run answered with `Permission denied`.

None of that is reachable by running the scripts here: one is bash, one is
PowerShell, both mutate the developer's home directory. So these read the
scripts as text and check the contract they have to hold up -- which is exactly
the level the bugs lived at, since every one of them was a script or a document
saying something the rest of the repo already contradicted.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UNIX = REPO / "scripts" / "unix" / "install.sh"
WINDOWS = REPO / "scripts" / "windows" / "install.ps1"
INSTALLERS = pytest.mark.parametrize(
    "script", [UNIX, WINDOWS], ids=["unix", "windows"])


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _step(script: Path, needle: str) -> int:
    """Line number of the first *command* containing `needle`.

    Comments are skipped: both scripts explain in prose why the floors are
    probed before `-m venv` runs, and an ordering check that matched the
    explanation rather than the command would pass however the commands were
    ordered.
    """
    for number, line in enumerate(_text(script).splitlines()):
        if needle in line and not line.lstrip().startswith("#"):
            return number
    raise AssertionError(f"{script.name} no longer runs a command with {needle!r}")


def _declared(script: Path, pattern: str) -> str:
    found = re.search(pattern, _text(script))
    assert found, f"{script.name} no longer declares a floor matching {pattern}"
    return found.group(1)


def _python_floor(script: Path) -> str:
    return _declared(script, r'(?:PY_MIN|\$PyMin)\s*=\s*"([\d.]+)"')


def _node_floor(script: Path) -> str:
    return _declared(script, r'(?:NODE_MIN|\$NodeMin)\s*=\s*"(\d+)"')


def test_python_floor_matches_requires_python():
    """A floor the installer invents is a floor that drifts off `pyproject`."""
    pyproject = _text(REPO / "backend" / "pyproject.toml")
    required = re.search(r'requires-python\s*=\s*">=([\d.]+)"', pyproject)
    assert required, "backend/pyproject.toml no longer pins requires-python"
    for script in (UNIX, WINDOWS):
        assert _python_floor(script) == required.group(1), (
            f"{script.name} checks a different Python than pyproject requires")


def test_node_floor_matches_package_engines():
    package = json.loads(_text(REPO / "frontend" / "package.json"))
    engines = package.get("engines", {}).get("node", "")
    required = re.match(r">=(\d+)", engines)
    assert required, "frontend/package.json no longer declares engines.node"
    for script in (UNIX, WINDOWS):
        assert _node_floor(script) == required.group(1), (
            f"{script.name} checks a different Node than package.json requires")


@INSTALLERS
def test_each_floor_is_probed_and_probed_against_what_was_declared(script):
    """Presence on PATH is not the check -- the running version is, and against
    the floor the tests above pin.

    Both halves matter. Without the probe the floor is a comment; without the
    interpolation the pinning is decorative, since `PY_MIN="3.11"` sitting
    beside a probe that hard-codes `(3, 9)` satisfies every other test in this
    file while checking the wrong number -- the same two-copies-of-one-value
    drift the floors were centralised to end. `_step` raises, naming the
    script, when no command carries the probe at all.
    """
    lines = _text(script).splitlines()
    python_probe = lines[_step(script, "sys.version_info")]
    node_probe = lines[_step(script, "process.versions.node")]
    assert re.search(r"\$\{?(PY_MIN|PyMin)\}?", python_probe), (
        f"{script.name} probes a Python version it did not declare:\n  {python_probe.strip()}")
    assert re.search(r"\$\{?(NODE_MIN|NodeMin)\}?", node_probe), (
        f"{script.name} probes a Node version it did not declare:\n  {node_probe.strip()}")


@INSTALLERS
def test_floors_are_probed_before_the_venv_is_built(script):
    """Otherwise the report arrives minutes later, as a pip error about a wheel."""
    assert _step(script, "sys.version_info") < _step(script, "-m venv")
    assert _step(script, "process.versions.node") < _step(script, "npm install")


@INSTALLERS
def test_installer_reports_the_store_location(script):
    """And by asking the resolver, so an already-repointed store reads true."""
    text = _text(script)
    assert "-m grimoire.where" in text, (
        f"{script.name} finishes without saying where the library will live")
    assert not re.search(r"(echo|Write-Host)[^\n]*\.grimoire", text), (
        f"{script.name} prints a store path of its own; only the resolver knows it")


@INSTALLERS
def test_store_location_is_reported_after_the_venv_exists(script):
    """`grimoire.where` runs out of the venv the lines above it create."""
    assert _step(script, "-m venv") < _step(script, "-m grimoire.where")


@pytest.mark.skipif(os.name == "nt", reason="no execute bit on a Windows checkout")
@pytest.mark.parametrize("name", ["install.sh", "run.sh", "shutdown.sh"])
def test_the_unix_scripts_are_executable(name):
    """The README invokes all three by path, so the mode git records has to
    allow it. `install.sh` chmods the directory, but that only ever repaired
    the other two after the fact -- and could never repair itself, since the
    chmod is a line *inside* the script that would not start."""
    script = UNIX.with_name(name)
    assert script.stat().st_mode & stat.S_IXUSR, (
        f"scripts/unix/{name} is not executable; running it as the README "
        "documents fails with Permission denied")


def test_the_readme_requirements_match_the_floors():
    """A third hand-maintained copy of the same two numbers, in the file a
    human reads before running anything. It drifts the same way the scripts
    would have, and is wrong in the same way -- so it is pinned the same way."""
    readme = _text(REPO / "README.md")
    python_stated = re.search(r"\*\*Python ([\d.]+)\+\*\*", readme)
    node_stated = re.search(r"\*\*Node (\d+)\+\*\*", readme)
    assert python_stated and node_stated, "README no longer states its Requirements"
    assert python_stated.group(1) == _python_floor(UNIX)
    assert node_stated.group(1) == _node_floor(UNIX)


# --- docs ------------------------------------------------------------------
#
# A venv interpreter is `bin/python` on macOS/Linux and `Scripts/python.exe` on
# Windows. `CLAUDE.md` gave only the second, as *the* way to run the tests, and
# it is the first file an agent reads on a fresh clone. Naming one form is only
# ever half an instruction, so both have to appear together -- within a few
# lines, since these documents are long and a matching form ten sections away
# helps nobody reading the command in front of them.

DOCS = pytest.mark.parametrize("doc", [
    REPO / "CLAUDE.md",
    REPO / "README.md",
    # The third one, and the reason the rule is a test rather than a fix:
    # CLAUDE.md's sweep note links here, and this file had the same Unix-only
    # command. Two spots got found by reading the issue; this one only by
    # grepping for the shape.
    Path(__file__).parent / "fixtures" / "frozen_campaign" / "README.md",
], ids=["claude-md", "readme", "frozen-campaign-readme"])
UNIX_PY = re.compile(r"\.venv/bin/python")
WINDOWS_PY = re.compile(r"\.venv[/\\]Scripts[/\\]python")
NEARBY = 4      # lines either side: the same command, bullet, or code block


@DOCS
def test_venv_paths_are_given_for_both_platforms(doc):
    lines = _text(doc).splitlines()
    for number, line in enumerate(lines):
        window = "\n".join(lines[max(0, number - NEARBY):number + NEARBY + 1])
        if UNIX_PY.search(line):
            assert WINDOWS_PY.search(window), (
                f"{doc.name}:{number + 1} gives a Unix-only venv python:\n  {line.strip()}")
        if WINDOWS_PY.search(line):
            assert UNIX_PY.search(window), (
                f"{doc.name}:{number + 1} gives a Windows-only venv python:\n  {line.strip()}")
