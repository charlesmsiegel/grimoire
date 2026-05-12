"""Shared types for plugin conformance suites."""

from __future__ import annotations

import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ConformanceReport:
    """Outcome of running one plugin through its conformance suite."""

    kind: str
    target_id: str
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        return (
            f"{self.kind}:{self.target_id} — "
            f"{len(self.passed)} passed, {len(self.failed)} failed, "
            f"{len(self.skipped)} skipped in {self.duration_ms}ms"
        )


class ConformanceSuite(Protocol):
    """Contract for a per-kind suite."""

    kind: str

    async def run(self, adapter: Any) -> ConformanceReport: ...


async def run_check(
    report: ConformanceReport,
    name: str,
    fn: Callable[[], Awaitable[None]],
) -> None:
    """Run a single check, recording the outcome on ``report``."""
    try:
        await fn()
    except _Skip as skip:
        report.skipped.append((name, skip.reason))
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"
        report.failed.append((name, message))
    else:
        report.passed.append(name)


class _Skip(Exception):
    """Raised by a check that determines it isn't applicable to this adapter."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def skip(reason: str) -> _Skip:
    return _Skip(reason)


def started() -> float:
    return time.monotonic()


def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


__all__ = [
    "ConformanceReport",
    "ConformanceSuite",
    "elapsed_ms",
    "run_check",
    "skip",
    "started",
]
