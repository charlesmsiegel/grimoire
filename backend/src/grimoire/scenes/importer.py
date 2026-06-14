"""Import scene files from arbitrary disk locations."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml
from grimoire.scenes.storage import PostTuple, parse_body, scene_paths, write_sidecar
from grimoire.scenes.types import AuthorKind, Post, SceneInit
from grimoire.util import parse_iso_datetime

logger = logging.getLogger(__name__)


@dataclass
class ImportParseResult:
    post_count: int
    posts: list[PostTuple]
    detected_pc_refs: list[str]
    detected_npc_refs: list[str]
    sidecar_metadata: dict[str, Any] | None = None


def parse_import_source(md_path: Path) -> ImportParseResult:
    """Parse a grimoire-format scene .md and optional .yaml sidecar."""
    text = md_path.read_text(encoding="utf-8")
    posts = parse_body(text, scene_id="__import_preview__")

    pc_refs: list[str] = []
    npc_refs: list[str] = []
    for _order, kind, pc_ref, npc_ref, _body in posts:
        if kind == AuthorKind.PC and pc_ref and pc_ref not in pc_refs:
            pc_refs.append(pc_ref)
        if kind == AuthorKind.NPC and npc_ref and npc_ref not in npc_refs:
            npc_refs.append(npc_ref)

    sidecar_meta: dict[str, Any] | None = None
    yaml_path = md_path.with_suffix(".yaml")
    if yaml_path.is_file():
        raw = load_yaml(yaml_path)
        if isinstance(raw, dict):
            sidecar_meta = {
                k: raw[k]
                for k in (
                    "title",
                    "location_ref",
                    "in_game_start",
                    "in_game_end",
                    "mood",
                    "tags",
                    "present_character_refs",
                    "present_pc_refs",
                )
                if k in raw
            }

    return ImportParseResult(
        post_count=len(posts),
        posts=posts,
        detected_pc_refs=pc_refs,
        detected_npc_refs=npc_refs,
        sidecar_metadata=sidecar_meta,
    )


@dataclass
class ImportProgress:
    step: str
    current: int
    total: int
    detail: str


async def run_import_pipeline(
    *,
    scene_manager: Any,
    md_path: Path,
    campaign_id: str,
    title: str,
    metadata: dict[str, Any],
    embedding_queue: Any | None = None,
) -> AsyncIterator[ImportProgress]:
    """Run the full import pipeline, yielding progress events."""
    parsed = await asyncio.to_thread(parse_import_source, md_path)
    n_posts = parsed.post_count
    if n_posts == 0:
        raise ValueError("No posts found — file must use grimoire format: ## Post N — author")
    # copy, N appends, threads, summarize, embed, done
    total = n_posts + 5

    from grimoire.scenes.storage import slugify

    init = SceneInit(
        campaign_id=campaign_id,
        title=title,
        slug=slugify(title),
        location_ref=metadata.get("location_ref"),
        in_game_start=parse_iso_datetime(metadata.get("in_game_start")),
        present_character_refs=metadata.get("present_character_refs", []),
        present_pc_refs=metadata.get("present_pc_refs", []),
        mood=metadata.get("mood"),
        tags=metadata.get("tags", []),
    )
    # Save active scene state so import doesn't redirect live play. Track
    # every PC the import can touch — the listed present PCs plus any post
    # author append_post will re-point — recording None when no mapping
    # existed so restore can pop keys the import created.
    active_key = campaign_id
    prev_active = getattr(scene_manager, "_active_scene", {}).get(active_key)
    prev_pc_scenes: dict[tuple[str, str], str | None] = {}
    pc_scene_map = getattr(scene_manager, "_pc_current_scene", {})
    for pc_ref in [*metadata.get("present_pc_refs", []), *parsed.detected_pc_refs]:
        pc_key = (campaign_id, pc_ref)
        if pc_key not in prev_pc_scenes:
            prev_pc_scenes[pc_key] = pc_scene_map.get(pc_key)

    def _restore_active_state() -> None:
        # Restore active scene state — import should not change what's "current".
        if prev_active is not None:
            scene_manager._active_scene[active_key] = prev_active
        else:
            scene_manager._active_scene.pop(active_key, None)
        for pc_key, prev_id in prev_pc_scenes.items():
            if prev_id is None:
                scene_manager._pc_current_scene.pop(pc_key, None)
            else:
                scene_manager._pc_current_scene[pc_key] = prev_id

    scene = await scene_manager.start_scene(init)

    # Guard against importing a file that lives in the destination directory.
    naming = getattr(getattr(scene_manager, "config", None), "files", None)
    _pattern = getattr(naming, "scene_naming_pattern", None) or "{ordinal:04d}-{slug}"
    dest_md, _ = scene_paths(scene_manager.data_root, scene, naming_pattern=_pattern)
    try:
        same_file = await asyncio.to_thread(lambda: dest_md.resolve() == md_path.resolve())
    finally:
        # Restored in a finally so a cancellation mid-await can't leave live
        # play redirected to the aborted import.
        _restore_active_state()
    if same_file:
        raise ValueError(f"Source file is already in the campaign scenes directory: {md_path}")

    in_game_end = parse_iso_datetime(metadata.get("in_game_end"))
    if in_game_end is not None:
        scene.in_game_end = in_game_end
        naming = getattr(
            getattr(scene_manager, "config", None),
            "files",
            None,
        )
        pattern = getattr(naming, "scene_naming_pattern", None) or "{ordinal:04d}-{slug}"
        _, yaml_path = scene_paths(scene_manager.data_root, scene, naming_pattern=pattern)
        # Deliberately synchronous: scene_started has already been emitted, so
        # a concurrent append to this scene could interleave with a threaded
        # write and clobber the sidecar's post records. Writing on the loop
        # keeps the read-modify-write atomic with other scene mutations.
        write_sidecar(yaml_path, scene)

    tick = 1
    yield ImportProgress(step="copy", current=tick, total=total, detail="Scene created")

    # Suppress per-post running-summary events during bulk append by
    # writing a campaign-level cadence override of 0 (disabled). This is
    # scoped to the campaign and does not affect other scenes/campaigns.
    state_store = getattr(scene_manager, "_state_store", None)
    saved_cadence: int | None = None
    if state_store is not None:
        try:
            saved_cadence = await _get_campaign_summary_cadence(state_store, campaign_id)
            await _set_campaign_summary_cadence(state_store, campaign_id, 0)
        except Exception:
            logger.debug("import: could not suppress summary cadence", exc_info=True)

    base_ts = datetime.now(UTC)
    posts: list[Post] = []
    try:
        for i, (_order, kind, pc_ref, npc_ref, body) in enumerate(parsed.posts):
            post = Post(
                id=str(uuid.uuid4()),
                scene_id=scene.id,
                order_in_scene=i + 1,
                author_kind=kind,
                body=body,
                is_player=(kind == AuthorKind.PC),
                created_at=base_ts
                + timedelta(milliseconds=i),  # distinct per post for fork cutoffs
                turn_id=str(uuid.uuid4()),
                author_pc_ref=pc_ref,
                author_npc_ref=npc_ref,
            )
            await scene_manager.append_post(scene.id, post)
            posts.append(post)
            tick += 1
            detail = f"Appended post {i + 1}/{n_posts}"
            yield ImportProgress(step="append", current=tick, total=total, detail=detail)
    finally:
        if state_store is not None:
            try:
                await _set_campaign_summary_cadence(state_store, campaign_id, saved_cadence)
            except Exception:
                logger.debug("import: could not restore summary cadence", exc_info=True)
        # Re-restore active scene state — append_post re-sets _pc_current_scene
        # on every PC post, undoing the earlier restore.
        _restore_active_state()

    tick += 1
    threads_detail = "Thread detection complete"
    try:
        threads = await scene_manager.detect_threads(scene.id)
        for thread, kind in threads:
            await scene_manager.add_thread(scene.id, thread, kind)
    except Exception:
        logger.warning("import: thread detection failed", exc_info=True)
        threads_detail = "Thread detection skipped (no model configured)"
    yield ImportProgress(step="threads", current=tick, total=total, detail=threads_detail)

    tick += 1
    summary_detail = "Summary generated"
    try:
        await scene_manager.generate_summary(scene.id, force=True)
    except Exception:
        logger.warning("import: summarization failed", exc_info=True)
        summary_detail = "Summary skipped (no model configured)"
    yield ImportProgress(step="summarize", current=tick, total=total, detail=summary_detail)

    tick += 1
    embed_detail = "Embedding enqueued"
    if embedding_queue is not None:
        try:
            from grimoire.watcher.watcher import EmbeddingJob

            md_body = await asyncio.to_thread(md_path.read_text, encoding="utf-8")
            embedding_queue.enqueue(
                EmbeddingJob(
                    ref=scene.id,
                    scope="campaign",
                    source_kind="scene",
                    text=f"{scene.title}\n\n{md_body}",
                    campaign_id=campaign_id,
                )
            )
        except Exception:
            logger.warning("import: embedding enqueue failed", exc_info=True)
            embed_detail = "Embedding skipped"
    else:
        embed_detail = "Embedding deferred (no queue available)"
    yield ImportProgress(step="embed", current=tick, total=total, detail=embed_detail)

    tick += 1
    yield ImportProgress(step="done", current=tick, total=total, detail=scene.id)


async def _get_campaign_summary_cadence(state_store: Any, campaign_id: str) -> int | None:
    import json as _json

    row = await state_store.db.fetchone("SELECT config FROM campaigns WHERE id = ?", (campaign_id,))
    if not row:
        return None
    raw = row.get("config") if hasattr(row, "get") else row["config"]
    if not raw:
        return None
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return None
    block = data.get("summaries") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return None
    n = block.get("running_every_n_posts")
    return int(n) if isinstance(n, int) else None


async def _set_campaign_summary_cadence(
    state_store: Any, campaign_id: str, value: int | None
) -> None:
    if value is not None:
        await state_store.db.execute(
            """UPDATE campaigns SET config = json_set(
                   COALESCE(config, '{}'),
                   '$.summaries.running_every_n_posts', ?
               ) WHERE id = ?""",
            (value, campaign_id),
        )
    else:
        await state_store.db.execute(
            """UPDATE campaigns SET config = json_remove(
                   COALESCE(config, '{}'),
                   '$.summaries.running_every_n_posts'
               ) WHERE id = ?""",
            (campaign_id,),
        )
