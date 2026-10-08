"""The Embedding role has one reader: `resolve.embedding` (slice D, Task 1).

It reads the role through the cascade (`translate.embedding_view`, then
`cascade.role_selection("embedding", campaign={})`), builds one attempt with
no fallback, says what that attempt is known not to do (`missing`), and names
the vector space it embeds in (`space_id`) only when it embeds. `embed_space`
answers from it: `endpoint` with the provider named for the ledger, and
`resolve` with the four keys it has always had.

Invented connection names and fake keys only.
"""

from __future__ import annotations

import pytest

from grimoire.store import config, embed_space, llm_connections, routing
from grimoire.store import inference_keys as keys
from grimoire.store.inference import cascade, facts, translate
from grimoire.store.inference import resolve as inference_resolve


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _local(name: str = "Local", base_url: str = "https://vectors.example/v1") -> str:
    return llm_connections.create_connection(
        "openai_compatible", name, base_url=base_url, api_key="sk-fake",
        model="", post_process="none")


def _current(conn: str, model: str = "m") -> dict:
    return {keys.FORMAT_KEY: "2",
            keys.role_key("embedding", "provider"): conn,
            keys.role_key("embedding", "model"): model}


def _rev(conn: str) -> str:
    return llm_connections.read_connection_raw(conn)["rev"]


def test_the_cascade_is_the_reader(monkeypatch):
    conn = _local()
    config.write_config(**_current(conn))
    calls: list[tuple[str, dict]] = []
    real = cascade.role_selection

    def spy(role, *, campaign, glob, exists):
        calls.append((role, campaign))
        return real(role, campaign=campaign, glob=glob, exists=exists)

    views: list[dict] = []
    real_view = translate.embedding_view

    def view_spy(cfg):
        views.append(cfg)
        return real_view(cfg)

    monkeypatch.setattr(cascade, "role_selection", spy)
    monkeypatch.setattr(translate, "embedding_view", view_spy)
    assert embed_space.resolve() is not None
    assert calls == [("embedding", {})]
    assert len(views) == 1


def test_space_id_is_todays_string():
    conn = _local()
    cfg = _current(conn)
    got = inference_resolve.embedding(cfg)
    space = embed_space.resolve(cfg)
    assert space is not None
    assert got.space_id == f"{conn}\0{_rev(conn)}\0m"
    assert got.space_id == space["space"]
    assert (got.role, got.via, got.operation, got.task) == ("embedding", "role", "embed", "")


def test_space_id_moves_with_rev_and_with_model():
    conn = _local()
    before = inference_resolve.embedding(_current(conn)).space_id
    assert before is not None

    llm_connections.update_connection(conn, api_key="sk-fake-2")
    rekeyed = inference_resolve.embedding(_current(conn)).space_id
    assert rekeyed not in (None, before)

    assert inference_resolve.embedding(_current(conn, "m2")).space_id not in (None, rekeyed)

    # `name` is rev-neutral: a rename moves nothing.
    llm_connections.update_connection(conn, name="Mara Vectors")
    assert inference_resolve.embedding(_current(conn)).space_id == rekeyed


def test_the_embedding_role_has_no_fallback():
    conn = _local()
    spare = _local("Saltmarch Spare", "https://spare.example/v1")
    config.write_config(**_current(conn),
                        **{keys.fallback_key("primary", "provider"): spare,
                           keys.fallback_key("primary", "model"): "m"})
    got = inference_resolve.embedding()
    assert len(got.attempts) == 1
    assert inference_resolve.FALLBACK_KEY not in got.attempts[0].conn
    assert got.fallback_missing == ()


def test_padded_ids_resolve_as_before():
    conn = _local()
    plain = embed_space.resolve({"embeddings_connection_id": conn, "embeddings_model": "m"})
    assert plain is not None
    legacy = embed_space.resolve({"embeddings_connection_id": f"  {conn}  ",
                                  "embeddings_model": "m"})
    current = embed_space.resolve({keys.FORMAT_KEY: "2",
                                   keys.role_key("embedding", "provider"): f"  {conn}  ",
                                   keys.role_key("embedding", "model"): " m "})
    assert legacy is not None and legacy["space"] == plain["space"]
    assert current is not None and current["space"] == plain["space"]


def test_a_known_no_turns_embedding_off_without_a_request():
    conn = _local("Winifred Zai", "https://api.z.ai/api/paas/v4")
    config.write_config(**_current(conn))
    assert embed_space.endpoint() is None
    assert embed_space.resolve() is None
    got = inference_resolve.embedding()
    assert got.missing == ("embed",)
    assert got.space_id is None


def _chat_only_router(model: str = "vendor/chat-x") -> str:
    """A format-2 OpenRouter provider whose cached catalog says `model` makes
    text only: a known `no` for `embed`, from the catalog."""
    conn = llm_connections.create_connection(
        "openrouter", "Saltmarch Router", api_key="sk-fake", model="", post_process="none")
    llm_connections.set_cached_models(conn, [{"id": model, "outputs": ["text"]}], _rev(conn))
    return conn


def test_a_failed_embed_test_never_turns_a_known_no_back_on():
    """A failed test is `unknown`; it never lifts the catalog's `no`, so the
    role stays off and no request that could only fail is sent (spec 7.3)."""
    conn = _chat_only_router()
    config.write_config(**_current(conn, "vendor/chat-x"))
    assert embed_space.endpoint() is None
    facts.record_verified(conn, "vendor/chat-x", _rev(conn),
                          {"embed": {"ok": False, "error": "404 not found"}})
    assert embed_space.endpoint() is None
    assert inference_resolve.embedding().missing == ("embed",)


def test_an_unknown_capability_still_embeds():
    conn = _local()
    config.write_config(**_current(conn))
    assert embed_space.endpoint() is not None
    assert inference_resolve.embedding().missing == ()


def test_endpoint_names_the_provider_and_resolve_keeps_four_keys():
    conn = _local("Seraphine Vectors")
    config.write_config(**_current(conn))
    got = embed_space.endpoint()
    assert got is not None
    assert set(got) == {"model", "base_url", "key", "space", "provider",
                        "provider_name", "provider_kind", "conn"}
    assert got["conn"]["id"] == conn
    assert (got["provider"], got["provider_name"], got["provider_kind"]) == (
        conn, "Seraphine Vectors", "openai_compatible")
    assert (got["model"], got["base_url"], got["key"]) == (
        "m", "https://vectors.example/v1", "sk-fake")
    four = embed_space.resolve()
    assert four is not None
    assert set(four) == {"model", "base_url", "key", "space"}
    assert four == {k: got[k] for k in four}


def test_nothing_selected_resolves_to_no_attempt():
    got = inference_resolve.embedding({keys.FORMAT_KEY: "2"})
    assert (got.role, got.via, got.scope, got.attempts) == ("", "", "none", ())
    assert got.space_id is None


def test_resolve_refuses_embed(monkeypatch):
    """Refused before anything is read: no config, no connection migration."""
    reads: list[str] = []
    monkeypatch.setattr(config, "read_config", lambda *a, **k: reads.append("config"))
    monkeypatch.setattr(llm_connections, "ensure_migrated",
                        lambda *a, **k: reads.append("connections"))
    with pytest.raises(ValueError):
        inference_resolve.resolve("semantic-recall")
    with pytest.raises(ValueError):
        inference_resolve.resolve("chat", operation="embed")
    with pytest.raises(ValueError):
        inference_resolve.resolve("", role="embedding")
    assert reads == []


def _edited(conn: str, **fields) -> tuple[dict, dict]:
    """`(before, after)` for an edit of `conn` that restamps its rev."""
    before = llm_connections.read_connection_raw(conn)
    return before, {**before, **fields, "rev": "next-rev"}


def test_a_provider_that_cannot_embed_never_asks_to_confirm_a_move():
    """`moved_by` honours a known `no`: a key edit on a provider that embeds
    nothing re-embeds nothing, so it is no move (spec 12)."""
    zai = _local("Winifred Zai", "https://api.z.ai/api/paas/v4")
    assert embed_space.moved_by(_current(zai), *_edited(zai, api_key="sk-fake-2")) is False
    # Leaving the known `no` for an address that embeds is a move: the library
    # is embedded from scratch.
    assert embed_space.moved_by(
        _current(zai), *_edited(zai, base_url="https://vectors.example/v1")) is True
    # The control: the same key edit on a provider that embeds is a move.
    local = _local()
    assert embed_space.moved_by(_current(local), *_edited(local, api_key="sk-fake-2")) is True


def _guarded_edit(conn: str, **fields) -> bool:
    """Make the edit as `put_connection` does, through `update_connection`'s
    guard, and return what `moved_by` said of it in the hold that writes."""
    said: list[bool] = []
    llm_connections.update_connection(
        conn, guard=lambda before, after: said.append(
            embed_space.moved_by(config.read_config(), before, after)), **fields)
    return said[0]


def test_moved_by_judges_the_provider_as_it_reads_after_the_save():
    """A rev restamp makes the old catalog row (and the old rev's probe
    verdicts) stale, so a `no` that came only from them does not survive the
    save: the edit that lands with embedding ON is a move, and asks."""
    conn = _local()
    llm_connections.set_cached_models(conn, [{"id": "m", "outputs": ["text"]}], _rev(conn))
    config.write_config(**_current(conn))
    assert embed_space.endpoint() is None            # off, by the catalog's `no`
    moved = _guarded_edit(conn, api_key="sk-fake-2")
    assert embed_space.endpoint() is not None        # on once the write landed
    assert moved is True                             # ...so the edit was a move


def test_a_passed_probe_over_a_catalog_no_is_judged_after_the_save():
    """A passed probe is rev-gated too: after a key edit the old catalog row is
    gone as well, so embedding stays on in a NEW space -- a move."""
    conn = _local()
    llm_connections.set_cached_models(conn, [{"id": "m", "outputs": ["text"]}], _rev(conn))
    facts.record_verified(conn, "m", _rev(conn), {"embed": {"ok": True}})
    config.write_config(**_current(conn))
    old = embed_space.endpoint()
    assert old is not None
    moved = _guarded_edit(conn, api_key="sk-fake-2")
    new = embed_space.endpoint()
    assert new is not None and new["space"] != old["space"]
    assert moved is True


def test_a_rev_independent_no_is_still_no_move():
    """The user's own `no` survives a rev restamp, so an edit of that provider
    re-embeds nothing and asks nothing."""
    conn = _local()
    facts.set_overrides(conn, "m", {"embed": "no"})
    config.write_config(**_current(conn))
    assert embed_space.endpoint() is None
    assert _guarded_edit(conn, api_key="sk-fake-2") is False
    assert embed_space.endpoint() is None


def test_embed_tasks_are_registered_apart():
    assert routing.EMBED_TASKS == ("semantic-recall", "semantic-search", "art-catalog",
                                   "continuity-similarity")
    assert not set(routing.EMBED_TASKS) & set(routing.TASK_ROUTE)
    assert not set(routing.EMBED_TASKS) & set(routing.NON_ROUTE_TASKS)
    for task in routing.EMBED_TASKS:
        assert routing.route(task) is None


def test_embedding_view_strips_at_both_formats():
    role = keys.role_key("embedding", "provider"), keys.role_key("embedding", "model")
    assert translate.embedding_view({"embeddings_connection_id": " a ",
                                     "embeddings_model": " b "}) == {role[0]: "a", role[1]: "b"}
    assert translate.embedding_view({keys.FORMAT_KEY: "2", role[0]: " a ",
                                     role[1]: " b "}) == {role[0]: "a", role[1]: "b"}
    assert translate.embedding_role({keys.FORMAT_KEY: "2", role[0]: " a "}) == ("a", "")
