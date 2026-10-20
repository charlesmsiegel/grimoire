"""Shared route resources keep their single defining owner through the facade."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from grimoire import routes
from grimoire.routes import common, config, streaming
from grimoire.routes.common import _clients, _inference
from grimoire.routes.config import _probes
from grimoire.routes.streaming import _state


def test_shared_claim_objects_and_inference_type_have_one_owner():
    assert streaming._turn_tokens is _state._turn_tokens
    assert streaming._turn_seq is _state._turn_seq
    assert streaming.StreamOutcome is _state.StreamOutcome
    assert common.UsableInference is _inference.UsableInference


def test_dependency_aliases_are_the_original_client_reader_objects():
    assert routes.get_llm is common.get_llm is _clients.get_llm
    assert routes.get_embeddings is common.get_embeddings is _clients.get_embeddings
    assert common.get_health is _clients.get_health


def test_probe_client_replacement_is_readable_and_closed_through_facade(monkeypatch):
    replacement = SimpleNamespace(close=Mock())
    monkeypatch.setattr(_probes, "_EMBEDDINGS", replacement)

    assert config._EMBEDDINGS is replacement
    config.close_clients()

    replacement.close.assert_called_once_with()
    with pytest.raises(AttributeError):
        _ = config._unknown_probe_client
