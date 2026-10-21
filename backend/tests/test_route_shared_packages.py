"""Replacing the probe client changes the client closed by the route facade."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from grimoire.routes import config
from grimoire.routes.config import _probes


def test_probe_client_replacement_is_readable_and_closed_through_facade(monkeypatch):
    replacement = SimpleNamespace(close=Mock())
    monkeypatch.setattr(_probes, "_EMBEDDINGS", replacement)

    assert config._EMBEDDINGS is replacement
    config.close_clients()

    replacement.close.assert_called_once_with()
    with pytest.raises(AttributeError):
        _ = config._unknown_probe_client
