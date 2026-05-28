"""Create response posts from LLM output, respecting narrator response mode."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from grimoire.scenes.narrator_mode import PER_CHARACTER
from grimoire.scenes.response_splitter import split_response
from grimoire.scenes.types import AuthorKind, Post


def create_response_posts(
    *,
    response_text: str,
    narrator_mode: str,
    turn_id: str,
    clock: Callable[[], datetime],
) -> list[Post]:
    if narrator_mode != PER_CHARACTER:
        return [_make_post(AuthorKind.NARRATOR, response_text, None, turn_id, clock)]

    segments = split_response(response_text)
    if not segments:
        return [_make_post(AuthorKind.NARRATOR, response_text, None, turn_id, clock)]

    posts: list[Post] = []
    for seg in segments:
        if seg.kind == "character":
            posts.append(_make_post(AuthorKind.NPC, seg.body, seg.ref, turn_id, clock))
        else:
            posts.append(_make_post(AuthorKind.NARRATOR, seg.body, None, turn_id, clock))
    return posts


def _make_post(
    author_kind: AuthorKind,
    body: str,
    npc_ref: str | None,
    turn_id: str,
    clock: Callable[[], datetime],
) -> Post:
    return Post(
        id=str(uuid.uuid4()),
        scene_id="",
        order_in_scene=0,
        author_kind=author_kind,
        body=body,
        is_player=False,
        created_at=clock(),
        turn_id=turn_id,
        author_npc_ref=npc_ref,
    )
