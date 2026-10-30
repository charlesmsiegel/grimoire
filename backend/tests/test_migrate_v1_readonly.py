"""Proof that a scan is read-only and offline, observed by a CPython audit hook.

The CLI runs in a fresh interpreter (``-B``: no bytecode writes; ``-S``: no site
hooks preloading modules) with a synthetic home. The hook records every write-mode
open, every filesystem mutation, process spawn and socket operation. A dry run
may do none of them; a run with ``--report`` may only create its exclusive temp
file and rename it onto the report.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from v1_fixture import build_library, tree_digest

SRC = Path(__file__).resolve().parents[1] / "src"

HOOKED = r"""
import json, os, sys
from pathlib import Path

ALLOW = os.environ.get("REPORT_TEMP_PREFIX")
ALLOW_DIR = os.environ.get("REPORT_DIR")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
MUTATIONS = {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime", "os.truncate",
             "os.link", "os.symlink", "os.rename", "os.chflags", "os.lchflags", "os.lchmod",
             "os.setxattr", "os.removexattr", "os.system", "os.fork", "os.forkpty", "os.kill"}
SPAWNS = ("subprocess.", "socket.", "shutil.", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile")
violations = []

def allowed(path):
    # Only the report's exclusive temp file, in the report's own directory (or by dir_fd).
    path = os.fsdecode(path)
    where = os.path.dirname(path)
    return (ALLOW is not None and os.path.basename(path).startswith(ALLOW)
            and (where == "" or os.path.realpath(where) == os.path.realpath(ALLOW_DIR)))

def hook(event, args):
    if event == "open":
        path, mode, flags = args
        write = (flags & WRITE_FLAGS) if mode is None else any(c in mode for c in "wax+")
        if write and not allowed(path):
            violations.append(f"open {path!r} {mode!r} {flags!r}")
    elif event in MUTATIONS:
        if not (event == "os.rename" and allowed(args[0])):
            violations.append(f"{event} {args[:2]!r}")
    elif event.startswith(SPAWNS):
        violations.append(event)

sys.addaudithook(hook)
from grimoire.migrate_v1.__main__ import main
code = main(sys.argv[2:], environ={}, home=Path(sys.argv[1]))
network = sorted(m for m in sys.modules
                 if m.split(".")[0] in ("socket", "_socket", "ssl", "http", "subprocess")
                 or m == "urllib.request")
print("AUDIT " + json.dumps({"code": code, "violations": violations, "network": network}), file=sys.stderr)
"""


def run_hooked(home, args, *, temp_prefix=None, report_dir=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRIMOIRE")}
    env.update(PYTHONPATH=str(SRC), HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
    if temp_prefix:
        env["REPORT_TEMP_PREFIX"], env["REPORT_DIR"] = temp_prefix, str(report_dir)
    done = subprocess.run([sys.executable, "-B", "-S", "-c", HOOKED, str(home), "scan", *args],
                          capture_output=True, text=True, env=env, timeout=120)
    line = next(l for l in done.stderr.splitlines() if l.startswith("AUDIT "))
    return json.loads(line[len("AUDIT "):])


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    return home, build_library(tmp_path / "v1")


def test_a_dry_run_writes_nothing_and_stays_offline(setup, tmp_path):
    home, library = setup
    before = tree_digest(library)
    audit = run_hooked(home, ["--source", str(library), "--archive-id", "fixture",
                              "--destination", str(tmp_path / "v2")])
    assert audit == {"code": 0, "violations": [], "network": []}
    assert tree_digest(library) == before
    assert not (tmp_path / "v2").exists()
    assert list(home.iterdir()) == []


def test_a_report_run_writes_only_its_temp_and_report(setup, tmp_path):
    home, library = setup
    before = tree_digest(library)
    report = tmp_path / "inventory.json"
    audit = run_hooked(home, ["--source", str(library), "--archive-id", "fixture",
                              "--destination", str(tmp_path / "v2"), "--report", str(report)],
                       temp_prefix=".inventory.json.", report_dir=tmp_path)
    assert audit == {"code": 0, "violations": [], "network": []}
    assert tree_digest(library) == before
    assert json.loads(report.read_text(encoding="ascii"))["status"] == "ready_for_review"
    assert not (tmp_path / "v2").exists()


def test_the_hook_does_see_writes(setup, tmp_path):
    home, library = setup
    audit = run_hooked(home, ["--source", str(library), "--archive-id", "fixture",
                              "--destination", str(tmp_path / "v2"), "--report", str(tmp_path / "r.json")])
    assert any(v.startswith("open") for v in audit["violations"])
    assert any(v.startswith("os.rename") for v in audit["violations"])
