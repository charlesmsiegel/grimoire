"""Author a grimoire World from a JSON plan. Built for the create-world skill —
see .claude/skills/create-world/SKILL.md for the workflow this drives.

Three subcommands:

- `apply --plan plan.json` — find-or-create the World named in the plan, then
  every record it lists, through the same store constructors the app's routes
  call. The plan names records by **name** and refers to other records by name
  too; ids are allocated by the store (`slugify`/`uniquify`), never written by
  hand. Re-runnable: a record whose name already exists in the World is
  updated in place rather than duplicated. The whole plan is validated before
  the first write, so a typo'd reference refuses the run instead of leaving
  half a world behind.
- `check --world <wid>` — what makes a World playable and its records joined
  up: every reference resolves, the plot map is a DAG, some greeting can open
  a scene, and a list of the activation choices worth a second look.
- `summary --world <wid>` — the World's records by kind, with their ids, for
  picking a session back up.

Patch semantics: a key the plan gives is written; a key it omits is left as it
is. Records the plan does not mention are never touched, and nothing is ever
deleted.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import (
    actor_names,
    calendars,
    campaigns,
    characters,
    config,
    entities,
    entity_schema,
    greetings,
    locks,
    modules,
    pcs,
    tags,
    voice_anchors,
    worlds,
)
from grimoire.store.frontmatter import parse_frontmatter

ENTITY_KINDS = entities.ENTITY_KINDS

#: The V3 card `data` fields a plan may set on a character. Anything else in a
#: character entry is a typo, and refused rather than silently dropped.
CARD_FIELDS = ("description", "personality", "scenario", "first_mes", "mes_example",
               "alternate_greetings", "tags", "creator_notes", "system_prompt",
               "post_history_instructions", "nickname")
CARD_LIST_FIELDS = ("alternate_greetings", "tags")

PLAN_KEYS = {"world", "module", "calendar", "tags", "characters", "pcs", "greetings",
             *ENTITY_KINDS}
ENTITY_KEYS = {"name", "body", "keys", "owners", "secrecy", "fields"}
CHARACTER_KEYS = {"name", "voice_anchor", *CARD_FIELDS}
PC_KEYS = {"name", "tags", "pronouns", "summary", "birthdate", "description"}
GREETING_KEYS = {"name", "body", "character", "present", "location", "requires_tags",
                 "predecessor_join", "pcless", "phase", "sequence", "optional",
                 "leads_to", "excludes"}
#: The kinds an `owners` ref may name: what `context.world_state.activate`
#: compares against the scene's present set, which holds the cast and the
#: current location. Nothing makes an item, a group or a creature present, so
#: an entry owned by one would never activate -- the app's owner picker
#: (`frontend/src/api/loreOwners.ts`) offers exactly these three.
OWNER_KINDS = ("characters", "pcs", "locations")
#: Plan keys holding a list of names (or one comma-separated string).
LIST_KEYS = {"keys", "owners", "present", "requires_tags", "leads_to", "excludes"}


class PlanError(Exception):
    """The plan cannot be applied as written. Carries every problem found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def _norm(name: str) -> str:
    return name.strip().casefold()


def _list_ok(value) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(v, str) for v in value))


def _as_list(value) -> list[str]:
    """A plan list, or a comma-separated string spelling one. Anything else
    reads as empty: validation (`_list_ok`) has refused it before a write."""
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if not _list_ok(value) or value is None:
        return []
    return [v.strip() for v in value if v.strip()]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _one_line(text: str) -> bool:
    """Frontmatter scalars are single lines, split with `splitlines` -- so is
    everything that counts as a line boundary there (`entity_schema.referenceable`)."""
    return text.splitlines() == [text]


# ---------------------------------------------------------------------------
# The world's records, indexed by name and by id
# ---------------------------------------------------------------------------

class Index:
    """name -> id for every kind a plan can refer to, over what is on disk
    plus what the plan is about to create (`PENDING` until it exists)."""

    PENDING = "<pending>"

    def __init__(self):
        self.by_kind: dict[str, dict[str, str]] = {}
        self.ids: dict[str, set[str]] = {}
        self.dupes: dict[str, set[str]] = {}

    def add(self, kind: str, name: str, rid: str) -> None:
        names = self.by_kind.setdefault(kind, {})
        key = _norm(name)
        if key in names and names[key] != rid and rid != self.PENDING:
            self.dupes.setdefault(kind, set()).add(key)
        names[key] = rid
        if rid != self.PENDING:
            self.ids.setdefault(kind, set()).add(rid)

    def lookup(self, kind: str, ref: str) -> str | None:
        """An id for `ref` in `kind`: a name first, as find-or-create matches,
        then an exact id."""
        ref = ref.strip()
        by_name = self.by_kind.get(kind, {}).get(_norm(ref))
        if by_name is not None:
            return by_name
        return ref if ref in self.ids.get(kind, set()) else None

    def named(self, kind: str, name: str) -> str | None:
        """The record called `name`. Find-or-create matches on the name alone:
        a new record whose name happens to equal an existing record's *id* is
        a different record."""
        return self.by_kind.get(kind, {}).get(_norm(name))

    def ambiguous(self, kind: str, ref: str) -> bool:
        """Two records share this name, or it is one record's name and another
        record's id -- the case a rename in the app leaves behind, where the
        old id now belongs to a record called something else."""
        ref = ref.strip()
        if _norm(ref) in self.dupes.get(kind, set()):
            return True
        by_name = self.by_kind.get(kind, {}).get(_norm(ref))
        return by_name is not None and by_name != ref and ref in self.ids.get(kind, set())


def world_index(root: Path | None) -> Index:
    idx = Index()
    if root is None:
        return idx
    for tid, display in tags.read_tags(root).items():
        idx.add("tags", display, tid)
    for row in characters.list_characters(root):
        idx.add("characters", row["name"], row["id"])
    for row in pcs.list_pcs(root):
        idx.add("pcs", row["name"], row["id"])
    for kind in ENTITY_KINDS:
        for row in entities.list_entities(root, kind):
            idx.add(kind, row["name"], row["id"])
    for row in greetings.list_greetings(root):
        idx.add("greetings", row["name"], row["id"])
    return idx


def find_world(name: str) -> str | None:
    matches = [w["id"] for w in worlds.list_worlds() if _norm(w["name"]) == _norm(name)]
    if len(matches) > 1:
        raise PlanError([(f"more than one world is named {name!r} ({', '.join(matches)}); "
                          "pass --world-id to say which")])
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Validation: everything that can be refused before the first write
# ---------------------------------------------------------------------------

def _ref(idx: Index, kind: str, value: str, where: str) -> Iterator[str]:
    if idx.ambiguous(kind, value):
        yield (f"{where}: {value!r} could mean more than one {kind} record (a shared "
               "name, or one record's name and another's id); rename one in the app")
    elif idx.lookup(kind, value) is None:
        yield f"{where}: no {kind} record named {value!r}"


def _refs(idx: Index, kind: str, values, where: str) -> Iterator[str]:
    for v in _as_list(values):
        yield from _ref(idx, kind, v, where)


def _kind_refs(idx: Index, values, allowed, where: str) -> Iterator[str]:
    """`<kind>:<name or id>` references, as `owners` and ref fields spell them."""
    for value in _as_list(values):
        kind, sep, target = value.partition(":")
        if not sep or kind not in allowed:
            yield (f"{where}: {value!r} must be <kind>:<name>, kind one of "
                   f"{', '.join(allowed)}")
        else:
            yield from _ref(idx, kind, target, where)


def _typed(row: dict, where: str, keys, kind: type, label: str) -> Iterator[str]:
    for k in keys:
        if k in row and not isinstance(row[k], kind):
            yield f"{where}: {k} must be {label}"


def _entries(plan: dict, key: str, allowed: set[str], problems: list[str],
             list_keys: set[str] = LIST_KEYS) -> list[dict]:
    rows = plan.get(key)
    if rows is None:
        return []
    if not isinstance(rows, list):
        # checked BEFORE defaulting: `"lore": {}` is a malformed plan, not an
        # empty section, and must not validate as one
        problems.append(f"{key}: must be a list")
        return []
    seen: set[str] = set()
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) \
                or not row["name"].strip():
            problems.append(f"{key}[{i}]: every entry needs a non-empty name")
            continue
        where = f"{key} {row['name']!r}"
        if not _one_line(row["name"]):
            problems.append(f"{where}: a name must be one line")
        problems.extend(f"{where}: {k} must be a list of names"
                        for k in sorted(set(row) & list_keys) if not _list_ok(row[k]))
        # Every item, whichever spelling the list came in (a list, or one
        # comma-separated string): each is written into a frontmatter line.
        problems.extend(f"{where}: {k} entry {v!r} must be one line"
                        for k in sorted(set(row) & list_keys) if _list_ok(row[k])
                        for v in _as_list(row[k]) if not _one_line(v))
        unknown = sorted(set(row) - allowed)
        if unknown:
            problems.append(f"{where}: unknown keys {unknown} (allowed: {sorted(allowed)})")
        if _norm(row["name"]) in seen:
            problems.append(f"{where}: listed twice")
        seen.add(_norm(row["name"]))
        out.append(row)
    return out


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """One representative path per cycle found by a DFS over `graph`."""
    found: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                found.append([*stack[stack.index(nxt):], nxt])
            elif nxt not in state:
                visit(nxt)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if node not in state:
            visit(node)
    return found


def _check_character(row: dict, idx: Index) -> Iterator[str]:
    where = f"characters {row['name']!r}"
    scalars = [f for f in CARD_FIELDS if f not in CARD_LIST_FIELDS]
    yield from _typed(row, where, [*scalars, "voice_anchor"], str, "a string")
    for f in CARD_LIST_FIELDS:
        if f in row and (not isinstance(row[f], list)
                         or not all(isinstance(v, str) for v in row[f])):
            yield f"{where}: {f} must be a list of strings"


def _single_lines(row: dict, where: str, keys) -> Iterator[str]:
    """Values written as frontmatter scalars must be one line: a line break
    there is stored, reported saved, and read back truncated -- or with the
    continuation parsed as a key of its own."""
    for k in keys:
        if isinstance(row.get(k), str) and row[k] and not _one_line(row[k]):
            yield f"{where}: {k} must be one line (it is stored as frontmatter)"


def _check_pc(row: dict, idx: Index) -> Iterator[str]:
    where = f"pcs {row['name']!r}"
    yield from _refs(idx, "tags", row.get("tags"), f"{where} tags")
    yield from _typed(row, where, ("pronouns", "summary", "birthdate", "description"),
                      str, "a string")
    # `description` is the persona's body; the rest are frontmatter scalars
    yield from _single_lines(row, where, ("pronouns", "summary", "birthdate"))


def _check_fields(kind: str, fields: dict, idx: Index, where: str) -> Iterator[str]:
    bad = entity_schema.invalid_keys(kind, fields)
    if bad:
        yield (f"{where}: {kind} has no fields {bad} "
               f"(it has: {list(entity_schema.field_keys(kind))})")
    refs = {f["key"]: f for f in entity_schema.ref_fields(kind)}
    plain = {k: v for k, v in fields.items() if k not in refs and k not in bad}
    for k in entity_schema.invalid_values(kind, plain):
        yield f"{where}: field {k} has an invalid value {fields[k]!r}"
    yield from _single_lines(plain, f"{where} field", plain)
    for k, spec in refs.items():
        if not _list_ok(fields.get(k)):
            yield f"{where}: field {k} must be a reference or a list of them"
            continue
        values = _as_list(fields.get(k))
        if len(values) > 1 and not spec.get("multi"):
            yield f"{where}: field {k} takes one reference"
        yield from _kind_refs(idx, values, spec["kinds"], f"{where} field {k}")


def _check_entity(kind: str, row: dict, idx: Index) -> Iterator[str]:
    where = f"{kind} {row['name']!r}"
    yield from _typed(row, where, ("body",), str, "a string")
    yield from _kind_refs(idx, row.get("owners"), OWNER_KINDS, f"{where} owners")
    if isinstance(row.get("keys"), list):
        yield from (f"{where}: key {k!r} has a comma, which the store reads as two keys"
                    for k in row["keys"] if isinstance(k, str) and "," in k)
    if "secrecy" in row and str(row["secrecy"]).strip().lower() not in entities.SECRECY_LEVELS:
        yield f"{where}: secrecy must be one of {', '.join(entities.SECRECY_LEVELS)}"
    fields = row.get("fields")
    if fields is None:
        return
    if not isinstance(fields, dict):
        # the raw value, before any default: `"fields": []` is a malformed
        # plan, not an empty block
        yield f"{where}: fields must be an object"
    else:
        yield from _check_fields(kind, fields, idx, where)


def _check_greeting(row: dict, idx: Index) -> Iterator[str]:
    where = f"greetings {row['name']!r}"
    # `character` and `location` are one name each, and "" is a value: it
    # clears the field. A non-string is refused here rather than read as empty
    # (which would silently clear it) or reaching a lookup (which would raise).
    yield from _typed(row, where, ("body", "phase", "character", "location"), str, "a string")
    yield from _typed(row, where, ("pcless", "optional"), bool, "true or false")
    yield from _single_lines(row, where, ("phase",))
    if isinstance(row.get("character"), str) and row["character"]:
        yield from _ref(idx, "characters", row["character"], f"{where} character")
    yield from _refs(idx, "characters", row.get("present"), f"{where} present")
    if isinstance(row.get("location"), str) and row["location"]:
        yield from _ref(idx, "locations", row["location"], f"{where} location")
    yield from _refs(idx, "tags", row.get("requires_tags"), f"{where} requires_tags")
    for key in ("leads_to", "excludes"):
        yield from _refs(idx, "greetings", row.get(key), f"{where} {key}")
    if row.get("predecessor_join", "all") not in ("all", "any"):
        yield f"{where}: predecessor_join must be 'all' or 'any'"
    seq = row.get("sequence")
    if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int) or seq < 1):
        yield f"{where}: sequence must be a positive integer"


def _edges_ok(edges) -> bool:
    return isinstance(edges, dict) and all(
        isinstance(edges.get(k, []), list) and all(isinstance(t, str) for t in edges.get(k, []))
        for k in ("leads_to", "excludes"))


def _read_plotmap(root: Path | None) -> tuple[dict, list[str]]:
    """The on-disk plot map, keeping only well-formed entries, plus what was
    wrong with the rest. `plotmap.json` is hand-editable and travels in world
    bundles, so a reader here must report a bad entry rather than raise on it."""
    if root is None:
        return {}, []
    try:
        raw = greetings.read_plotmap(root)
    except ValueError as exc:
        return {}, [f"plotmap: plotmap.json is not valid JSON ({exc})"]
    if not isinstance(raw, dict):
        return {}, ["plotmap: plotmap.json must be an object keyed by greeting id"]
    bad = [f"plotmap: {src} must map to {{leads_to: [...], excludes: [...]}}"
           for src, e in raw.items() if not _edges_ok(e)]
    return {src: e for src, e in raw.items() if _edges_ok(e)}, bad


def _merged_plotmap(rows: list[dict], idx: Index, root: Path | None) -> dict[str, list[str]]:
    """The `leads_to` graph as it would stand after apply: what is on disk,
    with each plan greeting's own `leads_to` replacing its stored edges. Nodes
    are ids, and `new:<name>` for a greeting the plan has yet to create, so a
    cycle closed half by an earlier apply and half by this one is still seen."""
    def node(ref: str) -> str:
        rid = idx.lookup("greetings", ref)
        return f"new:{_norm(ref)}" if rid in (None, Index.PENDING) else rid

    graph = {src: list(e.get("leads_to") or []) for src, e in _read_plotmap(root)[0].items()}
    for g in rows:
        if "leads_to" in g:
            graph[node(g["name"])] = [node(t) for t in _as_list(g["leads_to"])]
    return graph


def _check_actor_names(rows: dict, idx: Index, wid: str | None) -> Iterator[str]:
    """Characters and PCs share one name space: the transcript labels lines by
    full name, so two actors with one name are two people nobody can tell
    apart in a scene (`store/actor_names.py`)."""
    pc_names = {_norm(r["name"]) for r in rows["pcs"]}
    for row in rows["characters"]:
        if _norm(row["name"]) in pc_names:
            yield f"characters {row['name']!r}: a pcs entry has the same name"
    if wid is None:
        return
    for kind in ("characters", "pcs"):
        for row in rows[kind]:
            if idx.named(kind, row["name"]) != Index.PENDING:
                continue   # an existing record of this kind: it is updated, not claimed
            try:
                actor_names.require_unique(row["name"], scope="world", scope_id=wid)
            except actor_names.ActorNameError as exc:
                yield f"{kind} {row['name']!r}: {exc}"


CALENDAR_KEYS = {"primary", "secondary", "confirmed", "stale_after_days", "warn_days"}
CALENDAR_BLOCK_KEYS = {"provider", "region", "custom_holidays", "anchor"}


def _calendar_settings(cal: dict) -> Iterator[str]:
    """The full form's scalars, typed because `write_calendar` coerces rather
    than refuses: the string "false" is truthy and would save as confirmed."""
    if "confirmed" in cal and not isinstance(cal["confirmed"], bool):
        yield "calendar: confirmed must be true or false"
    for k in ("stale_after_days", "warn_days"):
        v = cal.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            yield f"calendar: {k} must be a whole number of days"


def _calendar_block(label: str, block) -> Iterator[str]:
    if not isinstance(block, dict):
        yield f"{label}: must be a calendar block (provider, region, ...)"
        return
    unknown = sorted(set(block) - CALENDAR_BLOCK_KEYS)
    if unknown:
        yield f"{label}: unknown keys {unknown} (allowed: {sorted(CALENDAR_BLOCK_KEYS)})"
    yield from (f"{label}: {k} must be a string"
                for k in ("provider", "region") if k in block and not isinstance(block[k], str))
    if "custom_holidays" in block and not isinstance(block["custom_holidays"], list):
        yield f"{label}: custom_holidays must be a list"


def _calendar_shape(cal) -> Iterator[str]:
    """Unknown keys and wrong types, refused rather than normalized away:
    `write_calendar` keeps only the keys it knows, so a typo'd `regoin` would
    save as the default region and report success."""
    if not isinstance(cal, dict):
        yield "calendar: must be an object"
        return
    if "primary" not in cal:
        yield from _calendar_block("calendar", cal)
        return
    unknown = sorted(set(cal) - CALENDAR_KEYS)
    if unknown:
        yield f"calendar: unknown keys {unknown} (allowed: {sorted(CALENDAR_KEYS)})"
    yield from _calendar_settings(cal)
    yield from _calendar_block("primary", cal["primary"])
    if cal.get("secondary") is not None:
        yield from _calendar_block("secondary", cal["secondary"])


def _check_world_settings(plan: dict, root: Path | None, wid: str | None) -> Iterator[str]:
    cal = plan.get("calendar")
    if cal is not None:
        yield from _calendar_shape(cal)
    if cal is not None and isinstance(cal, dict):
        try:
            calendars.validate_calendar(_calendar_config(cal, root))
        except (calendars.CalendarError, TypeError, AttributeError) as exc:
            yield f"calendar: {exc}"
    if "module" not in plan:
        return
    mid = plan["module"]
    if not isinstance(mid, str):
        yield "module: must be a module id, or \"\" to clear the world's module"
        return
    if mid:
        # `load_pack`, not `pack_root`: a user-library pack with a module.md
        # resolves either way, and an invalid one is disabled at resolution --
        # binding it would give the world a module that never takes effect.
        try:
            pack_errors = modules.load_pack(str(mid))["errors"]
        except modules.ModuleNotFound:
            yield f"module: no module {mid!r} (built-in or in the user library)"
        else:
            yield from (f"module {mid}: {e}" for e in pack_errors)
    if wid is not None and mid != _world_module(wid) and _world_campaigns(wid):
        yield ("module: this world already has campaigns; change its module from the "
               "world editor, which rebinds them under their locks")


def _index_plan(plan: dict, idx: Index, problems: list[str]) -> dict[str, list[dict]]:
    """The plan's entries per kind, with every new name added to `idx` as
    PENDING so a reference to a record the plan is about to create resolves."""
    tag_names = plan.get("tags")
    if tag_names is None:
        tag_names = []
    if not isinstance(tag_names, list) or not all(isinstance(t, str) and t.strip()
                                                  and _one_line(t) for t in tag_names):
        problems.append("tags: must be a list of display names")
        tag_names = []
    for t in tag_names:
        if idx.named("tags", t) is None:
            idx.add("tags", t, Index.PENDING)
    rows = {
        "characters": _entries(plan, "characters", CHARACTER_KEYS, problems, set()),
        "pcs": _entries(plan, "pcs", PC_KEYS, problems, {"tags"}),
        "greetings": _entries(plan, "greetings", GREETING_KEYS, problems),
        **{k: _entries(plan, k, ENTITY_KEYS, problems) for k in ENTITY_KINDS},
    }
    for kind, entries in rows.items():
        for row in entries:
            if idx.ambiguous(kind, row["name"]):
                problems.append(f"{kind} {row['name']!r}: more than one existing record "
                                "has this name; rename one in the app first")
            if idx.named(kind, row["name"]) is None:
                idx.add(kind, row["name"], Index.PENDING)
    return rows


def _check_world_name(world, wid: str | None) -> Iterator[str]:
    if not isinstance(world, str):
        yield "world: must be a string"
    elif not world.strip():
        if wid is None:
            yield "world: the plan needs a world name (or pass --world-id)"
    elif not _one_line(world.strip()):
        yield "world: a name must be one line"


def validate_plan(plan: dict, root: Path | None, wid: str | None) -> list[str]:
    """Every problem with `plan` against the world at `root` (None: a world
    the plan will create). Empty means `apply_plan` will write it."""
    if not isinstance(plan, dict):
        return ["the plan must be a JSON object"]
    problems: list[str] = []
    unknown = sorted(set(plan) - PLAN_KEYS)
    if unknown:
        problems.append(f"unknown top-level keys {unknown} (allowed: {sorted(PLAN_KEYS)})")
    problems.extend(_check_world_name(plan.get("world", ""), wid))
    idx = world_index(root)
    rows = _index_plan(plan, idx, problems)
    problems.extend(_check_actor_names(rows, idx, wid))
    for row in rows["characters"]:
        problems.extend(_check_character(row, idx))
    for row in rows["pcs"]:
        problems.extend(_check_pc(row, idx))
    for kind in ENTITY_KINDS:
        for row in rows[kind]:
            problems.extend(_check_entity(kind, row, idx))
    for row in rows["greetings"]:
        problems.extend(_check_greeting(row, idx))
    if any("leads_to" in g or "excludes" in g for g in rows["greetings"]):
        # `set_edges` reads the raw file, so edges cannot be written into a
        # plot map the sanitizing reader had to drop entries from -- refused
        # here, before phase A, rather than raised after the records landed
        problems.extend(f"{msg}; fix plotmap.json before writing edges"
                        for msg in _read_plotmap(root)[1])
    problems.extend(f"greetings: leads_to would form a cycle through {' -> '.join(c)}"
                    for c in _cycles(_merged_plotmap(rows["greetings"], idx, root)))
    problems.extend(_check_world_settings(plan, root, wid))
    return problems


def _calendar_config(cal: dict, root: Path | None) -> dict:
    """The plan's calendar as a whole `calendar.json` config.

    A plan may give the whole config (`{"primary": {...}, "secondary": ...}`)
    or just the primary block (`{"provider": "gregorian", "region": "US"}`).
    Either way an authored calendar is a deliberate choice, so it is
    `confirmed` unless the plan says otherwise -- a campaign made from this
    world then starts on it without asking (`routes/worlds.py`, #223)."""
    current = calendars.read_calendar(root) if root is not None else calendars.default_calendar()
    cfg = {**current, **cal} if "primary" in cal else {**current, "primary": cal}
    if "confirmed" not in cal:
        cfg["confirmed"] = True
    return cfg


def _world_module(wid: str) -> str:
    meta, _ = parse_frontmatter(worlds.world_meta_path(wid).read_text(encoding="utf-8"))
    return (meta.get("module") or "").strip()


def _world_campaigns(wid: str) -> list[str]:
    """Campaigns played in this world, matched by `references_world` -- a
    store written before campaign creation canonicalized its reference can
    spell the world in another case (#259), and a string compare would miss
    exactly the campaigns the module guard exists to protect."""
    root = worlds.world_root(wid)
    return [c["id"] for c in campaigns.list_campaigns()
            if worlds.references_world(c.get("world") or "", root)]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.rows: list[dict] = []

    def note(self, kind: str, name: str, rid: str, status: str) -> None:
        self.rows.append({"kind": kind, "name": name, "id": rid, "status": status})


def _snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _dir_snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.glob("*.md"))}


def _changed(before: bytes | None, path: Path) -> bool:
    return before != _snapshot(path)


def _resolve_world(plan: dict, world_id: str | None) -> str | None:
    """The world to write into: `world_id`, or None for a new one.

    A plan that names an EXISTING world without `--world-id` is refused rather
    than merged into it. Find-or-create by name is right for the records in a
    world this skill made; for the world itself it would mean a "new" concept
    that happens to share a name with somebody's library world silently
    overwrites that world's records."""
    if world_id is not None:
        if not worlds.world_exists(world_id):
            raise PlanError([f"no world with id {world_id!r}"])
        return world_id
    name = plan.get("world", "")
    found = find_world(name) if isinstance(name, str) and name.strip() else None
    if found is not None:
        raise PlanError([(f"world: a world named {name!r} already exists (id {found!r}); "
                          f"pass --world-id {found} to add to it, or choose another name")])
    return None


def _ensure_records(plan: dict, root: Path, wid: str, idx: Index,
                    report: Report) -> set[tuple[str, str]]:
    """Phase A: every record the plan names exists, so every name has an id.
    Records reference each other in every direction (an item held by a group,
    a group headquartered at a location), so their content waits for phase B.
    Returns the `(kind, id)` pairs this call created."""
    created: set[tuple[str, str]] = set()
    for name in plan.get("tags") or []:
        if idx.named("tags", name) is None:
            tid = tags.add_tag(root, name.strip())
            idx.add("tags", name, tid)
            report.note("tags", name, tid, "created")
    makers = [("characters", lambda n: _create_character(root, wid, n)),
              ("pcs", lambda n: _create_pc(root, wid, n)),
              *((k, lambda n, k=k: entities.create_entity(root, k, n)) for k in ENTITY_KINDS)]
    for kind, make in makers:
        for row in plan.get(kind) or []:
            if idx.named(kind, row["name"]) is None:
                rid = make(row["name"].strip())
                idx.add(kind, row["name"], rid)
                created.add((kind, rid))
    return created


def _write_records(plan: dict, root: Path, idx: Index, created: set[tuple[str, str]],
                   report: Report) -> None:
    """Phase B: content, with every reference resolved to an id."""
    writers = [("characters", lambda rid, row: _write_character(root, rid, row)),
               ("pcs", lambda rid, row: _write_pc(root, rid, row, idx)),
               *((k, lambda rid, row, k=k: _write_entity(root, k, rid, row, idx))
                 for k in ENTITY_KINDS)]
    for kind, write in writers:
        for row in plan.get(kind) or []:
            rid = idx.named(kind, row["name"])
            changed = write(rid, row)
            report.note(kind, row["name"], rid,
                        "created" if (kind, rid) in created
                        else ("updated" if changed else "unchanged"))
    for row in plan.get("greetings") or []:
        gid, was_created, changed = _write_greeting(root, row, idx)
        idx.add("greetings", row["name"], gid)
        report.note("greetings", row["name"], gid,
                    "created" if was_created else ("updated" if changed else "unchanged"))


def _write_edges(plan: dict, root: Path, idx: Index, report: Report) -> None:
    """After every greeting exists, since an edge names two of them."""
    path = root / "plotmap.json"
    before = _snapshot(path)
    for row in plan.get("greetings") or []:
        edges = {key: _unique([idx.lookup("greetings", t) for t in _as_list(row[key])])
                 for key in ("leads_to", "excludes") if key in row}
        if edges:
            greetings.set_edges(root, idx.named("greetings", row["name"]), **edges)
    if _changed(before, path):
        report.note("plotmap", "plotmap.json", "plotmap", "updated")


def _write_settings(plan: dict, root: Path, wid: str, report: Report) -> None:
    if plan.get("calendar") is not None:
        path = root / "calendar.json"
        before = _snapshot(path)
        calendars.write_calendar(root, _calendar_config(plan["calendar"], root))
        report.note("calendar", "calendar.json", "calendar",
                    "updated" if _changed(before, path) else "unchanged")
    # Presence, not truthiness: `"module": ""` clears the binding, omitting
    # the key leaves it alone.
    if "module" in plan and plan["module"] != _world_module(wid):
        modules.set_world_module(wid, plan["module"])
        report.note("module", plan["module"] or "(none)", plan["module"], "updated")


def apply_plan(plan: dict, world_id: str | None = None) -> dict:
    """Validate, then write. Returns `{"world": wid, "records": [...]}`."""
    if not isinstance(plan, dict):
        raise PlanError(["the plan must be a JSON object"])
    wid = _resolve_world(plan, world_id)
    problems = validate_plan(plan, worlds.world_root(wid) if wid else None, wid)
    if problems:
        raise PlanError(problems)

    report = Report()
    if wid is None:
        wid = worlds.create_world(str(plan["world"]).strip())
        config.mark_setup_done()   # as POST /worlds does (#194)
        report.note("world", plan["world"], wid, "created")
    else:
        report.note("world", worlds.world_name(wid) or wid, wid, "found")
    root = worlds.world_root(wid)
    idx = world_index(root)
    created = _ensure_records(plan, root, wid, idx, report)
    _write_records(plan, root, idx, created, report)
    _write_edges(plan, root, idx, report)
    _write_settings(plan, root, wid, report)
    return {"world": wid, "records": report.rows}


def _create_character(root: Path, wid: str, name: str) -> str:
    # The lock and the name check POST /worlds/{wid}/characters takes: the
    # check reads every actor in the world and its campaigns, and the lock is
    # what keeps a concurrent create in the app from claiming the name between.
    with locks.world_actor_lock(wid):
        actor_names.require_unique(name, scope="world", scope_id=wid)
        cid, _ = characters.create_character(root, name)
    return cid


def _create_pc(root: Path, wid: str, name: str) -> str:
    with locks.world_actor_lock(wid):
        actor_names.require_unique(name, scope="world", scope_id=wid)
        pid, _ = pcs.create_pc(root, name, [])
    return pid


def _write_character(root: Path, cid: str, row: dict) -> bool:
    vid = characters.default_version(root, cid)
    card_path = root / "characters" / cid / f"{vid}.json"
    before = _snapshot(card_path)
    given = [f for f in CARD_FIELDS if f in row]
    if given:
        # the same rule as a PC's persona: an anchor-only patch leaves the card
        card = characters.read_card(root, cid, vid)
        data = card.setdefault("data", {})
        data.update({f: list(row[f]) if f in CARD_LIST_FIELDS else row[f] for f in given})
        characters.update_version(root, cid, vid, card)
    changed = _changed(before, card_path)
    if "voice_anchor" in row:
        anchor = voice_anchors.anchor_path(root, cid)
        before = _snapshot(anchor)
        voice_anchors.write(root, cid, row["voice_anchor"])
        changed = changed or _changed(before, anchor)
    return changed


def _write_pc(root: Path, pid: str, row: dict, idx: Index) -> bool:
    vid = pcs.read_pc(root, pid)["meta"]["default_version"]
    before = _dir_snapshot(root / "pcs" / pid)
    given = [f for f in ("pronouns", "summary", "birthdate", "description") if f in row]
    if given:
        # only when the plan names a persona field: re-serializing an untouched
        # persona strips its body and drops frontmatter keys the writer does
        # not know, which a tags-only patch has no business doing
        persona = pcs.read_persona(root, pid, vid)
        persona.update({f: row[f] for f in given})
        pcs.update_version(root, pid, vid, persona)
    if "tags" in row:
        pcs.set_tags(root, pid, _unique([idx.lookup("tags", t) for t in _as_list(row["tags"])]))
    return before != _dir_snapshot(root / "pcs" / pid)


def _resolve_kind_ref(idx: Index, value: str) -> str:
    kind, _, target = value.partition(":")
    return f"{kind}:{idx.lookup(kind, target)}"


def _write_entity(root: Path, kind: str, eid: str, row: dict, idx: Index) -> bool:
    path = root / kind / f"{eid}.md"
    before = _snapshot(path)
    fields = {}
    ref_keys = {f["key"] for f in entity_schema.ref_fields(kind)}
    for k, v in (row.get("fields") or {}).items():
        value = ",".join(_resolve_kind_ref(idx, r) for r in _as_list(v)) if k in ref_keys else v
        fields[k] = "" if value is None else (value if isinstance(value, str) else str(value))
    entities.update_entity(
        root, kind, eid,
        body=row.get("body"),
        keys=", ".join(_as_list(row["keys"])) if "keys" in row else None,
        owners=(",".join(_unique([_resolve_kind_ref(idx, o) for o in _as_list(row["owners"])]))
                if "owners" in row else None),
        secrecy=row.get("secrecy"),
        fields=fields or None)
    return _changed(before, path)


def _write_greeting(root: Path, row: dict, idx: Index) -> tuple[str, bool, bool]:
    """(gid, created, changed). A greeting is created in one call rather than
    in phase A: nothing but plot-map edges refers to one, and those are written
    after every greeting exists."""
    character = idx.lookup("characters", row["character"]) if row.get("character") else None
    kw: dict = {}
    if "present" in row:
        kw["present"] = _unique([idx.lookup("characters", p) for p in _as_list(row["present"])])
    if "location" in row:
        kw["location"] = idx.lookup("locations", row["location"]) if row["location"] else ""
    if "requires_tags" in row:
        kw["requires_tags"] = _unique([idx.lookup("tags", t)
                                       for t in _as_list(row["requires_tags"])])
    for k in ("predecessor_join", "pcless", "phase", "optional"):
        if k in row:
            kw[k] = row[k]
    gid = idx.named("greetings", row["name"])
    if gid is None:
        version = characters.default_version(root, character) if character else ""
        gid = greetings.create_greeting(root, row["name"].strip(), character or "", version,
                                        body=row.get("body", ""), sequence=row.get("sequence"),
                                        **kw)
        return gid, True, True
    path = root / "greetings" / f"{gid}.md"
    before = _snapshot(path)
    stored = greetings.read_greeting(root, gid)["meta"]["character"]
    if "character" in row and (character or "") != stored:
        # Only a real change re-points: the plan cannot name a version, so
        # re-sending the same character would reset one chosen in the app.
        kw["character"] = character or ""
        kw["version"] = characters.default_version(root, character) if character else ""
    if "sequence" in row:
        kw["sequence"] = row["sequence"]
    greetings.update_greeting(root, gid, body=row.get("body"), **kw)
    return gid, False, _changed(before, path)


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

class _World:
    """One read of a world's records, shared by the checks below."""

    def __init__(self, wid: str):
        self.wid = wid
        self.root = root = worlds.world_root(wid)
        self.tag_ids = set(tags.read_tags(root))
        self.characters = characters.list_characters(root)
        self.pcs = pcs.list_pcs(root)
        self.ids: dict[str, set[str]] = {"characters": {c["id"] for c in self.characters},
                                         "pcs": {p["id"] for p in self.pcs}}
        self.entities = {k: entities.list_entities(root, k) for k in ENTITY_KINDS}
        for kind, rows in self.entities.items():
            self.ids[kind] = {e["id"] for e in rows}
        self.greetings = greetings.list_greetings(root)
        self.ids["greetings"] = {g["id"] for g in self.greetings}
        self.plotmap, self.plotmap_problems = _read_plotmap(root)

    def resolves(self, ref: str) -> bool:
        kind, _, rid = ref.partition(":")
        return rid in self.ids.get(kind, set())


def _entity_errors(w: _World) -> Iterator[str]:
    for kind, rows in w.entities.items():
        for e in rows:
            where = f"{kind}/{e['id']}"
            yield from (f"{where}: owner {ref} does not exist"
                        for ref in entities.owner_refs(e.get("owners", "")) if not w.resolves(ref))
            for spec in entity_schema.ref_fields(kind):
                yield from (f"{where}: {spec['key']} {ref} does not exist"
                            for ref in entity_schema.parse_refs(e.get(spec["key"]))
                            if not w.resolves(ref))


def _entity_warnings(w: _World) -> Iterator[str]:
    """The activation choices worth a second look: they are legitimate, and
    each is a common way to get the opposite of what was meant."""
    for kind, rows in w.entities.items():
        for e in rows:
            where = f"{kind}/{e['id']}"
            if not entities.read_entity(w.root, kind, e["id"])["body"].strip():
                yield f"{where}: empty body -- nothing reaches the prompt"
            yield from (f"{where}: owner {ref} can never be present in a scene, so the "
                        "owner gate never opens"
                        for ref in entities.owner_refs(e.get("owners", ""))
                        if ref.partition(":")[0] not in OWNER_KINDS)
            keyless = not e.get("keys", "").strip()
            unowned = not e.get("owners", "").strip()
            gm_only = entities.normalize_secrecy(e.get("secrecy")) == entities.GM_ONLY
            if keyless and kind == "locations" and not gm_only:
                # `context.world_state._world_info` skips a keyless location
                yield (f"{where}: no keys -- reaches the prompt only as the current setting, "
                       "never because a scene mentions it")
            elif keyless and unowned and not gm_only:
                yield f"{where}: no keys and no owners -- always on, in every turn's prompt"


def _greeting_errors(w: _World) -> Iterator[str]:
    for p in w.pcs:
        yield from (f"pcs/{p['id']}: tag {t} is not in the world's vocabulary"
                    for t in p["tags"] if t not in w.tag_ids)
    for g in w.greetings:
        where = f"greetings/{g['id']}"
        if g["character"] and g["character"] not in w.ids["characters"]:
            yield f"{where}: character {g['character']} does not exist"
        elif g["character"] and g["version"] not in characters.version_ids(w.root, g["character"]):
            # an EMPTY version included: a scene opened from this greeting
            # seats the character at that version, and there is no such one
            yield (f"{where}: version {g['version'] or '(none)'} of {g['character']} "
                   "does not exist")
        yield from (f"{where}: present {c} does not exist"
                    for c in g["present"] if c not in w.ids["characters"])
        yield from (f"{where}: requires tag {t}, which is not in the vocabulary"
                    for t in g["requires_tags"] if t not in w.tag_ids)
        if g["location"] and g["location"] not in w.ids["locations"]:
            yield f"{where}: location {g['location']} does not exist"


def _plotmap_errors(w: _World) -> Iterator[str]:
    graph: dict[str, list[str]] = {}
    yield from w.plotmap_problems
    for src, edges in w.plotmap.items():
        if src not in w.ids["greetings"]:
            yield f"plotmap: {src} is not a greeting"
        for key in ("leads_to", "excludes"):
            yield from (f"plotmap: {src} {key} {t}, which is not a greeting"
                        for t in edges.get(key) or [] if t not in w.ids["greetings"])
        graph[src] = list(edges.get("leads_to") or [])
    for cycle in _cycles(graph):
        yield (f"plotmap: leads_to cycle {' -> '.join(cycle)} -- none of these can ever "
               "be started")


def _openings(w: _World, errors: list[str], warnings: list[str]) -> None:
    """What a new campaign can open with. `greetings.availability` is the rule
    the scene-start pane applies, so this answers with the app's own rule."""
    if not w.greetings:
        warnings.append("no greetings: a campaign here starts from a blank scene")
        return

    def startable(player_tags) -> list[dict]:
        return [a for a in greetings.availability(w.greetings, w.plotmap, set(), player_tags)
                if a["available"]]

    def onscreen(rows: list[dict]) -> list[dict]:
        # The scene picker offers an offscreen (`pcless`) greeting only to an
        # offscreen scene and an onscreen one only to a PC scene
        # (`SceneIdeaPicker`), so an offscreen opener is no opening for a PC.
        return [a for a in rows if not a["pcless"]]

    tag_free = startable(set())
    every_pc = {p["id"]: startable(set(p["tags"])) for p in w.pcs}
    by_pc = {pid: onscreen(rows) for pid, rows in every_pc.items()}
    if not tag_free and not any(every_pc.values()):
        errors.append("no greeting can open a scene: every one is gated behind a predecessor "
                      "or a tag no player character carries")
    elif not onscreen(tag_free) and not any(by_pc.values()):
        warnings.append("no onscreen greeting is startable: every opening a campaign can use "
                        "is offscreen, with no player character in it")
    elif not onscreen(tag_free):
        warnings.append("no onscreen greeting is startable without tags: only a player "
                        "character with the right tags can begin")
    warnings.extend(f"pcs/{pid}: can start no onscreen greeting"
                    for pid, ok in by_pc.items() if not ok)
    carried = {t for p in w.pcs for t in p["tags"]}
    if w.pcs:
        warnings.extend(f"greetings/{g['id']}: requires {sorted(set(g['requires_tags']) - carried)}"
                        ", which no world player character carries"
                        for g in w.greetings if set(g["requires_tags"]) - carried)


def _character_warnings(w: _World) -> Iterator[str]:
    yield from (f"characters/{cid}: no voice anchor"
                for cid in voice_anchors.anchorless_ids(w.root, sorted(w.ids["characters"])))
    for c in w.characters:
        card = characters.read_card(w.root, c["id"], characters.default_version(w.root, c["id"]))
        if not (card.get("data") or {}).get("description", "").strip():
            yield f"characters/{c['id']}: empty description"


def _calendar_file_errors(root: Path) -> Iterator[str]:
    """The raw calendar.json. `read_calendar` substitutes the default for a
    file it cannot parse -- right for a turn, wrong for an integrity check,
    which has to say the world's calendar is not the one on disk."""
    path = root / "calendar.json"
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        yield f"calendar: calendar.json is not valid JSON ({exc}); the default calendar is used"
        return
    if not isinstance(raw, dict):
        yield "calendar: calendar.json must be an object"
        return
    yield from _calendar_shape(raw if "primary" in raw else {"primary": raw})
    try:
        calendars.validate_calendar(calendars.read_calendar(root))
    except (calendars.CalendarError, TypeError, AttributeError) as exc:
        yield f"calendar: {exc}"


def _settings_errors(w: _World) -> Iterator[str]:
    yield from _calendar_file_errors(w.root)
    mid = _world_module(w.wid)
    if mid:
        try:
            pack_errors = modules.load_pack(mid)["errors"]
        except modules.ModuleNotFound:
            pack_errors = [f"module {mid} not found"]
        yield from (f"module {mid}: {e}" for e in pack_errors)


def check_world(wid: str) -> dict:
    w = _World(wid)
    errors = [*_entity_errors(w), *_greeting_errors(w), *_plotmap_errors(w),
              *_settings_errors(w)]
    warnings = [*_entity_warnings(w), *_character_warnings(w)]
    _openings(w, errors, warnings)
    return {"world": wid, "ok": not errors, "errors": errors, "warnings": warnings}


def summary(wid: str) -> dict:
    root = worlds.world_root(wid)
    out: dict = {"world": wid, "name": worlds.world_name(wid), "module": _world_module(wid),
                 "calendar": calendars.read_calendar(root)["primary"]["provider"],
                 "tags": tags.read_tags(root)}
    out["characters"] = [{"id": c["id"], "name": c["name"]}
                         for c in characters.list_characters(root)]
    out["pcs"] = [{"id": p["id"], "name": p["name"], "tags": p["tags"]}
                  for p in pcs.list_pcs(root)]
    for kind in ENTITY_KINDS:
        out[kind] = [{"id": e["id"], "name": e["name"], "keys": e.get("keys", ""),
                      "owners": e.get("owners", "")} for e in entities.list_entities(root, kind)]
    out["greetings"] = [{"id": g["id"], "name": g["name"], "character": g["character"],
                         "requires_tags": g["requires_tags"]}
                        for g in greetings.list_greetings(root)]
    out["plotmap"] = greetings.read_plotmap(root)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _print({"ok": False, "problems": [f"plan: cannot read {args.plan} as JSON ({exc})"]})
        return 1
    try:
        if args.dry_run:
            if not isinstance(plan, dict):
                raise PlanError(["the plan must be a JSON object"])
            wid = _resolve_world(plan, args.world_id)
            root = worlds.world_root(wid) if wid else None
            problems = validate_plan(plan, root, wid)
            _print({"world": wid or f"(new) {plan.get('world', '')}", "ok": not problems,
                    "problems": problems})
            return 1 if problems else 0
        _print(apply_plan(plan, args.world_id))
    except PlanError as exc:
        _print({"ok": False, "problems": exc.problems})
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if not worlds.world_exists(args.world):
        print(f"no world with id {args.world!r}", file=sys.stderr)
        return 2
    result = check_world(args.world)
    _print(result)
    return 0 if result["ok"] else 1


def cmd_summary(args: argparse.Namespace) -> int:
    if not worlds.world_exists(args.world):
        print(f"no world with id {args.world!r}", file=sys.stderr)
        return 2
    _print(summary(args.world))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply", help="create or update a world from a JSON plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--world-id", help="target this existing world instead of matching by name")
    p.add_argument("--dry-run", action="store_true", help="validate the plan; write nothing")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("check", help="referential integrity and playability of a world")
    p.add_argument("--world", required=True)
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("summary", help="a world's records by kind, with ids")
    p.add_argument("--world", required=True)
    p.set_defaults(func=cmd_summary)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
