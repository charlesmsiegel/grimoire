"""Request/response schemas for campaign endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CampaignSummary(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    name: str
    description: str | None = None
    mechanics_module: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    created_at: str | None = None
    last_played_at: str | None = None
    forked_from_campaign_id: str | None = None
    forked_at_post_id: str | None = None
    forked_at_turn_id: str | None = None
    forked_image_handling: str | None = None


class SceneSummary(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    title: str | None = None
    status: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------


class WorldRefPayload(BaseModel):
    world_id: str
    priority: int = 1
    include: list[str] | None = None
    track_latest: bool = False
    bound_at_version: int | None = None


class CompositionPayload(BaseModel):
    worlds: list[WorldRefPayload] = Field(default_factory=list)
    mechanics: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None
    calendar_ids: list[str] = Field(default_factory=list)
    holiday_set_ids: list[str] = Field(default_factory=list)
    display_calendar_id: str | None = None


class CampaignCreatePayload(BaseModel):
    id: str
    name: str
    description: str | None = None
    composition: CompositionPayload | None = None
    greeting_id: str | None = None
    tags: list[str] | None = None


class CampaignUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    greeting_id: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None
    mechanics: str | None = None


class AddPCPayload(BaseModel):
    character_ref: str
    name: str
    owner: str = "local"
    # `None` (omitted) preserves any existing stored role_tags on upsert;
    # an explicit list (including `[]`) replaces them. See StateStore.add_pc.
    role_tags: list[str] | None = None


class PCProfilePayload(BaseModel):
    description: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""


class SubmitTurnPayload(BaseModel):
    pc_ref: str
    text: str
    metadata: dict[str, Any] | None = None


class AdvanceTurnPayload(BaseModel):
    scene_id: str


class NextSpeakerPayload(BaseModel):
    scene_id: str


class SubmitDirectionPayload(BaseModel):
    scene_id: str
    text: str | None = None


class RetconPayload(BaseModel):
    post_id: str
    new_text: str
    replay_subsequent: bool = False


class UndoPayload(BaseModel):
    count: int = 1


class ForkPayload(BaseModel):
    new_campaign_id: str
    new_name: str
    fork_at_post_id: str | None = None
    description: str | None = None
    make_active: bool = False


class CreateFactPayload(BaseModel):
    fact: dict[str, Any]
    source: str = "user"


class TimeAdvancePayload(BaseModel):
    duration: dict[str, Any] | None = None
    target: str | None = None
    reason: str = "narrative"
    scene_id: str | None = None


class ImageGenPayload(BaseModel):
    scene_id: str | None = None
    post_id: str | None = None
    request: dict[str, Any] | None = None
    priority: int = 5


class ComposePromptPayload(BaseModel):
    scene_id: str | None = None
    post_id: str | None = None


class ExportPayload(BaseModel):
    adapter_id: str
    selection: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ReviewUpdatePayload(BaseModel):
    notes: str = ""


class PromotePayload(BaseModel):
    target_world_id: str
    source: str = "user"
    confirm: bool = False


class EntityOverridePayload(BaseModel):
    """Campaign-local override patch for a library entity of any kind."""

    override: dict[str, Any]
    world_id: str | None = None
    source: str = "user"


class CharacterCreationSubmitPayload(BaseModel):
    step_outputs: dict[str, Any]
    source: str = "user"


class MechanicsSwitchPayload(BaseModel):
    mechanics: str | None = None
    source: str = "user"


class ProposalResolutionPayload(BaseModel):
    label: str
    accepted: bool = True
    modifications: dict[str, Any] | None = None


class ResolveProposalsPayload(BaseModel):
    resolutions: list[ProposalResolutionPayload] = Field(default_factory=list)


class ResolveSceneBreakPayload(BaseModel):
    choice: str


class RoutingPayload(BaseModel):
    llm: dict[str, str | None] = Field(default_factory=dict)
    embedding: dict[str, str | None] = Field(default_factory=dict)
    imagegen: dict[str, str | None] = Field(default_factory=dict)


class ImageGenSettingsPayload(BaseModel):
    backend: str | None = None
    preset: str | None = None
    sampler_defaults: dict[str, Any] | str | None = None


class StorageSettingsPayload(BaseModel):
    schedule: str = "off"
    retention_days: int = 30


class SummariesSettingsPayload(BaseModel):
    running_every_n_posts: int = Field(default=5, ge=0, le=1000)
    final_on_close: bool = True


class AdvancedSettingsPayload(BaseModel):
    debug_log: bool = False
    per_task_prompts: dict[str, str] = Field(default_factory=dict)


class TierSettingsPayload(BaseModel):
    heavy: str | None = None
    light: str | None = None
    embedding: str | None = None


class IntegratedDeltasPayload(BaseModel):
    enabled: bool = False


class VariantSelectionsPayload(BaseModel):
    """Replace the campaign's character-variant selection map.

    Keys are character library ids (``worlds/<world>/characters/<id>``),
    values are variant ids. An empty map clears every selection.
    """

    variants: dict[str, str] = Field(default_factory=dict)


class GenerationSettingsPayload(BaseModel):
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class NarratorSettingsPayload(BaseModel):
    response_mode: str = "all_at_once"


class SceneUpdatePayload(BaseModel):
    narrator_response_mode: str | None = Field(default=None)
    clear_narrator_response_mode: bool = False


class StarImagePayload(BaseModel):
    starred: bool


class SetActiveBackendPayload(BaseModel):
    backend_id: str


class PrioritizeJobPayload(BaseModel):
    priority: int = 5


class VariationPayload(BaseModel):
    strength: float = 0.6


class TriggerConfigPayload(BaseModel):
    mode: str = "manual_only"
    every_n: int = 5
    on_scene_open: bool = True
    on_new_location: bool = True
    on_new_character_appearance: bool = True
    auto_during_combat: bool = False


class FallbackBackendPayload(BaseModel):
    backend_id: str | None = None


class EditAndRegeneratePayload(BaseModel):
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] | None = None
    keep_seed: bool = False


class ExpressionsSettingsPayload(BaseModel):
    enabled_characters: list[str] = Field(default_factory=list)


class SetTagsPayload(BaseModel):
    tags: list[str] = Field(default_factory=list)
