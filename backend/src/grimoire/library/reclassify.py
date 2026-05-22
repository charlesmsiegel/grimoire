"""Pure-function mapping `LoreEntry` -> target-kind frontmatter+body (spec §2).

This module is the shared transform: the standalone
`LibraryService.reclassify_entity` flow calls `apply_mapping` after reading
the source, and the (future) card-imports E2 path will call it before
writing the lore entry to disk. Keeping the mapping table here (not on the
service) is what makes the import-time path possible without
round-tripping a file.

Also owns the audit log helpers (`append_audit`, `iter_audit`) so the
audit log shape is co-located with the conversion logic that produces it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.types.common import EntityKind
from grimoire.types.world import LoreEntry, LorePosition, SelectiveLogic

if TYPE_CHECKING:
    from grimoire.types.characters import IngestedLoreEntry

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReclassificationResult:
    source_id: str
    target_id: str
    target_kind: EntityKind
    fields_kept: list[str]
    fields_dropped: list[str]
    fields_into_notes: list[str]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Mapping table (spec §2)
# --------------------------------------------------------------------------- #

# Per-target-kind direct field map: lore_field -> target_frontmatter_key.
_DIRECT_MAP: dict[EntityKind, dict[str, str]] = {
    EntityKind.CHARACTER: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "related_factions": "factions",
        "secrecy": "secrecy",
    },
    EntityKind.LOCATION: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "secrecy": "secrecy",
    },
    EntityKind.FACTION: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "related_factions": "allies",
        "secrecy": "secrecy",
    },
    EntityKind.ITEM: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
    },
    EntityKind.MONSTER: {
        "title": "name",
        "keywords": "aliases",
        "tags": "tags",
        "related_locations": "habitat",
        "secrecy": "secrecy",
    },
}

# Fields that go into the body's ``## Notes`` section instead of frontmatter
# when converting to a given target kind. Order matters: rendered in this
# order.
#
# ``related_locations`` and ``related_characters`` are always routed to notes
# regardless of target kind: none of Character/Location/Faction/Item has a
# matching ``list[str]`` schema field, so the only safe thing is to preserve
# them as prose the user can reconcile later. Dropping silently would lose
# data the user spent time entering.
_INTO_NOTES: dict[EntityKind, tuple[str, ...]] = {
    EntityKind.CHARACTER: (
        "secondary_keys",
        "comment",
        "related_locations",
        "related_characters",
    ),
    EntityKind.LOCATION: (
        "secondary_keys",
        "comment",
        "related_factions",
        "related_locations",
        "related_characters",
    ),
    EntityKind.FACTION: (
        "secondary_keys",
        "comment",
        "related_locations",
        "related_characters",
    ),
    EntityKind.ITEM: (
        "secondary_keys",
        "comment",
        "related_factions",
        "secrecy",
        "related_locations",
        "related_characters",
    ),
    EntityKind.MONSTER: (
        "secondary_keys",
        "comment",
        "related_factions",
        "related_characters",
    ),
}

# Fields that are silently dropped (matching/scoring metadata only meaningful
# for LoreEntry's keyword-match algorithm). Always rolled up into a single
# warning per spec §2.
#
# NOTE: As of 2026-05-19 the LoreEntry model in types/world.py does not yet
# carry these fields — they are added by card-imports §3. The dropped-field
# logic is forward-compatible (``getattr(source, field, None)`` returns
# None today) and will start firing once card-imports lands and extends
# the model. Do not delete this list; it is the v2 surface area.
_DROPPED_MATCHING_FIELDS: frozenset[str] = frozenset(
    {
        "priority",
        "probability",
        "position",
        "at_depth",
        "scan_depth",
        "constant",
        "enabled",
        "case_sensitive",
        "match_whole_words",
        "selective_logic",
    }
)

# Per-field default sentinels: a value equal to its default should not be
# flagged as dropped because the user never set it. Defaults come from the
# card-imports-extended LoreEntry model (spec §3).
_DEFAULT_VALUES: dict[str, Any] = {
    "priority": 100,
    "probability": 100,
    "position": LorePosition.AFTER_CAST,
    "at_depth": None,
    "scan_depth": None,
    "constant": False,
    "enabled": True,
    "case_sensitive": False,
    "match_whole_words": False,
    "selective_logic": SelectiveLogic.AND_ANY,
}

# Required overrides per target kind (target schema fields lore can't supply).
_REQUIRED_OVERRIDES: dict[EntityKind, tuple[str, ...]] = {
    EntityKind.CHARACTER: (),
    EntityKind.LOCATION: ("kind",),
    EntityKind.FACTION: (),
    EntityKind.ITEM: (),
    EntityKind.MONSTER: (),
}

_VALID_TARGET_KINDS: frozenset[EntityKind] = frozenset(
    {
        EntityKind.CHARACTER,
        EntityKind.LOCATION,
        EntityKind.FACTION,
        EntityKind.ITEM,
        EntityKind.MONSTER,
    }
)


def required_overrides_for(target_kind: EntityKind) -> list[str]:
    """Return the override keys the UI must collect before allowing commit."""
    return list(_REQUIRED_OVERRIDES.get(target_kind, ()))


def apply_mapping(
    source: LoreEntry,
    target_kind: EntityKind,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, list[str], list[str], list[str], list[str]]:
    """Map `source` to `(frontmatter, body, kept, dropped, into_notes, warnings)`.

    ``overrides`` patches the resulting frontmatter after the mapping runs,
    so a UI-collected ``name`` edit or required ``Location.kind`` value
    replaces the derived/default value. ``frontmatter['id']`` is **not**
    set here; callers derive the target id (collision-resolved) and set it
    themselves.
    """
    if target_kind not in _VALID_TARGET_KINDS:
        raise ValueError(
            f"reclassify target_kind must be one of "
            f"{sorted(k.value for k in _VALID_TARGET_KINDS)!r}, got {target_kind!r}"
        )

    fm: dict[str, Any] = {}
    kept: list[str] = []
    dropped: list[str] = []
    into_notes_keys: list[str] = []
    warnings: list[str] = []
    notes_payload: list[tuple[str, Any]] = []

    direct = _DIRECT_MAP[target_kind]
    into_notes_fields = _INTO_NOTES[target_kind]

    for src_field, target_key in direct.items():
        value = getattr(source, src_field, None)
        if value in (None, "", [], {}):
            continue
        fm[target_key] = value
        kept.append(target_key)

    for src_field in into_notes_fields:
        value = getattr(source, src_field, None)
        if value in (None, "", [], {}):
            continue
        into_notes_keys.append(src_field)
        notes_payload.append((src_field, value))

    import_source = getattr(source, "import_source", None)
    if import_source is not None:
        fm["import_source"] = (
            import_source.model_dump() if hasattr(import_source, "model_dump") else import_source
        )
        kept.append("import_source")

    dropped_any = False
    for src_field in _DROPPED_MATCHING_FIELDS:
        value = getattr(source, src_field, None)
        default = _DEFAULT_VALUES.get(src_field)
        if value is None or value == default:
            continue
        dropped.append(src_field)
        dropped_any = True
    if dropped_any:
        warnings.append("matching metadata discarded (lore-only fields)")

    body = source.body or ""
    if notes_payload:
        lines = ["", "## Notes", ""]
        for name, value in notes_payload:
            rendered = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            lines.append(f"- **{name}**: {rendered}")
        if body:
            body = body.rstrip() + "\n" + "\n".join(lines) + "\n"
        else:
            body = "\n".join(lines[1:]) + "\n"

    if overrides:
        for key, value in overrides.items():
            fm[key] = value
            if key not in kept:
                kept.append(key)

    return fm, body, kept, dropped, into_notes_keys, warnings


def _lore_entry_from_ingested(
    entry: IngestedLoreEntry,
    *,
    world_id: str,
) -> LoreEntry:
    """Adapt an ``IngestedLoreEntry`` (preview-side) into a ``LoreEntry``.

    The classifier (``suggest_kind``) and the mapping (``apply_mapping``)
    both want a real ``LoreEntry``; the importer carries the raw card-side
    ``IngestedLoreEntry`` instead. This thin adapter bridges them. Only
    the fields the classifier + mapping read are copied; the rest stay at
    LoreEntry defaults.

    ``id`` is filled with a stable placeholder derived from ``source_index``
    — callers that persist the result must re-derive a real id (the
    ``_write_lore_entries`` path goes through ``_slug_for_lore_entry`` +
    ``_unique_id`` to do exactly that).
    """
    title = entry.name or (entry.keys[0] if entry.keys else f"entry-{entry.source_index}")
    return LoreEntry(
        world_id=world_id,
        id=f"ingested-{entry.source_index}",
        title=title,
        body=entry.body,
        keywords=list(entry.keys),
        secondary_keys=list(entry.secondary_keys),
        selective_logic=SelectiveLogic(entry.selective_logic),
        constant=entry.constant,
        enabled=entry.enabled,
        case_sensitive=entry.case_sensitive,
        match_whole_words=entry.match_whole_words,
        priority=entry.priority,
        probability=entry.probability,
        position=LorePosition(entry.position),
        at_depth=entry.at_depth,
        scan_depth=entry.scan_depth,
        comment=entry.comment,
    )


# --------------------------------------------------------------------------- #
# Audit log (spec §6)
# --------------------------------------------------------------------------- #


def audit_log_path(data_root: Path) -> Path:
    """Return the shared reclassifications audit log path under ``data_root``."""
    return data_root / "library" / "imports" / "reclassifications.jsonl"


def append_audit(
    data_root: Path,
    *,
    world_id: str,
    source_id: str,
    source_snapshot: dict[str, Any],
    target_id: str,
    target_kind: EntityKind,
    overrides: dict[str, Any],
    actor: str,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """Append one JSONL record to the audit log; return the record."""
    record = {
        "ts": (ts or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "world_id": world_id,
        "source_id": source_id,
        "source_snapshot": source_snapshot,
        "target_id": target_id,
        "target_kind": target_kind.value,
        "overrides": overrides or {},
        "actor": actor,
    }
    path = audit_log_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def iter_audit(data_root: Path, *, world_id: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield audit records in append order, optionally filtered by world_id."""
    path = audit_log_path(data_root)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if world_id is not None and record.get("world_id") != world_id:
                continue
            yield record


__all__ = [
    "ReclassificationResult",
    "append_audit",
    "apply_mapping",
    "audit_log_path",
    "iter_audit",
    "required_overrides_for",
]
