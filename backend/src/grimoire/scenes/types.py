from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class AuthorKind(StrEnum):
    PC = "pc"
    NARRATOR = "narrator"
    NPC = "npc"
    SYSTEM = "system"


@dataclass
class Alternate:
    """One sampled version of a post — text plus the delta set it produced.

    See ``docs/superpowers/specs/2026-05-19-swipes-alternates-design.md``.
    Each model-authored post has one or more alternates; exactly one is the
    primary (its ``text`` is what appears in the scene ``.md``). User posts
    carry a single implicit alternate so the schema stays uniform.
    """

    id: str
    post_id: str
    text: str
    delta_set_id: str
    author_kind: AuthorKind
    model: str | None = None
    prompt_hash: str | None = None
    steering_hint: str | None = None
    created_at: datetime | None = None
    tokens: int | None = None
    pinned: bool = False
    is_primary: bool = False
    # Set when this alternate was produced inside a retcon replay batch
    # (see 2026-05-19-retcon-design). Lets the replay UI group alternates by
    # batch and lets cancel-by-batch find the in-flight one.
    replay_batch_id: str | None = None


@dataclass
class Post:
    id: str
    scene_id: str
    order_in_scene: int
    author_kind: AuthorKind
    body: str
    is_player: bool
    created_at: datetime
    turn_id: str
    author_pc_ref: str | None = None
    author_npc_ref: str | None = None
    alternates: list[Alternate] = field(default_factory=list)
    primary_alternate_id: str | None = None

    @property
    def author_label(self) -> str:
        if self.author_kind == AuthorKind.PC and self.author_pc_ref:
            return f"pc:{self.author_pc_ref}"
        if self.author_kind == AuthorKind.NPC and self.author_npc_ref:
            return f"npc:{self.author_npc_ref}"
        return self.author_kind.value


@dataclass
class Thread:
    text: str
    introduced_at_post: int | None = None
    paid_off_at_post: int | None = None


@dataclass
class SceneThreads:
    introduced: list[Thread] = field(default_factory=list)
    paid_off: list[Thread] = field(default_factory=list)


@dataclass
class Scene:
    id: str
    campaign_id: str
    branch_id: str
    ordinal: int
    slug: str
    title: str

    location_ref: str | None = None
    in_game_start: datetime | None = None
    in_game_end: datetime | None = None
    greeting_id: str | None = None

    pov_character_ref: str | None = None
    present_character_refs: list[str] = field(default_factory=list)
    present_pc_refs: list[str] = field(default_factory=list)

    mood: str | None = None

    post_count: int = 0
    threads_introduced: list[Thread] = field(default_factory=list)
    threads_paid_off: list[Thread] = field(default_factory=list)

    tags: list[str] = field(default_factory=list)
    closed: bool = False
    closed_at_turn: str | None = None

    last_advance_at_post: int = 0
    running_summary: str | None = None
    final_summary: str | None = None
    key_beats: list[str] = field(default_factory=list)

    # Per-scene override of the campaign's narrator response mode.
    # ``None`` means "inherit from campaign". See
    # :mod:`grimoire.scenes.narrator_mode` for the allowed values and
    # the resolver that combines this with the campaign default.
    narrator_response_mode: str | None = None


@dataclass
class SceneInit:
    campaign_id: str
    branch_id: str = "main"
    title: str | None = None
    slug: str | None = None
    location_ref: str | None = None
    in_game_start: datetime | None = None
    greeting_id: str | None = None
    pov_character_ref: str | None = None
    present_character_refs: list[str] = field(default_factory=list)
    present_pc_refs: list[str] = field(default_factory=list)
    mood: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class SceneBreakDecision:
    is_break: bool
    confidence: float
    reason: str
    proposed_new_scene: SceneInit | None = None


@dataclass
class AdvanceDecision:
    auto_respond: bool
    reason: str


@dataclass
class AdvanceResult:
    scene: Scene
    pending_posts: list[Post]


@dataclass
class SceneCloseReport:
    scene: Scene
    final_summary: str
    key_beats: list[str]
    threads_resolved: list[Thread]
    threads_unresolved: list[Thread]


@dataclass(frozen=True)
class PostIndexRecord:
    post_id: str
    scene_id: str
    campaign_id: str
    branch_id: str
    author: str
    body: str
    turn_id: str | None = None
    ordinal: int = 0
    is_player: bool = False
    word_count: int = 0
    character_ref: str | None = None
