"""The resolver: a task (and a campaign's settings) in, the attempts it runs out.

`store.inference.resolve` assembles the pure pieces (`translate`, `cascade`)
around the store reads, and must answer exactly what the route layer answers
today. Nothing calls it yet, so these tests hold it to today's functions in
`routes.common` directly, over every store state the frozen baseline builds
(`inference_baseline.STATES`), for every task, with and without the campaign.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import routes
from grimoire.llm import effective_model
from grimoire.store import routing
from grimoire.store.inference import resolve as inf
from grimoire.store.inference.cascade import Selection
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as baseline

TASKS = [*sorted(routing.TASK_ROUTE), ""]


def _meta(cid: str) -> dict:
    return routes.common._campaign_routing_meta(cid)


def _state(name: str, tmp_path):
    """A client on a store built as `name`, and that state's context."""
    cm = baseline.client_at(tmp_path)
    client = cm.__enter__()
    try:
        ctx = baseline.STATES[name](client)
    except BaseException:
        cm.__exit__(None, None, None)
        raise
    return cm, client, ctx


@pytest.fixture
def at_state(tmp_path):
    """`at_state(name)` builds the store and returns its context; torn down after."""
    opened = []

    def build(name: str) -> dict:
        cm, _client, ctx = _state(name, tmp_path / name)
        opened.append(cm)
        return ctx

    yield build
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def _refusal(resolved: ResolvedInference) -> dict | None:
    """The 409 detail `_usable_or_409` would raise for this resolution, or None.

    Spelled out here as Task 6's seam will spell it, so the comparison below
    proves the resolver carries everything that refusal needs (`conn`, `via`,
    `legacy_route`)."""
    conn = resolved.conn
    if conn is None:
        return {"detail": "No LLM connection selected", "kind": "missing_key"}
    problem = inf.problem(conn)
    if problem is None:
        return None
    if resolved.via == "route":
        label = routing.label_for(resolved.legacy_route).lower()
        return {"detail": f"{problem} ({conn.get('name') or conn['id']}, routed for {label})",
                "kind": "missing_key"}
    return {"detail": problem, "kind": "missing_key"}


# ---- the equivalence sweep: every state, task and scope against today ----
@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_task_resolves_as_the_route_layer_does_today(state, at_state):
    ctx = at_state(state)
    cid = ctx["cid"]
    for task in TASKS:
        for scope_cid in ("", cid):
            where = (state, task, scope_cid)
            resolved = inf.resolve(task, campaign_meta=_meta(scope_cid))
            standing, resolution, routed = routes.common._standing_connection(task, scope_cid)

            # The primary: the same dict today's seam hands the facade,
            # `sampling` and `model_params` included.
            assert resolved.conn == standing, where
            # How it was decided: a pin is today's "routed", and the legacy
            # route is what today's 409 names.
            assert (resolved.via == "route") == routed, where
            assert resolved.legacy_route == resolution["route"], where
            assert resolved.task == task and resolved.operation == "generate", where

            # The refusal, where today refuses, and only there.
            try:
                routes.common._require_connection(task, scope_cid)
            except HTTPException as exc:
                assert exc.status_code == 409, where
                assert _refusal(resolved) == exc.detail, where
            else:
                assert _refusal(resolved) is None, where
                # The fallback: today's standing policy, whole.
                assert resolved.fallback == routes.common._fallback_connection(), where

            if resolved.conn is None:
                assert resolved.attempts == () and resolved.fallback is None, where
                continue
            first = resolved.attempts[0]
            assert first.provider_id == resolved.conn["id"], where
            assert first.model == resolved.conn["model"], where
            assert first.preset_id == resolved.conn["sampling"]["preset_id"], where


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_the_resolved_fallback_is_todays_fallback(state, at_state):
    ctx = at_state(state)
    today = routes.common._fallback_connection()
    for task in TASKS:
        for scope_cid in ("", ctx["cid"]):
            resolved = inf.resolve(task, campaign_meta=_meta(scope_cid))
            if resolved.conn is None:
                # Nothing to fall back FROM: the seam refuses before the
                # facade would ever ask (today's 409 "No LLM connection").
                assert resolved.fallback is None
                continue
            got = resolved.fallback
            assert (got or {}).get("id") == (today or {}).get("id"), (state, task)
            assert (got or {}).get("sampling") == (today or {}).get("sampling"), (state, task)
            if got is not None:
                fb = resolved.attempts[1]
                assert (fb.provider_id, fb.model, fb.preset_id) == (
                    got["id"], got["model"], got["sampling"]["preset_id"])


#: Override bodies the resolver answers. `long_model` is a 400 on the body's
#: own length, which the seam checks before resolving anything.
OVERRIDES = {name: body for name, body in baseline.OVERRIDE_BODIES.items()
             if name != "long_model"}


def _selection(body: dict) -> Selection:
    return Selection(body.get("connection_id", ""), body.get("model", ""), "")


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_override_resolves_as_today(state, at_state):
    ctx = at_state(state)
    cid = ctx["cid"]
    for name, body in OVERRIDES.items():
        for scope_cid in ("", cid):
            where = (state, name, scope_cid)
            resolved = inf.resolve("regenerate", campaign_meta=_meta(scope_cid),
                                   override=_selection(body))
            try:
                conn, _routed = routes.common._override_connection(
                    SimpleNamespace(**body), "regenerate", scope_cid)
            except HTTPException as exc:
                if exc.status_code == 400:
                    # A named connection that does not exist.
                    assert resolved.conn is None, where
                else:
                    assert exc.status_code == 409, where
                    assert resolved.conn is None or inf.problem(resolved.conn), where
                continue
            assert resolved.conn == conn, where
            # An override picks the primary; the fallback stays standing policy.
            assert resolved.fallback == routes.common._fallback_connection(), where


# ---- the brief's named cases ----
def test_a_fresh_store_resolves_every_task_to_the_active_connection(at_state):
    ctx = at_state("fresh")
    for task in TASKS:
        for meta in ({}, _meta(ctx["cid"])):
            resolved = inf.resolve(task, campaign_meta=meta)
            assert resolved.conn["id"] == "openrouter"
            assert resolved.conn["model"] == "vendor/active"
            assert resolved.via == "role"
            assert resolved.role == "primary" and resolved.scope == "global"
            assert resolved.fallback is None and len(resolved.attempts) == 1


def test_a_route_pin_lowers_to_that_connections_dict(at_state):
    at_state("routed")
    resolved = inf.resolve("dossier", campaign_meta={})
    raw = store.llm_connections.read_connection_raw("local")
    conn = resolved.conn
    assert conn["id"] == "local" and conn["base_url"] == "http://localhost:1234/v1"
    assert {k: v for k, v in conn.items() if k != "sampling"} == raw
    assert conn["sampling"] == {"preset_id": "warm", "preset_name": "warm",
                                "scope": "connection", "params": {"temperature": 0.9}}
    assert "model_params" not in conn          # not an OpenRouter connection
    assert (resolved.route, resolved.legacy_route) == ("dossier", "dossier")
    assert (resolved.via, resolved.scope, resolved.role) == ("route", "global", "")
    assert resolved.attempts[0] == Attempt("local", "local-model", "warm", conn)


def test_a_split_route_reports_its_legacy_route(at_state):
    at_state("fresh")
    resolved = inf.resolve("scene-break", campaign_meta={})
    assert (resolved.route, resolved.legacy_route) == ("scene_break", "summary")
    assert inf.resolve("", campaign_meta={}).legacy_route == ""
    assert inf.resolve("", campaign_meta={}).route == ""


def test_a_keyless_fallback_is_no_fallback(at_state):
    at_state("keyless")
    assert routes.common._fallback_connection() is None
    for task in ("chat", "absorb", ""):
        assert inf.resolve(task, campaign_meta={}).fallback is None
    # The ROUTED keyless connection is still the primary: the seam reports it.
    absorb = inf.resolve("absorb", campaign_meta={})
    assert absorb.conn["id"] == "nokey" and inf.problem(absorb.conn) is not None
    assert absorb.via == "route"


def test_no_active_connection_still_resolves_a_pinned_route(at_state):
    at_state("no_active")
    pinned = inf.resolve("dossier", campaign_meta={})
    assert pinned.conn["id"] == "local"
    # Review Focus 1: the pin keeps the global fallback.
    assert pinned.fallback is not None and pinned.fallback["id"] == "spare"
    unpinned = inf.resolve("chat", campaign_meta={})
    assert unpinned.conn is None and unpinned.attempts == ()
    assert unpinned.fallback is None


def test_a_legacy_flat_config_is_migrated_before_it_is_read(at_state, monkeypatch):
    at_state("pre_connections")
    order: list[str] = []
    real_migrate = store.llm_connections.ensure_migrated
    real_read = store.config.read_config

    def migrate():
        order.append("migrate")
        return real_migrate()

    def read():
        order.append("read")
        return real_read()

    monkeypatch.setattr(store.llm_connections, "ensure_migrated", migrate)
    monkeypatch.setattr(store.config, "read_config", read)
    resolved = inf.resolve("chat", campaign_meta={})
    assert order[0] == "migrate" and "read" in order
    assert resolved.conn["id"] == "openrouter"
    assert resolved.conn["model"] == "vendor/legacy"
    assert resolved.conn["api_key"] == "sk-test-legacy"


def test_an_unreadable_connection_file_reads_as_missing(at_state, monkeypatch):
    at_state("embed_with_dangling")
    # `spare` is invalid UTF-8 on disk and pinned for dossier: walked past.
    resolved = inf.resolve("dossier", campaign_meta={})
    assert resolved.conn["id"] == "claude" and resolved.via == "role"

    # Every way a read can fail reads as "no such connection", never raises.
    store.write_config(route_dossier="local")
    assert inf.resolve("dossier", campaign_meta={}).conn["id"] == "local"
    real = store.llm_connections.read_connection_raw
    for exc in (store.locks.StoreBusy("busy"), OSError("gone"),
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
                store.llm_connections.ConnectionNotFound("local")):
        def fail(conn_id, exc=exc):
            if conn_id == "local":
                raise exc
            return real(conn_id)
        monkeypatch.setattr(store.llm_connections, "read_connection_raw", fail)
        resolved = inf.resolve("dossier", campaign_meta={})
        assert resolved.conn["id"] == "claude", exc


def test_a_connection_is_read_once_per_resolve(at_state, monkeypatch):
    at_state("routed")
    calls: list[str] = []
    real = store.llm_connections.read_connection_raw

    def counting(conn_id):
        calls.append(conn_id)
        return real(conn_id)

    monkeypatch.setattr(store.llm_connections, "read_connection_raw", counting)
    inf.resolve("dossier", campaign_meta={})
    assert calls and len(calls) == len(set(calls))


def test_a_provider_only_override_keeps_that_providers_model(at_state):
    at_state("routed")
    resolved = inf.resolve("regenerate", campaign_meta={},
                           override=Selection("local", "", ""))
    assert resolved.conn["id"] == "local" and resolved.conn["model"] == "local-model"
    # The route's preset follows the route (global `preset_scene: cold`); a
    # route with none falls to the named connection's own (`warm`).
    assert resolved.conn["sampling"]["preset_id"] == "cold"
    assert resolved.conn["sampling"]["scope"] == "global"
    tagline = inf.resolve("tagline", campaign_meta={}, override=Selection("local", "", ""))
    assert tagline.conn["sampling"]["preset_id"] == "warm"
    assert tagline.conn["sampling"]["scope"] == "connection"


def test_a_model_only_override_keeps_the_standing_provider(at_state):
    ctx = at_state("routed")
    standing = inf.resolve("regenerate", campaign_meta=_meta(ctx["cid"]))
    assert standing.conn["id"] == "local"
    resolved = inf.resolve("regenerate", campaign_meta=_meta(ctx["cid"]),
                           override=Selection("", "bigger-local", ""))
    assert resolved.conn["id"] == "local" and resolved.conn["model"] == "bigger-local"
    # Global: the active OpenRouter connection, driven at a model whose catalog
    # entry says what it takes.
    glob = inf.resolve("regenerate", campaign_meta={},
                       override=Selection("", "vendor/bigger", ""))
    assert glob.conn["id"] == "openrouter" and glob.conn["model"] == "vendor/bigger"
    assert glob.conn["model_params"] == ["temperature"]
    assert effective_model(glob.conn) == "vendor/bigger"


def test_a_provider_override_needs_no_standing_selection(at_state):
    at_state("no_active")
    assert inf.resolve("regenerate", campaign_meta={}).conn is None
    named = inf.resolve("regenerate", campaign_meta={}, override=Selection("spare", "", ""))
    assert named.conn["id"] == "spare" and named.conn["model"] == "vendor/spare"
    # A model alone overrides the standing selection, and there is none.
    model_only = inf.resolve("regenerate", campaign_meta={},
                             override=Selection("", "vendor/bigger", ""))
    assert model_only.conn is None


def test_an_override_naming_no_connection_resolves_nothing(at_state):
    at_state("fresh")
    resolved = inf.resolve("regenerate", campaign_meta={}, override=Selection("nope", "", ""))
    assert resolved.conn is None and resolved.attempts == ()


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_own_sampling_matches_a_task_less_attach(state, at_state):
    at_state(state)
    ids = {c["id"] for c in store.llm_connections.list_connections()}
    assert ids
    for conn_id in sorted(ids):
        raw = store.llm_connections.read_connection_raw(conn_id)
        assert inf.own_sampling(raw) == routes.common._attach_sampling(raw, "", ""), conn_id
        assert inf.model_params(raw) == routes.common._model_params(raw), conn_id
        assert inf.problem(raw) == routes.common._connection_problem(raw), conn_id


def test_own_sampling_drops_a_stale_model_params(at_state):
    at_state("routed")
    raw = store.llm_connections.read_connection_raw("local")
    got = inf.own_sampling({**raw, "model_params": ["stale"]})
    assert "model_params" not in got


def test_the_operation_is_carried(at_state):
    at_state("fresh")
    assert inf.resolve("chat", campaign_meta={}, operation="decide").operation == "decide"
