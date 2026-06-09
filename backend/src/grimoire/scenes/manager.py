"""Scene Manager — owns the play history.

Implementation of spec 10. Deliberately depends only on the local filesystem
and the small ``EventBus`` / summarizer protocols defined here; the State
Store (task #8), watchdog (task #9), and event bus (task #3) plug in via
dependency injection once they exist.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.scenes.analysis import SceneAnalysisResult
from grimoire.scenes.boundary import BoundaryConfig, detect_scene_break
from grimoire.scenes.events import (
    ADVANCE_DISABLED,
    ADVANCE_ENABLED,
    ADVANCE_REQUESTED,
    PC_POST_APPENDED,
    POST_APPENDED,
    POST_DELETED,
    POST_EDITED,
    RUNNING_SUMMARY_DUE,
    RUNNING_SUMMARY_UPDATED,
    SCENE_DELETED,
    SCENE_ENDED,
    SCENE_FILE_CHANGED,
    SCENE_STARTED,
    THREAD_INTRODUCED,
    THREAD_PAID_OFF,
    EventBus,
    InMemoryEventBus,
    SceneEvent,
)
from grimoire.scenes.storage import (
    append_post_to_body,
    content_hash,
    next_ordinal,
    read_posts,
    read_sidecar,
    read_sidecar_post_records,
    scene_basename,
    scene_files_transaction,
    scene_paths,
    scenes_dir,
    slugify,
    write_body,
    write_sidecar,
)
from grimoire.scenes.types import (
    AdvanceDecision,
    AdvanceResult,
    Alternate,
    AuthorKind,
    Post,
    Scene,
    SceneBreakDecision,
    SceneCloseReport,
    SceneInit,
    SceneThreads,
    Thread,
)
from grimoire.types.scene import CastChange
from grimoire.util import canonicalize_character_ref, new_id

logger = logging.getLogger(__name__)

Summarizer = Callable[[str | None, list[Post]], Awaitable[str]]
FinalSummarizer = Callable[[Scene, list[Post]], Awaitable[tuple[str, list[str]]]]
AdaptiveSummarizer = Callable[[Scene, list[Post]], Awaitable[tuple[str, list[str]]]]
ThreadDetector = Callable[[Scene, list[Post]], Awaitable[list[tuple[Thread, str]]]]
SceneBreakClassifier = Callable[[Scene | None, str, list[Post]], Awaitable[SceneBreakDecision]]
SceneAnalyzer = Callable[[Scene, list[Post], str], Awaitable[SceneAnalysisResult]]


class _NullEventBus:
    async def emit(self, event: SceneEvent) -> None:  # pragma: no cover - trivial
        return None


@dataclass
class RunningSummaryConfig:
    model: str | None = None
    max_tokens: int = 1024


@dataclass
class ThreadDetectionConfig:
    enabled: bool = False
    model: str | None = None


@dataclass
class FilesConfig:
    scene_naming_pattern: str = "{ordinal:04d}-{slug}"
    post_heading_pattern: str = "## Post {order} — {author}"


@dataclass
class MultiPCConfig:
    show_pending_count_in_ui: bool = True


@dataclass
class SceneManagerConfig:
    running_summary_every_n_posts: int = 5
    boundary: BoundaryConfig = field(default_factory=BoundaryConfig)
    require_advance_with_multiple_pcs: bool = True
    running_summary: RunningSummaryConfig = field(default_factory=RunningSummaryConfig)
    thread_detection: ThreadDetectionConfig = field(default_factory=ThreadDetectionConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    multi_pc: MultiPCConfig = field(default_factory=MultiPCConfig)


@dataclass
class _PostRecord:
    """In-memory metadata about a post that isn't kept in the markdown body."""

    id: str
    turn_id: str
    created_at: datetime
    is_player: bool
    alternates: list[Alternate] = field(default_factory=list)
    primary_alternate_id: str | None = None


class SceneManager:
    """Concrete Scene Manager.

    Persistence is markdown + YAML sidecar on disk. Per-post identity
    (``id``, ``turn_id``, ``created_at``, ``is_player``) is held in a
    sidecar JSON-equivalent stored alongside; markdown stays the prose SSOT.
    """

    def __init__(
        self,
        data_root: Path,
        *,
        config: SceneManagerConfig | None = None,
        event_bus: EventBus | None = None,
        summarizer: Summarizer | None = None,
        final_summarizer: FinalSummarizer | None = None,
        thread_detector: ThreadDetector | None = None,
        scene_break_classifier: SceneBreakClassifier | None = None,
        scene_analyzer: SceneAnalyzer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        continuity: object | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
        state_store: Any = None,
        cast_change_store: Any = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.config = config or SceneManagerConfig()
        self.event_bus: EventBus = event_bus or _NullEventBus()
        self._summarizer = summarizer
        self._final_summarizer = final_summarizer
        self._adaptive_summarizer: AdaptiveSummarizer | None = None
        self._thread_detector = thread_detector
        self._scene_break_classifier = scene_break_classifier
        self._scene_analyzer = scene_analyzer
        self._clock = clock
        # §10 Continuity injection: when wired, ``start_scene`` calls
        # ``brief_for_scene`` and stuffs the result into the
        # ``scene_started`` event so the Frontend can render a pre-scene
        # briefing without a follow-up round-trip.
        self._continuity = continuity
        self._metrics: MetricsRegistryProtocol = metrics
        self._state_store = state_store
        self._cast_change_store = cast_change_store

        # Per-scene in-memory state. Persisted lazily where needed.
        self._post_records: dict[str, dict[str, _PostRecord]] = {}
        # post_id -> scene_id soft index, populated as posts are read.
        # _find_post consults it first to avoid the O(scenes x posts)
        # filesystem walk on every retcon/delete.
        self._post_scene_index: dict[str, str] = {}
        # Loaded sidecars whose `posts:` block we've already hydrated; avoids
        # re-reading the same file every time get_posts is called.
        self._records_hydrated: set[str] = set()
        # Hash of the .md file as we last wrote it. Used by reindex_from_disk
        # to detect concurrent external edits (last-write-wins + warning).
        self._known_body_hashes: dict[str, str] = {}
        # active scene per campaign_id and per-PC current scene
        self._active_scene: dict[str, str] = {}
        self._pc_current_scene: dict[tuple[str, str], str] = {}  # (campaign_id, pc_ref) -> scene_id
        self._locks: dict[str, asyncio.Lock] = {}

    def set_continuity(self, continuity: object) -> None:
        self._continuity = continuity

    def set_cast_change_store(self, store: object) -> None:
        self._cast_change_store = store

    def set_summarizer(self, summarizer: Summarizer | None) -> None:
        self._summarizer = summarizer

    def set_final_summarizer(self, summarizer: FinalSummarizer | None) -> None:
        self._final_summarizer = summarizer

    def set_adaptive_summarizer(self, summarizer: AdaptiveSummarizer | None) -> None:
        self._adaptive_summarizer = summarizer

    def set_scene_analyzer(self, analyzer: SceneAnalyzer | None) -> None:
        self._scene_analyzer = analyzer

    def set_metrics(self, metrics: MetricsRegistryProtocol) -> None:
        self._metrics = metrics

    # -- helpers ---------------------------------------------------------

    def _lock_for(self, scene_id: str) -> asyncio.Lock:
        lock = self._locks.get(scene_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scene_id] = lock
        return lock

    def _scene_id(self, campaign_id: str, ordinal: int, slug: str) -> str:
        base = scene_basename(ordinal, slug, self.config.files.scene_naming_pattern)
        return f"{campaign_id}:{base}"

    def _should_emit_running_summary(
        self,
        *,
        post_count: int,
        override: int | None,
    ) -> bool:
        """Return True when a RUNNING_SUMMARY_DUE event should fire now.

        ``override`` of ``0`` disables in-scene summaries entirely.
        ``None`` falls back to the manager-wide default
        (``self.config.running_summary_every_n_posts``).
        """
        n = override if override is not None else self.config.running_summary_every_n_posts
        if n <= 0 or post_count <= 0:
            return False
        return post_count % n == 0

    async def _campaign_summary_cadence(self, campaign_id: str) -> int | None:
        """Read ``campaigns.config["summaries"]["running_every_n_posts"]``.

        Returns ``None`` when no override is set (caller falls back to
        the manager-wide default). Best-effort: any error returns
        ``None`` to keep the post path stable.
        """
        store = getattr(self, "_state_store", None)
        if store is None:
            return None
        try:
            row = await store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return None
        if not row:
            return None
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return None
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return None
        block = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        n = block.get("running_every_n_posts")
        return int(n) if isinstance(n, int) else None

    def _should_run_final_summary(
        self,
        *,
        final_on_close_override: bool | None,
    ) -> bool:
        """Return True when ``close_scene`` should invoke the final summarizer.

        Default is True; ``False`` skips the LLM call and uses the
        running summary (or empty string) as the final summary.
        """
        return True if final_on_close_override is None else bool(final_on_close_override)

    async def _campaign_final_on_close(self, campaign_id: str) -> bool | None:
        """Read ``campaigns.config["summaries"]["final_on_close"]``.

        Mirrors :meth:`_campaign_summary_cadence`. Returns ``None`` when
        no override is set; the caller falls back to the default of True.
        """
        store = getattr(self, "_state_store", None)
        if store is None:
            return None
        try:
            row = await store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return None
        if not row:
            return None
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return None
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return None
        block = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        v = block.get("final_on_close")
        return bool(v) if isinstance(v, bool) else None

    async def _emit(self, type_: str, scene: Scene, **payload: object) -> None:
        # The shared in-process bus dispatches by ``event.type`` only, so
        # SceneEvent flows through it duck-typed. Subscribers on that bus
        # (api.stream, transient_state.triggers, imagegen.integration) read
        # routing fields off ``event.payload``, so we duplicate the
        # SceneEvent's top-level identity into the payload dict. The
        # dedicated SceneEvent attributes are still populated for the
        # SceneIndexer, which accesses ``event.scene_id`` directly.
        merged: dict[str, object] = {
            "campaign_id": scene.campaign_id,
            "scene_id": scene.id,
            **payload,
        }
        if type_ == SCENE_ENDED:
            merged.setdefault("location_ref", scene.location_ref)
            merged.setdefault("present_character_refs", list(scene.present_character_refs))
        await self.event_bus.emit(
            SceneEvent(
                type=type_,
                campaign_id=scene.campaign_id,
                scene_id=scene.id,
                payload=merged,
            )
        )

    def _scene_file_paths(self, scene: Scene) -> tuple[Path, Path]:
        return scene_paths(
            self.data_root,
            scene,
            naming_pattern=self.config.files.scene_naming_pattern,
        )

    def _records_for(self, scene_id: str) -> dict[str, _PostRecord]:
        return self._post_records.setdefault(scene_id, {})

    def _hydrate_records(self, scene: Scene) -> dict[str, _PostRecord]:
        """Lazily load ``_PostRecord`` entries from the sidecar's ``posts:`` block.

        After process restart the in-memory cache is empty; the sidecar is the
        source of truth for post identity. Each scene is hydrated at most once
        per process lifetime (the local cache is then authoritative).
        """
        cached = self._post_records.get(scene.id)
        if scene.id in self._records_hydrated:
            return cached if cached is not None else self._records_for(scene.id)
        _, yaml_path = self._scene_file_paths(scene)
        loaded = read_sidecar_post_records(yaml_path)
        # If the cache already has entries (e.g., writes happened between
        # construction and the first read), merge — in-memory wins.
        merged = {**loaded, **(cached or {})}
        self._post_records[scene.id] = merged
        self._records_hydrated.add(scene.id)
        return merged

    def _write_sidecar(
        self,
        scene: Scene,
        records: dict[str, _PostRecord] | None = None,
    ) -> None:
        """Persist ``scene`` and the in-memory post records together.

        Centralizing this keeps the sidecar's ``posts:`` block in sync — every
        mutation that writes the sidecar must include the live records.
        Post mutations pass ``records`` explicitly (a staged copy) so the
        in-memory cache can be committed only after both files are on disk
        (#586).
        """
        if records is None:
            # Ensure the records are loaded from disk before overwriting,
            # otherwise a sidecar-update-without-post-mutation (e.g., set_pov)
            # would drop the persisted block on the floor.
            self._hydrate_records(scene)
            records = self._records_for(scene.id)
        _, yaml_path = self._scene_file_paths(scene)
        write_sidecar(yaml_path, scene, post_records=records)

    # -- CRUD / read-only ------------------------------------------------

    async def list_scenes(self, campaign_id: str) -> list[Scene]:
        directory = scenes_dir(self.data_root, campaign_id)
        if not directory.exists():
            return []
        scenes: list[Scene] = []
        for yaml_path in sorted(directory.glob("*.yaml")):
            scenes.append(read_sidecar(yaml_path))
        scenes.sort(key=lambda s: s.ordinal)
        return scenes

    async def get_scene(self, scene_id: str) -> Scene:
        for campaign_id, active_id in list(self._active_scene.items()):
            if active_id == scene_id:
                for scene in await self.list_scenes(campaign_id):
                    if scene.id == scene_id:
                        return scene
        # Fallback: search every campaign directory.
        campaigns_root = self.data_root / "campaigns"
        if campaigns_root.exists():
            for campaign_dir in campaigns_root.iterdir():
                if not campaign_dir.is_dir():
                    continue
                for scene in await self.list_scenes(campaign_dir.name):
                    if scene.id == scene_id:
                        return scene
        raise KeyError(f"scene not found: {scene_id}")

    async def get_scene_file_path(self, scene_id: str) -> Path:
        scene = await self.get_scene(scene_id)
        md_path, _ = self._scene_file_paths(scene)
        return md_path

    async def load_scene_body(self, scene_id: str) -> str:
        path = await self.get_scene_file_path(scene_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # -- Active scene tracking ------------------------------------------

    async def active_scene_for_campaign(self, campaign_id: str) -> Scene | None:
        scene_id = self._active_scene.get(campaign_id)
        if scene_id:
            try:
                return await self.get_scene(scene_id)
            except KeyError:
                self._active_scene.pop(campaign_id, None)
        # Fallback: latest unclosed scene by ordinal.
        scenes = await self.list_scenes(campaign_id)
        for scene in reversed(scenes):
            if not scene.closed:
                self._active_scene[campaign_id] = scene.id
                return scene
        return None

    async def active_scene_for_pc(self, campaign_id: str, pc_ref: str) -> Scene | None:
        scene_id = self._pc_current_scene.get((campaign_id, pc_ref))
        if scene_id:
            try:
                return await self.get_scene(scene_id)
            except KeyError:
                self._pc_current_scene.pop((campaign_id, pc_ref), None)
        # Fallback: latest open scene where the PC is present.
        for scene in reversed(await self.list_scenes(campaign_id)):
            if pc_ref in scene.present_pc_refs and not scene.closed:
                self._pc_current_scene[(campaign_id, pc_ref)] = scene.id
                return scene
        return None

    # -- Scene lifecycle -------------------------------------------------

    async def start_scene(self, init: SceneInit) -> Scene:
        title = init.title or (init.location_ref or "scene").replace("-", " ").title()
        slug = init.slug or slugify(title)
        ordinal = next_ordinal(self.data_root, init.campaign_id)
        scene_id = self._scene_id(init.campaign_id, ordinal, slug)
        scene = Scene(
            id=scene_id,
            campaign_id=init.campaign_id,
            ordinal=ordinal,
            slug=slug,
            title=title,
            location_ref=init.location_ref,
            in_game_start=init.in_game_start,
            greeting_id=init.greeting_id,
            pov_character_ref=init.pov_character_ref,
            present_character_refs=list(dict.fromkeys(init.present_character_refs)),
            present_pc_refs=list(dict.fromkeys(init.present_pc_refs)),
            declared_character_refs=list(dict.fromkeys(init.present_character_refs)),
            mood=init.mood,
            tags=list(init.tags),
        )

        md_path, _yaml_path = self._scene_file_paths(scene)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        if not md_path.exists():
            md_path.write_text("", encoding="utf-8")
        # Initialize the records cache so the sidecar persists an (empty)
        # posts: block right from the start.
        self._post_records.setdefault(scene.id, {})
        self._records_hydrated.add(scene.id)
        self._write_sidecar(scene)
        self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))

        self._active_scene[scene.campaign_id] = scene.id
        for pc_ref in scene.present_pc_refs:
            self._pc_current_scene[(scene.campaign_id, pc_ref)] = scene.id

        briefing_payload = await self._build_briefing_payload(scene)
        await self._emit(SCENE_STARTED, scene, briefing=briefing_payload)
        return scene

    async def _build_briefing_payload(self, scene: Scene) -> dict | None:
        """Ask Continuity for the active threads involving this scene's
        PCs so the Frontend can render a pre-scene briefing without an
        extra round-trip.

        Returns ``None`` when no continuity is wired or the call fails;
        the rest of ``start_scene`` keeps working without a briefing.
        """
        if self._continuity is None:
            return None
        try:
            from grimoire.continuity.registry import resolve_continuity

            service = resolve_continuity(self._continuity, scene.campaign_id)
            if service is None or not hasattr(service, "brief_for_scene"):
                return None
            briefing = await service.brief_for_scene(scene.id, list(scene.present_pc_refs))
        except Exception:
            return None
        return {
            "scene_id": briefing.scene_id,
            "pc_refs": list(briefing.pc_refs),
            "fact_count": len(briefing.facts),
            "fact_texts": [getattr(f, "text", "") for f in briefing.facts[:5]],
            "commitment_count": len(briefing.commitments),
            "overdue_count": len(briefing.overdue),
        }

    async def close_scene(self, scene_id: str, *, closed_at_turn: str) -> SceneCloseReport:
        """Close a scene; ``closed_at_turn`` is the orchestrator's turn id.

        Required so downstream audit queries (``WHERE closed_at_turn = ?``)
        always have a value. Callers without a turn id (admin tools, tests)
        can synthesize one (``"manual"``, a UUID, etc.).
        """
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            if scene.closed:
                paid_off_texts = {t.text for t in scene.threads_paid_off}
                return SceneCloseReport(
                    scene=scene,
                    final_summary=scene.final_summary or scene.running_summary or "",
                    key_beats=list(scene.key_beats),
                    threads_resolved=list(scene.threads_paid_off),
                    threads_unresolved=[
                        t for t in scene.threads_introduced if t.text not in paid_off_texts
                    ],
                )

            posts = await self.get_posts(scene_id)
            final_override = await self._campaign_final_on_close(scene.campaign_id)
            if self._should_run_final_summary(final_on_close_override=final_override):
                final_summary, key_beats = await self._final_summary(scene, posts)
            else:
                final_summary = scene.running_summary or ""
                key_beats = []
                await self._emit(
                    "summary_skipped",
                    scene,
                    reason="final_on_close_disabled",
                )
            scene.final_summary = final_summary
            scene.key_beats = list(key_beats)
            scene.closed = True
            scene.closed_at_turn = closed_at_turn
            if scene.in_game_end is None:
                scene.in_game_end = scene.in_game_start

            self._write_sidecar(scene)

            paid_off_texts = {t.text for t in scene.threads_paid_off}
            unresolved = [t for t in scene.threads_introduced if t.text not in paid_off_texts]
            report = SceneCloseReport(
                scene=scene,
                final_summary=final_summary,
                key_beats=list(key_beats),
                threads_resolved=list(scene.threads_paid_off),
                threads_unresolved=unresolved,
            )
            await self._emit(
                SCENE_ENDED,
                scene,
                final_summary=final_summary,
                key_beats=list(key_beats),
                threads_unresolved=[t.text for t in unresolved],
                closed_at_turn=closed_at_turn,
            )
            if self._active_scene.get(scene.campaign_id) == scene.id:
                self._active_scene.pop(scene.campaign_id, None)
            return report

    async def delete_scene(self, scene_id: str) -> None:
        """Permanently delete a scene: removes files, emits event for index cleanup."""
        scene = await self.get_scene(scene_id)
        md_path, yaml_path = self._scene_file_paths(scene)

        if md_path.exists():
            md_path.unlink()
        if yaml_path.exists():
            yaml_path.unlink()

        self._post_records.pop(scene_id, None)
        self._records_hydrated.discard(scene_id)
        self._known_body_hashes.pop(scene_id, None)
        if self._active_scene.get(scene.campaign_id) == scene_id:
            self._active_scene.pop(scene.campaign_id, None)
        for pc_key, sid in list(self._pc_current_scene.items()):
            if sid == scene_id:
                self._pc_current_scene.pop(pc_key, None)
        self._locks.pop(scene_id, None)

        await self._emit(SCENE_DELETED, scene)
        logger.info("deleted scene %s", scene_id)

    async def _final_summary(self, scene: Scene, posts: list[Post]) -> tuple[str, list[str]]:
        if self._final_summarizer is not None:
            return await self._final_summarizer(scene, posts)
        # Fallback: derive a trivial summary from running summary + first/last
        # lines so the module is usable without an LLM in tests.
        base = scene.running_summary or ""
        if not base and posts:
            first = posts[0].body.split("\n", 1)[0]
            last = posts[-1].body.split("\n", 1)[0]
            base = f"{first} … {last}"
        return base, []

    async def generate_summary(
        self,
        scene_id: str,
        *,
        force: bool = False,
    ) -> tuple[str, list[str]]:
        """Generate a summary on demand for any scene (open or closed)."""
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)

            if scene.closed and scene.final_summary and not force:
                return scene.final_summary, list(scene.key_beats)

            posts = await self.get_posts(scene_id)
            if not posts:
                return "", []

            if self._adaptive_summarizer is not None:
                summary, key_beats = await self._adaptive_summarizer(scene, posts)
            else:
                summary, key_beats = await self._final_summary(scene, posts)

            if scene.closed:
                scene.final_summary = summary
            else:
                scene.running_summary = summary
            scene.key_beats = list(key_beats)
            self._write_sidecar(scene)
            return summary, key_beats

    async def analyze_scene(
        self,
        scene_id: str,
        *,
        force: bool = False,
    ) -> SceneAnalysisResult:
        """Run bundled scene analysis: summary + extraction in one LLM pass."""
        if self._scene_analyzer is None:
            raise RuntimeError("scene analyzer not configured")

        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)

            has_summary = (scene.closed and scene.final_summary) or (
                not scene.closed and scene.running_summary
            )
            if has_summary and not force:
                return SceneAnalysisResult(
                    summary=(scene.final_summary if scene.closed else scene.running_summary) or "",
                    key_beats=list(scene.key_beats),
                    threads_introduced=list(scene.threads_introduced),
                    threads_paid_off=list(scene.threads_paid_off),
                )

            posts = await self.get_posts(scene_id)
            if not posts:
                return SceneAnalysisResult()

            result = await self._scene_analyzer(scene, posts, scene.campaign_id)

            if result.summary:
                if scene.closed:
                    scene.final_summary = result.summary
                else:
                    scene.running_summary = result.summary
                scene.key_beats = list(result.key_beats)

            for thread in result.threads_introduced:
                if thread not in scene.threads_introduced:
                    scene.threads_introduced.append(thread)
            for thread in result.threads_paid_off:
                if thread not in scene.threads_paid_off:
                    scene.threads_paid_off.append(thread)

            self._write_sidecar(scene)
            return result

    # -- Posts -----------------------------------------------------------

    async def append_post(self, scene_id: str, post: Post) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            if scene.closed:
                raise RuntimeError(f"cannot append to closed scene {scene_id}")

            # Assign / validate order
            next_order = scene.post_count + 1
            if post.order_in_scene == 0:
                post = replace(post, order_in_scene=next_order, scene_id=scene_id)
            elif post.order_in_scene != next_order:
                raise ValueError(
                    f"post order {post.order_in_scene} does not match expected {next_order}"
                )
            else:
                post = replace(post, scene_id=scene_id)

            md_path, yaml_path = self._scene_file_paths(scene)

            scene.post_count = post.order_in_scene
            if post.author_kind == AuthorKind.PC and post.author_pc_ref:
                if post.author_pc_ref not in scene.present_pc_refs:
                    scene.present_pc_refs.append(post.author_pc_ref)
                if post.author_pc_ref not in scene.present_character_refs:
                    scene.present_character_refs.append(post.author_pc_ref)
            elif post.author_kind == AuthorKind.NPC and post.author_npc_ref:
                if post.author_npc_ref not in scene.present_character_refs:
                    scene.present_character_refs.append(post.author_npc_ref)

            # Stage the new record on a copy so the sidecar serializes it but
            # the live cache only changes once both files are on disk (#586:
            # files first, memory last).
            records = dict(self._hydrate_records(scene))
            records[str(post.order_in_scene)] = _PostRecord(
                id=post.id,
                turn_id=post.turn_id,
                created_at=post.created_at,
                is_player=post.is_player,
            )
            with scene_files_transaction(md_path, yaml_path):
                append_post_to_body(
                    md_path,
                    post,
                    heading_pattern=self.config.files.post_heading_pattern,
                )
                self._write_sidecar(scene, records=records)

            self._post_records[scene_id] = records
            if post.author_kind == AuthorKind.PC and post.author_pc_ref:
                self._pc_current_scene[(scene.campaign_id, post.author_pc_ref)] = scene.id
            self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))

            await self._emit(
                POST_APPENDED,
                scene,
                order=post.order_in_scene,
                author=post.author_label,
                post_id=post.id,
                turn_id=post.turn_id,
                is_player=post.is_player,
            )
            if post.author_kind == AuthorKind.PC:
                await self._emit(
                    PC_POST_APPENDED,
                    scene,
                    order=post.order_in_scene,
                    pc_ref=post.author_pc_ref,
                )

            # Cadence-based running summary kicks off out-of-band so a slow LLM
            # call doesn't block the next post append. Tests still drive
            # ``update_running_summary`` directly for the legacy inline path.
            override = await self._campaign_summary_cadence(scene.campaign_id)
            if self._should_emit_running_summary(
                post_count=scene.post_count,
                override=override,
            ):
                await self._emit(
                    RUNNING_SUMMARY_DUE,
                    scene,
                    post_count=scene.post_count,
                )
            elif override is not None and override <= 0:
                default_n = self.config.running_summary_every_n_posts
                if default_n > 0 and scene.post_count > 0 and scene.post_count % default_n == 0:
                    await self._emit(
                        "summary_skipped",
                        scene,
                        reason="running_cadence_disabled",
                        post_count=scene.post_count,
                    )

    async def get_posts(self, scene_id: str, range: tuple[int, int] | None = None) -> list[Post]:
        scene = await self.get_scene(scene_id)
        md_path, _ = self._scene_file_paths(scene)
        records = self._hydrate_records(scene)
        posts: list[Post] = []
        for order, kind, pc_ref, npc_ref, body in read_posts(md_path, scene_id):
            record = records.get(str(order))
            post_id = record.id if record else f"{scene_id}#post-{order}"
            self._post_scene_index[post_id] = scene_id
            posts.append(
                Post(
                    id=post_id,
                    scene_id=scene_id,
                    order_in_scene=order,
                    author_kind=kind,
                    author_pc_ref=pc_ref,
                    author_npc_ref=npc_ref,
                    body=body,
                    is_player=record.is_player if record else False,
                    created_at=record.created_at if record else datetime.fromtimestamp(0),
                    turn_id=record.turn_id if record else "",
                    alternates=list(record.alternates) if record else [],
                    primary_alternate_id=record.primary_alternate_id if record else None,
                )
            )
        if range is not None:
            start, end = range
            posts = [p for p in posts if start <= p.order_in_scene <= end]
        return posts

    async def posts_since_last_advance(self, scene_id: str) -> list[Post]:
        scene = await self.get_scene(scene_id)
        posts = await self.get_posts(scene_id)
        return [p for p in posts if p.order_in_scene > scene.last_advance_at_post]

    async def get_posts_paginated(
        self,
        scene_id: str,
        *,
        limit: int = 50,
        before: int | None = None,
        db: Any = None,
    ) -> list[Post]:
        """Return posts from the SQLite index, paginated by order_in_scene."""
        if db is None:
            raise ValueError("db is required for paginated reads")
        if before is not None:
            rows = await db.fetchall(
                "SELECT * FROM posts WHERE scene_id = ? AND order_in_scene < ? "
                "ORDER BY order_in_scene DESC LIMIT ?",
                (scene_id, before, limit),
            )
        else:
            rows = await db.fetchall(
                "SELECT * FROM posts WHERE scene_id = ? ORDER BY order_in_scene DESC LIMIT ?",
                (scene_id, limit),
            )
        return [
            Post(
                id=r["id"],
                scene_id=r["scene_id"],
                order_in_scene=r["order_in_scene"],
                author_kind=r["author_kind"],
                author_pc_ref=r["author_pc_ref"],
                author_npc_ref=r["author_npc_ref"],
                body=r["body"],
                is_player=bool(r["is_player"]),
                created_at=r["created_at"] or "",
                turn_id=r["turn_id"] or "",
                alternates=[],
                primary_alternate_id=None,
            )
            for r in reversed(rows)
        ]

    async def recent_posts(self, scene_id: str, n: int = 10) -> list[Post]:
        posts = await self.get_posts(scene_id)
        if n <= 0:
            return []
        return posts[-n:]

    # -- Presence --------------------------------------------------------

    async def add_present_character(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            already_present = character_ref in scene.present_character_refs
            # Manual / confirmed-cast-change presence is durable: record the ref
            # in the declared cast so a later truncate_scene_from (which only
            # drops *post-derived* NPCs) keeps a character a user or cast change
            # explicitly added, even once its authored posts are gone. Legacy
            # scenes (declared_character_refs is None) drop nothing on truncate,
            # so they need no marking.
            newly_declared = (
                scene.declared_character_refs is not None
                and character_ref not in scene.declared_character_refs
            )
            if already_present and not newly_declared:
                return
            if not already_present:
                scene.present_character_refs.append(character_ref)
            if newly_declared:
                scene.declared_character_refs.append(character_ref)
            self._write_sidecar(scene)

    async def remove_present_character(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            # Match by canonical form so a character removed under one spelling
            # (e.g. the canonical ref the frontend sends) clears an entry the
            # scene stored under an equivalent shorthand (#517).
            canon = canonicalize_character_ref(character_ref)
            changed = False
            present_matches = [
                r for r in scene.present_character_refs if canonicalize_character_ref(r) == canon
            ]
            for r in present_matches:
                scene.present_character_refs.remove(r)
                changed = True
            # A character that leaves is no longer durably declared: drop it from
            # declared_character_refs too. Otherwise a stale declared entry makes
            # truncate_scene_from treat a later *post-derived* re-entry as durable
            # and resurrect a cast member whose only surviving evidence was the
            # post being deleted.
            if scene.declared_character_refs is not None:
                declared_matches = [
                    r
                    for r in scene.declared_character_refs
                    if canonicalize_character_ref(r) == canon
                ]
                for r in declared_matches:
                    scene.declared_character_refs.remove(r)
                    changed = True
            pc_matches = [
                r for r in scene.present_pc_refs if canonicalize_character_ref(r) == canon
            ]
            was_pc = bool(pc_matches)
            for r in pc_matches:
                scene.present_pc_refs.remove(r)
                changed = True
                self._pc_current_scene.pop((scene.campaign_id, r), None)
            # §8 — when a PC leaves and we drop to ≤1 PCs, auto-respond resumes.
            # The advance watermark must catch up to the current post count so
            # the now-single PC's pending posts don't get treated as fresh
            # input on the next on_post_submitted call.
            flush = was_pc and len(scene.present_pc_refs) <= 1
            if flush:
                scene.last_advance_at_post = scene.post_count
            if changed:
                self._write_sidecar(scene)
                if flush:
                    await self._emit(
                        ADVANCE_ENABLED,
                        scene,
                        flushed_to_post=scene.post_count,
                    )

    async def add_present_pc(self, scene_id: str, pc_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            previous = len(scene.present_pc_refs)
            if pc_ref not in scene.present_pc_refs:
                scene.present_pc_refs.append(pc_ref)
            if pc_ref not in scene.present_character_refs:
                scene.present_character_refs.append(pc_ref)
            self._pc_current_scene[(scene.campaign_id, pc_ref)] = scene.id
            self._write_sidecar(scene)
            if previous <= 1 and len(scene.present_pc_refs) >= 2:
                pending = scene.post_count - scene.last_advance_at_post
                payload: dict = {}
                if self.config.multi_pc.show_pending_count_in_ui:
                    payload["pending_count"] = pending
                await self._emit(ADVANCE_DISABLED, scene, **payload)

    async def set_pov(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            scene.pov_character_ref = character_ref
            if character_ref not in scene.present_character_refs:
                scene.present_character_refs.append(character_ref)
            self._write_sidecar(scene)

    # -- Cast-change review (#464) --------------------------------------

    async def queue_cast_change(
        self,
        scene_id: str,
        *,
        character_ref: str,
        change: CastChange,
        is_pc: bool,
        evidence: str = "",
        confidence: float = 0.0,
        turn_id: str | None = None,
    ) -> str:
        """Persist a resolved cast change for review (never auto-applied).

        Deduplicates against existing pending rows: if the same
        ``(scene_id, character_ref, change)`` is already awaiting review, the
        existing id is returned rather than queuing a duplicate prompt.
        """
        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        scene = await self.get_scene(scene_id)
        # Serialize the check-then-insert under the per-scene lock (like every
        # other mutating method here) so concurrent turns can't both pass
        # find_pending and insert duplicate pending rows for the same triple.
        async with self._lock_for(scene_id):
            existing = await self._cast_change_store.find_pending(scene_id, character_ref, change)
            if existing is not None:
                return existing.id
            return await self._cast_change_store.add(
                campaign_id=scene.campaign_id,
                scene_id=scene_id,
                character_ref=character_ref,
                change=change,
                is_pc=is_pc,
                evidence=evidence,
                confidence=confidence,
                turn_id=turn_id,
            )

    async def list_pending_cast_changes(self, scene_id: str) -> list:
        if self._cast_change_store is None:
            return []
        return await self._cast_change_store.list_pending(scene_id)

    async def list_confirmed_cast_changes(self, scene_id: str) -> list:
        if self._cast_change_store is None:
            return []
        lister = getattr(self._cast_change_store, "list_confirmed", None)
        if lister is None:
            return []
        return await lister(scene_id)

    async def confirm_cast_change(self, scene_id: str, change_id: str) -> None:
        """Apply a pending cast change through the presence APIs and mark it
        confirmed. Idempotent presence methods make a stale confirm safe."""
        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        rec = await self._cast_change_store.get(change_id)
        if rec is None or rec.scene_id != scene_id:
            raise KeyError(f"cast change not found: {change_id}")
        if rec.status != "pending":
            raise ValueError(f"cast change {change_id} already {rec.status}")
        if rec.change == CastChange.ENTER:
            if rec.is_pc:
                await self.add_present_pc(scene_id, rec.character_ref)
            else:
                await self.add_present_character(scene_id, rec.character_ref)
        else:
            await self.remove_present_character(scene_id, rec.character_ref)
        await self._cast_change_store.set_status(change_id, "confirmed")

    async def dismiss_cast_change(self, scene_id: str, change_id: str) -> None:
        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        rec = await self._cast_change_store.get(change_id)
        if rec is None or rec.scene_id != scene_id:
            raise KeyError(f"cast change not found: {change_id}")
        if rec.status != "pending":
            raise ValueError(f"cast change {change_id} already {rec.status}")
        await self._cast_change_store.set_status(change_id, "dismissed")

    async def set_narrator_response_mode(
        self,
        scene_id: str,
        mode: str | None,
    ) -> Scene:
        """Update the per-scene narrator response mode override.

        ``mode=None`` clears the override (scene falls back to the
        campaign default). Unknown values are rejected by the resolver
        so callers — typically the REST layer — should validate before
        passing them in.
        """
        from grimoire.scenes.narrator_mode import normalize_response_mode

        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            scene.narrator_response_mode = None if mode is None else normalize_response_mode(mode)
            self._write_sidecar(scene)
            return scene

    # -- Decisions -------------------------------------------------------

    async def is_scene_break(
        self,
        scene_id: str | None,
        player_input: str,
        *,
        now_in_game: datetime | None = None,
        proposed_present_cast: list[str] | None = None,
        proposed_location_ref: str | None = None,
    ) -> SceneBreakDecision:
        scene = await self.get_scene(scene_id) if scene_id else None
        decision = detect_scene_break(
            scene,
            player_input,
            now_in_game=now_in_game,
            proposed_present_cast=proposed_present_cast,
            proposed_location_ref=proposed_location_ref,
            config=self.config.boundary,
        )
        # §7 — optional LLM refinement for borderline-confidence heuristic
        # results. Auto-break (>= auto_threshold) and clearly-not-break
        # (< prompt_threshold) decisions are left alone. The classifier
        # never sees the entire post history; we cap it at the recent window
        # used elsewhere so a long scene doesn't blow the model's context.
        if self._scene_break_classifier is None or scene is None:
            return decision
        cfg = self.config.boundary
        ambiguous = (
            cfg.confidence_threshold_prompt - 0.1
            <= decision.confidence
            < cfg.confidence_threshold_auto
        )
        if not ambiguous:
            return decision
        try:
            recent = await self.recent_posts(scene.id, n=8)
            refined = await self._scene_break_classifier(scene, player_input, recent)
        except Exception:  # pragma: no cover - defensive; keep heuristic
            return decision
        return refined

    async def on_post_submitted(self, scene_id: str, post: Post) -> AdvanceDecision:
        scene = await self.get_scene(scene_id)
        present_pcs = scene.present_pc_refs
        if len(present_pcs) <= 1 or not self.config.require_advance_with_multiple_pcs:
            return AdvanceDecision(auto_respond=True, reason="single_pc_scene")
        return AdvanceDecision(auto_respond=False, reason="multi_pc_pending_advance")

    async def on_advance_requested(self, scene_id: str) -> AdvanceResult:
        async with self._metrics.measure("scene_manager", "scene_resolve"):
            return await self._on_advance_requested_inner(scene_id)

    async def _on_advance_requested_inner(self, scene_id: str) -> AdvanceResult:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            pending = await self.posts_since_last_advance(scene_id)
            if not pending:
                raise NothingToAdvance(scene_id)
            scene.last_advance_at_post = scene.post_count
            self._write_sidecar(scene)
            await self._emit(ADVANCE_REQUESTED, scene, post_count=scene.post_count)
            return AdvanceResult(scene=scene, pending_posts=pending)

    # -- Summarization ---------------------------------------------------

    async def _update_running_summary_locked(self, scene: Scene) -> None:
        if self._summarizer is None:
            return
        recent = await self.recent_posts(scene.id, n=8)
        try:
            new_summary = await self._summarizer(scene.running_summary, recent)
        except Exception:
            return
        scene.running_summary = new_summary
        self._write_sidecar(scene)
        await self._emit(RUNNING_SUMMARY_UPDATED, scene, summary=new_summary)

    async def update_running_summary(self, scene_id: str) -> str:
        """Explicit-trigger summary update — used by the background worker,
        tests, and admin endpoints. Append-driven scenes emit
        ``running_summary_due`` on the bus and the background worker calls
        this method to do the actual LLM work outside the append lock.
        """
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            await self._update_running_summary_locked(scene)
            return scene.running_summary or ""

    # -- Threads ---------------------------------------------------------

    async def add_thread(self, scene_id: str, thread: Thread, kind: str) -> None:
        if kind not in ("introduced", "paid_off"):
            raise ValueError(f"unknown thread kind: {kind}")
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            target = scene.threads_introduced if kind == "introduced" else scene.threads_paid_off
            if any(t.text == thread.text for t in target):
                return
            # Auto-stamp the provenance to the current post if the caller
            # didn't already set it. Continuity uses these refs to backlink
            # thread payoffs to their introducing post.
            if kind == "introduced" and thread.introduced_at_post is None:
                thread = Thread(
                    text=thread.text,
                    introduced_at_post=scene.post_count or None,
                    paid_off_at_post=thread.paid_off_at_post,
                )
            elif kind == "paid_off" and thread.paid_off_at_post is None:
                thread = Thread(
                    text=thread.text,
                    introduced_at_post=thread.introduced_at_post,
                    paid_off_at_post=scene.post_count or None,
                )
            target.append(thread)
            self._write_sidecar(scene)
            event = THREAD_INTRODUCED if kind == "introduced" else THREAD_PAID_OFF
            await self._emit(
                event,
                scene,
                thread=thread.text,
                introduced_at_post=thread.introduced_at_post,
                paid_off_at_post=thread.paid_off_at_post,
            )

    async def list_threads(self, scene_id: str) -> SceneThreads:
        scene = await self.get_scene(scene_id)
        return SceneThreads(
            introduced=list(scene.threads_introduced),
            paid_off=list(scene.threads_paid_off),
        )

    async def detect_threads(self, scene_id: str) -> list[tuple[Thread, str]]:
        """Invoke the configured LLM thread detector for this scene.

        Returns the list of ``(Thread, kind)`` tuples the detector produced —
        the caller is expected to feed them through :meth:`add_thread` (the
        detector itself is intentionally side-effect-free so a dry-run pass
        can preview proposals before persisting). When no detector is wired
        or thread detection is disabled, returns an empty list.
        """
        if not self.config.thread_detection.enabled or self._thread_detector is None:
            return []
        scene = await self.get_scene(scene_id)
        posts = await self.get_posts(scene_id)
        try:
            return await self._thread_detector(scene, posts)
        except Exception:  # pragma: no cover - defensive
            return []

    # -- Editing ---------------------------------------------------------

    async def edit_post(self, post_id: str, new_body: str, source: str) -> None:
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            posts = await self.get_posts(scene.id)
            updated: list[Post] = []
            for existing in posts:
                if existing.id == post_id or existing.order_in_scene == post.order_in_scene:
                    updated.append(replace(existing, body=new_body))
                else:
                    updated.append(existing)
            md_path, yaml_path = self._scene_file_paths(scene)
            with scene_files_transaction(md_path, yaml_path):
                write_body(
                    md_path,
                    updated,
                    heading_pattern=self.config.files.post_heading_pattern,
                )
            self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))
            await self._emit(
                POST_EDITED,
                scene,
                order=post.order_in_scene,
                source=source,
                post_id=post.id,
            )

    async def delete_post(self, post_id: str, source: str) -> None:
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            posts = await self.get_posts(scene.id)
            kept: list[Post] = []
            removed_order = post.order_in_scene
            for existing in posts:
                if existing.order_in_scene == removed_order:
                    continue
                if existing.order_in_scene > removed_order:
                    existing = replace(existing, order_in_scene=existing.order_in_scene - 1)
                kept.append(existing)
            md_path, yaml_path = self._scene_file_paths(scene)
            scene.post_count = len(kept)
            if scene.last_advance_at_post > scene.post_count:
                scene.last_advance_at_post = scene.post_count
            # Re-key record metadata for shifted posts on a staged copy; the
            # live cache commits only after both files are on disk (#586).
            shifted: dict[str, _PostRecord] = {}
            for key, record in self._hydrate_records(scene).items():
                order = int(key)
                if order == removed_order:
                    continue
                if order > removed_order:
                    shifted[str(order - 1)] = record
                else:
                    shifted[key] = record
            with scene_files_transaction(md_path, yaml_path):
                write_body(
                    md_path,
                    kept,
                    heading_pattern=self.config.files.post_heading_pattern,
                )
                self._write_sidecar(scene, records=shifted)
            self._post_records[scene.id] = shifted
            self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))
            await self._emit(
                POST_DELETED,
                scene,
                order=removed_order,
                source=source,
                post_id=post_id,
            )

    async def truncate_scene_from(self, post_id: str, source: str) -> list[Post]:
        """Delete ``post_id`` and every post after it in the same scene.

        Suffix sibling of :meth:`delete_post`: because the removed posts are a
        contiguous tail, no order-shifting is needed. Rewrites the ``.md`` and
        sidecar, updates counts, drops the removed records, emits one
        ``POST_DELETED`` per removed post, and returns the removed posts.
        """
        scene, target = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            posts = await self.get_posts(scene.id)
            cut = target.order_in_scene
            kept = [p for p in posts if p.order_in_scene < cut]
            removed = [p for p in posts if p.order_in_scene >= cut]
            md_path, yaml_path = self._scene_file_paths(scene)
            scene.post_count = len(kept)
            # Roll the advance watermark back to the last surviving model
            # response. Merely clamping to post_count would leave PC inputs whose
            # response we just deleted marked "already advanced"
            # (posts_since_last_advance empty → NothingToAdvance); rolling back to
            # the last non-player post lets the user re-advance the still-visible
            # inputs whose batch lost its response.
            last_model_order = max((p.order_in_scene for p in kept if not p.is_player), default=0)
            if scene.last_advance_at_post > last_model_order:
                scene.last_advance_at_post = last_model_order
            # Derived summaries/threads can reference the now-deleted prose. Drop
            # the running summary + key beats (they regenerate on the next
            # cadence) and prune thread anchors beyond the new post_count so
            # context assembly and the HUD don't surface events from removed
            # posts. (Final summary is untouched — delete is rejected on closed
            # scenes.)
            if scene.running_summary:
                scene.running_summary = None
            if scene.key_beats:
                scene.key_beats = []
            scene.threads_introduced = [
                t
                for t in scene.threads_introduced
                if t.introduced_at_post is None or t.introduced_at_post <= scene.post_count
            ]
            scene.threads_paid_off = [
                t
                for t in scene.threads_paid_off
                if t.paid_off_at_post is None or t.paid_off_at_post <= scene.post_count
            ]
            # Undo the cast additions ``append_post`` made for removed NPC posts:
            # an NPC author is added to ``present_character_refs`` when it first
            # posts, so dropping its last surviving post must drop it too (else
            # the HUD/speaker loop keeps a character whose posts are gone). But
            # only for NPCs whose presence was post-derived — never drop one
            # that was in the scene's declared cast. PCs are durable
            # participants and are left untouched. ``declared_character_refs is
            # None`` means a legacy sidecar with unknown initial cast, so we
            # stay conservative and drop nothing.
            declared = scene.declared_character_refs
            if declared is not None:
                declared_set = set(declared)
                removed_npcs = {
                    p.author_npc_ref
                    for p in removed
                    if p.author_kind == AuthorKind.NPC and p.author_npc_ref
                }
                if removed_npcs:
                    kept_npcs = {
                        p.author_npc_ref
                        for p in kept
                        if p.author_kind == AuthorKind.NPC and p.author_npc_ref
                    }
                    drop = removed_npcs - kept_npcs - declared_set
                    if drop:
                        scene.present_character_refs = [
                            ref for ref in scene.present_character_refs if ref not in drop
                        ]
            # Drop removed records on a staged copy; the live cache commits
            # only after both files are on disk (#586).
            kept_records = {
                key: rec for key, rec in self._hydrate_records(scene).items() if int(key) < cut
            }
            with scene_files_transaction(md_path, yaml_path):
                write_body(
                    md_path,
                    kept,
                    heading_pattern=self.config.files.post_heading_pattern,
                )
                self._write_sidecar(scene, records=kept_records)
            self._post_records[scene.id] = kept_records
            self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))
            for removed_post in removed:
                await self._emit(
                    POST_DELETED,
                    scene,
                    order=removed_post.order_in_scene,
                    source=source,
                    post_id=removed_post.id,
                )
        return removed

    async def _find_post(self, post_id: str) -> tuple[Scene, Post]:
        # Fast path: a prior get_posts populated the post_id -> scene_id
        # index. Verify the post still exists in that scene (records on
        # disk could have been retconned out from under us).
        cached_scene_id = self._post_scene_index.get(post_id)
        if cached_scene_id is not None:
            try:
                scene = await self.get_scene(cached_scene_id)
            except KeyError:
                self._post_scene_index.pop(post_id, None)
            else:
                for post in await self.get_posts(scene.id):
                    if post.id == post_id:
                        return scene, post
                self._post_scene_index.pop(post_id, None)

        # Slow path: walk active + known scenes first, then fall back to
        # the full filesystem scan. Each scene we read populates the
        # index, so subsequent _find_post calls hit the fast path above.
        candidates: list[Scene] = []
        for scene_id in {*(s for s in self._active_scene.values()), *self._post_records.keys()}:
            try:
                candidates.append(await self.get_scene(scene_id))
            except KeyError:
                continue
        searched_ids = {s.id for s in candidates}
        for scene in candidates:
            for post in await self.get_posts(scene.id):
                if post.id == post_id:
                    return scene, post
        campaigns_root = self.data_root / "campaigns"
        if campaigns_root.exists():
            for campaign_dir in campaigns_root.iterdir():
                if not campaign_dir.is_dir():
                    continue
                for scene in await self.list_scenes(campaign_dir.name):
                    if scene.id in searched_ids:
                        continue
                    searched_ids.add(scene.id)
                    for post in await self.get_posts(scene.id):
                        if post.id == post_id:
                            return scene, post
        raise KeyError(f"post not found: {post_id}")

    # -- Alternates (swipes) --------------------------------------------

    async def append_alternate(self, post_id: str, alternate: Alternate) -> None:
        """Add an alternate to a post's sidecar record and persist.

        Does not change the primary pointer. If the post has no alternates yet
        (legacy state), an implicit alternate representing the existing body is
        synthesized as the primary so the new alternate is non-primary.
        """
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            records = self._hydrate_records(scene)
            record = records.get(str(post.order_in_scene))
            if record is None:
                raise KeyError(f"no post record for {post_id}")
            if not record.alternates:
                implicit = Alternate(
                    id=new_id("a", length=16),
                    post_id=post_id,
                    text=post.body,
                    delta_set_id="",
                    author_kind=post.author_kind,
                    created_at=record.created_at,
                    is_primary=True,
                )
                record.alternates.append(implicit)
                record.primary_alternate_id = implicit.id
            record.alternates.append(alternate)
            self._write_sidecar(scene)

    async def set_primary_alternate(self, post_id: str, alternate_id: str) -> None:
        """Switch which alternate is primary. Does not rebuild the .md — call
        :meth:`rebuild_md_from_primaries` after.
        """
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            records = self._hydrate_records(scene)
            record = records.get(str(post.order_in_scene))
            if record is None:
                raise KeyError(f"no post record for {post_id}")
            if not any(a.id == alternate_id for a in record.alternates):
                raise KeyError(f"alternate {alternate_id} not found on post {post_id}")
            for a in record.alternates:
                a.is_primary = a.id == alternate_id
            record.primary_alternate_id = alternate_id
            self._write_sidecar(scene)

    async def update_alternate(self, post_id: str, alternate_id: str, **changes: object) -> None:
        """Patch fields on an alternate (e.g. ``pinned=True``)."""
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            records = self._hydrate_records(scene)
            record = records.get(str(post.order_in_scene))
            if record is None:
                raise KeyError(f"no post record for {post_id}")
            for alt in record.alternates:
                if alt.id == alternate_id:
                    for k, v in changes.items():
                        if hasattr(alt, k):
                            setattr(alt, k, v)
                    break
            else:
                raise KeyError(f"alternate {alternate_id} not found on post {post_id}")
            self._write_sidecar(scene)

    async def remove_alternate(self, post_id: str, alternate_id: str) -> Alternate:
        """Drop an alternate from a post. Rejects removing the primary."""
        scene, post = await self._find_post(post_id)
        async with self._lock_for(scene.id):
            records = self._hydrate_records(scene)
            record = records.get(str(post.order_in_scene))
            if record is None:
                raise KeyError(f"no post record for {post_id}")
            if record.primary_alternate_id == alternate_id:
                raise ValueError(f"cannot remove primary alternate {alternate_id}; switch first")
            removed: Alternate | None = None
            kept: list[Alternate] = []
            for a in record.alternates:
                if a.id == alternate_id:
                    removed = a
                else:
                    kept.append(a)
            if removed is None:
                raise KeyError(f"alternate {alternate_id} not found on post {post_id}")
            record.alternates = kept
            self._write_sidecar(scene)
            return removed

    async def rebuild_md_from_primaries(self, scene_id: str) -> None:
        """Rewrite the scene's ``.md`` file using each post's primary alternate.

        Posts without alternates render their existing body (legacy + user
        posts). Posts with alternates render the primary's ``text``; if the
        primary is missing for any reason, fall back to the first alternate.
        """
        scene = await self.get_scene(scene_id)
        async with self._lock_for(scene.id):
            records = self._hydrate_records(scene)
            md_path, _ = self._scene_file_paths(scene)
            existing_posts = await self.get_posts(scene_id)
            rebuilt: list[Post] = []
            for post in existing_posts:
                record = records.get(str(post.order_in_scene))
                body = post.body
                if record and record.alternates:
                    primary = next(
                        (a for a in record.alternates if a.id == record.primary_alternate_id),
                        None,
                    )
                    if primary is None:
                        primary = next(
                            (a for a in record.alternates if a.is_primary),
                            record.alternates[0],
                        )
                    body = primary.text
                rebuilt.append(
                    Post(
                        id=post.id,
                        scene_id=post.scene_id,
                        order_in_scene=post.order_in_scene,
                        author_kind=post.author_kind,
                        author_pc_ref=post.author_pc_ref,
                        author_npc_ref=post.author_npc_ref,
                        body=body,
                        is_player=post.is_player,
                        created_at=post.created_at,
                        turn_id=post.turn_id,
                        alternates=post.alternates,
                        primary_alternate_id=post.primary_alternate_id,
                    )
                )
            write_body(
                md_path,
                rebuilt,
                heading_pattern=self.config.files.post_heading_pattern,
            )
            self._known_body_hashes[scene.id] = content_hash(md_path.read_text(encoding="utf-8"))

    # -- File-watcher hook ----------------------------------------------

    async def reindex_from_disk(self, scene_id: str) -> Scene:
        """Re-read a scene's files from disk.

        Called by the watcher (task #9) when the user edits a scene file
        directly. We rebuild ``post_count`` from the markdown and emit a
        ``scene_file_changed`` event so consumers can refresh.

        Conflict policy: last-write-wins. If the on-disk body hash doesn't
        match the hash we last wrote, the ``scene_file_changed`` event
        includes ``conflict=True`` so the frontend can surface a warning;
        the disk version is still accepted as the new source of truth.
        """
        scene = await self.get_scene(scene_id)
        md_path, yaml_path = self._scene_file_paths(scene)
        body_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        new_hash = content_hash(body_text)
        prior_hash = self._known_body_hashes.get(scene_id)
        conflict = prior_hash is not None and prior_hash != new_hash
        if yaml_path.exists():
            scene = read_sidecar(yaml_path)
        posts = read_posts(md_path, scene_id) if md_path.exists() else []
        scene.post_count = len(posts)
        # Drop in-memory records that no longer correspond to a parsed post
        # (e.g., the user deleted a heading on disk) and reload from the
        # sidecar's posts: block, which the watcher path doesn't update.
        self._records_hydrated.discard(scene_id)
        self._hydrate_records(scene)
        records = self._records_for(scene_id)
        valid_orders = {str(p[0]) for p in posts}
        for stale in [k for k in records if k not in valid_orders]:
            records.pop(stale, None)
        self._write_sidecar(scene)
        self._known_body_hashes[scene_id] = new_hash
        await self._emit(
            SCENE_FILE_CHANGED,
            scene,
            post_count=scene.post_count,
            conflict=conflict,
        )
        return scene

    async def hydrate_post_records(self, scene_id: str) -> None:
        """Force-load the sidecar's ``posts:`` block into the in-memory cache.

        Useful on startup or after a fork so the first call to ``get_posts``
        returns durable identity without an extra disk round-trip.
        """
        scene = await self.get_scene(scene_id)
        self._records_hydrated.discard(scene_id)
        self._hydrate_records(scene)
        md_path, _ = self._scene_file_paths(scene)
        if md_path.exists():
            self._known_body_hashes[scene_id] = content_hash(md_path.read_text(encoding="utf-8"))


class NothingToAdvance(RuntimeError):
    def __init__(self, scene_id: str) -> None:
        super().__init__(f"no pending posts to advance in scene {scene_id}")
        self.scene_id = scene_id


def new_post(
    *,
    author_kind: AuthorKind,
    body: str,
    is_player: bool,
    turn_id: str | None = None,
    author_pc_ref: str | None = None,
    author_npc_ref: str | None = None,
    created_at: datetime | None = None,
) -> Post:
    """Convenience constructor for callers that don't want to mint IDs."""
    return Post(
        id=str(uuid.uuid4()),
        scene_id="",  # populated on append
        order_in_scene=0,  # populated on append
        author_kind=author_kind,
        author_pc_ref=author_pc_ref,
        author_npc_ref=author_npc_ref,
        body=body,
        is_player=is_player,
        created_at=created_at or datetime.now(UTC),
        turn_id=turn_id or str(uuid.uuid4()),
    )


# Re-export for convenience.
__all__ = [
    "InMemoryEventBus",
    "NothingToAdvance",
    "SceneManager",
    "SceneManagerConfig",
    "new_post",
]
