"""Scene Manager — owns the play history.

Implementation of spec 10. Deliberately depends only on the local filesystem
and the small ``EventBus`` / summarizer protocols defined here; the State
Store (task #8), watchdog (task #9), and event bus (task #3) plug in via
dependency injection once they exist.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from grimoire.scenes.boundary import BoundaryConfig, detect_scene_break
from grimoire.scenes.events import (
    ADVANCE_DISABLED,
    ADVANCE_ENABLED,
    ADVANCE_REQUESTED,
    PC_POST_APPENDED,
    POST_APPENDED,
    POST_DELETED,
    POST_EDITED,
    RUNNING_SUMMARY_UPDATED,
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
    next_ordinal,
    read_posts,
    read_sidecar,
    scene_basename,
    scene_paths,
    scenes_dir,
    slugify,
    write_body,
    write_sidecar,
)
from grimoire.scenes.types import (
    AdvanceDecision,
    AdvanceResult,
    AuthorKind,
    Post,
    Scene,
    SceneBreakDecision,
    SceneCloseReport,
    SceneInit,
    SceneThreads,
    Thread,
)

Summarizer = Callable[[str | None, list[Post]], Awaitable[str]]
FinalSummarizer = Callable[[Scene, list[Post]], Awaitable[tuple[str, list[str]]]]


class _NullEventBus:
    async def emit(self, event: SceneEvent) -> None:  # pragma: no cover - trivial
        return None


@dataclass
class SceneManagerConfig:
    running_summary_every_n_posts: int = 5
    boundary: BoundaryConfig = field(default_factory=BoundaryConfig)
    require_advance_with_multiple_pcs: bool = True


@dataclass
class _PostRecord:
    """In-memory metadata about a post that isn't kept in the markdown body."""

    id: str
    turn_id: str
    created_at: datetime
    is_player: bool


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.data_root = Path(data_root)
        self.config = config or SceneManagerConfig()
        self.event_bus: EventBus = event_bus or _NullEventBus()
        self._summarizer = summarizer
        self._final_summarizer = final_summarizer
        self._clock = clock

        # Per-scene in-memory state. Persisted lazily where needed.
        self._post_records: dict[str, dict[str, _PostRecord]] = {}
        # active scene per (campaign_id, branch_id) and per-PC current scene
        self._active_scene: dict[tuple[str, str], str] = {}
        self._pc_current_scene: dict[tuple[str, str], str] = {}  # (campaign_id, pc_ref) -> scene_id
        self._locks: dict[str, asyncio.Lock] = {}

    # -- helpers ---------------------------------------------------------

    def _lock_for(self, scene_id: str) -> asyncio.Lock:
        lock = self._locks.get(scene_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scene_id] = lock
        return lock

    def _scene_id(self, campaign_id: str, branch_id: str, ordinal: int, slug: str) -> str:
        prefix = "" if branch_id == "main" else f"{branch_id}:"
        return f"{prefix}{campaign_id}:{scene_basename(ordinal, slug)}"

    async def _emit(self, type_: str, scene: Scene, **payload: object) -> None:
        await self.event_bus.emit(
            SceneEvent(
                type=type_,
                campaign_id=scene.campaign_id,
                scene_id=scene.id,
                payload=dict(payload),
            )
        )

    def _scene_file_paths(self, scene: Scene) -> tuple[Path, Path]:
        return scene_paths(self.data_root, scene)

    def _records_for(self, scene_id: str) -> dict[str, _PostRecord]:
        return self._post_records.setdefault(scene_id, {})

    # -- CRUD / read-only ------------------------------------------------

    async def list_scenes(self, campaign_id: str, branch_id: str = "main") -> list[Scene]:
        directory = scenes_dir(self.data_root, campaign_id, branch_id)
        if not directory.exists():
            return []
        scenes: list[Scene] = []
        for yaml_path in sorted(directory.glob("*.yaml")):
            scenes.append(read_sidecar(yaml_path))
        scenes.sort(key=lambda s: s.ordinal)
        return scenes

    async def get_scene(self, scene_id: str) -> Scene:
        for (campaign_id, branch_id), active_id in list(self._active_scene.items()):
            if active_id == scene_id:
                for scene in await self.list_scenes(campaign_id, branch_id):
                    if scene.id == scene_id:
                        return scene
        # Fallback: search every (campaign, branch) directory.
        campaigns_root = self.data_root / "campaigns"
        if campaigns_root.exists():
            for campaign_dir in campaigns_root.iterdir():
                if not campaign_dir.is_dir():
                    continue
                for branch_id in self._known_branches(campaign_dir):
                    for scene in await self.list_scenes(campaign_dir.name, branch_id):
                        if scene.id == scene_id:
                            return scene
        raise KeyError(f"scene not found: {scene_id}")

    @staticmethod
    def _known_branches(campaign_dir: Path) -> list[str]:
        branches = ["main"]
        branches_dir = campaign_dir / "branches"
        if branches_dir.exists():
            branches.extend(sorted(p.name for p in branches_dir.iterdir() if p.is_dir()))
        return branches

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

    async def active_scene_for_campaign(
        self, campaign_id: str, branch_id: str = "main"
    ) -> Scene | None:
        scene_id = self._active_scene.get((campaign_id, branch_id))
        if scene_id:
            try:
                return await self.get_scene(scene_id)
            except KeyError:
                self._active_scene.pop((campaign_id, branch_id), None)
        # Fallback: latest unclosed scene by ordinal.
        scenes = await self.list_scenes(campaign_id, branch_id)
        for scene in reversed(scenes):
            if not scene.closed:
                self._active_scene[(campaign_id, branch_id)] = scene.id
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
        ordinal = next_ordinal(self.data_root, init.campaign_id, init.branch_id)
        scene_id = self._scene_id(init.campaign_id, init.branch_id, ordinal, slug)
        scene = Scene(
            id=scene_id,
            campaign_id=init.campaign_id,
            branch_id=init.branch_id,
            ordinal=ordinal,
            slug=slug,
            title=title,
            location_ref=init.location_ref,
            in_game_start=init.in_game_start,
            greeting_id=init.greeting_id,
            pov_character_ref=init.pov_character_ref,
            present_character_refs=list(dict.fromkeys(init.present_character_refs)),
            present_pc_refs=list(dict.fromkeys(init.present_pc_refs)),
            mood=init.mood,
            tags=list(init.tags),
        )

        md_path, yaml_path = self._scene_file_paths(scene)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        if not md_path.exists():
            md_path.write_text("", encoding="utf-8")
        write_sidecar(yaml_path, scene)

        self._active_scene[(scene.campaign_id, scene.branch_id)] = scene.id
        for pc_ref in scene.present_pc_refs:
            self._pc_current_scene[(scene.campaign_id, pc_ref)] = scene.id

        await self._emit(SCENE_STARTED, scene)
        return scene

    async def close_scene(
        self, scene_id: str, *, closed_at_turn: str | None = None
    ) -> SceneCloseReport:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            if scene.closed:
                return SceneCloseReport(
                    scene=scene,
                    final_summary=scene.final_summary or scene.running_summary or "",
                    key_beats=list(scene.key_beats),
                    threads_resolved=list(scene.threads_paid_off),
                    threads_unresolved=list(
                        t for t in scene.threads_introduced if t not in scene.threads_paid_off
                    ),
                )

            posts = await self.get_posts(scene_id)
            final_summary, key_beats = await self._final_summary(scene, posts)
            scene.final_summary = final_summary
            scene.key_beats = list(key_beats)
            scene.closed = True
            scene.closed_at_turn = closed_at_turn
            if scene.in_game_end is None:
                scene.in_game_end = scene.in_game_start

            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)

            unresolved = [t for t in scene.threads_introduced if t not in scene.threads_paid_off]
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
                threads_unresolved=unresolved,
            )
            if self._active_scene.get((scene.campaign_id, scene.branch_id)) == scene.id:
                self._active_scene.pop((scene.campaign_id, scene.branch_id), None)
            return report

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
            append_post_to_body(md_path, post)

            scene.post_count = post.order_in_scene
            if post.author_kind == AuthorKind.PC and post.author_pc_ref:
                if post.author_pc_ref not in scene.present_pc_refs:
                    scene.present_pc_refs.append(post.author_pc_ref)
                if post.author_pc_ref not in scene.present_character_refs:
                    scene.present_character_refs.append(post.author_pc_ref)
                self._pc_current_scene[(scene.campaign_id, post.author_pc_ref)] = scene.id
            elif post.author_kind == AuthorKind.NPC and post.author_npc_ref:
                if post.author_npc_ref not in scene.present_character_refs:
                    scene.present_character_refs.append(post.author_npc_ref)

            write_sidecar(yaml_path, scene)

            self._records_for(scene_id)[str(post.order_in_scene)] = _PostRecord(
                id=post.id,
                turn_id=post.turn_id,
                created_at=post.created_at,
                is_player=post.is_player,
            )

            await self._emit(
                POST_APPENDED, scene, order=post.order_in_scene, author=post.author_label
            )
            if post.author_kind == AuthorKind.PC:
                await self._emit(
                    PC_POST_APPENDED,
                    scene,
                    order=post.order_in_scene,
                    pc_ref=post.author_pc_ref,
                )

            if (
                self.config.running_summary_every_n_posts > 0
                and scene.post_count > 0
                and scene.post_count % self.config.running_summary_every_n_posts == 0
            ):
                await self._update_running_summary_locked(scene)

    async def get_posts(self, scene_id: str, range: tuple[int, int] | None = None) -> list[Post]:
        scene = await self.get_scene(scene_id)
        md_path, _ = self._scene_file_paths(scene)
        records = self._records_for(scene_id)
        posts: list[Post] = []
        for order, kind, pc_ref, npc_ref, body in read_posts(md_path, scene_id):
            record = records.get(str(order))
            posts.append(
                Post(
                    id=record.id if record else f"{scene_id}#post-{order}",
                    scene_id=scene_id,
                    order_in_scene=order,
                    author_kind=kind,
                    author_pc_ref=pc_ref,
                    author_npc_ref=npc_ref,
                    body=body,
                    is_player=record.is_player if record else False,
                    created_at=record.created_at if record else datetime.fromtimestamp(0),
                    turn_id=record.turn_id if record else "",
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

    async def recent_posts(self, scene_id: str, n: int = 10) -> list[Post]:
        posts = await self.get_posts(scene_id)
        if n <= 0:
            return []
        return posts[-n:]

    # -- Presence --------------------------------------------------------

    async def add_present_character(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            if character_ref in scene.present_character_refs:
                return
            scene.present_character_refs.append(character_ref)
            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)

    async def remove_present_character(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            changed = False
            if character_ref in scene.present_character_refs:
                scene.present_character_refs.remove(character_ref)
                changed = True
            was_pc = character_ref in scene.present_pc_refs
            if was_pc:
                scene.present_pc_refs.remove(character_ref)
                changed = True
                self._pc_current_scene.pop((scene.campaign_id, character_ref), None)
            if changed:
                _, yaml_path = self._scene_file_paths(scene)
                write_sidecar(yaml_path, scene)
                if was_pc and len(scene.present_pc_refs) <= 1:
                    await self._emit(ADVANCE_ENABLED, scene)

    async def add_present_pc(self, scene_id: str, pc_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            previous = len(scene.present_pc_refs)
            if pc_ref not in scene.present_pc_refs:
                scene.present_pc_refs.append(pc_ref)
            if pc_ref not in scene.present_character_refs:
                scene.present_character_refs.append(pc_ref)
            self._pc_current_scene[(scene.campaign_id, pc_ref)] = scene.id
            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)
            if previous <= 1 and len(scene.present_pc_refs) >= 2:
                await self._emit(ADVANCE_DISABLED, scene)

    async def set_pov(self, scene_id: str, character_ref: str) -> None:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            scene.pov_character_ref = character_ref
            if character_ref not in scene.present_character_refs:
                scene.present_character_refs.append(character_ref)
            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)

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
        return detect_scene_break(
            scene,
            player_input,
            now_in_game=now_in_game,
            proposed_present_cast=proposed_present_cast,
            proposed_location_ref=proposed_location_ref,
            config=self.config.boundary,
        )

    async def on_post_submitted(self, scene_id: str, post: Post) -> AdvanceDecision:
        scene = await self.get_scene(scene_id)
        present_pcs = scene.present_pc_refs
        if len(present_pcs) <= 1 or not self.config.require_advance_with_multiple_pcs:
            return AdvanceDecision(auto_respond=True, reason="single_pc_scene")
        return AdvanceDecision(auto_respond=False, reason="multi_pc_pending_advance")

    async def on_advance_requested(self, scene_id: str) -> AdvanceResult:
        async with self._lock_for(scene_id):
            scene = await self.get_scene(scene_id)
            pending = await self.posts_since_last_advance(scene_id)
            if not pending:
                raise NothingToAdvance(scene_id)
            scene.last_advance_at_post = scene.post_count
            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)
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
        _, yaml_path = self._scene_file_paths(scene)
        write_sidecar(yaml_path, scene)
        await self._emit(RUNNING_SUMMARY_UPDATED, scene, summary=new_summary)

    async def update_running_summary(self, scene_id: str) -> str:
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
            if thread.text in target:
                return
            target.append(thread.text)
            _, yaml_path = self._scene_file_paths(scene)
            write_sidecar(yaml_path, scene)
            event = THREAD_INTRODUCED if kind == "introduced" else THREAD_PAID_OFF
            await self._emit(event, scene, thread=thread.text)

    async def list_threads(self, scene_id: str) -> SceneThreads:
        scene = await self.get_scene(scene_id)
        return SceneThreads(
            introduced=[Thread(text=t) for t in scene.threads_introduced],
            paid_off=[Thread(text=t) for t in scene.threads_paid_off],
        )

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
            md_path, _ = self._scene_file_paths(scene)
            write_body(md_path, updated)
            await self._emit(
                POST_EDITED,
                scene,
                order=post.order_in_scene,
                source=source,
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
            write_body(md_path, kept)
            scene.post_count = len(kept)
            if scene.last_advance_at_post > scene.post_count:
                scene.last_advance_at_post = scene.post_count
            write_sidecar(yaml_path, scene)
            # Re-key record metadata for shifted posts.
            records = self._records_for(scene.id)
            shifted = {}
            for key, record in list(records.items()):
                order = int(key)
                if order == removed_order:
                    continue
                if order > removed_order:
                    shifted[str(order - 1)] = record
                else:
                    shifted[key] = record
            self._post_records[scene.id] = shifted
            await self._emit(POST_DELETED, scene, order=removed_order, source=source)

    async def _find_post(self, post_id: str) -> tuple[Scene, Post]:
        # Search active and known scenes first; fall back to a filesystem walk.
        candidates: list[Scene] = []
        for scene_id in {*(s for s in self._active_scene.values()), *self._post_records.keys()}:
            try:
                candidates.append(await self.get_scene(scene_id))
            except KeyError:
                continue
        searched_ids = {s.id for s in candidates}
        campaigns_root = self.data_root / "campaigns"
        if campaigns_root.exists():
            for campaign_dir in campaigns_root.iterdir():
                if not campaign_dir.is_dir():
                    continue
                for scene in await self.list_scenes(campaign_dir.name):
                    if scene.id not in searched_ids:
                        candidates.append(scene)
                        searched_ids.add(scene.id)
        for scene in candidates:
            for post in await self.get_posts(scene.id):
                if post.id == post_id:
                    return scene, post
        raise KeyError(f"post not found: {post_id}")

    # -- Fork (copy-on-write) -------------------------------------------

    async def fork_scenes_for_branch(
        self,
        campaign_id: str,
        new_branch_id: str,
        *,
        from_branch_id: str = "main",
    ) -> list[Scene]:
        if new_branch_id == from_branch_id:
            raise ValueError("cannot fork onto the same branch")
        source_dir = scenes_dir(self.data_root, campaign_id, from_branch_id)
        target_dir = scenes_dir(self.data_root, campaign_id, new_branch_id)
        if target_dir.exists():
            raise FileExistsError(f"branch already exists: {target_dir}")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if source_dir.exists():
            shutil.copytree(source_dir, target_dir)
        else:
            target_dir.mkdir(parents=True)
        # Rewrite sidecars to set the new branch_id and id prefix.
        new_scenes: list[Scene] = []
        for yaml_path in sorted(target_dir.glob("*.yaml")):
            scene = read_sidecar(yaml_path)
            scene.branch_id = new_branch_id
            scene.id = self._scene_id(campaign_id, new_branch_id, scene.ordinal, scene.slug)
            write_sidecar(yaml_path, scene)
            new_scenes.append(scene)
        return new_scenes

    # -- File-watcher hook ----------------------------------------------

    async def reindex_from_disk(self, scene_id: str) -> Scene:
        """Re-read a scene's files from disk.

        Called by the watcher (task #9) when the user edits a scene file
        directly. We rebuild ``post_count`` from the markdown and emit a
        ``scene_file_changed`` event so consumers can refresh.
        """
        scene = await self.get_scene(scene_id)
        md_path, yaml_path = self._scene_file_paths(scene)
        if yaml_path.exists():
            scene = read_sidecar(yaml_path)
        posts = read_posts(md_path, scene_id) if md_path.exists() else []
        scene.post_count = len(posts)
        write_sidecar(yaml_path, scene)
        await self._emit(SCENE_FILE_CHANGED, scene, post_count=scene.post_count)
        return scene


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
