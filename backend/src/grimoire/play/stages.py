"""Where the runner gets a chat's 041 stage, and the rule that keeps only the latest one (035 / 041).

Nothing persists a stage yet: that is 041 milestone 2's store. Until then the app owns its stages and hands
the runner a ``StageSource``, any object whose ``latest(chat)`` gives the chat's latest ``Stage`` or ``None``
for a chat with no stage. ``StageBook`` is the smallest such source: one process's latest stage of each chat,
in memory, lost when the process ends. See docs/SCENES.md#the-stage-041 and docs/STAGING.md.
"""

from __future__ import annotations

from typing import Protocol

from grimoire.core.identity import ChatRef
from grimoire.staging import Stage


class StageSource(Protocol):
    """The app's stages: ``latest(chat)`` is the chat's latest ``Stage``, or ``None`` when it has none."""

    def latest(self, chat: ChatRef) -> Stage | None: ...


def succession(previous: Stage | None, found: Stage) -> str | None:
    """Why ``found`` cannot follow ``previous``, the latest stage already seen for its chat, or ``None``.

    A stage has no lineage, so only its sequence orders it: one older than the latest seen is ``stale_stage``
    (it cannot know of a change made since), and another stage at the same sequence is ``stage_diverged`` (two
    stagings of one moment, which one change per sequence never makes). The same facts again, or at a later
    sequence (``Stage.at``), succeed.
    """
    if previous is None:
        return None
    if found.as_of < previous.as_of:
        return "stale_stage"
    if found.as_of == previous.as_of and found != previous:
        return "stage_diverged"
    return None


def stage_key(chat: ChatRef) -> tuple[str, str]:
    return (chat.campaign_id, chat.chat_id)


class StageBook:
    """One process's latest stage of each chat, in memory: a ``StageSource`` until 041 milestone 2 stores them.

    ``put(chat, stage)`` keeps ``stage`` as the chat's latest. It is refused (``ValueError`` with the reason as
    its message, nothing kept) for a value that is not a ``Stage``, or one of another campaign or scene
    (``scene_mismatch``: a chat's stage has the chat id as its scene id), and by ``succession``'s rule.
    """

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], Stage] = {}

    def put(self, chat: ChatRef, stage: Stage) -> None:
        if type(chat) is not ChatRef or type(stage) is not Stage:
            raise ValueError("invalid_field")
        if (stage.campaign_id, stage.scene_id) != stage_key(chat):
            raise ValueError("scene_mismatch")
        reason = succession(self._latest.get(stage_key(chat)), stage)
        if reason is not None:
            raise ValueError(reason)
        self._latest[stage_key(chat)] = stage

    def latest(self, chat: ChatRef) -> Stage | None:
        return self._latest.get(stage_key(chat))


__all__ = ["StageBook", "StageSource", "stage_key", "succession"]
