"""Suite-wide privacy guard: no test may reach a real home or Grimoire library."""

import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

#: CI shards (docs/TESTING.md#ci-gate): the crash and fault explorations (files named ``*recovery*``
#: or ``*faults*``) run in two shards, ``GRIMOIRE_TEST_SHARD=store-explorations`` for the store-backed
#: ones (also named ``*store*``) and ``explorations`` for the others; ``sync`` runs the other
#: replica-sync files (named ``*sync*``), ``stores`` the other store files (named ``*store*``), and
#: ``rest`` runs every other file. Each file belongs to exactly one shard by its name, so together
#: they always run the whole suite; unset runs everything.
SHARDS = ("store-explorations", "explorations", "sync", "stores", "rest")


def is_exploration(name: str) -> bool:
    return "recovery" in name or "faults" in name


def shard_of(name: str) -> str:
    """The one shard a test file runs in; explorations win, so ``test_sync_faults.py`` is one, and a
    store-backed exploration such as ``test_store_recovery.py`` runs in ``store-explorations``. Sync
    wins over store, so a file named for both stays with the replica-sync files."""
    if is_exploration(name):
        return "store-explorations" if "store" in name else "explorations"
    if "sync" in name:
        return "sync"
    return "stores" if "store" in name else "rest"


def pytest_configure(config):
    shard = os.environ.get("GRIMOIRE_TEST_SHARD", "")
    if shard not in ("", *SHARDS):
        raise pytest.UsageError(f"GRIMOIRE_TEST_SHARD must be one of {SHARDS} or unset, not {shard!r}")


def pytest_ignore_collect(collection_path: Path, config):
    shard = os.environ.get("GRIMOIRE_TEST_SHARD", "")
    if not shard or collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return None
    return None if shard_of(collection_path.name) == shard else True


def pytest_report_header(config):
    shard = os.environ.get("GRIMOIRE_TEST_SHARD", "")
    return f"grimoire test shard: {shard}" if shard else None

_REDIRECTED = ("HOME", "USERPROFILE", "GRIMOIRE_HOME", "GRIMOIRE_V2_HOME")


@pytest.fixture(autouse=True)
def _private_home_sentinel(tmp_path_factory, monkeypatch):
    """Point home and library variables at empty sentinels and fail if one is written."""
    sentinels = {}
    # Windows falls back to HOMEDRIVE+HOMEPATH when USERPROFILE is unset, so a test
    # that deletes USERPROFILE must not reach the real profile through them.
    for variable in ("HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(variable, raising=False)
    for variable in _REDIRECTED:
        sentinel = tmp_path_factory.mktemp(f"sentinel-{variable.lower()}")
        monkeypatch.setenv(variable, str(sentinel))
        sentinels[variable] = sentinel
    yield
    dirty = {variable: sorted(os.listdir(path)) for variable, path in sentinels.items() if os.listdir(path)}
    if dirty:
        pytest.fail(f"test wrote into a redirected home or library directory: {dirty}")
