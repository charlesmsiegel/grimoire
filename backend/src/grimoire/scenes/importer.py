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
from grimoire.scenes.storage import PostTuple, parse_body, scene_paths, write_sidecar
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
    md_path: Path,
    campaign_id: str,
    title: str,
    metadata: dict[str, Any],
) -> AsyncIterator[ImportProgress]:
    """Run the full import pipeline, yielding progress events."""
    parsed = parse_import_source(md_path)
    n_posts = parsed.post_count
    if n_posts == 0:
        raise ValueError("No posts found — file must use grimoire format: ## Post N — author")
    # copy, N appends, threads, summarize, embed, done
    total = n_posts + 5

    from grimoire.scenes.storage import slugify

    def _parse_dt(raw: Any) -> datetime | None:
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
        return None

    init = SceneInit(
        campaign_id=campaign_id,
        branch_id="main",
        title=title,
        slug=slugify(title),
        location_ref=metadata.get("location_ref"),
        in_game_start=_parse_dt(metadata.get("in_game_start")),
        present_character_refs=metadata.get("present_character_refs", []),
        present_pc_refs=metadata.get("present_pc_refs", []),
        mood=metadata.get("mood"),
        tags=metadata.get("tags", []),
    )
    scene = await scene_manager.start_scene(init)

    in_game_end = _parse_dt(metadata.get("in_game_end"))
    if in_game_end is not None:
        scene.in_game_end = in_game_end
        _, yaml_path = scene_paths(scene_manager.data_root, scene)
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

    now = datetime.now(UTC)
    posts: list[Post] = []
    try:
        for i, (_order, kind, pc_ref, npc_ref, body) in enumerate(parsed.posts):
            post = Post(
                id=uuid.uuid4().hex,
                scene_id=scene.id,
                order_in_scene=i + 1,
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
            detail = f"Appended post {i + 1}/{n_posts}"
            yield ImportProgress(step="append", current=tick, total=total, detail=detail)
    finally:
        if state_store is not None:
            try:
                await _set_campaign_summary_cadence(state_store, campaign_id, saved_cadence)
            except Exception:
                logger.debug("import: could not restore summary cadence", exc_info=True)

    tick += 1
    try:
        threads = await scene_manager.detect_threads(scene.id)
        for thread, kind in threads:
            await scene_manager.add_thread(scene.id, thread, kind)
    except Exception:
        logger.warning("import: thread detection failed", exc_info=True)
    yield ImportProgress(
        step="threads",
        current=tick,
        total=total,
        detail="Thread detection complete",
    )

    tick += 1
    summary_detail = "Summary generated"
    try:
        await scene_manager.generate_summary(scene.id, force=True)
    except Exception:
        logger.warning("import: summarization failed", exc_info=True)
        summary_detail = "Summary skipped (no model configured)"
    yield ImportProgress(step="summarize", current=tick, total=total, detail=summary_detail)

    tick += 1
    yield ImportProgress(step="embed", current=tick, total=total, detail="Embedding enqueued")

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
