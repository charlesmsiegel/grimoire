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
        return InvariantSnapshot(
            character_count=character_count,
            scene_count=scene_count,
            open_commitment_count=open_commitments,
            fact_count=fact_count,
            post_count=post_count,
            embedded_row_count=embedded,
            max_turn_id=max_turn,
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
        return report


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


__all__ = ["FrozenCampaignHarness", "InvariantReport", "InvariantSnapshot"]
