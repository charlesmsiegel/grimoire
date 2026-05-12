"""Filesystem watcher.

Wires Python ``watchdog`` to the State Store and event bus. Monitors
``data/library/`` and ``data/campaigns/`` for changes; reindexes the affected
SQLite indexes; emits typed events (``library_file_changed``,
``campaign_file_changed``, ``scene_file_changed``, ``sheet_file_changed``).

Uses ``content_hash`` to filter out spurious filesystem events (touches that
don't change the content) and to drive last-write-wins resolution across races.
See spec 03 §File watcher and spec 18 §File watcher.
"""

from grimoire.watcher.classifier import WatchedFile, classify_path
from grimoire.watcher.watcher import FileWatcher

__all__ = [
    "FileWatcher",
    "WatchedFile",
    "classify_path",
]
