"""Catalogued invariants: test-only harness and catalog integrity (purity: test_purity.py)."""

import ast
import re
from pathlib import Path

from grimoire.testing.explore import HARNESS_INVARIANTS

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "src" / "grimoire"
CATALOG = BACKEND.parent / "docs" / "TESTING.md"
BUILT_INS = ("atomic_outcome", "idempotent_recovery", "no_residue", "completes_after", "untouched",
             "preserves_meddle")


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield node.level, module
            for alias in node.names:  # ``from grimoire import testing``, ``from . import testing``
                yield node.level, f"{module}.{alias.name}" if module else alias.name


def test_harness_is_test_only():
    problems = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        in_harness = relative.parts[0] == "testing"
        for level, module in _imports(ast.parse(path.read_text(encoding="utf-8"))):
            if in_harness and module.split(".")[0] == "pytest":
                problems.append(f"{relative} imports pytest")
            harness = module == "grimoire.testing" or module.startswith("grimoire.testing.") or (
                level > 0 and not in_harness and module.split(".")[0] == "testing")
            if harness and not in_harness:
                problems.append(f"{relative} imports the test harness")
    assert problems == []


def test_catalog_references_exist():
    text = CATALOG.read_text(encoding="utf-8")
    references = set(re.findall(r"backend/tests/(\w+\.py)::(test_\w+)", text))
    assert len(references) >= 10
    for filename, name in sorted(references):
        tree = ast.parse((BACKEND / "tests" / filename).read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        assert name in defined, f"docs/TESTING.md cites missing test {filename}::{name}"
    for invariant in HARNESS_INVARIANTS + BUILT_INS:
        assert f"| `{invariant}` |" in text, f"{invariant} is not catalogued"
