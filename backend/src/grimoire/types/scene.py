"""Scene Manager types: scenes, posts, multi-PC advance trigger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .common import BranchId, CampaignId, CharacterRef, Json, PostId, SceneId, TurnId
from .time import InGameTime


class AuthorKind(StrEnum):
    PC = "pc"
    NARRATOR = "narrator"
    NPC = "npc"
    SYSTEM = "system"


@dataclass
class Post:
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


@dataclass
class Thread:
    text: str
    introduced_in_post: PostId | None = None
    paid_off_in_post: PostId | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class SceneThreads:
    introduced: list[Thread] = field(default_factory=list)
    paid_off: list[Thread] = field(default_factory=list)


@dataclass
class Scene:
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
    present_character_refs: list[CharacterRef] = field(default_factory=list)
    present_pc_refs: list[CharacterRef] = field(default_factory=list)
    mood: str = ""
    post_count: int = 0
    threads_introduced: list[Thread] = field(default_factory=list)
    threads_paid_off: list[Thread] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    closed: bool = False
    closed_at_turn: TurnId | None = None
    last_advance_at_post: int | None = None
    running_summary: str = ""
    summary: str = ""
    key_beats: list[str] = field(default_factory=list)
    emotional_arc: str = ""


@dataclass
class SceneFile:
    """In-memory representation of a scene's markdown + sidecar pair."""

    scene: Scene
    body: str  # raw markdown
    sidecar_path: Path
    markdown_path: Path


@dataclass
class SceneInit:
    """Inputs for opening a new scene."""

    campaign_id: CampaignId
    branch_id: BranchId
    title: str = ""
    slug: str = ""
    location_ref: str | None = None
    in_game_start: InGameTime | None = None
    present_character_refs: list[CharacterRef] = field(default_factory=list)
    present_pc_refs: list[CharacterRef] = field(default_factory=list)
    pov_character_ref: CharacterRef | None = None
    greeting_id: str | None = None
    mood: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SceneBreakDecision:
    is_break: bool
    confidence: float
    # 'time_gap' | 'location_change' | 'cast_change' | 'tonal_shift' | 'explicit' | 'user_signal'
    reason: str
    proposed_new_scene: SceneInit | None = None


@dataclass
class AdvanceDecision:
    auto_respond: bool
    reason: str  # 'single_pc_scene', 'multi_pc_pending_advance'


@dataclass
class AdvanceResult:
    scene: Scene
    pending_posts: list[Post] = field(default_factory=list)
    turn_id: TurnId | None = None
    note: str = ""


@dataclass
class SceneCloseReport:
    scene_id: SceneId
    summary: str
    key_beats: list[str] = field(default_factory=list)
    threads_resolved: list[Thread] = field(default_factory=list)
    threads_unresolved: list[Thread] = field(default_factory=list)
    in_game_end: InGameTime | None = None


@dataclass
class SceneContext:
    """Lightweight view passed to mechanics + extractor."""

    scene: Scene
    recent_posts: list[Post] = field(default_factory=list)
    present_characters: list[CharacterRef] = field(default_factory=list)
    present_pcs: list[CharacterRef] = field(default_factory=list)
    location_ref: str | None = None
    in_game_time: InGameTime | None = None
    extras: Json = field(default_factory=dict)
