"""Scene Manager types: scenes, posts, multi-PC advance trigger."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    InGameTime,
    Json,
    PostId,
    SceneId,
    TurnId,
)


class AuthorKind(StrEnum):
    PC = "pc"
    NARRATOR = "narrator"
    NPC = "npc"
    SYSTEM = "system"


class Post(BaseModel):
    id: PostId
    scene_id: SceneId
    order_in_scene: int
    author_kind: AuthorKind
    body: str
    is_player: bool
    created_at: datetime
    turn_id: TurnId
    author_pc_ref: CharacterRef | None = None
    author_npc_ref: CharacterRef | None = None
    body_hash: str = ""
    retconned_from: PostId | None = None


class Thread(BaseModel):
    text: str
    introduced_in_post: PostId | None = None
    paid_off_in_post: PostId | None = None
    tags: list[str] = Field(default_factory=list)


class SceneThreads(BaseModel):
    introduced: list[Thread] = Field(default_factory=list)
    paid_off: list[Thread] = Field(default_factory=list)


class Scene(BaseModel):
    id: SceneId
    campaign_id: CampaignId
    branch_id: BranchId
    ordinal: int
    slug: str
    file_path: str
    title: str = ""
    location_ref: str | None = None
    in_game_start: InGameTime | None = None
    in_game_end: InGameTime | None = None
    greeting_id: str | None = None
    pov_character_ref: CharacterRef | None = None
    present_character_refs: list[CharacterRef] = Field(default_factory=list)
    present_pc_refs: list[CharacterRef] = Field(default_factory=list)
    mood: str = ""
    post_count: int = 0
    threads_introduced: list[Thread] = Field(default_factory=list)
    threads_paid_off: list[Thread] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    closed: bool = False
    closed_at_turn: TurnId | None = None
    last_advance_at_post: int | None = None
    running_summary: str = ""
    summary: str = ""
    key_beats: list[str] = Field(default_factory=list)
    emotional_arc: str = ""


class SceneFile(BaseModel):
    """In-memory representation of a scene's markdown + sidecar pair."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    scene: Scene
    body: str  # raw markdown
    sidecar_path: Path
    markdown_path: Path


class SceneInit(BaseModel):
    """Inputs for opening a new scene."""

    campaign_id: CampaignId
    branch_id: BranchId
    title: str = ""
    slug: str = ""
    location_ref: str | None = None
    in_game_start: InGameTime | None = None
    present_character_refs: list[CharacterRef] = Field(default_factory=list)
    present_pc_refs: list[CharacterRef] = Field(default_factory=list)
    pov_character_ref: CharacterRef | None = None
    greeting_id: str | None = None
    mood: str = ""
    tags: list[str] = Field(default_factory=list)


class SceneBreakDecision(BaseModel):
    is_break: bool
    confidence: float
    # 'time_gap' | 'location_change' | 'cast_change' | 'tonal_shift' | 'explicit' | 'user_signal'
    reason: str
    proposed_new_scene: SceneInit | None = None


class AdvanceDecision(BaseModel):
    auto_respond: bool
    reason: str  # 'single_pc_scene', 'multi_pc_pending_advance'


class AdvanceResult(BaseModel):
    scene: Scene
    pending_posts: list[Post] = Field(default_factory=list)
    turn_id: TurnId | None = None
    note: str = ""


class SceneCloseReport(BaseModel):
    scene_id: SceneId
    summary: str
    key_beats: list[str] = Field(default_factory=list)
    threads_resolved: list[Thread] = Field(default_factory=list)
    threads_unresolved: list[Thread] = Field(default_factory=list)
    in_game_end: InGameTime | None = None


class SceneContext(BaseModel):
    """Lightweight view passed to mechanics + extractor."""

    scene: Scene
    recent_posts: list[Post] = Field(default_factory=list)
    present_characters: list[CharacterRef] = Field(default_factory=list)
    present_pcs: list[CharacterRef] = Field(default_factory=list)
    location_ref: str | None = None
    in_game_time: InGameTime | None = None
    extras: Json = Field(default_factory=dict)
