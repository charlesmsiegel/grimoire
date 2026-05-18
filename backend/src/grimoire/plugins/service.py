"""Plugin lifecycle service.

`PluginsService` is the default implementation of the `Plugins` protocol
declared in `grimoire.types.protocols`. It composes discovery, loading,
registries, and the config store into the small surface the rest of the
app talks to (LLM Gateway, ImageGen, Export, the Frontend's Installed
Plugins view).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.event_bus import Event as BusEvent
from grimoire.event_bus import EventBus
from grimoire.plugins.config import PluginsConfig
from grimoire.plugins.config_store import (
    ActivationStore,
    KeyringBackend,
    PluginConfigStore,
    secret_property_names,
)
from grimoire.plugins.discovery import DiscoveryError, discover
from grimoire.plugins.loader import LoadResult, load_plugin
from grimoire.plugins.registry import PluginRegistry
from grimoire.types.common import HealthLevel, HealthStatus, PluginId
from grimoire.types.orchestrator import EventType
from grimoire.types.plugins import (
    PluginKind,
    PluginLifecycle,
    PluginManifest,
    PluginStatus,
    RescanReport,
)
from grimoire.types.protocols import (
    EmbeddingProvider,
    ExportAdapter,
    ImageGenBackend,
    LLMProvider,
)
from grimoire.validation.errors import ValidationResult
from grimoire.validation.validator import validate_config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PluginRecord:
    """In-memory bookkeeping for one plugin between rescans.

    `instances` mirrors the entries pushed into the registry so reload can
    reuse them without re-importing when only the config changed; today we
    always rebuild on rescan, but the structure leaves room.
    """

    manifest: PluginManifest
    lifecycle: PluginLifecycle
    bundled: bool
    instances: dict[PluginKind, Any] = field(default_factory=dict)
    last_error: str | None = None
    last_health: HealthStatus | None = None


class PluginsService:
    def __init__(
        self,
        config: PluginsConfig,
        *,
        keyring_backend: KeyringBackend | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._config = config
        self._registry = PluginRegistry()
        self._records: dict[PluginId, _PluginRecord] = {}
        self._failed: dict[PluginId, list[str]] = {}
        self._discovery_errors: list[DiscoveryError] = []
        self._config_store = PluginConfigStore(
            config.config_store.root,
            keyring_backend=keyring_backend,
            encrypt_secrets=config.config_store.encrypt_secrets_via_keyring,
        )
        # Persisted activation flags. A plugin the user explicitly
        # deactivated stays deactivated across app restarts; absence in
        # the file means "active by default".
        self._activations = ActivationStore(config.config_store.root)
        # When None, emit becomes a no-op so test harnesses that don't
        # care about events stay terse.
        self._event_bus = event_bus

    # ------------------------------------------------------------------ #
    # Discovery / lifecycle
    # ------------------------------------------------------------------ #

    async def rescan(self) -> RescanReport:
        """Re-discover plugins on disk and (re-)load them.

        Plugins that were registered before the rescan but are no longer
        found on disk are unregistered. Plugins that fail validation or
        import are recorded in `failed` with an explanation.
        """
        previous_ids = set(self._records)
        previous_failed = set(self._failed)
        discovered, errors = discover(
            roots=[self._config.root],
            bundled_roots=[self._config.bundled_root] if self._config.bundled_root else None,
        )
        self._discovery_errors = errors

        seen: set[PluginId] = set()
        loaded_ids: list[PluginId] = []
        failed: list[tuple[PluginId, str]] = []
        new_failed: dict[PluginId, list[str]] = {}

        for discovery_error in errors:
            failed.append((str(discovery_error.plugin_dir.name), discovery_error.message))

        for d in discovered:
            plugin_id = d.raw_manifest.get("id") if isinstance(d.raw_manifest, dict) else None
            if not isinstance(plugin_id, str):
                plugin_id = d.plugin_dir.name
            seen.add(plugin_id)

            config_dict = self._load_config_for_load(plugin_id, d.raw_manifest)
            result = load_plugin(d, config_dict)
            if result.ok and result.manifest is not None:
                self._unregister_record(plugin_id)
                await self._install(result)
                loaded_ids.append(plugin_id)
            else:
                self._unregister_record(plugin_id)
                reason = "; ".join(result.errors) or "unknown load error"
                failed.append((plugin_id, reason))
                new_failed[plugin_id] = list(result.errors)
                await self._emit(
                    EventType.PLUGIN_FAILED,
                    {"plugin_id": plugin_id, "errors": list(result.errors)},
                )

        removed = sorted((previous_ids | previous_failed) - seen)
        for plugin_id in removed:
            self._unregister_record(plugin_id)
            self._failed.pop(plugin_id, None)
            await self._emit(EventType.PLUGIN_UNLOADED, {"plugin_id": plugin_id})

        self._failed = new_failed

        return RescanReport(
            discovered=[d.raw_manifest.get("id", d.plugin_dir.name) for d in discovered],
            loaded=loaded_ids,
            failed=failed,
            removed=removed,
        )

    async def _install(self, result: LoadResult) -> None:
        assert result.manifest is not None
        manifest = result.manifest
        plugin_id = manifest.id

        config_dict = self._load_config_for_load(plugin_id, manifest.raw)
        validation = self._validate_config_against(manifest, config_dict)
        has_config_file = self._config_store.exists(plugin_id)
        configured = validation.ok and (
            has_config_file or not _has_required(manifest.config_schema)
        )

        # Persisted activation state wins over the computed default: a
        # plugin the user deactivated stays deactivated even when its
        # config is otherwise valid.
        persisted_active = self._activations.is_active(plugin_id)
        if not persisted_active:
            lifecycle = PluginLifecycle.DEACTIVATED
        else:
            lifecycle = PluginLifecycle.ACTIVE if configured else PluginLifecycle.LOADED

        record = _PluginRecord(
            manifest=manifest,
            lifecycle=lifecycle,
            bundled=result.bundled,
        )
        # Keep the instances on the record so a later `activate()` can
        # re-register them without going through the loader again.
        for loaded in result.instances:
            record.instances[loaded.kind] = loaded.instance
            if lifecycle != PluginLifecycle.DEACTIVATED:
                self._registry.register(plugin_id, loaded.kind, loaded.instance)
        self._records[plugin_id] = record
        await self._emit(
            EventType.PLUGIN_LOADED,
            {
                "plugin_id": plugin_id,
                "manifest": manifest.model_dump(mode="json"),
                "bundled": result.bundled,
                "lifecycle": lifecycle.value,
            },
        )

    def _unregister_record(self, plugin_id: PluginId) -> None:
        if self._registry.has(plugin_id):
            self._registry.unregister_all(plugin_id)
        self._records.pop(plugin_id, None)

    async def load(self, plugin_id: PluginId) -> None:
        """Reload a single plugin without re-discovering every directory.

        Two callers want this:

        * the UI retry button after a plugin failed — we don't want to pay
          the import cost of every healthy plugin just to retry the one
          broken one;
        * :meth:`set_config`, which needs the live instance rebuilt with
          the fresh config (plugins commonly cache the API key on
          ``__init__``).

        Missing plugins are surfaced as ``KeyError`` so callers can decide
        whether to fall back to a full rescan or report 404.
        """
        discovered = self._discover_one(plugin_id)
        if discovered is None:
            raise KeyError(f"plugin {plugin_id!r} not found on disk")

        config_dict = self._load_config_for_load(plugin_id, discovered.raw_manifest)
        result = load_plugin(discovered, config_dict)
        # Whether the new load succeeded or not, drop any previous record
        # so a rebuild from a fresh start (e.g. fixing a syntax error in
        # plugin.py) doesn't leave the old broken instance live in the
        # registry.
        self._unregister_record(plugin_id)
        if result.ok and result.manifest is not None:
            self._failed.pop(plugin_id, None)
            await self._install(result)
        else:
            self._failed[plugin_id] = list(result.errors)
            await self._emit(
                EventType.PLUGIN_FAILED,
                {"plugin_id": plugin_id, "errors": list(result.errors)},
            )

    def _discover_one(self, plugin_id: PluginId):
        """Find a single plugin on disk by id.

        Walks the configured roots looking for a directory whose
        ``manifest.yaml`` declares the requested id. We compare on the
        manifest's declared id rather than the directory name so a plugin
        renamed on disk still resolves (the loader's id-mismatch check
        catches the discrepancy later if the user forgot to rename one of
        the two).
        """
        roots: list[Path] = [self._config.root]
        bundled = [self._config.bundled_root] if self._config.bundled_root else None
        discovered, _ = discover(roots=roots, bundled_roots=bundled)
        for d in discovered:
            raw_id = d.raw_manifest.get("id") if isinstance(d.raw_manifest, dict) else None
            candidate = raw_id if isinstance(raw_id, str) else d.plugin_dir.name
            if candidate == plugin_id:
                return d
        return None

    async def unload(self, plugin_id: PluginId) -> None:
        was_known = plugin_id in self._records or plugin_id in self._failed
        self._unregister_record(plugin_id)
        self._failed.pop(plugin_id, None)
        if was_known:
            await self._emit(EventType.PLUGIN_UNLOADED, {"plugin_id": plugin_id})

    async def activate(self, plugin_id: PluginId) -> None:
        record = self._records.get(plugin_id)
        if record is None:
            raise KeyError(f"plugin {plugin_id!r} not loaded")
        for kind, instance in record.instances.items():
            self._registry.register(plugin_id, kind, instance)
        record.lifecycle = PluginLifecycle.ACTIVE
        self._activations.set_active(plugin_id, True)
        await self._emit(EventType.PLUGIN_ACTIVATED, {"plugin_id": plugin_id})

    async def deactivate(self, plugin_id: PluginId) -> None:
        record = self._records.get(plugin_id)
        if record is None:
            return
        self._registry.unregister_all(plugin_id)
        record.lifecycle = PluginLifecycle.DEACTIVATED
        self._activations.set_active(plugin_id, False)
        await self._emit(EventType.PLUGIN_DEACTIVATED, {"plugin_id": plugin_id})

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    async def list_installed(self) -> list[PluginManifest]:
        return [r.manifest for r in self._records.values()]

    async def get_manifest(self, plugin_id: PluginId) -> PluginManifest | None:
        record = self._records.get(plugin_id)
        return record.manifest if record else None

    async def get_status(self, plugin_id: PluginId) -> PluginStatus:
        record = self._records.get(plugin_id)
        if record is None:
            errors = self._failed.get(plugin_id)
            if errors is not None:
                return PluginStatus(
                    id=plugin_id,
                    lifecycle=PluginLifecycle.FAILED,
                    error="; ".join(errors),
                )
            return PluginStatus(
                id=plugin_id,
                lifecycle=PluginLifecycle.UNLOADED,
            )
        return PluginStatus(
            id=plugin_id,
            lifecycle=record.lifecycle,
            health=record.last_health,
            error=record.last_error,
            config_present=self._config_store.exists(plugin_id),
        )

    def discovery_errors(self) -> list[DiscoveryError]:
        """Errors from the most recent discovery pass — useful for the UI."""
        return list(self._discovery_errors)

    def failed_plugins(self) -> dict[PluginId, list[str]]:
        return {pid: list(errs) for pid, errs in self._failed.items()}

    # ------------------------------------------------------------------ #
    # Per-kind registries
    # ------------------------------------------------------------------ #

    def llm_providers(self) -> list[LLMProvider]:
        return list(self._registry.list(PluginKind.LLM_PROVIDER))

    def embedding_providers(self) -> list[EmbeddingProvider]:
        return list(self._registry.list(PluginKind.EMBEDDING_PROVIDER))

    def imagegen_backends(self) -> list[ImageGenBackend]:
        return list(self._registry.list(PluginKind.IMAGEGEN_BACKEND))

    def export_adapters(self) -> list[ExportAdapter]:
        return list(self._registry.list(PluginKind.EXPORT_ADAPTER))

    def get_llm_provider(self, id: str) -> LLMProvider | None:
        return self._registry.get(id, PluginKind.LLM_PROVIDER)

    def get_embedding_provider(self, id: str) -> EmbeddingProvider | None:
        return self._registry.get(id, PluginKind.EMBEDDING_PROVIDER)

    def get_imagegen_backend(self, id: str) -> ImageGenBackend | None:
        return self._registry.get(id, PluginKind.IMAGEGEN_BACKEND)

    def get_export_adapter(self, id: str) -> ExportAdapter | None:
        return self._registry.get(id, PluginKind.EXPORT_ADAPTER)

    # ------------------------------------------------------------------ #
    # Config storage
    # ------------------------------------------------------------------ #

    async def get_config(self, plugin_id: PluginId) -> dict:
        manifest = await self.get_manifest(plugin_id)
        schema = manifest.config_schema if manifest else {}
        config = self._config_store.load(plugin_id, schema)
        if manifest is not None:
            self._inherit_shared_secrets(schema, manifest.shares_secrets_with, config)
        return config

    async def set_config(self, plugin_id: PluginId, config: dict) -> None:
        manifest = await self.get_manifest(plugin_id)
        if manifest is None:
            raise KeyError(f"plugin {plugin_id!r} not loaded")
        validation = self._validate_config_against(manifest, config)
        if not validation.ok:
            raise ValueError(
                "; ".join(e.message for e in validation.errors) or "invalid plugin config"
            )
        self._config_store.save(plugin_id, config, manifest.config_schema)
        # Rebuild the live instance against the fresh config. Most bundled
        # plugins cache the API key on ``__init__``; without this step the
        # user would have to hit "Rescan" before a new key took effect.
        # Falls back to the in-place LOADED→ACTIVE transition only when
        # the plugin can't be re-loaded (e.g. removed from disk between
        # the get_manifest call and now) so behavior stays predictable.
        try:
            await self.load(plugin_id)
        except KeyError:
            record = self._records.get(plugin_id)
            if record is not None and record.lifecycle == PluginLifecycle.LOADED:
                record.lifecycle = PluginLifecycle.ACTIVE

    async def validate_config(self, plugin_id: PluginId, config: dict) -> ValidationResult:
        manifest = await self.get_manifest(plugin_id)
        if manifest is None:
            return ValidationResult.success()
        return self._validate_config_against(manifest, config)

    def _validate_config_against(
        self, manifest: PluginManifest, config: dict[str, Any]
    ) -> ValidationResult:
        schema = manifest.config_schema or {}
        if not schema:
            return ValidationResult.success()
        return validate_config(config, schema)

    def _load_config_for_load(
        self, plugin_id: PluginId, raw_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        schema = (
            raw_manifest.get("config_schema") if isinstance(raw_manifest, dict) else None
        ) or {}
        try:
            config = self._config_store.load(plugin_id, schema)
        except Exception as exc:
            logger.warning("could not load config for plugin %s: %r", plugin_id, exc)
            return {}
        siblings = [
            str(x)
            for x in (
                raw_manifest.get("shares_secrets_with") if isinstance(raw_manifest, dict) else None
            )
            or []
        ]
        self._inherit_shared_secrets(schema, siblings, config)
        return config

    def _inherit_shared_secrets(
        self,
        schema: dict[str, Any],
        siblings: list[str],
        config: dict[str, Any],
    ) -> None:
        """Fill empty secret fields in ``config`` from listed sibling plugins.

        The keyring is keyed by ``(plugin_id, field_name)``, so we don't
        need the sibling's full schema to fetch its secrets — a synthetic
        schema covering only the field names we want is enough to make
        :meth:`PluginConfigStore.load` consult the keyring entries for the
        sibling. Sibling plugins don't need to be loaded yet.
        """
        if not siblings:
            return
        own_secrets = secret_property_names(schema or {})
        missing = [n for n in own_secrets if not config.get(n)]
        if not missing:
            return
        for sibling_id in siblings:
            fetch_schema = {
                "properties": {n: {"type": "string", "secret": True} for n in missing},
            }
            try:
                sibling_config = self._config_store.load(sibling_id, fetch_schema)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "could not read sibling config %s for shared secrets: %r",
                    sibling_id,
                    exc,
                )
                continue
            for name in list(missing):
                value = sibling_config.get(name)
                if value:
                    config[name] = value
            missing = [n for n in own_secrets if not config.get(n)]
            if not missing:
                return

    # ------------------------------------------------------------------ #
    # Health checks
    # ------------------------------------------------------------------ #

    async def health_check(self, plugin_id: PluginId) -> HealthStatus:
        record = self._records.get(plugin_id)
        if record is None:
            return HealthStatus(level=HealthLevel.UNCONFIGURED, target_id=plugin_id)
        previous = record.last_health
        if not record.instances:
            status = HealthStatus(level=HealthLevel.UNCONFIGURED, target_id=plugin_id)
        else:
            statuses: list[HealthStatus] = []
            for kind, instance in record.instances.items():
                target = f"{plugin_id}:{kind.value}"
                statuses.append(await self._probe_instance(instance, target))
            status = _aggregate_health(plugin_id, statuses)
        record.last_health = status
        # Only emit when the level transitions so subscribers that mirror
        # the level into a Prometheus gauge or a WebSocket frame don't see
        # a stream of "still HEALTHY" updates.
        previous_level = previous.level if previous is not None else None
        if previous_level != status.level:
            await self._emit(
                EventType.PLUGIN_HEALTH_CHANGED,
                {
                    "plugin_id": plugin_id,
                    "before": previous_level.value if previous_level is not None else None,
                    "after": status.level.value,
                    "message": status.message,
                },
            )
        return status

    async def health_check_all(self) -> dict[str, HealthStatus]:
        results: dict[str, HealthStatus] = {}
        for plugin_id in list(self._records):
            results[plugin_id] = await self.health_check(plugin_id)
        return results

    async def _probe_instance(self, instance: Any, target_id: str) -> HealthStatus:
        probe = getattr(instance, "health_check", None)
        if probe is None:
            return HealthStatus(level=HealthLevel.UNCONFIGURED, target_id=target_id)
        timeout = max(1, self._config.health.timeout_seconds)
        started = time.monotonic()
        try:
            result = probe()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=timeout)
        except TimeoutError:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=target_id,
                message=f"health check timed out after {timeout}s",
            )
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=target_id,
                message=f"health check raised: {exc!r}",
            )
        if isinstance(result, HealthStatus):
            return result
        # Tolerate plugins that return a plain string/enum/bool — treat any
        # non-HealthStatus reply as healthy and stamp a target id so the UI
        # can still locate the row.
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=target_id,
            message=f"completed in {time.monotonic() - started:.2f}s",
        )

    async def _emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        """Emit a plugin lifecycle event when an event bus is wired.

        Becomes a silent no-op when the service was constructed without
        ``event_bus``; the in-process test harness almost always omits it.
        """
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(BusEvent(type=event_type.value, payload=dict(payload)))
        except Exception:
            logger.exception("plugins: event_bus.emit raised for %s", event_type.value)


def _has_required(schema: dict[str, Any]) -> bool:
    if not isinstance(schema, dict):
        return False
    required = schema.get("required")
    return bool(required)


def _aggregate_health(plugin_id: PluginId, statuses: list[HealthStatus]) -> HealthStatus:
    if not statuses:
        return HealthStatus(level=HealthLevel.UNCONFIGURED, target_id=plugin_id)
    order = {
        HealthLevel.UNHEALTHY: 3,
        HealthLevel.DEGRADED: 2,
        HealthLevel.UNCONFIGURED: 1,
        HealthLevel.HEALTHY: 0,
    }
    worst = max(statuses, key=lambda s: order.get(s.level, 0))
    return HealthStatus(
        level=worst.level,
        target_id=plugin_id,
        message=worst.message,
    )


__all__ = ["PluginsService"]
