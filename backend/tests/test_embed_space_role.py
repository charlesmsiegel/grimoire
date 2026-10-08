"""`embed_space.resolve` asks the Embedding role which endpoint and model it is.

The behaviour after that answer (connection read, kind rule, base URL check, the
cache namespace) is unchanged and held by `test_inference_equivalence.py`; these
tests hold only the new seam: where the pair comes from, and that nothing but
the embedding provider's connection is read.
"""

from __future__ import annotations

import httpx
import pytest

from grimoire.embeddings import EmbeddingsClient
from grimoire.store import config, embed_space, llm_connections
from grimoire.store.inference import providers, translate


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


# --- OpenRouter serves the Embedding role, in the current layout only --------

def _router(key: str = "sk-or-fake") -> str:
    return llm_connections.create_connection(
        "openrouter", "Router", api_key=key, model="", post_process="none")


def _current(conn: str) -> dict:
    return {"inference_format": "2", "role_embedding_provider": conn,
            "role_embedding_model": "vec-small"}


def test_openrouter_embeds_in_a_current_config():
    conn = _router()
    out = embed_space.resolve(_current(conn))
    rev = llm_connections.read_connection_raw(conn)["rev"]
    assert out == {"model": "vec-small", "base_url": providers.PRESETS["openrouter"].base_url,
                   "key": "sk-or-fake", "space": f"{conn}\0{rev}\0vec-small"}


def test_a_keyless_openrouter_connection_does_not_embed():
    conn = _router(key="")
    assert embed_space.resolve(_current(conn)) is None


def test_a_legacy_config_naming_openrouter_still_resolves_to_none():
    conn = _router()
    assert embed_space.resolve({"embeddings_connection_id": conn,
                                "embeddings_model": "vec-small"}) is None


def test_the_request_goes_to_the_openrouter_embeddings_route():
    conn = _router()
    got = embed_space.resolve(_current(conn))
    assert got is not None
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.25]}]})

    client = EmbeddingsClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    vectors = client.embed(["a line"], got["model"], got["key"], got["base_url"])
    assert vectors == [[0.5, 0.25]]
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/embeddings"
    assert seen[0].headers["Authorization"] == "Bearer sk-or-fake"
