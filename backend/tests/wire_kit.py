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
    """A hand-built resolution of `task` whose attempts send `conn`, as is:
    the dict a test used to hand the helper, now handed to
    `inference.generate` inside it as the chain it describes. Its targets are
    the dict's own and the fallback's it carries (`llm.FALLBACK_KEY`), read
    the way the resolver lowers them (`wire.from_lowered`), so a fake sees
    `request["target"]` say what the dict said."""
    route = routing.route(task)
    chain = wire.from_lowered(conn)
    attempts = [Attempt(chain.primary.provider_id, str(conn.get("model", "") or ""), "", conn,
                        target=chain.primary)]
    if chain.fallback is not None:
        fallback = conn[llm.FALLBACK_KEY]
        attempts.append(Attempt(chain.fallback.provider_id,
                                str(fallback.get("model", "") or ""), "", fallback,
                                target=chain.fallback))
    return UsableInference(
        task=task, operation=operation, route=route.key if route else "",
        role="", via="", scope="none", attempts=tuple(attempts))
