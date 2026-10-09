"""Every resolved attempt's `wire.Target` mirrors its lowered connection dict
(slice I, Task 7).

The resolver builds both from the same lowered values, and a resolution's
`chain` carries the fallback's target exactly where the primary's dict carries
it under `FALLBACK_KEY`. Held over every frozen baseline state
(`inference_baseline.STATES`), for every task at both scopes, every reroll
override and the Embedding role -- in memory (format 1) and after the
settings migration (format 2).
"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import llm, routes, wire
from grimoire.store import post_images, routing
from grimoire.store.inference import capabilities, resolve, resolved
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as baseline
from . import inference_fixtures as fx
from . import wire_kit

TASKS = [*sorted(routing.TASK_ROUTE), ""]


def _operation(task: str) -> str:
    route = routing.route(task)
    return route.operation if route is not None else "generate"


def _resolutions(cid: str) -> list[tuple[str, ResolvedInference]]:
    """Every resolution the state can produce: each task at both scopes, with
    its route's operation; each reroll override; and the Embedding role."""
    out = [(f"{task or '<unrouted>'}@{scope or 'global'}",
            resolve.resolve(task, scope, operation=_operation(task)))
           for task in TASKS for scope in ("", cid)]
    for name, body in baseline.OVERRIDE_BODIES.items():
        try:
            got, _routed = routes.common.override_inference(
                SimpleNamespace(**body), "regenerate", cid)
        except HTTPException:
            continue
        out.append((f"override:{name}", got))
    out.append(("embedding", resolve.embedding()))
    return out


def _assert_mirrors(where: str, a: Attempt) -> None:
    c, t = a.conn, a.target
    assert (t.provider_id, t.kind, t.model) == (c["id"], c["kind"], llm.effective_model(c)), where
    assert t.requested_model == llm.effective_model(c), where
    assert (t.provider_name, t.base_url, t.api_key, t.rev) == (
        c["name"], c["base_url"], c["api_key"], c["rev"]), where
    assert asdict(t.sampling) == c["sampling"], where
    assert t.sampler_support == c.get("sampler_support", ""), where
    assert t.model_params == (None if c.get("model_params") is None
                              else tuple(c["model_params"])), where
    assert t.model_features == c.get("model_features"), where
    assert t.structured == (c.get(resolve.STRUCTURED_KEY) is True), where
    assert asdict(t.account) == {**asdict(wire.Account()), **c.get(resolve.ACCOUNT_KEY, {})}, where
    assert t.reads_images == post_images.capability(c), where
    assert (t.prefill, t.post_process) == (c["prefill"], c["post_process"]), where
    assert t.degrade is False, where


def _assert_every_target_mirrors(cid: str) -> None:
    for where, r in _resolutions(cid):
        for a in r.attempts:
            _assert_mirrors(where, a)
        if not r.attempts:
            assert r.chain is None, where
            continue
        assert r.chain is not None, where
        assert r.chain.primary is r.attempts[0].target, where
        assert (r.chain.fallback is not None) == (resolve.FALLBACK_KEY in (r.conn or {})), where
        if r.chain.fallback is not None:
            assert r.chain.fallback is r.attempts[1].target, where


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_target_mirrors_its_lowered_dict(state, tmp_path):
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        _assert_every_target_mirrors(ctx["cid"])


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_target_mirrors_its_lowered_dict_after_migration(state, tmp_path):
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        assert baseline.migrate_state(state).state == "done"
        _assert_every_target_mirrors(ctx["cid"])


# ---- what no baseline state reaches ----
def _flag(conn_id: str, model: str) -> None:
    """`model`'s catalog row on `conn_id` lists structured outputs."""
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(
        conn_id, [{"id": model, "params": ["temperature", "structured_outputs"]}], rev)


def _decide() -> ResolvedInference:
    got = resolve.resolve("scene-break", operation="decide")
    for a in got.attempts:
        _assert_mirrors("scene-break", a)
    return got


def test_a_structured_decide_flags_both_targets_and_the_fallback_rides(tmp_path):
    with baseline.client_at(tmp_path) as client:
        fx.format2(client)
        fx.put_settings(client, {"roles": {"decision": {
            "selection": {"provider": "openrouter", "model": "vendor/active"},
            "fallback": {"provider": fx.SPARE[0], "model": fx.SPARE[1]}}}})
        _flag("openrouter", "vendor/active")
        _flag(*fx.SPARE)
        got = _decide()
        assert got.chain is not None and got.chain.fallback is not None
        assert [t.structured for t in got.chain.attempts] == [True, True]
        assert [t.account.operation for t in got.chain.attempts] == ["decide", "decide"]
        assert [t.account.role for t in got.chain.attempts] == ["decision", "decision"]


def test_a_native_decide_primary_carries_no_fallback_on_its_chain(tmp_path):
    """The generating fallback behind a native-only primary is a stage of its
    own (`inference.stages`): listed in `attempts`, not on the chain, exactly
    as it is not under the primary's `FALLBACK_KEY`."""
    with baseline.client_at(tmp_path) as client:
        fx.decide_only(client, fallback=True)
        got = _decide()
        assert len(got.attempts) == 2 and resolve.FALLBACK_KEY not in got.conn
        assert got.chain == wire.Chain(got.attempts[0].target)


def test_a_target_reads_images_by_the_post_image_preference(tmp_path):
    with baseline.client_at(tmp_path) as client:
        baseline._fresh(client)
        store.llm_connections.update_connection("openrouter", vision="on")
        (a,) = resolve.resolve("chat").attempts
        _assert_mirrors("chat", a)
        assert a.target.reads_images == "yes"


def test_the_restated_fallback_key_is_the_resolvers():
    assert resolved.FALLBACK_KEY == resolve.FALLBACK_KEY == llm.FALLBACK_KEY


def test_a_hand_built_attempt_has_no_chain_to_send():
    """An attempt built by hand (as tests build them) carries the unbuilt
    target, as it carries empty `controls`; nothing resolved, nothing sent."""
    a = Attempt("p", "m", "", {})
    assert a.target == resolved.UNBUILT
    empty = ResolvedInference(task="", operation="generate", route="", legacy_route="",
                              role="", via="", scope="none", attempts=())
    assert empty.chain is None


def test_a_chain_carries_the_fallback_only_where_the_dict_does():
    fb = Attempt("spare", "m", "", {"id": "spare"}, target=wire_kit.target(provider_id="spare"))
    attached = Attempt("p", "m", "", {"id": "p", resolve.FALLBACK_KEY: fb.conn},
                       target=wire_kit.target(provider_id="p"))
    unattached = Attempt("p", "m", "", {"id": "p"}, target=wire_kit.target(provider_id="p"))

    def chain(*attempts: Attempt) -> wire.Chain | None:
        return ResolvedInference(task="", operation="generate", route="", legacy_route="",
                                 role="", via="", scope="none", attempts=attempts).chain

    assert chain(attached, fb) == wire.Chain(attached.target, fb.target)
    assert chain(unattached, fb) == wire.Chain(unattached.target)
    assert chain(unattached) == wire.Chain(unattached.target)


# ---- post_image_reach ----
YES, NO, UNKNOWN = (Cap(v, "catalog") for v in ("yes", "no", "unknown"))


@pytest.mark.parametrize(("kind", "fact", "cap", "reach"), [
    # A kind whose client cannot carry an image part, whatever is said.
    ("claude", "on", YES, "no"),
    ("claude", "", YES, "no"),
    ("mystery", "on", YES, "no"),
    # The post-image preference wins over the capability.
    ("openrouter", "on", NO, "yes"),
    ("openrouter", "off", YES, "no"),
    ("anthropic", "off", UNKNOWN, "no"),
    # Otherwise the capability answers.
    ("openrouter", "", YES, "yes"),
    ("openai_compatible", "", NO, "no"),
    ("anthropic", "", UNKNOWN, "unknown"),
    ("openrouter", "auto", Cap("unknown", "test", "boom"), "unknown"),
])
def test_post_image_reach_is_post_images_rule(kind, fact, cap, reach):
    assert capabilities.post_image_reach(kind, fact, cap) == reach
