"""Apply a merged world-content manifest (locations/items/groups/lore/creatures
entities, lore reclassifications, tag vocabulary, greeting imports + plot-map
chaining) to a real grimoire world, idempotently and with a git checkpoint per
world. Built for the world-content-population swarm — see
docs/superpowers/specs/2026-08-08-world-content-population-swarm-design.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import entities, greetings, tags, worlds
from grimoire.store.paths import home

# creatures entities are only meaningful in fantasy worlds; enforced here (not
# just prompted) so one merge-agent mistake can't write them anywhere else.
CREATURE_ALLOWED_WORLDS = {"arcane-academy", "realm", "guildhall"}


def build_index(root: Path) -> dict:
    """Compact existing-content summary for the merge stage: id/name only, no
    body excerpts, so it stays cheap for worlds with 1000+ lore entries."""
    entity_rows = []
    for kind in entities.ENTITY_KINDS:
        for e in entities.list_entities(root, kind):
            entity_rows.append({"kind": kind, "id": e["id"], "name": e["name"]})
    tag_rows = [{"id": tid, "display_name": name} for tid, name in tags.read_tags(root).items()]
    greeting_rows = [
        {"id": g["id"], "name": g["name"], "character": g["character"], "version": g["version"]}
        for g in greetings.list_greetings(root)
    ]
    return {"entities": entity_rows, "tags": tag_rows, "greetings": greeting_rows}


def new_results() -> dict:
    return {"created": [], "skipped": [], "errors": [], "touched_files": set()}


def _world_rel(root: Path) -> str:
    return root.relative_to(home()).as_posix()


def apply_tags(root: Path, tag_specs: list[dict], results: dict) -> dict[str, str]:
    world_rel = _world_rel(root)
    existing = tags.read_tags(root)
    by_lower = {name.lower(): tid for tid, name in existing.items()}
    result: dict[str, str] = {}
    for spec in tag_specs:
        name = spec["display_name"]
        key = name.lower()
        if key in by_lower:
            result[name] = by_lower[key]
        else:
            tid = tags.add_tag(root, name)
            by_lower[key] = tid
            result[name] = tid
            results["touched_files"].add(f"{world_rel}/tags.md")
    return result


def _existing_names_by_kind(root: Path) -> dict[str, set[str]]:
    return {kind: {e["name"].lower() for e in entities.list_entities(root, kind)}
            for kind in entities.ENTITY_KINDS}


def apply_entities(root: Path, specs: list[dict], results: dict, wid: str) -> None:
    world_rel = _world_rel(root)
    existing_by_kind = _existing_names_by_kind(root)

    for spec in specs:
        kind = spec["kind"]
        if kind not in entities.ENTITY_KINDS:
            results["errors"].append(
                {"stage": "entities", "reason": "unknown kind", "spec": spec})
            continue
        if kind == "creatures" and wid not in CREATURE_ALLOWED_WORLDS:
            results["errors"].append({
                "stage": "entities",
                "reason": "creatures not allowed outside fantasy worlds",
                "world": wid, "name": spec["name"]})
            continue
        name_lower = spec["name"].lower()
        if name_lower in existing_by_kind[kind]:
            results["skipped"].append({
                "stage": "entities", "reason": "already exists",
                "kind": kind, "name": spec["name"]})
            continue
        eid = entities.create_entity(
            root, kind, spec["name"], body=spec.get("body", ""),
            keys=spec.get("keys", ""), owners=spec.get("owners", ""),
            fields=spec.get("fields") or None)
        existing_by_kind[kind].add(name_lower)
        results["created"].append({
            "stage": "entities", "kind": kind, "id": eid,
            "name": spec["name"], "source": spec.get("source", "")})
        results["touched_files"].add(f"{world_rel}/{kind}/{eid}.md")


def apply_reclassifications(root: Path, specs: list[dict], results: dict,
                            wid: str) -> None:
    world_rel = _world_rel(root)
    existing_by_kind = _existing_names_by_kind(root)

    for spec in specs:
        kind = spec["new_kind"]
        if kind not in entities.ENTITY_KINDS:
            results["errors"].append({
                "stage": "reclassifications", "reason": "unknown kind",
                "spec": spec})
            continue
        if kind == "creatures" and wid not in CREATURE_ALLOWED_WORLDS:
            results["errors"].append({
                "stage": "reclassifications",
                "reason": "creatures not allowed outside fantasy worlds",
                "world": wid, "name": spec["name"]})
            continue

        name_lower = spec["name"].lower()
        if name_lower in existing_by_kind[kind]:
            results["skipped"].append({
                "stage": "reclassifications",
                "reason": "target already exists", "kind": kind,
                "name": spec["name"]})
        else:
            eid = entities.create_entity(
                root, kind, spec["name"], body=spec.get("body", ""),
                keys=spec.get("keys", ""), owners=spec.get("owners", ""),
                fields=spec.get("fields") or None)
            existing_by_kind[kind].add(name_lower)
            results["created"].append({
                "stage": "reclassifications", "kind": kind, "id": eid,
                "name": spec["name"], "source": spec.get("source", "")})
            results["touched_files"].add(f"{world_rel}/{kind}/{eid}.md")

        # Idempotent by construction: EntityNotFound just means prior run
        # already deleted it.
        try:
            entities.delete_entity(root, "lore", spec["lore_id"])
            results["touched_files"].add(
                f"{world_rel}/lore/{spec['lore_id']}.md")
        except entities.EntityNotFound:
            pass


def cmd_index(args: argparse.Namespace) -> int:
    root = worlds.world_root(args.world)
    print(json.dumps(build_index(root), indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Apply a world-content manifest to a real grimoire world.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="print the existing-content index for a world")
    p_index.add_argument("--world", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "index":
        return cmd_index(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
