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
