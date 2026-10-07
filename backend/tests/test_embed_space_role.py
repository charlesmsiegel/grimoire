"""`embed_space.resolve` asks the Embedding role which endpoint and model it is.

The behaviour after that answer (connection read, kind rule, base URL check, the
cache namespace) is unchanged and held by `test_inference_equivalence.py`; these
tests hold only the new seam: where the pair comes from, and that nothing but
the embedding provider's connection is read.
"""

from __future__ import annotations

import pytest

from grimoire.store import config, embed_space, llm_connections
from grimoire.store.inference import translate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _local() -> str:
    return llm_connections.create_connection(
        "openai_compatible", "Local", base_url="https://vectors.example/v1",
        api_key="sk-x", model="", post_process="none")


def test_the_embedding_role_is_what_resolves(monkeypatch):
    conn = _local()
    config.write_config(embeddings_connection_id="elsewhere", embeddings_model="legacy")
    monkeypatch.setattr(translate, "embedding_role", lambda cfg: (conn, "m2"))
    out = embed_space.resolve()
    assert out is not None
    assert out["model"] == "m2"


def test_a_format_two_config_resolves_from_role_keys():
    conn = _local()
    out = embed_space.resolve({"inference_format": "2",
                               "role_embedding_provider": conn,
                               "role_embedding_model": "m"})
    rev = llm_connections.read_connection_raw(conn)["rev"]
    assert out is not None
    assert out["model"] == "m"
    assert out["space"] == f"{conn}\0{rev}\0m"


def test_other_connections_are_never_read(monkeypatch):
    conn = _local()
    llm_connections.create_connection("openai_compatible", "Spare",
                                      base_url="https://other.example/v1",
                                      api_key="sk-y", model="", post_process="none")
    seen: list[str] = []
    real = llm_connections.read_connection_raw

    def spy(conn_id: str) -> dict:
        seen.append(conn_id)
        return real(conn_id)

    monkeypatch.setattr(llm_connections, "read_connection_raw", spy)
    embed_space.resolve({"inference_format": "2", "role_embedding_provider": conn,
                         "role_embedding_model": "m", "role_primary_provider": "spare"})
    assert seen == [conn]
