"""Dynamic import and protocol validation for discovered plugins.

The loader is intentionally simple: it imports `plugin.py` under a synthetic
module name (so two plugins can both define `Provider`), instantiates the
classes named in the manifest, and verifies each instance against the
protocol for its declared kind. Errors are returned in the result rather
than raised so the caller can decide whether to fail loudly or skip and
continue.

Per-plugin virtual environments are *recognised* via the `isolated_venv`
manifest flag but actually creating them is left to v2 (spec 15 §Open
questions). The loader records the flag so the UI can show "this plugin
wanted an isolated venv but isolation is off" status.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.plugins.discovery import DiscoveredPlugin
from grimoire.types.plugins import PluginKind, PluginManifest
from grimoire.types.protocols import (
    EmbeddingProvider,
    ExportAdapter,
    ImageGenBackend,
    LLMProvider,
)
from grimoire.validation.errors import ValidationError
from grimoire.validation.manifests import validate_plugin_manifest

# Maps each plugin kind to the protocol the registered instance must
# satisfy at runtime. Kept as a `dict` rather than an enum lookup so
# adding a new kind is one line.
PROTOCOL_FOR_KIND: dict[PluginKind, type] = {
    PluginKind.LLM_PROVIDER: LLMProvider,
    PluginKind.EMBEDDING_PROVIDER: EmbeddingProvider,
    PluginKind.IMAGEGEN_BACKEND: ImageGenBackend,
    PluginKind.EXPORT_ADAPTER: ExportAdapter,
}


@dataclass(frozen=True)
class LoadedInstance:
    kind: PluginKind
    instance: Any


@dataclass(frozen=True)
class LoadResult:
    """Outcome of attempting to load a single discovered plugin.

    On success, `manifest` is populated and `instances` contains one entry
    per implemented kind. On failure, `errors` describes what went wrong;
    `manifest` may still be populated if only protocol validation failed.
    """

    plugin_dir: Path
    plugin_id: str
    manifest: PluginManifest | None
    instances: list[LoadedInstance]
    errors: list[str]
    bundled: bool

    @property
    def ok(self) -> bool:
        return not self.errors and self.manifest is not None


def load_plugin(
    discovered: DiscoveredPlugin,
    config: dict[str, Any] | None = None,
) -> LoadResult:
    """Validate the manifest, import `plugin.py`, instantiate each class."""
    plugin_dir = discovered.plugin_dir
    raw = discovered.raw_manifest
    declared_id = raw.get("id") if isinstance(raw, dict) else None
    plugin_id = declared_id if isinstance(declared_id, str) else plugin_dir.name

    errors: list[str] = []

    schema_result = validate_plugin_manifest(raw)
    if not schema_result.ok:
        errors.extend(_format_validation_errors(schema_result.errors))
        return LoadResult(
            plugin_dir=plugin_dir,
            plugin_id=plugin_id,
            manifest=None,
            instances=[],
            errors=errors,
            bundled=discovered.bundled,
        )

    if declared_id != plugin_dir.name:
        errors.append(
            f"manifest `id` ({declared_id!r}) does not match directory name ({plugin_dir.name!r})"
        )

    manifest = _build_manifest(raw)

    if not discovered.entry_path.is_file():
        errors.append(f"missing plugin.py at {discovered.entry_path}")
        return LoadResult(
            plugin_dir=plugin_dir,
            plugin_id=plugin_id,
            manifest=manifest,
            instances=[],
            errors=errors,
            bundled=discovered.bundled,
        )

    try:
        module = _import_plugin_module(plugin_id, discovered.entry_path)
    except Exception as exc:
        errors.append(f"failed to import plugin.py: {exc!r}")
        return LoadResult(
            plugin_dir=plugin_dir,
            plugin_id=plugin_id,
            manifest=manifest,
            instances=[],
            errors=errors,
            bundled=discovered.bundled,
        )

    instances: list[LoadedInstance] = []
    config_arg = dict(config or {})
    for kind in manifest.implements:
        class_name = manifest.classes.get(kind.value)
        if not class_name:
            errors.append(f"`classes` is missing an entry for kind '{kind.value}'")
            continue
        cls = getattr(module, class_name, None)
        if cls is None:
            errors.append(f"plugin.py does not define class '{class_name}' for kind '{kind.value}'")
            continue
        try:
            instance = _instantiate(cls, config_arg)
        except Exception as exc:
            errors.append(f"failed to instantiate {class_name} for kind '{kind.value}': {exc!r}")
            continue
        protocol = PROTOCOL_FOR_KIND[kind]
        if not _satisfies_protocol(instance, protocol):
            errors.append(
                f"{class_name} does not satisfy the {protocol.__name__} protocol "
                f"for kind '{kind.value}'"
            )
            continue
        instances.append(LoadedInstance(kind=kind, instance=instance))

    return LoadResult(
        plugin_dir=plugin_dir,
        plugin_id=plugin_id,
        manifest=manifest,
        instances=instances,
        errors=errors,
        bundled=discovered.bundled,
    )


def _build_manifest(raw: dict[str, Any]) -> PluginManifest:
    """Project the validated raw dict onto our typed PluginManifest.

    Validation already guarantees the required fields are present.
    """
    return PluginManifest(
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        api_version=raw["api_version"],
        implements=[PluginKind(k) for k in raw["implements"]],
        classes=dict(raw["classes"]),
        config_schema=dict(raw.get("config_schema") or {}),
        requirements=list(raw.get("requirements") or []),
        author=raw.get("author") or "",
        homepage=raw.get("homepage") or "",
        description=raw.get("description") or "",
        isolated_venv=bool(raw.get("isolated_venv") or False),
        raw=dict(raw),
    )


def _import_plugin_module(plugin_id: str, entry_path: Path) -> Any:
    """Import `plugin.py` under a stable synthetic module name.

    Re-importing the same plugin id replaces the previous module entry so
    rescans pick up edits without leaving stale state.
    """
    module_name = f"grimoire_plugins._loaded.{plugin_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {entry_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Don't leave a half-loaded module in sys.modules.
        sys.modules.pop(module_name, None)
        raise
    return module


def _instantiate(cls: type, config: dict[str, Any]) -> Any:
    """Call the plugin class constructor, passing config if it accepts it.

    Plugins commonly take a `config: dict` argument (spec 15 §Per-plugin
    configuration). Constructors with no required arguments are also
    supported so simple/zero-config plugins don't have to add a parameter.
    """
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        return cls()
    params = [p for p in sig.parameters.values() if p.name != "self"]
    if not params:
        return cls()
    first = params[0]
    if first.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ) and first.name in {"config", "settings", "options"}:
        return cls(**{first.name: config})
    if (
        first.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        and first.default is inspect.Parameter.empty
    ):
        return cls(config)
    return cls()


def _satisfies_protocol(instance: Any, protocol: type) -> bool:
    """Best-effort runtime check that `instance` implements `protocol`.

    Most plugin protocols (LLMProvider, EmbeddingProvider, ImageGenBackend,
    ExportAdapter) declare both class attributes (`id`, `name`, ...) and
    async methods. `typing.Protocol` with `@runtime_checkable` only checks
    *member presence* — we accept that and additionally verify the few
    well-known attributes are populated to catch the common "forgot to set
    self.id" mistake.
    """
    annotations = getattr(protocol, "__annotations__", {})
    for member in annotations:
        if not hasattr(instance, member):
            return False
    callable_members = [
        name
        for name in dir(protocol)
        if not name.startswith("_") and callable(getattr(protocol, name, None))
    ]
    for name in callable_members:
        if not hasattr(instance, name):
            return False
        attr = getattr(instance, name)
        if not callable(attr):
            return False
    return True


def _format_validation_errors(errors: Iterable[ValidationError]) -> list[str]:
    formatted: list[str] = []
    for err in errors:
        pointer = err.pointer or "/"
        formatted.append(f"manifest invalid at {pointer}: {err.message}")
    return formatted


__all__ = [
    "PROTOCOL_FOR_KIND",
    "LoadResult",
    "LoadedInstance",
    "load_plugin",
]
