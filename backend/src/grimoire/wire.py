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
`without_sampling`, `Chain.alone`), never a write into a shared one.

A target also carries what is known of its model's size (`Limits`, 01i): the
window and the most a reply may be asked for, each with where it came from.
They live here, beside `Target`, so an adapter or a capture holding a target
reads them without importing the store.

An embedding target may carry the options a model's facts state for it
(`EmbedOptions`, 01h): how its input type is sent, and (later) a requested
width. Their document side is part of the Embedding role's vector space id
(`store.inference.resolve.space_of`), which is why its canonical form and
digest are defined here, once.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import NamedTuple

#: Where a `Limit` came from: the user's word about the model on this provider
#: (its facts), the provider's own catalog row, or nowhere.
LIMIT_SOURCES: tuple[str, ...] = ("user", "catalog", "unknown")


class Limit(NamedTuple):
    """One size fact about a model: a positive token count, or None exactly
    when nothing says (`source` "unknown"). Never 0, and never guessed."""

    value: int | None
    #: One of `LIMIT_SOURCES`.
    source: str


#: A limit nothing has stated.
UNKNOWN_LIMIT = Limit(None, "unknown")


@dataclass(frozen=True)
class Limits:
    """What is known of a model's size on its provider
    (`store.inference.limits.of` resolves it)."""

    #: The context window, always read as shared by the prompt and the reply.
    window: Limit = UNKNOWN_LIMIT
    #: The most `max_tokens` a request may ask for. A cap on what is asked,
    #: never itself a reply reservation (`limits.reply_reserve`).
    max_output: Limit = UNKNOWN_LIMIT


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
    #: The output cap THIS CALL asked for (`Target.with_output_cap`; 01f
    #: 3.9), or None: never a preset's. It tells a refusal of a cap the call
    #: added (`llm.CapRefusalError`) from one the user's preset carried.
    call_cap: int | None = None
    #: The preset's own `max_tokens` before the call capped it, or None when
    #: the preset set none (or the call set no cap): a refused cap is the
    #: call's only when the preset itself sent no `max_tokens` (01f 3.9).
    preset_cap: int | None = None


@dataclass(frozen=True)
class Account:
    """The ledger's view of an attempt (spec 9.3): what it files that the wire
    does not say. The fields are `llm_usage.ACCOUNT_FIELDS`, in order, and a
    test holds the two equal. "" is "not stated", which files nothing.
    `hop` is "escalation" on an attempt an escalation hop sends (roadmap 01d
    §5.7), "" on every other."""

    operation: str = ""
    role: str = ""
    billing: str = ""
    decision_mode: str = ""
    hop: str = ""


#: The ways an embedding request can say whether a text is a query or a
#: document (01h §3.1): not at all, as a text prefix, or as a request field.
EMBED_INPUT_MODES: tuple[str, ...] = ("none", "prefix", "param")

#: The tag a space id carries its options' digest under (01h §5.1). A change
#: to `EmbedOptions.canonical` takes a new tag, deliberately: the old one
#: names every vector already cached.
EMBED_OPTIONS_TAG = "embopt1"


@dataclass(frozen=True)
class EmbedOptions:
    """The embedding options stated for one model on one provider (its facts'
    `embedding` block; `store.inference.facts.embed_options` reads them).

    Grimoire has two input types, query and document. `input` says how the
    type is sent: `none` sends texts unchanged, `prefix` prepends
    `query_prefix` or `document_prefix` to each text, and `param` (sent from
    a later slice) names the type in the request field `param_field`.
    `dimensions` (later too) asks for a narrower vector, in
    `dimensions_field`. `input` is one of `EMBED_INPUT_MODES`, held there by
    the facts validator rather than a `Literal`."""

    input: str = "none"
    query_prefix: str = ""
    document_prefix: str = ""
    param_field: str = ""
    query_value: str = ""
    document_value: str = ""
    dimensions: int | None = None
    dimensions_field: str = "dimensions"

    def canonical(self) -> str:
        """The DOCUMENT side of these options, as the text two devices must
        compute alike (01h §5.1): a JSON object with sorted keys, no spaces
        and ASCII escapes, holding at most `doc_prefix`, `field` with
        `doc_value`, and `dim` with `dim_field` -- each only where its mode or
        field applies and never empty. A cached vector depends only on what
        was sent for documents; a query is never cached and is embedded fresh,
        so the query side is left out. The default is `{}`."""
        doc: dict[str, object] = {}
        if self.input == "prefix" and self.document_prefix:
            doc["doc_prefix"] = self.document_prefix
        if self.input == "param" and self.param_field and self.document_value:
            doc["field"] = self.param_field
            doc["doc_value"] = self.document_value
        if self.dimensions is not None:
            doc["dim"] = self.dimensions
            if self.dimensions_field:
                doc["dim_field"] = self.dimensions_field
        return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def is_default(self) -> bool:
        """Whether these options embed documents exactly as no options do."""
        return self.canonical() == "{}"

    def digest(self) -> str:
        """The first 128 bits of `canonical()`'s SHA-256, in hex: what a space
        id carries in place of the options themselves, so no prefix text
        reaches a log line or a persisted basis."""
        return hashlib.sha256(self.canonical().encode("ascii")).hexdigest()[:32]


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
    #: The model's window and output cap, with their sources (01i): unknown on
    #: a target built by hand.
    limits: Limits = field(default_factory=Limits)
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
    #: The embedding options its model's facts state (01h), or None for none
    #: (or a block a fail-soft read could not use). The Embedding role's
    #: attempt carries the object its space id was computed from.
    embed_options: EmbedOptions | None = None

    @property
    def label(self) -> str:
        """How the provider is named to a reader: its name, else its id."""
        return self.provider_name or self.provider_id

    def with_account(self, **fields: str) -> Target:
        """This target with `fields` laid over its account: a NEW target and a
        NEW `Account`. An unknown field raises TypeError."""
        return dataclasses.replace(self, account=dataclasses.replace(self.account, **fields))

    def with_output_cap(self, n: int, *, most: int | None = None) -> Target:
        """A NEW target whose sampling `max_tokens` is `min(the preset's, n)`,
        or `n` when the preset sets none (01f 3.9), and never above `most`
        when one is given (the model's known maximum output, 01i-C1;
        `llm.clamp_to_max_output`). `n` is the cap THIS CALL asked for,
        recorded as `Sampling.call_cap` whatever was sent, and the preset's
        own `max_tokens` as `Sampling.preset_cap`. The preset's id, name and
        scope are kept, so the ledger still names the preset the call was
        sent with. Whether the cap reaches the wire is the adapter's, as a
        preset's `max_tokens` is (`inference.cap_sent`). A cap that is not a
        positive int is a ValueError."""
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"an output cap is a positive int, not {n!r}")
        params = dict(self.sampling.params)
        own = params.get("max_tokens")
        preset = own if isinstance(own, int) and not isinstance(own, bool) and own > 0 else None
        n_sent = n if most is None else min(n, most)
        if preset is not None:
            n_sent = min(preset, n_sent)
        params["max_tokens"] = n_sent
        return dataclasses.replace(self, sampling=dataclasses.replace(
            self.sampling, params=params, call_cap=n, preset_cap=preset))

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
