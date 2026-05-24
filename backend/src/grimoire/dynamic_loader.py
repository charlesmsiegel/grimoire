"""Shared dynamic module loading for mechanics and plugins."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module_from_path(
    path: Path,
    *,
    module_prefix: str,
    module_id: str,
) -> ModuleType:
    """Load a Python module from a filesystem path with cleanup on failure.

    The module is registered under ``{module_prefix}.{module_id}`` in
    ``sys.modules``.  Dashes in *module_id* are replaced with underscores
    for valid Python identifiers.  Re-importing the same id replaces the
    previous entry so reloads pick up edits without stale state.
    """
    name = f"{module_prefix}.{module_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module
