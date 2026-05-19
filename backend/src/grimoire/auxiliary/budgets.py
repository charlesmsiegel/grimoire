"""Per-task budget plans for auxiliary tasks.

Each :class:`TaskBudget` says which prompt template to load and which
context slabs to include for a task kind. The Context Builder's
auxiliary path reads `budget_for(kind)` and assembles the prompt from
that spec — bypassing the canonical tier-pack pipeline entirely.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grimoire.auxiliary.types import AuxiliaryTask, TaskKind


@dataclass(frozen=True, slots=True)
class TaskBudget:
    system_prompt_template: str
    include_active_pc_card: bool
    include_scene_header: bool
    voice_target_resolver: Callable[[AuxiliaryTask, Any], list[str]]
    recent_posts_count: int


def _voice_no_targets(_task: AuxiliaryTask, _scene: Any) -> list[str]:
    return []


def _voice_target_npc(task: AuxiliaryTask, _scene: Any) -> list[str]:
    return [task.target_character_ref] if task.target_character_ref else []


def _voice_active_pc_plus_present(task: AuxiliaryTask, scene: Any) -> list[str]:
    refs: list[str] = []
    active_pc = (task.extra_params or {}).get("active_pc_ref")
    if active_pc:
        refs.append(active_pc)
    if scene is not None:
        for ref in getattr(scene, "present_character_refs", None) or []:
            if ref not in refs:
                refs.append(ref)
    return refs


def _voice_original_speakers(task: AuxiliaryTask, scene: Any) -> list[str]:
    """Voice anchors for whoever authored the post being rewritten.

    The scene file tracks ``author_pc_ref`` and ``author_npc_ref`` per
    post; we collect both. If the active PC is also a speaker their
    anchor is included as well.
    """
    refs: list[str] = []
    if scene is None or not task.target_post_id:
        return refs
    posts = getattr(scene, "posts", None) or []
    for post in posts:
        if getattr(post, "id", None) != task.target_post_id:
            continue
        for attr in ("author_pc_ref", "author_npc_ref"):
            ref = getattr(post, attr, None)
            if ref and ref not in refs:
                refs.append(ref)
        break
    active_pc = (task.extra_params or {}).get("active_pc_ref")
    if active_pc and active_pc not in refs:
        refs.append(active_pc)
    return refs


_BUDGETS: dict[TaskKind, TaskBudget] = {
    TaskKind.IMPERSONATE_PC: TaskBudget(
        system_prompt_template="impersonate_pc.j2",
        include_active_pc_card=True,
        include_scene_header=True,
        voice_target_resolver=_voice_active_pc_plus_present,
        recent_posts_count=6,
    ),
    TaskKind.REWRITE_POST: TaskBudget(
        system_prompt_template="rewrite_post.j2",
        include_active_pc_card=True,
        include_scene_header=True,
        voice_target_resolver=_voice_original_speakers,
        recent_posts_count=4,
    ),
    TaskKind.CONTINUE_AS: TaskBudget(
        system_prompt_template="continue_as.j2",
        include_active_pc_card=True,
        include_scene_header=True,
        voice_target_resolver=_voice_target_npc,
        recent_posts_count=3,
    ),
    TaskKind.WHAT_WOULD_X_SAY: TaskBudget(
        system_prompt_template="what_would_x_say.j2",
        include_active_pc_card=False,
        include_scene_header=True,
        voice_target_resolver=_voice_target_npc,
        recent_posts_count=2,
    ),
    TaskKind.BRAINSTORM: TaskBudget(
        system_prompt_template="brainstorm.j2",
        include_active_pc_card=False,
        include_scene_header=False,
        voice_target_resolver=_voice_no_targets,
        recent_posts_count=0,
    ),
    TaskKind.EDIT_PROSE: TaskBudget(
        system_prompt_template="edit_prose.j2",
        include_active_pc_card=False,
        include_scene_header=False,
        voice_target_resolver=_voice_no_targets,
        recent_posts_count=0,
    ),
    TaskKind.TRANSLATE: TaskBudget(
        system_prompt_template="translate.j2",
        include_active_pc_card=False,
        include_scene_header=False,
        voice_target_resolver=_voice_no_targets,
        recent_posts_count=0,
    ),
}


def budget_for(kind: TaskKind) -> TaskBudget:
    return _BUDGETS[kind]


def resolve_voice_targets(task: AuxiliaryTask, scene: Any) -> list[str]:
    return budget_for(task.kind).voice_target_resolver(task, scene)


__all__ = ["TaskBudget", "budget_for", "resolve_voice_targets"]
