"""Shared helpers used across campaign sub-routers."""

from __future__ import annotations

import json as _json
import logging
import re
from collections.abc import Iterable
from datetime import UTC
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_GREETING_IMG_RE = re.compile(
    r"<img\s+[^>]*?src=[\"']([^\"']+)[\"'][^>]*?(?:alt=[\"']([^\"']*)[\"'][^>]*)?/?>",
    re.IGNORECASE,
)


def _img_to_markdown(body: str) -> str:
    """Convert inline ``<img src=... alt=...>`` tags to markdown ``![alt](src)``."""

    def replace(match: re.Match[str]) -> str:
        src = match.group(1) or ""
        alt = match.group(2) or ""
        return f"![{alt}]({src})"

    out = _GREETING_IMG_RE.sub(replace, body)
    alt_first = re.compile(
        r"<img\s+[^>]*?alt=[\"']([^\"']*)[\"'][^>]*?src=[\"']([^\"']+)[\"'][^>]*?/?>",
        re.IGNORECASE,
    )
    out = alt_first.sub(lambda m: f"![{m.group(1)}]({m.group(2)})", out)
    return out


def _substitute_placeholders(body: str, *, pc_name: str, char_name: str) -> str:
    """Resolve SillyTavern-style placeholders."""
    if pc_name:
        body = body.replace("{{user}}", pc_name).replace("[PC]", pc_name)
    if char_name:
        body = body.replace("{{char}}", char_name)
    return body


async def _resolve_pc_display_name(*, state_store: Any, library: Any, campaign_id: str) -> str:
    """Look up the first PC's display name on the campaign."""
    try:
        pc_rows = await state_store.list_pcs(campaign_id)
    except Exception:
        pc_rows = []
    for row in pc_rows or []:
        name = row.get("name") if isinstance(row, dict) else None
        if name:
            return str(name)
        ref = row.get("character_ref") if isinstance(row, dict) else None
        if ref:
            return ref.rsplit("/", 1)[-1]
    return "You"


async def _resolve_character_display_name(
    *, library: Any, world_id: str | None, character_id: str | None
) -> str:
    """Look up a character's display name from the library."""
    if not character_id or not world_id:
        return ""
    try:
        ent = await library.get_entity(world_id, "character", character_id)
        fm = getattr(ent, "frontmatter", {}) or {}
        return str(fm.get("name") or ent.name or character_id)
    except Exception:
        return character_id


async def _seed_greeting_first_post(
    *,
    scenes: Any,
    scene: Any,
    greeting: Any,
    state_store: Any,
    library: Any,
    world_id: str | None,
) -> None:
    """Append the greeting's verbatim body as scene 1's first post."""
    import uuid
    from datetime import datetime

    from grimoire.scenes.types import AuthorKind, Post

    body = (getattr(greeting, "body", "") or "").strip()
    if not body:
        return

    pc_name = await _resolve_pc_display_name(
        state_store=state_store, library=library, campaign_id=scene.campaign_id
    )
    char_id = getattr(greeting, "pov_character", None)
    if not char_id:
        present = list(getattr(greeting, "present_characters", []) or [])
        char_id = present[0] if present else None
    char_name = await _resolve_character_display_name(
        library=library, world_id=world_id, character_id=char_id
    )

    body = _substitute_placeholders(body, pc_name=pc_name, char_name=char_name)
    body = _img_to_markdown(body)

    post = Post(
        id=str(uuid.uuid4()),
        scene_id=scene.id,
        order_in_scene=0,
        author_kind=AuthorKind.NARRATOR,
        body=body,
        is_player=False,
        created_at=datetime.now(UTC),
        turn_id=str(uuid.uuid4()),
    )
    await scenes.append_post(scene.id, post)


def _pc_role_tag_union(pc_rows: Iterable[Any]) -> set[str]:
    """Union of every PC's ``role_tags`` for a campaign.

    ``state_store.list_pcs`` returns rows where ``role_tags`` is the raw
    JSON-string column value; tolerate a missing/invalid value or an already
    decoded list.
    """
    union: set[str] = set()
    for row in pc_rows or []:
        raw = row.get("role_tags") if isinstance(row, dict) else None
        if isinstance(raw, str):
            try:
                tags = _json.loads(raw)
            except (TypeError, ValueError):
                tags = []
        elif isinstance(raw, list):
            tags = raw
        else:
            tags = []
        union.update(str(t) for t in tags if t)
    return union


def _greeting_applies(greeting_role_tags: Iterable[str], pc_role_tag_union: set[str]) -> bool:
    """Whether a greeting applies to the campaign's PCs.

    Mirrors the wizard's greeting picker rule
    (``frontend/src/routes/CampaignCreate/StepStartingScene.tsx``): a greeting
    with no ``role_tags`` is universal; a tagged greeting applies only when one
    of its tags is present in the PC role-tag union.
    """
    tags = [t for t in greeting_role_tags if t]
    if not tags:
        return True
    if not pc_role_tag_union:
        return False
    return any(t in pc_role_tag_union for t in tags)


async def _backfill_ledger_from_greetings(
    *,
    campaign_id: str,
    library: Any,
    state_store: Any,
    ledger: Any,
    world_refs: Iterable[Any],
    exclude_greeting_ids: Iterable[str] = (),
) -> list[str]:
    """Populate the scene ledger with applicable, unused greetings.

    Only greetings that apply to the campaign's PCs (see :func:`_greeting_applies`)
    and are not already represented in the ledger or in ``exclude_greeting_ids``
    (typically the opening greeting plus any greeting already consumed by a scene)
    are added. Idempotent: re-running adds nothing new. Returns the ids of the
    ledger items created.
    """
    pc_rows = await state_store.list_pcs(campaign_id)
    pc_union = _pc_role_tag_union(pc_rows)

    existing = await ledger.list_all(campaign_id)
    seen: set[str] = {i.get("greeting_id") for i in existing if i.get("greeting_id")}
    seen.update(gid for gid in exclude_greeting_ids if gid)

    added: list[str] = []
    for ref in world_refs or []:
        wid = getattr(ref, "world_id", None) or (
            ref.get("world_id") if isinstance(ref, dict) else None
        )
        if not wid:
            continue
        try:
            greetings = await library.list_greetings(wid)
        except Exception:
            logger.warning("list_greetings failed for world %r", wid, exc_info=True)
            continue
        for g in greetings:
            if g.id in seen:
                continue
            if not _greeting_applies(list(getattr(g, "role_tags", []) or []), pc_union):
                continue
            body = getattr(g, "body", "") or ""
            item_id = await ledger.add(
                campaign_id=campaign_id,
                summary=g.name or body[:80],
                source="greeting",
                greeting_id=g.id,
                proposed_location=getattr(g, "starting_location", None),
            )
            added.append(item_id)
            seen.add(g.id)
    return added


async def _require_campaign_row(state_store: Any, campaign_id: str) -> dict:
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    return dict(row)


def _load_campaign_config(row: dict) -> dict:
    """Best-effort parse of ``campaigns.config`` into a dict."""
    import json as _json

    raw = row.get("config")
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _write_campaign_config(state_store: Any, campaign_id: str, config: dict) -> None:
    import json as _json

    await state_store.db.execute(
        "UPDATE campaigns SET config = ? WHERE id = ?",
        (_json.dumps(config, sort_keys=True), campaign_id),
    )


def _read_routing_blocks(state_store: Any, campaign_id: str) -> dict[str, dict[str, str]]:
    """Pull the three routing blocks straight from ``campaign.yaml``."""
    from grimoire.files.yaml_io import load_yaml

    data_root = getattr(state_store, "data_root", None)
    if data_root is None:
        return {"llm": {}, "embedding": {}, "imagegen": {}}
    yaml_path = data_root / "campaigns" / campaign_id / "campaign.yaml"
    if not yaml_path.is_file():
        return {"llm": {}, "embedding": {}, "imagegen": {}}
    try:
        raw = load_yaml(yaml_path)
    except Exception:
        return {"llm": {}, "embedding": {}, "imagegen": {}}
    if not isinstance(raw, dict):
        return {"llm": {}, "embedding": {}, "imagegen": {}}

    def _block(key: str) -> dict[str, str]:
        block = raw.get(key)
        return {str(k): str(v) for k, v in block.items()} if isinstance(block, dict) else {}

    return {
        "llm": _block("model_routing"),
        "embedding": _block("embedding_routing"),
        "imagegen": _block("imagegen_routing"),
    }


async def _require_scene_owned(scenes: Any, campaign_id: str, scene_id: str) -> Any:
    """Resolve ``scene_id`` and reject when it doesn't belong to ``campaign_id``."""
    scene = await scenes.get_scene(scene_id)
    if getattr(scene, "campaign_id", None) != campaign_id:
        raise HTTPException(
            status_code=404,
            detail=f"scene {scene_id!r} not found in campaign {campaign_id!r}",
        )
    return scene


async def _list_kind(campaign_id: str, kind: str, world: Any) -> Any:
    from grimoire.api.util import to_payload

    return to_payload(await world.list_for_campaign(campaign_id, kind))


def _continuity_for(continuity_dep: Any, campaign_id: str) -> Any:
    """Resolve a per-campaign Continuity from either a registry or a single shared service."""
    from grimoire.continuity.registry import resolve_continuity

    return resolve_continuity(continuity_dep, campaign_id)


async def _require_review_owned(state_store: Any, campaign_id: str, review_id: str) -> None:
    """Reject the request if ``review_id`` is not scoped to ``campaign_id``."""
    row = await state_store.db.fetchone(
        "SELECT campaign_id FROM review_queue WHERE id = ?",
        (review_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"review item {review_id!r} not found")
    if row["campaign_id"] != campaign_id:
        raise HTTPException(
            status_code=404,
            detail=f"review item {review_id!r} not found in campaign {campaign_id!r}",
        )
