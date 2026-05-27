"""Markdown + YAML sidecar storage for scenes.

Two files per scene under ``data/campaigns/<campaign_id>/scenes/``::

    0001-elysium-opening.md       # prose, posts in order
    0001-elysium-opening.yaml     # metadata sidecar

The ``.md`` file is the prose source of truth; the ``.yaml`` file is the
metadata source of truth. Both can be hand-edited; the watcher (task #9) will
reindex them. Helpers here are pure I/O — they don't update SQLite indexes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import yaml

from grimoire.files import load_yaml
from grimoire.files import slugify as _base_slugify

# Canonical impl lives in :mod:`grimoire.files.hashing` and normalizes line
# endings before hashing; re-exported here for the scenes module's callers.
from grimoire.files.hashing import content_hash  # noqa: F401  (re-exported)
from grimoire.scenes.types import Alternate, AuthorKind, Post, Scene

POST_HEADING_RE = re.compile(r"^##\s+Post\s+(\d+)\s+(?:—|--?)\s+(.+?)\s*$", re.MULTILINE)

# Defaults for the file/heading naming patterns. Both are exposed via
# ``SceneManagerConfig.files`` so a campaign can override them without
# patching this module. ``scene_naming_pattern`` is rendered with
# ``ordinal=<int>`` and ``slug=<str>``; ``post_heading_pattern`` with
# ``order=<int>`` and ``author=<str>``.
DEFAULT_SCENE_NAMING_PATTERN = "{ordinal:04d}-{slug}"
DEFAULT_POST_HEADING_PATTERN = "## Post {order} — {author}"


def slugify(text: str) -> str:
    """Scene-specific slug: delegates to :func:`grimoire.files.slugify` but
    falls back to ``"scene"`` (not ``"untitled"``) so empty titles produce
    ``0001-scene.md`` rather than ``0001-untitled.md``.

    Slugs are truncated to 60 characters (the canonical helper's default
    ``max_len``), so scene filenames stay below typical filesystem limits
    even when titles are long. A title that produces a >60-char slug will
    be silently shortened — pass an explicit ``init.slug`` to
    :meth:`SceneManager.start_scene` if the full form is needed.
    """
    return _base_slugify(text, fallback="scene")


def scenes_dir(data_root: Path, campaign_id: str) -> Path:
    return data_root / "campaigns" / campaign_id / "scenes"


def scene_basename(
    ordinal: int,
    slug: str,
    pattern: str = DEFAULT_SCENE_NAMING_PATTERN,
) -> str:
    return pattern.format(ordinal=ordinal, slug=slug)


def scene_paths(
    data_root: Path,
    scene: Scene,
    *,
    naming_pattern: str = DEFAULT_SCENE_NAMING_PATTERN,
) -> tuple[Path, Path]:
    directory = scenes_dir(data_root, scene.campaign_id)
    base = scene_basename(scene.ordinal, scene.slug, naming_pattern)
    return directory / f"{base}.md", directory / f"{base}.yaml"


def next_ordinal(data_root: Path, campaign_id: str) -> int:
    directory = scenes_dir(data_root, campaign_id)
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


def _threads_to_yaml(threads: list) -> list[dict]:
    """Serialize a list[Thread] to the sidecar's dict form."""
    return [
        {
            "text": t.text,
            "introduced_at_post": t.introduced_at_post,
            "paid_off_at_post": t.paid_off_at_post,
        }
        for t in threads
    ]


def _yaml_to_threads(value: object) -> list:
    """Parse the sidecar's thread list back to list[Thread].

    Accepts both the new ``[{text, introduced_at_post, paid_off_at_post}, ...]``
    shape and the legacy ``["string", ...]`` shape so older sidecars keep
    loading after the schema bump.
    """
    from grimoire.scenes.types import Thread  # local import: avoid cycle

    out: list = []
    for item in value or []:
        if isinstance(item, str):
            out.append(Thread(text=item))
        elif isinstance(item, dict):
            out.append(
                Thread(
                    text=str(item.get("text", "")),
                    introduced_at_post=item.get("introduced_at_post"),
                    paid_off_at_post=item.get("paid_off_at_post"),
                )
            )
    return out


def _alternate_to_yaml(alt: Alternate) -> dict:
    return {
        "id": alt.id,
        "post_id": alt.post_id,
        "delta_set_id": alt.delta_set_id,
        "text": alt.text,
        "author_kind": alt.author_kind.value
        if hasattr(alt.author_kind, "value")
        else str(alt.author_kind),
        "model": alt.model,
        "prompt_hash": alt.prompt_hash,
        "steering_hint": alt.steering_hint,
        "tokens": alt.tokens,
        "pinned": bool(alt.pinned),
        "is_primary": bool(alt.is_primary),
        "created_at": alt.created_at.isoformat() if alt.created_at else None,
        "replay_batch_id": alt.replay_batch_id,
    }


def _yaml_to_alternate(data: dict) -> Alternate:
    def parse_dt(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    raw_kind = data.get("author_kind") or "narrator"
    try:
        kind = AuthorKind(raw_kind)
    except ValueError:
        kind = AuthorKind.NARRATOR
    return Alternate(
        id=str(data.get("id") or ""),
        post_id=str(data.get("post_id") or ""),
        text=str(data.get("text") or ""),
        delta_set_id=str(data.get("delta_set_id") or ""),
        author_kind=kind,
        model=data.get("model"),
        prompt_hash=data.get("prompt_hash"),
        steering_hint=data.get("steering_hint"),
        tokens=data.get("tokens"),
        pinned=bool(data.get("pinned", False)),
        is_primary=bool(data.get("is_primary", False)),
        created_at=parse_dt(data.get("created_at")),
        replay_batch_id=(data.get("replay_batch_id") or None),
    )


def _post_records_to_yaml(records: dict) -> list[dict]:
    """Serialize the in-memory ``_PostRecord`` cache to the sidecar.

    Includes ``alternates`` and ``primary_alternate_id`` when present; emits a
    minimal legacy-compatible row when alternates are empty.
    """
    rows = []
    for order_str, rec in sorted(records.items(), key=lambda kv: int(kv[0])):
        row: dict = {
            "order": int(order_str),
            "id": rec.id,
            "turn_id": rec.turn_id,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "is_player": bool(rec.is_player),
        }
        alternates = getattr(rec, "alternates", None) or []
        if alternates:
            row["primary_alternate_id"] = getattr(rec, "primary_alternate_id", None)
            row["alternates"] = [_alternate_to_yaml(a) for a in alternates]
        rows.append(row)
    return rows


def _scene_to_yaml(scene: Scene, post_records: dict | None = None) -> dict:
    data = {
        "id": scene.id,
        "campaign_id": scene.campaign_id,
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
        "threads_introduced": _threads_to_yaml(scene.threads_introduced),
        "threads_paid_off": _threads_to_yaml(scene.threads_paid_off),
        "tags": list(scene.tags),
        "closed": scene.closed,
        "closed_at_turn": scene.closed_at_turn,
        "last_advance_at_post": scene.last_advance_at_post,
        "running_summary": scene.running_summary,
        "final_summary": scene.final_summary,
        "key_beats": list(scene.key_beats),
        "narrator_response_mode": scene.narrator_response_mode,
    }
    if post_records is not None:
        data["posts"] = _post_records_to_yaml(post_records)
    return data


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
        threads_introduced=_yaml_to_threads(data.get("threads_introduced")),
        threads_paid_off=_yaml_to_threads(data.get("threads_paid_off")),
        tags=list(data.get("tags") or []),
        closed=bool(data.get("closed", False)),
        closed_at_turn=data.get("closed_at_turn"),
        last_advance_at_post=int(data.get("last_advance_at_post") or 0),
        running_summary=data.get("running_summary"),
        final_summary=data.get("final_summary"),
        key_beats=list(data.get("key_beats") or []),
        narrator_response_mode=_normalize_response_mode(data.get("narrator_response_mode")),
    )


def _normalize_response_mode(value: object) -> str | None:
    """Drop unknown values so a hand-edited sidecar can't break the resolver."""
    from grimoire.scenes.narrator_mode import normalize_response_mode

    return normalize_response_mode(value)


def read_sidecar_post_records(path: Path) -> dict:
    """Read the ``posts`` block from a sidecar and return it as the in-memory
    record dict shape (``{order_str: _PostRecord, ...}``).

    Returns an empty dict if the sidecar has no posts block (legacy file) or
    the file does not exist.
    """
    if not path.exists():
        return {}
    from grimoire.scenes.manager import _PostRecord  # local import: avoid cycle

    data = load_yaml(path) or {}
    rows = data.get("posts") or []
    out: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        order = row.get("order")
        if order is None:
            continue
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.fromtimestamp(0)
        elif not isinstance(created_at, datetime):
            created_at = datetime.fromtimestamp(0)
        raw_alts = row.get("alternates") or []
        alts = [_yaml_to_alternate(a) for a in raw_alts if isinstance(a, dict)]
        out[str(int(order))] = _PostRecord(
            id=str(row.get("id") or ""),
            turn_id=str(row.get("turn_id") or ""),
            created_at=created_at,
            is_player=bool(row.get("is_player", False)),
            alternates=alts,
            primary_alternate_id=row.get("primary_alternate_id"),
        )
    return out


def write_sidecar(path: Path, scene: Scene, *, post_records: dict | None = None) -> None:
    """Write the YAML sidecar.

    When ``post_records`` is given (the in-memory ``_PostRecord`` cache for
    the scene) the per-post identity is persisted under a ``posts:`` block so
    ``id``/``turn_id``/``created_at``/``is_player`` survive process restart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _scene_to_yaml(scene, post_records=post_records)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def read_sidecar(path: Path) -> Scene:
    data = load_yaml(path) or {}
    return _yaml_to_scene(data)


def render_body(
    posts: Iterable[Post],
    heading_pattern: str = DEFAULT_POST_HEADING_PATTERN,
) -> str:
    parts: list[str] = []
    for post in posts:
        parts.append(
            heading_pattern.format(
                order=post.order_in_scene,
                author=format_author_label(post),
            )
        )
        parts.append("")
        parts.append(post.body.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_body(
    path: Path,
    posts: Iterable[Post],
    *,
    heading_pattern: str = DEFAULT_POST_HEADING_PATTERN,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_body(posts, heading_pattern), encoding="utf-8")


def append_post_to_body(
    path: Path,
    post: Post,
    *,
    heading_pattern: str = DEFAULT_POST_HEADING_PATTERN,
) -> None:
    """Append a single post heading + body to the .md file without rewriting it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    heading_line = heading_pattern.format(
        order=post.order_in_scene,
        author=format_author_label(post),
    )
    heading = f"{heading_line}\n\n"
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
