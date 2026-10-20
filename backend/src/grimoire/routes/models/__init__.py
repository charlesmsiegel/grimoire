"""Request bodies for the route modules.

Plain ``BaseModel`` subclasses only — no ``Field``, validators or
``ConfigDict`` — so the Android build can pin pydantic 1.x
(docs/android-architecture.md §7). Dump them with ``common._dump``.

A list or dict default here (``tags: list[str] = []``) is a *pydantic field*
default, not the shared-mutable-default hazard the same line would be on a
plain class or a dataclass: pydantic copies it per instance, so one request
mutating its own ``tags`` cannot reach the next. The health report flags these
as ``mutable_class_attribute`` and proposes ``Field(default_factory=...)``,
which is exactly what the paragraph above forbids — so the finding stays open
by decision, not oversight. ``test_pydantic_guard`` pins the copying against
whichever pydantic is installed, so the decision rests on a checked claim
rather than on this paragraph.
"""


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, PositiveInt, StrictStr

from ...store import membership


# ---- models ----

from ._settings import (
    ConfigUpdate,
    PromptLayoutSection,
    PromptLayoutUpdate,
    ConnectionCreate,
    ConnectionUpdate,
    FactsUpdate,
    HealthCheck,
    CatalogProbe,
    ModelTestPreview,
    ModelTestRun,
    DataDirUpdate,
    StyleCreate,
    StyleUpdate,
    ResponseSettings,
    GroupSettings,
    RegexLayer,
    RegexTest,
    RegexImportPreview,
    RegexImport,
    QuickReplySetBody,
    TrackerLayer,
    CampaignPlayDecisions,
    CampaignTracker,
    TrackerEdit,
    ResponsePresetCreate,
    ResponsePresetUpdate,
    RegenerateBody,
    RetryBody,
    BudgetBody,
    PricingBody,
)

from ._worlds import (
    NameBody,
    WorldCreate,
    WorldUpdate,
    RollBody,
    ProposalAction,
    CheckBody,
    NewCampaign,
    ForkCampaign,
    WeatherOverride,
    CampaignClimate,
    WeatherRange,
)

from ._mechanics import (
    ModuleCreate,
    ModuleManifestBody,
    ModuleGroupBody,
    ModuleSheetTypeBody,
    ModuleCheckBody,
    ModuleDefaultsBody,
    ModuleRuleBody,
    ModuleContentBody,
    ModuleLayoutBody,
    ModuleThemeBody,
    ModuleRenameBody,
    ModuleSetting,
    SheetBody,
    SheetCreationBody,
    SheetAdvanceBody,
    SheetBulkBody,
)

from ._actors import (
    PickBody,
    MarkBody,
    EntityCreate,
    EntityReclassify,
    GroupRefBinding,
    EntityUpdate,
    CharacterCreate,
    CharacterTextParse,
    CharacterTextRefine,
    VersionCreate,
    VersionUpdate,
    DefaultVersion,
    CharacterBirthdate,
    CharacterTags,
    ChubImportBody,
    ChubSourceBody,
    TaglineSave,
    VoiceAnchorSave,
    GroupStateSave,
    AvatarFocus,
    ImagePromote,
    RecordArtLink,
    ImageDescription,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    Ref,
    RefList,
    SyncPin,
    PushBody,
    DemoteBody,
)

from ._scenes import (
    PromptText,
    NewScene,
    RenameScene,
    BranchScene,
    ChronicleSave,
    ChatTurn,
    Appear,
    SceneLocation,
    SceneDatetime,
    AdvanceTime,
    CalendarConfig,
    NoticeMark,
    ScheduledEventCreate,
    ScheduledEventEdit,
    EditMessage,
    ExcludeMessage,
    ReplayStart,
    ReplayCancel,
    Dismiss,
    EmergentCast,
    GreetingCreate,
    SubjectsBody,
    GreetingSubjectsBody,
    CopyFromGreeting,
    GreetingUpdate,
    Edges,
    ImportGreetings,
    StartFromGreeting,
    Opener,
    SceneIntent,
    SceneSuggestionsRequest,
    SceneIdeaCreate,
    SceneIdeaStatus,
    FirstPost,
    LoreEntry,
    LorebookCommit,
    ScenarioCharacter,
    ScenarioGreeting,
    ScenarioProposal,
    ScenarioUrlBody,
    AppearBatch,
    ImportedMessage,
    SceneImportCommit,
)

from ._continuity import (
    PinRule,
    ThreadSave,
    CommitmentSave,
    FactSave,
    KnowledgePost,
    KnowledgeFingerprint,
    KnowledgeSave,
    KnowledgeEdit,
    FactRecord,
    FactRetire,
    RelationshipSave,
    ChronicleLineSave,
    ContinuityAliasCreate,
    ContinuityLinkCreate,
    ContinuityDismiss,
)

from ._extras import (
    SamplerPresetBody,
    SamplerImportBody,
    AuthorsNote,
    ImageMigrationStart,
    ImageGcStart,
    MembershipResponse,
    ActorGroup,
    ActorMembershipResponse,
    HistoryQuestion,
)

