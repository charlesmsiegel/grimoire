"""Campaign meta reads, enumeration and the world reference each one carries."""

from __future__ import annotations

from pathlib import Path

from .. import atomic, worlds
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import ensure_home, now_iso, safe_id
from . import paths

# A campaign may record no world at all, and every world-side read still wants
# a path it can treat as empty. That path has to be one nothing can occupy: any
# sentinel *directory* is one a restored or hand-managed store may already
# contain, and then a world-less campaign inherits whatever is inside it. So
# absence resolves below the campaign's own campaign.md -- a regular file, so
# the filesystem itself guarantees no child of it can ever exist.
_NO_WORLD = "(no world)"


def read_campaign(cid: str) -> dict:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "body": body}


def world_root_of(cid: str) -> Path:
    """The root of the campaign's world, or an unoccupiable path if it has none.

    A stored `world` the guard refuses to resolve — a restored or hand-edited
    campaign can carry one — counts as "no world" rather than raising: a world
    directory that has been deleted already reads as inheriting nothing, and a
    reference that cannot name one is no different. Raises CampaignNotFound
    for a campaign that isn't there. Callers holding a world id they know is
    set should use `worlds.world_root` directly.
    """
    wid = read_campaign(cid)["meta"].get("world", "")
    try:
        return worlds.world_root(wid)
    except worlds.WorldNotFound:
        return paths.campaign_meta_path(cid) / _NO_WORLD


def list_campaigns() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = paths._campaigns_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            # see worlds.list_worlds: enumeration agrees with the resolvers, so a
            # stray directory can't abort a listing -- or the startup migration
            if not d.is_dir() or not mp.exists() or not safe_id(d.name):
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "world": meta.get("world", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def world_refs() -> list[tuple[str, str | None]]:
    """(campaign name, referenced world id) for *every* campaign on disk.

    Deliberately unfiltered, unlike `list_campaigns`. This backs
    `worlds.delete_world`'s in-use check, and a campaign that is unusable as an
    id still pins the world it references: filtering it out of the check is
    what would make that world deletable out from under it (#259 review).
    Enumeration may hide a record from the UI; it must never hide it from a
    referential-integrity check.

    A world id of ``None`` means "this campaign's reference could not be read".
    Undecodable bytes in the *body* must not cost us a reference sitting in
    perfectly good frontmatter, so the read is retried lossily first; only a
    file that cannot be read at all yields ``None``. Callers must treat that as
    "may reference anything" -- skipping it is how "we could not tell" turns
    into "nothing uses this world", which deletes it (#259 review).
    """
    out: list[tuple[str, str | None]] = []
    base = paths._campaigns_dir()
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        mp = d / "campaign.md"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            text = mp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:   # frontmatter survives a bad byte in the body
                text = mp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                out.append((d.name, None))
                continue
        except OSError:
            out.append((d.name, None))
            continue
        meta, _ = parse_frontmatter(text)
        out.append((meta.get("name", d.name), meta.get("world", "")))
    return out


def touch(cid: str) -> None:
    mp = paths.campaign_meta_path(cid)
    if not mp.exists():
        raise paths.CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))
