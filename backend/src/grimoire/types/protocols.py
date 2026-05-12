"""Protocol stubs for cross-module interfaces.

These are *contracts*, not implementations. Each spec is the source of truth
for behavioral details. Domain modules implement these in later tasks.

Note: Protocols intentionally use `...` bodies (no implementation). The
attribute-style protocols (e.g., `LLMProvider`) declare class attributes that
implementers populate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .characters import (
    Character,
    CharacterData,
    CharacterFilter,
    DriftReport,
    ImportResult,
    PCEntry,
    ResolvedCharacter,
)
from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    CommitmentId,
    Duration,
    EntityKind,
    EventId,
    FactId,
    HealthStatus,
    InGameTime,
    JsonSchema,
    LocationRef,
    MechanicsModuleId,
    PluginId,
    PostId,
    SceneId,
    SubscriptionId,
    TurnId,
    ValidationResult,
)
from .composition import (
    CampaignRef,
    Composition,
    Greeting,
    LibraryEntity,
    ResolvedEntity,
    ResolvedLocation,
    SettingMeta,
    UpgradeReport,
)
from .context import AssembledPrompt, BudgetEstimate
from .continuity import (
    AgingReport,
    Commitment,
    CommitmentStatus,
    ContradictionReport,
    Fact,
    Relationship,
)
from .export import (
    ExportOptions,
    ExportPreview,
    ExportRecord,
    ExportResult,
    ExportSelection,
)
from .extraction import ExtractionResult
from .imagegen import (
    BackendInfo,
    GenerationJob,
    GenerationRequest,
    GenerationResult,
    ImageMetadata,
    JobStatus,
)
from .llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    LLMCallRecord,
    ModelInfo,
    ProviderCapabilities,
)
from .mechanics import (
    Capability,
    MechanicsResult,
    ModuleManifest,
    NarratedEvent,
    PowerDefinition,
    ProposedRoll,
    Roll,
    RollResult,
)
from .observability import (
    CostTotal,
    DailyCost,
    ErrorRecord,
    HealthTarget,
    LogEvent,
    LogQuery,
    ReplayOptions,
    ReplayResult,
    TurnAudit,
)
from .orchestrator import (
    Event,
    EventType,
    ForkResult,
    RegenerateResult,
    RetconResult,
    SubmitResult,
    Subscription,
    TurnStatus,
    UndoResult,
)
from .plugins import PluginManifest, PluginStatus, RescanReport
from .scene import (
    AdvanceDecision,
    AdvanceResult,
    Post,
    Scene,
    SceneBreakDecision,
    SceneCloseReport,
    SceneContext,
    SceneFile,
    SceneInit,
    SceneThreads,
    Thread,
)
from .state import (
    AppliedDelta,
    CharacterState,
    ContextTier,
    FactionState,
    LocationState,
    ReviewItem,
    SearchResult,
    StateDelta,
    StateSnapshot,
)
from .time import (
    ScheduledEvent,
    TimeAdvanceReason,
    TimeAdvanceResult,
)

# --------------------------------------------------------------------------- #
# Event bus
# --------------------------------------------------------------------------- #


EventHandler = Callable[[Event], Awaitable[None]]


@runtime_checkable
class EventBus(Protocol):
    def subscribe(
        self, event_type: EventType | str | None, handler: EventHandler
    ) -> Subscription: ...

    def unsubscribe(self, subscription_id: SubscriptionId) -> None: ...

    async def emit(self, event: Event) -> None: ...


# --------------------------------------------------------------------------- #
# LLM Gateway and plugin protocols
# --------------------------------------------------------------------------- #


class LLMProvider(Protocol):
    id: str
    name: str
    capabilities: ProviderCapabilities

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def estimate_tokens(self, text: str) -> int: ...

    async def health_check(self) -> HealthStatus: ...


class EmbeddingProvider(Protocol):
    id: str
    name: str
    model_id: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def health_check(self) -> HealthStatus: ...


@runtime_checkable
class LLMGateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> CompletionResponse: ...

    def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> AsyncIterator[CompletionChunk]: ...

    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: CampaignId | None = None,
    ) -> list[list[float]]: ...

    async def list_llm_providers(self) -> list[LLMProvider]: ...

    async def list_embedding_providers(self) -> list[EmbeddingProvider]: ...

    async def list_routes(self, campaign_id: CampaignId | None = None) -> dict[str, str]: ...

    async def set_route(
        self, task: str, route: str, campaign_id: CampaignId | None = None
    ) -> None: ...

    async def estimate_tokens(self, text: str, provider_id: str | None = None) -> int: ...

    async def estimate_cost(self, task: str, request: CompletionRequest) -> float | None: ...

    async def health_check(self, provider_id: str) -> HealthStatus: ...

    async def health_check_all(self) -> dict[str, HealthStatus]: ...


# --------------------------------------------------------------------------- #
# Plugins
# --------------------------------------------------------------------------- #


class ImageGenBackend(Protocol):
    id: str
    name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def list_samplers(self) -> list[str]: ...

    async def health_check(self) -> HealthStatus: ...


class ExportAdapter(Protocol):
    id: str
    name: str
    extensions: list[str]
    mime_type: str

    async def export(
        self,
        campaign_id: CampaignId,
        selection: ExportSelection,
        options: ExportOptions,
        output_path: Path,
    ) -> ExportResult: ...

    def default_options(self) -> ExportOptions: ...

    def option_schema(self) -> JsonSchema: ...


@runtime_checkable
class Plugins(Protocol):
    async def rescan(self) -> RescanReport: ...
    async def list_installed(self) -> list[PluginManifest]: ...
    async def get_manifest(self, plugin_id: PluginId) -> PluginManifest | None: ...
    async def get_status(self, plugin_id: PluginId) -> PluginStatus: ...

    async def load(self, plugin_id: PluginId) -> None: ...
    async def unload(self, plugin_id: PluginId) -> None: ...
    async def activate(self, plugin_id: PluginId) -> None: ...
    async def deactivate(self, plugin_id: PluginId) -> None: ...

    async def get_config(self, plugin_id: PluginId) -> dict: ...
    async def set_config(self, plugin_id: PluginId, config: dict) -> None: ...
    async def validate_config(self, plugin_id: PluginId, config: dict) -> ValidationResult: ...

    def llm_providers(self) -> list[LLMProvider]: ...
    def embedding_providers(self) -> list[EmbeddingProvider]: ...
    def imagegen_backends(self) -> list[ImageGenBackend]: ...
    def export_adapters(self) -> list[ExportAdapter]: ...

    def get_llm_provider(self, id: str) -> LLMProvider | None: ...
    def get_embedding_provider(self, id: str) -> EmbeddingProvider | None: ...
    def get_imagegen_backend(self, id: str) -> ImageGenBackend | None: ...
    def get_export_adapter(self, id: str) -> ExportAdapter | None: ...


# --------------------------------------------------------------------------- #
# Mechanics
# --------------------------------------------------------------------------- #


class MechanicsModule(Protocol):
    """The contract a mechanics module's `mechanics.py` implements."""

    id: MechanicsModuleId
    name: str
    version: str
    api_version: str

    def sheet_schema(self, entity_kind: str) -> JsonSchema | None: ...

    def validate_sheet(self, entity_kind: str, sheet: dict) -> ValidationResult: ...

    def initialize_sheet(self, entity_kind: str, entity_id: str) -> dict: ...

    def list_content_kinds(self) -> list[str]: ...

    def content_schema(self, kind: str) -> JsonSchema: ...

    def capabilities_of(self, entity_ref: CharacterRef, sheet: dict) -> list[Capability]: ...

    def power_definitions(self) -> list[PowerDefinition]: ...

    def power_definition(self, power_id: str) -> PowerDefinition | None: ...

    def evaluate_pre_roll(self, player_input: str, scene: SceneContext) -> list[ProposedRoll]: ...

    def resolve_roll(self, roll: Roll, rng_seed: int) -> RollResult: ...

    def validate_narrated_event(
        self, event: NarratedEvent, scene: SceneContext
    ) -> ValidationResult: ...

    def character_creation_steps(self) -> list[Any]: ...  # list[CreationStep]

    def time_tick(
        self,
        entity_ref: CharacterRef,
        sheet: dict,
        duration: Duration,
        context: Any,  # TickContext
    ) -> list[StateDelta]: ...

    def system_summary(self) -> str: ...


@runtime_checkable
class Mechanics(Protocol):
    """The app-facing façade over the active mechanics module per campaign."""

    async def active_module(self, campaign_id: CampaignId) -> MechanicsModule | None: ...

    async def sheet_schema(
        self, campaign_id: CampaignId, entity_kind: str
    ) -> JsonSchema | None: ...

    async def get_sheet(self, campaign_id: CampaignId, entity_ref: str) -> dict | None: ...

    async def update_sheet(self, campaign_id: CampaignId, entity_ref: str, patch: dict) -> dict: ...

    async def capabilities_of(
        self, campaign_id: CampaignId, entity_ref: str
    ) -> list[Capability]: ...

    async def evaluate_pre_roll(
        self,
        campaign_id: CampaignId,
        player_input: str,
        scene: SceneContext,
    ) -> list[ProposedRoll]: ...

    async def resolve_roll(self, campaign_id: CampaignId, roll: Roll) -> RollResult: ...

    async def validate_narrated_event(
        self,
        campaign_id: CampaignId,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult: ...

    async def time_tick(
        self,
        campaign_id: CampaignId,
        entity_ref: str,
        duration: Duration,
        context: Any,
    ) -> list[StateDelta]: ...

    async def list_installed_modules(self) -> list[ModuleManifest]: ...

    async def module_info(self, module_id: MechanicsModuleId) -> ModuleManifest | None: ...


# --------------------------------------------------------------------------- #
# State Store
# --------------------------------------------------------------------------- #


@runtime_checkable
class StateStore(Protocol):
    # Library reads
    async def get_library_entity(self, library_id: str) -> LibraryEntity | None: ...

    async def list_library_in_setting(self, setting_id: str, kind: str) -> list[LibraryEntity]: ...

    async def query_library(self, predicate: dict) -> list[LibraryEntity]: ...

    async def variants_of(self, asset_id: str, kind: str) -> list[LibraryEntity]: ...

    # Campaign content reads
    async def get_scene_file(self, scene_id: SceneId) -> SceneFile: ...
    async def get_scene_metadata(self, scene_id: SceneId) -> dict: ...
    async def list_scenes(self, campaign_id: CampaignId, branch_id: BranchId) -> list[Scene]: ...

    async def get_emergent(self, campaign_id: CampaignId, kind: str, id: str) -> dict | None: ...
    async def list_emergent(self, campaign_id: CampaignId, kind: str) -> list[dict]: ...

    async def get_override(self, campaign_id: CampaignId, library_id: str) -> dict | None: ...

    async def get_sheet(
        self,
        campaign_id: CampaignId,
        kind: str,
        entity_id: str,
        mechanics_id: MechanicsModuleId,
    ) -> dict | None: ...

    # Composition-aware resolution
    async def resolve_character(
        self,
        character_ref: CharacterRef,
        campaign_id: CampaignId,
        branch_id: BranchId,
    ) -> ResolvedCharacter: ...

    async def resolve_location(
        self,
        location_ref: LocationRef,
        campaign_id: CampaignId,
        branch_id: BranchId,
    ) -> ResolvedLocation: ...

    async def resolve_entity(
        self,
        kind: str,
        ref: str,
        campaign_id: CampaignId,
        branch_id: BranchId,
    ) -> ResolvedEntity: ...

    async def list_for_campaign(
        self, campaign_id: CampaignId, kind: str, filter: dict | None = None
    ) -> list[ResolvedEntity]: ...

    # Retrieval
    async def vector_search(
        self,
        query_text: str,
        campaign_id: CampaignId,
        kinds: list[str] | None = None,
        include_library: bool = True,
        top_k: int = 8,
    ) -> list[SearchResult]: ...

    async def keyword_search(
        self,
        terms: list[str],
        campaign_id: CampaignId,
        kinds: list[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]: ...

    # Audit
    async def get_delta_log(
        self,
        campaign_id: CampaignId | None = None,
        since: datetime | None = None,
        turn_id: TurnId | None = None,
    ) -> list[AppliedDelta]: ...

    # Library writes
    async def write_library_file(
        self,
        library_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> int: ...

    async def delete_library_file(self, library_id: str, source: str) -> None: ...

    # Campaign narrative writes
    async def write_scene_file(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> None: ...

    async def append_post_to_scene(self, scene_id: SceneId, post: Post, source: str) -> None: ...

    async def write_override(
        self,
        campaign_id: CampaignId,
        library_id: str,
        patch: dict,
        source: str,
    ) -> None: ...

    async def write_emergent(
        self,
        campaign_id: CampaignId,
        kind: str,
        entity_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> None: ...

    async def write_sheet(
        self,
        campaign_id: CampaignId,
        kind: str,
        entity_id: str,
        mechanics_id: MechanicsModuleId,
        sheet: dict,
        source: str,
    ) -> None: ...

    async def write_image_metadata(
        self,
        image_id: str,
        metadata: dict,
        source: str,
    ) -> None: ...

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        kind: str,
        campaign_entity_id: str,
        target_setting_id: str,
        source: str,
    ) -> str: ...

    # SQLite writes
    async def apply_delta(self, delta: StateDelta, source: str) -> AppliedDelta: ...
    async def reverse_delta(self, delta_id: str) -> None: ...
    async def queue_for_review(self, delta: StateDelta, source: str) -> ReviewItem: ...

    async def add_fact(self, fact: Fact, source: str) -> None: ...
    async def add_commitment(self, c: Commitment, source: str) -> None: ...
    async def upsert_character_state(self, state: CharacterState, source: str) -> None: ...
    async def upsert_location_state(self, state: LocationState, source: str) -> None: ...
    async def upsert_faction_state(self, state: FactionState, source: str) -> None: ...
    async def advance_time(self, to: datetime, branch_id: BranchId, source: str) -> None: ...

    # Composition
    async def upsert_setting_ref(
        self,
        campaign_id: CampaignId,
        setting_id: str,
        priority: int,
        include: list[str],
        track_latest: bool,
    ) -> None: ...

    async def upgrade_setting_ref(
        self, campaign_id: CampaignId, setting_id: str
    ) -> UpgradeReport: ...

    # PCs
    async def add_pc(
        self,
        campaign_id: CampaignId,
        character_ref: CharacterRef,
        display_name: str,
        owner: str,
    ) -> None: ...

    async def remove_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None: ...


# --------------------------------------------------------------------------- #
# Library
# --------------------------------------------------------------------------- #


@runtime_checkable
class Library(Protocol):
    async def list_settings(self) -> list[SettingMeta]: ...
    async def get_setting(self, setting_id: str) -> SettingMeta: ...
    async def list_in_setting(self, setting_id: str, kind: str) -> list[LibraryEntity]: ...
    async def get_entity(self, setting_id: str, kind: str, entity_id: str) -> LibraryEntity: ...

    async def list_style_guides(self) -> list[LibraryEntity]: ...
    async def list_image_presets(self) -> list[LibraryEntity]: ...
    async def get_style_guide(self, id: str) -> LibraryEntity: ...
    async def get_image_preset(self, id: str) -> LibraryEntity: ...

    async def list_greetings(self, setting_id: str) -> list[Greeting]: ...
    async def get_greeting(self, setting_id: str, id: str) -> Greeting: ...

    async def variants_of(self, asset_id: str, kind: str) -> list[LibraryEntity]: ...

    async def create_setting(self, id: str, meta: dict) -> SettingMeta: ...
    async def create_entity(
        self,
        setting_id: str,
        kind: str,
        entity_id: str,
        frontmatter: dict,
        body: str,
    ) -> LibraryEntity: ...
    async def update_entity(
        self,
        setting_id: str,
        kind: str,
        entity_id: str,
        frontmatter_patch: dict | None = None,
        body: str | None = None,
    ) -> LibraryEntity: ...
    async def delete_entity(self, setting_id: str, kind: str, entity_id: str) -> None: ...

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        entity_kind: EntityKind | str,
        campaign_entity_id: str,
        target_setting_id: str,
    ) -> str: ...

    async def get_composition(self, campaign_id: CampaignId) -> Composition: ...
    async def set_composition(self, campaign_id: CampaignId, composition: Composition) -> None: ...
    async def upgrade_setting_ref(
        self, campaign_id: CampaignId, setting_id: str
    ) -> UpgradeReport: ...

    async def resolve(self, entity_id: str, campaign_id: CampaignId) -> ResolvedEntity: ...

    async def dependents(self, setting_id: str, kind: str, entity_id: str) -> list[CampaignRef]: ...


# --------------------------------------------------------------------------- #
# Setting
# --------------------------------------------------------------------------- #


@runtime_checkable
class Setting(Protocol):
    async def get(self, setting_id: str) -> SettingMeta: ...
    async def list_settings(self) -> list[SettingMeta]: ...
    async def list_in_setting(
        self, setting_id: str, kind: EntityKind | str
    ) -> list[LibraryEntity]: ...
    async def get_entity(
        self, setting_id: str, kind: EntityKind | str, entity_id: str
    ) -> LibraryEntity: ...
    async def resolve(
        self,
        kind: EntityKind | str,
        ref: str,
        campaign_id: CampaignId,
    ) -> ResolvedEntity: ...

    async def list_greetings(self, setting_id: str) -> list[Greeting]: ...

    async def fork_setting(self, src_setting_id: str, dst_setting_id: str) -> None: ...

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        entity_kind: EntityKind | str,
        campaign_entity_id: str,
        target_setting_id: str,
    ) -> str: ...


# --------------------------------------------------------------------------- #
# Characters
# --------------------------------------------------------------------------- #


@runtime_checkable
class Characters(Protocol):
    async def list_in_setting(self, setting_id: str) -> list[Character]: ...
    async def get(self, setting_id: str, id: str) -> Character: ...
    async def create(self, setting_id: str, character: CharacterData) -> Character: ...
    async def update(self, setting_id: str, id: str, patch: dict) -> Character: ...
    async def delete(self, setting_id: str, id: str) -> None: ...

    async def create_emergent(
        self,
        campaign_id: CampaignId,
        character: CharacterData,
        source: str,
    ) -> str: ...

    async def resolve(
        self, character_ref: CharacterRef, campaign_id: CampaignId
    ) -> ResolvedCharacter: ...

    async def list_for_campaign(
        self,
        campaign_id: CampaignId,
        filter: CharacterFilter | None = None,
    ) -> list[ResolvedCharacter]: ...

    async def cross_setting_lookup(
        self,
        character_id: str,
        exclude_setting: str | None = None,
    ) -> list[Character]: ...

    async def get_full_card(self, ref: CharacterRef, campaign_id: CampaignId) -> str: ...
    async def get_compressed_card(self, ref: CharacterRef, campaign_id: CampaignId) -> str: ...
    async def get_voice_only(self, ref: CharacterRef, campaign_id: CampaignId) -> str: ...
    async def get_capsule(self, ref: CharacterRef, campaign_id: CampaignId) -> str: ...

    async def update_state(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        branch_id: BranchId,
        state: CharacterState,
        source: str,
    ) -> None: ...

    async def recommend_tiers(self, scene: Scene) -> dict[CharacterRef, ContextTier]: ...

    async def pin_tier(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        tier: ContextTier,
    ) -> None: ...

    async def check_drift(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        window: int = 10,
    ) -> DriftReport: ...

    async def list_pcs(self, campaign_id: CampaignId) -> list[PCEntry]: ...
    async def add_pc(
        self,
        campaign_id: CampaignId,
        character_ref: CharacterRef,
        name: str,
        owner: str,
    ) -> PCEntry: ...
    async def remove_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None: ...
    async def set_active_pc(self, campaign_id: CampaignId, character_ref: CharacterRef) -> None: ...

    async def present_pcs_in_scene(self, scene_ref: SceneId) -> list[PCEntry]: ...
    async def should_auto_respond(self, scene_ref: SceneId) -> bool: ...

    async def capabilities_of(
        self, ref: CharacterRef, campaign_id: CampaignId
    ) -> list[Capability]: ...

    async def get_relationships(
        self, ref: CharacterRef, campaign_id: CampaignId
    ) -> list[Relationship]: ...

    async def promote_to_library(
        self,
        campaign_id: CampaignId,
        character_id: str,
        target_setting_id: str,
    ) -> str: ...

    async def import_sillytavern(self, card: bytes, target_setting_id: str) -> ImportResult: ...
    async def import_charx(self, charx: bytes, target_setting_id: str) -> ImportResult: ...
    async def import_plaintext(self, text: str, target_setting_id: str) -> ImportResult: ...


# --------------------------------------------------------------------------- #
# Scene Manager
# --------------------------------------------------------------------------- #


@runtime_checkable
class SceneManager(Protocol):
    async def list_scenes(self, campaign_id: CampaignId, branch_id: BranchId) -> list[Scene]: ...
    async def get_scene(self, scene_id: SceneId) -> Scene: ...
    async def get_scene_file_path(self, scene_id: SceneId) -> Path: ...
    async def load_scene_body(self, scene_id: SceneId) -> str: ...

    async def active_scene_for_campaign(
        self, campaign_id: CampaignId, branch_id: BranchId
    ) -> Scene | None: ...
    async def active_scene_for_pc(
        self, campaign_id: CampaignId, pc_ref: CharacterRef
    ) -> Scene | None: ...

    async def start_scene(self, init: SceneInit) -> Scene: ...
    async def close_scene(self, scene_id: SceneId) -> SceneCloseReport: ...

    async def append_post(self, scene_id: SceneId, post: Post) -> None: ...
    async def get_posts(
        self,
        scene_id: SceneId,
        range: tuple[int, int] | None = None,
    ) -> list[Post]: ...
    async def posts_since_last_advance(self, scene_id: SceneId) -> list[Post]: ...
    async def recent_posts(self, scene_id: SceneId, n: int = 10) -> list[Post]: ...

    async def is_scene_break(self, scene_id: SceneId, player_input: str) -> SceneBreakDecision: ...
    async def on_post_submitted(self, scene_id: SceneId, post: Post) -> AdvanceDecision: ...
    async def on_advance_requested(self, scene_id: SceneId) -> AdvanceResult: ...

    async def update_running_summary(self, scene_id: SceneId) -> str: ...

    async def add_thread(self, scene_id: SceneId, thread: Thread, kind: str) -> None: ...
    async def list_threads(self, scene_id: SceneId) -> SceneThreads: ...

    async def edit_post(self, post_id: PostId, new_body: str, source: str) -> None: ...
    async def delete_post(self, post_id: PostId, source: str) -> None: ...

    async def fork_scenes_for_branch(
        self, campaign_id: CampaignId, branch_id: BranchId
    ) -> None: ...


# --------------------------------------------------------------------------- #
# Continuity
# --------------------------------------------------------------------------- #


@runtime_checkable
class Continuity(Protocol):
    async def add_fact(self, fact: Fact, source: str) -> FactId: ...
    async def retire_fact(self, id: FactId, in_post: PostId, reason: str) -> None: ...
    async def update_fact(self, id: FactId, patch: dict) -> Fact: ...

    async def get_fact(self, id: FactId) -> Fact: ...
    async def facts_about(self, **subjects: Any) -> list[Fact]: ...
    async def search_facts(self, query: str, top_k: int = 10) -> list[Fact]: ...
    async def recent_facts(self, since: InGameTime, limit: int = 50) -> list[Fact]: ...

    async def check_contradictions(self, candidate: Fact) -> list[ContradictionReport]: ...
    async def resolve_contradiction(self, report_id: str, resolution: dict) -> None: ...

    async def add_commitment(self, c: Commitment, source: str) -> CommitmentId: ...
    async def resolve_commitment(
        self, id: CommitmentId, status: CommitmentStatus, in_post: PostId
    ) -> None: ...
    async def get_commitment(self, id: CommitmentId) -> Commitment: ...
    async def open_commitments(self, **filters: Any) -> list[Commitment]: ...
    async def overdue_commitments(self, as_of: InGameTime) -> list[Commitment]: ...
    async def stale_commitments(self, threshold: Duration) -> list[Commitment]: ...

    async def knows(self, character_id: CharacterRef, fact_id: FactId) -> bool: ...
    async def reveal(
        self,
        fact_id: FactId,
        to: list[CharacterRef],
        in_post: PostId,
        source: str,
    ) -> None: ...
    async def secrets_of(self, character_id: CharacterRef) -> list[Fact]: ...

    async def age(self, to_time: InGameTime) -> AgingReport: ...


# --------------------------------------------------------------------------- #
# Time Engine
# --------------------------------------------------------------------------- #


@runtime_checkable
class TimeEngine(Protocol):
    def current(self) -> InGameTime: ...
    def calendar(self) -> dict: ...

    async def advance(
        self,
        duration: Duration,
        reason: TimeAdvanceReason,
        scene_id: SceneId | None = None,
    ) -> TimeAdvanceResult: ...

    async def skip_to(
        self,
        target: InGameTime,
        reason: TimeAdvanceReason,
    ) -> TimeAdvanceResult: ...

    async def schedule_event(self, event: ScheduledEvent) -> EventId: ...
    async def cancel_event(self, event_id: EventId) -> None: ...
    async def upcoming_events(self, within: Duration) -> list[ScheduledEvent]: ...

    def subscribe_calendar(self, handler: Callable[[Event], Awaitable[None]]) -> SubscriptionId: ...


# --------------------------------------------------------------------------- #
# Context Builder
# --------------------------------------------------------------------------- #


@runtime_checkable
class ContextBuilder(Protocol):
    async def build(
        self,
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult] | None = None,
        extra: str | None = None,
    ) -> AssembledPrompt: ...

    async def estimate(self, player_input: str, campaign_id: CampaignId) -> BudgetEstimate: ...


# --------------------------------------------------------------------------- #
# Extractor
# --------------------------------------------------------------------------- #


@runtime_checkable
class Extractor(Protocol):
    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        prior_state_snapshot: StateSnapshot,
    ) -> ExtractionResult: ...

    async def extract_from_user_text(
        self,
        user_text: str,
        scene: Scene,
        campaign_id: CampaignId,
    ) -> ExtractionResult: ...


# --------------------------------------------------------------------------- #
# ImageGen
# --------------------------------------------------------------------------- #


@runtime_checkable
class ImageGen(Protocol):
    async def list_backends(self) -> list[BackendInfo]: ...
    async def active_backend(self, campaign_id: CampaignId) -> BackendInfo: ...
    async def set_active_backend(self, campaign_id: CampaignId, backend_id: str) -> None: ...

    async def queue_generation(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId | None,
        post_id: PostId | None,
        request: GenerationRequest | None = None,
        priority: int = 5,
    ) -> str: ...

    async def generate_sync(
        self,
        campaign_id: CampaignId,
        request: GenerationRequest,
    ) -> GenerationResult: ...

    async def list_jobs(
        self,
        campaign_id: CampaignId,
        status: JobStatus | None = None,
    ) -> list[GenerationJob]: ...
    async def cancel_job(self, job_id: str) -> None: ...
    async def prioritize_job(self, job_id: str, priority: int) -> None: ...

    async def reroll(self, image_id: str) -> str: ...
    async def variation(self, image_id: str, strength: float) -> str: ...

    async def list_images(
        self,
        campaign_id: CampaignId,
        scene_id: SceneId | None = None,
        starred_only: bool = False,
    ) -> list[ImageMetadata]: ...
    async def get_image(self, image_id: str) -> ImageMetadata: ...
    async def star_image(self, image_id: str, starred: bool) -> None: ...
    async def delete_image(self, image_id: str) -> None: ...

    async def health_check(self, backend_id: str) -> HealthStatus: ...


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


@runtime_checkable
class Export(Protocol):
    def list_adapters(self) -> list[ExportAdapter]: ...
    def get_adapter(self, id: str) -> ExportAdapter: ...

    async def export(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportResult: ...

    async def preview(
        self,
        campaign_id: CampaignId,
        adapter_id: str,
        selection: ExportSelection,
        options: ExportOptions,
    ) -> ExportPreview: ...

    async def history(self, campaign_id: CampaignId) -> list[ExportRecord]: ...


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #


@runtime_checkable
class CostTracker(Protocol):
    async def record(self, call: LLMCallRecord) -> None: ...
    async def total(
        self,
        campaign_id: CampaignId | None = None,
        provider: str | None = None,
        model: str | None = None,
        task: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CostTotal: ...
    async def by_day(self, campaign_id: CampaignId, days: int = 30) -> list[DailyCost]: ...
    async def by_task(self, campaign_id: CampaignId) -> dict[str, float]: ...
    async def by_model(self, campaign_id: CampaignId) -> dict[str, float]: ...


@runtime_checkable
class HealthMonitor(Protocol):
    async def probe(self, target: HealthTarget) -> HealthStatus: ...
    async def probe_all(self) -> list[HealthStatus]: ...
    def subscribe(self, handler: Callable[[HealthStatus], Awaitable[None]]) -> SubscriptionId: ...
    def latest(self) -> dict[str, HealthStatus]: ...


@runtime_checkable
class TurnReplayer(Protocol):
    async def replay(self, turn_id: TurnId, opts: ReplayOptions) -> ReplayResult: ...


@runtime_checkable
class Observability(Protocol):
    async def record_turn_audit(self, audit: TurnAudit) -> None: ...
    async def get_turn_audit(self, turn_id: TurnId) -> TurnAudit: ...
    async def list_turn_audits(
        self,
        campaign_id: CampaignId,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TurnAudit]: ...

    async def replay_turn(self, turn_id: TurnId, opts: ReplayOptions) -> ReplayResult: ...

    def costs(self) -> CostTracker: ...
    def health(self) -> HealthMonitor: ...

    async def log(self, event: LogEvent) -> None: ...
    async def query_log(self, query: LogQuery) -> list[LogEvent]: ...

    async def record_error(self, err: ErrorRecord) -> None: ...
    async def recent_errors(self, limit: int = 50) -> list[ErrorRecord]: ...


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


@runtime_checkable
class Orchestrator(Protocol):
    async def submit_post(
        self,
        campaign_id: CampaignId,
        pc_ref: CharacterRef,
        text: str,
        metadata: dict | None = None,
    ) -> SubmitResult: ...

    async def advance(self, campaign_id: CampaignId, scene_id: SceneId) -> AdvanceResult: ...

    async def regenerate_last(self, campaign_id: CampaignId) -> RegenerateResult: ...

    async def undo_turn(self, campaign_id: CampaignId, count: int = 1) -> UndoResult: ...
    async def retcon_post(self, post_id: PostId, new_text: str) -> RetconResult: ...
    async def fork(
        self,
        campaign_id: CampaignId,
        from_turn_id: TurnId,
        label: str,
    ) -> ForkResult: ...

    async def turn_in_progress(self, campaign_id: CampaignId) -> TurnStatus | None: ...
    async def queue_length(self, campaign_id: CampaignId) -> int: ...

    def event_bus(self) -> EventBus: ...


# --------------------------------------------------------------------------- #
# File / content helpers (re-exported for convenience)
# --------------------------------------------------------------------------- #


@runtime_checkable
class FileWatcher(Protocol):
    """Subset of `watchdog.observers.Observer` that we depend on."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...

    def schedule(
        self,
        handler: Any,
        path: str,
        recursive: bool = True,
    ) -> Any: ...


# A small alias used by parsing helpers
SlugIterable = Iterable[str]
