"""Tests for the per-plugin config store + keyring."""

from __future__ import annotations

from pathlib import Path

import yaml

from grimoire.plugins.config_store import (
    InMemoryKeyring,
    PluginConfigStore,
    secret_property_names,
)

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string", "secret": True},
        "base_url": {"type": "string"},
    },
    "required": ["api_key"],
}


def test_secret_property_names_picks_up_secret_flag() -> None:
    assert secret_property_names(SCHEMA) == {"api_key"}


def test_save_and_load_round_trip(config_root: Path) -> None:
    store = PluginConfigStore(config_root, keyring_backend=InMemoryKeyring())
    store.save("alpha", {"api_key": "sk-xxx", "base_url": "https://api"}, SCHEMA)

    loaded = store.load("alpha", SCHEMA)
    assert loaded == {"api_key": "sk-xxx", "base_url": "https://api"}


def test_save_redacts_secrets_in_yaml(config_root: Path) -> None:
    keyring = InMemoryKeyring()
    store = PluginConfigStore(config_root, keyring_backend=keyring)
    store.save("alpha", {"api_key": "sk-xxx", "base_url": "https://api"}, SCHEMA)

    raw_text = (config_root / "alpha.yaml").read_text(encoding="utf-8")
    on_disk = yaml.safe_load(raw_text)
    assert on_disk["api_key"] == "***"
    assert on_disk["base_url"] == "https://api"
    assert keyring.get("alpha", "api_key") == "sk-xxx"


def test_load_recovers_secret_from_keyring(config_root: Path) -> None:
    keyring = InMemoryKeyring()
    store = PluginConfigStore(config_root, keyring_backend=keyring)
    store.save("alpha", {"api_key": "sk-xxx"}, SCHEMA)

    fresh = PluginConfigStore(config_root, keyring_backend=keyring)
    assert fresh.load("alpha", SCHEMA)["api_key"] == "sk-xxx"


def test_save_falls_back_to_plaintext_when_keyring_disabled(config_root: Path) -> None:
    keyring = InMemoryKeyring()
    store = PluginConfigStore(config_root, keyring_backend=keyring, encrypt_secrets=False)
    store.save("alpha", {"api_key": "sk-xxx"}, SCHEMA)

    on_disk = yaml.safe_load((config_root / "alpha.yaml").read_text())
    assert on_disk == {"api_key": "sk-xxx"}
    # keyring should not be used when encryption is disabled
    assert keyring.get("alpha", "api_key") is None


def test_load_missing_file_returns_empty(config_root: Path) -> None:
    store = PluginConfigStore(config_root, keyring_backend=InMemoryKeyring())
    assert store.load("nope", SCHEMA) == {}


def test_delete_removes_file_and_secrets(config_root: Path) -> None:
    keyring = InMemoryKeyring()
    store = PluginConfigStore(config_root, keyring_backend=keyring)
    store.save("alpha", {"api_key": "sk-xxx"}, SCHEMA)
    assert store.exists("alpha")

    store.delete("alpha", SCHEMA)
    assert not store.exists("alpha")
    assert keyring.get("alpha", "api_key") is None


def test_clearing_secret_value_removes_from_keyring(config_root: Path) -> None:
    keyring = InMemoryKeyring()
    store = PluginConfigStore(config_root, keyring_backend=keyring)
    store.save("alpha", {"api_key": "sk-xxx"}, SCHEMA)
    store.save("alpha", {"api_key": ""}, SCHEMA)
    assert keyring.get("alpha", "api_key") is None
