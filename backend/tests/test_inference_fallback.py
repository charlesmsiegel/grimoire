"""Each call carries its own fallback (slice C, Task 6; spec 5.3, 5.4, 5.5).

The resolver decides the fallback per call -- the role the route resolves
through, campaign before global -- and the facade sends exactly that one: the
primary's lowered connection carries the fallback's under `llm.FALLBACK_KEY`.
There is no global fallback on the shipped client any more. A fallback KNOWN
unable to do what the route needs is reported (`fallback_missing`, and still
an attempt) but never attached, so the facade never sends it.

The key is the facade's to read and nobody else's: `LLMClient` strips it
before any adapter, capture, health record, `ATTEMPTED` stamp or preset
refusal sees the dict, and `single` never reads it at all.

Invented connection ids and fake keys only.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import grimoire.store as store
from grimoire import llm, llm_sampling, routes
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import resolve as inf

from . import inference_baseline as base

KEY = llm.FALLBACK_KEY


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
    """A facade wired as `build_llm` wires the shipped one -- no `fallback` --
    over a fake provider."""
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     timeout=0, retries=0, **kw)


def _route(conn_id: str, model: str, **fields) -> dict:
    return {"id": conn_id, "name": f"conn-{conn_id}", "kind": "openrouter", "model": model,
            "api_key": "k", **fields}


# ---- the facade sends the resolver's fallback ----
async def test_the_facade_sends_the_resolved_fallback(at):
    at()
    _format2(**_spare_fallback())
    conn = routes.common.require_inference("chat").conn
    assert conn[KEY]["id"] == "spare" and conn[KEY]["model"] == "vendor/spare"
    # The shipped client holds no fallback of its own.
    assert routes.common.build_llm()._fallback is None
    provider = Recorder(failing={"vendor/active"})
    assert await _facade(provider).complete([], conn) == "from vendor/spare"
    assert provider.models == ["vendor/active", "vendor/spare"]


async def test_a_campaign_role_fallback_is_used_in_that_campaign(at):
    ctx = at()
    _format2(**_spare_fallback())
    _campaign_keys(ctx["cid"], {"role_primary_fallback_provider": "local",
                                "role_primary_fallback_model": "local-model"})
    in_campaign = routes.common.require_inference("chat", ctx["cid"]).conn
    assert in_campaign[KEY]["id"] == "local"
    # Everywhere else the global role's fallback stands.
    assert routes.common.require_inference("chat").conn[KEY]["id"] == "spare"
    provider = Recorder(failing={"vendor/active"})
    assert await _facade(provider).complete([], in_campaign) == "from local-model"
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
    assert image.fallback["id"] == "spare"          # reported
    assert KEY not in image.conn                      # never sent
    sent = routes.common.build_llm()._routes(image.conn)
    assert [conn["id"] for conn, _ in sent] == ["openrouter"]
    # The seam does not refuse over a fallback.
    assert routes.common.require_inference("image-description").conn["id"] == "openrouter"
    # A route that needs nothing the fallback lacks still carries it.
    assert routes.common.require_inference("chat").conn[KEY]["id"] == "spare"
    provider = Recorder(failing={"vendor/active"})
    with pytest.raises(LLMError):
        await _facade(provider).complete([], image.conn)
    assert provider.models == ["vendor/active"]


def test_no_fallback_attaches_nothing(at):
    at()
    _format2()
    conn = inf.resolve("chat").conn
    assert KEY not in conn
    # And a fallback naming the primary's own provider is no fallback.
    _format2(role_primary_fallback_provider="openrouter",
             role_primary_fallback_model="vendor/other")
    assert KEY not in inf.resolve("chat").conn


def test_the_attached_fallback_is_the_fallback_attempts_conn(at):
    at()
    _format2(**_spare_fallback())
    resolved = inf.resolve("chat")
    assert resolved.conn[KEY] is resolved.attempts[1].conn
    assert KEY not in resolved.attempts[1].conn


def test_the_primary_carries_the_retry_budget_and_the_fallback_none(at):
    at()
    _format2(**_spare_fallback())
    store.write_config(llm_retries="4")
    resolved = inf.resolve("chat")
    assert [a.retries for a in resolved.attempts] == [store.config.llm_retries(), 0]
    assert resolved.attempts[0].retries == 4


def test_the_fallback_key_is_the_facades():
    assert inf.FALLBACK_KEY == llm.FALLBACK_KEY == "_fallback"


# ---- an override preset is the primary's alone (ruling 1) ----
def test_an_override_preset_leaves_the_fallback_the_route_preset(at):
    """The fallback gets what it would have had without the override: the
    route's preset when the standing route has one."""
    ctx = at()
    _format2(**_spare_fallback(role_primary_fallback_preset="warm"), preset_scene="cold")
    standing, _ = routes.common.override_inference(None, "regenerate", ctx["cid"])
    assert standing.conn[KEY]["sampling"]["preset_id"] == "cold"
    body = SimpleNamespace(preset="warm")
    resolved, routed = routes.common.override_inference(body, "regenerate", ctx["cid"])
    assert routed is True
    assert resolved.conn["sampling"]["scope"] == "override"
    fallback = resolved.conn[KEY]
    assert (fallback["id"], fallback["sampling"]["preset_id"],
            fallback["sampling"]["scope"]) == ("spare", "cold", "global")
    assert resolved.attempts[1].preset_id == "cold"
    # And the facade sends that.
    sent = routes.common.build_llm()._routes(resolved.conn)
    assert sent[1][0]["sampling"]["preset_id"] == "cold"


def test_an_override_preset_with_no_route_preset_leaves_the_fallback_its_own(at):
    ctx = at()
    _format2(**_spare_fallback(role_primary_fallback_preset="warm"))
    body = SimpleNamespace(preset="cold")
    resolved, _ = routes.common.override_inference(body, "regenerate", ctx["cid"])
    assert resolved.conn["sampling"]["preset_id"] == "cold"
    fallback = resolved.conn[KEY]
    assert (fallback["sampling"]["preset_id"], fallback["sampling"]["scope"]) == (
        "warm", "connection")


# ---- the key stops at the facade's boundary ----
async def test_the_fallback_key_reaches_no_adapter_capture_or_health_record(monkeypatch):
    """Every consumer of an attempt's dict sees the attempt, never the chain."""
    seen: dict[str, list[dict]] = {"adapter": [], "observer": [], "stamp": [],
                                   "sent_fields": []}
    events: list[dict] = []
    real_provider = LLMClient._provider
    real_stamp = llm._stamp
    real_sent = llm_sampling.sent_fields

    def provider_spy(self, messages, conn, usage, schema=None):
        seen["adapter"].append(conn)
        return real_provider(self, messages, conn, usage, schema)

    def stamp_spy(usage, conn, attempts):
        seen["stamp"].append(conn)
        real_stamp(usage, conn, attempts)

    def sent_spy(conn):
        seen["sent_fields"].append(conn)
        return real_sent(conn)

    monkeypatch.setattr(LLMClient, "_provider", provider_spy)
    monkeypatch.setattr(llm, "_stamp", stamp_spy)
    monkeypatch.setattr(llm.llm_sampling, "sent_fields", sent_spy)

    # A 400 that names nothing the preset sent: the refusal check runs, and the
    # call still falls back.
    provider = Recorder(failing={"primary"},
                        error=LLMError("bad_response", "context is too long", status=400))
    facade = _facade(provider, observer=lambda conn, err: seen["observer"].append(conn),
                     capture=lambda: events.append)
    fallback = _route("b", "backup")
    conn = {**_route("a", "primary"),
            "sampling": {"preset_id": "warm", "preset_name": "Warm", "scope": "connection",
                         "params": {"temperature": 0.7}},
            KEY: fallback}
    usage: dict = {}
    assert await facade.complete([], conn, usage) == "from backup"
    assert provider.models == ["primary", "backup"]

    assert [c["id"] for c in seen["adapter"]] == ["a", "b"]
    assert [c["id"] for c in seen["stamp"]] == ["a", "b"]
    assert [c["id"] for c in seen["observer"]] == ["a", "b"]
    assert [c["id"] for c in seen["sent_fields"]] == ["a"]
    assert usage[llm.ATTEMPTED]["id"] == "b"
    for where, conns in seen.items():
        assert all(KEY not in c for c in conns), where
    assert KEY not in usage[llm.ATTEMPTED]
    assert events and "_fallback" not in json.dumps(events, default=str)
    # The caller's dict is left as it was handed in.
    assert conn[KEY] is fallback

    # A preset refusal, on a dict that carries a fallback: refused, and the
    # fallback never tried.
    refusing = Recorder(failing={"primary"},
                        error=LLMError("bad_response", "temperature is not supported",
                                       status=400))
    with pytest.raises(llm.PresetRefusalError):
        await _facade(refusing).complete([], conn)
    assert refusing.models == ["primary"]
    assert all(KEY not in c for c in seen["sent_fields"])

    # And an outcome filed from outside the facade is filed for the attempt.
    seen["observer"].clear()
    facade.note_outcome(conn, LLMError("timeout", "overran"))
    assert seen["observer"] and KEY not in seen["observer"][0]


async def test_single_never_uses_a_fallback(monkeypatch):
    adapter: list[dict] = []
    real_provider = LLMClient._provider

    def provider_spy(self, messages, conn, usage, schema=None):
        adapter.append(conn)
        return real_provider(self, messages, conn, usage, schema)

    monkeypatch.setattr(LLMClient, "_provider", provider_spy)
    provider = Recorder(failing={"primary"})
    conn = {**_route("a", "primary"), KEY: _route("b", "backup")}
    with pytest.raises(LLMError):
        await _facade(provider, fallback=lambda: _route("c", "standing")).single([], conn)
    assert provider.models == ["primary"]
    assert [c["id"] for c in adapter] == ["a"] and KEY not in adapter[0]


async def test_a_client_built_with_a_fallback_still_uses_it():
    provider = Recorder(failing={"primary"})
    facade = _facade(provider, fallback=lambda: _route("b", "backup"))
    assert await facade.complete([], _route("a", "primary")) == "from backup"
    assert provider.models == ["primary", "backup"]
    # A call that carries its own fallback is sent that one instead.
    provider.models.clear()
    conn = {**_route("a", "primary"), KEY: _route("c", "carried")}
    assert await facade.complete([], conn) == "from carried"
    assert provider.models == ["primary", "carried"]
