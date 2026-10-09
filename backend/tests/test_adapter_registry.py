"""The adapter registry (`grimoire.adapters`, slice I Task 9): one adapter per
connection kind, sending a typed `wire.Target`, built beside the facade.

What is held here:

- the registry covers every kind the store knows, and its capability flags
  agree with the store's own facts about each kind (`providers.PRESETS`'
  `never`, `resolve.embed_endpoint`, `image_drafts.SUPPORTED_KINDS`) and with
  slice H's native table;
- an adapter sends exactly what the facade sends for the same attempt --
  every resolved attempt of every frozen baseline state, in memory, after
  the migration (as a C-H build left it) and after retirement (Task 6), and
  a hand-built attempt of each kind. What the facade sent for the lowered
  dict, before Task 9d took the dict door away and Task 10 the dict, is
  frozen in `test_adapter_wire_golden`;
- `resolve.target_for` builds the target a resolved attempt carries;
- the facade takes chains of targets and no dict (Task 9d), and files the
  ledger row the lowered dict filed (Task 9b), as recorded at Task 10's
  base, before the dict was deleted.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
from pathlib import Path
from unittest import mock

import pytest

import grimoire.store as store
from grimoire import (
    adapters,
    content_parts,
    decisions,
    health,
    llm,
    llm_usage,
    wire,
)
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.llm_errors import LLMError
from grimoire.store import image_drafts, usage_rollup
from grimoire.store.inference import capabilities, migrate, providers, resolve

from . import inference_baseline as baseline
from . import inference_baseline_c as baseline_c
from . import inference_fixtures as fx
from . import wire_kit
from .test_inference_target import _resolutions

#: Every frozen baseline state, both families, keyed `<family>:<state>`.
STATES = {**{f"base:{k}": (baseline, k) for k in baseline.STATES},
          **{f"c:{k}": (baseline_c, k) for k in baseline_c.STATES}}

MARA = Option("characters:mara", "Mara, the cartographer.")
WINIFRED = Option("characters:winifred", "Winifred, the harbourmaster.")
ITEM = Item("Mara and Winifred argue over the Saltmarch charts.",
            (Predicate("over", "Has the scene reached its end?"),
             Choice("speaker", "Who speaks next?", (MARA, WINIFRED), allow_none=True),
             Score("tension", "How tense is the exchange?", ("Calm.", "Uneasy.", "Heated."))))

MESSAGES = [{"role": "system", "content": "You narrate Saltmarch."},
            {"role": "user", "content": "Mara unrolls the charts."}]
SCHEMA = {"type": "object", "properties": {"over": {"type": "boolean"}}}


class _Wire:
    """One provider client, recording each call as `(method, args, kwargs)`.
    `stream` records when it is CALLED, as the facade's dispatch is what is
    compared, and answers nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def stream(self, *args, **kwargs):
        self.calls.append(("stream", args, kwargs))
        return self._nothing()

    async def _nothing(self):
        return
        yield  # pragma: no cover - makes this an async generator

    async def list_models(self, *args, **kwargs):
        self.calls.append(("list_models", args, kwargs))
        return []

    async def probe(self, *args, **kwargs):
        self.calls.append(("probe", args, kwargs))

    async def decide(self, *args, **kwargs):
        self.calls.append(("decide", args, kwargs))
        return decisions.ItemResult(answers={}, backend="native")


def _clients() -> dict[str, _Wire]:
    return {kind: _Wire() for kind in adapters.KINDS}


def _registry(clients: dict[str, _Wire]) -> dict[str, adapters.Adapter]:
    return adapters.build(**clients)  # type: ignore[arg-type]


def _facade(clients: dict[str, _Wire]) -> llm.LLMClient:
    return llm.LLMClient(openrouter=clients["openrouter"], claude=clients["claude"],
                         openai_compatible=clients["openai_compatible"],
                         anthropic=clients["anthropic"], retries=0, timeout=7)


def _states():
    return pytest.mark.parametrize("state", sorted(STATES))


#: The passes each state is resolved in: in memory (format 1, the legacy
#: keys read through `translate`), after the settings migration as a C-H
#: build left it (format 2, nothing retired), and after retirement (Task 6:
#: migrated, retired and stripped).
PASSES = ("memory", "migrated", "retired")


def _passes():
    return pytest.mark.parametrize("stage", PASSES)


def _resolved(state: str, tmp_path, *, stage: str):
    """Every resolution `state` produces (`test_inference_target`'s), in the
    pass `stage` names (`PASSES`)."""
    assert stage in PASSES, stage
    family, name = STATES[state]
    with baseline.client_at(tmp_path) as client:
        ctx = family.STATES[name](client)
        if stage == "migrated":
            with mock.patch.object(migrate, "_retire", lambda *_args: None):
                assert baseline.migrate_state(name).state == "done"
            assert not store.read_config()[store.inference_keys.RETIRED_KEY]
        elif stage == "retired":
            assert baseline.migrate_state(name).state == "done"
            assert store.read_config()[store.inference_keys.RETIRED_KEY] == "1"
            assert migrate.status().retirement["left"] == []
        yield from _resolutions(ctx["cid"])


# ---- the registry and its flags ----
def test_every_kind_has_one_adapter():
    registry = _registry(_clients())
    assert set(registry) == set(adapters.KINDS) == {p.kind for p in providers.PRESETS.values()}
    assert all(adapter.kind == kind for kind, adapter in registry.items())
    assert len(adapters.KINDS) == len(set(adapters.KINDS))


def test_adapter_facts_agree_with_the_registry():
    """A False flag is backed by every preset of its kind ruling the
    capability out, and a capability every preset of a kind rules out is a
    False flag -- so the registry and the store's facts cannot drift."""
    registry = _registry(_clients())
    flags = {"embed": "embeds", "decide_native": "decides_natively", "vision": "carries_images"}
    for preset in providers.PRESETS.values():
        adapter = registry[preset.kind]
        for cap, flag in flags.items():
            if not getattr(adapter, flag):
                assert cap in preset.never, (preset.id, cap)
    for kind in adapters.KINDS:
        of_kind = [p for p in providers.PRESETS.values() if p.kind == kind]
        for cap, flag in flags.items():
            if all(cap in p.never for p in of_kind):
                assert getattr(registry[kind], flag) is False, (kind, cap)
    # The native cases named outright: the two kinds with no endpoint list it,
    # the OpenAI preset does not, and every other OpenAI-compatible one does.
    assert "decide_native" in providers.PRESETS["anthropic"].never
    assert "decide_native" in providers.PRESETS["claude"].never
    assert "decide_native" not in providers.PRESETS["openai"].never
    assert all("decide_native" in p.never for p in providers.PRESETS.values()
               if p.kind == "openai_compatible" and p.id != "openai")


def test_the_registry_embed_flag_matches_the_store_endpoint_rule():
    registry = _registry(_clients())
    for kind in adapters.KINDS:
        conn = {"kind": kind, "base_url": "https://embed.example.test/v1", "api_key": "sk-test-e"}
        assert bool(resolve.embed_endpoint(conn)) is registry[kind].embeds, kind


def test_decides_natively_is_every_kind_a_preset_may_decide_natively_on():
    """The registry's native flag (which replaced slice H's
    `NATIVE_DECISION_KINDS` table in 9c) is True for exactly the kinds
    that have a provider preset not ruling `decide_native` out: OpenRouter,
    and OpenAI-compatible through the OpenAI preset."""
    registry = _registry(_clients())
    native = {k for k, a in registry.items() if a.decides_natively}
    assert native == {p.kind for p in providers.PRESETS.values()
                      if "decide_native" not in p.never}
    assert native == {"openrouter", "openai_compatible"}
    assert all(adapters.decides_natively(k) is (k in native) for k in (*adapters.KINDS, "x"))


def test_the_derived_kind_sets_are_the_facades():
    assert adapters.TEXT_ONLY_KINDS == llm.TEXT_ONLY_KINDS == {"claude"}
    assert adapters.LISTABLE_KINDS == llm.LISTABLE_KINDS
    assert ({k for k in adapters.KINDS if k not in adapters.TEXT_ONLY_KINDS}
            == set(image_drafts.SUPPORTED_KINDS))


def test_the_claude_default_is_the_stores():
    """An unset Claude model runs the facade's default, and the store builds
    every target's `model` by the same rule (`facts.model_of`)."""
    assert llm.CLAUDE_DEFAULT_MODEL == store.config.DEFAULT_CLAUDE_MODEL
    assert resolve.provider_target({"id": "c", "kind": "claude", "model": ""}).model \
        == llm.CLAUDE_DEFAULT_MODEL


# ---- an adapter sends what the facade sends ----
async def _sends(target: wire.Target, schema: dict | None) -> None:
    """The facade sends, for an attempt's chain, the adapter's own wire call
    handed its target. (Before the facade moved onto the registry this held
    the adapter to the facade's per-kind `_provider`, which it copied; until
    Task 9d it held the dict spelling to it too, whose wire is frozen in
    `test_adapter_wire_golden`.)"""
    via_adapter, via_chain = _clients(), _clients()
    _registry(via_adapter)[target.kind].generate(
        MESSAGES, target, None, schema=schema if target.structured else None)
    await _facade(via_chain).complete(MESSAGES, wire.Chain(target), schema=schema)
    calls = [{k: [(m, a, {**kw, "usage": None}) for m, a, kw in c.calls]
              for k, c in clients.items()} for clients in (via_adapter, via_chain)]
    assert calls[0] == calls[1], target.provider_id
    assert sum(len(c.calls) for c in via_adapter.values()) == 1


@_states()
@_passes()
async def test_a_chain_sends_what_its_adapter_sends(state, stage, tmp_path):
    attempts = [attempt for _where, resolved in _resolved(state, tmp_path, stage=stage)
                if resolved.operation != "embed" for attempt in resolved.attempts]
    assert attempts
    for attempt in attempts:
        for schema in (None, SCHEMA):
            await _sends(attempt.target, schema)


#: What a hand-built target says when it says nothing: no name, key, account
#: or post-processing of its own (the edges were written as connection dicts
#: stating only what they needed, and are held to the wire those dicts sent).
_BARE = {"provider_name": "", "api_key": "", "account": wire.Account(), "post_process": ""}


def hand(provider_id: str, kind: str, model: str, **fields) -> wire.Target:
    """A hand-built attempt of `kind` sending `model`: what `fields` state and
    nothing else (`_BARE`), its post images left to its model (`unknown`), or
    refused by a kind that carries no image part (`no`)."""
    reach = "unknown" if kind in image_drafts.SUPPORTED_KINDS else "no"
    return wire_kit.target(**{**_BARE, "reads_images": reach, "provider_id": provider_id,
                              "kind": kind, "model": model, **fields})


HAND_BUILT = [
    hand("or", "openrouter", "vendor/warm", api_key="sk-test-or",
         sampling=wire.Sampling("warm", "Warm", "global",
                                {"temperature": 0.9, "top_p": 0.8, "reasoning_effort": "high"}),
         model_params=("temperature", "reasoning"), structured=True),
    hand("glm", "openai_compatible", "glm-5.3", api_key="sk-test-glm",
         base_url="https://glm.example.test/v1", post_process="strict",
         sampler_support="extended",
         sampling=wire.Sampling("r", "Reasoning max", "connection",
                                {"reasoning_effort": "max", "repetition_penalty": 1.1}),
         structured=True),
    hand("oa", "openai_compatible", "o4-mini", api_key="sk-test-oa",
         base_url="https://api.openai.com/v1",
         sampling=wire.Sampling("c", "Cold", "campaign",
                                {"max_tokens": 300, "reasoning_effort": "low"})),
    hand("an", "anthropic", "claude-haiku-4-5", api_key="sk-test-an",
         base_url="https://anthropic.example.test",
         sampling=wire.Sampling("w", "Warm", "global",
                                {"temperature": 0.7, "top_p": 0.9, "stop": ["END"]}),
         model_features={"max_tokens": 4000}, structured=True),
    hand("an2", "anthropic", "claude-opus-5", api_key="sk-test-an",
         sampling=wire.Sampling("t", "Think", "global",
                                {"reasoning_effort": "medium", "temperature": 0.5})),
    # An unset Claude model is the default it runs.
    hand("cl", "claude", "opus",
         sampling=wire.Sampling("w", "Warm", "global", {"temperature": 0.7})),
    hand("bare", "openrouter", "vendor/bare", api_key="sk-test-b"),
]


@pytest.mark.parametrize("target", HAND_BUILT, ids=[t.provider_id for t in HAND_BUILT])
@pytest.mark.parametrize("schema", [None, SCHEMA])
async def test_a_hand_built_attempt_of_each_kind_sends_what_its_adapter_sends(target, schema):
    await _sends(target, schema)


@pytest.mark.parametrize("target", HAND_BUILT, ids=[t.provider_id for t in HAND_BUILT])
async def test_models_and_check_ask_what_the_facade_asks(target):
    adapter, facade = _clients(), _clients()
    registry = _registry(adapter)
    if target.kind in adapters.LISTABLE_KINDS:
        await registry[target.kind].models(target)
        await _facade(facade).list_models(target)
    else:
        with pytest.raises(LLMError) as by_adapter:
            await registry[target.kind].models(target)
        with pytest.raises(LLMError) as by_facade:
            await _facade(facade).list_models(target)
        assert (by_adapter.value.kind, by_adapter.value.detail) == (
            by_facade.value.kind, by_facade.value.detail)
    await registry[target.kind].check(target)
    await _facade(facade).check(target)
    assert ({k: c.calls for k, c in adapter.items()}
            == {k: c.calls for k, c in facade.items()})


NATIVE = [
    hand("or", "openrouter", "typesafe/jev-1.13", api_key="sk-test-or"),
    hand("oa", "openai_compatible", "gpt-6-luna", api_key="sk-test-oa",
         base_url="https://decisions.example.test/v1/"),
]


@pytest.mark.parametrize("target", NATIVE, ids=[t.provider_id for t in NATIVE])
async def test_a_native_decide_asks_what_the_facade_asks(target):
    adapter, facade = _clients(), _clients()
    # No holder: the facade stamps the one it is given, which the adapter
    # (a single POST) never does.
    await _registry(adapter)[target.kind].decide(ITEM, target, None, bound=7.0)
    await _facade(facade).decide_native(ITEM, target, None)
    assert ({k: c.calls for k, c in adapter.items()}
            == {k: c.calls for k, c in facade.items()})


@pytest.mark.parametrize("kind", ["anthropic", "claude"])
async def test_a_kind_with_no_native_endpoint_refuses_it(kind):
    registry = _registry(_clients())
    target = hand(kind, kind, "m", api_key="sk-test")
    with pytest.raises(LLMError) as asked:
        await registry[kind].decide(ITEM, target, None)
    with pytest.raises(LLMError) as built:
        registry[kind].decision_body(ITEM, target)
    with pytest.raises(LLMError) as by_facade:
        llm.native_body(ITEM, wire_kit.target(kind=kind, model="m"))
    for exc in (asked.value, built.value):
        assert (exc.kind, exc.detail) == (by_facade.value.kind, by_facade.value.detail)


@_passes()
def test_a_native_body_is_its_adapters(stage, tmp_path):
    registry = _registry(_clients())
    for target in NATIVE:
        assert registry[target.kind].decision_body(ITEM, target) == llm.native_body(ITEM, target)
    seen = 0
    for _where, resolved in _resolved("base:routed", tmp_path, stage=stage):
        for attempt in resolved.attempts:
            if adapters.decides_natively(attempt.target.kind):
                assert (registry[attempt.target.kind].decision_body(ITEM, attempt.target)
                        == llm.native_body(ITEM, attempt.target))
                seen += 1
    assert seen


# ---- `resolve.target_for` ----
@_states()
@_passes()
def test_target_for_matches_the_attempt_target(state, stage, tmp_path):
    """A target built outside any route is the one the resolver builds, but
    for the two stamps only a resolution lays on: the account block's
    `operation`/`role`/`decision_mode`, and the structured flag."""
    lookup = None
    for where, resolved in _resolved(state, tmp_path, stage=stage):
        lookup = lookup or resolve.connection_lookup()
        if resolved.operation == "embed":
            continue
        for attempt in resolved.attempts:
            raw = lookup(attempt.provider_id)
            assert raw is not None, where
            built = resolve.target_for(raw, attempt.model,
                                       dataclasses.asdict(attempt.target.sampling),
                                       model_facts=attempt.facts)
            assert built.account == wire.Account(billing=attempt.target.account.billing)
            assert built.structured is False
            assert dataclasses.replace(built, account=attempt.target.account,
                                       structured=attempt.target.structured) == attempt.target, where


# ---- the facade on chains, with no dict door (Tasks 9b, 9d) ----
_DICT = {"id": "a", "kind": "openrouter", "model": "m", "api_key": "sk-test"}


async def test_the_facade_takes_no_dict():
    """Every door of the facade refuses a connection dict with a `TypeError`,
    before any provider is asked or any holder stamped."""
    clients = _clients()
    facade = _facade(clients)
    usage: dict = {}
    calls = [
        lambda: facade.complete(MESSAGES, _DICT, usage),
        lambda: facade.single(MESSAGES, _DICT, usage),
        lambda: facade.decide_native(ITEM, _DICT, usage),
        lambda: facade.list_models(_DICT),
        lambda: facade.check(_DICT),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            await call()
    with pytest.raises(TypeError):
        [c async for c in facade.stream(MESSAGES, _DICT, usage)]
    with pytest.raises(TypeError):
        facade.note_outcome(_DICT, None)
    with pytest.raises(TypeError):
        llm.native_body(ITEM, _DICT)
    assert usage == {}
    assert all(not c.calls for c in clients.values())


def test_the_facade_reads_no_dict_as_a_chain():
    """`llm.py` no longer reads a lowered dict as a chain anywhere."""
    tree = ast.parse(Path(llm.__file__).read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree)
                if isinstance(node, (ast.Attribute, ast.Name))
                and getattr(node, "attr", getattr(node, "id", "")) == "from_lowered"]


class _Answering:
    """One provider, answering every call with one reply and the counts and
    price a provider reports on its last frame."""

    def __init__(self) -> None:
        self.models: list[str] = []

    async def stream(self, messages, model="", *args, usage=None, **kwargs):
        self.models.append(model)
        yield "{}"
        if usage is not None:
            usage.update({"prompt_tokens": 11, "completion_tokens": 3, "cost_usd": 0.002})


def _filed(conn_or_chain, task: str, schema: dict | None) -> dict:
    """The ledger row one call files, less what differs between any two calls
    (when it ran and how long it took)."""
    provider = _Answering()
    client = llm.LLMClient(openrouter=provider, openai_compatible=provider, anthropic=provider,
                           claude=provider, retries=0, timeout=0)
    with store.usage.meter(task) as m:
        asyncio.run(client.complete(MESSAGES, conn_or_chain, m.usage, schema=schema))
    assert m.row is not None
    return {k: v for k, v in m.row.items() if k not in ("ts", "duration_ms")}


#: The rows `test_the_ledger_row_is_the_lowered_dicts` files, as the lowered
#: dict filed them: recorded at Task 10's base (302eb8e), where the chain and
#: the dict it lowered from were both driven and agreed, before the dict was
#: deleted.
_LOWERED_ROWS = [
    {"billing": "metered", "completion_tokens": 3, "connection": "OpenRouter",
     "cost_basis": "billed", "cost_usd": 0.002, "kind": "llm", "model": "vendor/active",
     "operation": "generate", "prompt_tokens": 11, "provider": "openrouter",
     "provider_id": "openrouter", "role": "primary", "status": "ok", "task": "chat"},
    {"billing": "metered", "completion_tokens": 3, "connection": "OpenRouter",
     "cost_basis": "billed", "cost_usd": 0.002, "decision_mode": "structured", "kind": "llm",
     "model": "vendor/active", "operation": "decide", "prompt_tokens": 11,
     "provider": "openrouter", "provider_id": "openrouter", "role": "decision",
     "status": "ok", "task": "scene-break"},
]


def test_the_ledger_row_is_the_lowered_dicts(tmp_path):
    """A generate call and a structured decide call file, from the
    resolution's chain, the row the lowered dict filed (`_LOWERED_ROWS`) --
    `operation`, `role`, `billing` and `decision_mode` included, so
    `usage_rollup.VERSION` stays where it is."""
    assert usage_rollup.VERSION == 6
    with baseline.client_at(tmp_path) as client:
        fx.format2(client)
        fx.put_settings(client, {"roles": {"decision": {
            "selection": {"provider": "openrouter", "model": "vendor/active"},
            "fallback": {"provider": fx.SPARE[0], "model": fx.SPARE[1]}}}})
        for provider, model in (("openrouter", "vendor/active"), fx.SPARE):
            rev = store.llm_connections.read_connection_raw(provider)["rev"]
            store.llm_connections.set_cached_models(
                provider, [{"id": model, "params": ["temperature", "structured_outputs"]}], rev)
        generate = resolve.resolve("chat")
        decide = resolve.resolve("scene-break", operation="decide")
        assert decide.chain is not None and decide.chain.primary.structured
        filed = [_filed(chain, resolved.task, schema) for resolved, chain, schema in (
            (generate, generate.chain, None),
            (decide, decide.chain.with_account(decision_mode="structured"), SCHEMA))]
        assert filed == _LOWERED_ROWS


async def test_a_chain_hands_its_targets_back():
    """Handed a chain, the facade hands targets to what it calls back -- the
    observer, the image budget, the `ATTEMPTED` stamp -- and the fallback it
    sent is the one named; a connection dict is refused unsent."""
    provider = _Failing({"primary"})
    seen: list = []
    images: list = []
    client = llm.LLMClient(openrouter=provider, retries=0, timeout=0,
                           observer=lambda attempt, error: seen.append(attempt),
                           images=lambda attempt: images.append(attempt) or 0)
    primary = hand("a", "openrouter", "primary")
    backup = hand("b", "openrouter", "backup")
    usage: dict = {}
    assert await client.complete(_REFS, wire.Chain(primary, backup), usage) == "from backup"
    assert seen == [primary, backup] and usage[llm.ATTEMPTED] == backup
    assert all(isinstance(x, wire.Target) for x in images)
    assert all(not x.degrade for x in images)

    seen.clear()
    images.clear()
    as_dict = {"id": "a", "kind": "openrouter", "model": "primary"}
    usage = {}
    with pytest.raises(TypeError):
        await client.complete(_REFS, as_dict, usage)
    assert seen == [] and images == [] and usage == {}


class _Failing:
    def __init__(self, failing: set[str]) -> None:
        self.failing = failing

    async def stream(self, messages, model="", *args, **kwargs):
        if model in self.failing:
            raise LLMError("network", f"{model} down")
        yield f"from {model}"


#: A prompt holding an image reference, so the facade asks the image budget.
_REFS = [{"role": "user", "content": [
    {"type": "text", "text": "Mara looks at the chart."},
    content_parts.ref("/api/campaigns/c/images/chart", "the chart", False)]}]


def test_a_chain_or_a_target_and_nothing_else():
    for other in ("openrouter", {"id": "a", "model": "m"}, None):
        with pytest.raises(TypeError):
            llm._chain_of(other)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            llm._target_of(other)  # type: ignore[arg-type]
    target = wire_kit.target(provider_id="a", model="m")
    assert llm._chain_of(target) == wire.Chain(target)
    assert llm._target_of(target) is target
    chain = wire.Chain(target)
    assert llm._chain_of(chain) is chain
    with pytest.raises(TypeError):
        llm._target_of(chain)  # type: ignore[arg-type]


@_states()
@_passes()
def test_what_reads_an_attempt_reads_its_target(state, stage, tmp_path):
    """The readers the facade hands a target to -- the image budget, the
    health registry, the ledger's account, the prefill rule -- read the
    target's own fields: what the resolver built, nothing re-read."""
    for where, resolved in _resolved(state, tmp_path, stage=stage):
        for attempt in resolved.attempts:
            target = attempt.target
            assert llm.prefill_capable(target) is target.prefill
            assert llm.effective_model(target) == target.model
            if target.reads_images in ("yes", "no") and target.kind in image_drafts.SUPPORTED_KINDS:
                assert store.post_images.capability(target) == target.reads_images, where
            filed: dict = {}
            llm_usage.account(filed, target)
            assert filed.get("provider_id", "") == target.provider_id, where
            assert filed.get("preset", "") == target.sampling.preset_id, where
            for key in llm_usage.ACCOUNT_FIELDS:
                assert filed.get(key, "") == getattr(target.account, key), (where, key)
            registry = health.ProviderHealth()
            status = registry.record(target, LLMError("auth", "refused"))
            if target.provider_id:
                assert registry.status(target.provider_id, target.rev) == status, where


#: Models whose names the preset-sensitive rules read: a Claude version before
#: and after the prefill cut (`providers.ANTHROPIC_PREFILL_UNTIL`), one with
#: none, and models no rule names.
_MODELS = ("claude-haiku-4-5", "claude-opus-4-7", "claude-opus-5", "claude-mythos",
           "gpt-4o", "glm-5.3", "vendor/any", "")


@pytest.mark.parametrize("cap", ["vision", "prefill"])
def test_no_provider_preset_changes_a_vision_or_prefill_answer(cap):
    """A `wire.Target` carries no explicit provider preset, so a reader that
    places one by its kind and URL (`post_images._target_capability`, and a
    tail chooser asking `providers.infer` of a target) must get the answer
    the connection's own preset would give. It does while every preset of a
    kind agrees on the capability -- in `always`, and in what `never_for`
    rules out for any model -- which is what this holds. A preset that
    differs from its kind's others on `vision` or `prefill` fails here, and
    `Target` then has to carry the preset (the 9a/9b review's M4)."""
    for kind in adapters.KINDS:
        of_kind = [p for p in providers.PRESETS.values() if p.kind == kind]
        inferred = providers.infer({"kind": kind})
        for model in _MODELS:
            answers = {(cap in p.always, cap in providers.never_for(p, model)) for p in of_kind}
            assert answers == {(cap in inferred.always,
                                cap in providers.never_for(inferred, model))}, (kind, model)
            for preset in of_kind:
                named = capabilities.resolve_caps(preset, model, catalog_row=None, facts={})
                placed = capabilities.resolve_caps(inferred, model, catalog_row=None, facts={})
                assert named[cap] == placed[cap], (kind, preset.id, model)
