"""World meta reads and enumeration."""

from __future__ import annotations

from .. import characters, entities, greetings, pcs
from ..frontmatter import parse_frontmatter
from ..paths import any_child_record, ensure_home, safe_id
from . import paths


def has_worlds() -> bool:
    """Whether the store holds at least one world, without reading any of them.

    `list_worlds()` would answer this too, but at the cost of parsing every
    world's frontmatter and counting its entities — far too much for the
    first-run check that is the only caller.
    """
    ensure_home()
    return any_child_record(paths._worlds_dir(), "world.md")


def list_worlds() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = paths._worlds_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "world.md"
            # an id the resolvers refuse must not be listed: it would only fail
            # on the caller's next call (#259 review)
            if not d.is_dir() or not mp.exists() or not safe_id(d.name):
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "counts": {**entities.entity_counts(d), "characters": characters.character_count(d),
                           "pcs": pcs.pc_count(d), "greetings": greetings.greeting_count(d)},
            })
    # `created` breaks a tie on `updated`, newest first. `now_iso()` has
    # one-second resolution, so a world forked (or created) in the same second
    # as an edit to its source ties -- and a plain single-key sort then falls
    # back to the directory order underneath it, which is alphabetical and says
    # nothing about recency. The fork would sit wherever its slug happened to
    # land, while the UI that refreshes instead of navigating promises it is at
    # the front (Codex review). `created` separates them because a copy is new
    # and the thing it was copied from is not.
    #
    # It is a tie-breaker, not a clock. Two worlds CREATED in the same second
    # -- a brand-new world forked immediately -- tie on both keys and fall back
    # to directory order, and closing that would mean giving `now_iso()`
    # sub-second precision, which every stamp in the store and the frozen
    # campaign's snapshot are written against. Not worth it for two adjacent
    # rows that are both seconds old.
    out.sort(key=lambda m: (m["updated"], m["created"]), reverse=True)
    return out


def read_world(wid: str) -> dict:
    mp = paths.world_meta_path(wid)
    if not mp.exists():
        raise paths.WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    root = paths.world_root(wid)
    return {"meta": {"id": wid, **meta}, "body": body,
            "counts": {**entities.entity_counts(root), "characters": characters.character_count(root),
                       "pcs": pcs.pc_count(root), "greetings": greetings.greeting_count(root)}}


def world_name(wid: str) -> str | None:
    """Just the display name — no entity counts, one file read (for embedding
    in other payloads without read_world's directory sweeps). A nullable
    lookup: an id that can't resolve to a world reports absence, so a campaign
    with no world recorded embeds cleanly instead of raising."""
    if not paths.world_exists(wid):
        return None
    mp = paths.world_meta_path(wid)
    meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return meta.get("name", wid)
