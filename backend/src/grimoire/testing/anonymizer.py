"""Snapshot anonymization pipeline (spec 17 §L4 / fixture privacy).

Frozen campaign snapshots and recorded LLM fixtures contain real
campaign prose. Before they land in the repo we run them through an
:class:`Anonymizer` that rewrites a configurable set of names (people
and places) to a stable synthetic vocabulary. The rewrite is a simple
case-insensitive whole-word substitution; the mapping is persisted as a
sidecar JSON file so re-recording yields the same renames.

Two public surfaces:

* :meth:`Anonymizer.anonymize_text` — rewrite a single string. Used by
  :class:`RecordReplayLLM` to redact completion prose and embedding
  inputs before they hit disk.
* :meth:`Anonymizer.anonymize_sqlite` — rewrite the prose columns of a
  campaign snapshot in-place. Tolerant of missing tables so early
  schemas don't crash the pipeline.

A per-test escape hatch (:meth:`Anonymizer.with_passthrough`) returns a
shallow clone that won't rewrite a chosen set of names — useful when a
specific test needs the original name to match a fixture.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A small, replaceable default vocabulary. Real fixtures should bring
# their own dictionaries via the constructor.
DEFAULT_FIRST_NAMES: tuple[str, ...] = (
    "Alice",
    "Bob",
    "Carol",
    "Dan",
    "Eve",
    "Frank",
    "Grace",
    "Hank",
    "Iris",
    "Jude",
)

DEFAULT_PLACE_NAMES: tuple[str, ...] = (
    "Riverdell",
    "Greycoast",
    "Highmoor",
    "Ashvale",
    "Northkeep",
    "Bramblefen",
    "Stonereach",
    "Westwind",
)

# Tables/columns the in-place sqlite anonymizer will rewrite. Tolerant
# of tables that don't exist yet in older snapshots.
SQLITE_TARGETS: tuple[tuple[str, str, str], ...] = (
    # Columns are table, primary_key, column_to_rewrite
    ("posts", "id", "body_excerpt"),
    ("scenes", "id", "summary"),
    ("scenes", "id", "running_summary"),
    ("scenes", "id", "title"),
    ("facts", "id", "text"),
    ("commitments", "id", "text"),
)


@dataclass
class _Rule:
    """A compiled rewrite rule."""

    original: str
    replacement: str
    pattern: re.Pattern[str]


class Anonymizer:
    """Configurable name-rewriting pipeline.

    The class is intentionally small: it owns a vocabulary (a mapping
    from real names to anonymized stand-ins) and a compiled regex per
    rule. Callers build the vocabulary themselves or let
    :meth:`learn_names` populate it from a list of real names by
    cycling through the default first/place name pools.
    """

    def __init__(
        self,
        mapping: Mapping[str, str] | None = None,
        *,
        first_names: Iterable[str] = DEFAULT_FIRST_NAMES,
        place_names: Iterable[str] = DEFAULT_PLACE_NAMES,
        passthrough: Iterable[str] = (),
    ) -> None:
        self._first_pool: list[str] = list(first_names)
        self._place_pool: list[str] = list(place_names)
        self._passthrough: set[str] = {name.lower() for name in passthrough}
        self._mapping: dict[str, str] = {}
        self._rules: list[_Rule] = []
        if mapping:
            for original, replacement in mapping.items():
                self.add(original, replacement)

    # ------------------------------------------------------------------ #
    # Mapping management
    # ------------------------------------------------------------------ #

    def add(self, original: str, replacement: str) -> None:
        """Add a rewrite rule. ``original`` is matched case-insensitively
        as a whole word; ``replacement`` is used verbatim."""
        if not original:
            return
        if original.lower() in self._passthrough:
            return
        self._mapping[original] = replacement
        self._rules.append(
            _Rule(
                original=original,
                replacement=replacement,
                pattern=re.compile(rf"\b{re.escape(original)}\b", re.IGNORECASE),
            )
        )

    def learn_names(
        self,
        real_first_names: Iterable[str] = (),
        real_place_names: Iterable[str] = (),
    ) -> None:
        """Bulk-add rewrites for a list of real names.

        Cycles through the configured first/place pools, so two
        ``learn_names`` calls with the same inputs produce the same
        mapping (deterministic).
        """
        for index, name in enumerate(sorted(set(real_first_names))):
            if not name or name in self._mapping:
                continue
            replacement = self._first_pool[index % len(self._first_pool)]
            self.add(name, replacement)
        for index, name in enumerate(sorted(set(real_place_names))):
            if not name or name in self._mapping:
                continue
            replacement = self._place_pool[index % len(self._place_pool)]
            self.add(name, replacement)

    def anonymize_mapping(self) -> dict[str, str]:
        """Return a copy of the persisted mapping (stable across runs)."""
        return dict(self._mapping)

    def with_passthrough(self, names: set[str]) -> Anonymizer:
        """Return a clone that won't rewrite ``names`` (case-insensitive).

        The clone keeps every rule whose ``original`` isn't in the
        passthrough set; this is useful when a single test needs a
        specific real name to survive.
        """
        clone = Anonymizer(
            first_names=self._first_pool,
            place_names=self._place_pool,
            passthrough=set(self._passthrough) | {n.lower() for n in names},
        )
        for original, replacement in self._mapping.items():
            if original.lower() in clone._passthrough:
                continue
            clone.add(original, replacement)
        return clone

    # ------------------------------------------------------------------ #
    # Text rewriting
    # ------------------------------------------------------------------ #

    def anonymize_text(self, text: str | None) -> str | None:
        """Rewrite a single string. ``None`` passes through unchanged."""
        if not text:
            return text
        result = text
        for rule in self._rules:
            result = rule.pattern.sub(rule.replacement, result)
        return result

    # ------------------------------------------------------------------ #
    # SQLite rewriting
    # ------------------------------------------------------------------ #

    def anonymize_sqlite(self, path: Path) -> dict[str, str]:
        """Rewrite prose columns in a SQLite file in-place.

        Returns the persisted mapping (the same one
        :meth:`anonymize_mapping` reports). Missing tables/columns are
        skipped so old snapshots that pre-date a migration still work.
        """
        path = Path(path)
        conn = sqlite3.connect(path)
        try:
            for table, pk, column in SQLITE_TARGETS:
                if not _table_has_column(conn, table, column):
                    continue
                rows = list(conn.execute(f"SELECT {pk}, {column} FROM {table}"))
                for row_id, original in rows:
                    if original is None:
                        continue
                    rewritten = self.anonymize_text(original)
                    if rewritten != original:
                        conn.execute(
                            f"UPDATE {table} SET {column} = ? WHERE {pk} = ?",
                            (rewritten, row_id),
                        )
            # Also rewrite character / location names captured in the
            # generic content index (kind = 'character' | 'location').
            if _table_has_column(conn, "campaign_content_index", "frontmatter"):
                rows = list(
                    conn.execute("SELECT id, frontmatter, body FROM campaign_content_index")
                )
                for row_id, frontmatter, body in rows:
                    new_fm = self.anonymize_text(frontmatter)
                    new_body = self.anonymize_text(body)
                    if new_fm != frontmatter or new_body != body:
                        conn.execute(
                            "UPDATE campaign_content_index SET frontmatter = ?, body = ?"
                            " WHERE id = ?",
                            (new_fm, new_body, row_id),
                        )
            conn.commit()
        finally:
            conn.close()
        return self.anonymize_mapping()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save_mapping(self, path: Path) -> None:
        """Persist the mapping to a sidecar file (`<snapshot>.anonymizer.json`)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self._mapping, f, ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def load_mapping(cls, path: Path) -> Anonymizer:
        """Load a previously persisted mapping."""
        with Path(path).open("r", encoding="utf-8") as f:
            mapping = json.load(f)
        return cls(mapping=mapping)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cursor.fetchall())
    except sqlite3.DatabaseError:
        return False


def sidecar_path(snapshot_path: Path) -> Path:
    """The standard sidecar file location for a snapshot's mapping."""
    return Path(str(snapshot_path) + ".anonymizer.json")


__all__: list[str] = [
    "DEFAULT_FIRST_NAMES",
    "DEFAULT_PLACE_NAMES",
    "SQLITE_TARGETS",
    "Anonymizer",
    "sidecar_path",
]


# Re-export for type checking that ``Any`` is used through the API.
_ = Any
