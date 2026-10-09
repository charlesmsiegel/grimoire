"""Each call carries its own fallback (slice C, Task 6; spec 5.3, 5.4, 5.5).

The resolver decides the fallback per call -- the role the route resolves
through, campaign before global -- and the facade sends exactly that one: the
resolution's chain carries the fallback's target (`wire.Chain.fallback`).
There is no global fallback on the shipped client any more. A fallback KNOWN
unable to do what the route needs is reported (`fallback_missing`, and still
an attempt) but never rides, so the facade never sends it.

The chain is the facade's to read and nobody else's: `LLMClient` hands each
adapter, capture, health record, `ATTEMPTED` stamp and preset refusal one
attempt's target, and `single` takes no chain at all.

Invented connection ids and fake keys only.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import grimoire.store as store
from grimoire import llm, llm_sampling, routes, wire
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import resolve as inf

from . import inference_baseline as base


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def at(tmp_path):
    """`at()` builds the baseline's `fresh` store -- `openrouter` (key, model
    `vendor/active`) -- plus `spare` (OpenRouter, `vendor/spare`), `local`
    (OpenAI-compatible, `local-model`) and the presets warm and cold, and
    returns its context (`cid`). Still format 1: each test sets its layout."""
    cms = []

    def build() -> dict:
        cm = base.client_at(tmp_path / "store")
        client = cm.__enter__()
        cms.append(cm)
        ctx = base._fresh(client)
        base._presets()
        base._spare(client)
        base._local(client)
        return ctx

    yield build
    for cm in reversed(cms):
        cm.__exit__(None, None, None)


def _format2(**fields: str) -> None:
    store.write_config(inference_format="2", role_primary_provider="openrouter",
                       role_primary_model="vendor/active", **fields)
    assert store.inference_keys.is_current(store.read_config())


def _spare_fallback(**fields: str) -> dict:
    return {"role_primary_fallback_provider": "spare",
            "role_primary_fallback_model": "vendor/spare", **fields}


def _campaign_keys(cid: str, fields: dict) -> None:
    """Write current-layout keys (and the campaign's marker) into a campaign's
    frontmatter, as a migrated campaign holds them."""
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    marked = {**meta, store.inference_keys.FORMAT_KEY: store.inference_keys.CURRENT_FORMAT,
              **fields}
    path.write_text(dump_frontmatter(marked, body), encoding="utf-8")


def _catalog(conn_id: str, models: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, models, rev)


class Recorder:
    """One provider standing in for every connection: remembers the model each
    attempt asked for, and fails the ones in `failing`."""

    def __init__(self, failing=(), error=None):
        self.failing = set(failing)
        self.error = error
        self.models: list[str] = []
        self.calls: list[tuple] = []

    async def stream(self, messages, model="", *args, **kwargs):
        self.models.append(model)
        self.calls.append((model, args, kwargs))
        if model in self.failing:
            raise self.error or LLMError("rate_limit", f"{model} is busy")
        yield f"from {model}"


def _facade(provider, **kw) -> LLMClient:
    """A facade wired as `build_llm` wires the shipped one over a fake
    provider: no fallback of its own, as no client has one."""
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     timeout=0, retries=0, **kw)


def _route(conn_id: str, model: str, **fields) -> wire.Target:
    return wire.Target(**{"provider_id": conn_id, "provider_name": f"conn-{conn_id}",
                          "kind": "openrouter", "model": model, "requested_model": model,
                          "api_key": "k", **fields})


# ---- the facade sends the resolver's fallback ----
async def test_the_facade_sends_the_resolved_fallback(at):
    at()
    _format2(**_spare_fallback())
    resolved = routes.common.require_inference("chat")
    fallback = resolved.chain.fallback
    assert fallback.provider_id == "spare" and fallback.model == "vendor/spare"
    # The shipped client holds no fallback of its own: it has none to hold.
    assert not hasattr(routes.common.build_llm(), "_fallback")
    provider = Recorder(failing={"vendor/active"})
    assert await _facade(provider).complete([], resolved.chain) == "from vendor/spare"
    assert provider.models == ["vendor/active", "vendor/spare"]


async def test_a_campaign_role_fallback_is_used_in_that_campaign(at):
    ctx = at()
    _format2(**_spare_fallback())
    _campaign_keys(ctx["cid"], {"role_primary_fallback_provider": "local",
                                "role_primary_fallback_model": "local-model"})
    in_campaign = routes.common.require_inference("chat", ctx["cid"])
    assert in_campaign.chain.fallback.provider_id == "local"
    # Everywhere else the global role's fallback stands.
    assert routes.common.require_inference("chat").chain.fallback.provider_id == "spare"
    provider = Recorder(failing={"vendor/active"})
    assert await _facade(provider).complete([], in_campaign.chain) == "from local-model"
    assert provider.models == ["vendor/active", "local-model"]


async def test_an_incapable_fallback_is_not_sent_and_is_reported(at):
    """Spec 5.3: a fallback known unable to do what the route needs is dropped
    from the chain -- and still reported, so a surface can say why."""
    at()
    _format2(**_spare_fallback())
    _catalog("openrouter", [{"id": "vendor/active", "vision": True}])
    _catalog("spare", [{"id": "vendor/spare", "vision": False}])
    image = inf.resolve("image-description")
    assert image.missing == () and image.fallback_missing == ("vision",)
    assert image.attempts[1].provider_id == "spare"   # reported
    assert image.chain.fallback is None                # never sent
    sent = routes.common.build_llm()._routes(image.chain)
    assert [route.target.provider_id for route in sent] == ["openrouter"]
    # The seam does not refuse over a fallback.
    assert routes.common.require_inference(
        "image-description").chain.primary.provider_id == "openrouter"
    # A route that needs nothing the fallback lacks still carries it.
    assert routes.common.require_inference("chat").chain.fallback.provider_id == "spare"
    provider = Recorder(failing={"vendor/active"})
    with pytest.raises(LLMError):
        await _facade(provider).complete([], image.chain)
    assert provider.models == ["vendor/active"]


def test_no_fallback_rides_nothing(at):
    at()
    _format2()
    assert inf.resolve("chat").chain.fallback is None
    # And a fallback naming the primary's own provider is no fallback.
    _format2(role_primary_fallback_provider="openrouter",
             role_primary_fallback_model="vendor/other")
    assert inf.resolve("chat").chain.fallback is None


def test_the_riding_fallback_is_the_fallback_attempts_target(at):
    at()
    _format2(**_spare_fallback())
    resolved = inf.resolve("chat")
    assert resolved.rides
    assert resolved.chain.fallback is resolved.attempts[1].target


def test_the_primary_carries_the_retry_budget_and_the_fallback_none(at):
    at()
    _format2(**_spare_fallback())
    store.write_config(llm_retries="4")
    resolved = inf.resolve("chat")
    assert [a.retries for a in resolved.attempts] == [store.config.llm_retries(), 0]
    assert resolved.attempts[0].retries == 4


# ---- an override preset is the primary's alone (ruling 1) ----
def test_an_override_preset_leaves_the_fallback_the_route_preset(at):
    """The fallback gets what it would have had without the override: the
    route's preset when the standing route has one."""
    ctx = at()
    _format2(**_spare_fallback(role_primary_fallback_preset="warm"), preset_scene="cold")
    standing, _ = routes.common.override_inference(None, "regenerate", ctx["cid"])
    assert standing.chain.fallback.sampling.preset_id == "cold"
    body = SimpleNamespace(preset="warm")
    resolved, routed = routes.common.override_inference(body, "regenerate", ctx["cid"])
    assert routed is True
    assert resolved.chain.primary.sampling.scope == "override"
    fallback = resolved.chain.fallback
    assert (fallback.provider_id, fallback.sampling.preset_id,
            fallback.sampling.scope) == ("spare", "cold", "global")
    assert resolved.attempts[1].preset_id == "cold"
    # And the facade sends that.
    sent = routes.common.build_llm()._routes(resolved.chain)
    assert sent[1].target.sampling.preset_id == "cold"


def test_an_override_preset_with_no_route_preset_leaves_the_fallback_its_own(at):
    ctx = at()
    _format2(**_spare_fallback(role_primary_fallback_preset="warm"))
    body = SimpleNamespace(preset="cold")
    resolved, _ = routes.common.override_inference(body, "regenerate", ctx["cid"])
    assert resolved.chain.primary.sampling.preset_id == "cold"
    fallback = resolved.chain.fallback
    assert (fallback.sampling.preset_id, fallback.sampling.scope) == ("warm", "connection")


# ---- the chain stops at the facade's boundary ----
async def test_the_fallback_reaches_no_adapter_capture_or_health_record_but_its_own(monkeypatch):
    """Every consumer of an attempt sees the attempt, never the chain: an
    adapter, the preset refusal, the stamp and the observer are each handed
    that attempt's `wire.Target`, which has no room for a fallback."""
    seen: dict[str, list] = {"adapter": [], "observer": [], "stamp": [],
                             "sent_fields": []}
    events: list[dict] = []
    real_generate = LLMClient._generate
    real_stamp = llm._stamp
    real_sent = llm_sampling.sent_fields

    def generate_spy(self, messages, target, usage, schema=None):
        seen["adapter"].append(target)
        return real_generate(self, messages, target, usage, schema)

    def stamp_spy(usage, route, attempts):
        seen["stamp"].append(route.target)
        real_stamp(usage, route, attempts)

    def sent_spy(target):
        seen["sent_fields"].append(target)
        return real_sent(target)

    monkeypatch.setattr(LLMClient, "_generate", generate_spy)
    monkeypatch.setattr(llm, "_stamp", stamp_spy)
    monkeypatch.setattr(llm.llm_sampling, "sent_fields", sent_spy)

    # A 400 that names nothing the preset sent: the refusal check runs, and the
    # call still falls back.
    provider = Recorder(failing={"primary"},
                        error=LLMError("bad_response", "context is too long", status=400))
    facade = _facade(provider, observer=lambda conn, err: seen["observer"].append(conn),
                     capture=lambda: events.append)
    fallback = _route("b", "backup")
    conn = wire.Chain(_route("a", "primary", sampling=wire.Sampling(
        preset_id="warm", preset_name="Warm", scope="connection",
        params={"temperature": 0.7})), fallback)
    usage: dict = {}
    assert await facade.complete([], conn, usage) == "from backup"
    assert provider.models == ["primary", "backup"]

    assert [t.provider_id for t in seen["adapter"]] == ["a", "b"]
    assert [t.provider_id for t in seen["stamp"]] == ["a", "b"]
    assert [t.provider_id for t in seen["observer"]] == ["a", "b"]
    assert [t.provider_id for t in seen["sent_fields"]] == ["a"]
    assert usage[llm.ATTEMPTED] == fallback
    for where in ("adapter", "sent_fields", "stamp", "observer"):
        assert all(isinstance(t, wire.Target) for t in seen[where]), where
    assert events and "_fallback" not in json.dumps(events, default=str)

    # A preset refusal, on a chain that carries a fallback: refused, and the
    # fallback never tried.
    refusing = Recorder(failing={"primary"},
                        error=LLMError("bad_response", "temperature is not supported",
                                       status=400))
    with pytest.raises(llm.PresetRefusalError):
        await _facade(refusing).complete([], conn)
    assert refusing.models == ["primary"]
    assert all(isinstance(t, wire.Target) for t in seen["sent_fields"])

    # And an outcome filed from outside the facade is filed for the attempt.
    seen["observer"].clear()
    facade.note_outcome(conn.primary, LLMError("timeout", "overran"))
    assert seen["observer"] == [conn.primary]
    with pytest.raises(TypeError):
        facade.note_outcome(conn, LLMError("timeout", "overran"))  # type: ignore[arg-type]
    assert seen["observer"] == [conn.primary]


async def test_single_never_uses_a_fallback(monkeypatch):
    adapter: list[wire.Target] = []
    real_generate = LLMClient._generate

    def generate_spy(self, messages, target, usage, schema=None):
        adapter.append(target)
        return real_generate(self, messages, target, usage, schema)

    monkeypatch.setattr(LLMClient, "_generate", generate_spy)
    provider = Recorder(failing={"primary"})
    with pytest.raises(LLMError):
        await _facade(provider).single([], _route("a", "primary"))
    assert provider.models == ["primary"]
    assert [t.provider_id for t in adapter] == ["a"]
    # A chain carrying a fallback is refused unsent: `single` takes one target.
    with pytest.raises(TypeError):
        await _facade(provider).single([], wire.Chain(_route("a", "primary"),  # type: ignore[arg-type]
                                                      _route("b", "backup")))
    assert provider.models == ["primary"] and len(adapter) == 1


async def test_a_call_carrying_a_fallback_is_sent_that_one():
    """The client holds no fallback of its own: each call's is the one it
    carries, and a call that carries none has none."""
    provider = Recorder(failing={"primary"})
    facade = _facade(provider)
    conn = wire.Chain(_route("a", "primary"), _route("c", "carried"))
    assert await facade.complete([], conn) == "from carried"
    assert provider.models == ["primary", "carried"]
    provider.models.clear()
    with pytest.raises(LLMError):
        await facade.complete([], _route("a", "primary"))
    assert provider.models == ["primary"]
