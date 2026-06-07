#!/usr/bin/env python
"""Validate a Grimoire world directory against the real backend models.

Round-trips world.yaml and every entity markdown file through its Pydantic
model, then runs a cross-entity ref-integrity pass. Run from backend/ so the
`grimoire` package imports under the uv env:

    uv run python ../.claude/skills/create-world/scripts/validate_world.py <id-or-path>

Pass a world id (resolved under $GRIMOIRE_DATA_ROOT or ~/.grimoire) or a path to
a world directory. Exit code is non-zero when any ERROR is found; warnings never
fail the run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ValidationError

from grimoire.files import FrontmatterError, load_yaml, read_markdown
from grimoire.types.characters import Character
from grimoire.types.composition import Greeting, WorldMeta
from grimoire.types.world import Faction, Item, Location, LoreEntry, Monster

# entity subdir -> model
KINDS: dict[str, type[BaseModel]] = {
    "characters": Character,
    "locations": Location,
    "items": Item,
    "factions": Faction,
    "monsters": Monster,
    "lore": LoreEntry,
    "greetings": Greeting,
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Loaded:
    meta: WorldMeta | None = None
    # kind -> set of ids
    ids: dict[str, set[str]] = field(default_factory=dict)
    # parsed model instances, keyed (kind, id)
    entities: dict[tuple[str, str], BaseModel] = field(default_factory=dict)


def resolve_data_root() -> Path:
    env = os.environ.get("GRIMOIRE_DATA_ROOT")
    return Path(env) if env else Path.home() / ".grimoire"


def resolve_world_dir(arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_dir():
        return candidate
    return resolve_data_root() / "library" / "worlds" / arg


def _allows_none(annotation: object) -> bool:
    """True if the field annotation permits ``None`` (e.g. ``str | None``)."""
    return type(None) in get_args(annotation)


# Synthetic provenance used when wrapping bare-scalar extras for validation.
_EXTRA_PROVENANCE = {"set_at": "1970-01-01T00:00:00Z", "set_by": "skill-validate"}


def _normalize_extras(data: dict) -> None:
    """Wrap bare-scalar ``extras`` values into ExtraValue shape in place.

    Mirrors the app's ``_decode_extras`` leniency so hand-authored
    ``extras: {favorite_drink: rum}`` round-trips through the strict
    ``dict[str, ExtraValue]`` model field. Entries already in full ExtraValue
    form (``{value, set_at, ...}``) are left untouched; key/value rules are
    still enforced by the model's own validators.
    """
    raw = data.get("extras")
    if not isinstance(raw, dict):
        return
    wrapped: dict = {}
    for key, entry in raw.items():
        if isinstance(entry, dict) and "value" in entry and "set_at" in entry:
            wrapped[key] = entry
        else:
            wrapped[key] = {"value": entry, **_EXTRA_PROVENANCE}
    data["extras"] = wrapped


def _rel(path: Path, world: Path) -> str:
    try:
        return str(path.relative_to(world))
    except ValueError:
        return str(path)


def _load_entities(world: Path, report: Report) -> Loaded:
    loaded = Loaded(ids={k: set() for k in KINDS})

    meta_path = world / "world.yaml"
    if not meta_path.is_file():
        report.errors.append("world.yaml: missing")
    else:
        try:
            raw = load_yaml(meta_path) or {}
            loaded.meta = WorldMeta.model_validate(raw)
        except Exception as exc:  # report any load/validate failure
            report.errors.append(f"world.yaml: {exc}")

    world_id = loaded.meta.id if loaded.meta else world.name

    for kind, model in KINDS.items():
        kind_dir = world / kind
        if not kind_dir.is_dir():
            continue
        for md in sorted(kind_dir.glob("*.md")):
            rel = _rel(md, world)
            try:
                doc = read_markdown(md)
            except FrontmatterError as exc:
                report.errors.append(f"{rel}: {exc}")
                continue
            data = dict(doc.frontmatter)
            # Inject derived/contextual fields the file omits.
            if "world_id" in model.model_fields:
                data.setdefault("world_id", world_id)
            if "body" in model.model_fields and not data.get("body"):
                data["body"] = doc.body
            # Unknown-key check (top level only; models ignore extras silently).
            unknown = set(data) - set(model.model_fields)
            for key in sorted(unknown):
                report.warnings.append(f"{rel}: unknown field {key!r} (ignored on load)")
            # Mirror the app's loaders, which fill absent optional-but-required
            # fields with None (e.g. a greeting without starting_time) rather
            # than rejecting them. Genuinely required non-nullable fields (id,
            # name, role) stay required and still error when missing.
            for fname, finfo in model.model_fields.items():
                if fname not in data and finfo.is_required() and _allows_none(finfo.annotation):
                    data[fname] = None
            if "extras" in model.model_fields:
                _normalize_extras(data)
            try:
                inst = model.model_validate(data)
            except ValidationError as exc:
                report.errors.append(f"{rel}: {exc}")
                continue
            ent_id = data.get("id")
            if not ent_id:
                report.errors.append(f"{rel}: missing 'id'")
                continue
            if ent_id in loaded.ids[kind]:
                report.errors.append(f"{rel}: duplicate id {ent_id!r} in {kind}")
            loaded.ids[kind].add(ent_id)
            loaded.entities[(kind, ent_id)] = inst
    return loaded


def _err(report: Report, where: str, ref: str, kind: str) -> None:
    report.errors.append(f"{where}: missing {kind} ref {ref!r}")


def _warn(report: Report, where: str, ref: str, kind: str) -> None:
    report.warnings.append(f"{where}: unresolved {kind} ref {ref!r}")


def _check_refs(loaded: Loaded, report: Report) -> None:
    chars = loaded.ids["characters"]
    locs = loaded.ids["locations"]
    facs = loaded.ids["factions"]

    # ERROR-level: things that break a campaign start.
    if loaded.meta:
        start = (loaded.meta.defaults or {}).get("starting_location")
        if start and start not in locs:
            _err(report, "world.yaml defaults", start, "location")

    for (kind, eid), ent in loaded.entities.items():
        where = f"{kind}/{eid}.md"
        if kind == "greetings":
            g: Greeting = ent  # type: ignore[assignment]
            if g.starting_location and g.starting_location not in locs:
                _err(report, where, g.starting_location, "location")
            for c in g.present_characters:
                if c not in chars:
                    _err(report, where, c, "character")
            if g.pov_character and g.pov_character not in chars:
                _err(report, where, g.pov_character, "character")

        elif kind == "locations":
            loc: Location = ent  # type: ignore[assignment]
            if loc.parent_id and loc.parent_id not in locs:
                _warn(report, where, loc.parent_id, "location")
            for conn in loc.connections:
                if conn.to and conn.to not in locs:
                    _warn(report, where, conn.to, "location")
            for occ in loc.typical_occupants:
                if occ not in chars:
                    _warn(report, where, occ, "character")

        elif kind == "factions":
            fac: Faction = ent  # type: ignore[assignment]
            if fac.base_location and fac.base_location not in locs:
                _warn(report, where, fac.base_location, "location")
            for c in (*fac.leaders, *fac.members):
                if c not in chars:
                    _warn(report, where, c, "character")
            for f in (*fac.allies, *fac.rivals):
                if f not in facs:
                    _warn(report, where, f, "faction")

        elif kind == "items":
            it: Item = ent  # type: ignore[assignment]
            if it.current_holder and it.current_holder not in chars:
                _warn(report, where, it.current_holder, "character")

        elif kind == "lore":
            lore: LoreEntry = ent  # type: ignore[assignment]
            for r in lore.related_locations:
                if r not in locs:
                    _warn(report, where, r, "location")
            for r in lore.related_factions:
                if r not in facs:
                    _warn(report, where, r, "faction")
            for r in lore.related_characters:
                if r not in chars:
                    _warn(report, where, r, "character")

        elif kind == "characters":
            ch: Character = ent  # type: ignore[assignment]
            for rel in ch.structural_relationships:
                ref = rel.to_ref
                if "/" in ref:
                    continue  # path-form ref (e.g. worlds/<w>/factions/<id>)
                if ref not in chars and ref not in facs:
                    _warn(report, where, ref, "character/faction")


def validate_world(world_dir: str | Path) -> Report:
    world = Path(world_dir)
    report = Report()
    if not world.is_dir():
        report.errors.append(f"{world}: not a directory")
        return report
    loaded = _load_entities(world, report)
    _check_refs(loaded, report)
    return report


def _print(report: Report) -> None:
    for w in report.warnings:
        print(f"WARN  {w}")
    for e in report.errors:
        print(f"ERROR {e}")
    print(
        f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s) — "
        f"{'OK' if report.ok else 'FAILED'}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_world.py <world-id-or-path>", file=sys.stderr)
        return 2
    world = resolve_world_dir(argv[0])
    report = validate_world(world)
    print(f"validating {world}")
    _print(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
