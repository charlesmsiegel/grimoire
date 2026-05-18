"""Optional base class for mechanics modules backed by an on-disk directory.

``DiskBackedMechanicsModule`` inspects ``__file__`` of the concrete subclass
to find its directory and supplies default implementations of
:meth:`sheet_schema`, :meth:`list_content_kinds`, and :meth:`content_schema`
that read JSON Schemas from ``sheets/<kind>.json`` and ``content/<kind>.json``
respectively. Modules may still override; subclassing is purely opt-in.

Existing modules that hand-roll their factory keep working unchanged — the
mechanics loader inspects the module directory itself (see
``mechanics/loader.py``) so a subclass of this is not required to participate
in the disk-loaded schema flow. The base class is convenience sugar for
authors who don't want to repeat the directory-resolution boilerplate.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any


class DiskBackedMechanicsModule:
    """Base class that resolves its module directory from ``__file__``.

    Subclasses are expected to define identity attributes (``id``, ``name``,
    ``version``, ``api_version``). Override ``module_dir`` to point at a
    different location if the on-disk layout doesn't match Python's import
    layout (uncommon).
    """

    # Subclasses set these on the class. Declared here so type checkers see them.
    id: str
    name: str
    version: str
    api_version: str

    @property
    def module_dir(self) -> Path:
        # Resolve the directory containing the concrete subclass module.
        # ``inspect.getfile`` returns the source file path even when the
        # module was loaded under a synthetic name by the mechanics loader.
        try:
            source = inspect.getfile(type(self))
        except TypeError:  # pragma: no cover - extremely rare
            module = sys.modules.get(type(self).__module__)
            source = getattr(module, "__file__", "") if module else ""
        if not source:
            return Path.cwd()
        return Path(source).resolve().parent

    # ------------------------------------------------------------------
    # Sheet schemas
    # ------------------------------------------------------------------

    def sheet_schema(self, entity_kind: str) -> dict | None:
        path = self.module_dir / "sheets" / f"{entity_kind}.json"
        return _load_json_if_exists(path)

    # ------------------------------------------------------------------
    # Content schemas
    # ------------------------------------------------------------------

    def list_content_kinds(self) -> list[str]:
        content_dir = self.module_dir / "content"
        if not content_dir.is_dir():
            return []
        return sorted(
            entry.stem
            for entry in content_dir.iterdir()
            if entry.is_file() and entry.suffix == ".json"
        )

    def content_schema(self, kind: str) -> dict:
        path = self.module_dir / "content" / f"{kind}.json"
        schema = _load_json_if_exists(path)
        return schema if schema is not None else {}


def _load_json_if_exists(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


__all__ = ["DiskBackedMechanicsModule"]
