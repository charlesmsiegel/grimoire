"""Request models: extras domain."""


from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ...store import membership

# ---- models ----

class SamplerPresetBody(BaseModel):
    """A sampler preset as written. `params` is a bare `dict`, checked by hand
    in `llm_sampling.validate`: pydantic 1.10 coerces 40.7 into an `int` field
    where v2 refuses it, and the two builds must agree about what a preset is."""

    name: str
    params: dict | None = None
    notes: str = ""


class SamplerImportBody(BaseModel):
    """A SillyTavern preset file, read by the browser and sent as JSON -- which
    keeps the route off multipart. `data` is whatever the file parsed to; the
    handler refuses anything but an object."""

    name: str = ""
    data: Any = None
    include_max_tokens: bool = False


class AuthorsNote(BaseModel):
    """An author's note (play controls V). Ranges are checked by the route, so
    a bad field answers a 400 naming it rather than a 422 from the model."""
    text: str = ""
    depth: int = 4
    every: int = 1


class ImageMigrationStart(BaseModel):
    """`POST /maintenance/images/migrate`. A dry run unless it says otherwise."""
    dry_run: bool = True


class ImageGcStart(BaseModel):
    """`POST /maintenance/images/gc`. A dry run (the scan) unless it says
    otherwise; a real run (the collection) carries the token a scan issued."""
    dry_run: bool = True
    token: str | None = None


# Membership is read-only: share the store's roster and affiliation models,
# rather than maintaining a second serializer that can drop roles or secrecy.
class MembershipResponse(BaseModel):
    digest: str
    groups: tuple[membership.Roster, ...]
    by_actor: dict[str, tuple[membership.Affiliation, ...]]


class ActorGroup(BaseModel):
    ref: str
    name: str
    secrecy: str
    roles: tuple[str, ...]


class ActorMembershipResponse(BaseModel):
    actor: str
    groups: tuple[ActorGroup, ...]


class HistoryQuestion(BaseModel):
    """A reader's question to a campaign's history (roadmap 12, E3)."""
    question: str = ""
