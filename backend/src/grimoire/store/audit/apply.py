"""Part 2b: parsing the audit reply, and the deterministic gate that turns it
into StagedEdits (``materialize``) or applies one under lock (``apply_delta``)."""

from __future__ import annotations

import json

from .. import locks
from ..modules import (binding as modules_binding, fields as modules_fields,
                       pack as modules_pack, validate as modules_validate)
from ..sheets import (paths as sheets_paths, reader as sheets_reader,
                      schema as sheets_schema, writer as sheets_writer)
from . import baselines, prompt


class AuditParseError(Exception):
    """The audit reply violated the output schema (fail closed, not clean)."""


def parse_output(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise AuditParseError("no JSON object in the audit reply")
    if not isinstance(obj, dict):
        raise AuditParseError("audit reply is not a JSON object")
    if not isinstance(obj.get("warnings"), list) or not isinstance(obj.get("sheet_deltas"), list):
        raise AuditParseError(
            "audit reply must carry 'warnings' and 'sheet_deltas' arrays")
    warnings, deltas, dropped = [], [], []
    for w in obj["warnings"]:
        if isinstance(w, str) and w.strip():
            warnings.append(w.strip())
        else:
            dropped.append({"id": "", "reason": f"malformed warning: {w!r}"})
    for d in obj["sheet_deltas"]:
        if isinstance(d, dict) and isinstance(d.get("id"), str) and isinstance(d.get("field"), str):
            deltas.append({"id": d["id"].strip(), "field": d["field"].strip(),
                           "value": d.get("value"),
                           "note": str(d.get("note") or "").strip()})
        else:
            dropped.append({"id": "", "reason": f"malformed delta: {d!r}"})
    return {"warnings": warnings, "sheet_deltas": deltas, "dropped": dropped}


def apply_delta(cid: str, sid: str, edit: dict) -> None:
    """Apply one StagedEdit of kind "sheet". One critical section: authorize
    (scope + baseline, both re-checked against the in-lock live sheet) then
    write (set_field's body) -- never two lock acquisitions, so a concurrent
    write can't land between the check and the write. The module is resolved
    here, inside the lock, and that same mid is threaded through to
    set_field_locked; sheets.read still re-resolves it internally too, but
    rebinds only ever publish under this same lock (see sheets.write's
    rebind-serialization note), so both resolutions are guaranteed to agree.
    Raises sheets.SheetConflict / sheets.SheetError; never returns a failure
    value -- callers (absorb.apply_edits) catch and report."""
    payload = edit.get("payload", {})
    target = edit.get("target", {})
    kind, eid = target.get("kind"), target.get("id")
    field_key = payload.get("field")
    if not (isinstance(kind, str) and isinstance(eid, str) and isinstance(field_key, str)):
        raise sheets_paths.SheetError("malformed sheet edit")
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)               # once, inside the lock
        if mid is None:
            raise sheets_paths.SheetError("no module resolved for this campaign")
        if (kind, eid) not in {(k, e) for k, e, _ in prompt.sheet_scope(cid, sid)}:
            raise sheets_paths.SheetError("entity not in this scene's sheet scope")
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None or sheet["errors"]:
            raise sheets_paths.SheetError("entity has no readable sheet")
        if not baselines.baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
            raise sheets_paths.SheetError("no valid scene baseline for this entity")
        sheets_writer.set_field_locked(mid, cid, kind, eid, field_key,
                                       payload.get("value"), payload.get("expect"))


def materialize(cid: str, sid: str, parsed: dict) -> tuple[list[dict], list[dict]]:
    """Deterministic gate over parsed sheet_deltas -> (StagedEdits, dropped).
    Mirrored inside the apply lock by apply_delta; set_field is the boundary."""
    mid = modules_binding.resolve(cid)
    edits: list[dict] = []
    dropped: list[dict] = list(parsed.get("dropped", []))
    if mid is None:
        return edits, dropped
    sheets_def = modules_pack.load_pack(mid)["sheets"]
    scope = {(k, e): name for k, e, name in prompt.sheet_scope(cid, sid)}
    for d in parsed.get("sheet_deltas", []):
        ref, field_key = d["id"], d["field"]
        kind, sep, eid = ref.partition(":")
        drop = lambda why: dropped.append({"id": ref, "field": field_key, "reason": why})
        if not sep or (kind, eid) not in scope:
            drop("entity not in this scene's sheet scope"); continue
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None or sheet["errors"]:
            drop("entity has no readable sheet"); continue
        if baselines.baseline_field(cid, sid, kind, eid, field_key) is None and \
                not baselines.baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
            drop("no valid scene baseline for this entity"); continue
        fdefs = {f["key"]: f for f in modules_fields.assembled_fields(sheets_def,
                                                                     sheet["sheet_type"])
                 if isinstance(f, dict) and isinstance(f.get("key"), str)}
        fdef = fdefs.get(field_key)
        if fdef is None or fdef.get("type") not in sheets_schema.MUTABLE_TYPES:
            drop("not a mutable field of this sheet"); continue
        merged = {**sheets_schema.default_fields(sheets_def, sheet["sheet_type"]),
                  **sheet["fields"]}
        live = merged.get(field_key)
        try:
            value = sheets_schema.canonical_field_value(fdef, d["value"], live)
        except sheets_paths.SheetError as e:
            drop(str(e)); continue
        errs = modules_validate.validate_sheet_values(
            sheets_def, sheet["sheet_type"], {**sheet["fields"], field_key: value})
        if errs:
            drop("; ".join(errs)); continue
        expect = sheets_schema.canonical_field_value(fdef, live, live)
        if value == expect:
            continue                                     # benign no-op: agreement
        name = scope[(kind, eid)]
        edits.append({"id": f"sheet:{kind}:{eid}:{field_key}", "kind": "sheet",
                      "target": {"kind": kind, "id": eid},
                      "label": f"{name} — {prompt._field_label(fdef)} (sheet)",
                      "field": field_key,
                      "before": prompt.render_value(fdef, expect),
                      "after": prompt.render_value(fdef, value),
                      "authored": False,
                      "payload": {"field": field_key, "value": value,
                                  "expect": expect, "note": d.get("note", "")}})
    return edits, dropped
