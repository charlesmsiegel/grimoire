"""One attempt as an adapter sends it (`Target`), and a primary with its
optional fallback (`Chain`): what the store resolves a task to, typed.

A gateway leaf, and **standard library only** (#239): the store builds these
(`store.inference.resolve`) and the gateway will send them, so neither side
may be what this module imports. `test_wire.py` holds that by the AST.

The resolver builds each resolved attempt's `Target` directly
(`Attempt.target`), and a resolution's `chain` carries the fallback's target
when it rides the primary. The facade is sent a `Chain` (or a lone
`Target`), sends each `Target` through the adapter registry (`adapters`), and
refuses anything else. There is no connection dict any more: slice I
deleted the lowering these replaced (Task 10).

Every class is frozen. A change is a new value (`with_account`,
`with_output_cap`, `without_sampling`, `Chain.alone`), never a write into a
shared one.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sampling:
    """The sampler preset an attempt is sent with (`resolve._sampling` builds
    it). No preset is provider defaults."""

    preset_id: str = ""
    preset_name: str = ""
    #: Where the preset came from: "override", "campaign", "global",
    #: "connection" or "none".
    scope: str = "none"
    params: dict = field(default_factory=dict)
    #: The output cap THIS call added (`Target.with_output_cap`, 01f), or None:
    #: what lets a refusal of `max_tokens` be worded as the call's rather than
    #: the user's preset's (`llm.CapRefusalError`). Never set by a resolution.
    call_cap: int | None = None
    #: The preset's own `max_tokens` beneath that cap (a positive int), or
    #: None when it set none: a refusal of a value the preset itself would
    #: have sent is the preset's too, and with none an adapter's own default
    #: is what the call's cap may not raise (`llm_sampling.effective`).
    preset_cap: int | None = None


@dataclass(frozen=True)
class Account:
    """The ledger's view of an attempt (spec 9.3): what it files that the wire
    does not say. The fields are `llm_usage.ACCOUNT_FIELDS`, in order, and a
    test holds the two equal. "" is "not stated", which files nothing."""

    operation: str = ""
    role: str = ""
    billing: str = ""
    decision_mode: str = ""


@dataclass(frozen=True)
class Target:
    """One attempt as an adapter sends it."""

    provider_id: str
    #: The provider's adapter (`openrouter`, `openai_compatible`, ...).
    kind: str
    #: The model SENT: an unset Claude model is the default it runs
    #: (`llm.CLAUDE_DEFAULT_MODEL`).
    model: str
    #: What the user called the provider ("" for none; `label` falls back).
    provider_name: str = ""
    #: The connection's own URL, as the adapter reads it ("" for the kind's own).
    base_url: str = ""
    #: Never in a `repr`, so never in a log line or a captured traceback.
    api_key: str = field(default="", repr=False)
    #: The provider's revision -- what gates its catalog and verified facts.
    rev: str = ""
    #: What the call asks for, which the usage holder keeps beside the model a
    #: provider reports having answered on (`requested_model`).
    requested_model: str = ""
    sampling: Sampling = field(default_factory=Sampling)
    #: The connection's `sampler_support` ("extended" or "").
    sampler_support: str = ""
    #: The request parameters the model takes, from its catalog; None when
    #: that is not known.
    model_params: tuple[str, ...] | None = None
    #: The catalog's feature flags for the model; None when it states none.
    model_features: dict | None = None
    #: Whether a reply cut short may be continued as the model's own turn.
    prefill: bool = False
    #: The strict post-processing the reply is put through ("none", "strict").
    post_process: str = "none"
    #: Whether post images may be sent to it: "yes", "no" or "unknown"
    #: (`capabilities.post_image_reach`).
    reads_images: str = "unknown"
    #: Whether to ask this attempt for its provider's structured mode.
    structured: bool = False
    #: The post-image degrade sibling: the same attempt, sent its images as
    #: their descriptions (#377).
    degrade: bool = False
    account: Account = field(default_factory=Account)

    @property
    def label(self) -> str:
        """How the provider is named to a reader: its name, else its id."""
        return self.provider_name or self.provider_id

    def with_account(self, **fields: str) -> Target:
        """This target with `fields` laid over its account: a NEW target and a
        NEW `Account`. An unknown field raises TypeError."""
        return dataclasses.replace(self, account=dataclasses.replace(self.account, **fields))

    def with_output_cap(self, n: int) -> Target:
        """A NEW target whose sampling `max_tokens` is the smaller of the
        preset's and `n` -- or `n` when the preset sets none (or sets
        something that is not a positive int) -- per call (01f, 3.9). The
        preset's id, name and scope are kept, so the ledger still names the
        preset the call was sent with; `call_cap` records `n`. Whether the cap
        reaches the wire is the adapter's (`inference.cap_sent`)."""
        own = self.sampling.params.get("max_tokens")
        preset = (own if isinstance(own, int) and not isinstance(own, bool) and own > 0
                  else None)
        capped = preset if preset is not None and preset < n else n
        sampling = dataclasses.replace(
            self.sampling, params={**self.sampling.params, "max_tokens": capped},
            call_cap=n, preset_cap=preset)
        return dataclasses.replace(self, sampling=sampling)

    def without_sampling(self) -> Target:
        """This target with no sampler preset: what a native decision is sent,
        whose endpoint takes no sampling (and whose row files no `preset`)."""
        return dataclasses.replace(self, sampling=Sampling())


@dataclass(frozen=True)
class Chain:
    """What one call is sent: a primary, and the fallback the facade tries
    when it fails (None for none)."""

    primary: Target
    fallback: Target | None = None

    @property
    def attempts(self) -> tuple[Target, ...]:
        """The primary, then the fallback when there is one."""
        return (self.primary,) if self.fallback is None else (self.primary, self.fallback)

    def with_account(self, **fields: str) -> Chain:
        """This chain with `fields` laid over BOTH targets' accounts."""
        return Chain(self.primary.with_account(**fields),
                     None if self.fallback is None else self.fallback.with_account(**fields))

    def alone(self) -> Chain:
        """The primary with no fallback."""
        return Chain(self.primary)
