"""Dynamic import and protocol validation for discovered plugins.

The loader is intentionally simple: it imports `plugin.py` under a synthetic
module name (so two plugins can both define `Provider`), instantiates the
classes named in the manifest, and verifies each instance against the
protocol for its declared kind. Errors are returned in the result rather
than raised so the caller can decide whether to fail loudly or skip and
continue.

When the caller passes an ``extra_sys_path`` (typically the
``site-packages`` of a per-plugin venv built by
:mod:`grimoire.plugins.venv`), the loader prepends it to ``sys.path``
only for the duration of the plugin's import so the deps in that venv
are visible without leaking into other plugins' import resolution.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.dynamic_loader import load_module_from_path
from grimoire.plugins.discovery import DiscoveredPlugin
from grimoire.plugins.venv import prepended_sys_path
from grimoire.types.plugins import (
    EmbeddingCapabilities,
    ExportCapabilities,
    ImageGenCapabilities,
    LLMCapabilities,
    PluginCapabilities,
    PluginKind,
    PluginManifest,
)
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
    *,
    extra_sys_path: Path | None = None,
) -> LoadResult:
    """Validate the manifest, import `plugin.py`, instantiate each class.

    When ``extra_sys_path`` is provided, it is prepended to ``sys.path``
    while ``plugin.py`` is being imported. Callers use this to thread the
    ``site-packages`` of a per-plugin venv through so plugins that
    declared ``isolated_venv: true`` resolve their declared
    ``requirements`` independently of the host environment.
    """
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

    # Both the module import *and* the per-class instantiation may resolve
    # imports from the plugin's venv (some plugins do lazy imports inside
    # ``__init__`` to defer heavy deps until they're needed). Keep the
    # site-packages prepended for both phases.
    with prepended_sys_path(extra_sys_path):
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
                errors.append(
                    f"plugin.py does not define class '{class_name}' for kind '{kind.value}'"
                )
                continue
            try:
                instance = _instantiate(cls, config_arg)
            except Exception as exc:
                errors.append(
                    f"failed to instantiate {class_name} for kind '{kind.value}': {exc!r}"
                )
                continue
            protocol = PROTOCOL_FOR_KIND[kind]
            ok, proto_errors = _check_protocol(instance, protocol)
            if not ok:
                joined = "; ".join(proto_errors)
                errors.append(
                    f"{class_name} does not satisfy the {protocol.__name__} protocol "
                    f"for kind '{kind.value}': {joined}"
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
        shares_secrets_with=[str(x) for x in raw.get("shares_secrets_with") or []],
        capabilities=_build_capabilities(raw.get("capabilities")),
        raw=dict(raw),
    )


def _build_capabilities(raw_caps: Any) -> PluginCapabilities:
    """Project the manifest's free-form ``capabilities`` block onto typed shapes.

    Manifest authors can declare per-kind capabilities under the matching
    key (``llm_provider``, ``embedding_provider``, etc.); other shapes are
    ignored rather than rejected so legacy free-form metadata still loads.
    Returns an empty :class:`PluginCapabilities` if the block is missing
    or malformed.
    """
    if not isinstance(raw_caps, dict):
        return PluginCapabilities()
    kwargs: dict[str, Any] = {}
    llm_raw = raw_caps.get(PluginKind.LLM_PROVIDER.value)
    if isinstance(llm_raw, dict):
        kwargs["llm_provider"] = LLMCapabilities.model_validate(llm_raw)
    emb_raw = raw_caps.get(PluginKind.EMBEDDING_PROVIDER.value)
    if isinstance(emb_raw, dict):
        kwargs["embedding_provider"] = EmbeddingCapabilities.model_validate(emb_raw)
    img_raw = raw_caps.get(PluginKind.IMAGEGEN_BACKEND.value)
    if isinstance(img_raw, dict):
        kwargs["imagegen_backend"] = ImageGenCapabilities.model_validate(img_raw)
    exp_raw = raw_caps.get(PluginKind.EXPORT_ADAPTER.value)
    if isinstance(exp_raw, dict):
        kwargs["export_adapter"] = ExportCapabilities.model_validate(exp_raw)
    return PluginCapabilities(**kwargs)


def _import_plugin_module(plugin_id: str, entry_path: Path) -> Any:
    """Import `plugin.py` under a stable synthetic module name."""
    return load_module_from_path(
        entry_path,
        module_prefix="grimoire_plugins._loaded",
        module_id=plugin_id,
    )


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

    Wraps :func:`_check_protocol`; returns the boolean half so existing
    truthiness call sites keep working. New code that wants the reasons a
    plugin failed conformance should call :func:`_check_protocol` directly.
    """
    ok, _ = _check_protocol(instance, protocol)
    return ok


def _check_protocol(instance: Any, protocol: type) -> tuple[bool, list[str]]:
    """Verify ``instance`` satisfies ``protocol`` and explain any failures.

    The check is intentionally stricter than `typing.Protocol`'s
    ``@runtime_checkable`` default (which only looks at member presence):

    * each annotated class attribute must be present on the instance, with
      a value other than ``None`` for attributes that have no protocol-level
      default (catches the common "forgot to set ``self.id``" mistake);
    * each method declared async on the protocol must be a coroutine
      function on the instance — a sync ``def complete`` passes the
      presence check today but explodes the first time the gateway awaits
      it;
    * signatures must accept the parameter names/count the protocol
      declares (so a plugin that renamed ``request`` to ``payload`` fails
      at load time rather than at first request).

    Returns ``(ok, messages)``. ``messages`` is a flat list suitable for
    bubbling into ``LoadResult.errors``.
    """
    errors: list[str] = []
    proto_name = getattr(protocol, "__name__", repr(protocol))

    annotations = getattr(protocol, "__annotations__", {})
    proto_vars = vars(protocol)
    for member in annotations:
        proto_member = proto_vars.get(member)
        # Annotations that point to a callable (async/sync method declared
        # via `async def`) are checked in the callable loop below.
        if callable(proto_member):
            continue
        if not hasattr(instance, member):
            # Protocol-level defaults make the attribute optional —
            # consumers fall back to them when the plugin omits it.
            if member in proto_vars:
                continue
            errors.append(f"missing attribute '{member}' required by {proto_name}")
            continue
        # Allow None when the protocol declared the attribute as Optional
        # (presence of a class-level default value of None makes the slot
        # explicitly optional).
        if getattr(instance, member, None) is None and proto_vars.get(member) is not None:
            errors.append(f"attribute '{member}' is None but {proto_name} requires a value")

    callable_members = [
        name
        for name in dir(protocol)
        if not name.startswith("_") and callable(getattr(protocol, name, None))
    ]
    for name in callable_members:
        proto_attr = getattr(protocol, name)
        if not hasattr(instance, name):
            errors.append(f"missing method '{name}' required by {proto_name}")
            continue
        impl = getattr(instance, name)
        if not callable(impl):
            errors.append(f"member '{name}' on {proto_name} must be callable")
            continue

        # Protocols declare async methods with `async def ... ...`; even
        # with the `...` body, those are coroutine functions at runtime, so
        # we can use them as the source of truth for async-ness.
        if inspect.iscoroutinefunction(proto_attr) and not inspect.iscoroutinefunction(impl):
            errors.append(
                f"method '{name}' on {proto_name} must be async (declare with `async def`)"
            )

        try:
            proto_sig = inspect.signature(proto_attr)
            impl_sig = inspect.signature(impl)
        except (TypeError, ValueError):
            # Builtins / C-implemented callables don't expose signatures.
            # Skip the parameter check rather than guess.
            continue

        proto_params = [p for p in proto_sig.parameters.values() if p.name != "self"]
        impl_params = list(impl_sig.parameters.values())
        impl_accepts_var = any(
            p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for p in impl_params
        )
        impl_positional_names = [
            p.name
            for p in impl_params
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ]
        impl_required = [
            p
            for p in impl_params
            if p.default is inspect.Parameter.empty
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
        ]
        if not impl_accepts_var:
            if len(impl_required) > len(proto_params):
                errors.append(
                    f"method '{name}' on {proto_name} requires "
                    f"{len(impl_required)} args but the protocol provides "
                    f"{len(proto_params)}"
                )
            elif len(impl_positional_names) < len(proto_params):
                errors.append(
                    f"method '{name}' on {proto_name} accepts "
                    f"{len(impl_positional_names)} args but the protocol "
                    f"declares {len(proto_params)}"
                )

        # Each protocol-declared positional name must be addressable on
        # the implementation — either as a named param or absorbed by
        # **kwargs. Renames (e.g. `request` → `payload`) trip this.
        impl_has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in impl_params)
        if not impl_has_var_kw:
            impl_names = set(impl_positional_names)
            for proto_param in proto_params:
                if proto_param.name not in impl_names:
                    errors.append(
                        f"method '{name}' on {proto_name} is missing parameter '{proto_param.name}'"
                    )

    return not errors, errors


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
