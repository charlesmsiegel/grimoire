"""Tests for §7: MechanicsFileWatcher debounced rescan."""

from __future__ import annotations

import asyncio
from pathlib import Path

from grimoire.mechanics.config import MechanicsConfig
from grimoire.mechanics.file_watcher import MechanicsFileWatcher


class _CountingMechanics:
    """Minimal stand-in: tracks how many times rescan was invoked."""

    def __init__(self, root: Path) -> None:
        self.config = MechanicsConfig(root=root)
        self.calls = 0

    async def rescan(self) -> None:
        self.calls += 1


async def test_debounce_coalesces_rapid_events(tmp_path: Path) -> None:
    root = tmp_path / "mechanics"
    root.mkdir()
    mechanics = _CountingMechanics(root)
    watcher = MechanicsFileWatcher(mechanics, debounce_seconds=0.05)
    watcher._loop = asyncio.get_running_loop()

    # Fire several "events" in quick succession; the debounce should collapse
    # them into a single rescan.
    for _ in range(5):
        watcher._reschedule_on_loop()
    await asyncio.sleep(0.2)
    assert mechanics.calls == 1


async def test_stop_cancels_pending_rescan(tmp_path: Path) -> None:
    root = tmp_path / "mechanics"
    root.mkdir()
    mechanics = _CountingMechanics(root)
    watcher = MechanicsFileWatcher(mechanics, debounce_seconds=0.5)
    watcher._loop = asyncio.get_running_loop()

    watcher._reschedule_on_loop()
    await watcher.stop()
    # After stop, the pending task should be cancelled and rescan never invoked.
    await asyncio.sleep(0.6)
    assert mechanics.calls == 0


async def test_start_creates_observer(tmp_path: Path) -> None:
    """Smoke test: start/stop the real watchdog observer cycle without errors."""
    root = tmp_path / "mechanics"
    root.mkdir()
    mechanics = _CountingMechanics(root)
    watcher = MechanicsFileWatcher(mechanics, debounce_seconds=0.05)
    try:
        await watcher.start()
        # Touch a file inside the watched root to wake the observer.
        (root / "ping.txt").write_text("hi", encoding="utf-8")
        # The observer fires on its own thread; give it generous time on CI.
        for _ in range(20):
            if mechanics.calls > 0:
                break
            await asyncio.sleep(0.1)
    finally:
        await watcher.stop()
    assert mechanics.calls >= 1
