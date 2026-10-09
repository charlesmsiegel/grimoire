"""One attempt as an adapter sends it (`Target`), and a primary with its
optional fallback (`Chain`): the typed form of the lowered connection dict.

A gateway leaf, and **standard library only** (#239): the store builds these
(`store.inference.resolve`) and the gateway will send them, so neither side
may be what this module imports. `test_wire.py` holds that by the AST.

Slice I builds them BESIDE the dict for now. Each resolved attempt carries
both (`Attempt.conn`, `Attempt.target`), from the same lowered values, and a
resolution's `chain` carries the fallback's target exactly where the
primary's dict carries it under `FALLBACK_KEY`. Nothing sends a `Target` yet;
the facade moves onto them in a later task, and the dict goes then.

Every class is frozen. A change is a new value (`with_account`,
`without_sampling`, `Chain.alone`), never a write into a shared one -- the
reason the dict's account block had to be replaced whole rather than updated.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sampling:
    """The sampler preset an attempt is sent with: the dict's `sampling` block
    (`resolve._sampling`'s shape). No preset is provider defaults."""

    preset_id: str = ""
    preset_name: str = ""
    #: Where the preset came from: "override", "campaign", "global",
    #: "connection" or "none".
    scope: str = "none"
    params: dict = field(default_factory=dict)


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
    #: (`llm.effective_model`).
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
    sampling: Sampling = Sampling()
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
    account: Account = Account()

    @property
    def label(self) -> str:
        """How the provider is named to a reader: its name, else its id."""
        return self.provider_name or self.provider_id

    def with_account(self, **fields: str) -> Target:
        """This target with `fields` laid over its account: a NEW target and a
        NEW `Account`. An unknown field raises TypeError."""
        return dataclasses.replace(self, account=dataclasses.replace(self.account, **fields))

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
        """The primary with no fallback: the dict without `FALLBACK_KEY`."""
        return Chain(self.primary)
