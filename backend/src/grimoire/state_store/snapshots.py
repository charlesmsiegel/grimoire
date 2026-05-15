"""Library snapshot management for version-pinned campaigns.

A campaign that binds a world with ``track_latest = False`` materializes
``library_index`` rows into ``library_snapshots`` keyed by ``(campaign_id,
branch_id, library_id)``. Reads on a pinned campaign consult snapshots first
and fall back to the live index only as a safety net.

Branch forks point at the same snapshot rows (no per-branch duplication) —
the read layer falls back from child branch → parent branch → main.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def write_snapshots_for_world(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str,
    branch_id: str,
    world_id: str,
    include: list[str] | None = None,
) -> int:
    """Copy library rows for ``world_id`` into ``library_snapshots``.

    ``include`` is a list of singular kinds; if ``None`` every kind under the
    world is snapshotted. Returns the number of rows written.
    """
    if include is None:
        rows = await (
            await conn.execute(
                "SELECT id, kind, frontmatter, body, version FROM library_index WHERE world_id = ?",
                (world_id,),
            )
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(include))
        if not include:
            return 0
        rows = await (
            await conn.execute(
                f"SELECT id, kind, frontmatter, body, version FROM library_index "
                f"WHERE world_id = ? AND kind IN ({placeholders})",
                (world_id, *include),
            )
        ).fetchall()

    now = _now_iso()
    written = 0
    for row in rows:
        await conn.execute(
            """
            INSERT INTO library_snapshots (
              campaign_id, branch_id, library_id, version, frontmatter, body, snapshot_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, branch_id, library_id) DO UPDATE SET
              version = excluded.version,
              frontmatter = excluded.frontmatter,
              body = excluded.body,
              snapshot_at = excluded.snapshot_at
            """,
            (
                campaign_id,
                branch_id,
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
    branch_id: str,
    world_id: str,
) -> int:
    cur = await conn.execute(
        """
        DELETE FROM library_snapshots
        WHERE campaign_id = ? AND branch_id = ? AND library_id IN (
          SELECT id FROM library_index WHERE world_id = ?
        )
        """,
        (campaign_id, branch_id, world_id),
    )
    return cur.rowcount or 0


async def upgrade_snapshots(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str,
    branch_id: str,
    world_id: str,
    include: list[str] | None = None,
) -> dict[str, dict]:
    """Refresh snapshots from the current ``library_index``.

    Returns a diff: ``{library_id: {"before": int, "after": int}}`` mapping
    library ids whose snapshot version changed.
    """
    before_rows = await (
        await conn.execute(
            """
            SELECT library_id, version FROM library_snapshots
            WHERE campaign_id = ? AND branch_id = ?
            """,
            (campaign_id, branch_id),
        )
    ).fetchall()
    before = {row["library_id"]: int(row["version"]) for row in before_rows}

    await write_snapshots_for_world(
        conn,
        campaign_id=campaign_id,
        branch_id=branch_id,
        world_id=world_id,
        include=include,
    )

    after_rows = await (
        await conn.execute(
            """
            SELECT library_id, version FROM library_snapshots
            WHERE campaign_id = ? AND branch_id = ?
            """,
            (campaign_id, branch_id),
        )
    ).fetchall()
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
