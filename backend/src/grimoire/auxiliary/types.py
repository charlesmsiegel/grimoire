"""Auxiliary-task data model.

`TaskKind` enumerates the seven supported auxiliary calls; `AuxiliaryTask`
is the per-call request payload; `AuxiliaryResult` is the in-memory record
the orchestrator returns and parks in `_inflight_aux` until accept/discard;
`CommitAction` tells the accept dispatch how to commit the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TaskKind(StrEnum):
    IMPERSONATE_PC = "impersonate_pc"
    REWRITE_POST = "rewrite_post"
    CONTINUE_AS = "continue_as"
    WHAT_WOULD_X_SAY = "what_would_x_say"
    BRAINSTORM = "brainstorm"
    EDIT_PROSE = "edit_prose"
    TRANSLATE = "translate"


class CommitAction(StrEnum):
    SUBMIT_POST = "submit_post"
    REPLACE_POST = "replace_post"
    APPEND_POST = "append_post"
    EXTEND_POST = "extend_post"
    COPY = "copy"
    REPLACE_DRAFT = "replace_draft"


_COMMIT_ACTION: dict[TaskKind, CommitAction] = {
    TaskKind.IMPERSONATE_PC: CommitAction.SUBMIT_POST,
    TaskKind.REWRITE_POST: CommitAction.REPLACE_POST,
    # Continue-as extends the body of the post it was triggered from,
    # rather than appending a fresh NPC post — the new text reads as a
    # continuation of the same beat.
    TaskKind.CONTINUE_AS: CommitAction.EXTEND_POST,
    TaskKind.WHAT_WOULD_X_SAY: CommitAction.COPY,
    TaskKind.BRAINSTORM: CommitAction.COPY,
    TaskKind.EDIT_PROSE: CommitAction.REPLACE_DRAFT,
    TaskKind.TRANSLATE: CommitAction.REPLACE_DRAFT,
}


def commit_action_for(kind: TaskKind) -> CommitAction:
    return _COMMIT_ACTION[kind]


@dataclass
class AuxiliaryTask:
    kind: TaskKind
    target_character_ref: str | None = None
    target_post_id: str | None = None
    edit_instruction: str | None = None
    snippet: str | None = None
    steering_hint: str | None = None
    target_language: str | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuxiliaryResult:
    id: str
    task: AuxiliaryTask
    text: str
    completed_at: datetime
    model_used: str
    tokens: int
    pending_commit_action: CommitAction
    warnings: list[str] = field(default_factory=list)


__all__ = [
    "AuxiliaryResult",
    "AuxiliaryTask",
    "CommitAction",
    "TaskKind",
    "commit_action_for",
]
