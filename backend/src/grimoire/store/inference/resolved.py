"""What a task resolved to: the attempts it runs, and how they were chosen.

Built by `resolve.resolve` (and, for the Embedding role, `resolve.embedding`)
and nowhere else. Until the facade takes attempts directly (spec §13), each
`Attempt` carries its lowered connection dict -- the shape `LLMClient` reads
today -- so `conn` is what a call site hands the facade.
From slice C that dict carries the fallback too: the fallback attempt's own
lowered dict, under `llm.FALLBACK_KEY` (`resolve.FALLBACK_KEY`), which is what
the facade sends when that primary fails (spec §5.2, §5.4, §5.5). There is no
other fallback on the shipped client.

Slice B adds what each attempt IS (its provider's kind, URL, rev, billing and
preset), what is known of its model (`facts`), and what it can do
(`capabilities`, each a `capabilities.Cap` with its source) -- and, from those,
`missing` / `fallback_missing`: what the route needs that an attempt is known
not to have. The seam refuses on `missing`. A non-empty `fallback_missing`
drops the fallback from the chain (spec §5.3): it stays in `attempts`, so a
surface can say why, but it is never attached to the primary, so it is never
sent.

Slice F adds what a decide resolution needs: each attempt's `decision_mode`
(which backend would answer it). Slice H serves a primary that cannot
generate natively, so `conn` is always the primary's.

Slice I builds each attempt's `wire.Target` beside its dict (`target`), from
the same lowered values, and a resolution's `chain`: the primary's target,
with the fallback's exactly where the primary's dict carries it under
`FALLBACK_KEY`. Nothing sends them yet; the dict stays what the facade reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ... import wire
from .capabilities import Cap
from .cascade import Selection

#: Where the primary's connection dict carries the fallback the facade sends:
#: `resolve.FALLBACK_KEY`, restated because `resolve` imports this module; a
#: test holds the two equal. What `ResolvedInference.chain` reads to decide
#: whether the fallback rides, so the chain and the dict cannot disagree.
FALLBACK_KEY = "_fallback"

#: The target of an attempt built by hand, as tests build them -- its twin is
#: the empty `controls`. Every attempt the resolver builds carries its own
#: (`resolve._target`).
UNBUILT = wire.Target(provider_id="", kind="", model="")


@dataclass(frozen=True)
class Attempt:
    """One provider/model/preset the generation may run on."""

    provider_id: str
    model: str
    #: The sampler preset the attempt runs with ("" for provider defaults). On
    #: a fallback, the primary's when the primary's came from a route scope.
    preset_id: str
    #: The attempt lowered to today's connection dict (`sampling` and, where
    #: the catalog says, `model_params` and `model_features` attached; at
    #: format 2, the model's `vision`, `prefill` and `post_process` facts in
    #: place of the connection's legacy fields -- `resolve.with_facts`).
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
    #: Every capability name -> `Cap(value, source, error)` (`capabilities.resolve_caps`;
    #: `error` is set only on a failed test, which reads `unknown`).
    capabilities: dict[str, Cap] = field(default_factory=dict)
    #: Effective controls (spec 8): `llm_sampling.effective(conn)` -- what each
    #: preset control sends on this attempt and why. Empty only on an attempt
    #: built by hand.
    controls: dict = field(default_factory=dict)
    #: Spec 5.4's retry budget: the primary gets `config.llm_retries`, the
    #: fallback none. Informational for now -- the facade keeps its own budget
    #: (`LLMClient._retry_count`), which reads the same setting.
    retries: int = 0
    #: On a decide resolution, the backend that would answer this attempt
    #: (`resolve.decision_mode`): "native" when it is known unable to
    #: generate and not known unable to decide natively, "structured" when it
    #: can generate (whatever its `decide_native`), "" when it can do neither.
    #: "" on a generate resolution. The capability answer only -- the backend
    #: stamps the mode a call actually used on a copy of its account block.
    decision_mode: str = ""
    #: The attempt as an adapter sends it (`resolve._target`): built from the
    #: same lowered values as `conn`, with the same account block and
    #: structured flag. `UNBUILT` only on an attempt built by hand.
    target: wire.Target = UNBUILT


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
    #: cannot be read, cannot send, or is the primary's own connection (unless
    #: a decide primary that cannot generate keeps it, `resolve._apart`); and
    #: carrying the route's sampling when the route has a preset (campaign or
    #: global scope). Still listed when it is known incapable
    #: (`fallback_missing`), though not sent. Empty when nothing resolved.
    attempts: tuple[Attempt, ...]
    #: The selection the cascade chose before any per-call override, from the
    #: same reads the attempts were built from (None when it chose nothing).
    #: What an override is compared against to say whether it moved the call.
    standing: Selection | None = None
    #: Whether the layout this was resolved from is the current one (roles and
    #: route choices) rather than the legacy keys read as it. The per-call
    #: override means different things in the two (spec 5.6), so the seam that
    #: refuses on it asks this rather than re-reading `config.md`.
    current: bool = False
    #: The sampler preset the STANDING selection would have run with, when the
    #: call carried an override preset (either layout); None otherwise (it
    #: is only ever compared against that preset).
    standing_preset: str | None = None
    #: The capabilities the route needs -- its operation's own and its
    #: `requires` -- that the PRIMARY attempt is known (`no`) not to have, in
    #: `capabilities.NAMES` order. `unknown` is never missing, and nor is the
    #: name rule's `no` (a guess, `resolve._GUESSES`). What the seam refuses on.
    missing: tuple[str, ...] = ()
    #: The same check on the fallback attempt. Never refused on: a fallback
    #: with anything here is reported, and not attached to the primary's
    #: connection (`llm.FALLBACK_KEY`), so the facade does not send it.
    fallback_missing: tuple[str, ...] = ()
    #: Why the chosen fallback is left out of `attempts` though it exists: it
    #: cannot send at all (`resolve.problem`: no key, no base URL), or it is on
    #: the primary's own provider (`resolve.SAME_PROVIDER`; lifted behind a
    #: decide primary that cannot generate). None when it is
    #: attempted, or there is no fallback to send. Never refused on -- the primary is
    #: what the call runs on -- so the settings view is where it shows.
    fallback_problem: str | None = None
    #: The vector space an Embedding-role resolution embeds in
    #: (`resolve.embedding`): `f"{provider_id}\0{rev}\0{model}"`, the key every
    #: vector cache is read and written under. Set only when the role embeds --
    #: a model, an endpoint, and nothing in `missing`; None otherwise, and
    #: always None for a generative resolution.
    space_id: str | None = None

    @property
    def conn(self) -> dict | None:
        """The connection dict the facade is sent: the primary attempt's.
        None when nothing resolved."""
        return self.attempts[0].conn if self.attempts else None

    @property
    def fallback(self) -> dict | None:
        """The fallback attempt's connection dict (`llm.fallback_sampling`
        applied, `llm._same_route` honoured), or None when there is none. What
        the facade sends behind the primary, unless `fallback_missing` dropped
        it (spec 5.3) -- or what `inference.stages` sends as a stage of its own
        where either attempt is native."""
        return self.attempts[1].conn if len(self.attempts) > 1 else None

    @property
    def chain(self) -> wire.Chain | None:
        """What the facade is sent, typed: the primary attempt's target, and
        the fallback attempt's exactly where the primary's dict carries it
        (`FALLBACK_KEY`) -- so a fallback known incapable, or one a decide
        resolution sends as a stage of its own, is not on it. None when
        nothing resolved."""
        if not self.attempts:
            return None
        primary = self.attempts[0]
        rides = len(self.attempts) > 1 and FALLBACK_KEY in primary.conn
        return wire.Chain(primary.target, self.attempts[1].target if rides else None)

    @property
    def decision_mode(self) -> str | None:
        """The primary attempt's decision mode, or None when it has none or
        nothing resolved."""
        return (self.attempts[0].decision_mode or None) if self.attempts else None
