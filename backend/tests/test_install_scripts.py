"""The two installers and the docs that stand in for them agree with the repo.

Three fresh-clone failures (#207), each of which the repo already had the right
answer for somewhere else:

- a **version floor** stated in an install script but nowhere checked, so too
  old a python or node got past `venv`/PATH detection and failed minutes later
  inside `pip install`, naming a wheel instead of the cause;
- **no word of where the library lands** -- the store is created lazily by the
  first API call, so an installer that says nothing leaves the path (and the
  chance to repoint it) undiscoverable until after first run;
- **a Windows-only interpreter path given as *the* command** in `CLAUDE.md`,
  which is simply wrong on a Unix clone.

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


def test_the_two_installers_state_the_same_floors():
    """The platforms are meant to be symmetric; a floor is easy to bump singly."""
    assert _python_floor(UNIX) == _python_floor(WINDOWS)
    assert _node_floor(UNIX) == _node_floor(WINDOWS)


@INSTALLERS
def test_floors_are_probed_not_merely_stated(script):
    """Presence on PATH is not the check -- the running version is."""
    # `_step` raises, naming the script, when no command carries the probe --
    # a floor stated in a variable and never compared against is the bug.
    _step(script, "sys.version_info")
    _step(script, "process.versions.node")


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
    assert not re.search(r'echo[^\n]*~[/\\]\.grimoire|Write-Host[^\n]*~[\\/]\.grimoire',
                         text), f"{script.name} hard-codes a store path it cannot know"


@INSTALLERS
def test_store_location_is_reported_after_the_venv_exists(script):
    """`grimoire.where` runs out of the venv the lines above it create."""
    assert _step(script, "-m venv") < _step(script, "-m grimoire.where")


@pytest.mark.skipif(os.name == "nt", reason="no execute bit on a Windows checkout")
def test_the_unix_installer_is_executable():
    """README tells a fresh clone to run it directly, so the mode git records
    has to allow that. `install.sh` chmods the *other* unix scripts, which is
    why only this one can be stranded: it cannot fix itself before it runs."""
    assert UNIX.stat().st_mode & stat.S_IXUSR, (
        "scripts/unix/install.sh is not executable; `scripts/unix/install.sh` "
        "as documented in the README fails with Permission denied")


# --- docs ------------------------------------------------------------------
#
# A venv interpreter is `bin/python` on macOS/Linux and `Scripts/python.exe` on
# Windows. `CLAUDE.md` gave only the second, as *the* way to run the tests, and
# it is the first file an agent reads on a fresh clone. Naming one form is only
# ever half an instruction, so both have to appear together -- within a few
# lines, since these documents are long and a matching form ten sections away
# helps nobody reading the command in front of them.

DOCS = pytest.mark.parametrize(
    "doc", [REPO / "CLAUDE.md", REPO / "README.md"], ids=["claude-md", "readme"])
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
