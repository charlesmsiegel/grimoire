"""Vector setup shared by the continuity identity, reconcile and similarity
suites: an embedding connection, and a fake standing in for the client
continuity similarity embeds through."""

import pytest

from grimoire.store import config, llm_connections
from grimoire.store.continuity import similarity
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeEmbeddings


@pytest.fixture
def fake(monkeypatch):
    """The `FakeEmbeddings` installed as continuity similarity's client.
    Imported by name (`from tests.continuity_fixtures import fake as fake`)."""
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    return double


def configure_vectors(**config_fields) -> None:
    """Embeddings on: an OpenAI-compatible "Vectors" connection holding the
    Embedding role at `embed-1`. Any `config_fields` are written to the config
    after the connection exists and before the role is set."""
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    if config_fields:
        config.write_config(**config_fields)
    embedding(conn, "embed-1")
