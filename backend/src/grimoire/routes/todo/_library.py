"""todo: library responsibilities."""

from __future__ import annotations

from ... import store
from . import _context, _sources
from ._router import router as router


def _chore_world_describe(ctx: _context._Ctx) -> dict | None:
    rows = _sources._world_describe_counts(ctx.worlds())
    n = sum(r["n"] for r in rows)
    if not n:
        return None
    return {
        "id": "world-describe", "scope": "world", "group": "Artwork",
        "severity": "note", "n": n,
        "what": f"{n} image{'s' if n != 1 else ''} with no description",
        "why": "An undescribed image is one nothing can offer a scene, because "
               "what it depicts is written down nowhere.",
        # The cast page, because that is the only place `DescribeQueue` is
        # mounted (`components/CharacterGrid.tsx`) -- and its queue reads
        # `/images/undescribed`, which carries the world's own library rows too
        # and which `DescribeQueue` handles as `kind: "world"`. Pointing this at
        # the Images tab looked righter (that is where library art is edited)
        # and sent the reader to a page with no describe queue on it at all.
        "fix": f"/worlds/{rows[0]['wid']}" if len(rows) == 1 else "/worlds",
        "fix_label": "The cast" if len(rows) == 1 else "The worlds",
    }


def _chore_world_taglines(ctx: _context._Ctx) -> dict | None:
    who = ctx.world_char_gaps()[0]
    if not who:
        return None
    return {
        "id": "world-taglines", "scope": "world", "group": "Character & voice",
        "severity": "note", "n": len(who),
        "what": f"{len(who)} character{'s' if len(who) != 1 else ''} with no tagline",
        # No "in worlds the campaign does not use" here any more: the row
        # carries a scope chip that says which body of content it is about,
        # and the sentence was the same fact spelled out where only a reader
        # who finished the paragraph would find it.
        "why": "The tagline is what a browse grid and a scene suggestion have to go "
               "on before anything else is read.",
        "fix": f"/worlds/{who[0]['wid']}" if len({c['wid'] for c in who}) == 1 else "/worlds",
        "fix_label": "The cast" if len({c["wid"] for c in who}) == 1 else "The worlds",
    }


def _chore_world_anchors(ctx: _context._Ctx) -> dict | None:
    who = ctx.world_char_gaps()[1]
    if not who:
        return None
    return {
        "id": "world-anchors", "scope": "world", "group": "Character & voice",
        "severity": "note", "n": len(who),
        "what": f"{len(who)} character{'s' if len(who) != 1 else ''} with no voice anchor",
        "why": "Without one nothing measures whether a reply still sounds like them, "
               "so drift goes unreported rather than absent.",
        "fix": f"/worlds/{who[0]['wid']}" if len({c['wid'] for c in who}) == 1 else "/worlds",
        "fix_label": "The cast" if len({c["wid"] for c in who}) == 1 else "The worlds",
    }


def _chore_world_avatars(ctx: _context._Ctx) -> dict | None:
    who = ctx.world_avatar_gaps()
    if not who:
        return None
    n = len(who)
    one_world = len({c["wid"] for c in who}) == 1
    return {
        "id": "world-avatars", "scope": "world", "group": "Character & voice", "severity": "note", "n": n,
        "what": f"{n} character{'s' if n != 1 else ''} without an avatar",
        "why": "The default version has no portrait to show in the cast grid.",
        "fix": f"/worlds/{who[0]['wid']}" if one_world else "/worlds",
        "fix_label": "The cast" if one_world else "The worlds",
    }


def _chore_world_covers(ctx: _context._Ctx) -> dict | None:
    worlds = ctx.world_cover_gaps()
    if not worlds:
        return None
    n = len(worlds)
    return {
        "id": "world-covers", "scope": "world", "group": "Artwork", "severity": "note", "n": n,
        "what": f"{n} world{'s' if n != 1 else ''} without a cover",
        "why": "These worlds have no cover to show on the worlds shelf.",
        "fix": f"/worlds/{worlds[0]['id']}/images" if n == 1 else "/worlds",
        "fix_label": "World images" if n == 1 else "The worlds",
    }


def _chore_world_subjects(ctx: _context._Ctx) -> dict | None:
    waiting = ctx.world_subject_gaps()
    if not waiting:
        return None
    n = len(waiting)
    one_world = len({i["wid"] for i in waiting}) == 1
    return {
        "id": "world-subjects", "scope": "world", "group": "Artwork", "severity": "note", "n": n,
        "what": f"{n} greeting image{'s' if n != 1 else ''} without a character assignment",
        "why": "Assign the characters shown, or choose No subjects for an image with none.",
        "fix": f"/worlds/{waiting[0]['wid']}/images" if one_world else "/worlds",
        "fix_label": "Assign characters" if one_world else "The worlds",
    }


def _items_world_avatars(cid: str) -> list[dict]:
    return [{"id": f"{r['wid']}:{r['id']}", "label": r["name"],
             "detail": " · ".join((r["world"], r["default_version"])),
             "fix": f"/worlds/{r['wid']}/characters/{r['id']}"}
            for r in _context._Ctx(cid).world_avatar_gaps()]


def _items_world_covers(cid: str) -> list[dict]:
    return [{"id": w["id"], "label": w["name"], "detail": "no cover",
             "fix": f"/worlds/{w['id']}/images"}
            for w in _context._Ctx(cid).world_cover_gaps()]


def _items_world_subjects(cid: str) -> list[dict]:
    # Titles are paid for only when this chore is expanded. Counting the
    # queue inventories stored art and images referenced by greeting bodies.
    names: dict[str, dict[str, str]] = {}
    out = []
    for image in _context._Ctx(cid).world_subject_gaps():
        wid, gid = image["wid"], image["gid"]
        if wid not in names:
            try:
                root = store.worlds.paths.world_root(wid)
                names[wid] = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
            except (OSError, store.worlds.paths.WorldNotFound):
                names[wid] = {}
        out.append({"id": f"{wid}:{gid}:{image['name']}", "label": names[wid].get(gid, gid),
                    "detail": " · ".join((image["world"], image["name"])),
                    "fix": f"/worlds/{wid}/greetings/{gid}"})
    return out


def _items_world_describe(cid: str) -> list[dict]:
    """One row per WORLD, not per image.

    A per-image row would be hundreds of rows saying nothing individually
    useful, and would hit `ITEM_CAP` on any real library. The world is the
    grain the fix is applied at -- the describe queue runs over a whole world
    from its cast page -- so it is the grain the reader needs.
    """
    ctx = _context._Ctx(cid)
    return [{"id": r["wid"], "label": r["world"],
             "detail": f"{r['n']} image{'s' if r['n'] != 1 else ''}",
             "fix": f"/worlds/{r['wid']}"}
            for r in _sources._world_describe_counts(ctx.worlds())]


def _world_gap_items(cid: str, which: int) -> list[dict]:
    """One row per character, naming it -- the read `_world_char_gaps` skips.

    The name costs a frontmatter read per row, which is why the count does not
    pay it. Here it is the point: a list of slugs is not something a reader can
    act on, and a library with two Maras is exactly when it matters.
    """
    ctx = _context._Ctx(cid)
    out = []
    for c in ctx.world_char_gaps()[which]:
        try:
            root = store.worlds.paths.world_root(c["wid"])
            name = store.characters.read_character(root, c["id"])["meta"].get("name") or c["id"]
        except (OSError, KeyError, store.characters.CharacterNotFound,
                store.worlds.paths.WorldNotFound):
            name = c["id"]
        out.append({"id": f"{c['wid']}:{c['id']}", "label": str(name),
                    "detail": c["world"], "fix": f"/worlds/{c['wid']}"})
    return out


def _items_world_taglines(cid: str) -> list[dict]:
    return _world_gap_items(cid, 0)


def _items_world_anchors(cid: str) -> list[dict]:
    return _world_gap_items(cid, 1)
