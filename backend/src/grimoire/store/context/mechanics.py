"""The module-driven prompt sections (#162): activated rules docs, compact
character-sheet summaries, and the available-checks table.

All of it is empty unless a module pack resolves for the campaign, which is why
this is the one context file that takes the campaign lock: a half-published
pack must not be read mid-swap.
"""

from __future__ import annotations

import re

from .. import checks, entities, locks, overlay
from ..modules import (binding as modules_binding, content as modules_content,
                       fields as modules_fields, pack as modules_pack)
from ..scenes import read as scenes_read
from ..sheets import reader as sheets_reader, schema as sheets_schema


def _sheet_type_label(sheets_def: dict, type_id) -> str:
    st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
    if isinstance(st, dict) and st.get("label"):
        return st["label"]
    return type_id if isinstance(type_id, str) else ""


def _sheet_summary_lines(sheets_def: dict, sheet: dict) -> list[str]:
    """key value entries (resources as key cur/max) for a sheet's assembled
    fields, then its derived values, chunked into ~4-entry lines."""
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
    for name, value in sheet["derived"].items():
        entries.append(f"{name} {value}")
    return [" · ".join(entries[i:i + 4]) for i in range(0, len(entries), 4)]


def _rule_keys_match(keys: list[str], recent_text: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys)


def _mechanics(cid: str, sid: str, cast, recent_text: str) -> dict:
    """Module-driven prompt data (#162): activated rules docs (frontmatter
    always -> sheet_types -> keys, keys capped at 6), compact sheet summaries
    for sheeted cast + the current location, and the available-checks table.
    All empty when no module resolves (modules.resolve)."""
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            return {"mechanics_rules": [], "mechanics_sheets": [], "mechanics_checks": []}
        pack = modules_pack.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}

        history_ids = scenes_read.get_location_history(cid, sid)
        current_loc = history_ids[-1] if history_ids else None
        actors = [(a["kind"], a["id"], a["name"]) for a in cast]
        if current_loc:
            try:
                loc_name = overlay.read_entity(cid, "locations", current_loc)["meta"].get("name", current_loc)
                actors.append(("locations", current_loc, loc_name))
            except entities.EntityNotFound:
                pass  # referenced location was deleted — omit from sheet summaries

        mechanics_sheets: list[dict] = []
        present_types: set[str] = set()
        for kind, eid, label in actors:
            sheet = sheets_reader.read(cid, kind, eid)
            if sheet is None:
                continue
            type_id = sheet["sheet_type"]
            entry = {"ref": f"{kind}:{eid}", "label": label,
                     "type_label": _sheet_type_label(sheets_def, type_id)}
            if sheet["errors"]:
                entry["lines"] = ["(sheet invalid)"]
            else:
                if isinstance(type_id, str):
                    present_types.add(type_id)
                entry["lines"] = _sheet_summary_lines(sheets_def, sheet)
            mechanics_sheets.append(entry)

        always_docs, type_docs, key_docs = [], [], []
        for doc in pack["rules"]:
            if doc["always"]:
                always_docs.append(doc)
            elif set(doc["sheet_types"]) & present_types:
                type_docs.append(doc)
            elif doc["keys"] and _rule_keys_match(doc["keys"], recent_text):
                key_docs.append(doc)
        mechanics_rules: list[str] = []
        for doc in always_docs + type_docs + key_docs[:6]:
            rule = modules_content.read_rule(mid, doc["id"])
            if rule is not None:
                mechanics_rules.append(rule["body"].strip())

        return {"mechanics_rules": mechanics_rules, "mechanics_sheets": mechanics_sheets,
                "mechanics_checks": checks.available_checks(cid, sid)}
