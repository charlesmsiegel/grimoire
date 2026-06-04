"""`AutoDisableState`: per-(provider, model, mode) health tracking.

Reads/writes the ``extractor_mode_health`` table populated by the 025
migration. The mode selector consults `together_disabled` /
`tool_use_disabled` before honouring a configured Together/Tool-use
preference; the extractor calls `record_call` on every attempt so the
failure-rate window stays current.
"""

from __future__ import annotations

from typing import Protocol

from grimoire.util import now_iso


class _DBLike(Protocol):
    async def execute(self, sql: str, params: tuple = ...) -> None: ...
    async def fetchone(self, sql: str, params: tuple = ...) -> object | None: ...


class AutoDisableState:
    """Failure-rate tracker for Together / Tool-use modes.

    A `(provider_id, model, mode)` triple is "disabled" when:

    * `disabled_at` is set explicitly, OR
    * `total_calls >= min_samples` AND `failures / total_calls >= threshold`.

    `record_call` increments the counters; once the threshold is crossed
    we also stamp `disabled_at` so subsequent reads short-circuit. User
    re-enable (`re_enable`) zeros the counters and clears `disabled_at`.
    """

    TOGETHER = "together"
    TOOL_USE = "tool_use"

    def __init__(
        self,
        db: _DBLike,
        *,
        together_threshold: float = 0.15,
        tool_use_threshold: float = 0.10,
        min_samples: int = 20,
    ) -> None:
        self._db = db
        self._together_threshold = together_threshold
        self._tool_use_threshold = tool_use_threshold
        self._min_samples = min_samples

    async def together_disabled(self, provider_id: str, model: str) -> bool:
        return await self._disabled(provider_id, model, self.TOGETHER, self._together_threshold)

    async def tool_use_disabled(self, provider_id: str, model: str) -> bool:
        return await self._disabled(provider_id, model, self.TOOL_USE, self._tool_use_threshold)

    async def record_call(
        self,
        provider_id: str,
        model: str,
        mode: str,
        *,
        success: bool,
    ) -> None:
        """Increment the call/failure counters for one extractor invocation."""
        await self._db.execute(
            "INSERT INTO extractor_mode_health "
            "(provider_id, model, mode, window_start, total_calls, failures) "
            "VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(provider_id, model, mode) DO UPDATE SET "
            "  total_calls = total_calls + 1, "
            "  failures = failures + excluded.failures",
            (provider_id, model, mode, now_iso(), 0 if success else 1),
        )
        # If the latest write pushed us past the threshold, stamp disabled_at.
        threshold = self._threshold_for(mode)
        row = await self._db.fetchone(
            "SELECT total_calls, failures, disabled_at "
            "FROM extractor_mode_health WHERE provider_id=? AND model=? AND mode=?",
            (provider_id, model, mode),
        )
        if row is None:
            return
        total = int(row["total_calls"])
        failures = int(row["failures"])
        already_disabled = row["disabled_at"] is not None
        if already_disabled or total < self._min_samples:
            return
        if failures / total >= threshold:
            await self._db.execute(
                "UPDATE extractor_mode_health "
                "SET disabled_at=? WHERE provider_id=? AND model=? AND mode=?",
                (now_iso(), provider_id, model, mode),
            )

    async def re_enable(self, provider_id: str, model: str, mode: str) -> None:
        """User-initiated re-enable. Zeros counters and clears `disabled_at`.

        The `re_enabled_at` stamp lets the diagnostics UI distinguish
        "fresh row" from "recently revived" without needing a separate
        audit log.
        """
        now = now_iso()
        await self._db.execute(
            "UPDATE extractor_mode_health "
            "SET re_enabled_at=?, total_calls=0, failures=0, "
            "    disabled_at=NULL, window_start=? "
            "WHERE provider_id=? AND model=? AND mode=?",
            (now, now, provider_id, model, mode),
        )

    async def _disabled(
        self,
        provider_id: str,
        model: str,
        mode: str,
        threshold: float,
    ) -> bool:
        row = await self._db.fetchone(
            "SELECT total_calls, failures, disabled_at "
            "FROM extractor_mode_health WHERE provider_id=? AND model=? AND mode=?",
            (provider_id, model, mode),
        )
        if row is None:
            return False
        if row["disabled_at"] is not None:
            return True
        total = int(row["total_calls"])
        failures = int(row["failures"])
        if total < self._min_samples:
            return False
        return (failures / total) >= threshold

    def _threshold_for(self, mode: str) -> float:
        if mode == self.TOOL_USE:
            return self._tool_use_threshold
        return self._together_threshold


__all__ = ["AutoDisableState"]
