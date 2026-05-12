"""Scene Manager (spec 10).

The Scene Manager owns the play history: scenes-as-files, posts within them,
the multi-PC advance trigger, running summaries, and thread tracking.
"""

from grimoire.scenes.boundary import BoundaryConfig, detect_scene_break
from grimoire.scenes.events import (
    ADVANCE_DISABLED,
    ADVANCE_ENABLED,
    ADVANCE_REQUESTED,
    PC_POST_APPENDED,
    POST_APPENDED,
    POST_DELETED,
    POST_EDITED,
    RUNNING_SUMMARY_UPDATED,
    SCENE_ENDED,
    SCENE_FILE_CHANGED,
    SCENE_STARTED,
    THREAD_INTRODUCED,
    THREAD_PAID_OFF,
    EventBus,
    InMemoryEventBus,
    SceneEvent,
)
from grimoire.scenes.manager import (
    NothingToAdvance,
    SceneManager,
    SceneManagerConfig,
    new_post,
)
from grimoire.scenes.types import (
    AdvanceDecision,
    AdvanceResult,
    AuthorKind,
    Post,
    Scene,
    SceneBreakDecision,
    SceneCloseReport,
    SceneInit,
    SceneThreads,
    Thread,
)

__all__ = [
    "ADVANCE_DISABLED",
    "ADVANCE_ENABLED",
    "ADVANCE_REQUESTED",
    "PC_POST_APPENDED",
    "POST_APPENDED",
    "POST_DELETED",
    "POST_EDITED",
    "RUNNING_SUMMARY_UPDATED",
    "SCENE_ENDED",
    "SCENE_FILE_CHANGED",
    "SCENE_STARTED",
    "THREAD_INTRODUCED",
    "THREAD_PAID_OFF",
    "AdvanceDecision",
    "AdvanceResult",
    "AuthorKind",
    "BoundaryConfig",
    "EventBus",
    "InMemoryEventBus",
    "NothingToAdvance",
    "Post",
    "Scene",
    "SceneBreakDecision",
    "SceneCloseReport",
    "SceneEvent",
    "SceneInit",
    "SceneManager",
    "SceneManagerConfig",
    "SceneThreads",
    "Thread",
    "detect_scene_break",
    "new_post",
]
