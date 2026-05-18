"""Time Engine — per-campaign in-game clock + advancement coordinator.

See :mod:`grimoire.time_engine.service` for the public ``TimeEngineService``
and :mod:`grimoire.time_engine.config` for the configuration dataclasses.
"""

from grimoire.time_engine.config import (
    SignificanceConfig,
    TimeEngineConfig,
    TimePrecision,
)
from grimoire.time_engine.errors import (
    CheckpointTokenError,
    InvalidSkipError,
    TimeEngineError,
    TimeNotSetError,
)
from grimoire.time_engine.service import TimeEngineService
from grimoire.time_engine.subscriber import (
    TimeEngineSubscriber,
    extract_time_advances_from_deltas,
)

__all__ = [
    "CheckpointTokenError",
    "InvalidSkipError",
    "SignificanceConfig",
    "TimeEngineConfig",
    "TimeEngineError",
    "TimeEngineService",
    "TimeEngineSubscriber",
    "TimeNotSetError",
    "TimePrecision",
    "extract_time_advances_from_deltas",
]
