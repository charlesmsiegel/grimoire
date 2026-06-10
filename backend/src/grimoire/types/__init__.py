"""Shared types and protocol stubs for Grimoire.

This package is the single source of truth for cross-module dataclasses and
`typing.Protocol` interfaces referenced throughout the specs. It contains pure
type declarations — no behavior. Backend modules and tests both import from
here so they stay in agreement on shape.
"""

from __future__ import annotations

from .characters import (
    AwarenessState,
    Character,
    CharacterData,
    CharacterFilter,
    CharacterPrivacy,
    CharacterRole,
    DriftReport,
    ImagePromptTemplate,
    ImportResult,
    InternalThoughtsPrivacy,
    PCEntry,
    RelationshipEvent,
    RelationshipState,
    ResolvedCharacter,
    StructuralRelationship,
    VoiceAnchor,
)
from .common import (
    CampaignId,
    CharacterRef,
    CommitmentId,
    Duration,
    EntityKind,
    EntityRef,
    EventId,
    FactId,
    FactionRef,
    GenJobId,
    HealthLevel,
    HealthStatus,
    InGameTime,
    ItemRef,
    Json,
    JsonSchema,
    LocationRef,
    MechanicsModuleId,
    PluginId,
    PostId,
    SceneId,
    SceneRef,
    Scope,
    SubscriptionId,
    TurnId,
    ValidationResult,
)
from .composition import (
    CampaignRef,
    CharacterVariant,
    Composition,
    Greeting,
    LibraryEntity,
    ResolutionLayer,
    ResolutionSource,
    ResolvedEntity,
    ResolvedLocation,
    UpgradeReport,
    WorldMeta,
    WorldRef,
)
from .context import AssembledPrompt, BudgetEstimate, ContextSource
from .continuity import (
    AgingReport,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    ContradictionReport,
    Fact,
    FactScope,
    FactSource,
    FactSubject,
    KnowledgeEntry,
    Relationship,
    StaleCommitmentQuery,
)
from .export import (
    ExportCapabilities,
    ExportOptions,
    ExportPreview,
    ExportRecord,
    ExportResult,
    ExportSelection,
)
from .extraction import (
    EntityCandidate,
    ExtractionFlag,
    ExtractionResult,
    FlagLevel,
)
from .imagegen import (
    BackendCapabilities,
    BackendInfo,
    GenerationJob,
    GenerationRequest,
    GenerationResult,
    ImageMetadata,
    JobStatus,
    LoraSpec,
)
from .llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    LLMCallRecord,
    Message,
    MessageRole,
    ModelInfo,
    ModelParams,
    ProviderCapabilities,
    RetryPolicy,
    TimeoutPolicy,
    TokenUsage,
)
from .mechanics import (
    ApiVersion,
    Capability,
    CreationStep,
    MechanicsResult,
    ModuleManifest,
    NarratedEvent,
    PowerDefinition,
    ProposedRoll,
    ResourceCost,
    Roll,
    RollModifier,
    RollResult,
    TickContext,
)
from .observability import (
    CompositionSnapshot,
    ContextSummary,
    CostTotal,
    DailyCost,
    ErrorRecord,
    HealthTarget,
    LogEvent,
    LogLevel,
    LogQuery,
    MetricSample,
    ReplayOptions,
    ReplayResult,
    ReplaySubstitution,
    TurnAudit,
    WarningRecord,
)
from .orchestrator import (
    Event,
    EventRecord,
    EventType,
    RetconResult,
    SubmitResult,
    Subscription,
    TurnStatus,
    UndoResult,
)
from .plugins import (
    PluginKind,
    PluginLifecycle,
    PluginManifest,
    PluginStatus,
    RescanReport,
)
from .protocols import (
    Characters as CharactersProtocol,
)
from .protocols import (
    ContextBuilder as ContextBuilderProtocol,
)
from .protocols import (
    Continuity as ContinuityProtocol,
)
from .protocols import (
    CostTracker as CostTrackerProtocol,
)
from .protocols import (
    EmbeddingProvider,
    EventBus,
    EventHandler,
    ExportAdapter,
    FileWatcher,
    ImageGenBackend,
    LLMProvider,
    MechanicsModule,
)
from .protocols import (
    Export as ExportProtocol,
)
from .protocols import (
    Extractor as ExtractorProtocol,
)
from .protocols import (
    HealthMonitor as HealthMonitorProtocol,
)
from .protocols import (
    ImageGen as ImageGenProtocol,
)
from .protocols import (
    Library as LibraryProtocol,
)
from .protocols import (
    LLMGateway as LLMGatewayProtocol,
)
from .protocols import (
    Mechanics as MechanicsProtocol,
)
from .protocols import (
    Observability as ObservabilityProtocol,
)
from .protocols import (
    Orchestrator as OrchestratorProtocol,
)
from .protocols import (
    Plugins as PluginsProtocol,
)
from .protocols import (
    SceneManager as SceneManagerProtocol,
)
from .protocols import (
    StateStore as StateStoreProtocol,
)
from .protocols import (
    TimeEngine as TimeEngineProtocol,
)
from .protocols import (
    TurnReplayer as TurnReplayerProtocol,
)
from .protocols import (
    World as WorldProtocol,
)
from .scene import (
    AdvanceDecision,
    AdvanceResult,
    AuthorKind,
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
    DeltaKind,
    FactionState,
    LocationState,
    ReviewItem,
    ReviewStatus,
    SearchResult,
    StateDelta,
    StateSnapshot,
)
from .time import (
    FactionTickSummary,
    NpcTickSummary,
    ScheduledEvent,
    TimeAdvanceReason,
    TimeAdvanceResult,
    WeatherChange,
)
from .transient import (
    DecayHint,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientUpdateProposal,
    TransientValue,
)
from .transient import (
    EntityKind as TransientEntityKind,
)
from .world import (
    Coords,
    Faction,
    FactionGoal,
    FactionStateData,
    Holiday,
    Item,
    Location,
    LocationConnection,
    LocationKind,
    LocationStateData,
    LoreEntry,
    Month,
    Season,
    SecrecyLevel,
    Weather,
    WeatherKind,
    WorldCalendar,
)

__all__ = [  # noqa: RUF022 — grouped by topic for readability
    # common
    "CampaignId",
    "CharacterRef",
    "CommitmentId",
    "EntityKind",
    "EntityRef",
    "EventId",
    "FactId",
    "FactionRef",
    "GenJobId",
    "HealthLevel",
    "HealthStatus",
    "ItemRef",
    "Json",
    "JsonSchema",
    "LocationRef",
    "MechanicsModuleId",
    "PluginId",
    "PostId",
    "SceneId",
    "SceneRef",
    "Scope",
    "SubscriptionId",
    "TurnId",
    "ValidationResult",
    # time
    "Duration",
    "FactionTickSummary",
    "InGameTime",
    "NpcTickSummary",
    "ScheduledEvent",
    "TimeAdvanceReason",
    "TimeAdvanceResult",
    "WeatherChange",
    # composition
    "CampaignRef",
    "CharacterVariant",
    "Composition",
    "Greeting",
    "LibraryEntity",
    "ResolutionLayer",
    "ResolutionSource",
    "ResolvedEntity",
    "ResolvedLocation",
    "WorldMeta",
    "WorldRef",
    "UpgradeReport",
    # world
    "Coords",
    "Faction",
    "FactionGoal",
    "FactionStateData",
    "Holiday",
    "Item",
    "Location",
    "LocationConnection",
    "LocationKind",
    "LocationStateData",
    "LoreEntry",
    "Month",
    "SecrecyLevel",
    "Season",
    "WorldCalendar",
    "Weather",
    "WeatherKind",
    # scene
    "AdvanceDecision",
    "AdvanceResult",
    "AuthorKind",
    "Post",
    "Scene",
    "SceneBreakDecision",
    "SceneCloseReport",
    "SceneContext",
    "SceneFile",
    "SceneInit",
    "SceneThreads",
    "Thread",
    # state
    "AppliedDelta",
    "CharacterState",
    "ContextTier",
    "DeltaKind",
    "FactionState",
    "LocationState",
    "ReviewItem",
    "ReviewStatus",
    "SearchResult",
    "StateDelta",
    "StateSnapshot",
    # continuity
    "AgingReport",
    "Commitment",
    "CommitmentKind",
    "CommitmentStatus",
    "ContradictionReport",
    "Fact",
    "FactScope",
    "FactSource",
    "FactSubject",
    "KnowledgeEntry",
    "Relationship",
    "StaleCommitmentQuery",
    # characters
    "AwarenessState",
    "Character",
    "CharacterData",
    "CharacterFilter",
    "CharacterRole",
    "DriftReport",
    "CharacterPrivacy",
    "ImagePromptTemplate",
    "ImportResult",
    "InternalThoughtsPrivacy",
    "PCEntry",
    "RelationshipEvent",
    "RelationshipState",
    "ResolvedCharacter",
    "StructuralRelationship",
    "VoiceAnchor",
    # mechanics
    "ApiVersion",
    "Capability",
    "CreationStep",
    "MechanicsResult",
    "ModuleManifest",
    "NarratedEvent",
    "PowerDefinition",
    "ProposedRoll",
    "ResourceCost",
    "Roll",
    "RollModifier",
    "RollResult",
    "TickContext",
    # llm
    "CompletionChunk",
    "CompletionRequest",
    "CompletionResponse",
    "LLMCallRecord",
    "Message",
    "MessageRole",
    "ModelInfo",
    "ModelParams",
    "ProviderCapabilities",
    "RetryPolicy",
    "TimeoutPolicy",
    "TokenUsage",
    # imagegen
    "BackendCapabilities",
    "BackendInfo",
    "GenerationJob",
    "GenerationRequest",
    "GenerationResult",
    "ImageMetadata",
    "JobStatus",
    "LoraSpec",
    # export
    "ExportCapabilities",
    "ExportOptions",
    "ExportPreview",
    "ExportRecord",
    "ExportResult",
    "ExportSelection",
    # extraction
    "EntityCandidate",
    "ExtractionFlag",
    "ExtractionResult",
    "FlagLevel",
    # context
    "AssembledPrompt",
    "BudgetEstimate",
    "ContextSource",
    # plugins
    "PluginKind",
    "PluginLifecycle",
    "PluginManifest",
    "PluginStatus",
    "RescanReport",
    # orchestrator
    "Event",
    "EventRecord",
    "EventType",
    "RetconResult",
    "SubmitResult",
    "Subscription",
    "TurnStatus",
    "UndoResult",
    # observability
    "CompositionSnapshot",
    "ContextSummary",
    "CostTotal",
    "DailyCost",
    "ErrorRecord",
    "HealthTarget",
    "LogEvent",
    "LogLevel",
    "LogQuery",
    "MetricSample",
    "ReplayOptions",
    "ReplayResult",
    "ReplaySubstitution",
    "TurnAudit",
    "WarningRecord",
    # plugin-shaped protocols
    "EmbeddingProvider",
    "EventBus",
    "EventHandler",
    "ExportAdapter",
    "FileWatcher",
    "ImageGenBackend",
    "LLMProvider",
    "MechanicsModule",
    # module-shaped protocols
    "CharactersProtocol",
    "ContextBuilderProtocol",
    "ContinuityProtocol",
    "CostTrackerProtocol",
    "ExportProtocol",
    "ExtractorProtocol",
    "HealthMonitorProtocol",
    "ImageGenProtocol",
    "LLMGatewayProtocol",
    "LibraryProtocol",
    "MechanicsProtocol",
    "ObservabilityProtocol",
    "OrchestratorProtocol",
    "PluginsProtocol",
    "SceneManagerProtocol",
    "WorldProtocol",
    "StateStoreProtocol",
    "TimeEngineProtocol",
    "TurnReplayerProtocol",
    # transient state
    "DecayHint",
    "ObserverKind",
    "Provenance",
    "TransientConflict",
    "TransientEntityKind",
    "TransientUpdateProposal",
    "TransientValue",
]
