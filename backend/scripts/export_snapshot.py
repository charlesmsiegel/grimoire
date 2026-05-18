"""Export an anonymized frozen-campaign snapshot (spec 17 §L4).

Run via::

    uv run python scripts/export_snapshot.py --source <src.sqlite> --output <dst.sqlite>

The script:

1. Copies the source file into the destination.
2. Runs every pending migration so the snapshot matches the current
   schema (fixtures forward-migrate automatically when loaded by the
   :class:`FrozenCampaignHarness`).
3. Runs the configured anonymizer to redact campaign prose.
4. VACUUMs the file so the on-disk size shrinks before commit.
5. Persists the anonymizer mapping as ``<dst>.anonymizer.json``.

The ``--seed-minimal`` flag is a self-contained shortcut for the
synthetic ``minimal_test_campaign`` fixture: it builds an empty
database, applies migrations, optionally inserts a couple of canonical
rows, and runs the same VACUUM step. No source file required.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
from pathlib import Path

from grimoire.storage import Database, apply_migrations
from grimoire.testing.anonymizer import Anonymizer, sidecar_path


def _vacuum(path: Path) -> None:
    """VACUUM the database, repacking with the smallest SQLite page size.

    512 bytes is the SQLite minimum. For synthetic fixtures with only
    a handful of rows this trims the file from ~500KB (the default 4KB
    page size cost of every empty table) to well under 50KB. The
    page_size pragma must be set on the same connection that runs
    VACUUM, before any other write happens; otherwise SQLite ignores
    it and the file stays at its original page size.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        # SQLite refuses to change page_size while in WAL mode; switch
        # to journal mode DELETE first.
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA page_size = 512")
        conn.execute("VACUUM")
    finally:
        conn.close()


async def export_snapshot(
    source: Path,
    output: Path,
    *,
    anonymizer: Anonymizer | None = None,
) -> Path:
    """Copy ``source`` → ``output``, migrate forward, anonymize, vacuum.

    Returns the output path. The anonymizer mapping (if any) is written
    next to ``output`` as ``<output>.anonymizer.json``.
    """
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)

    db = Database(output, pool_size=1)
    await db.connect()
    try:
        await apply_migrations(db)
    finally:
        await db.close()

    if anonymizer is not None:
        anonymizer.anonymize_sqlite(output)
        anonymizer.save_mapping(sidecar_path(output))

    _vacuum(output)
    return output


async def seed_minimal(output: Path) -> Path:
    """Build the synthetic ``minimal_test_campaign`` snapshot from scratch.

    No real data — a single campaign row + branch + one scene so the
    invariants harness has something non-trivial to look at without
    pulling personal prose into the repo.
    """
    output = Path(output)
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    db = Database(output, pool_size=1)
    await db.connect()
    try:
        await apply_migrations(db)
        await db.execute(
            "INSERT INTO campaigns (id, name, description, created_at)"
            " VALUES (?, ?, ?, datetime('now'))",
            ("cmp-min", "Minimal Test Campaign", "synthetic"),
        )
        await db.execute(
            "INSERT INTO branches (id, campaign_id, parent_branch_id, label, rng_seed,"
            " created_at) VALUES (?, ?, NULL, ?, ?, datetime('now'))",
            ("b-main", "cmp-min", "main", 0),
        )
        await db.execute(
            "INSERT INTO scenes (id, campaign_id, branch_id, ordinal, slug, file_path,"
            " summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s-0", "cmp-min", "b-main", 0, "intro", "scenes/intro.md", "Opening scene."),
        )
        # A pair of contiguous deltas so the contiguity invariant has
        # something to check (turn ids ``t-0`` and ``t-1``).
        for turn in ("t-0", "t-1"):
            await db.execute(
                "INSERT INTO deltas (id, campaign_id, branch_id, turn_id, source, kind)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (f"d-{turn}", "cmp-min", "b-main", turn, "extractor", "note"),
            )
    finally:
        await db.close()
    _vacuum(output)
    return output


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export an anonymized SQLite snapshot.")
    parser.add_argument("--source", type=Path, help="Source SQLite path.")
    parser.add_argument("--output", type=Path, required=True, help="Output SQLite path.")
    parser.add_argument(
        "--seed-minimal",
        action="store_true",
        help="Skip --source and generate the synthetic minimal_test_campaign snapshot.",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="Add a name → replacement pair as 'Original=Replacement'. May be repeated.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.seed_minimal:
        asyncio.run(seed_minimal(args.output))
        return 0
    if args.source is None:
        raise SystemExit("--source is required unless --seed-minimal is given")
    anonymizer: Anonymizer | None = None
    if args.name:
        anonymizer = Anonymizer()
        for entry in args.name:
            if "=" not in entry:
                raise SystemExit(f"--name expects 'Original=Replacement', got {entry!r}")
            original, replacement = entry.split("=", 1)
            anonymizer.add(original.strip(), replacement.strip())
    asyncio.run(export_snapshot(args.source, args.output, anonymizer=anonymizer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
