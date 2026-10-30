"""The inference contracts stay pure: no I/O, clock, randomness or providers."""

import ast
from pathlib import Path

import grimoire.core
import grimoire.core.inference as inference

ALLOWED_STDLIB = {"__future__", "collections", "dataclasses", "enum", "functools",
                  "itertools", "types", "typing"}


def test_inference_package_imports_only_pure_stdlib():
    package = Path(inference.__file__).parent
    sources = sorted(package.glob("*.py"))
    assert sources
    for source in sources:
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
                assert (name.split(".")[0] in ALLOWED_STDLIB
                        or name.startswith("grimoire.core.inference")), (
                    f"{source.name} imports {name}")


def test_inference_is_not_exported_from_fiction_core():
    assert not set(inference.__all__) & set(grimoire.core.__all__)
