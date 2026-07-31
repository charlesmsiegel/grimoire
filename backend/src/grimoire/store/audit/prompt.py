"""Part 2a: the audit prompt -- sheet scope, rendered sheet blocks, roll lines.

This is the only part of the package that reads scene state
(``scenes/read.py``'s ``get_location_history``), which is exactly why it lives apart from
``baselines``: ``scenes`` calls into ``baselines``, never into here.
"""

from __future__ import annotations

from ... import prompts
from .. import entities, overlay, rolls
from ..appearances import cast as appearances_cast
from ..modules import (binding as modules_binding, fields as modules_fields,
                       pack as modules_pack)
from ..scenes import read as scenes_read
from ..sheets import reader as sheets_reader, schema as sheets_schema
from . import baselines


def sheet_scope(cid: str, sid: str) -> list[tuple[str, str, str]]:
    """(kind, eid, name) for present cast + the current location -- the same
    scope Phase 4's mechanics_sheets context section uses (context._mechanics).
    Unsheeted entries are included; callers decide what to do with them."""
    out = [(a["kind"], a["id"], a.get("name", a["id"]))
           for a in appearances_cast.scene_cast(cid, sid)]
    history = scenes_read.get_location_history(cid, sid)
    if history:
        loc = history[-1]
        try:
            name = overlay.read_entity(cid, "locations", loc)["meta"].get("name", loc)
            out.append(("locations", loc, name))
        except entities.EntityNotFound:
            pass
    return out


def _field_label(fdef: dict) -> str:
    return fdef.get("label") or fdef.get("key", "")


def render_value(fdef: dict, value) -> str:
    key = fdef.get("key", "")
    if fdef.get("type") == "resource" and isinstance(value, dict):
        return f"{key} {value.get('current', 0)}/{value.get('max', 0)}"
    if fdef.get("type") == "list" and isinstance(value, list):
        return f"{key}:\n" + "\n".join(f"- {v}" for v in value) if value else f"{key}: (empty)"
    return f"{key} {value}"


def sheet_blocks(cid: str, sid: str) -> tuple[list[str], list[dict]]:
    mid = modules_binding.resolve(cid)
    if mid is None:
        return [], []
    sheets_def = modules_pack.load_pack(mid)["sheets"]
    blocks, excluded = [], []
    for kind, eid, name in sheet_scope(cid, sid):
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None:
            continue                                   # unsheeted: not in scope
        ref = f"{kind}:{eid}"
        if sheet["errors"]:
            excluded.append({"id": ref,
                             "reason": "sheet invalid: " + "; ".join(sheet["errors"])})
            continue
        type_id = sheet["sheet_type"]
        merged = {**sheets_schema.default_fields(sheets_def, type_id), **sheet["fields"]}
        lines = [f"{ref} — {type_id} ({name})"]
        for f in modules_fields.assembled_fields(sheets_def, type_id):
            key = f.get("key")
            if not isinstance(key, str) or key not in merged:
                continue
            if f.get("type") in sheets_schema.MUTABLE_TYPES:
                start = baselines.baseline_field(cid, sid, kind, eid, key)
                if start is None:
                    lines.append(f"  {render_value(f, merged[key])}  "
                                 "[mutable — no scene baseline, report only]")
                else:
                    lines.append(f"  start {render_value(f, start)} -> now "
                                 f"{render_value(f, merged[key])}  [mutable]")
            else:
                # FULL blocks: text fields included, marked static, so
                # contradictions involving text-valued mechanics stay visible
                lines.append(f"  {render_value(f, merged[key])}  [static]")
        blocks.append("\n".join(lines))
    return blocks, excluded


def roll_lines(cid: str, sid: str) -> list[str]:
    out = []
    for entry in rolls.read(cid):
        if entry.get("scene") != sid:
            continue
        r = entry.get("result", {})
        bits = [entry.get("label") or r.get("notation", ""), str(r.get("notation", ""))]
        if "successes" in r:
            bits.append(f"{r['successes']} successes")
        elif "total" in r:
            bits.append(f"total {r['total']}")
        tier = entry.get("tier")          # sibling of `result` -- see rolls.append
        if isinstance(tier, str) and tier:
            bits.append(tier)
        out.append("- " + " · ".join(b for b in bits if b))
    return out


def build_prompt(transcript: str, blocks: list[str], roll_lines_: list[str]) -> list[dict]:
    return [{"role": "system", "content": prompts.render("audit/system.j2")},
            {"role": "user", "content": prompts.render(
                "audit/user.j2", sheet_blocks=blocks, roll_lines=roll_lines_,
                transcript=transcript)}]
