"""Library snapshot management for version-pinned campaigns.

A campaign that binds a world with ``track_latest = False`` materializes
``library_index`` rows into ``library_snapshots`` keyed by ``(campaign_id,
library_id)``. Reads on a pinned campaign consult snapshots first and fall
back to the live index only as a safety net.
"""

from __future__ import annotations

import aiosqlite

from grimoire.util import now_iso


async def write_snapshots_for_world(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str,
    world_id: str,
    include: list[str] | None = None,
) -> int:
    """Copy library rows for ``world_id`` into ``library_snapshots``.

    ``include`` is a list of singular kinds; if ``None`` every kind under the
    world is snapshotted. Returns the number of rows written.
    """
    if include is None:
        async with conn.execute(
            "SELECT id, kind, frontmatter, body, version FROM library_index WHERE world_id = ?",
            (world_id,),
        ) as cur:
            rows = await cur.fetchall()
    else:
        if not include:
            return 0
        placeholders = ",".join("?" * len(include))
        async with conn.execute(
            f"SELECT id, kind, frontmatter, body, version FROM library_index "
            f"WHERE world_id = ? AND kind IN ({placeholders})",
            (world_id, *include),
        ) as cur:
            rows = await cur.fetchall()

    now = now_iso()
    written = 0
    for row in rows:
        await conn.execute(
            """
            INSERT INTO library_snapshots (
              campaign_id, library_id, version, frontmatter, body, snapshot_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, library_id) DO UPDATE SET
              version = excluded.version,
              frontmatter = excluded.frontmatter,
              body = excluded.body,
              snapshot_at = excluded.snapshot_at
            """,
            (
                campaign_id,
                row["id"],
                int(row["version"]),
                row["frontmatter"],
                row["body"],
                now,
            ),
        )
        written += 1
    return written


async def remove_snapshots_for_world(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str,
    world_id: str,
) -> int:
    cur = await conn.execute(
        """
        DELETE FROM library_snapshots
        WHERE campaign_id = ? AND library_id IN (
          SELECT id FROM library_index WHERE world_id = ?
        )
        """,
        (campaign_id, world_id),
    )
    return cur.rowcount or 0


async def upgrade_snapshots(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str,
    world_id: str,
    include: list[str] | None = None,
) -> dict[str, dict]:
    """Refresh snapshots from the current ``library_index``."""
    async with conn.execute(
        """
        SELECT library_id, version FROM library_snapshots
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ) as cur:
        before_rows = await cur.fetchall()
    before = {row["library_id"]: int(row["version"]) for row in before_rows}

    await write_snapshots_for_world(
        conn,
        campaign_id=campaign_id,
        world_id=world_id,
        include=include,
    )

    async with conn.execute(
        """
        SELECT library_id, version FROM library_snapshots
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ) as cur:
        after_rows = await cur.fetchall()
    after = {row["library_id"]: int(row["version"]) for row in after_rows}

    diff: dict[str, dict] = {}
    for library_id, new_version in after.items():
        old_version = before.get(library_id)
        if old_version != new_version:
            diff[library_id] = {"before": old_version, "after": new_version}
    return diff


__all__ = [
    "remove_snapshots_for_world",
    "upgrade_snapshots",
    "write_snapshots_for_world",
]
