"""Watchdog-backed filesystem watcher.

Owns a ``watchdog.observers.Observer`` for ``data/library/`` and
``data/campaigns/``. Filesystem events are bridged from the watchdog worker
thread back into the owner event loop via
:func:`asyncio.run_coroutine_threadsafe`. Each event is funneled through
:meth:`FileWatcher.process_path` so tests can drive the watcher synchronously
without touching the OS event source.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from grimoire.event_bus import Event, EventBus
from grimoire.files import (
    FrontmatterError,
    YamlError,
    content_hash,
    load_yaml,
    read_markdown,
)
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import (
    delete_campaign_content_row,
    delete_library_index_row,
    upsert_campaign_content_index,
    upsert_library_index,
)
from grimoire.watcher.classifier import WatchedFile, classify_path

logger = logging.getLogger(__name__)


# Kinds whose body is prose and should be embedded for retrieval.
# Structured-only files (worlds, image presets, sheets, image metadata,
# scene sidecars, campaign configs) are skipped — embedding them adds noise
# without buying recall on the surfaces that query the vector index.
_EMBEDDABLE_KINDS: frozenset[str] = frozenset(
    {"library_entity", "library_style_guide", "emergent", "scene_body"}
)


@dataclass(frozen=True, slots=True)
class EmbeddingJob:
    """A unit of pending embedding work produced by the watcher.

    Consumed by a worker that fans out to the embedding provider plugin and
    writes vectors via :meth:`StateStore.add_embedding`. The job is kept small
    on purpose — the consumer re-reads metadata it needs from SQLite.
    """

    ref: str
    scope: str  # "library" | "campaign"
    source_kind: str  # entity kind (character, lore, scene, ...)
    text: str
    campaign_id: str | None = None


class EmbeddingQueue:
    """In-memory FIFO of pending embedding jobs.

    The watcher pushes jobs whenever a real content change is indexed. A
    downstream worker drains the queue and computes vectors out-of-band so the
    initial scan (which can touch thousands of files) stays fast. The queue is
    intentionally minimal — it has no persistence and is rebuilt from
    SQLite-vs-files diffing on restart.
    """

    def __init__(self) -> None:
        self._items: deque[EmbeddingJob] = deque()

    def enqueue(self, job: EmbeddingJob) -> None:
        self._items.append(job)

    @property
    def pending(self) -> int:
        return len(self._items)

    def __len__(self) -> int:  # convenience for tests
        return len(self._items)

    def drain(self) -> list[EmbeddingJob]:
        out = list(self._items)
        self._items.clear()
        return out

    def peek(self) -> list[EmbeddingJob]:
        return list(self._items)


class FileWatcher:
    """Reindex on file change; emit ``*_file_changed`` events on the bus.

    The watcher keeps an in-memory ``_known_hashes`` cache so spurious
    filesystem events (e.g. ``touch`` that doesn't change content) are filtered
    before reaching the index. Real changes are classified, reindexed against
    the appropriate table, and broadcast as typed events.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        store: StateStore,
        bus: EventBus,
        loop: asyncio.AbstractEventLoop | None = None,
        embedding_queue: EmbeddingQueue | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.store = store
        self.bus = bus
        self._loop = loop
        self._observer: Observer | None = None
        self._known_hashes: dict[Path, str | None] = {}
        self.embedding_queue = embedding_queue or EmbeddingQueue()
        # Paths the app is about to write, keyed to the content_hash the app
        # expects to land on disk. Used to distinguish "watcher saw our own
        # write" (silent reindex) from "external user wrote during/after our
        # write" (last-write-wins with a conflict warning).
        self._expected_writes: dict[Path, str] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self, *, initial_scan: bool = True) -> None:
        """Run the initial scan and start the OS-level observer."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self.data_root.mkdir(parents=True, exist_ok=True)
        library_root = self.data_root / "library"
        campaigns_root = self.data_root / "campaigns"
        library_root.mkdir(parents=True, exist_ok=True)
        campaigns_root.mkdir(parents=True, exist_ok=True)

        if initial_scan:
            await self.scan_now()

        if self._observer is not None:
            return
        observer = Observer()
        handler = _WatchdogBridge(self)
        observer.schedule(handler, str(library_root), recursive=True)
        observer.schedule(handler, str(campaigns_root), recursive=True)
        observer.start()
        self._observer = observer

    async def stop(self) -> None:
        """Stop the observer and join its worker thread."""
        observer = self._observer
        if observer is None:
            return
        self._observer = None
        observer.stop()
        await asyncio.get_running_loop().run_in_executor(None, observer.join, 5.0)

    # ------------------------------------------------------------------ #
    # Public processing API
    # ------------------------------------------------------------------ #

    async def scan_now(self) -> None:
        """Walk both roots and bring SQLite indexes in line with the filesystem.

        Emits ``library_indexed`` once at completion so consumers (frontend
        progress UI, embedding worker) can react to the steady-state index.
        Live filesystem events are not emitted during the scan — they would
        produce a thundering herd of changes for state that's just being
        catching up to disk.
        """
        seen_library: set[str] = set()
        seen_content: set[str] = set()
        library_files = 0
        campaign_files = 0

        library_root = self.data_root / "library"
        if library_root.exists():
            for path in _iter_files(library_root):
                watched = classify_path(self.data_root, path)
                if watched is None:
                    continue
                await self._reindex(watched, emit=False)
                library_files += 1
                if watched.library_id is not None and watched.scope == "library":
                    seen_library.add(watched.library_id)

        campaigns_root = self.data_root / "campaigns"
        if campaigns_root.exists():
            for path in _iter_files(campaigns_root):
                watched = classify_path(self.data_root, path)
                if watched is None:
                    continue
                await self._reindex(watched, emit=False)
                campaign_files += 1
                cid = watched.content_index_id
                if cid is not None:
                    seen_content.add(cid)

        await self._drop_orphan_library_rows(seen_library)
        await self._drop_orphan_content_rows(seen_content)

        await self.bus.emit(
            Event(
                type="library_indexed",
                payload={
                    "library_files": library_files,
                    "campaign_files": campaign_files,
                    "embedding_queue_depth": self.embedding_queue.pending,
                },
            )
        )

    async def process_path(self, path: Path) -> None:
        """Process a single filesystem event for ``path``.

        Tests call this directly; the watchdog bridge funnels real events here.
        """
        watched = classify_path(self.data_root, path)
        if watched is None:
            return
        await self._reindex(watched, emit=True)

    # ------------------------------------------------------------------ #
    # Write coordination (conflict detection)
    # ------------------------------------------------------------------ #

    def register_expected_write(self, path: Path, expected_hash: str) -> None:
        """Tell the watcher the app is about to land ``expected_hash`` at ``path``.

        Called by file-write mediators in :class:`StateStore` immediately
        before flushing to disk. The watcher uses this to decide whether the
        next event for ``path`` is the app's own write (silent) or an external
        edit that raced with it (last-write-wins + conflict warning).
        """
        self._expected_writes[Path(path).resolve()] = expected_hash

    def clear_expected_write(self, path: Path) -> None:
        self._expected_writes.pop(Path(path).resolve(), None)

    # ------------------------------------------------------------------ #
    # Internal: reindex + emit
    # ------------------------------------------------------------------ #

    async def _reindex(self, watched: WatchedFile, *, emit: bool) -> None:
        path = watched.path
        # Consume any pending write expectation up front so it can't survive
        # early returns (parse error, spurious-event dedup) and falsely flag
        # the next real edit as a conflict.
        expected = self._expected_writes.pop(path, None)
        try:
            parsed = _parse_file(watched)
        except (FrontmatterError, YamlError, OSError) as exc:
            logger.warning("watcher: failed to parse %s: %s", path, exc)
            return

        prior = self._known_hashes.get(path)
        new_hash = None if parsed is None else _compute_index_hash(parsed.frontmatter, parsed.body)

        if path in self._known_hashes and prior == new_hash:
            return  # Spurious event — content didn't change.

        change_type = _change_type(prior, new_hash, path in self._known_hashes)

        # A conflict means the app pre-registered a write here but the file
        # on disk landed on different content — an external editor raced and
        # last-write-wins gave the user's edit priority. We still reindex
        # what's on disk; the warning surfaces downstream.
        conflict = expected is not None and expected != new_hash

        if new_hash is None:
            self._known_hashes.pop(path, None)
            await self._apply_delete(watched)
        else:
            self._known_hashes[path] = new_hash
            assert parsed is not None
            await self._apply_upsert(watched, parsed.frontmatter, parsed.body)
            if watched.kind in _EMBEDDABLE_KINDS and parsed.body.strip():
                self._enqueue_embedding(watched, parsed.frontmatter, parsed.body)

        if emit:
            await self._emit(
                watched,
                change_type=change_type,
                content_hash_value=new_hash,
                conflict=conflict,
            )

    def _enqueue_embedding(
        self,
        watched: WatchedFile,
        frontmatter: dict,
        body: str,
    ) -> None:
        if watched.scope == "library":
            ref = watched.library_id
        else:
            ref = watched.content_index_id or (
                f"campaigns/{watched.campaign_id}/scenes/{watched.scene_basename}"
                if watched.kind == "scene_body"
                else None
            )
        if ref is None:
            return
        name = frontmatter.get("name") or frontmatter.get("title") or ""
        text = f"{name}\n\n{body}" if name else body
        self.embedding_queue.enqueue(
            EmbeddingJob(
                ref=ref,
                scope=watched.scope,
                source_kind=watched.entity_kind or watched.kind,
                text=text,
                campaign_id=watched.campaign_id,
            )
        )

    async def _apply_upsert(
        self,
        watched: WatchedFile,
        frontmatter: dict,
        body: str,
    ) -> None:
        async with self.store.db.acquire() as conn:
            await conn.execute("BEGIN")
            try:
                if watched.scope == "library" and watched.library_id is not None:
                    await upsert_library_index(
                        conn,
                        data_root=self.data_root,
                        library_id=watched.library_id,
                        path=watched.path,
                        frontmatter=frontmatter,
                        body=body,
                    )
                elif watched.scope == "campaign" and watched.content_index_id is not None:
                    await upsert_campaign_content_index(
                        conn,
                        data_root=self.data_root,
                        campaign_id=watched.campaign_id or "",
                        composite_id=watched.content_index_id,
                        kind=_content_kind_for(watched),
                        entity_subkind=watched.entity_kind,
                        asset_id=watched.asset_id or watched.image_id,
                        path=watched.path,
                        frontmatter=frontmatter,
                        body=body if body else None,
                    )
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")

    async def _apply_delete(self, watched: WatchedFile) -> None:
        async with self.store.db.acquire() as conn:
            await conn.execute("BEGIN")
            try:
                if watched.scope == "library" and watched.library_id is not None:
                    await delete_library_index_row(conn, watched.library_id)
                elif watched.scope == "campaign" and watched.content_index_id is not None:
                    await delete_campaign_content_row(conn, watched.content_index_id)
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")
        # Embeddings tied to this ref are now stale. The classifier sets
        # ``library_id`` on campaign overrides too (it points at the underlying
        # library entity) so we route by ``scope`` to avoid wiping shared
        # library embeddings when a single campaign override is removed.
        ref = watched.library_id if watched.scope == "library" else watched.content_index_id
        if ref is not None:
            try:
                await self.store.delete_embeddings(ref)
            except Exception:
                logger.exception("watcher: failed to delete embeddings for %s", ref)

    async def _emit(
        self,
        watched: WatchedFile,
        *,
        change_type: str,
        content_hash_value: str | None,
        conflict: bool = False,
    ) -> None:
        payload: dict = {
            "scope": watched.scope,
            "kind": watched.kind,
            "path": str(watched.path),
            "change_type": change_type,
            "content_hash": content_hash_value,
            "conflict": conflict,
        }
        if watched.library_id is not None:
            payload["library_id"] = watched.library_id
        if watched.campaign_id is not None:
            payload["campaign_id"] = watched.campaign_id
        if watched.branch_id is not None:
            payload["branch_id"] = watched.branch_id
        if watched.world_id is not None:
            payload["world_id"] = watched.world_id
        if watched.entity_kind is not None:
            payload["entity_kind"] = watched.entity_kind
        if watched.asset_id is not None:
            payload["asset_id"] = watched.asset_id
        if watched.mechanics_id is not None:
            payload["mechanics_id"] = watched.mechanics_id
        if watched.scene_basename is not None:
            payload["scene_basename"] = watched.scene_basename
        if watched.image_id is not None:
            payload["image_id"] = watched.image_id
        await self.bus.emit(Event(type=watched.event_type, payload=payload))

    async def _drop_orphan_library_rows(self, seen: set[str]) -> None:
        rows = await self.store.db.fetchall("SELECT id FROM library_index")
        for row in rows:
            if row["id"] not in seen:
                await self.store.db.execute("DELETE FROM library_index WHERE id = ?", (row["id"],))

    async def _drop_orphan_content_rows(self, seen: set[str]) -> None:
        rows = await self.store.db.fetchall("SELECT id FROM campaign_content_index")
        for row in rows:
            if row["id"] not in seen:
                await self.store.db.execute(
                    "DELETE FROM campaign_content_index WHERE id = ?", (row["id"],)
                )

    # Hook for the watchdog bridge thread to enqueue work onto our loop.
    def _schedule_from_thread(self, path: Path) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._safe_process(path), loop)

    async def _safe_process(self, path: Path) -> None:
        try:
            await self.process_path(path)
        except Exception:
            logger.exception("watcher: process_path failed for %s", path)


# ----------------------------------------------------------------------- #
# Helpers
# ----------------------------------------------------------------------- #


class _Parsed:
    __slots__ = ("body", "frontmatter")

    def __init__(self, frontmatter: dict, body: str) -> None:
        self.frontmatter = frontmatter
        self.body = body


def _parse_file(watched: WatchedFile) -> _Parsed | None:
    """Parse a file into ``(frontmatter, body)``. ``None`` if it doesn't exist."""
    path = watched.path
    if not path.exists():
        return None
    if watched.kind in {
        "library_entity",
        "library_style_guide",
        "emergent",
    }:
        doc = read_markdown(path)
        return _Parsed(doc.frontmatter, doc.body)
    if watched.kind in {
        "library_world",
        "library_image_preset",
        "override",
        "sheet",
        "image_metadata",
        "campaign_config",
    }:
        data = load_yaml(path) or {}
        if not isinstance(data, dict):
            raise YamlError(f"{path}: top-level YAML must be a mapping")
        return _Parsed(data, "")
    if watched.kind in {"scene_body", "scene_sidecar", "image_asset"}:
        # Scene files and image assets aren't indexed in SQLite; we just hash
        # their raw bytes so dedup still works.
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return _Parsed({"__binary_sha__": content_hash(path.read_bytes().hex())}, "")
        return _Parsed({}, text)
    return None


def _compute_index_hash(frontmatter: dict | None, body: str | None) -> str:
    """Match the hash computed by :func:`upsert_library_index` and friends."""
    serialized = (
        (json.dumps(frontmatter, sort_keys=True) if frontmatter is not None else "")
        + "\n"
        + (body if body is not None else "")
    )
    return content_hash(serialized)


def _change_type(
    prior_hash: str | None,
    new_hash: str | None,
    was_known: bool,
) -> str:
    if new_hash is None:
        return "deleted"
    if not was_known or prior_hash is None:
        return "created"
    return "modified"


def _content_kind_for(watched: WatchedFile) -> str:
    if watched.kind == "override":
        return "override"
    if watched.kind == "emergent":
        return "emergent"
    if watched.kind == "sheet":
        return "sheet"
    if watched.kind == "image_metadata":
        return "image"
    return watched.kind


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


# ----------------------------------------------------------------------- #
# Watchdog bridge
# ----------------------------------------------------------------------- #


class _WatchdogBridge(FileSystemEventHandler):
    """Forward filesystem events from watchdog's worker thread to the watcher."""

    def __init__(self, watcher: FileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = getattr(event, "src_path", None)
        if src:
            self._watcher._schedule_from_thread(Path(src))
        dest = getattr(event, "dest_path", None)
        if dest:
            self._watcher._schedule_from_thread(Path(dest))
