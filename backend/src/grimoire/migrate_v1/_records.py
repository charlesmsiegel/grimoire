"""Worlds, flat records, greetings, characters and PCs, read as V0.2.1 reads them."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import _text
from ._context import Context

WORLD_KEYS = frozenset({"name", "created", "updated", "genre", "tone", "themes", "module"})
COMMON_RECORD_KEYS = frozenset({"name", "keys", "owners", "secrecy", "sd_prompt"})
SCHEMA_KEYS = {
    "locations": {"climate", "persistence", "weather_zone"},
    "items": {"item_type", "rarity", "holder"},
    "groups": {"group_type", "leader", "members", "headquarters"},
    "creatures": {"creature_type", "threat", "habitat"},
    "lore": set(),
}
GREETING_KEYS = frozenset({"name", "character", "version", "present", "location", "requires_tags",
                           "predecessor_join", "pcless", "phase", "sequence", "optional"})
#: Reference fields: which kinds each may name (V1 entity_schema), and whether it is a list.
REF_FIELDS = {
    "holder": (("characters", "pcs", "groups", "locations"), False),
    "leader": (("characters", "pcs"), False),
    "members": (("characters", "pcs"), True),
    "headquarters": (("locations",), False),
    "habitat": (("locations",), True),
    "owners": (None, True),
}
ACTOR_META_KEYS = {"characters": frozenset({"name", "default_version", "birthdate", "tags"}),
                   "pcs": frozenset({"name", "tags", "default_version"})}
PERSONA_KEYS = frozenset({"name", "pronouns", "summary", "birthdate", "goals", "player_notes"})
CARD_TOP_KEYS = frozenset({"spec", "spec_version", "data"})
CARD_DATA_KEYS = frozenset({
    "name", "description", "tags", "creator", "character_version", "mes_example", "extensions",
    "system_prompt", "post_history_instructions", "first_mes", "alternate_greetings", "personality",
    "scenario", "creator_notes", "character_book", "assets", "nickname", "creator_notes_multilingual",
    "source", "group_only_greetings", "creation_date", "modification_date"})
#: Card text V1 renders as Markdown, and so may carry image references.
CARD_TEXT_FIELDS = ("description", "first_mes")


@dataclass
class Version:
    id: str
    path: str
    v1_hash: str | None = None
    display_name: str | None = None
    label: str | None = None
    malformed: bool = False
    text: str | None = None
    texts: list[str] = field(default_factory=list)


@dataclass
class Actor:
    scope: str
    scope_id: str
    base: str
    id: str
    path: str
    meta_path: str | None = None
    meta_text: str | None = None
    name: str | None = None
    default_version: str | None = None
    versions: dict[str, Version] = field(default_factory=dict)
    sidecars: list[str] = field(default_factory=list)


@dataclass
class Record:
    scope: str
    scope_id: str
    kind: str
    id: str
    path: str
    name: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""
    v1_hash: str | None = None


@dataclass
class Library:
    """What the source holds, by V1 coordinates. Built once; read by later interpreters."""
    worlds: dict[str, dict] = field(default_factory=dict)
    records: dict[tuple, Record] = field(default_factory=dict)
    actors: dict[tuple, Actor] = field(default_factory=dict)
    markers: dict[str, str] = field(default_factory=dict)

    def scope_root(self, scope: str, scope_id: str) -> str:
        return f"{'worlds' if scope == 'world' else 'campaigns'}/{scope_id}"


def _unknown(ctx: Context, path: str, meta: dict, known: frozenset | set) -> list[str]:
    extra = sorted(k for k in meta if k not in known)
    if extra:
        ctx.finding("unknown_fields", path, detail=",".join(extra))
    return extra


def read_config(ctx: Context, lib: Library) -> dict:
    """Format markers only: no other value of config.md is ever copied out."""
    for entry in ctx.snap.files("config"):
        text = ctx.read_text(entry)
        meta = _text.parse_frontmatter(text)[0] if text is not None else {}
        for key in ("inference_format", "inference_retired"):
            if key in meta:
                lib.markers[key] = meta[key]
        if _newer(meta.get("inference_format")):
            ctx.finding("newer_format", entry.path, detail=f"inference_format {meta['inference_format']}")
    return dict(lib.markers)


def _newer(value: str | None) -> bool:
    return value is not None and value.isdigit() and int(value) > 2


def read_worlds(ctx: Context, lib: Library) -> list[dict]:
    out = []
    for entry in ctx.snap.files("world_meta"):
        wid = entry.layout.coords["scope_id"]
        text = ctx.read_text(entry)
        meta = _text.parse_frontmatter(text)[0] if text is not None else {}
        if not _text.safe_id(wid):
            ctx.finding("invalid_v1_id", entry.path, detail="world")
        world = {"id": wid, "path": entry.path, "name": meta.get("name"), "readable": text is not None,
                 "v1_listed": _text.safe_id(wid), "unknown_fields": _unknown(ctx, entry.path, meta, WORLD_KEYS)}
        lib.worlds[wid] = world
        out.append(world)
    return out


def read_flat_records(ctx: Context, lib: Library) -> None:
    for entry in ctx.snap.files("flat_record"):
        c = entry.layout.coords
        text = ctx.read_text(entry)
        meta, body = _text.parse_frontmatter(text) if text is not None else ({}, "")
        record = Record(c["scope"], c["scope_id"], c["kind"], c["record"], entry.path,
                        meta.get("name") or c["record"], meta, body,
                        _text.v1_hash(text) if text is not None else None)
        if not _text.safe_id(record.id):
            ctx.finding("invalid_v1_id", entry.path, detail=record.kind)
        lib.records[(record.scope, record.scope_id, record.kind, record.id)] = record


def read_actors(ctx: Context, lib: Library) -> None:
    for entry in ctx.snap.files("actor_meta"):
        actor = _actor(lib, entry.layout.coords)
        actor.meta_path = entry.path
        text = ctx.read_text(entry)
        actor.meta_text = text
        meta = _text.parse_frontmatter(text)[0] if text is not None else {}
        actor.name = meta.get("name") or actor.id
        actor.default_version = meta.get("default_version") or None
        _unknown(ctx, entry.path, meta, ACTOR_META_KEYS[actor.base])
    for entry in ctx.snap.files("card"):
        actor = _actor(lib, entry.layout.coords)
        actor.versions[entry.layout.coords["version"]] = _card(ctx, entry, actor)
    for entry in ctx.snap.files("persona"):
        actor = _actor(lib, entry.layout.coords)
        actor.versions[entry.layout.coords["version"]] = _persona(ctx, entry, actor)
    for entry in ctx.snap.files("actor_sidecar"):
        actor = _actor(lib, entry.layout.coords)
        ctx.read(entry)  # presence and digest; the text itself travels with the source
        actor.sidecars.append(entry.layout.coords["sidecar"])
    for actor in lib.actors.values():
        if not _text.safe_id(actor.id):
            ctx.finding("invalid_v1_id", actor.path, detail=actor.base)
        if actor.meta_path is None:
            ctx.finding("unknown_fields", actor.path, detail="actor directory without its metadata file")
        listed = sorted(v for v in actor.versions if _text.safe_id(v))
        if not listed:
            ctx.finding("no_versions", actor.path)
        elif actor.default_version not in listed:
            ctx.finding("missing_default_version", actor.path,
                        detail=f"V1 would use {listed[0]!r}")


def _actor(lib: Library, coords: dict) -> Actor:
    key = (coords["scope"], coords["scope_id"], coords["base"], coords["actor"])
    actor = lib.actors.get(key)
    if actor is None:
        actor = Actor(*key, path=f"{lib.scope_root(coords['scope'], coords['scope_id'])}/"
                                 f"{coords['base']}/{coords['actor']}")
        lib.actors[key] = actor
    return actor


def _card(ctx: Context, entry, actor: Actor) -> Version:
    version = Version(entry.layout.coords["version"], entry.path)
    text = ctx.read_text(entry)
    if text is None:
        version.malformed = True
        return version
    version.v1_hash, version.text = _text.v1_hash(text), text
    card, problem = _text.load_json(text.encode("utf-8"))
    if problem is not None or not isinstance(card, dict):
        ctx.finding("malformed_card", entry.path, detail="not a JSON object")
        version.malformed = True
        return version
    spec = card.get("spec")
    if spec != "chara_card_v3":
        ctx.finding("card_spec", entry.path, detail=str(spec)[:40])
    data = card.get("data")
    unknown = sorted(k for k in card if k not in CARD_TOP_KEYS)
    if not isinstance(data, dict):
        ctx.finding("malformed_card", entry.path, detail="data is not an object")
        version.malformed = True
        data = {}
    unknown += sorted(f"data.{k}" for k in data if k not in CARD_DATA_KEYS)
    extensions = data.get("extensions")
    if isinstance(extensions, dict):
        unknown += sorted(f"data.extensions.{k}" for k in extensions
                          if k not in ("grimoire_label", "chub_source"))
    if unknown:
        ctx.finding("unknown_fields", entry.path, detail=",".join(unknown))
    name = data.get("name")
    version.display_name = (name if isinstance(name, str) and name else None) or actor.id
    label = extensions.get("grimoire_label") if isinstance(extensions, dict) else None
    fallback = data.get("character_version")
    version.label = next((v for v in (label, fallback) if isinstance(v, str) and v), version.id)
    version.texts = [data[k] for k in CARD_TEXT_FIELDS if isinstance(data.get(k), str)]
    greetings = data.get("alternate_greetings")
    if isinstance(greetings, list):
        version.texts += [g for g in greetings if isinstance(g, str)]
    return version


def _persona(ctx: Context, entry, actor: Actor) -> Version:
    version = Version(entry.layout.coords["version"], entry.path)
    text = ctx.read_text(entry)
    if text is None:
        version.malformed = True
        return version
    version.v1_hash, version.text = _text.v1_hash(text), text
    meta, body = _text.parse_frontmatter(text)
    _unknown(ctx, entry.path, meta, PERSONA_KEYS)
    version.display_name = meta.get("name") or actor.id
    version.label = version.id
    version.texts = [body]
    return version


def resolve_ref(lib: Library, scope: str, scope_id: str, world: str | None, kind: str, rid: str) -> bool:
    """Whether `<kind>:<rid>` names a record visible from this scope (campaigns see their world)."""
    scopes = [(scope, scope_id)] + ([("world", world)] if scope == "campaign" and world else [])
    for s, sid in scopes:
        if kind in ("characters", "pcs"):
            actor = lib.actors.get((s, sid, kind, rid))
            if actor is not None and actor.meta_path is not None:
                return True
        elif (s, sid, kind, rid) in lib.records:
            return True
    return False


def record_rows(ctx: Context, lib: Library, world_of: dict[str, str | None]) -> list[dict]:
    """Report rows for flat records, with reference fields resolved (never rebound by name)."""
    rows = []
    for key in sorted(lib.records):
        record = lib.records[key]
        world = world_of.get(record.scope_id) if record.scope == "campaign" else None
        if record.kind == "greetings":
            known = GREETING_KEYS
        else:
            known = COMMON_RECORD_KEYS | SCHEMA_KEYS.get(record.kind, set())
        refs = []
        for field_name, (kinds, many) in REF_FIELDS.items():
            if record.kind == "greetings" or field_name not in record.meta:
                continue
            values = _text.split_list(record.meta[field_name]) if many else [record.meta[field_name]]
            for value in values:
                refs.append(_ref_row(ctx, lib, record, world, field_name, value, kinds))
        if record.kind == "greetings":
            for field_name, kind in (("character", "characters"), ("location", "locations")):
                if record.meta.get(field_name):
                    refs.append(_ref_row(ctx, lib, record, world, field_name,
                                         f"{kind}:{record.meta[field_name]}", (kind,)))
            for member in _text.split_list(record.meta.get("present", "")):
                refs.append(_ref_row(ctx, lib, record, world, "present", f"characters:{member}",
                                     ("characters",)))
        rows.append({"scope": record.scope, "scope_id": record.scope_id, "kind": record.kind,
                     "id": record.id, "path": record.path, "name": record.name,
                     "v1_listed": _text.safe_id(record.id), "refs": refs,
                     "unknown_fields": _unknown(ctx, record.path, record.meta, known)})
    return rows


def _ref_row(ctx, lib, record, world, field_name, value, kinds) -> dict:
    kind, sep, rid = value.partition(":")
    if not sep or not rid or (kinds is not None and kind not in kinds):
        status = "malformed"
    else:
        status = "resolved" if resolve_ref(lib, record.scope, record.scope_id, world, kind, rid) else "dangling"
    if status != "resolved":
        ctx.finding("dangling_ref", record.path, detail=f"{field_name}={value}")
    return {"field": field_name, "target": value, "status": status}


def actor_rows(lib: Library) -> list[dict]:
    rows = []
    for key in sorted(lib.actors):
        actor = lib.actors[key]
        rows.append({
            "scope": actor.scope, "scope_id": actor.scope_id, "base": actor.base, "id": actor.id,
            "path": actor.path, "meta_path": actor.meta_path, "name": actor.name,
            "default_version": actor.default_version, "v1_listed": _text.safe_id(actor.id),
            "sidecars": sorted(actor.sidecars),
            "versions": [{"id": v.id, "path": v.path, "label": v.label, "display_name": v.display_name,
                          "v1_hash": v.v1_hash, "malformed": v.malformed}
                         for _, v in sorted(actor.versions.items())],
        })
    return rows
