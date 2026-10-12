"""Every publishing function in `store.atomic` notes its path (spec 05-C4).

The write set (`store/writeset.py`) is how Grimoire's own writes reach the
write-through queue without wiring two hundred call sites: `atomic`'s
publishing functions each call `writeset.note(path)` once their replace or
append has succeeded. A fifth writer added to `atomic` without that call would
be invisible to every warm-up, and nothing else would fail, so this does.

Mechanical rule: every public callable in `atomic`, except an explicit
allowlist of the ones that publish nothing, ends its OWN body with a
`writeset.note(...)` statement -- after the publish, on every path that gets
there, and not only in a private helper it calls, where one helper's note
would silently cover a writer that never reaches it. No marker: a publishing
function either notes or is added to the allowlist with its reason.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire.store.atomic as atomic

ATOMIC = pathlib.Path(atomic.__file__)

#: Public functions in `atomic` that publish nothing, each with its reason.
NON_PUBLISHING = {
    "is_write_temp": "a name test for walks, never a write",
    "open_text": "opens a record for reading",
    "read_text": "reads a record",
}


def _notes(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Attribute) and func.attr == "note"
            and isinstance(func.value, ast.Name) and func.value.id == "writeset")


def violations(source: str) -> list[str]:
    """Public module-level functions in `source` that should note and do not."""
    tree = ast.parse(source)
    bad = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") or node.name in NON_PUBLISHING:
            continue
        last = node.body[-1]
        if not (isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) and _notes(last.value)):
            bad.append(node.name)
    return bad


def test_every_publishing_function_notes_its_path():
    bad = violations(ATOMIC.read_text(encoding="utf-8"))
    assert not bad, (f"store.atomic publishing functions that do not call writeset.note "
                     f"in their own body: {bad}")


def test_the_allowlist_names_only_real_functions():
    tree = ast.parse(ATOMIC.read_text(encoding="utf-8"))
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert set(NON_PUBLISHING) <= names, sorted(set(NON_PUBLISHING) - names)


def test_the_four_writers_are_the_ones_held():
    """The guard is not vacuous: it covers today's four publishing functions."""
    tree = ast.parse(ATOMIC.read_text(encoding="utf-8"))
    public = {node.name for node in tree.body
              if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert public - set(NON_PUBLISHING) == {"write_text", "write_bytes", "append_line", "streaming_write"}


def test_planted_writer_without_note_fails():
    planted = '''
def write_json(path, value):
    _write_through_fd(path, "w", "utf-8", value)
'''
    assert violations(planted) == ["write_json"]


def test_planted_note_only_in_a_private_helper_fails():
    planted = '''
def _publish(path, data):
    _write_through_fd(path, "wb", None, data)
    writeset.note(path)

def write_blob(path, data):
    _publish(path, data)
'''
    assert violations(planted) == ["write_blob"]


def test_planted_note_in_a_nested_def_does_not_count():
    planted = '''
def write_later(path, data):
    def later():
        writeset.note(path)
    _write_through_fd(path, "wb", None, data)
    return later
'''
    assert violations(planted) == ["write_later"]


def test_planted_note_before_the_publish_fails():
    planted = '''
def write_blob(path, data):
    writeset.note(path)
    _write_through_fd(path, "wb", None, data)
'''
    assert violations(planted) == ["write_blob"]


def test_planted_note_in_a_branch_fails():
    planted = '''
def write_blob(path, data):
    _write_through_fd(path, "wb", None, data)
    if data:
        writeset.note(path)
'''
    assert violations(planted) == ["write_blob"]


def test_planted_writer_with_note_passes():
    planted = '''
def write_blob(path, data):
    _write_through_fd(path, "wb", None, data)
    writeset.note(path)
'''
    assert violations(planted) == []
