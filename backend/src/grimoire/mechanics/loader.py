"""Dynamic import and protocol validation for discovered mechanics modules.

The loader imports ``mechanics.py`` under a synthetic module name (so two
modules can each define ``Mechanics``), instantiates the entry class, and
verifies the instance against the ``MechanicsModule`` protocol. Errors are
returned in the result rather than raised so the caller can decide whether
to fail loudly or skip and continue.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.mechanics.discovery import DiscoveredModule
from grimoire.types.mechanics import ModuleManifest
from grimoire.types.protocols import MechanicsModule
from grimoire.validation.errors import ValidationError
from grimoire.validation.manifests import validate_mechanics_manifest
from grimoire.validation.validator import check_schema

# Names the loader will look up on the imported module when no
# ``entry_class`` is declared in the manifest. The first one that exists
# wins. ``MECHANICS`` (a pre-built instance) is honored before classes so
# modules can skip the factory step entirely.
DEFAULT_ENTRY_CANDIDATES: tuple[str, ...] = (
    "MECHANICS",
    "Mechanics",
    "MechanicsModule",
)

# The minimum set of attributes / methods we expect on a MechanicsModule.
# This is a *runtime* subset of the protocol; the protocol class itself
# uses `...` bodies and isn't `runtime_checkable`.
REQUIRED_ATTRS: tuple[str, ...] = ("id", "name", "version", "api_version")
REQUIRED_METHODS: tuple[str, ...] = (
    "sheet_schema",
    "validate_sheet",
    "initialize_sheet",
    "list_content_kinds",
    "content_schema",
    "capabilities_of",
    "power_definitions",
    "power_definition",
    "evaluate_pre_roll",
    "resolve_roll",
    "validate_narrated_event",
    "character_creation_steps",
    "time_tick",
    "system_summary",
)


@dataclass(frozen=True)
class LoadResult:
    """Outcome of attempting to load a single discovered mechanics module.

    On success, ``manifest`` and ``instance`` are both populated. On
    failure, ``errors`` describes what went wrong; ``manifest`` may still
    be populated if only protocol validation failed.

    ``sheet_schemas`` and ``content_schemas`` are JSON Schemas read from
    ``sheets/<kind>.json`` / ``content/<kind>.json`` under ``module_dir``.
    ``theme_css`` is the raw CSS body if the manifest declared one and the
    file was readable. ``warnings`` collects non-fatal complaints (declared
    sheet/content/theme that didn't show up on disk, malformed schemas).
    """

    module_dir: Path
    module_id: str
    manifest: ModuleManifest | None
    instance: MechanicsModule | None
    errors: list[str]
    sheet_schemas: dict[str, dict] = field(default_factory=dict)
    content_schemas: dict[str, dict] = field(default_factory=dict)
    theme_css: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.manifest is not None and self.instance is not None


def load_module(discovered: DiscoveredModule) -> LoadResult:
    """Validate the manifest, import ``mechanics.py``, instantiate the class."""
    module_dir = discovered.module_dir
    raw = discovered.raw_manifest
    declared_id = raw.get("id") if isinstance(raw, dict) else None
    module_id = declared_id if isinstance(declared_id, str) else module_dir.name

    errors: list[str] = []

    schema_result = validate_mechanics_manifest(raw)
    if not schema_result.ok:
        errors.extend(_format_validation_errors(schema_result.errors))
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=None,
            instance=None,
            errors=errors,
        )

    if declared_id != module_dir.name:
        errors.append(
            f"manifest `id` ({declared_id!r}) does not match directory name ({module_dir.name!r})"
        )

    manifest = _build_manifest(raw)

    if not discovered.entry_path.is_file():
        errors.append(f"missing mechanics.py at {discovered.entry_path}")
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=manifest,
            instance=None,
            errors=errors,
        )

    try:
        module = _import_module(module_id, discovered.entry_path)
    except Exception as exc:
        errors.append(f"failed to import mechanics.py: {exc!r}")
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=manifest,
            instance=None,
            errors=errors,
        )

    entry_name = _entry_name(raw)
    instance, instance_err = _resolve_entry(module, entry_name)
    if instance_err is not None:
        errors.append(instance_err)
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=manifest,
            instance=None,
            errors=errors,
        )

    if not satisfies_mechanics_protocol(instance):
        missing = _missing_members(instance)
        errors.append(
            "mechanics entry does not satisfy the MechanicsModule protocol; "
            f"missing members: {sorted(missing)}"
        )
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=manifest,
            instance=None,
            errors=errors,
        )

    # Surface a friendly error if the instance's ``id`` disagrees with the
    # manifest — the rest of the app keys by manifest id so a mismatch is
    # a foot-gun for module authors.
    instance_id = getattr(instance, "id", None)
    if isinstance(instance_id, str) and instance_id != manifest.id:
        errors.append(f"instance.id ({instance_id!r}) does not match manifest.id ({manifest.id!r})")
        return LoadResult(
            module_dir=module_dir,
            module_id=module_id,
            manifest=manifest,
            instance=None,
            errors=errors,
        )

    sheet_schemas, sheet_warnings = _load_sheet_schemas(module_dir, manifest)
    content_schemas, content_warnings = _load_content_schemas(module_dir, manifest)
    theme_css, theme_warnings = _load_theme_css(module_dir, raw)

    warnings: list[str] = []
    warnings.extend(sheet_warnings)
    warnings.extend(content_warnings)
    warnings.extend(theme_warnings)

    return LoadResult(
        module_dir=module_dir,
        module_id=module_id,
        manifest=manifest,
        instance=instance,
        errors=errors,
        sheet_schemas=sheet_schemas,
        content_schemas=content_schemas,
        theme_css=theme_css,
        warnings=warnings,
    )


def _load_sheet_schemas(
    module_dir: Path, manifest: ModuleManifest
) -> tuple[dict[str, dict], list[str]]:
    """Read ``sheets/<kind>.json`` for each declared sheet_kind."""
    schemas: dict[str, dict] = {}
    warnings: list[str] = []
    sheet_dir = module_dir / "sheets"
    for kind in manifest.sheet_kinds:
        path = sheet_dir / f"{kind}.json"
        if not path.is_file():
            warnings.append(f"sheet_kind {kind!r} declared in manifest but {path.name} is missing")
            continue
        schema, err = _read_schema_file(path)
        if err is not None:
            warnings.append(f"sheets/{kind}.json: {err}")
            continue
        schemas[kind] = schema
    return schemas, warnings


def _load_content_schemas(
    module_dir: Path, manifest: ModuleManifest
) -> tuple[dict[str, dict], list[str]]:
    """Read ``content/<kind>.json`` for each declared content_kind."""
    schemas: dict[str, dict] = {}
    warnings: list[str] = []
    content_dir = module_dir / "content"
    for kind in manifest.content_kinds:
        path = content_dir / f"{kind}.json"
        if not path.is_file():
            warnings.append(
                f"content_kind {kind!r} declared in manifest but {path.name} is missing"
            )
            continue
        schema, err = _read_schema_file(path)
        if err is not None:
            warnings.append(f"content/{kind}.json: {err}")
            continue
        schemas[kind] = schema
    return schemas, warnings


def _read_schema_file(path: Path) -> tuple[dict, str | None]:
    """Load a JSON Schema from disk and validate it with ``check_schema``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"could not read JSON: {exc}"
    if not isinstance(raw, dict):
        return {}, "schema must be a JSON object"
    result = check_schema(raw)
    if not result.ok:
        msgs = "; ".join(e.message for e in result.errors)
        return raw, f"invalid JSON Schema: {msgs}"
    return raw, None


def _load_theme_css(module_dir: Path, raw_manifest: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Read ``manifest.ui.theme_css`` (a path relative to ``module_dir``)."""
    ui = raw_manifest.get("ui")
    if not isinstance(ui, dict):
        return None, []
    declared = ui.get("theme_css")
    if not isinstance(declared, str) or not declared:
        return None, []
    target = (module_dir / declared).resolve()
    # Refuse paths that escape the module directory.
    try:
        target.relative_to(module_dir.resolve())
    except ValueError:
        return None, [f"theme_css path {declared!r} escapes module directory"]
    if not target.is_file():
        return None, [f"theme_css declared as {declared!r} but file is missing"]
    try:
        return target.read_text(encoding="utf-8"), []
    except OSError as exc:
        return None, [f"theme_css read failed: {exc}"]


def _build_manifest(raw: dict[str, Any]) -> ModuleManifest:
    return ModuleManifest(
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        api_version=raw["api_version"],
        author=raw.get("author") or "",
        homepage=raw.get("homepage") or "",
        description=raw.get("description") or "",
        sheet_kinds=list(raw.get("sheet_kinds") or []),
        content_kinds=list(raw.get("content_kinds") or []),
        capabilities=list(raw.get("capabilities") or []),
        ui=dict(raw.get("ui") or {}),
    )


def _import_module(module_id: str, entry_path: Path) -> Any:
    """Import ``mechanics.py`` under a stable synthetic module name.

    Re-importing the same module id replaces the previous module entry so
    reloads pick up edits without leaving stale state.
    """
    module_name = f"grimoire_mechanics._loaded.{module_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {entry_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _entry_name(raw: dict[str, Any]) -> str | None:
    """The manifest can declare ``entry_class: Foo`` to override discovery."""
    name = raw.get("entry_class")
    if isinstance(name, str) and name:
        return name
    return None


def _resolve_entry(module: Any, entry_name: str | None) -> tuple[Any | None, str | None]:
    """Find the entry instance on ``module``.

    Preference order:

    1. ``module.<entry_name>`` if the manifest declared ``entry_class``.
    2. A pre-built instance named ``MECHANICS``.
    3. A class named ``Mechanics`` or ``MechanicsModule`` — instantiated
       with no arguments.
    """
    if entry_name is not None:
        candidate = getattr(module, entry_name, None)
        if candidate is None:
            return None, f"mechanics.py does not define '{entry_name}'"
        instance, err = _materialise(candidate, entry_name)
        return instance, err

    for name in DEFAULT_ENTRY_CANDIDATES:
        candidate = getattr(module, name, None)
        if candidate is None:
            continue
        instance, err = _materialise(candidate, name)
        if instance is not None:
            return instance, None
        if err is not None:
            return None, err

    return None, (
        "mechanics.py must define one of "
        f"{list(DEFAULT_ENTRY_CANDIDATES)} or declare entry_class in manifest.yaml"
    )


def _materialise(candidate: Any, name: str) -> tuple[Any | None, str | None]:
    if inspect.isclass(candidate):
        try:
            return candidate(), None
        except Exception as exc:
            return None, f"failed to instantiate {name}: {exc!r}"
    return candidate, None


def satisfies_mechanics_protocol(instance: Any) -> bool:
    """Best-effort runtime check that ``instance`` is a MechanicsModule.

    ``typing.Protocol`` with `...` bodies isn't `runtime_checkable`, so we
    spell out the membership check here.
    """
    return not _missing_members(instance)


def _missing_members(instance: Any) -> set[str]:
    missing: set[str] = set()
    for attr in REQUIRED_ATTRS:
        if not hasattr(instance, attr):
            missing.add(attr)
            continue
        value = getattr(instance, attr)
        if not isinstance(value, str) or not value:
            missing.add(attr)
    for method in REQUIRED_METHODS:
        fn = getattr(instance, method, None)
        if not callable(fn):
            missing.add(method)
    return missing


def _format_validation_errors(errors: Iterable[ValidationError]) -> list[str]:
    formatted: list[str] = []
    for err in errors:
        pointer = err.pointer or "/"
        formatted.append(f"manifest invalid at {pointer}: {err.message}")
    return formatted


__all__ = [
    "DEFAULT_ENTRY_CANDIDATES",
    "REQUIRED_ATTRS",
    "REQUIRED_METHODS",
    "LoadResult",
    "load_module",
    "satisfies_mechanics_protocol",
]
