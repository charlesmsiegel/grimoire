"""Suite-wide privacy guard: no test may reach a real home or Grimoire library."""

import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

#: CI shards (docs/TESTING.md#ci-gate): the crash and fault explorations (files named ``*recovery*``
#: or ``*faults*``) run in three shards: ``GRIMOIRE_TEST_SHARD=deletion-explorations`` for the store's
#: deletion explorations (also named ``*store*`` and ``*deletion*``), ``store-explorations`` for the
#: other store-backed ones (named ``*store*``) and ``explorations`` for the rest. ``peersync`` runs the
#: two-device transfer files (named ``*peersync*``), ``sync`` the other replica-sync files (named
#: ``*sync*``), ``stores`` the other store files (named ``*store*``), ``scenes`` the other scene files
#: (named ``*scene*``), ``app`` the browser app's files (named ``test_app*``, or ``*_page*`` for a page's own
#: tests), and ``rest`` every other file.
#: Each file belongs to exactly one shard by its name, so together they always run the whole suite;
#: unset runs everything. Several shards joined by ``+`` run in one job (macOS runs the suite in two).
SHARDS = ("store-explorations", "deletion-explorations", "explorations", "sync", "peersync", "stores", "scenes",
          "app", "rest")


def is_exploration(name: str) -> bool:
    return "recovery" in name or "faults" in name


def shard_of(name: str) -> str:
    """The one shard a test file runs in; explorations win, so ``test_sync_faults.py`` is one, and a
    store-backed exploration such as ``test_store_recovery.py`` runs in ``store-explorations`` (or
    ``deletion-explorations`` when it is also named for deletion). Sync wins over store, so a file
    named for both stays with the replica-sync files, and ``peersync`` is the sync files' own split. Store
    wins over scene, so ``test_scenestore.py`` would stay with the store files, and each of those wins over
    app, so ``test_app_play_story.py`` is an app file and an app file named for a store would be a store's."""
    if is_exploration(name):
        if "store" in name:
            return "deletion-explorations" if "deletion" in name else "store-explorations"
        return "explorations"
    if "sync" in name:
        return "peersync" if "peersync" in name else "sync"
    if "store" in name:
        return "stores"
    if "scene" in name:
        return "scenes"
    return "app" if name.startswith("test_app") or "_page" in name else "rest"


def asked_shards() -> tuple[str, ...]:
    """The shards ``GRIMOIRE_TEST_SHARD`` asks for: one, or several joined by ``+``; none when it is unset."""
    return tuple(part for part in os.environ.get("GRIMOIRE_TEST_SHARD", "").split("+") if part)


def pytest_configure(config):
    asked = asked_shards()
    if any(shard not in SHARDS for shard in asked) or len(set(asked)) != len(asked) \
            or (os.environ.get("GRIMOIRE_TEST_SHARD") and not asked):
        raise pytest.UsageError(f"GRIMOIRE_TEST_SHARD must be one of {SHARDS}, several joined by '+', or unset, "
                                f"not {os.environ.get('GRIMOIRE_TEST_SHARD')!r}")


def pytest_ignore_collect(collection_path: Path, config):
    asked = asked_shards()
    if not asked or collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return None
    return None if shard_of(collection_path.name) in asked else True


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
