"""Every resolved attempt's `wire.Target` is what its provider record, its
model, its preset, its catalog row and its model's facts say (slice I, Tasks 7
and 10).

Task 7 built the target beside a lowered connection dict and held every field
to the dict; Task 10 deleted the dict, so each field is now held to the store
it was read from, and a resolution's `chain` carries the fallback's target
exactly where the fallback `rides`. Held over every frozen baseline state
(`inference_baseline.STATES`), for every task at both scopes, every reroll
override and the Embedding role -- in memory (format 1), after the settings
migration (format 2) and after retirement.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import routes, wire
from grimoire.store import routing
from grimoire.store.inference import capabilities, migrate, resolve, resolved
from grimoire.store.inference import facts as inference_facts
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as baseline
from . import inference_baseline_c as baseline_c
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


def _assert_mirrors(where: str, a: Attempt, operation: str) -> None:
    """`a`'s target, field by field, against what it was read from: the
    provider's record, the attempt's model and preset, the cached catalog row
    for that model, and the model's facts as the attempt read them
    (`Attempt.facts`). Each read independently of the resolver's builder."""
    t = a.target
    raw = store.llm_connections.read_connection_raw(a.provider_id)
    sent = inference_facts.model_of({**raw, "model": a.model})
    assert (t.provider_id, t.kind, t.model, t.requested_model) == (
        raw["id"], raw["kind"], sent, sent), where
    assert (t.provider_name, t.base_url, t.api_key, t.rev) == (
        raw["name"], raw["base_url"], raw["api_key"], raw["rev"]), where
    assert (t.kind, t.rev) == (a.provider_kind, a.rev), where
    assert t.sampling.preset_id == a.preset_id, where
    assert t.sampler_support == raw.get("sampler_support", ""), where
    row = store.llm_connections.cached_row(raw["id"], a.model)
    params = row.get("params") if row is not None and raw["kind"] == "openrouter" else None
    assert t.model_params == (tuple(params) if isinstance(params, list) else None), where
    features = row.get("features") if row is not None else None
    assert t.model_features == (features if isinstance(features, dict) else None), where
    found = a.capabilities.get("structured_output")
    assert t.structured == (operation == "decide" and found is not None
                            and found.value == "yes"), where
    assert (t.account.billing, t.account.operation) == (a.billing, operation), where
    vision = a.facts.get("vision")
    vision = vision if isinstance(vision, str) else ""
    assert t.reads_images == capabilities.post_image_reach(
        raw["kind"], vision, capabilities.caps_for(raw, a.model)["vision"]), where
    post_process = a.facts.get("post_process")
    assert (t.prefill, t.post_process) == (
        a.facts.get("prefill") is True,
        post_process if post_process in inference_facts.POST_PROCESS_VALUES and post_process
        else "none"), where
    assert t.degrade is False, where


def _assert_every_target_mirrors(cid: str) -> None:
    for where, r in _resolutions(cid):
        for a in r.attempts:
            _assert_mirrors(where, a, r.operation)
        if not r.attempts:
            assert r.chain is None, where
            continue
        assert r.chain is not None, where
        assert r.chain.primary is r.attempts[0].target, where
        assert (r.chain.fallback is not None) == (r.rides and len(r.attempts) > 1), where
        if r.chain.fallback is not None:
            assert r.chain.fallback is r.attempts[1].target, where


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_target_mirrors_what_it_was_read_from(state, tmp_path):
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        _assert_every_target_mirrors(ctx["cid"])


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_target_mirrors_what_it_was_read_from_after_migration(state, tmp_path):
    """Migrated as a C-H build migrated: format 2, nothing retired, the
    legacy GLM effort still planned in memory."""
    with baseline.client_at(tmp_path) as client:
        ctx = baseline.STATES[state](client)
        with mock.patch.object(migrate, "_retire", lambda *_args: None):
            assert baseline.migrate_state(state).state == "done"
        assert not store.read_config()[store.inference_keys.RETIRED_KEY]
        _assert_every_target_mirrors(ctx["cid"])


@pytest.mark.parametrize("state", sorted({*baseline.STATES, *baseline_c.STATES}))
def test_every_target_mirrors_what_it_was_read_from_after_retirement(state, tmp_path):
    """The retired pass (slice I, Task 6): migrated, retired and stripped --
    the derived presets real files, no legacy key or field left -- every
    attempt's target still mirrors what it was read from."""
    builders = {**baseline.STATES, **baseline_c.STATES}
    with baseline.client_at(tmp_path) as client:
        ctx = builders[state](client)
        assert baseline.migrate_state(state).state == "done"
        status = migrate.status()
        assert store.read_config()[store.inference_keys.RETIRED_KEY] == "1"
        assert status.retirement["left"] == [], status.retirement
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
        _assert_mirrors("scene-break", a, "decide")
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
    own (`inference.stages`): listed in `attempts`, not on the chain, because
    it does not ride."""
    with baseline.client_at(tmp_path) as client:
        fx.decide_only(client, fallback=True)
        got = _decide()
        assert len(got.attempts) == 2 and not got.rides
        assert got.chain == wire.Chain(got.attempts[0].target)


def test_a_target_reads_images_by_the_post_image_preference(tmp_path):
    with baseline.client_at(tmp_path) as client:
        baseline._fresh(client)
        store.llm_connections.update_connection("openrouter", vision="on")
        (a,) = resolve.resolve("chat").attempts
        _assert_mirrors("chat", a, "generate")
        assert a.target.reads_images == "yes"


def test_a_target_reads_images_by_the_model_facts_at_format_2(tmp_path):
    """At format 2 the post-image preference is the model's facts: `vision:
    on` there reads "yes" over a catalog `no`, with the connection's own
    (frozen) field left blank."""
    with baseline.client_at(tmp_path) as client:
        baseline._fresh(client)
        assert baseline.migrate_state("fresh").state == "done"
        (before,) = resolve.resolve("chat").attempts
        model, rev = before.target.model, before.target.rev
        store.llm_connections.set_cached_models(
            "openrouter", [{"id": model, "vision": False}], rev)
        assert resolve.resolve("chat").attempts[0].target.reads_images == "no"
        inference_facts.set_stated("openrouter", model, vision="on")
        (a,) = resolve.resolve("chat").attempts
        _assert_mirrors("chat", a, "generate")
        assert store.llm_connections.read_connection_raw("openrouter")["vision"] == ""
        assert a.facts["vision"] == "on"
        assert a.target.reads_images == "yes"


def test_a_hand_built_attempt_has_no_chain_to_send():
    """An attempt built by hand (as tests build them) carries the unbuilt
    target, as it carries empty `controls`; nothing resolved, nothing sent."""
    a = Attempt("p", "m", "")
    assert a.target == resolved.UNBUILT
    empty = ResolvedInference(task="", operation="generate", route="",
                              role="", via="", scope="none", attempts=())
    assert empty.chain is None


def test_a_chain_carries_the_fallback_only_where_it_rides():
    fb = Attempt("spare", "m", "", target=wire_kit.target(provider_id="spare"))
    primary = Attempt("p", "m", "", target=wire_kit.target(provider_id="p"))

    def chain(*attempts: Attempt, rides: bool) -> wire.Chain | None:
        return ResolvedInference(task="", operation="generate", route="", role="", via="",
                                 scope="none", attempts=attempts, rides=rides).chain

    assert chain(primary, fb, rides=True) == wire.Chain(primary.target, fb.target)
    assert chain(primary, fb, rides=False) == wire.Chain(primary.target)
    assert chain(primary, rides=True) == wire.Chain(primary.target)
    assert chain(primary, rides=False) == wire.Chain(primary.target)


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
