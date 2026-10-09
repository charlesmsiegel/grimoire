"""Builders for `grimoire.wire` values in tests, so a test spells only what it
means. The defaults are a keyed OpenRouter target at `vendor/active`; a field
passed overrides its default.

`resolution` builds the resolution a test hands a generation helper it drives
directly (`streaming._chat_stream`, `character_turns._frames`, ...), which
take one since every generation goes through `inference.generate`."""

from __future__ import annotations

from grimoire import llm, wire
from grimoire.routes.common import UsableInference
from grimoire.store import routing
from grimoire.store.inference.resolved import Attempt

#: What `target()` builds when it is told nothing.
DEFAULTS: dict = {"provider_id": "openrouter", "kind": "openrouter",
                  "model": "vendor/active", "provider_name": "OpenRouter",
                  "api_key": "sk-test", "requested_model": "vendor/active",
                  "account": wire.Account(billing="metered")}


def target(**fields) -> wire.Target:
    """A `Target`: `DEFAULTS` with `fields` laid over them."""
    return wire.Target(**{**DEFAULTS, **fields})


def chain(primary: wire.Target, fallback: wire.Target | None = None) -> wire.Chain:
    """A `Chain` of `primary` and, when given, `fallback`."""
    return wire.Chain(primary, fallback)


def resolution(conn: dict, task: str = "chat", *,
               operation: str = "generate") -> UsableInference:
    """A hand-built resolution of `task` whose one attempt sends `conn`, as
    is: the dict a test used to hand the helper, now handed to
    `inference.generate` inside it, so a fake still sees `request["conn"]`
    unchanged. Its target carries the dict's display facts (`provider_id`,
    `kind` and the effective model) and nothing else."""
    route = routing.route(task)
    provider = str(conn.get("id", "") or "")
    model = llm.effective_model(conn) if conn.get("kind") else str(conn.get("model", "") or "")
    return UsableInference(
        task=task, operation=operation, route=route.key if route else "",
        role="", via="", scope="none",
        attempts=(Attempt(provider, str(conn.get("model", "") or ""), "", conn,
                          target=wire.Target(provider_id=provider,
                                             kind=str(conn.get("kind", "") or ""),
                                             model=model)),))
