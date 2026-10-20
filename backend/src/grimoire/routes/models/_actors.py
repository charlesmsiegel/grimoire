"""Request models: actors domain."""


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ---- models ----

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


class GroupRefBinding(BaseModel):
    field: Literal["members", "leader"]
    actor: str


class EntityUpdate(BaseModel):
    # A submitted field alone grants no review of retained World actor refs.
    # Defaults are copied per Pydantic instance, across both supported versions.
    group_ref_bindings: list[GroupRefBinding] = []
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


class CharacterTextParse(BaseModel):
    text: str
    filename: str = ""


class CharacterTextRefine(CharacterTextParse):
    card: dict


class VersionCreate(BaseModel):
    name: str
    card: dict


class VersionUpdate(BaseModel):
    card: dict


class DefaultVersion(BaseModel):
    default_version: str


class CharacterBirthdate(BaseModel):
    birthdate: str = ""


class CharacterTags(BaseModel):
    tags: list[str] = []


class ChubImportBody(BaseModel):
    url: str
    into: str | None = None
    into_version: str | None = None
    version_name: str | None = None


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
    expected_v: str | None = None


class ImagePromote(BaseModel):
    expected_v: str | None = None
    expected_primary_v: str | None = None


class RecordArtLink(BaseModel):
    source: dict
    intent: str = "gallery"
    expected_primary_v: str | None = None


class ImageDescription(BaseModel):
    """One image's description. An empty string is meaningful and is NOT the
    same as never having written one: it means "reviewed, nothing to say", and
    it is what takes an image out of the undescribed queue without offering it
    to the model. Plain field, no `Field(...)`, per the pydantic v1/v2 rule."""

    description: str
    expected_v: str | None = None
    expected_description_rev: str | None = None
    cleanup_only: bool = False


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


class SyncPin(BaseModel):
    # One ref plus the direction, because pinning is a toggle with two spellings
    # ("stop offering updates" / "resume") and a body that names the end state
    # is idempotent where a bare toggle would flip on a retried request.
    ref: Ref
    pinned: bool


class PushBody(BaseModel):
    # Set only after the user has been shown the library's competing version:
    # this is the mirror of accepting a pull conflict, and it overwrites.
    force: bool = False


class DemoteBody(BaseModel):
    copy_down: bool = True
    target: str | None = None   # one campaign, rather than every dependent


