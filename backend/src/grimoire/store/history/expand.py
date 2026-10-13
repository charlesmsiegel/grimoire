"""09's prompt-only adapter over 08-C3b; copies every post identifier."""

from __future__ import annotations

from ..searchdocs import expand as searchdocs_expand
from . import model


def excerpts(
    cid: str, scene: model.SceneRef, query: model.Query, max_bytes: int
) -> tuple[model.Excerpt, ...]:
    found = searchdocs_expand.expand(
        cid,
        scene.sid,
        phase="prompt",
        terms=query.terms,
        identity=scene.identity,
        radius=1,
        max_excerpts=2,
        max_bytes=max_bytes,
        fallback="none",
    )
    if found is None:
        return ()
    return tuple(
        model.Excerpt(
            tuple(
                model.EvidencePost(p.index, p.key, p.part, p.speaker, p.content) for p in e.posts
            ),
            e.text,
        )
        for e in found.excerpts
    )
