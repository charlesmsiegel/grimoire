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


def test_optional_import_flags_are_reads_of_the_defining_owner(monkeypatch):
    support = importlib.import_module("grimoire.store.compiled._sqlite_support")
    filesystem = importlib.import_module("grimoire.store.compiled._filesystem")
    monkeypatch.setattr(support, "_sqlite", None)
    monkeypatch.delattr(support, "_sqlite_available", raising=False)
    monkeypatch.setattr(filesystem, "_ctypes", None)
    monkeypatch.delattr(filesystem, "_ctypes_available", raising=False)
    assert compiled._sqlite is None and compiled._ctypes is None
    assert not hasattr(compiled, "_sqlite_available")
    assert not hasattr(compiled, "_ctypes_available")
    assert "_state" in dir(compiled)
    assert "_sqlite_available" not in dir(compiled)
    assert "_ctypes_available" not in dir(compiled)


def test_registered_kinds_dictionary_is_shared_with_the_defining_owner():
    contracts = importlib.import_module("grimoire.store.compiled._contracts")
    assert compiled.KINDS is contracts.KINDS


@pytest.mark.parametrize("optional, flag, alias", [
    ("sqlite3", "_sqlite", "_sqlite_available"),
    ("ctypes", "_ctypes", "_ctypes_available"),
])
@pytest.mark.parametrize("failure", ["ImportError", "OSError"])
def test_unavailable_optional_import_keeps_the_package_importable(optional, flag, alias, failure):
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
assert not hasattr(compiled, {alias!r})
assert {alias!r} not in dir(compiled)
assert compiled._state is not None
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15,
                   capture_output=True, text=True)
