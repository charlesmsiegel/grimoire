"""Scene reads: the parsed transcript, frontmatter-only metadata, enumeration,
and the history fields (`location_history`, `time_history`) other stores follow.

`read_scene` needs the seated players to decide which messages are user-side,
so this module imports `appearances/cast.py` — the read-only half of
`appearances`, which touches no scene state. That cut is what lets the import
sit at module scope in one direction only (`appearances/transitions.py` is the
half that comes back the other way, into `write.py`).
"""

from __future__ import annotations

from ..appearances import cast
from ..frontmatter import parse_frontmatter, parse_frontmatter_head
from ..paths import safe_id
from . import paths, serialize


def list_scenes(cid: str) -> list[dict]:
    paths._require_campaign(cid)
    out: list[dict] = []
    d = paths._scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            if not safe_id(p.stem):   # enumeration agrees with the resolvers
                continue
            meta = parse_frontmatter_head(p)  # never reads the transcript body
            history = [x for x in meta.get("time_history", "").split(",") if x]
            out.append({
                "id": p.stem,
                "title": meta.get("title", p.stem),
                "model": meta.get("model", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "date": history[0] if history else "",
                "pcless": meta.get("pcless") == "true",
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def is_pcless(cid: str, sid: str) -> bool:
    """A scene deliberately without a player character (director-driven)."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return False
    return parse_frontmatter_head(p).get("pcless") == "true"


def read_scene(cid: str, sid: str) -> dict:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    players = frozenset(cast.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": serialize._parse_messages(body, players)}


def read_scene_window(cid: str, sid: str, limit: int, before: int | None = None) -> dict:
    """`read_scene` narrowed to a window of the transcript's TAIL.

    The window is the `limit` messages ending just before index `before`
    (`None` ⇒ the end of the transcript), which is how a reader pages
    backwards: ask for the tail, then ask again with the offset the last page
    reported until `has_older` goes false.

    `offset` is the absolute index of `messages[0]` — the same index
    `edit_message`/`split_reply` take, so a client that has only page 2 in hand
    addresses a message exactly as one holding the whole transcript does.

    `has_user_message` answers a question no window can answer for itself:
    whether the transcript contains a player turn ANYWHERE. It is what reroll
    eligibility turns on — the regenerate route refuses an all-assistant
    transcript as an opening post — and an offscreen scene is all-assistant no
    matter how long it grows, so "there are older pages" is not a substitute.

    The file is still parsed whole (a scene is one flat markdown script; there
    is no on-disk pagination and the write path rebuilds the full message list
    regardless). What windowing buys is the client's render path, which is
    where a several-hundred-post scene actually hurts.
    """
    scene = read_scene(cid, sid)
    messages = scene["messages"]
    total = len(messages)
    end = total if before is None else max(0, min(before, total))
    start = max(0, end - max(1, limit))
    return {"meta": scene["meta"], "messages": messages[start:end],
            "offset": start, "total": total, "has_older": start > 0,
            "has_user_message": any(m["role"] == "user" for m in messages)}


def read_scene_meta(cid: str, sid: str) -> dict:
    """A scene's frontmatter without parsing its transcript. For bulk scans
    (response-preset usage) where the messages are irrelevant and reading them
    for every scene in the library is pure waste."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    return {"id": sid, **parse_frontmatter_head(p)}


def get_dismissed(cid: str, sid: str) -> list[str]:
    """Suggestion ids the user dismissed for this scene. Missing scene ⇒ none."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("dismissed", "").split(",") if x]


def trailing_transitions(messages: list[dict]) -> int:
    """How many messages at the tail are scene-transition lines.

    Reroll steps OVER these: a join/leave/location/time line is a record of
    something the player did, not model output, so it survives a reroll of the
    reply beneath it (whereas a manual dice roll blocks reroll outright — its
    entry lives on in rolls.json and the transcript line must stay in lockstep).
    """
    n = 0
    while n < len(messages) and messages[-1 - n].get("speaker") == serialize.TRANSITION_SPEAKER:
        n += 1
    return n


def get_location_history(cid: str, sid: str) -> list[str]:
    """Ordered campaign-location ids this scene has been at; last is current. Missing ⇒ []."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("location_history", "").split(",") if x]


def get_time_history(cid: str, sid: str) -> list[str]:
    """Ordered scene moments (native datetime strings); last is current. Missing ⇒ []."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("time_history", "").split(",") if x]


def get_suggested_date(cid: str, sid: str) -> str:
    """The creation-time date hint, if the scene still carries one. Missing ⇒ ""."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return ""
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta.get("suggested_date", "")
