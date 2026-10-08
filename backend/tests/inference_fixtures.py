"""Model-settings scaffolding the decide conversions' route tests share.

Slice F moves three calls onto `decide()` and the Decision role -- the
scene-break check, the voice-drift judge and the speaker pick -- and each
proves the same three things against a format-2 store: the Decision role is
what serves it, a decide-only Decision model is skipped for a generating role
fallback, and with no such fallback the seam refuses. The store those tests
stand on is built here once, through the HTTP API, rather than in each suite.
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
    decides, with a generating fallback (`on`: `spare` at `vendor/spare`, or
    `SAME_PROVIDER`) or none."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": ({"provider": on[0], "model": on[1]} if fallback
                     else {"provider": ""})}}})
