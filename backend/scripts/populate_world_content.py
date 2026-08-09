"""Apply a merged world-content manifest (locations/items/groups/lore/creatures
entities, lore reclassifications, tag vocabulary, greeting imports + plot-map
chaining) to a real grimoire world, idempotently and with a git checkpoint per
world. Built for the world-content-population swarm — see
docs/superpowers/specs/2026-08-08-world-content-population-swarm-design.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import cards, characters, entities, greetings, overlay, tags, worlds
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
    # "swept" records every `overlay.forget_world_record` call this run made.
    # cmd_run needs it: a sweep is the only thing here allowed to write outside
    # the world being processed, so it is also the only explanation for a git
    # change outside `world_rel` (see cmd_run).
    return {"created": [], "skipped": [], "errors": [], "swept": [], "touched_files": set()}


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
        except entities.EntityNotFound:
            continue
        results["touched_files"].add(f"{world_rel}/lore/{spec['lore_id']}.md")

        # The sweep every world-side deleter in this codebase owes the overlay
        # (`overlay.forget_world_record`, and see its docstring): the id this
        # delete just freed is handed straight back to the next record of the
        # same name, so anything still filed under it -- the world's own asset
        # directory, and each dependent campaign's state for a record it only
        # ever inherited -- would be adopted by an unrelated record (#225).
        # Reclassification is exactly that shape: the lore slug goes away and
        # the world keeps being edited afterwards.
        #
        # Run AFTER the delete, like the routes do, so a crash between the two
        # leaves state attached to a record that still exists rather than a
        # record stripped of state it still owns.
        rec_dir = root / "lore" / spec["lore_id"]
        before = [p for p in rec_dir.rglob("*") if p.is_file()] if rec_dir.is_dir() else []
        overlay.forget_world_record(root, "lore", spec["lore_id"])
        # The world-side half of the sweep drops that record directory, so its
        # files are real changes under `world_rel`; unaccounted for, they fail
        # verify_manifest's git cross-check as "changes apply did not account
        # for". (The campaign-side half writes outside `world_rel` and is
        # handled in cmd_run, which commits those paths in the same commit.)
        for p in before:
            if not p.exists():
                results["touched_files"].add(p.relative_to(home()).as_posix())
        results["swept"].append({"stage": "reclassifications", "kind": "lore",
                                 "id": spec["lore_id"]})


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
    matched = [(gid, expected_index_map.get(
                    _normalize_newlines(greetings.read_greeting(root, gid)["body"]), ()))
               for gid in candidates]
    # Most-constrained-first, not plain greedy over `candidates`: a candidate
    # that fits only ONE position must claim it before a candidate that fits
    # several, or the loose one takes that slot and the tight one is left with
    # nothing -- reported as "these greetings don't match the card" for a store
    # where a perfectly good assignment existed. Not hypothetical: two card
    # positions whose text differs only in CRLF-vs-LF read back as one shared
    # body on one platform and two distinct ones on the other, which is exactly
    # a one-choice candidate and a two-choice candidate over the same pair of
    # slots (and `candidates` is id-sorted, which decides nothing about which
    # is tighter). Ties break by the immutable id, so duplicate-body twins --
    # genuinely interchangeable, since nothing on disk tells them apart -- get
    # the same assignment on every rerun, which is what keeps a `new:*:idx` ref
    # pointing at the same greeting run after run.
    #
    # Still a heuristic rather than a full bipartite matching: a contrived
    # overlap pattern could make it give up where an augmenting-path search
    # would succeed. That direction of wrongness is the safe one -- the caller
    # records an error and touches nothing, exactly as it does for real drift.
    matched.sort(key=lambda pair: (len(pair[1]), pair[0]))
    for gid, choices in matched:
        idx = next((i for i in choices if i not in taken), None)
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


def apply_greeting_gating(root: Path, specs: list[dict], ref_map: dict[str, str], results: dict) -> None:
    world_rel = _world_rel(root)
    tag_by_lower = {name.lower(): tid for tid, name in tags.read_tags(root).items()}

    for spec in specs:
        gid = resolve_ref(spec["greeting_ref"], ref_map, root)
        if gid is None:
            results["errors"].append({"stage": "greeting_gating", "reason": "unresolvable ref", "ref": spec["greeting_ref"]})
            continue
        cur = greetings.read_greeting(root, gid)["meta"]

        new_tag_ids = []
        for name in spec.get("requires_tags", []):
            tid = tag_by_lower.get(name.lower())
            if tid is None:
                results["errors"].append({"stage": "greeting_gating", "reason": "unknown tag display_name",
                                           "display_name": name, "gid": gid})
                continue
            new_tag_ids.append(tid)
        requires_tags = list(cur["requires_tags"])
        for tid in new_tag_ids:
            if tid not in requires_tags:
                requires_tags.append(tid)

        present = list(cur["present"])
        for cid in spec.get("present", []):
            if cid not in present:
                present.append(cid)

        if requires_tags != cur["requires_tags"] or present != cur["present"]:
            greetings.update_greeting(root, gid, requires_tags=requires_tags, present=present)
            results["touched_files"].add(f"{world_rel}/greetings/{gid}.md")


# ---- manifest shape validation ----
#
# A manifest is LLM-authored JSON, so a missing or mistyped key is expected
# INPUT, not a programmer error -- but every apply_* stage indexes its required
# keys directly (`spec["kind"]`, `spec["name"]`, ...). Without a check up front
# one bad item raised KeyError out of the middle of a stage: earlier items in
# the same list were already on disk, later ones never ran, and cmd_run never
# reached its `print`, so the whole run reported nothing at all on stdout.
#
# Validating the WHOLE manifest before any stage runs turns that into ordinary
# per-item errors: the malformed item is recorded in results["errors"] (which
# already gates the commit) and skipped, every well-formed item beside it still
# applies, and cmd_run still prints its JSON with committed=false.

_STR_LIST = "list[str]"

#: {stage: (required, optional)}, each a {key: kind} map over "str", "dict" and
#: `_STR_LIST`. Required strings must also be non-blank -- a record with a blank
#: name has no usable id. Mirrors the manifest contract in
#: docs/superpowers/plans/2026-08-08-world-content-population-swarm.md.
_ENTITY_OPTIONAL = {"body": "str", "keys": "str", "owners": "str",
                    "source": "str", "fields": "dict"}
_MANIFEST_SCHEMA: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "tags": ({"display_name": "str"}, {"source": "str"}),
    "entities": ({"kind": "str", "name": "str"}, _ENTITY_OPTIONAL),
    "reclassifications": ({"lore_id": "str", "new_kind": "str", "name": "str"}, _ENTITY_OPTIONAL),
    "greeting_imports": ({"character": "str", "version": "str"},
                         {"titles": _STR_LIST, "source": "str"}),
    "greeting_edges": ({"greeting_ref": "str"},
                       {"leads_to": _STR_LIST, "excludes": _STR_LIST, "source": "str"}),
    "greeting_gating": ({"greeting_ref": "str"},
                        {"requires_tags": _STR_LIST, "present": _STR_LIST, "source": "str"}),
}


def _value_problem(key: str, value, kind: str, *, required: bool) -> str | None:
    if kind == "str":
        if not isinstance(value, str):
            return f"{key!r} must be a string"
        if required and not value.strip():
            return f"{key!r} must not be blank"
    elif kind == "dict":
        if not isinstance(value, dict):
            return f"{key!r} must be an object"
    elif kind == _STR_LIST:
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            return f"{key!r} must be a list of strings"
    return None


def _item_problem(item, required: dict[str, str], optional: dict[str, str]) -> str | None:
    if not isinstance(item, dict):
        return "item is not an object"
    for key, kind in required.items():
        if key not in item:
            return f"missing required key {key!r}"
        problem = _value_problem(key, item[key], kind, required=True)
        if problem:
            return problem
    for key, kind in optional.items():
        # `None` reads as "not supplied": every stage reaches optional keys
        # through `.get(key, default)` / `or None`, so a null is already the
        # default rather than a value anything indexes.
        if item.get(key) is not None:
            problem = _value_problem(key, item[key], kind, required=False)
            if problem:
                return problem
    return None


def validate_manifest(manifest: dict, results: dict) -> dict:
    """The manifest with every malformed item dropped and one error recorded
    per drop. Same shape as the input, holding only items the apply_* stages
    can safely index."""
    clean: dict[str, list] = {}
    for stage, (required, optional) in _MANIFEST_SCHEMA.items():
        raw = manifest.get(stage) or []
        if not isinstance(raw, list):
            results["errors"].append({
                "stage": stage, "reason": "manifest section is not a list"})
            clean[stage] = []
            continue
        good = []
        for item in raw:
            problem = _item_problem(item, required, optional)
            if problem is None:
                good.append(item)
            else:
                results["errors"].append({
                    "stage": stage, "reason": f"malformed manifest item: {problem}",
                    "spec": item})
        clean[stage] = good
    return clean


def apply_manifest(root: Path, manifest: dict, wid: str) -> dict:
    results = new_results()
    manifest = validate_manifest(manifest, results)

    apply_tags(root, manifest["tags"], results)

    apply_entities(root, manifest["entities"], results, wid)
    apply_reclassifications(root, manifest["reclassifications"], results, wid)

    import_ref_map = apply_greeting_imports(root, manifest["greeting_imports"], results)
    ref_map = dict(import_ref_map)
    for g in greetings.list_greetings(root):
        ref_map.setdefault(f"id:{g['id']}", g["id"])

    apply_greeting_edges(root, manifest["greeting_edges"], ref_map, results)
    apply_greeting_gating(root, manifest["greeting_gating"], ref_map, results)

    results["touched_files"] = sorted(results["touched_files"])
    return results


def verify_manifest(root: Path, touched_files: list[str] | None = None,
                    git_changed: set[str] | None = None) -> dict:
    """Verify referential integrity in the store and optionally cross-check
    against git changes.

    Returns: {"ok": bool, "problems": [str, ...]}
    """
    problems: list[str] = []
    world_rel = _world_rel(root)

    char_ids = {c["id"] for c in characters.list_characters(root)}
    tag_ids = set(tags.read_tags(root))
    greeting_list = greetings.list_greetings(root)
    greeting_ids = {g["id"] for g in greeting_list}

    for g in greeting_list:
        if g["character"] and g["character"] not in char_ids:
            problems.append(f"greeting {g['id']}: character references unknown character {g['character']}")
        for tid in g["requires_tags"]:
            if tid not in tag_ids:
                problems.append(f"greeting {g['id']}: requires_tags references unknown tag {tid}")
        for cid in g["present"]:
            if cid not in char_ids:
                problems.append(f"greeting {g['id']}: present references unknown character {cid}")

    plotmap = greetings.read_plotmap(root)
    for gid, edges in plotmap.items():
        if gid not in greeting_ids:
            problems.append(f"plotmap: edge source {gid} is not a real greeting")
        for target in edges.get("leads_to", []) + edges.get("excludes", []):
            if target not in greeting_ids:
                problems.append(f"plotmap edge from {gid}: references unknown greeting {target}")

    # Pre-compute entity id sets by kind to avoid repeated file I/O in the owners loop
    entity_ids_by_kind: dict[str, set[str]] = {}
    for kind in entities.ENTITY_KINDS:
        entity_ids_by_kind[kind] = {e["id"] for e in entities.list_entities(root, kind)}

    for kind in entities.ENTITY_KINDS:
        for e in entities.list_entities(root, kind):
            owners = [o.strip() for o in e.get("owners", "").split(",") if o.strip()]
            for ref in owners:
                ref_kind, _, ref_id = ref.partition(":")
                ok = (ref_kind == "characters" and ref_id in char_ids) or \
                     (ref_kind in entities.ENTITY_KINDS and ref_id in entity_ids_by_kind.get(ref_kind, set()))
                if not ok:
                    problems.append(f"{kind}/{e['id']}: owners references unresolvable {ref}")

    if touched_files is not None and git_changed is not None:
        scoped_git_changed = {p for p in git_changed if p.startswith(world_rel + "/")}
        unexpected = scoped_git_changed - set(touched_files)
        if unexpected:
            problems.append(f"git shows changes apply did not account for: {sorted(unexpected)}")
        missing = set(touched_files) - scoped_git_changed
        if missing:
            problems.append(f"apply claimed to touch files git shows no change to: {sorted(missing)}")

    return {"ok": not problems, "problems": problems}


def cmd_index(args: argparse.Namespace) -> int:
    root = worlds.world_root(args.world)
    print(json.dumps(build_index(root), indent=2, sort_keys=True))
    return 0


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_changed_paths(cwd: Path, scope: str) -> set[str]:
    """Get changed file paths from git status.

    Uses -z for NUL-separated output (safe for non-ASCII/renamed paths)
    and core.quotePath=false to avoid C-quoting of filenames.

    Porcelain format (-z): each line is "XY PATH", status + space + path.
    For renames/copies: status line is "R  NEW_PATH", followed by a second
    field "OLD_PATH" (no status prefix). We extract the new path from the
    status line and skip the old-path field (not appending to result).
    """
    result = _git(
        ["-c", "core.quotePath=false", "status", "--porcelain", "-z",
         "--untracked-files=all", "--", scope],
        cwd
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr}")

    paths = set()
    # Split on NUL byte for safety with non-ASCII and renamed paths
    if result.stdout:
        fields = result.stdout.split("\0")
        i = 0
        while i < len(fields):
            line = fields[i]
            if not line:  # skip empty strings from trailing NUL
                i += 1
                continue

            # Porcelain format: XY PATH (skip first 3 chars: 2 status + space)
            if len(line) > 3:
                status = line[0]
                path = line[3:]

                # For renames (R) and copies (C), the next field is the old path
                # We skip it and don't add it to results (we only care about new paths)
                if status in ("R", "C"):
                    # Skip the old-path field by incrementing i
                    i += 2  # Move past this line and the next (old-path)
                    paths.add(path)
                else:
                    paths.add(path)
                    i += 1
            else:
                i += 1

    return paths


def cmd_run(args: argparse.Namespace) -> int:
    # Before anything else, and before any write: `world_root` only checks that
    # the id is filesystem-safe, so a typo'd --world used to CREATE
    # `worlds/<typo>/...` from nothing, commit it, and report success -- while
    # `list_worlds` skipped it forever (no world.md), leaving the content
    # unreachable from the app and absent from the world it was meant for. The
    # `manifest["world"] != --world` guard below cannot catch that: both names
    # come from the same mistaken source.
    if not worlds.world_exists(args.world):
        print(json.dumps({"status": "aborted", "reason": "no such world",
                          "world": args.world}))
        return 1

    root = worlds.world_root(args.world)
    grimoire_root = home()
    world_rel = _world_rel(root)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    if manifest.get("world") != args.world:
        print(json.dumps({"status": "aborted", "reason": "manifest world does not match --world"}))
        return 1

    # Whole-repo dirty check, not just this world's path: `git commit` with no
    # pathspec commits everything staged, so anything dirty anywhere is a risk
    # of getting swept into this world's checkpoint.
    if _git_changed_paths(grimoire_root, "."):
        print(json.dumps({"status": "aborted", "reason": "repo is dirty"}))
        return 1

    results = apply_manifest(root, manifest, args.world)
    git_changed = _git_changed_paths(grimoire_root, world_rel)

    # Anything changed OUTSIDE this world. The repo was verified clean above, so
    # every such path was written by this run -- and the only thing here that
    # writes outside `world_rel` is `overlay.forget_world_record`, sweeping a
    # reclassified lore id out of the dependent campaigns (detached.json,
    # sync.md, campaign-local state filed under that id).
    #
    # Those writes have to land in THIS commit, not be left behind: they are
    # consequences of the same reclassification, and the whole-repo dirty check
    # at the top means anything left uncommitted here aborts the NEXT world's
    # run with a bare "repo is dirty" that names nothing. So the commit's
    # pathspec widens to exactly those paths -- still enumerated rather than a
    # bare repo-wide `git add -A`, which would let an interleaved run's staged
    # work be swept into this world's checkpoint.
    outside = {p for p in _git_changed_paths(grimoire_root, ".")
               if p != world_rel and not p.startswith(world_rel + "/")}
    results["swept_files"] = sorted(outside)
    if outside and not results["swept"]:
        # No sweep ran, so nothing here explains a write outside the world.
        # Refuse the commit and leave it in the tree for a human: a silent
        # commit of an unexplained change is the worse outcome, and so is a
        # scoped commit that leaves it to surface as the next run's dirty abort.
        results["errors"].append({
            "stage": "run",
            "reason": "changes outside this world that no record sweep explains",
            "paths": sorted(outside)})

    verify_result = verify_manifest(root, touched_files=results["touched_files"], git_changed=git_changed)
    results["verify"] = verify_result

    if results["errors"] or not verify_result["ok"]:
        results["committed"] = False
    elif not git_changed and not outside:
        results["committed"] = "noop"
    else:
        scope = [world_rel, *sorted(outside)]
        add = _git(["add", "-A", "--", *scope], grimoire_root)
        summary = f"{len(results['created'])} created, {len(results['skipped'])} skipped, {len(results['errors'])} errors"
        # Scoped commit: prevents interleaved runs from sweeping other worlds' staged changes
        commit = _git(["commit", "-q", "-m", f"{args.world}: populate content ({summary})", "--", *scope], grimoire_root)
        results["committed"] = add.returncode == 0 and commit.returncode == 0
        if not results["committed"]:
            results["git_error"] = (add.stderr or "") + (commit.stderr or "")

    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if results["committed"] in (True, "noop") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Apply a world-content manifest to a real grimoire world.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="print the existing-content index for a world")
    p_index.add_argument("--world", required=True)

    p_run = sub.add_parser("run", help="apply a manifest to a world, verify, and commit")
    p_run.add_argument("--world", required=True)
    p_run.add_argument("--manifest", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "index":
        return cmd_index(args)
    if args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
