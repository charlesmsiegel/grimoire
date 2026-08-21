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
    absorb_concurrency: str | None = None
    llm_call_budget: str | None = None
    llm_retries: str | None = None
    fallback_connection_id: str | None = None
    context_budget: str | None = None
    context_scan_depth: str | None = None
    archive_depth: str | None = None
    setup_done: str | None = None
    prompt_log_depth: str | None = None
    turnstate_depth: str | None = None
    promote_streak: str | None = None
    rolling_summary_every: str | None = None
    scene_break_every: str | None = None
    offscene_known_limit: str | None = None
    embeddings_connection_id: str | None = None
    embeddings_model: str | None = None
    semantic_recall_depth: str | None = None
    semantic_recall_threshold: str | None = None
    prompt_layout_enabled: str | None = None
    speaker_turn_taking: str | None = None
    backup_enabled: str | None = None
    backup_interval_hours: str | None = None
    backup_keep: str | None = None
    backup_dir: str | None = None
    replay_fork_threshold: str | None = None
    advance_fork_threshold: str | None = None
    log_level: str | None = None


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

    kind: Literal["openrouter", "claude", "openai_compatible"]
    base_url: str = ""
    api_key: str = ""


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
    """
    guidance: str | None = None
    response: ResponseSettings | None = None
    connection_id: str | None = None
    model: str | None = None


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


class ForkCampaign(BaseModel):
    """A fork's name, and optionally the scene to cut it at (#72).

    `from_scene` names a scene of the SOURCE campaign: it and everything before
    it stay on the fork, everything after it comes off. Absent (or "") forks
    from where the campaign stands, which is the shape with no approximation in
    it -- see `store/fork.py` for what a retrospective cut can and cannot put
    back.
    """
    name: str
    from_scene: str | None = None


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


class SheetBulkBody(BaseModel):
    # Per file kind, the sheet type a bulk create should use. A kind the module
    # has exactly one sheet type for needs no entry; one with several has no
    # default, and is skipped-with-a-reason when it has none here.
    types: dict[str, str] = {}


class PickBody(BaseModel):
    version: str


class MarkBody(BaseModel):
    status: str  # "completed" | "skipped" | "none" — validated in the store


class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""
    secrecy: str = ""      # "" == public; see store.entities.SECRECY_LEVELS
    fields: dict | None = None


class EntityReclassify(BaseModel):
    # The kind to move the record to. `rev` is the same precondition
    # `EntityUpdate` carries and for the same reason (#35): a reclassify moves
    # the record the editor is looking at, and doing that to text somebody else
    # has since rewritten is the write that precondition exists to refuse.
    to: str
    rev: str | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None
    secrecy: str | None = None
    fields: dict | None = None
    # The rev the editor was shown, echoed back so a write cannot silently land
    # on top of an external edit (#35). Optional, and absent means "no
    # precondition": scripts and the store's own callers write records they did
    # not first read, and refusing those would break every one of them to
    # protect a window they never open.
    rev: str | None = None


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


class ImageDescription(BaseModel):
    """One image's description. An empty string is meaningful and is NOT the
    same as never having written one: it means "reviewed, nothing to say", and
    it is what takes an image out of the undescribed queue without offering it
    to the model. Plain field, no `Field(...)`, per the pydantic v1/v2 rule."""

    description: str


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


class PushBody(BaseModel):
    # Set only after the user has been shown the library's competing version:
    # this is the mirror of accepting a pull conflict, and it overwrites.
    force: bool = False


class DemoteBody(BaseModel):
    copy_down: bool = True
    target: str | None = None   # one campaign, rather than every dependent


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
    # #83: send this turn as a director note rather than a player post -- the
    # composer's Direct mode. Ephemerality was previously inferred, from the
    # scene being `pcless` or from the content being blank, which left the
    # feature blank-only in an ordinary scene and unlabelled everywhere. The
    # inference stays (an empty send is still a director beat, and an offscreen
    # scene still has no other kind of turn); this only lets a client say so
    # outright. Defaults off, so a send that does not mention it still posts.
    director: bool = False


class Appear(BaseModel):
    kind: str = "characters"
    id: str
    version: str | None = None
    role: str | None = None


class SceneLocation(BaseModel):
    location: str


class SceneDatetime(BaseModel):
    datetime: str


class AdvanceTime(BaseModel):
    """A campaign-clock advance (#100): skip to `to`, or move on by `days`.

    Both fields are optional here because the store owns the choice between
    them: `to` wins when both arrive, and sending neither is a `ClockError` with
    a sentence of its own. Restating either rule in this model would give the
    two places to disagree. `reason` defaults to empty for the same kind of
    reason — a missing reason earns a 400 that says what is wrong, rather than a
    422 about a field name.
    """
    to: str | None = None
    days: int | None = None
    reason: str = ""


class CalendarConfig(BaseModel):
    primary: dict
    secondary: dict | None = None
    confirmed: bool = False
    #: How long a thread or commitment may go untouched before the ledger calls
    #: it stale (#103). Defaulted rather than required so a client that predates
    #: the field -- or one editing only the calendars -- keeps sending three
    #: fields; the store coerces anything unusable back to its own default, so
    #: this is the shape of the request, not the validation of it.
    stale_after_days: int = 0


class ScheduledEventCreate(BaseModel):
    """A new scheduled event (#101): what happens, and the day it happens on.

    Both fields are required in substance but neither is validated here. An
    empty name earns the id it deserves, and a date this campaign's calendar
    cannot read earns a 400 carrying the calendar's own sentence about it --
    which is more use to the reader than a 422 naming a field.
    """
    name: str = ""
    date: str = ""
    note: str = ""


class ScheduledEventEdit(BaseModel):
    """An edit to one scheduled event. Every field is three-valued.

    None leaves the stored value alone, which is what lets a client send only
    the field it changed -- see `store.events.update`, which owns that rule.
    Firing is deliberately not editable here: the stamp says the clock reached
    this day, and taking it back is `POST .../unfire`, an action with a name.
    """
    name: str | None = None
    date: str | None = None
    note: str | None = None


class EditMessage(BaseModel):
    content: str


class ReplayStart(BaseModel):
    #: The first post to replay -- the one AFTER the retconned post.
    index: int


class ReplayCancel(BaseModel):
    #: Whether to put the unreplayed originals back. Defaults to the
    #: non-destructive answer: a cancel that silently dropped the rest of the
    #: scene would be the one mistake this flow exists to make recoverable.
    restore: bool = True


class Dismiss(BaseModel):
    character: str


class EmergentCast(BaseModel):
    """A name the prose used that no record answers to (#98). `role` follows
    `Appear`'s: a created character seats as an npc unless asked otherwise."""
    name: str
    role: str | None = None


class GreetingCreate(BaseModel):
    name: str
    character: str
    version: str
    body: str = ""
    requires_tags: list[str] = []
    predecessor_join: str = "all"
    present: list[str] | None = None
    pcless: bool = False
    location: str = ""      # a location id, "" for none (#218)


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
    # "" clears the location; None leaves whatever is on disk (#218)
    location: str | None = None
    rev: str | None = None      # see EntityUpdate.rev (#35)


class Edges(BaseModel):
    leads_to: list[str] | None = None
    excludes: list[str] | None = None


class ImportGreetings(BaseModel):
    character: str
    version: str


class StartFromGreeting(BaseModel):
    greeting: str
    # Whether the greeting's own location should seed a scene that has none
    # (#218). False means the caller has already decided this scene's location,
    # including deciding it has none. The confirm pane sends false only when its
    # location picker actually loaded: it pre-fills that picker from the
    # greeting and applies whatever the reader leaves there, so an empty scene
    # means the reader CHOSE none. When the read failed there was no picker to
    # choose with, so it sends true and the greeting's own location is used --
    # an infrastructure fault must not be mistaken for an answer. Callers with
    # no location UI at all (the campaign wizard's opener step) leave the
    # default.
    seed_location: bool = True


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


# ---- scenario-card import (#217) ----
# A proposal speaks in cast NAMES, not ids: the characters it proposes do not
# exist when the reviewer edits it, and `store.scenario.apply` resolves the
# names once they do. That is also what lets a reviewer retype a name and have
# the opener follow it.
class ScenarioCharacter(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    # Set by the parse routes: the import will REUSE a world character of this
    # name rather than create one. Advisory — `scenario.apply` re-resolves
    # against the world as it stands and never reads this back.
    exists: bool = False


class ScenarioGreeting(BaseModel):
    name: str = ""
    body: str = ""
    character: str = ""
    present: list[str] = []


class ScenarioProposal(BaseModel):
    characters: list[ScenarioCharacter] = []
    entries: list[LoreEntry] = []
    greetings: list[ScenarioGreeting] = []
    art: bool = True


class ScenarioUrlBody(BaseModel):
    url: str


class AppearBatch(BaseModel):
    refs: list[Appear]


# ---- importing an existing transcript (#92) ----
# The draft `store.scene_import.parse` returned, after the reviewer edited it.
# `cast` reuses `Appear` because the seats an import asks for are the seats
# `POST .../cast` asks for -- same resolution, same 404s, same role rules.
class ImportedMessage(BaseModel):
    # Literal, not `str`: the serializer looks a role that is neither of these
    # up in `ROLE_TO_LABEL` and raises `KeyError` -- a 500 from inside the
    # transcript write, on a value the boundary can reject as a 422. Same
    # reasoning as `PinRule` below.
    role: Literal["user", "assistant"] = "assistant"
    speaker: str | None = None
    content: str = ""


class SceneImportCommit(BaseModel):
    title: str = ""
    date: str = ""
    location: str = ""
    pcless: bool = False
    messages: list[ImportedMessage] = []
    cast: list[Appear] = []
    # The source's reply boundaries, as `parse` reported them. Rides the draft
    # rather than being shown: there is nothing for a reviewer to decide about
    # it, and only the parse has seen the frontmatter it came from. A client
    # that omits it gets an untracked scene, which is what it was before.
    turn_sizes: list[int] | None = None


class PinRule(BaseModel):
    """One user pin or exclude (#129). `sid` is required for the default
    scene scope; `ttl_posts` counts posts and only a scene rule may carry one
    (see store/pins.py). Literal-typed where the value space is closed, so a
    typo is a 422 rather than a rule that silently matches nothing -- the store
    refuses the same values again, since it is reachable from other callers."""
    ref: str
    mode: Literal["pin", "exclude"]
    scope: Literal["scene", "campaign"] = "scene"
    sid: str = ""
    ttl_posts: int = 0
