"""Where one bounded tool run stands: its settled turns, clock and trace."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .. import tool_calls, wire
from ._contracts import LoopOptions


@dataclass(frozen=True)
class _Turn:
    """One settled model turn."""
    turn: int
    text: str
    calls: tuple[tool_calls.ToolCall, ...]
    finish: str
    opaque: dict | None
    target: wire.Target | None
    #: Whether the turn streamed visible text (01g-C6).
    shown: bool = False
    #: Whether the turn streamed text the CALLER's reader would see -- its
    #: `visibility` predicate's answer, which a hidden fence does not meet
    #: (roadmap 02 §8.2); `shown` when the caller gave none.
    visible: bool = False


class _RunClock:
    """The run's time (spec 3.9): when it started (`t0`), its wall and the
    per-call ceiling, both pinned as the run starts (`_Loop._pin`). A wall
    `<= 0` is none."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.wall = 0.0
        self.call_budget = 0.0

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def wall_short(self, need: float) -> bool:
        return self.wall > 0 and self.elapsed() + need > self.wall

    def end(self) -> float:
        """When the wall runs out, on the monotonic clock; never with none."""
        return self.t0 + self.wall if self.wall > 0 else float("inf")

    def turn_seconds(self) -> tuple[float | None, bool]:
        """This turn's wait (spec 3.9, joined): `min(llm_call_budget, wall
        left)`, either absent at `<= 0`, and whether the run's wall is the
        binding one."""
        left = self.wall - self.elapsed() if self.wall > 0 else None
        if self.call_budget > 0 and (left is None or self.call_budget <= left):
            return self.call_budget, False
        return left, left is not None


class _RunState:
    """What every part of one run reads and moves (spec 3.7): its task and
    the caller's options, its clock and pinned store root, the turns sent,
    the limit that stops it ("" while none has), whether the finalize turn
    was sent, the text it has so far, its trace, and where its events go --
    `emit`, the consumer's queue once `_Loop.events` runs."""

    def __init__(self, task: str, options: LoopOptions):
        self.task = task
        self.options = options
        self.clock = _RunClock()
        self.root = ""
        self.turns = 0
        self.finalized = False
        self.limit = ""
        self.text = ""
        self.trace: list[tool_calls.TraceEntry] = []
        self.emit: Callable[[object], None] = lambda _item: None

    def hold(self, limit: str) -> None:
        """`limit` as the run's, unless one was already reached first."""
        self.limit = self.limit or limit

    def check_cancelled(self) -> None:
        cancelled = self.options.cancelled
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError("the run was cancelled")

    def add_trace(self, turn: int, kind: Literal["model", "tool", "decide", "stop"],
                  name: str = "", call_id: str = "", *, ok: bool = True,
                  refs: tuple[str, ...] = (), chars: int = 0, note: str = "",
                  started: float | None = None) -> None:
        elapsed = int((time.monotonic() - started) * 1000) if started is not None else 0
        self.trace.append(tool_calls.TraceEntry(turn, kind, name, call_id, ok, tuple(refs),
                                                chars, elapsed, note))
