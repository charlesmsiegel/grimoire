"""Standard-library modules that an embedded or packaged Python may omit."""

from __future__ import annotations

from types import ModuleType
from typing import Any


def optional_module(name: str, importer: dict[str, Any]) -> ModuleType | None:
    """Import ``name`` on behalf of a module, or None when the host lacks it.

    Embedded distributions can ship without libffi, so ``import ctypes`` fails
    with ImportError or, when the extension is present but cannot load, with
    OSError. Either must leave the importing store module usable: the caller
    treats None as "this platform probe is unavailable". Passing the caller's
    ``globals()`` attributes the import to that module exactly as an ``import``
    statement written there would be, so import hooks see the real importer.
    """
    try:
        return __import__(name, importer)
    except (ImportError, OSError):
        return None
