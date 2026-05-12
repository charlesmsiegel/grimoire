"""Time Engine — per-campaign in-game clock + advancement coordinator.

See :mod:`grimoire.time_engine.service` for the public ``TimeEngineService``
and :mod:`grimoire.time_engine.config` for the configuration dataclasses.
"""

from grimoire.time_engine.config import SignificanceConfig, TimeEngineConfig
from grimoire.time_engine.errors import (
    InvalidSkipError,
    TimeEngineError,
    TimeNotSetError,
)
from grimoire.time_engine.service import TimeEngineService

__all__ = [
    "InvalidSkipError",
    "SignificanceConfig",
    "TimeEngineConfig",
    "TimeEngineError",
    "TimeEngineService",
    "TimeNotSetError",
]
