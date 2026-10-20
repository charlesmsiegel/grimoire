"""Request models: worlds domain."""


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, PositiveInt, StrictStr

from ...store import membership


# ---- models ----

class NameBody(BaseModel):
    name: str


class WorldCreate(BaseModel):
    """`POST /worlds` (#38). Only `name` is required, so the body every client
    sent before the profile existed is still a complete one."""
    name: str
    genre: str = ""
    tone: str = ""
    themes: list[str] = []
    description: str = ""


class WorldUpdate(BaseModel):
    """`PUT /worlds/{wid}`: every field optional, and an absent one is left as
    it is -- which keeps the rename-only `{name}` body meaning what it meant."""
    name: str | None = None
    genre: str | None = None
    tone: str | None = None
    themes: list[str] | None = None
    description: str | None = None


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
    #: Fork AT A POST of `from_scene` (play controls III): the copy keeps that
    #: scene through this index and cuts the rest. Requires `from_scene`.
    from_index: int | None = None
    #: An optional idempotency key (#409). A repeat with the same key is answered
    #: with the fork the first call made instead of copying the campaign again —
    #: because a lost response and a failed write are the same thing to a client,
    #: and a `copytree` is an expensive thing to guess wrong about. Defaulted to
    #: "" rather than required, so a client that predates it keeps working and
    #: simply gets what it has today: no idempotency.
    idempotency_key: str = ""
    #: The source campaign's write token as the caller priced this fork against
    #: (#409). Empty is "no expectation", as everywhere else. Checked under the
    #: source's own lock, immediately before the copy, which is the only place a
    #: check can bind: a client that read the token and then asked for a fork has
    #: a whole request's worth of window in between, and a copy taken across a
    #: write is a checkpoint of a state nobody asked for.
    expect_revision: str = ""


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


