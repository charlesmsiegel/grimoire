"""Request models: settings domain."""


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, PositiveInt, StrictStr

from ...store import membership


# ---- models ----

class ConfigUpdate(BaseModel):
    theme: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
    user_label: str | None = None
    assistant_label: str | None = None
    active_connection_id: str | None = None
    llm_timeout: str | None = None
    absorb_budget: str | None = None
    absorb_concurrency: str | None = None
    llm_call_budget: str | None = None
    llm_retries: str | None = None
    fallback_connection_id: str | None = None
    context_budget: str | None = None
    context_scan_depth: str | None = None
    lore_recursion_depth: str | None = None
    archive_depth: str | None = None
    setup_done: str | None = None
    prompt_log_depth: str | None = None
    rolling_summary_every: str | None = None
    scene_break_every: str | None = None
    offscene_known_limit: str | None = None
    embeddings_connection_id: str | None = None
    embeddings_model: str | None = None
    history_recall_depth: str | None = None
    history_recall_budget: str | None = None
    history_recall_threshold: str | None = None
    history_recall_rerank: str | None = None
    history_plan: str | None = None
    history_plan_repair: str | None = None
    semantic_recall_depth: str | None = None
    semantic_recall_threshold: str | None = None
    prompt_layout_enabled: str | None = None
    speaker_turn_taking: str | None = None
    tracker: str | None = None
    perception_rider: str | None = None
    decide_speaker_sampling: str | None = None
    decide_turn_plan: str | None = None
    decide_play_tool: str | None = None
    epistemic_decide: str | None = None
    investigation_continuity: str | None = None
    investigation_question: str | None = None
    investigation_spend_ceiling: str | None = None
    send_images: str | None = None
    send_images_limit: str | None = None
    backup_enabled: str | None = None
    backup_interval_hours: str | None = None
    backup_keep: str | None = None
    backup_dir: str | None = None
    replay_fork_threshold: str | None = None
    advance_fork_threshold: str | None = None
    log_level: str | None = None
    #: The yes to a change of the legacy embedding keys that re-embeds the
    #: library (`put_config`); only a JSON `true` confirms.
    confirm_embedding: Any = None


class PromptLayoutSection(BaseModel):
    """One row of the prompt layout as the editor saves it. `label` blank means
    "use the catalog's" — the label is the inspector's row name and never
    reaches the model, which emits each section's heading from its template."""
    id: str
    label: str = ""
    enabled: bool = True


class PromptLayoutUpdate(BaseModel):
    #: The WHOLE list, not a patch: `write_layout` replaces. A partial list
    #: would merely be merged back against the catalog on read, which reorders
    #: almost nothing -- see `context/layout.py`'s insert rule.
    sections: list[PromptLayoutSection] = []


class ConnectionCreate(BaseModel):
    kind: Literal["openrouter", "claude", "openai_compatible", "anthropic"]
    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    post_process: Literal["none", "strict"] = "none"
    reasoning_effort: Literal["", "low", "high", "max"] = ""
    #: The connection's own sampler preset id, "" for none; checked to exist by
    #: the handler, not here (sampler presets spec).
    sampler_preset: str = ""
    #: Whether an OpenAI-compatible endpoint takes top-k, min-p and repetition
    #: penalty. "" reads as standard.
    sampler_support: Literal["", "standard", "extended"] = ""
    vision: Literal["", "on", "off"] = ""
    #: Whether "Keep writing" sends a cut-short reply back as the start of the
    #: model's own turn (prefill) rather than asking it to continue.
    prefill: bool = False
    #: The provider preset (`store/inference/providers.py`) this is made from;
    #: "" for none named. Checked by the handler (an unknown one, or one on
    #: another adapter, is a 400), not here.
    preset: str = ""
    #: "metered" or "subscription"; "" takes the preset's. Checked by the handler.
    billing: str = ""


class ConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    post_process: Literal["none", "strict"] | None = None
    reasoning_effort: Literal["", "low", "high", "max"] | None = None
    sampler_preset: str | None = None
    sampler_support: Literal["", "standard", "extended"] | None = None
    vision: Literal["", "on", "off"] | None = None
    prefill: bool | None = None
    preset: str | None = None
    billing: str | None = None
    #: Not a field of the provider: the reader's yes to an edit that re-embeds
    #: the library (`embed_space.moved_by`). Only `true` confirms; `Any`, so a
    #: `"true"` is refused rather than coerced by one pydantic version.
    confirm_embedding: Any = None


class FactsUpdate(BaseModel):
    """What the user says about one model on one provider (spec 4.2). Every
    field but `model` is `Any`: the store checks each value (`facts.state`),
    so a `"true"` for `prefill` is the 400 it is rather than coerced into a
    bool by one pydantic version and not the other. `overrides` is
    `{capability: "" | "yes" | "no"}`, merged per capability ("" removes).
    `rates` is the model's own per-token price (`pricing.FIELDS`): both base
    rates or a 400, `{}` removing them, null leaving them. `context_window`
    and `max_output` are the model's size on this provider (01i): a positive
    int sets one, `0` removes it, null leaves it -- and a `"8192"` is a 400
    under either pydantic, since the store decides. `embedding` is the
    model's embedding options (01h, `facts._check_embedding`): null leaves
    them, `{}` removes them, an object replaces them."""

    model: str = ""
    vision: Any = None
    prefill: Any = None
    post_process: Any = None
    overrides: Any = None
    #: Only `true` confirms a write that moves the Embedding role's vector
    #: space: turning it on, or changing its model's document-side options.
    confirm_embedding: Any = None
    rates: Any = None
    context_window: Any = None
    max_output: Any = None
    embedding: Any = None


class HealthCheck(BaseModel):
    """A provider health check. `confirm` is `Any` for `ModelTestRun`'s
    reason: only a JSON `true` lets a check that generates be sent."""

    confirm: Any = None


class CatalogProbe(BaseModel):
    """A connection described but not saved, for the sake of listing its models.

    The setup wizard and the New-connection form both need a catalog before
    there is anything on disk to hang one on (#149) — the wizard's whole job is
    picking a model for a connection that does not exist yet. Same fields as
    `ConnectionCreate` minus the ones a catalog cannot use: no name (nothing is
    being named), no model (that is what this is for), no post-processing (a
    prompt-shaping rule with no prompt to shape).

    The key travels as it does on create: this route is how a typed-but-unsaved
    key gets exercised, and refusing to carry one would leave the wizard listing
    only the catalogs that need no auth.
    """

    kind: Literal["openrouter", "claude", "openai_compatible", "anthropic"]
    base_url: str = ""
    api_key: str = ""


class ModelTestPreview(BaseModel):
    """What a model test would send, asked before anything is sent (spec 6.4).
    `model` blank means the connection's own (effective) model."""

    model: str = ""
    capabilities: list[str] = []


class ModelTestRun(BaseModel):
    """A model test, to be run. `confirm` is `Any` rather than `bool` on
    purpose: pydantic coerces `"true"`, `1` and `"yes"` to a bool, and the
    only thing that may spend money here is a JSON `true` (`is True`)."""

    model: str = ""
    capabilities: list[str] = []
    confirm: Any = None


class DataDirUpdate(BaseModel):
    data_dir: str | None = None


class StyleCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    body: str = ""


class StyleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    body: str | None = None


class ResponseSettings(BaseModel):
    response_preset: str | None = None
    style_id: str | None = None
    length_reply_words: str | None = None
    length_blocks: str | None = None
    length_paragraphs: str | None = None
    length_speakers: str | None = None
    length_blocks_per_speaker: str | None = None
    response_opening_words: str | None = None
    response_opening_paragraphs: str | None = None
    response_continuation_words: str | None = None
    response_continuation_paragraphs: str | None = None


class GroupSettings(BaseModel):
    """A scene's group-play settings. Every key is optional on the wire; the
    store's `group_play.validate` is what judges the values."""

    order: str = "directed"
    order_list: list[str] = []
    talkativeness: dict[str, int] = {}
    sitting_out: list[str] = []
    auto_rounds: int = 0


class RegexLayer(BaseModel):
    """One level's regex rule file (global, world, campaign or connection)."""

    rules: list[dict] = []
    off: list[str] = []


class RegexTest(BaseModel):
    """The rule editor's test pane: text run through a scope's rules."""

    scope: dict
    text: str
    role: str = "model"
    phase: str = "display"
    depth: int = 0
    draft: dict | None = None
    connection: str = ""


class RegexImportPreview(BaseModel):
    """A SillyTavern regex upload, parsed by the client: one script, a list, or
    a settings / preset export carrying `regex_scripts`."""

    data: Any = None


class RegexImport(BaseModel):
    """The preview rows the user kept (their rules), and the level they go to."""

    scope: dict
    rows: list[dict] = []


class QuickReplySetBody(BaseModel):
    """A whole quick-reply set, replacing what is stored. Loose on purpose: every
    rule is checked in `store.quick_replies`, so a violation is a 400 with a
    `kind`, never a pydantic 422. `expect` is the digest of the set the caller
    read; a stored set that has moved since refuses the write (409)."""

    replies: list[dict] = []
    expect: str = ""


class TrackerLayer(BaseModel):
    """One layer of tracker field definitions (world, campaign or scene)."""

    fields: list[dict] = []
    change: dict = {}
    off: list[str] = []


class CampaignPlayDecisions(BaseModel):
    """A campaign's own play-decision switches (roadmap 02 §3.6): each one a
    value of its switch, or "" to follow the global one. A field left out is
    left as it is."""

    speaker_sampling: str | None = None
    turn_plan: str | None = None
    play_tool: str | None = None


class CampaignTracker(BaseModel):
    """A campaign's own scene-tracker setting: "on", "off", or "" to follow the
    global one."""

    setting: str


class TrackerEdit(BaseModel):
    """A person's hand edit of one tracker record:
    `{ref: {field_key: {"value": ..., "aware": "present" | [refs]}}}`, either
    half optional (`store.tracker.merge.apply_edit` validates it)."""

    edits: dict


class ResponsePresetCreate(BaseModel):
    name: str
    description: str = ""
    style_id: str = ""
    length_preset: str = ""
    knobs: dict | None = None


class ResponsePresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    style_id: str | None = None
    length_preset: str | None = None
    knobs: dict | None = None


class RegenerateBody(BaseModel):
    """One reroll's overrides, every one of them riding this call alone.

    `connection_id` and `model` are the manual route override (#77), and they
    are two fields rather than one because neither can express the other's
    case. A bare model id cannot reach a *different provider* — the interesting
    reroll is "try that again on the local Ollama", and the credentials, base
    URL and prompt post-processing that makes possible live on a connection,
    not in a string. A bare connection id cannot say "the same provider, its
    bigger model", which is the cheap everyday case and the one that needs no
    setup at all. Sent together they compose: the named connection, driven at
    the named model.

    Both are optional and both default to the standing configuration — an
    absent `connection_id` means the active connection, an absent `model` means
    whatever model the resolved connection carries. Nothing here is persisted
    as a preference; #142/#143 are where standing per-task routing lives.

    `provider` is what `connection_id` is now called (spec 5.6), and the old
    name is read as it when `provider` is absent. In the roles format a
    provider alone keeps the standing MODEL on the new provider (the
    connection no longer has a model of its own to fall back to); a store that
    has not moved to the roles format keeps the old meaning, where
    `connection_id` alone runs that connection's own model. On either layout
    `preset` replaces the route's sampler preset for this call's primary
    attempt only: the fallback gets what it would have had without the
    override -- the route's preset when the route has one, else its own. The
    preset id `PRESET_CLEAR` means "no preset".
    """
    guidance: str | None = None
    response: ResponseSettings | None = None
    connection_id: str | None = None
    model: str | None = None
    provider: str | None = None
    preset: str | None = None


class RetryBody(BaseModel):
    response: ResponseSettings | None = None


class BudgetBody(BaseModel):
    """A campaign's cost budget (#153).

    `budget_usd` is nullable, and null is how a budget is CLEARED -- distinct
    from the 0 a form would send for "no budget yet", though the store reads
    both the same way. `budget_period` is a plain string rather than an enum so
    an unknown one is normalized by `store.usage.normalize_period` alongside
    every hand-edited value it already has to survive, instead of 422-ing a
    request the store can answer.
    """
    budget_usd: float | None = None
    budget_period: str | None = None


class PricingBody(BaseModel):
    """The whole per-model rate table (#158), replaced in one PUT.

    `rates` is a plain `dict` rather than a mapping of a typed entry model, and
    that is the pydantic-v1/v2-agnostic constraint doing its job (`CLAUDE.md`):
    no `Field`, no validators, no nested constraint types. Validation lives in
    `store.pricing.write_pricing`, which is where the same rules already have to
    hold for a file the user can hand-edit — one implementation, one answer,
    whichever door the table came through.
    """
    #: REQUIRED, with no default. A PUT here replaces the whole table, so a
    #: body that omits `rates` would otherwise be a successful request that
    #: deletes every rate the user has -- which is what a version-skewed or
    #: malformed client sends. Clearing the table on purpose is still available
    #: and still says so out loud: `{"rates": {}}`.
    rates: dict


