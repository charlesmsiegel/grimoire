"""Import scene files from arbitrary disk locations."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml
from grimoire.scenes.storage import PostTuple, parse_body
from grimoire.scenes.types import AuthorKind, Post, SceneInit

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
    extractor: Any | None,
    delta_applier: Any | None,
    md_path: Path,
    campaign_id: str,
    title: str,
    metadata: dict[str, Any],
) -> AsyncIterator[ImportProgress]:
    """Run the full import pipeline, yielding progress events."""
    parsed = parse_import_source(md_path)
    n_posts = parsed.post_count
    total = n_posts + 6  # copy, index, N extracts, threads, summarize, embed, done

    from grimoire.scenes.storage import slugify

    in_game_start = metadata.get("in_game_start")
    if isinstance(in_game_start, str):
        try:
            in_game_start = datetime.fromisoformat(in_game_start)
        except ValueError:
            in_game_start = None

    init = SceneInit(
        campaign_id=campaign_id,
        branch_id="main",
        title=title,
        slug=slugify(title),
        location_ref=metadata.get("location_ref"),
        in_game_start=in_game_start,
        present_character_refs=metadata.get("present_character_refs", []),
        present_pc_refs=metadata.get("present_pc_refs", []),
        mood=metadata.get("mood"),
        tags=metadata.get("tags", []),
    )
    scene = await scene_manager.start_scene(init)

    tick = 1
    yield ImportProgress(step="copy", current=tick, total=total, detail="Scene created")

    now = datetime.now(UTC)
    posts: list[Post] = []
    for order, kind, pc_ref, npc_ref, body in parsed.posts:
        post = Post(
            id=uuid.uuid4().hex,
            scene_id=scene.id,
            order_in_scene=order,
            author_kind=kind,
            body=body,
            is_player=(kind == AuthorKind.PC),
            created_at=now,
            turn_id=uuid.uuid4().hex,
            author_pc_ref=pc_ref,
            author_npc_ref=npc_ref,
        )
        await scene_manager.append_post(scene.id, post)
        posts.append(post)

    tick += 1
    yield ImportProgress(step="index", current=tick, total=total, detail=f"Indexed {n_posts} posts")

    for i, post in enumerate(posts):
        tick += 1
        if extractor is not None:
            try:
                result = await extractor.extract_from_user_text(
                    user_text=post.body,
                    scene=scene,
                    campaign_id=campaign_id,
                    player_pc_ref=post.author_pc_ref,
                    turn_id=post.turn_id,
                )
                if delta_applier is not None and result and result.deltas:
                    try:
                        await delta_applier.apply_routing(
                            campaign_id=campaign_id,
                            branch_id="main",
                            turn_id=post.turn_id,
                            extraction=result,
                        )
                    except Exception:
                        logger.warning("import: delta routing failed for post %d", i + 1, exc_info=True)
            except Exception:
                logger.warning("import: extraction failed for post %d", i + 1, exc_info=True)
        yield ImportProgress(step="extract", current=tick, total=total, detail=f"Extracted post {i + 1}/{n_posts}")

    tick += 1
    try:
        threads = await scene_manager.detect_threads(scene.id)
        for thread, kind in threads:
            await scene_manager.add_thread(scene.id, thread, kind)
    except Exception:
        logger.warning("import: thread detection failed", exc_info=True)
    yield ImportProgress(step="threads", current=tick, total=total, detail="Thread detection complete")

    tick += 1
    try:
        await scene_manager.generate_summary(scene.id, force=True)
    except Exception:
        logger.warning("import: summarization failed", exc_info=True)
    yield ImportProgress(step="summarize", current=tick, total=total, detail="Summary generated")

    tick += 1
    yield ImportProgress(step="embed", current=tick, total=total, detail="Embedding enqueued")

    tick += 1
    yield ImportProgress(step="done", current=tick, total=total, detail=scene.id)
