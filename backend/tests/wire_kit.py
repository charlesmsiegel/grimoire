"""Builders for `grimoire.wire` values in tests, so a test spells only what it
means. The defaults are a keyed OpenRouter target at `vendor/active`; a field
passed overrides its default."""

from __future__ import annotations

from grimoire import wire

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
