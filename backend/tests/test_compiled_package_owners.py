"""The cache package preserves live process state and optional imports."""

import importlib
import os
import subprocess
import sys

import pytest

from grimoire.store import compiled


def test_process_state_replacement_is_visible_through_the_facade(monkeypatch):
    runtime = importlib.import_module("grimoire.store.compiled._runtime")
    parent = runtime._State(pid=os.getpid() - 1, build_checked=True, build="b" * 64)
    monkeypatch.setattr(runtime, "_state", parent)
    monkeypatch.setattr(runtime, "_inherited", [])
    compiled._process_state()
    child = compiled._state
    assert child is runtime._state and child is not parent
    assert child.pid == os.getpid()
    assert child.build_checked and child.build == parent.build
    assert runtime._inherited == [parent]
    assert child.lock is not parent.lock


@pytest.mark.parametrize("optional, flag", [
    ("sqlite3", "_sqlite"),
    ("ctypes", "_ctypes"),
])
@pytest.mark.parametrize("failure", ["ImportError", "OSError"])
def test_unavailable_optional_import_keeps_the_package_importable(optional, flag, failure):
    script = f"""
import builtins
original = builtins.__import__
def without_optional(name, *args, **kwargs):
    caller = args[0].get('__name__', '') if args and args[0] else ''
    if name == {optional!r} and caller.startswith('grimoire.store.compiled'):
        raise {failure}('optional import unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = without_optional
from grimoire.store import compiled
assert compiled.{flag} is None
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15,
                   capture_output=True, text=True)
