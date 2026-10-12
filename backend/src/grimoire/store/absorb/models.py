"""Plain extraction models: the prompt and provider share one wire contract.

Raw parsing still owns tolerant input and null-versus-clear semantics; instantiating
these defaults before that boundary would erase whether the model spoke at all.
"""

from typing import Literal

from pydantic import BaseModel


class Citation(BaseModel):
    quote: str | None = None
    speaker: str | None = None
    certainty: float | None = None


class TimelineEvent(BaseModel):
    date: str = ""
    text: str = ""


class Fact(Citation):
    text: str = ""
    date: str | None = None
    supersedes: str | None = None


class CharacterState(Citation):
    id: str
    current_state: str | None = None
    knows: str | None = None
    suspects: str | None = None


class GroupState(Citation):
    id: str
    goals: str | None = None
    resources: str | None = None
    focus: str | None = None
    public_perception: str | None = None
    secrets: str | None = None


class MembershipChange(Citation):
    group: str
    actor: str
    change: Literal["join", "leave"]


class LoreEdit(Citation):
    id: str
    append: str


class AuthoredEdit(Citation):
    id: str
    field: str
    text: str


class RelationshipDelta(Citation):
    from_: str
    to: str
    trust: int
    affection: int
    tension: int
    note: str | None = None


class BondChange(Citation):
    a: str
    b: str
    type: str


class IdentityMovement(Citation):
    id: str | None = None
    title: str | None = None
    beat: str
    why_new: str | None = None
    distinguished_from: list[str] | None = None


class PlotMovement(IdentityMovement):
    status: Literal["open", "advanced", "closed"]


class CommitmentMovement(IdentityMovement):
    kind: Literal["promise", "threat", "foreshadowing"] | None = None
    status: Literal["open", "fulfilled", "broken", "expired"] | None = None
    due: str | None = None


class NewCharacter(Citation):
    name: str
    description: str
    history: str
    personality: str
    mes_example: str
    evidence: str
    confidence: Literal["thin", "sketched", "established"]
    open_questions: str | list[str]
    sd_prompt: str


class NewLocation(Citation):
    name: str
    body: str
    keys: str
    sd_prompt: str
    current_setting: bool


class NewLore(Citation):
    name: str
    body: str
    keys: str
    kind: Literal["lore", "items", "groups", "creatures"]


class WeatherEdit(Citation):
    location: str | None = None
    condition: str | None = None
    temperature: str | None = None
    wind: str | None = None
    duration_blocks: int | None = None
    note: str | None = None


class Extraction(BaseModel):
    one_line: str = ""
    summary: str = ""
    keywords: list[str] = []
    timeline_events: list[TimelineEvent] = []
    facts: list[Fact] = []
    character_state_edits: list[CharacterState] = []
    group_state_edits: list[GroupState] = []
    membership_changes: list[MembershipChange] = []
    lore_edits: list[LoreEdit] = []
    authored_edits: list[AuthoredEdit] = []
    relationship_deltas: list[RelationshipDelta] = []
    bond_changes: list[BondChange] = []
    plot_movements: list[PlotMovement] = []
    commitment_movements: list[CommitmentMovement] = []
    new_characters: list[NewCharacter] = []
    new_locations: list[NewLocation] = []
    new_lore: list[NewLore] = []
    weather_edits: list[WeatherEdit] = []
