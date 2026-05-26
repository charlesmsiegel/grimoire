"""Index upsert/delete helpers for ``library_index`` and ``campaign_content_index``.

Index rows are derived from files. Each helper takes the parsed frontmatter +
body and writes a row whose ``content_hash`` matches the file. Versions bump
when the hash changes so consumers can detect real edits.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from grimoire.files import content_hash
from grimoire.state_store.paths import (
    DIR_TO_KIND,
    KIND_TO_DIR,
    LibraryRef,
    parse_library_id,
    relative_to_root,
)
from grimoire.util import now_iso


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _json_or_none(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _name_from(frontmatter: dict, fallback: str) -> str:
    for key in ("name", "title", "id"):
        v = frontmatter.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return fallback


async def upsert_library_index(
    conn: aiosqlite.Connection,
    *,
    data_root: Path,
    library_id: str,
    path: Path,
    frontmatter: dict,
    body: str,
) -> int:
    """Insert or update a ``library_index`` row. Returns the new version.

    Version increments by 1 whenever the content_hash changes; unchanged
    rows keep their existing version.
    """
    ref = parse_library_id(library_id)
    body_for_hash = body if body is not None else ""
    serialized = json.dumps(frontmatter, sort_keys=True) + "\n" + body_for_hash
    chash = content_hash(serialized)

    cur = await conn.execute(
        "SELECT version, content_hash FROM library_index WHERE id = ?",
        (library_id,),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is not None and row["content_hash"] == chash:
        # No content change; touch indexed_at + mtime but keep version.
        await conn.execute(
            """
            UPDATE library_index
            SET path = ?, file_mtime = ?, indexed_at = ?
            WHERE id = ?
            """,
            (relative_to_root(data_root, path), file_mtime_iso(path), now_iso(), library_id),
        )
        return int(row["version"])

    new_version = (int(row["version"]) + 1) if row else 1
    tags = frontmatter.get("tags") or []
    keywords = frontmatter.get("keywords") or []
    name = _name_from(frontmatter, ref.asset_id)

    await conn.execute(
        """
        INSERT INTO library_index (
          id, world_id, kind, asset_id, name, path, frontmatter, body,
          body_compressed, tags, keywords, file_mtime, content_hash, indexed_at, version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          world_id = excluded.world_id,
          kind = excluded.kind,
          asset_id = excluded.asset_id,
          name = excluded.name,
          path = excluded.path,
          frontmatter = excluded.frontmatter,
          body = excluded.body,
          tags = excluded.tags,
          keywords = excluded.keywords,
          file_mtime = excluded.file_mtime,
          content_hash = excluded.content_hash,
          indexed_at = excluded.indexed_at,
          version = excluded.version
        """,
        (
            library_id,
            ref.world_id,
            ref.kind,
            ref.asset_id,
            name,
            relative_to_root(data_root, path),
            json.dumps(frontmatter, sort_keys=True),
            body,
            _json_or_none(tags),
            _json_or_none(keywords),
            file_mtime_iso(path),
            chash,
            now_iso(),
            new_version,
        ),
    )
    return new_version


async def delete_library_index_row(conn: aiosqlite.Connection, library_id: str) -> None:
    await conn.execute("DELETE FROM library_index WHERE id = ?", (library_id,))


async def upsert_campaign_content_index(
    conn: aiosqlite.Connection,
    *,
    data_root: Path,
    campaign_id: str,
    composite_id: str,
    kind: str,  # 'scene', 'override', 'emergent', 'sheet', 'image'
    entity_subkind: str | None,
    asset_id: str | None,
    path: Path,
    frontmatter: dict | None,
    body: str | None,
) -> None:
    serialized = (
        (json.dumps(frontmatter, sort_keys=True) if frontmatter is not None else "")
        + "\n"
        + (body if body is not None else "")
    )
    chash = content_hash(serialized)
    await conn.execute(
        """
        INSERT INTO campaign_content_index (
          id, campaign_id, kind, entity_subkind, asset_id, path,
          frontmatter, body, file_mtime, content_hash, indexed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          campaign_id = excluded.campaign_id,
          kind = excluded.kind,
          entity_subkind = excluded.entity_subkind,
          asset_id = excluded.asset_id,
          path = excluded.path,
          frontmatter = excluded.frontmatter,
          body = excluded.body,
          file_mtime = excluded.file_mtime,
          content_hash = excluded.content_hash,
          indexed_at = excluded.indexed_at
        """,
        (
            composite_id,
            campaign_id,
            kind,
            entity_subkind,
            asset_id,
            relative_to_root(data_root, path),
            _json_or_none(frontmatter) if frontmatter is not None else None,
            body,
            file_mtime_iso(path),
            chash,
            now_iso(),
        ),
    )


async def delete_campaign_content_row(conn: aiosqlite.Connection, composite_id: str) -> None:
    await conn.execute("DELETE FROM campaign_content_index WHERE id = ?", (composite_id,))


def make_library_id(world_id: str | None, kind: str, asset_id: str) -> str:
    """Inverse of :func:`parse_library_id`."""
    if kind == "world":
        # Match the watcher classifier's canonical form (and the row id it
        # writes when indexing world.yaml from disk). parse_library_id also
        # accepts the shorter ``worlds/<id>`` form, but library_index rows
        # are keyed on the 3-segment form, so direct lookups need it too.
        return f"worlds/{asset_id}/world"
    if kind == "style_guide":
        return f"style-guides/{asset_id}"
    if kind == "image_preset":
        return f"image-presets/{asset_id}"
    if kind == "calendar":
        return f"calendars/{asset_id}"
    if kind == "holiday_set":
        return f"holiday-sets/{asset_id}"
    if world_id is None:
        raise ValueError(f"kind {kind!r} requires a world_id")
    dir_name = KIND_TO_DIR.get(kind, kind)
    return f"worlds/{world_id}/{dir_name}/{asset_id}"


__all__ = [
    "DIR_TO_KIND",
    "KIND_TO_DIR",
    "LibraryRef",
    "delete_campaign_content_row",
    "delete_library_index_row",
    "file_mtime_iso",
    "make_library_id",
    "upsert_campaign_content_index",
    "upsert_library_index",
]
