"""What a task resolved to: the attempts it runs, and how they were chosen.

Built by `resolve.resolve` (and, for the Embedding role, `resolve.embedding`)
and nowhere else. Each `Attempt` carries what it IS (its provider's kind, URL,
rev, billing and preset), what is known of its model (`facts`), what it can do
(`capabilities`, each a `capabilities.Cap` with its source) -- and, from
those, `missing` / `fallback_missing`: what the route needs that an attempt is
known not to have. The seam refuses on `missing`. A non-empty
`fallback_missing` drops the fallback from the chain (spec §5.3): it stays in
`attempts`, so a surface can say why, but it never rides the primary, so it is
never sent.

Each attempt is sent as its `target`, a `wire.Target` the resolver builds
directly (slice I), and a resolution's `chain` is what a call site hands the
facade: the primary's target, and the fallback's when it `rides` (spec §5.2,
§5.4, §5.5). Slice F adds each attempt's `decision_mode` (which backend would
answer it on a decide resolution). There is no connection dict: slice I
deleted the lowering (Task 10), and `test_lowering_retired_guard.py` keeps it
deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ... import wire
from .capabilities import Cap
from .cascade import Selection

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
    #: Effective controls (spec 8): `llm_sampling.effective(target)` -- what each
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
    #: stamps the mode a call actually used on a new target's account.
    decision_mode: str = ""
    #: The attempt as an adapter sends it (`resolve._target`): the provider's
    #: record at this model and preset, its model's stated behaviour laid on,
    #: and the resolution's account stamp and structured flag. `UNBUILT` only
    #: on an attempt built by hand.
    target: wire.Target = UNBUILT

    @property
    def limits(self) -> wire.Limits:
        """The model's window and output cap on this attempt (01i-C1): its
        target's, never a second copy that could disagree. Unknown on an
        attempt built by hand."""
        return self.target.limits


@dataclass(frozen=True)
class ResolvedInference:
    task: str
    operation: str
    #: The route's key, or "" for a task no route claims.
    route: str
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
    #: The sampler preset the STANDING selection would have run with, when the
    #: call carried an override preset; None otherwise (it
    #: is only ever compared against that preset).
    standing_preset: str | None = None
    #: The capabilities the route needs -- its operation's own and its
    #: `requires` -- that the PRIMARY attempt is known (`no`) not to have, in
    #: `capabilities.NAMES` order. `unknown` is never missing, and nor is the
    #: name rule's `no` (a guess, `resolve._GUESSES`). What the seam refuses on.
    missing: tuple[str, ...] = ()
    #: The same check on the fallback attempt. Never refused on: a fallback
    #: with anything here is reported, and does not ride (`rides`), so the
    #: facade does not send it.
    fallback_missing: tuple[str, ...] = ()
    #: Why the chosen fallback is left out of `attempts` though it exists: it
    #: cannot send at all (`resolve.problem`: no key, no base URL), or it is on
    #: the primary's own provider (`resolve.SAME_PROVIDER`; lifted behind a
    #: decide primary that cannot generate), or the task's code policy sends
    #: none (`resolve.NO_FALLBACK_POLICY`, which outranks both). None when it is
    #: attempted, or there is no fallback to send. Never refused on -- the primary is
    #: what the call runs on -- so the settings view is where it shows.
    fallback_problem: str | None = None
    #: The vector space an Embedding-role resolution embeds in
    #: (`resolve.embedding`): `f"{provider_id}\0{rev}\0{model}"`, the key every
    #: vector cache is read and written under. Set only when the role embeds --
    #: a model, an endpoint, and nothing in `missing`; None otherwise, and
    #: always None for a generative resolution. Stated embedding options whose
    #: document side is not the default add `\0embopt1:<digest>` (01h §5.1).
    space_id: str | None = None
    #: Why the Embedding role names no space because of its model's facts
    #: (`resolve.OPTIONS_HELD`, `OPTIONS_MANGLED`, `OPTIONS_INVALID`), or "".
    embed_options_problem: str = ""
    #: Whether the fallback attempt rides the facade behind the primary
    #: (`chain`'s `fallback`): always on a generate resolution whose fallback
    #: is not known incapable; on a decide one only where both attempts are
    #: structured (`resolve._rides`) -- elsewhere `inference.stages` sends the
    #: fallback as a stage of its own. False with no fallback.
    rides: bool = False
    #: Set only on a resolution of a format-1 store (`config.md` below format
    #: 2, read through the planner, `legacy_plan.Overlay.legacy`): the legacy
    #: route the task's route was stored under ("" for none), which the
    #: format-1 `missing_key` sentence names a pin by (`resolve.unusable`).
    #: None at format 2, and on an attempt built by hand.
    legacy_route: str | None = None

    @property
    def chain(self) -> wire.Chain | None:
        """What the facade is sent: the primary attempt's target, and the
        fallback attempt's when it `rides` -- so a fallback known incapable,
        or one a decide resolution sends as a stage of its own, is not on it.
        None when nothing resolved."""
        if not self.attempts:
            return None
        rides = self.rides and len(self.attempts) > 1
        return wire.Chain(self.attempts[0].target, self.attempts[1].target if rides else None)

    @property
    def legacy(self) -> bool:
        """Whether this is a format-1 store's resolution (`legacy_route` set):
        the two places such a store still answers as it always has -- a
        reroll naming a provider alone, and the `missing_key` sentence."""
        return self.legacy_route is not None

    @property
    def decision_mode(self) -> str | None:
        """The primary attempt's decision mode, or None when it has none or
        nothing resolved."""
        return (self.attempts[0].decision_mode or None) if self.attempts else None
