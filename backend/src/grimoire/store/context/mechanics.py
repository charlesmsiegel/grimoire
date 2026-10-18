"""The module-driven prompt sections (#162): activated rules docs, compact
character-sheet summaries, and the available-checks table.

All of it is empty unless a module pack resolves for the campaign, which is why
this is the one context file that takes the campaign lock: a half-published
pack must not be read mid-swap.
"""

from __future__ import annotations

import re

from .. import checks, entities, locks, overlay
from ..modules import binding as modules_binding
from ..modules import content as modules_content
from ..modules import fields as modules_fields
from ..modules import pack as modules_pack
from ..scenes import read as scenes_read
from ..sheets import reader as sheets_reader
from ..sheets import schema as sheets_schema

#: Entries per sheet-summary line: a line is meant to be taken in at a
#: glance, and a sheet with many fields wraps onto more lines rather than
#: one long one.
SUMMARY_ENTRIES_PER_LINE = 4

#: How many rules docs keyword activation may add. Always-on and sheet-type
#: docs are bounded by the pack itself; keyword matches grow with whatever
#: the conversation happens to mention, so they are the ones capped.
KEYED_RULES_CAP = 6


def _sheet_type_label(sheets_def: dict, type_id) -> str:
    st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
    if isinstance(st, dict) and st.get("label"):
        return st["label"]
    return type_id if isinstance(type_id, str) else ""


def _sheet_summary_lines(sheets_def: dict, sheet: dict) -> list[str]:
    """key value entries (resources as key cur/max) for a sheet's assembled
    fields, then its derived values, chunked into lines of
    `SUMMARY_ENTRIES_PER_LINE`."""
    type_id = sheet["sheet_type"]
    merged = ({**sheets_schema.default_fields(sheets_def, type_id), **sheet["fields"]}
              if isinstance(type_id, str) else {})
    entries: list[str] = []
    for f in (modules_fields.assembled_fields(sheets_def, type_id) if isinstance(type_id, str) else []):
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        v = merged.get(key)
        if f.get("type") == "resource" and isinstance(v, dict):
            entries.append(f"{key} {v.get('current')}/{v.get('max')}")
        else:
            entries.append(f"{key} {v}")
    entries.extend(f"{name} {value}" for name, value in sheet["derived"].items())
    step = SUMMARY_ENTRIES_PER_LINE
    return [" · ".join(entries[i:i + step]) for i in range(0, len(entries), step)]


def _rule_keys_match(keys: list[str], recent_text: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys)


def _mechanics(cid: str, sid: str, cast, recent_text: str) -> dict:
    """Module-driven prompt data (#162): activated rules docs (frontmatter
    always -> sheet_types -> keys, keys capped at `KEYED_RULES_CAP`), compact
    sheet summaries for sheeted cast + the current location, and the
    available-checks table. All empty when no module resolves
    (modules.resolve)."""
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            return {"mechanics_rules": [], "mechanics_sheets": [], "mechanics_checks": []}
        pack = modules_pack.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
        mechanics_sheets, present_types = _sheets(cid, _sheet_holders(cid, sid, cast), sheets_def)
        return {"mechanics_rules": _rules(mid, pack["rules"], present_types, recent_text),
                "mechanics_sheets": mechanics_sheets,
                "mechanics_checks": checks.available_checks(cid, sid)}


def _sheet_holders(cid: str, sid: str, cast) -> list[tuple[str, str, str]]:
    """`(kind, id, label)` for everything whose sheet the prompt may show:
    the cast, then the scene's current location."""
    history_ids = scenes_read.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    actors = [(a["kind"], a["id"], a["name"]) for a in cast]
    if current_loc:
        try:
            loc_name = overlay.read_entity(cid, "locations", current_loc)["meta"].get("name", current_loc)
            actors.append(("locations", current_loc, loc_name))
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit from sheet summaries
    return actors


def _sheets(cid: str, holders: list[tuple[str, str, str]],
            sheets_def: dict) -> tuple[list[dict], set[str]]:
    """Each holder's sheet summarised, in order, and the sheet types present
    among the valid ones. A holder with no sheet is left out; an invalid
    sheet says so rather than summarising."""
    mechanics_sheets: list[dict] = []
    present_types: set[str] = set()
    for kind, eid, label in holders:
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None:
            continue
        type_id = sheet["sheet_type"]
        entry: dict[str, object] = {"ref": f"{kind}:{eid}", "label": label,
                 "type_label": _sheet_type_label(sheets_def, type_id)}
        if sheet["errors"]:
            entry["lines"] = ["(sheet invalid)"]
        else:
            if isinstance(type_id, str):
                present_types.add(type_id)
            entry["lines"] = _sheet_summary_lines(sheets_def, sheet)
        mechanics_sheets.append(entry)
    return mechanics_sheets, present_types


def _rules(mid: str, docs: list[dict], present_types: set[str], recent_text: str) -> list[str]:
    """The bodies of the rules docs this turn activates, in their order of
    precedence: every always-on doc, then those for a sheet type present,
    then those whose keys `recent_text` mentions, at most `KEYED_RULES_CAP`.
    A doc the pack lists but cannot read is skipped."""
    always_docs, type_docs, key_docs = [], [], []
    for doc in docs:
        if doc["always"]:
            always_docs.append(doc)
        elif set(doc["sheet_types"]) & present_types:
            type_docs.append(doc)
        elif doc["keys"] and _rule_keys_match(doc["keys"], recent_text):
            key_docs.append(doc)
    mechanics_rules: list[str] = []
    for doc in always_docs + type_docs + key_docs[:KEYED_RULES_CAP]:
        rule = modules_content.read_rule(mid, doc["id"])
        if rule is not None:
            mechanics_rules.append(rule["body"].strip())
    return mechanics_rules
