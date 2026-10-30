"""The authority kernel stays pure: stdlib only, no I/O-capable modules."""

import ast
import sys
from pathlib import Path

import grimoire.security

FORBIDDEN = {
    "os", "pathlib", "io", "shutil", "tempfile", "glob", "socket", "ssl", "http",
    "urllib", "subprocess", "multiprocessing", "threading", "asyncio", "sqlite3",
    "importlib", "builtins", "ctypes",
}


def test_security_package_imports_only_pure_stdlib():
    package = Path(grimoire.security.__file__).parent
    sources = sorted(package.glob("*.py"))
    assert sources
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.level == 1, f"{source.name} imports outside its package"
                continue
            if isinstance(node, ast.ImportFrom):
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                assert top in sys.stdlib_module_names, f"{source.name}: {module}"
                assert top not in FORBIDDEN, f"{source.name}: {module}"
