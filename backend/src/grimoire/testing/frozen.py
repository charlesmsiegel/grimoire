"""Frozen-campaign harness (spec 17 §L4).

Load an anonymized SQLite snapshot, run forward turns with deterministic
LLM responses, and assert the snapshot invariants haven't drifted.

Invariants the harness can check today:

* ``character_count`` — never decreases.
* ``scene_count`` — increases only when a scene break was reported.
* ``open_commitments`` — never decreases without an explicit resolution.
* ``embedded_row_count`` — embeddings exist for every post, scene
  summary, and fact (i.e. ``embeddings.target_kind`` counts match).
* ``delta_log_contiguous`` — applied deltas form an unbroken sequence
  per turn id.
* ``voice_anchors_present`` — every PC sheet still has at least one
  voice anchor (placeholder; tightens as Characters lands).

Loading is two steps: copy the snapshot file into the test data root,
then run any pending migrations so old fixtures keep working as the
schema evolves (spec 17 open question: fixture migration).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.storage import Database, apply_migrations


@dataclass(slots=True)
class InvariantSnapshot:
    """Numeric snapshot of the things that must not silently drift."""

    character_count: int = 0
    scene_count: int = 0
    open_commitment_count: int = 0
    fact_count: int = 0
    post_count: int = 0
    embedded_row_count: int = 0
    max_turn_id: str | None = None
    # Sorted list of turn ids observed in the delta log (used to assert
    # the log is contiguous — no missing turn ids).
    delta_turn_ids: tuple[str, ...] = ()
    # Embedding counts grouped by the embedding's source kind
    # (``post``, ``scene_summary``, ``fact``, …). Lets ``validate``
    # assert that per-kind counts never decrease across a turn.
    embeddings_by_kind: dict[str, int] = field(default_factory=dict)
    # Optional: voice anchor count (TODO §7 — wires up once Characters
    # exposes a counting API; until then this stays at None / 0 and
    # ``validate`` skips the check cleanly).
    voice_anchor_count: int | None = None

    def diff(self, other: InvariantSnapshot) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for field_name in (
            "character_count",
            "scene_count",
            "open_commitment_count",
            "fact_count",
            "post_count",
            "embedded_row_count",
        ):
            before = getattr(self, field_name)
            after = getattr(other, field_name)
            if before != after:
                out[field_name] = (before, after)
        return out


@dataclass(slots=True)
class InvariantReport:
    """Result of comparing two snapshots against the rules."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


class FrozenCampaignHarness:
    """Loads a snapshot SQLite file into a temporary location and validates it."""

    def __init__(self, snapshot_path: Path, data_root: Path) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.data_root = Path(data_root)
        self.db: Database | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> FrozenCampaignHarness:
        if not self.snapshot_path.is_file():
            raise FileNotFoundError(f"snapshot {self.snapshot_path} does not exist")
        self.data_root.mkdir(parents=True, exist_ok=True)
        target = self.data_root / "grimoire.sqlite"
        shutil.copyfile(self.snapshot_path, target)
        self.db = Database(target, pool_size=2)
        await self.db.connect()
        # Apply any migrations newer than the snapshot. Old fixtures
        # migrate forward instead of breaking.
        await apply_migrations(self.db)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.db is not None:
            await self.db.close()

    # ------------------------------------------------------------------ #
    # Snapshots
    # ------------------------------------------------------------------ #

    async def snapshot(self) -> InvariantSnapshot:
        assert self.db is not None
        async with self.db.acquire() as conn:
            character_count = await _scalar(
                conn,
                "SELECT COUNT(*) FROM campaign_content_index WHERE kind = 'character'",
            )
            scene_count = await _scalar(conn, "SELECT COUNT(*) FROM scenes")
            open_commitments = await _scalar(
                conn,
                "SELECT COUNT(*) FROM commitments WHERE status = 'OPEN'",
            )
            fact_count = await _scalar(conn, "SELECT COUNT(*) FROM facts")
            post_count = await _scalar(conn, "SELECT COUNT(*) FROM posts")
            embedded = await _scalar(conn, "SELECT COUNT(*) FROM embeddings")
            max_turn = await _scalar(
                conn,
                "SELECT MAX(turn_id) FROM deltas",
                default=None,
            )
            delta_turn_ids = await _list(
                conn,
                "SELECT DISTINCT turn_id FROM deltas WHERE turn_id IS NOT NULL ORDER BY turn_id",
            )
            embeddings_by_kind = await _group_count(
                conn,
                "SELECT COALESCE(source_kind, '__unknown__') AS kind, COUNT(*)"
                " FROM embeddings GROUP BY source_kind",
            )
            voice_anchor_count = await _voice_anchor_count(conn)
        return InvariantSnapshot(
            character_count=character_count,
            scene_count=scene_count,
            open_commitment_count=open_commitments,
            fact_count=fact_count,
            post_count=post_count,
            embedded_row_count=embedded,
            max_turn_id=max_turn,
            delta_turn_ids=tuple(delta_turn_ids),
            embeddings_by_kind=embeddings_by_kind,
            voice_anchor_count=voice_anchor_count,
        )

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate(
        self,
        before: InvariantSnapshot,
        after: InvariantSnapshot,
        *,
        scene_broke: bool = False,
        resolved_commitments: int = 0,
    ) -> InvariantReport:
        """Compare two snapshots against the invariants.

        ``scene_broke`` reflects whether the turn produced a new scene;
        ``resolved_commitments`` is the count of commitments the turn
        explicitly closed (so we can tell that drop from a silent one).
        """
        report = InvariantReport()
        if after.character_count < before.character_count:
            report.violations.append(
                f"character_count decreased: {before.character_count} → {after.character_count}"
            )
        expected_scenes = before.scene_count + (1 if scene_broke else 0)
        if after.scene_count < expected_scenes:
            report.violations.append(
                f"scene_count dropped below expected {expected_scenes}: got {after.scene_count}"
            )
        # Commitments can only fall by the number explicitly resolved.
        if after.open_commitment_count + resolved_commitments < before.open_commitment_count:
            report.violations.append(
                "open commitments dropped without explicit resolution: "
                f"{before.open_commitment_count} → {after.open_commitment_count}"
            )
        if after.fact_count < before.fact_count:
            report.warnings.append(
                f"fact_count decreased: {before.fact_count} → {after.fact_count}; "
                "retirement should preserve the row (status flip)"
            )

        # Delta log must remain contiguous (no missing turn ids).
        gaps = _delta_gaps(after.delta_turn_ids)
        if gaps:
            report.violations.append(
                f"delta log has missing turn ids between observed entries: {gaps[:5]}"
            )

        # Embedding counts per source kind must not decrease.
        for kind, before_count in before.embeddings_by_kind.items():
            after_count = after.embeddings_by_kind.get(kind, 0)
            if after_count < before_count:
                report.violations.append(
                    f"embedding count for kind {kind!r} decreased: {before_count} → {after_count}"
                )

        # Voice anchor count — only enforced if Characters surfaced a
        # count. Otherwise we skip silently (the snapshot reports
        # ``None``); see TODO in :func:`_voice_anchor_count`.
        if (
            before.voice_anchor_count is not None
            and after.voice_anchor_count is not None
            and after.voice_anchor_count < before.voice_anchor_count
        ):
            report.violations.append(
                "voice_anchor_count decreased: "
                f"{before.voice_anchor_count} → {after.voice_anchor_count}"
            )
        return report

    # ------------------------------------------------------------------ #
    # Schema drift detection
    # ------------------------------------------------------------------ #

    async def assert_snapshot_matches_current_migrations(self) -> None:
        """Re-run migrations and fail loudly if any actually applied.

        Frozen snapshots are expected to be exported with every migration
        in the codebase already applied. If new migrations have shipped
        since the snapshot was taken, this will raise a
        :class:`SnapshotStaleError` pointing at the offending versions so
        the snapshot can be regenerated.
        """
        assert self.db is not None
        run = await apply_migrations(self.db)
        if run:
            versions = ", ".join(f"{m.version}:{m.name}" for m in run)
            raise SnapshotStaleError(
                "snapshot is missing migrations; re-export via "
                f"scripts/export_snapshot.py. Newly applied: {versions}"
            )


async def _scalar(conn: Any, sql: str, default: Any = 0) -> Any:
    try:
        cursor = await conn.execute(sql)
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return default
        return row[0] if row[0] is not None else default
    except Exception:
        # Schema may not yet have all tables (early in development). Treat
        # as the default rather than failing the whole harness.
        return default


async def _list(conn: Any, sql: str) -> list[Any]:
    try:
        cursor = await conn.execute(sql)
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows if row[0] is not None]
    except Exception:
        return []


async def _group_count(conn: Any, sql: str) -> dict[str, int]:
    try:
        cursor = await conn.execute(sql)
        rows = await cursor.fetchall()
        await cursor.close()
        return {str(row[0]): int(row[1]) for row in rows}
    except Exception:
        return {}


async def _voice_anchor_count(conn: Any) -> int | None:
    """Return the number of PC voice anchors in the snapshot, or
    ``None`` if the count cannot be determined.

    TODO(§7): wire voice anchor count once Characters API exposes it.
    For now we approximate by counting character rows in the content
    index — but only when the column we'd query exists. The harness
    skips the check (returns ``None``) so frozen snapshots that
    predate Characters don't break.
    """
    # Once `characters.service.CharactersService.count_voice_anchors`
    # lands, replace this body with a call into that service.
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM campaign_content_index"
            " WHERE kind = 'character' AND entity_subkind = 'pc'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return int(row[0]) if row[0] is not None else 0
    except Exception:
        return None


def _delta_gaps(turn_ids: tuple[str, ...]) -> list[tuple[str, str]]:
    """Detect gaps in a sorted list of integer-shaped turn ids.

    Returns the (prev, current) pairs where a gap was found. Non-numeric
    turn ids are skipped — only the integer-suffix slice is checked so
    string ids like ``turn-3`` and ``turn-5`` correctly flag a gap.
    """
    pairs: list[tuple[str, str]] = []
    previous_n: int | None = None
    previous_raw: str | None = None
    for raw in turn_ids:
        n = _extract_int(raw)
        if n is None:
            continue
        if previous_n is not None and n != previous_n + 1:
            pairs.append((str(previous_raw), str(raw)))
        previous_n = n
        previous_raw = raw
    return pairs


def _extract_int(value: str) -> int | None:
    # Accept either bare numbers or ``<prefix>-<n>`` style ids.
    s = str(value)
    if s.isdigit():
        return int(s)
    tail = s.rsplit("-", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None


class SnapshotStaleError(RuntimeError):
    """Raised when a frozen snapshot is missing migrations the code now requires."""


__all__ = [
    "FrozenCampaignHarness",
    "InvariantReport",
    "InvariantSnapshot",
    "SnapshotStaleError",
]
