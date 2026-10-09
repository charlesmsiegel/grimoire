"""One attempt as an adapter sends it (`Target`), and a primary with its
optional fallback (`Chain`): the typed form of the lowered connection dict.

A gateway leaf, and **standard library only** (#239): the store builds these
(`store.inference.resolve`) and the gateway will send them, so neither side
may be what this module imports. `test_wire.py` holds that by the AST.

Slice I builds them BESIDE the dict for now. Each resolved attempt carries
both (`Attempt.conn`, `Attempt.target`), from the same lowered values, and a
resolution's `chain` carries the fallback's target exactly where the
primary's dict carries it under `FALLBACK_KEY`. The facade sends a `Target`
through the adapter registry (`adapters`); a caller that still hands it the
dict is read through `from_lowered`, which reads a dict as the chain it
describes -- the one dict door, deleted with the lowering.

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


# ---- the lowered dict, read as a chain (temporary: Task 10 deletes it) ----
#: What the Claude path runs when its connection names no model: an unset
#: Claude model is this alias (`llm.effective_model`, which binds this name).
CLAUDE_DEFAULT_MODEL = "opus"

#: The lowered dict's private keys, spelled as literals because this module
#: imports nothing of the gateway's or the store's (#239): the fallback the
#: dict carries (`llm.FALLBACK_KEY`), the structured flag
#: (`llm.STRUCTURED_KEY`), the account block (`llm_usage.ACCOUNT_KEY`) and the
#: degrade marker (`llm.DEGRADE`). A test holds each to its owner's spelling.
_FALLBACK = "_fallback"
_STRUCTURED = "_structured"
_ACCOUNT = "_account"
_DEGRADE = "_degrade"

#: The post-image preference, read as a reach where it decides one
#: (`capabilities.post_image_reach`): "on" and "off" win over any capability.
_PREFERENCE_REACH = {"on": "yes", "off": "no"}

#: The kinds whose client can carry an image part: `store.image_drafts.
#: SUPPORTED_KINDS`, and the kinds whose adapter `carries_images`
#: (`adapters`), restated for the reason the keys above are. Any other kind
#: is sent no image, whatever its preference says.
_IMAGE_KINDS = frozenset({"openrouter", "openai_compatible", "anthropic"})


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _sampling_of(block: object) -> Sampling:
    if not isinstance(block, dict):
        return Sampling()
    scope = block.get("scope")
    params = block.get("params")
    return Sampling(preset_id=_text(block.get("preset_id")),
                    preset_name=_text(block.get("preset_name")),
                    scope=scope if isinstance(scope, str) else "none",
                    params=dict(params) if isinstance(params, dict) else {})


def _account_of(block: object) -> Account:
    if not isinstance(block, dict):
        return Account()
    return Account(**{f.name: _text(block.get(f.name)) for f in dataclasses.fields(Account)})


def _target_of(conn: dict) -> Target:
    """One lowered dict as the `Target` it describes, read the way the facade
    reads a dict: a `kind` left off is OpenRouter's, the model is the one sent
    (an unset Claude model is `CLAUDE_DEFAULT_MODEL`), and a field of the
    wrong type is the field unset -- the same defensive reading every
    consumer of the dict made of it.

    `reads_images` is `capabilities.post_image_reach`'s rule as far as the
    dict can decide it: "no" for a kind that cannot carry an image part,
    else what its post-image preference (`vision`) decides, and "unknown"
    where that leaves the answer to the model's capability, which only the
    store can read (`store.post_images.capability` asks it of a target)."""
    kind = conn.get("kind", "openrouter")
    kind = kind if isinstance(kind, str) else ""
    model = _text((conn.get("model") or CLAUDE_DEFAULT_MODEL) if kind == "claude"
                  else conn.get("model", ""))
    params = conn.get("model_params")
    features = conn.get("model_features")
    return Target(
        provider_id=_text(conn.get("id")), kind=kind, model=model,
        provider_name=_text(conn.get("name")), base_url=_text(conn.get("base_url")),
        api_key=_text(conn.get("api_key")), rev=_text(conn.get("rev")),
        requested_model=model, sampling=_sampling_of(conn.get("sampling")),
        sampler_support=_text(conn.get("sampler_support")),
        model_params=tuple(params) if isinstance(params, list) else None,
        model_features=dict(features) if isinstance(features, dict) else None,
        prefill=conn.get("prefill") is True, post_process=_text(conn.get("post_process")),
        reads_images=(_PREFERENCE_REACH.get(_text(conn.get("vision")), "unknown")
                      if kind in _IMAGE_KINDS else "no"),
        structured=conn.get(_STRUCTURED) is True, degrade=bool(conn.get(_DEGRADE)),
        account=_account_of(conn.get(_ACCOUNT)))


def from_lowered(conn: dict) -> Chain:
    """A lowered connection dict as the `Chain` it sends: its own attempt, and
    the fallback it carries under `_fallback` (a falsy one is none).

    The dict door, and a temporary one: callers that still hold a dict --
    the facade's shim (`llm._as_chain`), `llm_sampling`'s dict callers, and
    the model test's probes -- go through here until Task 10 deletes the
    lowering, and this with it."""
    fallback = conn.get(_FALLBACK)
    return Chain(_target_of(conn),
                 _target_of(fallback) if isinstance(fallback, dict) and fallback else None)
