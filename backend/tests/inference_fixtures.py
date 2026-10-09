"""Model-settings scaffolding the decide conversions' route tests share.

Slice F moves three calls onto `decide()` and the Decision role -- the
scene-break check, the voice-drift judge and the speaker pick -- and each
proves the same three things against a format-2 store: the Decision role is
what serves it, a decide-only Decision model is answered natively (slice H),
falling to a generating role fallback when that fails, and a model that can
neither generate nor decide natively is refused by the seam. The store those
tests stand on is built here once, through the HTTP API, rather than in each
suite.
"""

from __future__ import annotations

import grimoire.store as store
from grimoire.store.inference import migrate


def put_settings(client, body: dict) -> None:
    """`PUT /api/inference/settings` with `body`, which must be accepted."""
    got = client.put("/api/inference/settings", json=body)
    assert got.status_code == 200, got.text


def format2(client) -> None:
    """A format-2 store: the seeded `openrouter` provider at `vendor/active`
    with a key, a `spare` provider at `vendor/spare`, migrated."""
    client.put("/api/llm-connections/openrouter",
               json={"api_key": "sk-test-active", "model": "vendor/active"})
    client.post("/api/llm-connections", json={"kind": "openrouter", "name": "spare",
                                              "api_key": "sk-spare",
                                              "model": "vendor/spare"})
    assert migrate.ensure().state == "done"


#: Where `decide_only`'s generating fallback is: on another provider, or on
#: the decide-only model's own (one OpenRouter account, two of its models).
SPARE = ("spare", "vendor/spare")
SAME_PROVIDER = ("openrouter", "vendor/active")


def decide_only(client, *, fallback: bool, on: tuple[str, str] = SPARE) -> None:
    """The Decision role on an OpenRouter model whose catalog row says it only
    decides -- served natively (`decision_mode == "native"`) -- with a
    generating fallback (`on`: `spare` at `vendor/spare`, or `SAME_PROVIDER`)
    or none."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": ({"provider": on[0], "model": on[1]} if fallback
                     else {"provider": ""})}}})


#: The model `neither` puts on the Decision role.
NEITHER = ("openrouter", "vendor/neither")


def neither(client) -> None:
    """The Decision role, with no fallback, on an OpenRouter model known
    unable to do either thing a decision needs (I8): its catalog `outputs`
    lacks `text` (`generate: no`), and the user has overridden
    `decide_native` to `no`. Both are known `no`, so a decide resolution is
    refused with 409 `incapable`.

    The override is what makes this model: the catalog alone cannot. An
    OpenRouter catalog row only ever says `decide_native: yes` (`decisions`
    in its outputs); a list without it says nothing (`capabilities.py`:
    "absence says nothing"), so a non-text row leaves `decide_native`
    unknown, and that model is served natively."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": NEITHER[1], "outputs": ["image"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": NEITHER[1], "overrides": {"decide_native": "no"}})
    assert got.status_code == 200, got.text
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": NEITHER[0], "model": NEITHER[1]},
        "fallback": {"provider": ""}}}})


#: A decisions-only model on the OpenAI preset (invented id).
OPENAI_DECIDER = "gpt-test-decider"


def openai_decides_only(client) -> str:
    """The Decision role on an OpenAI-preset provider's decisions-only model,
    returning the provider's id. The preset's `always` says every model on it
    generates, and an OpenAI `/models` row states no outputs, so nothing but
    the user can say this model cannot: until they mark it -- the capability
    override `generate: no` (`PUT /llm-connections/{id}/facts`), which
    outranks the preset (`capabilities.resolve_caps`) -- it resolves
    structured, as spec 5.3 says. The callers mark it themselves."""
    format2(client)
    got = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Realm OpenAI",
        "base_url": "https://api.openai.com/v1", "api_key": "sk-test-openai"})
    assert got.status_code == 200, got.text
    conn_id = got.json()["id"]
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": conn_id, "model": OPENAI_DECIDER},
        "fallback": {"provider": ""}}}})
    return conn_id
