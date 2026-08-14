"""Request bodies for the route modules.

Plain ``BaseModel`` subclasses only — no ``Field``, validators or
``ConfigDict`` — so the Android build can pin pydantic 1.x
(docs/android-architecture.md §7). Dump them with ``common._dump``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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
    llm_call_budget: str | None = None
    context_budget: str | None = None
    archive_depth: str | None = None
    setup_done: str | None = None
    prompt_log_depth: str | None = None
    turnstate_depth: str | None = None
    promote_streak: str | None = None
    rolling_summary_every: str | None = None
    embeddings_connection_id: str | None = None
    embeddings_model: str | None = None
    semantic_recall_depth: str | None = None
    semantic_recall_threshold: str | None = None


class ConnectionCreate(BaseModel):
    kind: Literal["openrouter", "claude", "openai_compatible"]
    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    post_process: Literal["none", "strict"] = "none"


class ConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    post_process: Literal["none", "strict"] | None = None


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
    guidance: str | None = None
    response: ResponseSettings | None = None


class RetryBody(BaseModel):
    response: ResponseSettings | None = None


class NameBody(BaseModel):
    name: str


class RollBody(BaseModel):
    notation: str
    label: str | None = None


class ProposalAction(BaseModel):
    proposal: str
    action: str
    check: str | None = None
    actor: str | None = None
    difficulty: int | None = None
    modifier: int | None = None


class CheckBody(BaseModel):
    check: str
    actor: str
    difficulty: int | None = None
    modifier: int | None = None


class NewCampaign(BaseModel):
    name: str
    world: str
    region: str | None = None
    calendar: str | None = None
    module: str | None = None
    climate: str | None = None


class WeatherOverride(BaseModel):
    """One override span, or a clear of the range it names.

    `start`/`end` rather than the spec's `from`/`to`: `from` is a Python
    keyword, and reaching it would need `Field(alias=...)`, which this codebase
    forbids to stay pydantic v1/v2-agnostic for Chaquopy (CLAUDE.md). `end` is
    None for an open-ended span — the storage behind the HUD's "until I clear
    it" duration.
    """
    location: str = "_default"
    start: str
    end: str | None = None
    condition: str | None = None
    temperature: str | None = None
    wind: str | None = None
    note: str | None = None
    suppress: list[str] | None = None
    clear: bool = False
    # Which moment a block count is measured from, when it is not the start.
    blocks_from: str | None = None
    # A block count instead of an `end`. The duration control offers "this
    # block" and "the rest of today"; turning those into native strings
    # client-side means reimplementing the calendar's month lengths.
    blocks: int | None = None


class CampaignClimate(BaseModel):
    default_climate: str


class WeatherRange(BaseModel):
    """A location, a span, and the axes to act on — for clear and resume."""
    location: str = "_default"
    start: str
    end: str | None = None
    axes: list[str] | None = None
    blocks: int | None = None


class ModuleCreate(BaseModel):
    name: str


class ModuleManifestBody(BaseModel):
    name: str = ""
    description: str = ""
    version: str = ""
    dice: str = ""
    notes: str = ""
    dry_run: bool = False


class ModuleGroupBody(BaseModel):
    group: dict = {}
    dry_run: bool = False


class ModuleSheetTypeBody(BaseModel):
    sheet_type: dict = {}
    dry_run: bool = False


class ModuleCheckBody(BaseModel):
    check: dict = {}
    dry_run: bool = False


class ModuleDefaultsBody(BaseModel):
    defaults: dict = {}
    dry_run: bool = False


class ModuleRuleBody(BaseModel):
    flags: dict = {}
    body: str = ""
    dry_run: bool = False


class ModuleContentBody(BaseModel):
    name: str = ""
    body: str = ""
    keys: str = ""
    fields: dict = {}
    sheet: dict | None = None
    dry_run: bool = False


class ModuleLayoutBody(BaseModel):
    layout: dict = {}
    dry_run: bool = False


class ModuleThemeBody(BaseModel):
    theme: dict = {}
    dry_run: bool = False


class ModuleRenameBody(BaseModel):
    kind: str = ""
    address: dict = {}
    to: str = ""
    dry_run: bool = False


class ModuleSetting(BaseModel):
    module: str = ""


class SheetBody(BaseModel):
    sheet_type: str
    fields: dict | None = None
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetCreationBody(BaseModel):
    sheet_type: str
    spends: dict[str, dict[str, int]] = {}
    expected: dict | None = None  # omitted == null == "assert no sheet exists"


class SheetAdvanceBody(BaseModel):
    field: str


class PickBody(BaseModel):
    version: str


class MarkBody(BaseModel):
    status: str  # "completed" | "skipped" | "none" — validated in the store


class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""
    fields: dict | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None
    fields: dict | None = None


class CharacterCreate(BaseModel):
    name: str
    version_name: str = "default"
    card: dict | None = None


class VersionCreate(BaseModel):
    name: str
    card: dict


class VersionUpdate(BaseModel):
    card: dict


class DefaultVersion(BaseModel):
    default_version: str


class CharacterBirthdate(BaseModel):
    birthdate: str = ""


class ChubImportBody(BaseModel):
    url: str
    into: str | None = None
    into_version: str | None = None


class ChubSourceBody(BaseModel):
    url: str


class TaglineSave(BaseModel):
    tagline: str = ""


class VoiceAnchorSave(BaseModel):
    # No default, deliberately: a blank anchor DELETES, so `{}` from an
    # incomplete or mismatched client would be a destructive request nobody
    # made. An explicit {"voice_anchor": ""} is still the supported opt-out.
    voice_anchor: str


class GroupStateSave(BaseModel):
    goals: str = ""
    resources: str = ""
    focus: str = ""
    public_perception: str = ""
    secrets: str = ""


class AvatarFocus(BaseModel):
    focus: int


class PCCreate(BaseModel):
    name: str
    tags: list[str] = []
    version_name: str = "default"
    persona: dict | None = None


class PCUpdate(BaseModel):
    default_version: str | None = None
    tags: list[str] | None = None


class PersonaVersionCreate(BaseModel):
    name: str
    persona: dict


class PersonaVersionUpdate(BaseModel):
    persona: dict


class Ref(BaseModel):
    kind: str
    id: str


class RefList(BaseModel):
    refs: list[Ref]


class NewScene(BaseModel):
    title: str | None = None
    suggested_date: str | None = None
    pcless: bool = False


class RenameScene(BaseModel):
    title: str


class ChronicleSave(BaseModel):
    one_line: str = ""
    summary: str = ""
    keywords: list[str] = []
    timeline_events: list[dict] = []
    edits: list[dict] = []
    # Idempotency key minted by POST /absorb (#235). Optional: a body without
    # one simply opts out of the replay guard.
    commit_token: str = ""


class ChatTurn(BaseModel):
    content: str = ""
    response: ResponseSettings | None = None


class Appear(BaseModel):
    kind: str = "characters"
    id: str
    version: str | None = None
    role: str | None = None


class SceneLocation(BaseModel):
    location: str


class SceneDatetime(BaseModel):
    datetime: str


class CalendarConfig(BaseModel):
    primary: dict
    secondary: dict | None = None
    confirmed: bool = False


class EditMessage(BaseModel):
    content: str


class Dismiss(BaseModel):
    character: str


class GreetingCreate(BaseModel):
    name: str
    character: str
    version: str
    body: str = ""
    requires_tags: list[str] = []
    predecessor_join: str = "all"
    present: list[str] | None = None
    pcless: bool = False


class SubjectsBody(BaseModel):
    subjects: list[str] = []


class CopyFromGreeting(BaseModel):
    gid: str
    name: str
    slot: str


class GreetingUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    requires_tags: list[str] | None = None
    predecessor_join: str | None = None
    present: list[str] | None = None
    pcless: bool | None = None


class Edges(BaseModel):
    leads_to: list[str] | None = None
    excludes: list[str] | None = None


class ImportGreetings(BaseModel):
    character: str
    version: str


class StartFromGreeting(BaseModel):
    greeting: str


class Opener(BaseModel):
    prompt: str


class SceneIntent(BaseModel):
    text: str
    offscreen: bool = False


class SceneIdeaCreate(BaseModel):
    title: str = ""
    premise: str = ""
    cast: list[str] = []
    location: str = ""
    date: str = ""
    pcless: bool = False
    # "greeting" is deliberately absent: those entries are composed from
    # played.json, never stored (see store/scene_ideas.py).
    source: Literal["llm", "user"] = "user"


class SceneIdeaStatus(BaseModel):
    status: Literal["active", "used", "dismissed"]
    #: only meaningful with status "used" -- the scene the idea became
    scene: str = ""


class FirstPost(BaseModel):
    text: str


class LoreEntry(BaseModel):
    name: str
    keys: list[str] = []
    body: str = ""
    category: str = "lore"


class LorebookCommit(BaseModel):
    entries: list[LoreEntry]


class AppearBatch(BaseModel):
    refs: list[Appear]
