"""The opt-in 0.2.1 capture command: refusals, isolation, cleanup and output (no 0.2.1 needed).

A fake driver stands in for ``backend/tools/v021_driver.py`` and a scratch git repository for
the 0.2.1 checkout; the real end-to-end dry run is ``test_v021_real_dry_run.py``.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from evals_helpers import bundle, minimal_scenario
from grimoire.capture import v021 as capture
from grimoire.evals.format import parse_scenario
from grimoire.evals.format2 import parse_run_v2

HAS_GIT = shutil.which("git") is not None
KEY = "sk-test-SECRET-0123456789"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    if not HAS_GIT:
        pytest.skip("git is not available")
    repo = tmp_path / "v021"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README").write_text("synthetic 0.2.1 stand-in\n")
    git(repo, "add", "README")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
    monkeypatch.setattr(capture, "EXPECTED_COMMIT", git(repo, "rev-parse", "HEAD"))
    return repo


@pytest.fixture
def scratch_tmp(tmp_path, monkeypatch):
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def scenario_files(tmp_path, monkeypatch):
    """A one-turn synthetic scenario and setup, found by name like the shipped benchmarks."""
    bench = tmp_path / "bench" / "mini"
    bench.mkdir(parents=True)
    (bench / "scenario.json").write_text(json.dumps(minimal_scenario()))
    (bench / "v021_setup.json").write_text(json.dumps({"scenario_id": "mini"}))
    monkeypatch.setattr(capture, "benchmark_file", lambda name, filename: bench.parent / name / filename)
    return bench


def fake_driver(tmp_path, monkeypatch, body: str) -> Path:
    """A driver script; ``body`` runs with ``args`` (argv dict) and ``bundle`` (the default)."""
    script = tmp_path / "driver.py"
    script.write_text(textwrap.dedent(f"""
        import json, os, sys
        argv = sys.argv[1:]
        args = dict(zip(argv[::2], argv[1::2]))
        bundle = {json.dumps(bundle())!r}
        bundle = json.loads(bundle)
    """) + textwrap.dedent(body))
    monkeypatch.setattr(capture, "DRIVER", script)
    return script


PRINT = "print(json.dumps(bundle))\n"


def argv(checkout, out, *extra):
    return ["--scenario", "mini", "--v1-checkout", str(checkout), "--v1-python", sys.executable,
            "--out", str(out), *extra]


def dry(checkout, out):
    return argv(checkout, out, "--dry-run", "--provider", "openrouter")


def leftovers(scratch: Path) -> list[str]:
    return sorted(p.name for p in scratch.iterdir())


def test_requires_exactly_one_mode(tmp_path, capsys):
    out = tmp_path / "out"
    assert capture.main(argv(tmp_path, out, "--provider", "openrouter")) == 2
    assert capture.main(argv(tmp_path, out, "--dry-run", "--live", "--provider",
                             "openrouter")) == 2
    assert not out.exists()


def test_live_requires_confirm_billing_and_key_env(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("TEST_KEY", KEY)
    base = argv(tmp_path, out, "--live", "--provider", "openrouter", "--model", "m")
    assert capture.main([*base, "--api-key-env", "TEST_KEY"]) == 2
    assert "--confirm-billing" in capsys.readouterr().err
    assert capture.main([*base, "--confirm-billing"]) == 2
    assert "--api-key-env" in capsys.readouterr().err
    monkeypatch.delenv("TEST_KEY")
    assert capture.main([*base, "--confirm-billing", "--api-key-env", "TEST_KEY"]) == 2
    assert KEY not in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("kind", ["claude", "openai_compatible"])
def test_claude_and_openai_compatible_refused(tmp_path, kind):
    assert capture.main(argv(tmp_path, tmp_path / "out", "--dry-run", "--provider", kind)) == 2


def test_wrong_commit_refused(checkout, monkeypatch):
    monkeypatch.setattr(capture, "EXPECTED_COMMIT", "1" * 40)
    with pytest.raises(capture.CaptureRefused, match="pinned"):
        capture.verify_checkout(checkout)


def test_dirty_checkout_refused(checkout):
    assert capture.verify_checkout(checkout) == capture.EXPECTED_COMMIT
    (checkout / "README").write_text("modified\n")
    with pytest.raises(capture.CaptureRefused, match="modified"):
        capture.verify_checkout(checkout)
    git(checkout, "checkout", "README")
    (checkout / "untracked.py").write_text("")
    with pytest.raises(capture.CaptureRefused, match="modified"):
        capture.verify_checkout(checkout)


def test_ignored_files_do_not_dirty_the_checkout(checkout):
    (checkout / ".gitignore").write_text("venv/\n")
    git(checkout, "add", ".gitignore")
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "i")
    (checkout / "venv").mkdir()
    assert capture.verify_checkout(checkout, expected_commit=git(checkout, "rev-parse", "HEAD"))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_out_refuses_nonempty_and_symlink_into_repo(tmp_path):
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("")
    with pytest.raises(capture.CaptureRefused, match="empty"):
        capture.check_out_dir(full, forbidden=[])
    link = tmp_path / "link"
    link.symlink_to(capture.REPO_ROOT / "docs")
    with pytest.raises(capture.CaptureRefused, match="inside"):
        capture.check_out_dir(link / "out", forbidden=[capture.REPO_ROOT])
    assert capture.check_out_dir(tmp_path / "fresh", forbidden=[capture.REPO_ROOT]) == (
        tmp_path / "fresh").resolve()


def test_out_inside_v1_checkout_or_library_refused(tmp_path, monkeypatch):
    v1 = tmp_path / "v021"
    v1.mkdir()
    roots = capture.forbidden_roots(v1, environ={"GRIMOIRE_V2_HOME": str(tmp_path / "v2home"),
                                                 "GRIMOIRE_HOME": str(tmp_path / "v1home")},
                                    home=tmp_path / "user")
    for bad in (v1 / "out", tmp_path / "v2home" / "out", tmp_path / "v1home" / "out",
                tmp_path / "user" / ".grimoire" / "out", tmp_path / "user" / ".grimoire-v2"):
        with pytest.raises(capture.CaptureRefused):
            capture.check_out_dir(bad, forbidden=roots)


def test_fake_dry_run_end_to_end_writes_three_files(checkout, scratch_tmp, scenario_files,
                                                    tmp_path, monkeypatch, capsys):
    fake_driver(tmp_path, monkeypatch, PRINT)
    out = tmp_path / "out"
    assert capture.main(dry(checkout, out)) == 0, capsys.readouterr().err
    assert sorted(p.name for p in out.iterdir()) == ["capture-report.txt", "evidence.json",
                                                     "run.json"]
    run = parse_run_v2(json.loads((out / "run.json").read_text()),
                       parse_scenario(minimal_scenario()))
    assert run.source.mode == "dry_run" and len(run.calls) == 1
    report = (out / "capture-report.txt").read_text()
    assert "usage status" in report and "synthetic" in report
    assert json.loads((out / "evidence.json").read_text()) == bundle()
    assert leftovers(scratch_tmp) == []


def test_driver_failure_leaves_no_output_and_no_temp_home(checkout, scratch_tmp, scenario_files,
                                                          tmp_path, monkeypatch, capsys):
    fake_driver(tmp_path, monkeypatch, "sys.stderr.write('boom\\n'); sys.exit(1)\n")
    out = tmp_path / "out"
    assert capture.main(dry(checkout, out)) == 1
    assert "boom" in capsys.readouterr().err
    assert not out.exists() and leftovers(scratch_tmp) == []
    assert [p.name for p in tmp_path.iterdir() if ".partial" in p.name] == []


def test_unconvertible_bundle_leaves_no_output(checkout, scratch_tmp, scenario_files, tmp_path,
                                               monkeypatch, capsys):
    fake_driver(tmp_path, monkeypatch, "bundle['usage_rows'] = []\n" + PRINT)
    out = tmp_path / "out"
    assert capture.main(dry(checkout, out)) == 1
    assert "missing_usage_row" in capsys.readouterr().err
    assert not out.exists() and leftovers(scratch_tmp) == []


def test_keyboard_interrupt_cleans_up(checkout, scratch_tmp, scenario_files, tmp_path,
                                      monkeypatch):
    fake_driver(tmp_path, monkeypatch, "import time; time.sleep(30)\n")
    real_popen = subprocess.Popen
    started = []

    class Interrupting(real_popen):
        def communicate(self, *args, **kwargs):
            started.append(self)
            raise KeyboardInterrupt

    monkeypatch.setattr(capture.subprocess, "Popen", Interrupting)
    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        capture.main(dry(checkout, out))
    assert started and started[0].poll() is not None  # the child was killed
    assert not out.exists() and leftovers(scratch_tmp) == []


def test_key_value_in_output_refused_nothing_written(checkout, scratch_tmp, scenario_files,
                                                     tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TEST_KEY", KEY)
    fake_driver(tmp_path, monkeypatch, textwrap.dedent("""
        bundle['mode'] = 'live'
        bundle['choice_outcomes'] = {'c1': 'unlabelled'}
        bundle['turns'][0]['responses'][0]['text'] = os.environ['TEST_KEY']
        sys.stderr.write('debug ' + os.environ['TEST_KEY'] + '\\n')
    """) + PRINT)
    out = tmp_path / "out"
    code = capture.main(argv(checkout, out, "--live", "--provider", "openrouter", "--model", "m",
                             "--api-key-env", "TEST_KEY", "--confirm-billing"))
    assert code == 1
    assert KEY not in capsys.readouterr().err
    assert not out.exists() and leftovers(scratch_tmp) == []


def test_child_env_has_no_parent_secrets(checkout, scratch_tmp, scenario_files, tmp_path,
                                         monkeypatch, capsys):
    monkeypatch.setenv("PARENT_SECRET", "x")
    monkeypatch.setenv("GRIMOIRE_V2_HOME", str(tmp_path / "v2home"))
    monkeypatch.setenv("TEST_KEY", KEY)
    env_dump = tmp_path / "env.json"
    fake_driver(tmp_path, monkeypatch, textwrap.dedent(f"""
        open({str(env_dump)!r}, 'w').write(json.dumps(sorted(os.environ)))
        bundle['mode'] = 'live'
        bundle['choice_outcomes'] = {{'c1': 'unlabelled'}}
    """) + PRINT)
    out = tmp_path / "out"
    assert capture.main(argv(checkout, out, "--live", "--provider", "openrouter", "--model", "m",
                             "--api-key-env", "TEST_KEY", "--confirm-billing")) == 0, \
        capsys.readouterr().err
    # macOS's CoreFoundation may add __CF_* variables to any process it starts.
    names = {n for n in json.loads(env_dump.read_text()) if not n.startswith("__CF")}
    assert "PARENT_SECRET" not in names and "GRIMOIRE_V2_HOME" not in names
    assert {"GRIMOIRE_HOME", "HOME", "XDG_STATE_HOME", "TEST_KEY"} <= names
    assert names <= set(capture.CHILD_ENV_NAMES) | {"TEST_KEY", "LC_CTYPE"}


def test_dry_run_child_gets_no_key(checkout, scratch_tmp, scenario_files, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    env_dump = tmp_path / "env.json"
    fake_driver(tmp_path, monkeypatch, f"open({str(env_dump)!r}, 'w').write(json.dumps(sorted(os.environ)))\n" + PRINT)
    assert capture.main(dry(checkout, tmp_path / "out")) == 0
    assert "OPENROUTER_API_KEY" not in json.loads(env_dump.read_text())


def test_driver_gets_the_temp_home_not_a_real_library(checkout, scratch_tmp, scenario_files,
                                                      tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    fake_driver(tmp_path, monkeypatch, textwrap.dedent(f"""
        open({str(seen)!r}, 'w').write(json.dumps({{k: os.environ[k] for k in
            ('GRIMOIRE_HOME', 'HOME', 'XDG_STATE_HOME')}} | {{'args': args}}))
    """) + PRINT)
    assert capture.main(dry(checkout, tmp_path / "out")) == 0
    data = json.loads(seen.read_text())
    for name in ("GRIMOIRE_HOME", "HOME", "XDG_STATE_HOME"):
        assert Path(data[name]).is_relative_to(scratch_tmp), name
    assert data["args"]["--mode"] == "dry_run" and data["args"]["--provider"] == "openrouter"
    assert Path(data["args"]["--checkout"]) == checkout.resolve()


def test_child_env_keeps_what_windows_needs_to_start_python(tmp_path, monkeypatch):
    # Windows cannot start an interpreter without SYSTEMROOT; nothing else is passed through.
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    options = capture.CaptureOptions("mini", tmp_path, Path(sys.executable), tmp_path / "out",
                                     "dry_run", "openrouter")
    env = capture._child_env(tmp_path, options, None)
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert set(env) <= set(capture.CHILD_ENV_NAMES)
