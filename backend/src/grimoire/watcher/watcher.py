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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchdog.events import EVENT_TYPE_MOVED, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.files import (
    FrontmatterError,
    YamlError,
    content_hash,
    load_yaml,
    read_markdown,
)
from grimoire.library.config import LibraryConfig
from grimoire.state_store.indexers import (
    delete_campaign_content_row,
    delete_library_index_row,
    upsert_campaign_content_index,
    upsert_library_index,
)
from grimoire.watcher.classifier import WatchedFile, classify_path

if TYPE_CHECKING:
    from grimoire.state_store.store import StateStore

logger = logging.getLogger(__name__)


# Kinds whose body is prose and should be embedded for retrieval.
# Structured-only files (worlds, image presets, sheets, image metadata,
# scene sidecars, campaign configs) are skipped — embedding them adds noise
# without buying recall on the surfaces that query the vector index.
_EMBEDDABLE_KINDS: frozenset[str] = frozenset(
    {"library_entity", "library_style_guide", "emergent", "scene_body"}
)

# Kinds whose body is summarized into ``library_index.body_compressed`` for
# the background-tier context injection (spec 02 §Background tier). Only
# library-scoped prose-bearing kinds qualify; emergent and scene bodies live
# in ``campaign_content_index`` which has no body_compressed column.
_SUMMARIZABLE_KINDS: frozenset[str] = frozenset({"library_entity", "library_style_guide"})


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


@dataclass(frozen=True, slots=True)
class SummaryJob:
    """A unit of pending auto-summary work produced by the watcher.

    Consumed by a worker that fans out to a summarizer (typically an LLM
    call) and writes the result via :meth:`StateStore.set_body_compressed`.
    The worker re-reads the row's current body before writing so a stale
    job cannot overwrite a fresher summary.
    """

    library_id: str
    source_kind: str  # entity_kind: character / lore / style_guide / ...
    text: str
    content_hash: str


class SummaryQueue:
    """In-memory FIFO of pending body-compressed jobs.

    Mirrors :class:`EmbeddingQueue` — the watcher enqueues whenever a
    real content change indexes a summarizable prose body that exceeds
    the configured threshold; a downstream worker drains and processes
    out-of-band.
    """

    def __init__(self) -> None:
        self._items: deque[SummaryJob] = deque()

    def enqueue(self, job: SummaryJob) -> None:
        self._items.append(job)

    @property
    def pending(self) -> int:
        return len(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def drain(self) -> list[SummaryJob]:
        out = list(self._items)
        self._items.clear()
        return out

    def peek(self) -> list[SummaryJob]:
        return list(self._items)


@dataclass(slots=True)
class _PendingRename:
    """An unacknowledged directory move awaiting reconciliation.

    While a rename is pending, per-file events under ``src`` or ``dest`` are
    suppressed so the cascade of delete-then-create events watchdog produces
    for the moved subtree does not silently re-key index rows. The user must
    call :meth:`FileWatcher.reconcile_directory_rename` to either accept
    (re-key index rows to the new paths) or reject (leave rows untouched).
    """

    src: Path
    dest: Path
    scope: str  # "library" | "campaign"
    library_ids: list[str] = field(default_factory=list)
    content_index_ids: list[str] = field(default_factory=list)


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
        summary_queue: SummaryQueue | None = None,
        scene_manager: object | None = None,
        config: LibraryConfig | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.store = store
        self.bus = bus
        self._loop = loop
        self._observer: Observer | None = None
        self._known_hashes: dict[Path, str | None] = {}
        self.embedding_queue = embedding_queue or EmbeddingQueue()
        self.summary_queue = summary_queue or SummaryQueue()
        self.config = config or LibraryConfig()
        # Paths the app is about to write, keyed to the content_hash the app
        # expects to land on disk. Used to distinguish "watcher saw our own
        # write" (silent reindex) from "external user wrote during/after our
        # write" (last-write-wins with a conflict warning).
        self._expected_writes: dict[Path, str] = {}
        # Optional Scene Manager handle. When set, scene_body / scene_sidecar
        # filesystem events are forwarded to ``scene_manager.reindex_from_disk``
        # which rebuilds in-memory state, persists the sidecar, and emits its
        # own ``scene_file_changed`` event (with conflict=True when the
        # on-disk body hash doesn't match the last app-written hash). The
        # watcher suppresses its own scene_file_changed emit so consumers
        # only see one event per change.
        self._scene_manager = scene_manager
        # Directory renames awaiting user reconciliation. Keyed by the moved
        # subtree's destination path; an entry's ``src`` and ``dest`` together
        # define the prefixes whose per-file events should be suppressed.
        self._pending_renames: dict[Path, _PendingRename] = {}

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

    def _mtime_skip(self, path: Path, mtime_cache: dict[str, tuple[str, str]]) -> bool:
        """Return True if the file's mtime matches the cached value — skip I/O."""
        from grimoire.state_store.indexers import file_mtime_iso

        try:
            rel = str(path.relative_to(self.data_root))
        except ValueError:
            return False
        cached = mtime_cache.get(rel)
        if cached is None:
            return False
        cached_mtime_str, cached_hash = cached
        try:
            current_mtime_str = file_mtime_iso(path)
        except OSError:
            return False
        if current_mtime_str == cached_mtime_str:
            self._known_hashes[path] = cached_hash
            return True
        return False

    async def scan_now(self, *, scope: str = "all") -> dict[str, Any]:
        """Walk one or both roots and bring SQLite indexes in line with the filesystem.

        ``scope`` selects which roots to walk: ``"library"`` walks only
        ``data/library``, ``"campaigns"`` walks only ``data/campaigns``, and
        ``"all"`` (the default) walks both. Orphan cleanup is restricted to
        the scopes actually walked so a partial rescan doesn't wipe rows for
        the untouched root.

        Emits ``library_indexed`` once at completion so consumers (frontend
        progress UI, embedding worker) can react to the steady-state index.
        Live filesystem events are not emitted during the scan — they would
        produce a thundering herd of changes for state that's just being
        catching up to disk.

        Returns a summary dict with the scope and the per-root file counts
        the caller can surface to the UI.
        """
        if scope not in {"all", "library", "campaigns"}:
            raise ValueError(f"unknown scan scope {scope!r}")

        do_library = scope in {"all", "library"}
        do_campaigns = scope in {"all", "campaigns"}

        seen_library: set[str] = set()
        seen_content: set[str] = set()
        library_files = 0
        campaign_files = 0

        mtime_cache = await self.store.bulk_load_index_mtimes()

        if do_library:
            library_root = self.data_root / "library"
            if library_root.exists():
                paths = await asyncio.to_thread(lambda: list(_iter_files(library_root)))
                for path in paths:
                    watched = classify_path(self.data_root, path)
                    if watched is None:
                        continue
                    if self._mtime_skip(path, mtime_cache):
                        library_files += 1
                        if watched.library_id is not None and watched.scope == "library":
                            seen_library.add(watched.library_id)
                        await asyncio.sleep(0)
                        continue
                    await self._reindex(watched, emit=False)
                    library_files += 1
                    if watched.library_id is not None and watched.scope == "library":
                        seen_library.add(watched.library_id)
                    await asyncio.sleep(0)

        if do_campaigns:
            campaigns_root = self.data_root / "campaigns"
            if campaigns_root.exists():
                paths = await asyncio.to_thread(lambda: list(_iter_files(campaigns_root)))
                for path in paths:
                    watched = classify_path(self.data_root, path)
                    if watched is None:
                        continue
                    if self._mtime_skip(path, mtime_cache):
                        campaign_files += 1
                        cid = watched.content_index_id
                        if cid is not None:
                            seen_content.add(cid)
                        await asyncio.sleep(0)
                        continue
                    await self._reindex(watched, emit=False)
                    campaign_files += 1
                    cid = watched.content_index_id
                    if cid is not None:
                        seen_content.add(cid)
                    await asyncio.sleep(0)

        if do_library:
            await self._drop_orphan_library_rows(seen_library)
        if do_campaigns:
            await self._drop_orphan_content_rows(seen_content)
            await self._rebuild_inventory_holdings()

        await self.bus.emit(
            Event(
                type=events.LIBRARY_INDEXED,
                payload={
                    "library_files": library_files,
                    "campaign_files": campaign_files,
                    "embedding_queue_depth": self.embedding_queue.pending,
                    "summary_queue_depth": self.summary_queue.pending,
                },
            )
        )
        return {
            "scope": scope,
            "library_files": library_files,
            "campaign_files": campaign_files,
        }

    async def process_path(self, path: Path) -> None:
        """Process a single filesystem event for ``path``.

        Tests call this directly; the watchdog bridge funnels real events here.
        """
        resolved = self._resolve_for_suppression(path)
        if self._is_suppressed(resolved):
            return
        watched = classify_path(self.data_root, path)
        if watched is None:
            return
        await self._reindex(watched, emit=True)

    async def handle_directory_move(self, src: Path, dest: Path) -> None:
        """Handle a directory rename/move under ``data/library`` or ``data/campaigns``.

        Suppresses the per-file delete/create cascade for the moved subtree and
        emits a single ``library_rename_detected`` or ``campaign_rename_detected``
        event the user must acknowledge via :meth:`reconcile_directory_rename`.
        Until reconciliation, the SQLite index rows for the moved subtree are
        left untouched (still pointing at the old paths) so an accidental
        rename can be reverted without losing index state.
        """
        src_resolved = Path(src).resolve(strict=False)
        dest_resolved = Path(dest).resolve(strict=False)

        library_root = self.data_root / "library"
        campaigns_root = self.data_root / "campaigns"
        if _is_under(src_resolved, library_root) or _is_under(dest_resolved, library_root):
            scope = "library"
            event_type = events.LIBRARY_RENAME_DETECTED
        elif _is_under(src_resolved, campaigns_root) or _is_under(dest_resolved, campaigns_root):
            scope = "campaign"
            event_type = events.CAMPAIGN_RENAME_DETECTED
        else:
            return

        library_ids, content_index_ids = await self._collect_affected_index_rows(src_resolved)

        pending = _PendingRename(
            src=src_resolved,
            dest=dest_resolved,
            scope=scope,
            library_ids=library_ids,
            content_index_ids=content_index_ids,
        )
        # Keying on dest gives reconcile_directory_rename a unique lookup,
        # but we also need to know about pending renames keyed on either
        # prefix when filtering live events. The list-scan in _is_suppressed
        # handles that — we don't need a parallel index.
        self._pending_renames[dest_resolved] = pending

        await self.bus.emit(
            Event(
                type=event_type,
                payload={
                    "src_path": str(src_resolved),
                    "dest_path": str(dest_resolved),
                    "scope": scope,
                    "library_ids": list(library_ids),
                    "content_index_ids": list(content_index_ids),
                },
            )
        )

    async def reconcile_directory_rename(
        self,
        src: Path,
        dest: Path,
        *,
        accept: bool,
    ) -> None:
        """Resolve a pending directory rename.

        ``accept=True`` re-keys the moved subtree's index rows to the new paths
        (drops stale rows whose old library_id embedded the old directory name,
        then walks the new tree and reindexes from disk). ``accept=False``
        clears the suppression without touching the index — index rows continue
        to point at the now-missing original paths, and the caller is expected
        to either undo the rename on disk or trigger a manual cleanup.
        """
        src_resolved = Path(src).resolve(strict=False)
        dest_resolved = Path(dest).resolve(strict=False)
        pending = self._pending_renames.pop(dest_resolved, None)
        if pending is None:
            # Unknown rename — nothing to do. Don't raise; reconciliation is
            # best-effort and the caller may retry after a missed event.
            return

        if not accept:
            # Drop the in-memory hashes for the old subtree so a later
            # genuine edit at the same paths (e.g. after the user undoes
            # the rename on disk) isn't deduped against stale state.
            self._forget_hashes_under(src_resolved)
            return

        # Accept: tear down old rows tied to the moved subtree, then rescan
        # the destination tree to insert fresh rows whose ids embed the new
        # directory names.
        await self._delete_index_rows(pending)
        self._forget_hashes_under(pending.src)
        if dest_resolved.exists():
            for path in _iter_files(dest_resolved):
                watched = classify_path(self.data_root, path)
                if watched is None:
                    continue
                await self._reindex(watched, emit=False)

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
            if (
                self.config.indexing.embed_on_index
                and watched.kind in _EMBEDDABLE_KINDS
                and parsed.body.strip()
            ):
                self._enqueue_embedding(watched, parsed.frontmatter, parsed.body)
            if (
                self.config.indexing.summarize_on_index
                and watched.kind in _SUMMARIZABLE_KINDS
                and watched.library_id is not None
                and len(parsed.body) >= self.config.indexing.summarize_min_body_length
            ):
                self._enqueue_summary(watched, parsed.body, new_hash or "")

        # §3 — forward scene file changes into the Scene Manager so it can
        # re-parse the markdown, rebuild post records, and emit its own
        # scene_file_changed event with the hash-based conflict flag. Suppress
        # our own emit for that kind to avoid duplicate notifications.
        if (
            self._scene_manager is not None
            and watched.kind in {"scene_body", "scene_sidecar"}
            and watched.scene_basename is not None
            and watched.campaign_id is not None
            and emit
        ):
            scene_id = _resolve_scene_id(watched)
            try:
                await self._scene_manager.reindex_from_disk(scene_id)
            except KeyError:
                # New scene file (not yet known to the manager) — fall back to
                # the watcher's own emit so consumers still see the change.
                pass
            except Exception:
                logger.exception("scene manager reindex failed for %s", path)
            else:
                return

        if emit:
            await self._emit(
                watched,
                change_type=change_type,
                content_hash_value=new_hash,
                conflict=conflict,
            )

    def _enqueue_summary(
        self,
        watched: WatchedFile,
        body: str,
        content_hash_value: str,
    ) -> None:
        """Push a body-compressed job onto the queue (§1).

        The worker re-reads the row's ``body`` and ``body_compressed`` to
        skip stale work — see :meth:`StateStore.set_body_compressed`.
        """
        if watched.library_id is None:
            return
        self.summary_queue.enqueue(
            SummaryJob(
                library_id=watched.library_id,
                source_kind=watched.entity_kind or watched.kind,
                text=body,
                content_hash=content_hash_value,
            )
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
            # IMMEDIATE: take the writer lock now so busy_timeout applies.
            # Deferred BEGIN + SELECT + UPDATE races background writers and
            # returns SQLITE_BUSY immediately on snapshot upgrade.
            await conn.execute("BEGIN IMMEDIATE")
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
            await conn.execute("BEGIN IMMEDIATE")
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
        await self._drop_orphans("library_index", seen)

    async def _drop_orphan_content_rows(self, seen: set[str]) -> None:
        await self._drop_orphans("campaign_content_index", seen)

    async def _rebuild_inventory_holdings(self) -> None:
        """Repopulate the derived inventory_holdings table from `inventory:`
        sections in overlay files. Delegates to the storage layer, which owns
        the table and does a full truncate-and-repopulate (so removed sections
        and removed holders leave no stale rows)."""
        await self.store.rebuild_inventory_holdings_from_files()

    async def _drop_orphans(self, table: str, seen: set[str]) -> None:
        """Delete every ``table`` row whose id isn't in ``seen``.

        Batches into a single connection + executemany rather than one
        ``self.store.db.execute(...)`` per orphan; on a large rescan with
        thousands of orphans the old loop opened N pool connections and
        ran outside any transaction, so a partial failure left SQLite
        half-cleaned.
        """
        # Table name is a hard-coded literal from the caller (no user
        # input), so format-interpolation is safe here.
        rows = await self.store.db.fetchall(f"SELECT id FROM {table}")
        orphans = [(row["id"],) for row in rows if row["id"] not in seen]
        if not orphans:
            return
        async with self.store.db.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.executemany(f"DELETE FROM {table} WHERE id = ?", orphans)
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            await conn.execute("COMMIT")

    # Hook for the watchdog bridge thread to enqueue work onto our loop.
    def _schedule_from_thread(self, path: Path) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._safe_process(path), loop)

    def _schedule_directory_move_from_thread(self, src: Path, dest: Path) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._safe_handle_directory_move(src, dest), loop)

    async def _safe_process(self, path: Path) -> None:
        try:
            await self.process_path(path)
        except Exception:
            logger.exception("watcher: process_path failed for %s", path)

    async def _safe_handle_directory_move(self, src: Path, dest: Path) -> None:
        try:
            await self.handle_directory_move(src, dest)
        except Exception:
            logger.exception("watcher: handle_directory_move failed for %s -> %s", src, dest)

    # ------------------------------------------------------------------ #
    # Suppression + rename bookkeeping
    # ------------------------------------------------------------------ #

    def _resolve_for_suppression(self, path: Path) -> Path:
        # ``path`` may not exist (the deleted half of a move); ``Path.resolve``
        # with strict=False still normalises symlinks in the parent chain.
        try:
            return Path(path).resolve(strict=False)
        except OSError:
            return Path(path)

    def _is_suppressed(self, resolved_path: Path) -> bool:
        if not self._pending_renames:
            return False
        for pending in self._pending_renames.values():
            if _is_under(resolved_path, pending.src) or _is_under(resolved_path, pending.dest):
                return True
        return False

    async def _collect_affected_index_rows(self, src: Path) -> tuple[list[str], list[str]]:
        """Find the library_index / campaign_content_index rows under ``src``.

        Rows are matched by their stored ``path`` column (which is relative to
        ``data_root``). The lookup is "starts with the rel-src + '/'" — anything
        whose stored path falls inside the moved subtree is in scope.
        """
        try:
            rel = str(Path(src).resolve().relative_to(self.data_root))
        except (ValueError, OSError):
            return [], []
        prefix = rel.rstrip("/") + "/"
        like = prefix.replace("\\", "/") + "%"

        library_rows = await self.store.db.fetchall(
            "SELECT id FROM library_index WHERE path LIKE ?",
            (like,),
        )
        content_rows = await self.store.db.fetchall(
            "SELECT id FROM campaign_content_index WHERE path LIKE ?",
            (like,),
        )
        return (
            [str(r["id"]) for r in library_rows],
            [str(r["id"]) for r in content_rows],
        )

    async def _delete_index_rows(self, pending: _PendingRename) -> None:
        async with self.store.db.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                for library_id in pending.library_ids:
                    await delete_library_index_row(conn, library_id)
                for cid in pending.content_index_ids:
                    await delete_campaign_content_row(conn, cid)
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")
        # Stale embeddings are tied to the old refs — drop them so retrieval
        # doesn't continue scoring rows that no longer exist.
        for ref in (*pending.library_ids, *pending.content_index_ids):
            try:
                await self.store.delete_embeddings(ref)
            except Exception:
                logger.exception("watcher: failed to delete embeddings for %s", ref)

    def _forget_hashes_under(self, root: Path) -> None:
        root_resolved = Path(root).resolve(strict=False)
        stale = [p for p in self._known_hashes if _is_under(p, root_resolved)]
        for p in stale:
            self._known_hashes.pop(p, None)


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


def _resolve_scene_id(watched: WatchedFile) -> str:
    """Build the Scene Manager's scene_id from a classified scene path.

    Mirrors :meth:`grimoire.scenes.manager.SceneManager._scene_id`.
    """
    return f"{watched.campaign_id}:{watched.scene_basename}"


def _is_under(path: Path, root: Path) -> bool:
    """``True`` if ``path`` equals ``root`` or sits inside it.

    Both arguments must already be resolved (or at least be in the same
    canonical form) — the comparison is done on parts, not strings, so a
    rename between ``/foo/bar`` and ``/foo/bar-suffix`` won't falsely match.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# ----------------------------------------------------------------------- #
# Watchdog bridge
# ----------------------------------------------------------------------- #


class _WatchdogBridge(FileSystemEventHandler):
    """Forward filesystem events from watchdog's worker thread to the watcher."""

    def __init__(self, watcher: FileWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            # Only one directory event matters for the index: a directory
            # rename/move. Everything else (mkdir/rmdir of an empty dir, dir
            # touch) is uninteresting — the per-file events for any contents
            # already cover what the index cares about.
            if event.event_type != EVENT_TYPE_MOVED:
                return
            src = getattr(event, "src_path", None)
            dest = getattr(event, "dest_path", None)
            if src and dest:
                self._watcher._schedule_directory_move_from_thread(Path(src), Path(dest))
            return
        src = getattr(event, "src_path", None)
        if src:
            self._watcher._schedule_from_thread(Path(src))
        dest = getattr(event, "dest_path", None)
        if dest:
            self._watcher._schedule_from_thread(Path(dest))
