"""The inference contracts stay pure: no I/O, clock, randomness or providers."""

import ast
from pathlib import Path

import grimoire.core
import grimoire.core.inference as inference

ALLOWED_STDLIB = {"__future__", "collections", "dataclasses", "enum", "functools",
                  "itertools", "types", "typing"}
OWN_PACKAGE = "grimoire.core.inference"
BANNED_BUILTINS = {"open", "exec", "eval", "compile", "__import__", "input", "print",
                   "breakpoint", "__builtins__", "globals", "locals", "vars"}


def _sources():
    sources = sorted(Path(inference.__file__).parent.rglob("*.py"))
    assert sources
    return sources


def test_inference_package_imports_only_pure_stdlib():
    for source in _sources():
        for node in ast.walk(ast.parse(source.read_text(), filename=str(source))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative: must stay inside this package
                    assert node.level == 1, f"{source.name} reaches outside the package"
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert (name.split(".")[0] in ALLOWED_STDLIB or name == OWN_PACKAGE
                        or name.startswith(OWN_PACKAGE + ".")), f"{source.name} imports {name}"


def test_inference_package_uses_no_io_builtins():
    for source in _sources():
        for node in ast.walk(ast.parse(source.read_text(), filename=str(source))):
            if isinstance(node, ast.Name):
                assert node.id not in BANNED_BUILTINS, f"{source.name} uses {node.id}"


def test_inference_is_not_exported_from_fiction_core():
    exported = {id(getattr(grimoire.core, name)) for name in grimoire.core.__all__}
    assert not set(inference.__all__) & set(grimoire.core.__all__)
    assert not {id(getattr(inference, name)) for name in inference.__all__} & exported
