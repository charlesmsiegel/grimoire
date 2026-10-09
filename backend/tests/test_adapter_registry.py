"""The adapter registry (`grimoire.adapters`, slice I Task 9): one adapter per
connection kind, sending a typed `wire.Target`, built beside the facade.

What is held here:

- the registry covers every kind the store knows, and its capability flags
  agree with the store's own facts about each kind (`providers.PRESETS`'
  `never`, `resolve.embed_endpoint`, `image_drafts.SUPPORTED_KINDS`) and with
  slice H's native table;
- an adapter sends exactly what the facade sends today for the same attempt
  -- every resolved attempt of every frozen baseline state, in memory and
  after the migration, and a hand-built attempt of each kind;
- `resolve.target_for` builds the target a resolved attempt carries;
- `wire.from_lowered` reads a resolved attempt's dict back as its chain, and
  `llm_sampling` answers the same for a dict and its target.
"""

from __future__ import annotations

import dataclasses

import pytest

from grimoire import adapters, decisions, llm, llm_sampling, llm_usage, wire
from grimoire.decisions import Choice, Item, Option, Predicate, Score
from grimoire.llm_errors import LLMError
from grimoire.store import image_drafts
from grimoire.store.inference import providers, resolve

from . import inference_baseline as baseline
from . import inference_baseline_c as baseline_c
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


def _resolved(state: str, tmp_path, *, migrated: bool):
    """Every resolution `state` produces (`test_inference_target`'s), in
    memory or after the settings migration."""
    family, name = STATES[state]
    with baseline.client_at(tmp_path) as client:
        ctx = family.STATES[name](client)
        if migrated:
            assert baseline.migrate_state(name).state == "done"
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


def test_decides_natively_is_the_native_table():
    registry = _registry(_clients())
    assert ({k for k, a in registry.items() if a.decides_natively}
            == set(llm.NATIVE_DECISION_KINDS))


def test_the_derived_kind_sets_are_the_facades():
    assert adapters.TEXT_ONLY_KINDS == llm.TEXT_ONLY_KINDS == {"claude"}
    assert adapters.LISTABLE_KINDS == llm.LISTABLE_KINDS
    assert ({k for k in adapters.KINDS if k not in adapters.TEXT_ONLY_KINDS}
            == set(image_drafts.SUPPORTED_KINDS) == wire._IMAGE_KINDS)


def test_the_wire_spells_the_dict_keys_as_their_owners_do():
    assert wire._FALLBACK == llm.FALLBACK_KEY == resolve.FALLBACK_KEY
    assert wire._STRUCTURED == llm.STRUCTURED_KEY == resolve.STRUCTURED_KEY
    assert wire._ACCOUNT == llm_usage.ACCOUNT_KEY == resolve.ACCOUNT_KEY
    assert wire._DEGRADE == llm.DEGRADE
    assert wire.CLAUDE_DEFAULT_MODEL == llm.CLAUDE_DEFAULT_MODEL


# ---- an adapter sends what the facade sends ----
def _sends(attempt_conn: dict, target: wire.Target, schema: dict | None) -> None:
    """The adapter, handed the target, calls its client exactly as the
    facade's per-kind dispatch does when handed the dict."""
    via_adapter, via_facade = _clients(), _clients()
    _registry(via_adapter)[target.kind].generate(
        MESSAGES, target, {}, schema=schema if target.structured else None)
    _facade(via_facade)._provider(MESSAGES, attempt_conn, {}, schema)
    assert ({k: c.calls for k, c in via_adapter.items()}
            == {k: c.calls for k, c in via_facade.items()}), target.provider_id
    assert sum(len(c.calls) for c in via_adapter.values()) == 1


@_states()
@pytest.mark.parametrize("migrated", [False, True])
def test_a_chain_sends_what_the_lowered_dict_sent(state, migrated, tmp_path):
    sent = 0
    for _where, resolved in _resolved(state, tmp_path, migrated=migrated):
        for attempt in resolved.attempts:
            if resolved.operation == "embed":
                continue
            for schema in (None, SCHEMA):
                _sends(attempt.conn, attempt.target, schema)
                sent += 1
    assert sent


HAND_BUILT = [
    {"id": "or", "kind": "openrouter", "model": "vendor/warm", "api_key": "sk-test-or",
     "sampling": {"preset_id": "warm", "preset_name": "Warm", "scope": "global",
                  "params": {"temperature": 0.9, "top_p": 0.8, "reasoning_effort": "high"}},
     "model_params": ["temperature", "reasoning"], llm.STRUCTURED_KEY: True},
    {"id": "glm", "kind": "openai_compatible", "model": "glm-5.3", "api_key": "sk-test-glm",
     "base_url": "https://glm.example.test/v1", "post_process": "strict",
     "sampler_support": "extended",
     "sampling": {"preset_id": "r", "preset_name": "Reasoning max", "scope": "connection",
                  "params": {"reasoning_effort": "max", "repetition_penalty": 1.1}},
     llm.STRUCTURED_KEY: True},
    {"id": "oa", "kind": "openai_compatible", "model": "o4-mini", "api_key": "sk-test-oa",
     "base_url": "https://api.openai.com/v1",
     "sampling": {"preset_id": "c", "preset_name": "Cold", "scope": "campaign",
                  "params": {"max_tokens": 300, "reasoning_effort": "low"}}},
    {"id": "an", "kind": "anthropic", "model": "claude-haiku-4-5", "api_key": "sk-test-an",
     "base_url": "https://anthropic.example.test",
     "sampling": {"preset_id": "w", "preset_name": "Warm", "scope": "global",
                  "params": {"temperature": 0.7, "top_p": 0.9, "stop": ["END"]}},
     "model_features": {"max_tokens": 4000}, llm.STRUCTURED_KEY: True},
    {"id": "an2", "kind": "anthropic", "model": "claude-opus-5", "api_key": "sk-test-an",
     "sampling": {"preset_id": "t", "preset_name": "Think", "scope": "global",
                  "params": {"reasoning_effort": "medium", "temperature": 0.5}}},
    {"id": "cl", "kind": "claude", "model": "",
     "sampling": {"preset_id": "w", "preset_name": "Warm", "scope": "global",
                  "params": {"temperature": 0.7}}},
    {"id": "bare", "kind": "openrouter", "model": "vendor/bare", "api_key": "sk-test-b"},
]


@pytest.mark.parametrize("conn", HAND_BUILT, ids=[c["id"] for c in HAND_BUILT])
@pytest.mark.parametrize("schema", [None, SCHEMA])
def test_a_hand_built_attempt_of_each_kind_sends_what_its_dict_sent(conn, schema):
    _sends(conn, wire.from_lowered(conn).primary, schema)


def _target(conn: dict) -> wire.Target:
    return wire.from_lowered(conn).primary


@pytest.mark.parametrize("conn", HAND_BUILT, ids=[c["id"] for c in HAND_BUILT])
async def test_models_and_check_ask_what_the_facade_asks(conn):
    target = _target(conn)
    adapter, facade = _clients(), _clients()
    registry = _registry(adapter)
    if target.kind in adapters.LISTABLE_KINDS:
        await registry[target.kind].models(target)
        await _facade(facade).list_models(conn)
    else:
        with pytest.raises(LLMError) as by_adapter:
            await registry[target.kind].models(target)
        with pytest.raises(LLMError) as by_facade:
            await _facade(facade).list_models(conn)
        assert (by_adapter.value.kind, by_adapter.value.detail) == (
            by_facade.value.kind, by_facade.value.detail)
    await registry[target.kind].check(target)
    await _facade(facade).check(conn)
    assert ({k: c.calls for k, c in adapter.items()}
            == {k: c.calls for k, c in facade.items()})


NATIVE = [
    {"id": "or", "kind": "openrouter", "model": "typesafe/jev-1.13", "api_key": "sk-test-or"},
    {"id": "oa", "kind": "openai_compatible", "model": "gpt-6-luna", "api_key": "sk-test-oa",
     "base_url": "https://decisions.example.test/v1/"},
]


@pytest.mark.parametrize("conn", NATIVE, ids=[c["id"] for c in NATIVE])
async def test_a_native_decide_asks_what_the_facade_asks(conn):
    target = _target(conn)
    adapter, facade = _clients(), _clients()
    # No holder: the facade stamps the one it is given, which the adapter
    # (a single POST) never does.
    await _registry(adapter)[target.kind].decide(ITEM, target, None, bound=7.0)
    await _facade(facade).decide_native(ITEM, conn, None)
    assert ({k: c.calls for k, c in adapter.items()}
            == {k: c.calls for k, c in facade.items()})


@pytest.mark.parametrize("kind", ["anthropic", "claude"])
async def test_a_kind_with_no_native_endpoint_refuses_it(kind):
    registry = _registry(_clients())
    target = _target({"id": kind, "kind": kind, "model": "m", "api_key": "sk-test"})
    with pytest.raises(LLMError) as asked:
        await registry[kind].decide(ITEM, target, None)
    with pytest.raises(LLMError) as built:
        registry[kind].decision_body(ITEM, target)
    with pytest.raises(LLMError) as by_facade:
        llm.native_body(ITEM, {"kind": kind, "model": "m"})
    for exc in (asked.value, built.value):
        assert (exc.kind, exc.detail) == (by_facade.value.kind, by_facade.value.detail)


def test_a_native_body_is_the_same_from_a_target(tmp_path):
    registry = _registry(_clients())
    for conn in NATIVE:
        target = _target(conn)
        assert registry[target.kind].decision_body(ITEM, target) == llm.native_body(ITEM, conn)
    seen = 0
    for _where, resolved in _resolved("base:routed", tmp_path, migrated=True):
        for attempt in resolved.attempts:
            if attempt.target.kind in llm.NATIVE_DECISION_KINDS:
                assert (registry[attempt.target.kind].decision_body(ITEM, attempt.target)
                        == llm.native_body(ITEM, attempt.conn))
                seen += 1
    assert seen


# ---- the dict, read as its chain ----
@_states()
@pytest.mark.parametrize("migrated", [False, True])
def test_from_lowered_round_trips_every_baseline_attempt(state, migrated, tmp_path):
    for where, resolved in _resolved(state, tmp_path, migrated=migrated):
        if resolved.chain is None:
            continue
        assert wire.from_lowered(resolved.conn) == resolved.chain, where


@_states()
@pytest.mark.parametrize("migrated", [False, True])
def test_effective_answers_the_same_for_a_dict_and_its_target(state, migrated, tmp_path):
    for where, resolved in _resolved(state, tmp_path, migrated=migrated):
        for attempt in resolved.attempts:
            for answer in (llm_sampling.effective, llm_sampling.split,
                           llm_sampling.sent_fields, llm_sampling.sent_names):
                assert answer(attempt.conn) == answer(attempt.target), (where, answer)
            assert (llm_sampling.not_applicable(attempt.conn, llm_sampling.WHY_NATIVE)
                    == llm_sampling.not_applicable(attempt.target, llm_sampling.WHY_NATIVE))


def test_from_lowered_reads_a_dict_as_the_facade_does():
    """The facade's own readings: a `kind` left off is OpenRouter's, an unset
    Claude model is the one it runs, a mistyped field is unset, a falsy
    fallback is none, and the post-image preference decides what it can."""
    bare = wire.from_lowered({"model": "vendor/m"})
    assert (bare.primary.kind, bare.primary.model, bare.fallback) == ("openrouter", "vendor/m", None)
    assert bare.primary.reads_images == "unknown"
    claude = wire.from_lowered({"kind": "claude", "vision": "on", llm.FALLBACK_KEY: {}})
    assert (claude.primary.model, claude.primary.requested_model) == ("opus", "opus")
    assert claude.primary.reads_images == "no" and claude.fallback is None
    odd = wire.from_lowered({"id": 3, "sampling": "nope", "model_params": "x",
                             llm_usage.ACCOUNT_KEY: "not a block", "vision": "off"})
    assert odd.primary.provider_id == "" and odd.primary.sampling == wire.Sampling()
    assert odd.primary.model_params is None and odd.primary.account == wire.Account()
    assert odd.primary.reads_images == "no"
    chained = wire.from_lowered({"id": "a", "kind": "openrouter", "model": "m", "vision": "on",
                                 llm.DEGRADE: True,
                                 llm.FALLBACK_KEY: {"id": "b", "model": "n"}})
    assert chained.primary.reads_images == "yes" and chained.primary.degrade is True
    assert chained.fallback is not None and chained.fallback.provider_id == "b"


# ---- `resolve.target_for` ----
@_states()
@pytest.mark.parametrize("migrated", [False, True])
def test_target_for_matches_the_attempt_target(state, migrated, tmp_path):
    """A target built outside any route is the one the resolver builds, but
    for the two stamps only a resolution lays on: the account block's
    `operation`/`role`/`decision_mode`, and the structured flag."""
    lookup = None
    for where, resolved in _resolved(state, tmp_path, migrated=migrated):
        lookup = lookup or resolve.connection_lookup()
        if resolved.operation == "embed":
            continue
        for attempt in resolved.attempts:
            raw = lookup(attempt.provider_id)
            assert raw is not None, where
            built = resolve.target_for(raw, attempt.model, attempt.conn["sampling"],
                                       model_facts=attempt.facts)
            assert built.account == wire.Account(billing=attempt.target.account.billing)
            assert built.structured is False
            assert dataclasses.replace(built, account=attempt.target.account,
                                       structured=attempt.target.structured) == attempt.target, where
