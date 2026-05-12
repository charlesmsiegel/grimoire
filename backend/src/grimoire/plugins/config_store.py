"""Per-plugin config persistence.

Spec 15 §Per-plugin configuration: each plugin owns
`data/config/plugins/<plugin-id>.yaml`. Secrets (fields whose property
declares `secret: true` in the manifest's `config_schema`) prefer the OS
keyring; when keyring is unavailable they fall back to plaintext on disk
with a logged warning so users can act on the lower trust posture.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from grimoire.files.yaml_io import dump_yaml, load_yaml, write_yaml

logger = logging.getLogger(__name__)

_REDACTED = "***"


try:  # pragma: no cover — exercised when the optional dep is installed
    import keyring as _keyring_module  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    _keyring_module = None


class KeyringBackend:
    """Adapter over the optional `keyring` dependency.

    Tests substitute an in-memory implementation. The default delegates to
    the system keyring when available; otherwise every operation reports
    `available=False` and callers fall back to plaintext.
    """

    def __init__(self, service: str) -> None:
        self.service = service

    @property
    def available(self) -> bool:
        return _keyring_module is not None

    def get(self, plugin_id: str, key: str) -> str | None:
        if _keyring_module is None:
            return None
        try:
            return _keyring_module.get_password(self.service, f"{plugin_id}:{key}")
        except Exception as exc:  # pragma: no cover — keyring backends vary
            logger.warning("keyring get failed for %s/%s: %r", plugin_id, key, exc)
            return None

    def set(self, plugin_id: str, key: str, value: str) -> bool:
        if _keyring_module is None:
            return False
        try:
            _keyring_module.set_password(self.service, f"{plugin_id}:{key}", value)
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning("keyring set failed for %s/%s: %r", plugin_id, key, exc)
            return False

    def delete(self, plugin_id: str, key: str) -> None:
        if _keyring_module is None:
            return
        with contextlib.suppress(Exception):  # pragma: no cover — missing entry, etc.
            _keyring_module.delete_password(self.service, f"{plugin_id}:{key}")


class InMemoryKeyring(KeyringBackend):
    """Test-friendly keyring that lives in memory only."""

    def __init__(self, service: str = "test") -> None:
        super().__init__(service)
        self._store: dict[tuple[str, str], str] = {}

    @property
    def available(self) -> bool:
        return True

    def get(self, plugin_id: str, key: str) -> str | None:
        return self._store.get((plugin_id, key))

    def set(self, plugin_id: str, key: str, value: str) -> bool:
        self._store[(plugin_id, key)] = value
        return True

    def delete(self, plugin_id: str, key: str) -> None:
        self._store.pop((plugin_id, key), None)


def secret_property_names(config_schema: dict[str, Any]) -> set[str]:
    """Names of top-level properties marked `secret: true`."""
    props = config_schema.get("properties") if isinstance(config_schema, dict) else None
    if not isinstance(props, dict):
        return set()
    return {name for name, prop in props.items() if isinstance(prop, dict) and prop.get("secret")}


class PluginConfigStore:
    """Read/write a single plugin's config file with secret separation."""

    def __init__(
        self,
        root: Path,
        keyring_backend: KeyringBackend | None = None,
        *,
        encrypt_secrets: bool = True,
    ) -> None:
        self.root = root
        self._keyring = keyring_backend
        self._encrypt_secrets = encrypt_secrets

    def path_for(self, plugin_id: str) -> Path:
        return self.root / f"{plugin_id}.yaml"

    def exists(self, plugin_id: str) -> bool:
        return self.path_for(plugin_id).is_file()

    def load(self, plugin_id: str, config_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self.path_for(plugin_id)
        if not path.is_file():
            return {}
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"config for plugin {plugin_id!r} must be a YAML mapping")
        merged: dict[str, Any] = dict(raw)
        secrets = secret_property_names(config_schema or {})
        if self._keyring is not None and secrets:
            for name in secrets:
                if merged.get(name) in (None, "", _REDACTED):
                    stored = self._keyring.get(plugin_id, name)
                    if stored is not None:
                        merged[name] = stored
        return merged

    def save(
        self,
        plugin_id: str,
        config: dict[str, Any],
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        path = self.path_for(plugin_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        secrets = secret_property_names(config_schema or {})
        on_disk = dict(config)
        if secrets:
            if self._encrypt_secrets and self._keyring is not None and self._keyring.available:
                for name in secrets:
                    value = config.get(name)
                    if value in (None, ""):
                        self._keyring.delete(plugin_id, name)
                        on_disk.pop(name, None)
                    else:
                        self._keyring.set(plugin_id, name, str(value))
                        on_disk[name] = _REDACTED
            else:
                logger.warning(
                    "plugin %s: storing secret fields %s in plaintext (keyring unavailable)",
                    plugin_id,
                    sorted(secrets),
                )
        path.write_text(dump_yaml(on_disk) if on_disk else "", encoding="utf-8")

    def delete(self, plugin_id: str, config_schema: dict[str, Any] | None = None) -> None:
        path = self.path_for(plugin_id)
        if path.is_file():
            path.unlink()
        if self._keyring is not None:
            for name in secret_property_names(config_schema or {}):
                self._keyring.delete(plugin_id, name)


__all__ = [
    "InMemoryKeyring",
    "KeyringBackend",
    "PluginConfigStore",
    "secret_property_names",
    "write_yaml",
]
