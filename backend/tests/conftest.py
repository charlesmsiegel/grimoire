"""Suite-wide privacy guard: no test may reach a real home or Grimoire library."""

import os

import pytest

pytest_plugins = ["pytester"]

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
