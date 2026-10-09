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
from grimoire.store import inference_keys as keys
from grimoire.store.inference import facts, providers
from grimoire.store.inference import resolve as inference_resolve


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _local() -> str:
    return llm_connections.create_connection(
        "openai_compatible", "Local", base_url="https://vectors.example/v1",
        api_key="sk-x", model="", post_process="none")


def test_the_embedding_role_is_what_resolves(monkeypatch):
    conn = _local()
    config.write_config(**{keys.role_key("embedding", "provider"): "elsewhere",
                           keys.role_key("embedding", "model"): "stored"})
    # `resolve.embedding` reads the role through `_embedding_view` (slice D;
    # the format-2 keys of the settings the planner overlays, slice I).
    monkeypatch.setattr(inference_resolve, "_embedding_view", lambda cfg: {
        keys.role_key("embedding", "provider"): conn,
        keys.role_key("embedding", "model"): "m2"})
    out = embed_space.resolve()
    assert out is not None
    assert out["model"] == "m2"


def test_a_format_two_store_ignores_the_legacy_embedding_keys():
    """At format 2 the role keys are the only answer: `embeddings_*` naming a
    working endpoint embeds nothing while the role is empty, and does not
    move the model once the role names one."""
    conn = _local()
    config.write_config(embeddings_connection_id=conn, embeddings_model="legacy")
    assert embed_space.resolve() is None
    config.write_config(**{keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): "m"})
    out = embed_space.resolve()
    assert out is not None
    assert out["model"] == "m"


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
    """By the embedding resolution. (On a store not yet retired the planner
    reads the settings' other slots too, for their derived presets; a retired
    one -- every store from slice I's retirement on -- reads nothing there.)"""
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
    embed_space.resolve({"inference_format": "2", keys.RETIRED_KEY: "1",
                         "role_embedding_provider": conn,
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


# --- A provider KNOWN not to embed is no space -------------------------------
# The same rule slice D's `resolve.embedding` keeps: a known `no` for `embed`
# (adapter, the preset's `never`, the user's override, the catalog -- never
# the name rule's guess) turns embedding off exactly as an unset role does.

def _zai() -> str:
    return llm_connections.create_connection(
        "openai_compatible", "Winifred Zai", base_url="https://api.z.ai/api/paas/v4",
        api_key="sk-fake", model="", post_process="none")


def _role(conn: str, model: str = "m") -> dict:
    return {"inference_format": "2", "role_embedding_provider": conn,
            "role_embedding_model": model}


def _rev(conn: str) -> str:
    return llm_connections.read_connection_raw(conn)["rev"]


def test_a_provider_whose_preset_never_embeds_is_no_space():
    conn = _zai()
    assert embed_space.resolve(_role(conn)) is None
    config.write_config(**_role(conn))
    assert embed_space.resolve() is None
    # The legacy layout reads the same rule.
    assert embed_space.resolve({"embeddings_connection_id": conn,
                                "embeddings_model": "m"}) is None


def test_a_users_no_and_a_catalogs_no_turn_embedding_off():
    conn = _local()
    assert embed_space.resolve(_role(conn)) is not None   # unknown still embeds
    llm_connections.set_cached_models(conn, [{"id": "m", "outputs": ["text"]}], _rev(conn))
    assert embed_space.resolve(_role(conn)) is None
    other = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Vectors", base_url="https://other.example/v1",
        api_key="sk-fake", model="", post_process="none")
    facts.set_overrides(other, "m", {"embed": "no"})
    assert embed_space.resolve(_role(other)) is None


def _edited(conn: str, **fields) -> tuple[dict, dict]:
    before = llm_connections.read_connection_raw(conn)
    return before, {**before, **fields, "rev": "next-rev"}


def test_a_provider_that_cannot_embed_never_asks_to_confirm_a_move():
    zai = _zai()
    assert embed_space.moved_by(_role(zai), *_edited(zai, api_key="sk-fake-2")) is False
    # Leaving the known `no` for an address that embeds is a move.
    assert embed_space.moved_by(
        _role(zai), *_edited(zai, base_url="https://vectors.example/v1")) is True


def test_moved_by_judges_the_provider_as_it_reads_after_the_save():
    """A rev restamp leaves the cached catalog row stale, so its `no` does not
    survive the save: the edit lands with embedding ON, and asks first."""
    conn = _local()
    llm_connections.set_cached_models(conn, [{"id": "m", "outputs": ["text"]}], _rev(conn))
    config.write_config(**_role(conn))
    assert embed_space.resolve() is None
    said: list[bool] = []
    llm_connections.update_connection(
        conn, guard=lambda before, after: said.append(
            embed_space.moved_by(config.read_config(), before, after)), api_key="sk-fake-2")
    assert embed_space.resolve() is not None
    assert said == [True]


def test_facts_moved_is_off_to_on_only():
    """A user's `embed: yes` over the catalog's `no` turns the role on (a
    move, asked first); taking it back turns it off, which re-embeds nothing."""
    conn = _local()
    llm_connections.set_cached_models(conn, [{"id": "m", "outputs": ["text"]}], _rev(conn))
    cfg = _role(conn)
    before = facts.of(conn, "m", _rev(conn))
    after = {**before, "overrides": {"embed": "yes"}}
    assert embed_space.facts_moved(cfg, conn, "m", before, after) is True
    assert embed_space.facts_moved(cfg, conn, "m", after, before) is False
    # Another model, or a role naming another provider, moves nothing.
    assert embed_space.facts_moved(cfg, conn, "m2", before, after) is False
    assert embed_space.facts_moved(_role(_zai()), conn, "m", before, after) is False
