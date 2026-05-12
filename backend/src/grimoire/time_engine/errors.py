"""Error types raised by the Time Engine."""

from __future__ import annotations


class TimeEngineError(Exception):
    """Base class for Time Engine errors."""


class TimeNotSetError(TimeEngineError):
    """The campaign has no recorded in-game time yet.

    ``advance`` and ``skip_to`` require an anchor; either the caller passes
    ``from_time`` explicitly, or the campaign's calendar row carries a value.
    Hitting this means neither was true.
    """


class InvalidSkipError(TimeEngineError):
    """``skip_to`` was given a target that's not strictly in the future."""


__all__ = ["InvalidSkipError", "TimeEngineError", "TimeNotSetError"]
