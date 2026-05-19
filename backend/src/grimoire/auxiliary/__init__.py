"""Auxiliary tasks — non-canonical model calls (drafts, rewrites, brainstorms).

See ``docs/superpowers/specs/2026-05-19-auxiliary-tasks-design.md``.

The defining property of an auxiliary task: no silent state mutation. The
runner produces text into an in-memory :class:`AuxiliaryResult`, the user
accepts or discards, and only on accept does the result flow through the
canonical pipeline.
"""

from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    CommitAction,
    TaskKind,
    commit_action_for,
)

__all__ = [
    "AuxiliaryResult",
    "AuxiliaryTask",
    "CommitAction",
    "TaskKind",
    "commit_action_for",
]
