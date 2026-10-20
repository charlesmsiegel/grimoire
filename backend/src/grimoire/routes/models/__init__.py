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

Theory: these are wire request shapes, grouped by the domain that consumes
them, with this facade preserving class identity for existing callers.
Cross-domain annotations import their defining class directly; they must be
available at runtime on both Pydantic versions. New fields stay plain fields,
and persistence and validation policy remain with the existing route/store.
"""


from __future__ import annotations

from typing import Any as Any
from typing import Literal as Literal

from pydantic import BaseModel as BaseModel
from pydantic import PositiveInt as PositiveInt
from pydantic import StrictStr as StrictStr

from ...store import membership as membership
from ._actors import AvatarFocus as AvatarFocus
from ._actors import CharacterBirthdate as CharacterBirthdate
from ._actors import CharacterCreate as CharacterCreate
from ._actors import CharacterTags as CharacterTags
from ._actors import CharacterTextParse as CharacterTextParse
from ._actors import CharacterTextRefine as CharacterTextRefine
from ._actors import ChubImportBody as ChubImportBody
from ._actors import ChubSourceBody as ChubSourceBody
from ._actors import DefaultVersion as DefaultVersion
from ._actors import DemoteBody as DemoteBody
from ._actors import EntityCreate as EntityCreate
from ._actors import EntityReclassify as EntityReclassify
from ._actors import EntityUpdate as EntityUpdate
from ._actors import GroupRefBinding as GroupRefBinding
from ._actors import GroupStateSave as GroupStateSave
from ._actors import ImageDescription as ImageDescription
from ._actors import ImagePromote as ImagePromote
from ._actors import MarkBody as MarkBody
from ._actors import PCCreate as PCCreate
from ._actors import PCUpdate as PCUpdate
from ._actors import PersonaVersionCreate as PersonaVersionCreate
from ._actors import PersonaVersionUpdate as PersonaVersionUpdate
from ._actors import PickBody as PickBody
from ._actors import PushBody as PushBody
from ._actors import RecordArtLink as RecordArtLink
from ._actors import Ref as Ref
from ._actors import RefList as RefList
from ._actors import SyncPin as SyncPin
from ._actors import TaglineSave as TaglineSave
from ._actors import VersionCreate as VersionCreate
from ._actors import VersionUpdate as VersionUpdate
from ._actors import VoiceAnchorSave as VoiceAnchorSave
from ._continuity import ChronicleLineSave as ChronicleLineSave
from ._continuity import CommitmentSave as CommitmentSave
from ._continuity import ContinuityAliasCreate as ContinuityAliasCreate
from ._continuity import ContinuityDismiss as ContinuityDismiss
from ._continuity import ContinuityLinkCreate as ContinuityLinkCreate
from ._continuity import FactRecord as FactRecord
from ._continuity import FactRetire as FactRetire
from ._continuity import FactSave as FactSave
from ._continuity import KnowledgeEdit as KnowledgeEdit
from ._continuity import KnowledgeFingerprint as KnowledgeFingerprint
from ._continuity import KnowledgePost as KnowledgePost
from ._continuity import KnowledgeSave as KnowledgeSave
from ._continuity import PinRule as PinRule
from ._continuity import RelationshipSave as RelationshipSave
from ._continuity import ThreadSave as ThreadSave
from ._extras import ActorGroup as ActorGroup
from ._extras import ActorMembershipResponse as ActorMembershipResponse
from ._extras import AuthorsNote as AuthorsNote
from ._extras import HistoryQuestion as HistoryQuestion
from ._extras import ImageGcStart as ImageGcStart
from ._extras import ImageMigrationStart as ImageMigrationStart
from ._extras import MembershipResponse as MembershipResponse
from ._extras import SamplerImportBody as SamplerImportBody
from ._extras import SamplerPresetBody as SamplerPresetBody
from ._mechanics import ModuleCheckBody as ModuleCheckBody
from ._mechanics import ModuleContentBody as ModuleContentBody
from ._mechanics import ModuleCreate as ModuleCreate
from ._mechanics import ModuleDefaultsBody as ModuleDefaultsBody
from ._mechanics import ModuleGroupBody as ModuleGroupBody
from ._mechanics import ModuleLayoutBody as ModuleLayoutBody
from ._mechanics import ModuleManifestBody as ModuleManifestBody
from ._mechanics import ModuleRenameBody as ModuleRenameBody
from ._mechanics import ModuleRuleBody as ModuleRuleBody
from ._mechanics import ModuleSetting as ModuleSetting
from ._mechanics import ModuleSheetTypeBody as ModuleSheetTypeBody
from ._mechanics import ModuleThemeBody as ModuleThemeBody
from ._mechanics import SheetAdvanceBody as SheetAdvanceBody
from ._mechanics import SheetBody as SheetBody
from ._mechanics import SheetBulkBody as SheetBulkBody
from ._mechanics import SheetCreationBody as SheetCreationBody
from ._scenes import AdvanceTime as AdvanceTime
from ._scenes import Appear as Appear
from ._scenes import AppearBatch as AppearBatch
from ._scenes import BranchScene as BranchScene
from ._scenes import CalendarConfig as CalendarConfig
from ._scenes import ChatTurn as ChatTurn
from ._scenes import ChronicleSave as ChronicleSave
from ._scenes import CopyFromGreeting as CopyFromGreeting
from ._scenes import Dismiss as Dismiss
from ._scenes import Edges as Edges
from ._scenes import EditMessage as EditMessage
from ._scenes import EmergentCast as EmergentCast
from ._scenes import ExcludeMessage as ExcludeMessage
from ._scenes import FirstPost as FirstPost
from ._scenes import GreetingCreate as GreetingCreate
from ._scenes import GreetingSubjectsBody as GreetingSubjectsBody
from ._scenes import GreetingUpdate as GreetingUpdate
from ._scenes import ImportedMessage as ImportedMessage
from ._scenes import ImportGreetings as ImportGreetings
from ._scenes import LorebookCommit as LorebookCommit
from ._scenes import LoreEntry as LoreEntry
from ._scenes import NewScene as NewScene
from ._scenes import NoticeMark as NoticeMark
from ._scenes import Opener as Opener
from ._scenes import PromptText as PromptText
from ._scenes import RenameScene as RenameScene
from ._scenes import ReplayCancel as ReplayCancel
from ._scenes import ReplayStart as ReplayStart
from ._scenes import ScenarioCharacter as ScenarioCharacter
from ._scenes import ScenarioGreeting as ScenarioGreeting
from ._scenes import ScenarioProposal as ScenarioProposal
from ._scenes import ScenarioUrlBody as ScenarioUrlBody
from ._scenes import SceneDatetime as SceneDatetime
from ._scenes import SceneIdeaCreate as SceneIdeaCreate
from ._scenes import SceneIdeaStatus as SceneIdeaStatus
from ._scenes import SceneImportCommit as SceneImportCommit
from ._scenes import SceneIntent as SceneIntent
from ._scenes import SceneLocation as SceneLocation
from ._scenes import SceneSuggestionsRequest as SceneSuggestionsRequest
from ._scenes import ScheduledEventCreate as ScheduledEventCreate
from ._scenes import ScheduledEventEdit as ScheduledEventEdit
from ._scenes import StartFromGreeting as StartFromGreeting
from ._scenes import SubjectsBody as SubjectsBody
from ._settings import BudgetBody as BudgetBody
from ._settings import CampaignPlayDecisions as CampaignPlayDecisions
from ._settings import CampaignTracker as CampaignTracker
from ._settings import CatalogProbe as CatalogProbe

# ---- models ----
from ._settings import ConfigUpdate as ConfigUpdate
from ._settings import ConnectionCreate as ConnectionCreate
from ._settings import ConnectionUpdate as ConnectionUpdate
from ._settings import DataDirUpdate as DataDirUpdate
from ._settings import FactsUpdate as FactsUpdate
from ._settings import GroupSettings as GroupSettings
from ._settings import HealthCheck as HealthCheck
from ._settings import ModelTestPreview as ModelTestPreview
from ._settings import ModelTestRun as ModelTestRun
from ._settings import PricingBody as PricingBody
from ._settings import PromptLayoutSection as PromptLayoutSection
from ._settings import PromptLayoutUpdate as PromptLayoutUpdate
from ._settings import QuickReplySetBody as QuickReplySetBody
from ._settings import RegenerateBody as RegenerateBody
from ._settings import RegexImport as RegexImport
from ._settings import RegexImportPreview as RegexImportPreview
from ._settings import RegexLayer as RegexLayer
from ._settings import RegexTest as RegexTest
from ._settings import ResponsePresetCreate as ResponsePresetCreate
from ._settings import ResponsePresetUpdate as ResponsePresetUpdate
from ._settings import ResponseSettings as ResponseSettings
from ._settings import RetryBody as RetryBody
from ._settings import StyleCreate as StyleCreate
from ._settings import StyleUpdate as StyleUpdate
from ._settings import TrackerEdit as TrackerEdit
from ._settings import TrackerLayer as TrackerLayer
from ._worlds import CampaignClimate as CampaignClimate
from ._worlds import CheckBody as CheckBody
from ._worlds import ForkCampaign as ForkCampaign
from ._worlds import NameBody as NameBody
from ._worlds import NewCampaign as NewCampaign
from ._worlds import ProposalAction as ProposalAction
from ._worlds import RollBody as RollBody
from ._worlds import WeatherOverride as WeatherOverride
from ._worlds import WeatherRange as WeatherRange
from ._worlds import WorldCreate as WorldCreate
from ._worlds import WorldUpdate as WorldUpdate

