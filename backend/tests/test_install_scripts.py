"""The two installers and launchers agree with the executable project inputs.

Four fresh-clone failures (#207), each of which the repo already had the right
answer for somewhere else:

- a **version floor** stated in an install script but nowhere checked, so too
  old a python or node got past `venv`/PATH detection and failed minutes later
  inside `pip install`, naming a wheel instead of the cause;
- **no word of where the library lands** -- the store is created lazily by the
  first API call, so an installer that says nothing leaves the path (and the
  chance to repoint it) undiscoverable until after first run;
- **`install.sh` committed non-executable**, so the one line the README tells a
  fresh clone to run answered with `Permission denied`.

None of that is reachable by running the scripts here: one is bash, one is
PowerShell, both mutate the developer's home directory. So these read the
scripts as text and check the contract they have to hold up -- which is exactly
the level of the script failures. Documentation examples are maintained as
prose; the tests do not parse them as configuration or scenario data.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
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
@pytest.mark.parametrize("name", ["install.sh", "run.sh", "shutdown.sh", "macos-launcher.sh"])
def test_the_unix_scripts_are_executable(name):
    """The README invokes all three by path, so the mode git records has to
    allow it. `install.sh` chmods the directory, but that only ever repaired
    the other two after the fact -- and could never repair itself, since the
    chmod is a line *inside* the script that would not start."""
    script = UNIX.with_name(name)
    assert script.stat().st_mode & stat.S_IXUSR, (
        f"scripts/unix/{name} is not executable; running it as the README "
        "documents fails with Permission denied")


# --- launchers ---------------------------------------------------------------
#
# The run scripts serve the production bundle from the backend by default and
# keep the two-server Vite setup behind `--dev` / `-Dev`. That put three facts
# in places that each hold a copy: the bundle the installer builds, the list of
# files whose change makes that bundle stale (one copy per platform), and the
# ports -- two scripts, the README, and the dev proxy in `vite.config.ts`. Each
# copy drifts on its own and silently: a stale-input list that lost an entry
# keeps serving an old UI after `git pull`, and a port the README no longer
# agrees with sends a fresh clone to a dead address.

RUN_UNIX = UNIX.with_name("run.sh")
RUN_WINDOWS = WINDOWS.with_name("run.ps1")
LAUNCHERS = pytest.mark.parametrize(
    "script", [RUN_UNIX, RUN_WINDOWS], ids=["unix", "windows"])
FRONTEND = REPO / "frontend"

#: `vite build` through the platform's `node_modules/.bin` shim -- the one
#: spelling that runs the locked vite on both, with no npm/npx wrapper that
#: could fetch one or (under PowerShell's `npm.ps1`) eat an argument.
VITE_BUILD = r"node_modules[/\\]\.bin[/\\]vite(\.cmd)?\"?\s+build\b"


def _first_command_matching(script: Path, pattern: str) -> int:
    """`_step`, for a command recognised by shape rather than a fixed substring."""
    for number, line in enumerate(_text(script).splitlines()):
        if re.search(pattern, line) and not line.lstrip().startswith("#"):
            return number
    raise AssertionError(f"{script.name} no longer runs a command matching {pattern!r}")


@INSTALLERS
def test_installer_builds_the_bundle_after_installing_what_it_needs(script):
    """The default launch serves `frontend/dist`, so an install that never built
    it opens on a page the backend has nothing to answer with -- and the
    installer's terminal is the one place a build failure is sure to be seen
    (the Linux desktop entry launches without one)."""
    assert _step(script, "npm install") < _first_command_matching(script, VITE_BUILD)


@LAUNCHERS
def test_launcher_can_rebuild_the_bundle(script):
    """The first launch after an update has to rebuild what moved; otherwise the
    default mode keeps serving whatever the installer built."""
    _first_command_matching(script, VITE_BUILD)


def _bundle_inputs(script: Path) -> list[str]:
    text = _text(script)
    found = (re.search(r"^BUNDLE_INPUTS=\(([^)]*)\)", text, re.MULTILINE)
             or re.search(r"^\$BundleInputs\s*=\s*@\(([^)]*)\)", text, re.MULTILINE))
    assert found, f"{script.name} no longer declares the inputs its bundle is rebuilt from"
    return re.findall(r"[\w.-]+", found.group(1))


def test_both_launchers_watch_the_same_bundle_inputs():
    """One list per platform is two copies of one decision."""
    assert _bundle_inputs(RUN_UNIX) == _bundle_inputs(RUN_WINDOWS)


def test_every_watched_bundle_input_exists():
    """Both scripts skip an input that is not on disk -- they must, or a checkout
    without an optional file could not launch -- which means a renamed one
    (`vite.config.ts` becoming `.mts`) would drop out of the staleness check
    without a sound. So its presence is asserted here instead."""
    missing = [n for n in _bundle_inputs(RUN_UNIX) if not (FRONTEND / n).exists()]
    assert not missing, f"the launchers watch frontend/ inputs that do not exist: {missing}"


def _port(script: Path, unix_name: str, windows_name: str) -> str:
    return _declared(script, rf"(?m)(?:^{unix_name}|^\${windows_name})\s*=\s*(\d+)\s*$")


def test_the_launcher_ports_agree_with_the_dev_proxy():
    """The dev proxy must reach the backend the two launchers actually start."""
    backend = {_port(s, "BACKEND_PORT", "BackendPort") for s in (RUN_UNIX, RUN_WINDOWS)}
    vite = {_port(s, "VITE_PORT", "VitePort") for s in (RUN_UNIX, RUN_WINDOWS)}
    assert len(backend) == 1 and len(vite) == 1, (
        f"run.sh and run.ps1 disagree on their ports: backend {backend}, vite {vite}")
    (backend_port,) = backend

    proxy = _text(FRONTEND / "vite.config.ts")
    assert f"http://127.0.0.1:{backend_port}" in proxy, (
        "vite.config.ts proxies /api somewhere other than the backend --dev starts")


# Not on Windows even with a bash on PATH: that is as likely to be WSL's, which
# cannot open the Windows path it would be handed.
@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="no POSIX bash to parse with")
@pytest.mark.parametrize("name", ["install.sh", "run.sh", "shutdown.sh", "macos-launcher.sh"])
def test_the_unix_scripts_parse(name):
    """Nothing in the gate runs these, so a syntax error in a branch its author
    did not exercise (`--dev`, the build-failed fallback) would first be met by
    whoever double-clicks the launcher. `bash -n` reads without running."""
    result = subprocess.run(["bash", "-n", str(UNIX.with_name(name))],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="no POSIX bash to run it with")
def test_a_failed_rebuild_is_announced_in_the_browser(tmp_path):
    """A failed rebuild after an update serves the previous build against the
    updated backend -- an old UI talking to a newer API. Its one warning went
    to stderr, which the Linux desktop entry (Terminal=false) sends nowhere,
    so the player met a UI that broke oddly with nothing saying why. The
    branch is run for real here, with the build, the backend and the browser
    stubbed, since nothing else in the gate reaches it."""
    port = int(_port(RUN_UNIX, "BACKEND_PORT", "BackendPort"))
    if not _port_is_free(port):
        pytest.skip(f"port {port} is in use, and the launcher insists on it")
    root = tmp_path / "grimoire"
    run = root / "scripts" / "unix" / "run.sh"
    run.parent.mkdir(parents=True)
    shutil.copy2(RUN_UNIX, run)
    built = root / "frontend" / "dist" / "index.html"
    built.parent.mkdir(parents=True)
    built.write_text("the previous build", encoding="utf-8")
    hour_ago = time.time() - 3600
    os.utime(built, (hour_ago, hour_ago))
    (root / "frontend" / "src").mkdir()
    (root / "frontend" / "src" / "main.tsx").write_text("what the update brought", encoding="utf-8")
    _executable(root / "frontend" / "node_modules" / ".bin" / "vite",
                "#!/bin/sh\necho 'Could not resolve \"left-pad\"' >&2\nexit 1\n")
    # The backend: listens where the launcher waits for it, then exits, which
    # ends the launcher's `wait`.
    _executable(root / "backend" / ".venv" / "bin" / "python",
                f"#!/bin/sh\nexec {sys.executable} -c \"import socket, time; "
                f"s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(2)\"\n")
    opened = tmp_path / "opened"
    for opener in ("open", "xdg-open"):
        _executable(tmp_path / "bin" / opener, f'#!/bin/sh\necho "$1" >> "{opened}"\n')
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(["bash", str(run)], env=env, capture_output=True, text=True,
                          timeout=120, check=False)
    notice = root / ".run" / "ui-build-failed.html"
    assert "serving the previous build" in done.stderr, done.stdout + done.stderr
    assert opened.read_text(encoding="utf-8").splitlines() == [
        f"http://127.0.0.1:{port}", str(notice)], done.stdout + done.stderr
    page = notice.read_text(encoding="utf-8")
    assert "scripts/unix/install.sh" in page and ".run/ui-build.log" in page
    assert "left-pad" in (root / ".run" / "ui-build.log").read_text(encoding="utf-8")


@LAUNCHERS
def test_both_launchers_open_a_notice_when_serving_a_stale_build(script):
    """The Windows half of the test above, by shape: nothing here can run
    PowerShell, and the two launchers are one decision in two files."""
    text = _text(script)
    assert "ui-build-failed.html" in text, f"{script.name} no longer writes the notice"
    assert re.search(r"(open_page|Start-Process) \"?\$(STALE_NOTICE|StaleNotice)", text), (
        f"{script.name} writes the notice but never opens it")


# --- macOS launcher ----------------------------------------------------------
#
# macOS draws an icon for an application bundle and for nothing else: a bare
# `.command` file gets the generic script icon whatever sits beside it. So the
# installer wraps the launcher in `Grimoire.app`, and the icon is built with the
# platform's own iconset tools (`sips`, `iconutil`), which exist only on a Mac.
# Here both are stubbed, so the bundle's layout and the iconset's contents are
# checked for real on any Unix. Whether the icon renders is a check for a Mac.

MACOS_LAUNCHER = UNIX.with_name("macos-launcher.sh")
SOURCE_ICON = FRONTEND / "public" / "grimoire-512.png"
ICONSET_NAMES = {
    "icon_16x16.png", "icon_16x16@2x.png",
    "icon_32x32.png", "icon_32x32@2x.png",
    "icon_128x128.png", "icon_128x128@2x.png",
    "icon_256x256.png", "icon_256x256@2x.png",
    "icon_512x512.png",
}


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="no POSIX bash to run it with")
def test_macos_launcher_builds_an_app_bundle_with_an_icon(tmp_path):
    root = tmp_path / "grimoire"
    (root / "frontend" / "public").mkdir(parents=True)
    shutil.copy2(SOURCE_ICON, root / "frontend" / "public" / "grimoire-512.png")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    # What an earlier install left behind: a bare launcher, and a bundle whose
    # contents a rebuild must not keep.
    (desktop / "Grimoire.command").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    stale = desktop / "Grimoire.app" / "Contents" / "Resources" / "old.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("from the last install", encoding="utf-8")

    sizes = tmp_path / "sizes.txt"
    listing = tmp_path / "iconset.txt"
    # sips is told a height (-z H W); each output records the pixel size it was
    # asked for, so the iconset's sizes can be checked and not just its names.
    _executable(tmp_path / "bin" / "sips",
                f'#!/bin/sh\necho "$6 $2" >> "{sizes}"\ncp "$4" "$6"\n')
    _executable(tmp_path / "bin" / "iconutil",
                f'#!/bin/sh\nls "$3" > "{listing}"\nprintf icns > "$5"\n')
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(["bash", str(MACOS_LAUNCHER), str(root), str(desktop)],
                          env=env, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stdout + done.stderr

    assert not (desktop / "Grimoire.command").exists(), "the bare launcher survived"
    assert not stale.exists(), "a rebuild kept the previous bundle's contents"
    app = desktop / "Grimoire.app" / "Contents"

    info = plistlib.loads((app / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Grimoire"
    assert info["CFBundleIconFile"] == "AppIcon"
    assert info["CFBundlePackageType"] == "APPL"

    assert (app / "Resources" / "AppIcon.icns").read_bytes() == b"icns"
    names = set(listing.read_text(encoding="utf-8").split())
    assert names >= ICONSET_NAMES, f"iconset is missing {ICONSET_NAMES - names}"
    assert "icon_512x512@2x.png" not in names, "would need an upscaled 1024px source"
    for line in sizes.read_text(encoding="utf-8").splitlines():
        output, pixels = line.rsplit(" ", 1)
        match = re.fullmatch(r"icon_(\d+)x\d+(@2x)?\.png", Path(output).name)
        assert match, f"unexpected iconset entry {output}"
        expected = int(match.group(1)) * (2 if match.group(2) else 1)
        assert int(pixels) == expected, f"{Path(output).name} is {pixels}px, not {expected}px"

    executable = app / "MacOS" / "Grimoire"
    assert executable.stat().st_mode & stat.S_IXUSR, "the bundle's executable is not runnable"
    assert "open -a Terminal" in executable.read_text(encoding="utf-8")
    launcher = app / "Resources" / "Grimoire.command"
    assert launcher.stat().st_mode & stat.S_IXUSR, "the .command inside the bundle is not runnable"
    assert str(root / "scripts" / "unix" / "run.sh") in launcher.read_text(encoding="utf-8"), (
        "the bundle's launcher does not run run.sh")
    for script in (executable, launcher):
        assert subprocess.run(["bash", "-n", str(script)], capture_output=True, check=False).returncode == 0


def test_installer_hands_the_desktop_launcher_to_the_macos_script():
    """install.sh decides the platform; the bundle is built by the script it
    names, so the two cannot drift into two copies of the icon steps."""
    _first_command_matching(UNIX, r"macos-launcher\.sh")
    assert "Grimoire.command" not in _text(UNIX), "install.sh still writes the bare launcher"
