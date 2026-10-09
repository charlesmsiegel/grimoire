"""Builders for `grimoire.wire` values in tests, so a test spells only what it
means. The defaults are a keyed OpenRouter target at `vendor/active`; a field
passed overrides its default.

`resolution` builds the resolution a test hands a generation helper it drives
directly (`streaming._chat_stream`, `character_turns._frames`, ...), which
take one since every generation goes through `inference.generate`."""

from __future__ import annotations

from grimoire import wire
from grimoire.routes.common import UsableInference
from grimoire.store import routing
from grimoire.store.inference.resolved import Attempt

#: What `target()` builds when it is told nothing.
DEFAULTS: dict = {"provider_id": "openrouter", "kind": "openrouter",
                  "model": "vendor/active", "provider_name": "OpenRouter",
                  "api_key": "sk-test", "requested_model": "vendor/active",
                  "account": wire.Account(billing="metered")}


def target(**fields) -> wire.Target:
    """A `Target`: `DEFAULTS` with `fields` laid over them. A `model` given
    without a `requested_model` is also the model asked for, as the resolver
    builds every target."""
    if "model" in fields and "requested_model" not in fields:
        fields["requested_model"] = fields["model"]
    return wire.Target(**{**DEFAULTS, **fields})


def chain(primary: wire.Target, fallback: wire.Target | None = None) -> wire.Chain:
    """A `Chain` of `primary` and, when given, `fallback`."""
    return wire.Chain(primary, fallback)


def resolution(sent: wire.Chain | wire.Target, task: str = "chat", *,
               operation: str = "generate") -> UsableInference:
    """A hand-built resolution of `task` whose chain is `sent` (a lone target
    is a chain of one): what a test hands a generation helper it drives
    directly, which passes it to `inference.generate` -- so a fake sees
    `request["chain"]` be `sent`. Its fallback, when `sent` has one, rides."""
    route = routing.route(task)
    chain = sent if isinstance(sent, wire.Chain) else wire.Chain(sent)
    attempts = tuple(Attempt(t.provider_id, t.model, t.sampling.preset_id, target=t)
                     for t in chain.attempts)
    return UsableInference(
        task=task, operation=operation, route=route.key if route else "",
        role="", via="", scope="none", attempts=attempts,
        rides=chain.fallback is not None)
