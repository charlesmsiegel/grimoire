"""What a task resolved to: the attempts it runs, and how they were chosen.

Built by `resolve.resolve` and nowhere else. Until the facade takes attempts
directly (spec §13), each `Attempt` carries its lowered connection dict -- the
shape `LLMClient` reads today -- so `conn` and `fallback` are what a call site
hands the facade.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Attempt:
    """One provider/model/preset the generation may run on."""

    provider_id: str
    model: str
    #: The sampler preset the attempt runs with ("" for provider defaults).
    preset_id: str
    #: The attempt lowered to today's connection dict (`sampling` and, where
    #: the catalog says, `model_params` attached).
    conn: dict


@dataclass(frozen=True)
class ResolvedInference:
    task: str
    operation: str
    #: The route's key, or "" for a task no route claims.
    route: str
    #: The key the route's legacy settings live under (`routing.legacy_key`),
    #: which is what a refusal names; "" for no route.
    legacy_route: str
    #: The role whose slot supplied the selection ("" for a pin, or nothing).
    role: str
    #: "route" (a pin), "role", or "" when nothing was selected.
    via: str
    #: "campaign" | "global" | "none".
    scope: str
    #: Primary first, then the fallback. Empty when nothing resolved.
    attempts: tuple[Attempt, ...]

    @property
    def conn(self) -> dict | None:
        """The primary attempt's connection dict, or None when nothing resolved."""
        return self.attempts[0].conn if self.attempts else None

    @property
    def fallback(self) -> dict | None:
        """The fallback attempt's connection dict, or None when there is none."""
        return self.attempts[1].conn if len(self.attempts) > 1 else None
