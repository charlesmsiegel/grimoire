"""`grimoire.where` reports the store location the resolver actually resolves.

This is what the installers print, and it is the only thing a fresh clone tells
its owner about where their library will land (#207). Hard-coding `~/.grimoire`
into the install scripts would have been right on a default machine and wrong
on every machine that had already been repointed -- so these tests pin the two
overriding cases, not just the default.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from grimoire import where
from grimoire.store import paths


def test_reports_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "library"))
    text = where.describe()
    assert str(tmp_path / "library") in text
    assert "GRIMOIRE_HOME" in text


def test_reports_the_pointer_choice(monkeypatch, tmp_path):
    """A pointer-chosen dir wins over the default and is named as the user's."""
    pointer = tmp_path / ".grimoire.json"
    chosen = tmp_path / "synced" / "grimoire"
    pointer.write_text(json.dumps({"data_dir": str(chosen)}), encoding="utf-8")
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    monkeypatch.setattr(paths, "pointer_path", lambda: pointer)

    text = where.describe()
    assert str(chosen) in text
    assert str(paths.DEFAULT_HOME) not in text
    assert "your chosen location" in text
    assert str(pointer) in text          # named, so it can be edited before first run


def test_reports_the_default_and_how_to_repoint(monkeypatch, tmp_path):
    pointer = tmp_path / ".grimoire.json"      # absent: nothing has been chosen
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    monkeypatch.setattr(paths, "pointer_path", lambda: pointer)
    monkeypatch.setattr(paths, "DEFAULT_HOME", tmp_path / ".grimoire")

    text = where.describe()
    assert str(tmp_path / ".grimoire") in text
    assert "the default location" in text
    assert "data_dir" in text and str(pointer) in text


def test_says_whether_the_store_exists_yet(monkeypatch, tmp_path):
    """The installer runs before anything is created; that has to read as normal."""
    home = tmp_path / "library"
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    assert "created on first use" in where.describe()
    home.mkdir()
    assert "already exists" in where.describe()


def test_printing_creates_nothing(monkeypatch, tmp_path, capsys):
    """Saying where the library goes must not commit the user to that answer."""
    home = tmp_path / "library"
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    where.main()
    assert str(home) in capsys.readouterr().out
    assert not home.exists()


def test_blurb_is_ascii_so_a_windows_console_can_print_it(monkeypatch, tmp_path):
    """cp437/cp1252 stdout raises UnicodeEncodeError on an em dash, not a typo."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "library"))
    text = where.describe()
    assert text.isascii(), [ln for ln in text.splitlines() if not ln.isascii()]


def test_runs_as_a_module(monkeypatch, tmp_path):
    """`python -m grimoire.where` is the form both installers invoke."""
    env = {
        **os.environ,
        "GRIMOIRE_HOME": str(tmp_path / "library"),
        "PYTHONPATH": str(Path(where.__file__).resolve().parents[1]),
    }
    out = subprocess.run([sys.executable, "-m", "grimoire.where"],
                         capture_output=True, text=True, env=env, check=True)
    assert str(tmp_path / "library") in out.stdout
