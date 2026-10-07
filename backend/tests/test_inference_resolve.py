"""The resolver: a task (and a campaign's settings) in, the attempts it runs out.

`store.inference.resolve` assembles the pure pieces (`translate`, `cascade`)
around the store reads, and must answer exactly what the route layer answered
before the refactor. The functions it replaced are gone from `routes.common`
now, so these tests hold the resolver itself -- not the seam built on it -- to
the frozen baseline (`fixtures/inference_baseline.json`), over every store state
that baseline builds (`inference_baseline.STATES`), for every task, with and
without the campaign. `test_inference_equivalence.py` holds the seam to the same
file.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import llm_sampling, routes
from grimoire.llm import effective_model
from grimoire.store import routing
from grimoire.store.inference import resolve as inf
from grimoire.store.inference.cascade import Selection
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as baseline

TASKS = [*sorted(routing.TASK_ROUTE), ""]

#: The frozen answers (never regenerated -- see `inference_baseline`).
BASELINE = json.loads(baseline.FIXTURE.read_text(encoding="utf-8"))


def _normalised(value):
    """`value` as the baseline spells it: connection revs replaced."""
    return baseline._normalise(value, baseline._revs())


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
    """The 409 detail `routes.common.require_inference` raises for this
    resolution, or None.

    Spelled out here rather than read off the seam, so the comparison below
    proves the resolver carries everything that refusal needs (`conn`, `via`,
    `legacy_route`) independently of the code that builds it."""
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


# ---- the equivalence sweep: every state, task and scope against the baseline ----
@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_task_resolves_as_the_baseline_recorded(state, at_state):
    ctx = at_state(state)
    cid = ctx["cid"]
    for task in TASKS:
        for scope, scope_cid in (("global", ""), ("campaign", cid)):
            where = (state, task, scope)
            recorded = BASELINE[state]["tasks"][task][scope]
            resolved = inf.resolve(task, campaign_meta=_meta(scope_cid))
            assert resolved.task == task and resolved.operation == "generate", where

            # The refusal, where the baseline refused, and only there.
            if "status" in recorded:
                assert recorded["status"] == 409, where
                assert _refusal(resolved) == recorded["detail"], where
            else:
                assert _refusal(resolved) is None, where
                # The primary: the same connection, model, sampling and
                # catalog parameters the seam handed the facade.
                got = _normalised(baseline._resolved(resolved.conn))
                assert got == {k: v for k, v in recorded.items() if k != "fallback"}, where
                # The fallback's identity, as the facade uses it: it drops a
                # fallback naming the primary's own connection, and carries a
                # route's preset onto the one it keeps -- so the sampling is
                # held to `_fallback_connection` by the next test instead.
                fallback = resolved.fallback
                if fallback is not None and fallback["id"] == resolved.conn["id"]:
                    fallback = None
                assert (None if fallback is None else fallback["id"]) == (
                    None if recorded["fallback"] is None else recorded["fallback"]["id"]), where

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
def test_every_override_resolves_as_the_baseline_recorded(state, at_state):
    """The resolver's half of an override, against what the seam answered
    (recorded at the campaign scope). A 400 or a 409 is the seam's to raise;
    the resolver's part is to have nothing, or something that cannot send."""
    ctx = at_state(state)
    for name, body in OVERRIDES.items():
        where = (state, name)
        recorded = BASELINE[state]["overrides"][name]
        resolved = inf.resolve("regenerate", campaign_meta=_meta(ctx["cid"]),
                               override=_selection(body))
        if recorded.get("status") == 400:
            # A named connection that does not exist.
            assert resolved.conn is None, where
        elif recorded.get("status") == 409:
            assert resolved.conn is None or inf.problem(resolved.conn), where
        else:
            got = _normalised(baseline._resolved(resolved.conn))
            assert got == {k: v for k, v in recorded.items() if k != "routed"}, where
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
def test_own_sampling_reports_what_the_baseline_recorded(state, at_state):
    """A connection's own sampling, as the connection editor's sidebar reports
    it, is what the baseline's `display.connection_sampling` recorded."""
    at_state(state)
    recorded = BASELINE[state]["display"]["connection_sampling"]
    ids = {c["id"] for c in store.llm_connections.list_connections()}
    assert ids
    for conn_id in sorted(ids):
        raw = store.llm_connections.read_connection_raw(conn_id)
        got = _normalised(llm_sampling.report(inf.own_sampling(raw)))
        assert got == recorded[conn_id], conn_id


def test_own_sampling_drops_a_stale_model_params(at_state):
    at_state("routed")
    raw = store.llm_connections.read_connection_raw("local")
    got = inf.own_sampling({**raw, "model_params": ["stale"]})
    assert "model_params" not in got


def test_the_operation_is_carried(at_state):
    at_state("fresh")
    assert inf.resolve("chat", campaign_meta={}, operation="decide").operation == "decide"


def test_an_unreadable_campaign_resolves_globally(at_state):
    """A `campaign.md` that cannot be decoded has no routing opinion: the seam
    answers what the global scope says, rather than failing the generation."""
    ctx = at_state("routed")
    cid = ctx["cid"]
    path = store.campaigns.campaign_root(cid) / "campaign.md"

    def answer(task: str, scope_cid: str):
        try:
            return routes.common.require_inference(task, scope_cid)
        except HTTPException as exc:
            return exc.status_code, exc.detail

    # The state is chosen so the question has teeth: this campaign routes
    # `scene` elsewhere while its file can be read.
    assert answer("chat", cid) != answer("chat", "")
    path.write_bytes(b"\xff\xfe\x00 not text")
    assert routes.common._campaign_routing_meta(cid) == {}
    for task in TASKS:
        assert answer(task, cid) == answer(task, ""), task


def _key_cleared_after_first_read(monkeypatch, conn_id: str) -> list[str]:
    """Patch the connection reader so `conn_id` loses its key on every read
    after the first -- a key cleared in another tab mid-request. Returns the
    log of ids read."""
    real = store.llm_connections.read_connection_raw
    reads: list[str] = []

    def reader(cid: str):
        reads.append(cid)
        got = real(cid)
        if cid == conn_id and reads.count(cid) > 1:
            return {**got, "api_key": ""}
        return got

    monkeypatch.setattr(store.llm_connections, "read_connection_raw", reader)
    return reads


@pytest.mark.parametrize("body", [{"model": "vendor/bigger"},
                                  {"connection_id": "openrouter", "model": "vendor/bigger"}])
def test_an_override_is_refused_on_the_copy_that_serves(at_state, monkeypatch, body):
    """Review probe: an override that read the standing connection twice
    checked one copy and served the other, so a key cleared between the two
    reads went out keyless with no 409. One read per call, and what serves is
    what was checked."""
    ctx = at_state("fresh")
    for scope_cid in ("", ctx["cid"]):
        reads = _key_cleared_after_first_read(monkeypatch, "openrouter")
        resolved, routed = routes.common.override_inference(
            SimpleNamespace(**body), "regenerate", scope_cid)
        assert reads.count("openrouter") == 1, (scope_cid, reads)
        assert resolved.conn["api_key"] == "sk-test-active"
        assert resolved.conn["model"] == "vendor/bigger" and routed
        monkeypatch.undo()


@pytest.mark.parametrize("body", [{"model": "vendor/bigger"}, {}])
def test_a_model_only_override_on_a_keyless_standing_route_is_a_409(at_state, monkeypatch,
                                                                    body):
    """The other half of the probe: when the one read IS keyless, the reroll
    is refused with the seam's own wording -- never served."""
    at_state("fresh")
    real = store.llm_connections.read_connection_raw
    monkeypatch.setattr(store.llm_connections, "read_connection_raw",
                        lambda cid: {**real(cid), "api_key": ""})
    with pytest.raises(HTTPException) as exc:
        routes.common.override_inference(SimpleNamespace(**body), "regenerate", "")
    assert exc.value.status_code == 409
    assert exc.value.detail == {"detail": "OpenRouter key not set", "kind": "missing_key"}
