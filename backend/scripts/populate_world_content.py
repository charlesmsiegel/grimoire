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

from grimoire.store import cards, characters, entities, greetings, tags, worlds
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
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


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _stored_body_forms(baked: str) -> list[str]:
    r"""Every body string the store can legitimately hand back for `baked`.

    A greeting body does not survive its own write/read round-trip
    byte-for-byte, so comparing a freshly-baked body against a stored one
    directly reports drift on a store that never drifted. The lossy step is
    newline translation: `atomic.write_text` writes in text mode with
    `newline=None` (deliberately — forcing `newline=""` would rewrite a user's
    whole CRLF store to LF on its next save), which expands every "\n" to the
    platform separator, while `read_greeting` reads with universal newlines,
    which collapses "\r\n" and "\r" back to "\n". That pair is not an identity:
    a card's "\r\n" comes back as "\n\n" on a CRLF platform and as "\n" on an
    LF one. Cards themselves are JSON and *do* round-trip "\r\n" exactly, so
    only the greeting side loses anything — which is why CRLF cards
    (SillyTavern/Chub exports routinely have them) are the case this exists
    for; without it every rerun against such a card hard-errors.

    Modelled by running the real `dump_frontmatter`/`parse_frontmatter` pair
    rather than by guessing which characters move, so the fence handling (the
    body sits after a blank line the parser strips back off) stays correct too.
    Both platform separators are modelled, not just this machine's: the store
    may live in a synced folder, written by one machine and reread by another.
    """
    forms: list[str] = []
    for linesep in ("\n", "\r\n"):
        written = dump_frontmatter({"name": "x"}, baked).replace("\n", linesep)
        _, body = parse_frontmatter(_normalize_newlines(written))
        if body not in forms:
            forms.append(body)
    return forms


def _existing_greetings_in_creation_order(root: Path, character: str, version: str) -> list[str] | None:
    """Greetings for one character/version, in the order import_from_character
    would create them from the CURRENT card — recovered via a value-based 1:1
    match between each existing greeting's stored body and the body the card
    would produce at each position, never by sequential/positional consumption
    (which silently mis-assigns when the card's greeting list has been edited
    between runs — e.g. a new alternate inserted in the middle). Returns:
    - [] if nothing exists yet for this character/version (fresh import case)
    - the ordered id list if every existing greeting matches exactly one
      expected body and every expected body is matched (clean case)
    - None if existing greetings and the current card's expected bodies don't
      form a clean bijection (ambiguous — caller must treat this as an error,
      never guess by shifting or re-importing)"""
    try:
        card = characters.read_card(root, character, version)
    except (characters.CharacterNotFound, characters.VersionNotFound):
        return []
    data = card.get("data", {})
    baked_name = greetings.char_name(root, character, version)

    raw_items = []
    first = data.get("first_mes", "")
    if isinstance(first, str) and first.strip():
        raw_items.append(first)
    for alt in data.get("alternate_greetings", []) or []:
        if isinstance(alt, str) and alt.strip():
            raw_items.append(alt)
    expected_bodies = [cards.bake_char_token(raw, baked_name) for raw in raw_items]

    # Sorted by id, which is immutable — this script renames greetings when it
    # applies titles, and `list_greetings` sorts by that mutable `name`, so its
    # own order differs between the run that imported and every run after.
    # Body matching pins every position whose body is unique regardless of
    # order, but two positions that bake to the *same* body can only be told
    # apart by the tiebreak below, and a name-ordered tiebreak would hand them
    # out differently on a rerun — silently swapping which `new:*:idx` ref each
    # twin answers to.
    candidates = sorted(g["id"] for g in greetings.list_greetings(root)
                        if g["character"] == character and g["version"] == version)
    if not candidates:
        return []

    # Value-based 1:1 assignment: map every form each expected body can read
    # back as to the (possibly several, if duplicated) indices it occupies,
    # then consume one index per matching candidate. This catches "count still
    # matches but a candidate landed at the wrong slot" because it checks
    # per-value membership, not sequential position.
    expected_index_map: dict[str, list[int]] = {}
    for idx, body in enumerate(expected_bodies):
        for form in _stored_body_forms(body):
            expected_index_map.setdefault(form, []).append(idx)

    ordered: list[str | None] = [None] * len(expected_bodies)
    taken: set[int] = set()
    for gid in candidates:
        body = _normalize_newlines(greetings.read_greeting(root, gid)["body"])
        # lowest unused index for this body: with `candidates` sorted by id,
        # duplicate-body twins get a deterministic, rerun-stable assignment.
        idx = next((i for i in expected_index_map.get(body, ()) if i not in taken), None)
        if idx is None:
            return None  # a stored greeting matches nothing the card produces now
        taken.add(idx)
        ordered[idx] = gid

    if any(slot is None for slot in ordered):
        return None  # some expected body has no matching stored greeting

    return ordered  # every element is a str at this point, not None


def apply_greeting_imports(root: Path, specs: list[dict], results: dict) -> dict[str, str]:
    world_rel = _world_rel(root)
    imported_this_call: set[tuple[str, str]] = set()
    ref_map: dict[str, str] = {}

    for spec in specs:
        char_id, version = spec["character"], spec["version"]
        already_existing = _existing_greetings_in_creation_order(root, char_id, version)
        if already_existing is None:
            results["errors"].append({
                "stage": "greeting_imports",
                "reason": ("existing greetings for this character/version don't match "
                           "the current card content — cannot safely resolve which is which"),
                "character": char_id, "version": version})
            continue

        already = bool(already_existing) or (char_id, version) in imported_this_call

        if already:
            results["skipped"].append({"stage": "greeting_imports", "reason": "already imported",
                                        "character": char_id, "version": version})
            for idx, gid in enumerate(already_existing):
                ref_map[f"new:{char_id}:{version}:{idx}"] = gid
            continue

        try:
            new_ids = greetings.import_from_character(root, char_id, version)
        except Exception as exc:  # noqa: BLE001 — bad character/version in one spec must not abort the run
            results["errors"].append({"stage": "greeting_imports", "reason": str(exc),
                                       "character": char_id, "version": version})
            continue

        imported_this_call.add((char_id, version))
        # Index the greetings we just created through the SAME resolver a rerun
        # will use, rather than trusting import order, so this call and every
        # later one agree by construction. They genuinely can differ: when two
        # positions bake to the same body, only the resolver's id tiebreak can
        # separate them, and `import_from_character`'s creation order is not
        # id-sorted (`… (alt 10)` slugifies to an id that sorts before
        # `… (alt 2)`). Trusting creation order here would give one twin the
        # title and the rerun's `new:*:idx` ref to the other.
        ordered = _existing_greetings_in_creation_order(root, char_id, version)
        if ordered is None or len(ordered) != len(new_ids):
            results["errors"].append({
                "stage": "greeting_imports",
                "reason": ("imported greetings could not be resolved back to the card's "
                           "expected order — left in place, unreferenced, for inspection"),
                "character": char_id, "version": version})
            continue

        titles = spec.get("titles") or []
        for idx, gid in enumerate(ordered):
            if idx < len(titles):
                greetings.update_greeting(root, gid, name=titles[idx])
            ref_map[f"new:{char_id}:{version}:{idx}"] = gid
            results["touched_files"].add(f"{world_rel}/greetings/{gid}.md")
        results["created"].append({"stage": "greeting_imports", "character": char_id,
                                    "version": version, "count": len(ordered)})
    return ref_map


def resolve_ref(ref: str, ref_map: dict[str, str], root: Path) -> str | None:
    if ref in ref_map:
        return ref_map[ref]
    elif ref.startswith("id:"):
        gid = ref[len("id:"):]
        try:
            greetings.read_greeting(root, gid)
        except greetings.GreetingNotFound:
            return None
        return gid
    else:
        return None


def _resolve_refs(refs: list[str], ref_map: dict[str, str], root: Path, results: dict, stage: str) -> list[str]:
    out = []
    for ref in refs:
        gid = resolve_ref(ref, ref_map, root)
        if gid is None:
            results["errors"].append({"stage": stage, "reason": "unresolvable ref", "ref": ref})
            continue
        out.append(gid)
    return out


def _reaches(plotmap: dict, src: str, target: str, seen: set[str]) -> bool:
    if src == target:
        return True
    if src in seen:
        return False
    seen.add(src)
    return any(_reaches(plotmap, nxt, target, seen) for nxt in plotmap.get(src, {}).get("leads_to", []))


def apply_greeting_edges(root: Path, specs: list[dict], ref_map: dict[str, str], results: dict) -> None:
    world_rel = _world_rel(root)
    plotmap = greetings.read_plotmap(root)
    for spec in specs:
        gid = resolve_ref(spec["greeting_ref"], ref_map, root)
        if gid is None:
            results["errors"].append({"stage": "greeting_edges", "reason": "unresolvable ref", "ref": spec["greeting_ref"]})
            continue
        cur = greetings.edges_of(plotmap, gid)
        new_leads_to = _resolve_refs(spec.get("leads_to", []), ref_map, root, results, "greeting_edges.leads_to")
        new_excludes = _resolve_refs(spec.get("excludes", []), ref_map, root, results, "greeting_edges.excludes")

        accepted = list(cur["leads_to"])
        for target in new_leads_to:
            if target in accepted:
                continue
            if _reaches(plotmap, target, gid, set()):
                results["skipped"].append({"stage": "greeting_edges", "reason": "would create a cycle",
                                            "gid": gid, "target": target})
                continue
            accepted.append(target)

        excludes = list(cur["excludes"])
        for target in new_excludes:
            if target not in excludes:
                excludes.append(target)

        if accepted != cur["leads_to"] or excludes != cur["excludes"]:
            greetings.set_edges(root, gid, leads_to=accepted, excludes=excludes)
            plotmap[gid] = {"leads_to": accepted, "excludes": excludes}
            results["touched_files"].add(f"{world_rel}/plotmap.json")


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
