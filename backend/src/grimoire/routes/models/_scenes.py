"""Request models: scenes domain."""


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, PositiveInt, StrictStr

from ...store import membership


# ---- models ----

from ._settings import ResponseSettings

class PromptText(BaseModel):
    text: StrictStr


class NewScene(BaseModel):
    title: str | None = None
    suggested_date: str | None = None
    pcless: bool = False
    focus: StrictStr = ""


class RenameScene(BaseModel):
    title: str


class BranchScene(BaseModel):
    """Branch a scene from a post: the sibling keeps `messages[: through + 1]`.
    An empty `title` takes "<source title> (branch)"."""
    through: int
    title: str = ""


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
    speaker_ref: str | None = None
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
    #: The campaign's write token as the caller priced this move against it
    #: (#409, `store/revision.py`). Empty is "no expectation", which is what
    #: every caller had before it existed; a token that no longer matches earns a
    #: 409 the client re-prices and re-asks against, because an advance resolves
    #: its starting moment from the live clock and a move priced against one
    #: state is not the move it shows when confirmed against another.
    #:
    #: Ignored by `/advance/preview`, which writes nothing — the preview is where
    #: a caller GETS a token, not where it spends one.
    expect_revision: str = ""


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
    #: How far ahead an imminent event is warned about (#106). `None`, not 0,
    #: is "no opinion": unlike a staleness threshold, zero is a setting somebody
    #: can mean here -- it turns the warnings off -- so a client that predates
    #: the field must be distinguishable from one that switched them off.
    warn_days: int | None = None


class NoticeMark(BaseModel):
    """Acknowledge pre-notices, by key (#106).

    A list rather than one key because a banner shows what a day carries and a
    reader dismisses that, not each row of it -- and because two round trips
    would let half an acknowledgement land.

    `scene` is where it happened, recorded for the reader's benefit alone;
    once-ness is campaign-wide, so nothing reads it back to decide anything.
    """
    keys: list[str] = []
    scene: str = ""


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
    #: Restore original: `content` is the original a stored rewrite recorded,
    #: written back as it is -- no macro expansion, no store phase -- and the
    #: record is cleared.
    restore: bool = False


class ExcludeMessage(BaseModel):
    excluded: bool


class ReplayStart(BaseModel):
    #: The first post to replay -- the one AFTER the retconned post.
    index: int
    #: Replay inside a branch of the scene (play controls III, #151): the whole
    #: transcript is branched and the replay runs in the sibling, so the
    #: original is never touched. Off by default, so existing callers keep the
    #: in-place replay; the client turns it on for an unabsorbed scene.
    branch: bool = False


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
    phase: str = ""
    sequence: PositiveInt | None = None
    optional: bool = False


class SubjectsBody(BaseModel):
    subjects: list[str] = []


class GreetingSubjectsBody(SubjectsBody):
    image: str


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
    # re-point the greeting at a different character/version, keeping its id
    # and plot-map edges (#17); "" clears the character (narrator-only)
    character: str | None = None
    version: str | None = None
    phase: str | None = None
    sequence: PositiveInt | None = None
    optional: bool | None = None
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
    # False opens the scene from the greeting WITHOUT posting its body (#91):
    # cast seated, scene stamped and renamed, transcript left empty for the
    # opener to adapt the greeting into (`Opener.adapt`).
    seed: bool = True


class Opener(BaseModel):
    prompt: str = ""
    completed: list[dict] = []
    snapshot: list[dict] = []
    # Rewrite the greeting this scene was started from (its `greeting` stamp)
    # for where the campaign stands now, instead of generating from `prompt`,
    # which is then ignored (#91). The body is read server-side so the model is
    # handed the greeting as authored, not a copy the client may have edited.
    adapt: bool = False


class SceneIntent(BaseModel):
    text: str
    offscreen: bool = False


class SceneSuggestionsRequest(BaseModel):
    """`POST /scene-suggestions`'s body (capstone spec §16.3). The first four
    fields are the query parameters the route took before it had a body; the
    rest are the Story Pressure controls `store.suggest.resolve_controls`
    validates. `Literal`s, so an unknown mode or relation is a 422."""
    after: str | None = None
    offscreen: bool = False
    direction: str = ""
    rank: bool = True
    focus_refs: list[str] = []
    avoid_refs: list[str] = []
    must_refs: list[str] = []
    time_mode: Literal["auto", "near", "move", "anchor"] = "auto"
    time_anchor_ref: str = ""
    time_anchor_relation: Literal["", "before", "on", "after", "by"] = ""


class SceneIdeaCreate(BaseModel):
    title: str = ""
    premise: str = ""
    focus: StrictStr = ""
    cast: list[str] = []
    location: str = ""
    date: str = ""
    pcless: bool = False
    # "greeting" is deliberately absent: those entries are composed from
    # played.json, never stored (see store/scene_ideas.py).
    source: Literal["llm", "user"] = "user"
    #: A generated card's validated provenance (spec §17): `[{ref, action}]`
    #: and `{ref, relation}`. Plain fields, re-validated on the way in by
    #: `suggest.idea_provenance`, which never raises for a malformed shape.
    drivers: list[dict] = []
    time_anchor: dict | None = None


class SceneIdeaStatus(BaseModel):
    status: Literal["active", "used", "dismissed"]
    #: only meaningful with status "used" -- the scene the idea became
    scene: str = ""


class FirstPost(BaseModel):
    text: str = ""
    contributions: list[dict] = []
    snapshot: list[dict] = []


class LoreEntry(BaseModel):
    name: str
    keys: list[str] = []
    body: str = ""
    category: str = "lore"
    # The advanced ST activation fields `parse` stashed (#20) -- carried through
    # the review table untouched and committed as `st_extensions` frontmatter.
    extensions: dict = {}


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
    # The hidden-from-context stamp the draft carried ("" when included), so a
    # reviewed import keeps a hidden post out of context.
    excluded: str = ""


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


