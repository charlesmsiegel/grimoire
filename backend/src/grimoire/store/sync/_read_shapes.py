"""sync: read shapes responsibilities."""

from __future__ import annotations

from pathlib import Path

from .. import characters, entities, entity_schema, greetings, pcs
from .. import frontmatter as _store_frontmatter


def _ref_str(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    if kind == "greetings":
        g = greetings.read_greeting(root, eid)
        return {"name": g["meta"].get("name", eid), "body": g["body"]}
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"], "fields": [
        {"key": field["key"], "label": field["label"], "value": e["meta"][field["key"]],
         "shown": e["meta"][field["key"]]}
        for field in entity_schema.FIELDS.get(kind, ()) if field["key"] in e["meta"]]}


def _align_entity_fields(world: dict, mine: dict, kind: str) -> None:
    left = {row["key"]: row for row in world.get("fields", ())}
    right = {row["key"]: row for row in mine.get("fields", ())}
    fields = [field for field in entity_schema.FIELDS.get(kind, ())
              if field["key"] in left or field["key"] in right]
    for blob, values in ((world, left), (mine, right)):
        blob["fields"] = [values.get(field["key"], {
            "key": field["key"], "label": field["label"], "value": "", "shown": ""}) for field in fields]


def _plotmap_blob(root: Path) -> dict:
    p = root / "plotmap.json"
    return {"name": "Plot map", "body": p.read_text(encoding="utf-8") if p.exists() else ""}


def _actor_blob(root: Path, kind: str, actor_id: str, vid: str) -> dict:
    if kind == "characters":
        card = characters.read_card(root, actor_id, vid)
        return {"name": card["data"].get("name", actor_id), "version": vid, "card": card}
    persona = pcs.read_persona(root, actor_id, vid)
    return {"name": persona.get("name", actor_id), "version": vid, "persona": persona}


def _dir_hash(root: Path, kind: str, actor_id: str) -> str | None:
    return characters.dir_hash(root, actor_id) if kind == "characters" else pcs.dir_hash(root, actor_id)


def _actor_summary_blob(root: Path, kind: str, actor_id: str) -> dict:
    detail = (characters.read_character(root, actor_id) if kind == "characters"
              else pcs.read_pc(root, actor_id))
    versions = ", ".join(v["id"] for v in detail["versions"])
    return {"name": detail["meta"].get("name", actor_id), "body": f"versions: {versions}"}


def _actor_name_or_id(root: Path, kind: str, actor_id: str) -> str:
    """Display name off the container meta, or the id when it cannot be read.

    Fallback rather than raise, for `appearances.cast._actor_name`'s reason:
    this runs over every actor the campaign holds, and one malformed record
    must not take out the whole overview.
    """
    try:
        detail = (characters.read_character(root, actor_id) if kind == "characters"
                  else pcs.read_pc(root, actor_id))
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return actor_id
    return str(detail["meta"].get("name", actor_id)) or actor_id


def _blob_name(text: str, eid: str) -> str:
    meta, _ = _store_frontmatter.parse_frontmatter(text)
    return str(meta.get("name", eid)) or eid
