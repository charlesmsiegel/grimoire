"""What a task resolved to: the attempts it runs, and how they were chosen.

Built by `resolve.resolve` and nowhere else. Until the facade takes attempts
directly (spec §13), each `Attempt` carries its lowered connection dict -- the
shape `LLMClient` reads today -- so `conn` is what a call site hands the facade,
and `fallback` is what the facade then sends when that primary fails: the same
connection and sampling `LLMClient._routes` derives (spec §5.2, §5.4). The
facade still resolves its own fallback in this slice; this one is the
resolver's account of it, held equal to the facade's by the tests.

Slice B adds what each attempt IS (its provider's kind, URL, rev, billing and
preset), what is known of its model (`facts`), and what it can do
(`capabilities`, each a `capabilities.Cap` with its source) -- and, from those,
`missing` / `fallback_missing`: what the route needs that an attempt is known
not to have. The seam refuses on `missing`; `fallback_missing` is reported only,
because the facade still sends the fallback it resolves itself until slice C.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .capabilities import Cap
from .cascade import Selection


@dataclass(frozen=True)
class Attempt:
    """One provider/model/preset the generation may run on."""

    provider_id: str
    model: str
    #: The sampler preset the attempt runs with ("" for provider defaults). On
    #: a fallback, the primary's when the primary's came from a route scope.
    preset_id: str
    #: The attempt lowered to today's connection dict (`sampling` and, where
    #: the catalog says, `model_params` and `model_features` attached).
    conn: dict
    #: The connection's adapter (`kind`).
    provider_kind: str = ""
    #: Where requests go: the connection's own URL, else its preset's.
    base_url: str = ""
    #: The connection's revision -- what gates its catalog and verified facts.
    rev: str = ""
    #: "metered" | "subscription" (`providers.billing`).
    billing: str = ""
    #: The provider preset (`providers.PRESETS` id) -- named apart from the
    #: sampler `preset_id` above.
    provider_preset: str = ""
    #: What is known of this model on this provider (`facts.of`).
    facts: dict = field(default_factory=dict)
    #: Every capability name -> `Cap(value, source)` (`capabilities.resolve_caps`).
    capabilities: dict[str, Cap] = field(default_factory=dict)
    #: Effective controls (spec 8): `llm_sampling.effective(conn)` -- what each
    #: preset control sends on this attempt and why. Empty only on an attempt
    #: built by hand.
    controls: dict = field(default_factory=dict)


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
    #: Primary first, then the fallback as the facade sends it: none when it
    #: cannot be read, cannot send, or is the primary's own connection; and
    #: carrying the primary's sampling when that came from a route scope
    #: (campaign or global). Empty when nothing resolved.
    attempts: tuple[Attempt, ...]
    #: The selection the cascade chose before any per-call override, from the
    #: same reads the attempts were built from (None when it chose nothing).
    #: What an override is compared against to say whether it moved the call.
    standing: Selection | None = None
    #: The capabilities the route needs -- its operation's own and its
    #: `requires` -- that the PRIMARY attempt is known (`no`) not to have, in
    #: `capabilities.NAMES` order. `unknown` is never missing. What the seam
    #: refuses on.
    missing: tuple[str, ...] = ()
    #: The same check on the fallback attempt. Reported only: the facade still
    #: sends that fallback until slice C drops it.
    fallback_missing: tuple[str, ...] = ()

    @property
    def conn(self) -> dict | None:
        """The primary attempt's connection dict, or None when nothing resolved."""
        return self.attempts[0].conn if self.attempts else None

    @property
    def fallback(self) -> dict | None:
        """The fallback attempt's connection dict as the facade sends it
        (`llm.fallback_sampling` applied, `llm._same_route` honoured), or None
        when there is none."""
        return self.attempts[1].conn if len(self.attempts) > 1 else None
