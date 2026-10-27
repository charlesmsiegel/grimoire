"""Launchers run against controlled build, backend, browser and icon tools.

The macOS test checks the generated application bundle on Unix; rendering its
icon still needs a Mac. Windows skips these POSIX execution tests because a bash
on PATH may be WSL and cannot open the Windows paths passed to it.
"""

from __future__ import annotations

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
RUN_UNIX = REPO / "scripts" / "unix" / "run.sh"
FRONTEND = REPO / "frontend"
MACOS_LAUNCHER = RUN_UNIX.with_name("macos-launcher.sh")
SOURCE_ICON = FRONTEND / "public" / "grimoire-512.png"
ICONSET_NAMES = {
    "icon_16x16.png", "icon_16x16@2x.png",
    "icon_32x32.png", "icon_32x32@2x.png",
    "icon_128x128.png", "icon_128x128@2x.png",
    "icon_256x256.png", "icon_256x256@2x.png",
    "icon_512x512.png",
}


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
    port = 8173  # The production launcher's public default port.
    if not _port_is_free(port):
        pytest.skip(f"port {port} is in use, and the launcher insists on it")
    root = tmp_path / "grimoire"
    run = root / "scripts" / "unix" / "run.sh"
    run.parent.mkdir(parents=True)
    shutil.copy2(RUN_UNIX, run)
    built = root / "frontend" / "dist" / "index.html"
    built.parent.mkdir(parents=True)
    shutil.copy2(FRONTEND / "build-inputs.txt", root / "frontend" / "build-inputs.txt")
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
