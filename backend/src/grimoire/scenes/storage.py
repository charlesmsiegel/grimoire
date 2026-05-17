"""Markdown + YAML sidecar storage for scenes.

Two files per scene under ``data/campaigns/<campaign_id>/scenes/`` (or per
``branch_id`` when not ``main``)::

    0001-elysium-opening.md       # prose, posts in order
    0001-elysium-opening.yaml     # metadata sidecar

The ``.md`` file is the prose source of truth; the ``.yaml`` file is the
metadata source of truth. Both can be hand-edited; the watcher (task #9) will
reindex them. Helpers here are pure I/O — they don't update SQLite indexes.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import yaml

from grimoire.scenes.types import AuthorKind, Post, Scene

POST_HEADING_RE = re.compile(r"^##\s+Post\s+(\d+)\s+[—-]\s+(.+?)\s*$", re.MULTILINE)


def slugify(text: str) -> str:
    """Convert a title into a stable, filesystem-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "scene"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_branch_segment(branch_id: str) -> str:
    """Return a filesystem-safe directory name for *branch_id*.

    Windows forbids ``:`` in path components, but branch IDs use the convention
    ``<campaign>:<label>``.  We encode ``:`` as ``__`` so the on-disk layout
    remains portable.  Use :func:`_from_safe_segment` to reverse the mapping
    when reading directory names back as branch IDs.
    """
    return branch_id.replace(":", "__")


def _from_safe_segment(dir_name: str) -> str:
    """Reverse :func:`_safe_branch_segment` — convert a directory name back to a branch ID."""
    return dir_name.replace("__", ":")


def scenes_dir(data_root: Path, campaign_id: str, branch_id: str = "main") -> Path:
    if branch_id == "main":
        return data_root / "campaigns" / campaign_id / "scenes"
    return (
        data_root
        / "campaigns"
        / campaign_id
        / "branches"
        / _safe_branch_segment(branch_id)
        / "scenes"
    )


def scene_basename(ordinal: int, slug: str) -> str:
    return f"{ordinal:04d}-{slug}"


def scene_paths(data_root: Path, scene: Scene) -> tuple[Path, Path]:
    directory = scenes_dir(data_root, scene.campaign_id, scene.branch_id)
    base = scene_basename(scene.ordinal, scene.slug)
    return directory / f"{base}.md", directory / f"{base}.yaml"


def next_ordinal(data_root: Path, campaign_id: str, branch_id: str = "main") -> int:
    directory = scenes_dir(data_root, campaign_id, branch_id)
    if not directory.exists():
        return 1
    highest = 0
    for path in directory.glob("*.yaml"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def parse_author_label(label: str) -> tuple[AuthorKind, str | None, str | None]:
    label = label.strip()
    if label.startswith("pc:"):
        return AuthorKind.PC, label[3:].strip() or None, None
    if label.startswith("npc:"):
        return AuthorKind.NPC, None, label[4:].strip() or None
    if label == "narrator":
        return AuthorKind.NARRATOR, None, None
    if label == "system":
        return AuthorKind.SYSTEM, None, None
    return AuthorKind.NARRATOR, None, None


def format_author_label(post: Post) -> str:
    return post.author_label


def _scene_to_yaml(scene: Scene) -> dict:
    return {
        "id": scene.id,
        "campaign_id": scene.campaign_id,
        "branch_id": scene.branch_id,
        "ordinal": scene.ordinal,
        "slug": scene.slug,
        "title": scene.title,
        "location_ref": scene.location_ref,
        "in_game_start": scene.in_game_start.isoformat() if scene.in_game_start else None,
        "in_game_end": scene.in_game_end.isoformat() if scene.in_game_end else None,
        "greeting_id": scene.greeting_id,
        "pov_character_ref": scene.pov_character_ref,
        "present_character_refs": list(scene.present_character_refs),
        "present_pc_refs": list(scene.present_pc_refs),
        "mood": scene.mood,
        "post_count": scene.post_count,
        "threads_introduced": list(scene.threads_introduced),
        "threads_paid_off": list(scene.threads_paid_off),
        "tags": list(scene.tags),
        "closed": scene.closed,
        "closed_at_turn": scene.closed_at_turn,
        "last_advance_at_post": scene.last_advance_at_post,
        "running_summary": scene.running_summary,
        "final_summary": scene.final_summary,
        "key_beats": list(scene.key_beats),
    }


def _yaml_to_scene(data: dict) -> Scene:
    def parse_dt(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    return Scene(
        id=data["id"],
        campaign_id=data["campaign_id"],
        branch_id=data.get("branch_id", "main"),
        ordinal=int(data["ordinal"]),
        slug=data["slug"],
        title=data.get("title", data["slug"]),
        location_ref=data.get("location_ref"),
        in_game_start=parse_dt(data.get("in_game_start")),
        in_game_end=parse_dt(data.get("in_game_end")),
        greeting_id=data.get("greeting_id"),
        pov_character_ref=data.get("pov_character_ref"),
        present_character_refs=list(data.get("present_character_refs") or []),
        present_pc_refs=list(data.get("present_pc_refs") or []),
        mood=data.get("mood"),
        post_count=int(data.get("post_count") or 0),
        threads_introduced=list(data.get("threads_introduced") or []),
        threads_paid_off=list(data.get("threads_paid_off") or []),
        tags=list(data.get("tags") or []),
        closed=bool(data.get("closed", False)),
        closed_at_turn=data.get("closed_at_turn"),
        last_advance_at_post=int(data.get("last_advance_at_post") or 0),
        running_summary=data.get("running_summary"),
        final_summary=data.get("final_summary"),
        key_beats=list(data.get("key_beats") or []),
    )


def write_sidecar(path: Path, scene: Scene) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _scene_to_yaml(scene)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def read_sidecar(path: Path) -> Scene:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _yaml_to_scene(data)


def render_body(posts: Iterable[Post]) -> str:
    parts: list[str] = []
    for post in posts:
        parts.append(f"## Post {post.order_in_scene} — {format_author_label(post)}")
        parts.append("")
        parts.append(post.body.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_body(path: Path, posts: Iterable[Post]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_body(posts), encoding="utf-8")


def append_post_to_body(path: Path, post: Post) -> None:
    """Append a single post heading + body to the .md file without rewriting it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    heading = f"## Post {post.order_in_scene} — {format_author_label(post)}\n\n"
    body = post.body.rstrip() + "\n\n"
    with path.open("a", encoding="utf-8") as fh:
        if path.stat().st_size > 0:
            # Ensure a blank line separator if file already has content.
            fh.write("")
        fh.write(heading)
        fh.write(body)


PostTuple = tuple[int, AuthorKind, str | None, str | None, str]


def parse_body(text: str, scene_id: str) -> list[PostTuple]:
    """Parse a scene body into a list of post tuples.

    Returns ``[(order, author_kind, pc_ref, npc_ref, body), ...]``. The caller
    is responsible for attaching id/turn_id/created_at — those aren't in the
    markdown.
    """
    posts: list[tuple[int, AuthorKind, str | None, str | None, str]] = []
    matches = list(POST_HEADING_RE.finditer(text))
    for index, match in enumerate(matches):
        order = int(match.group(1))
        label = match.group(2)
        kind, pc_ref, npc_ref = parse_author_label(label)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip("\n").rstrip()
        posts.append((order, kind, pc_ref, npc_ref, body))
    return posts


def read_posts(path: Path, scene_id: str) -> list[PostTuple]:
    if not path.exists():
        return []
    return parse_body(path.read_text(encoding="utf-8"), scene_id)
