"""Scene CRUD — chat transcripts living under <campaign>/scenes/."""

from __future__ import annotations

import re
from pathlib import Path

from . import campaigns, entities
from .config import read_config
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso, slugify, uniquify

ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
LABEL_TO_ROLE = {"You": "user", "Grimoire": "assistant"}
_MARKER = re.compile(r"^\*\*(You|Grimoire):\*\*[ ]?", re.MULTILINE)


class SceneNotFound(Exception):
    pass


def _scenes_dir(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "scenes"


def _scene_path(cid: str, sid: str) -> Path:
    return _scenes_dir(cid) / f"{sid}.md"


def _safe_id(sid: str) -> bool:
    """Reject ids that could escape the scenes directory (defense in depth)."""
    return sid not in ("", ".", "..") and "/" not in sid and "\\" not in sid


def _require_campaign(cid: str) -> None:
    if not campaigns.campaign_meta_path(cid).exists():
        raise campaigns.CampaignNotFound(cid)


def create_scene(cid: str, title: str) -> str:
    _require_campaign(cid)
    d = _scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    now = now_iso()
    base = f"{now[:10]}-{slugify(title)}"
    sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    _scene_path(cid, sid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return sid


def list_scenes(cid: str) -> list[dict]:
    _require_campaign(cid)
    out: list[dict] = []
    d = _scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            out.append({
                "id": p.stem,
                "title": meta.get("title", p.stem),
                "model": meta.get("model", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def _parse_messages(body: str) -> list[dict]:
    matches = list(_MARKER.finditer(body))
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        messages.append({"role": LABEL_TO_ROLE[m.group(1)], "content": body[start:end].strip()})
    return messages


def read_scene(cid: str, sid: str) -> dict:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": _parse_messages(body)}


def rename_scene(cid: str, sid: str, title: str) -> str:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    prefix = meta.get("created", now_iso())[:10]
    new_sid = uniquify(
        f"{prefix}-{slugify(title)}",
        lambda c: c != sid and _scene_path(cid, c).exists(),
    )
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if new_sid != sid:
        p.rename(_scene_path(cid, new_sid))
    return new_sid


def delete_scene(cid: str, sid: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    p.unlink()


def get_dismissed(cid: str, sid: str) -> list[str]:
    """Suggestion ids the user dismissed for this scene. Missing scene ⇒ none."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("dismissed", "").split(",") if x]


def add_dismissed(cid: str, sid: str, char_id: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    current = [x for x in meta.get("dismissed", "").split(",") if x]
    if char_id not in current:
        current.append(char_id)
    meta["dismissed"] = ",".join(current)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def append_message(cid: str, sid: str, role: str, content: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    block = f"**{ROLE_TO_LABEL[role]}:** {content.strip()}\n"
    body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def _serialize_messages(messages: list[dict]) -> str:
    body = ""
    for m in messages:
        block = f"**{ROLE_TO_LABEL[m['role']]}:** {m['content'].strip()}\n"
        body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    return body


def edit_message(cid: str, sid: str, index: int, content: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    messages = read_scene(cid, sid)["messages"]
    if index < 0 or index >= len(messages):
        raise IndexError(index)
    messages[index]["content"] = content.strip()
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, _serialize_messages(messages)), encoding="utf-8")


def get_location_history(cid: str, sid: str) -> list[str]:
    """Ordered campaign-location ids this scene has been at; last is current. Missing ⇒ []."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("location_history", "").split(",") if x]


def set_location(cid: str, sid: str, eid: str) -> dict:
    """Make campaign location `eid` the scene's current setting.

    First setting on a location-less scene is silent; a real change appends an
    assistant transition line. Re-selecting the current location is a no-op.
    Returns {"moved": bool, "name": str}.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    croot = campaigns.campaign_root(cid)
    name = entities.read_entity(croot, "locations", eid)["meta"].get("name", eid)  # raises EntityNotFound
    history = get_location_history(cid, sid)
    if history and history[-1] == eid:
        return {"moved": False, "name": name}
    moved = bool(history)
    if moved:
        append_message(cid, sid, "assistant", f"*The scene moves to {name}.*")
    # re-read after the possible append_message rewrite, then record the new current
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(eid)
    meta["location_history"] = ",".join(history)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return {"moved": moved, "name": name}
