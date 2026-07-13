"""Narrated-event validation (mechanics Phase 5, roadmap #826).

Part 1: scene-start sheet baselines at <campaign>/sheet_baselines.json --
{"<sid>": {"module", "schema": {"hash","mtime"}, "sheets": {"kind--eid":
{"sheet_type","gen","fields"}}}}. Validity = module id + schema stamp +
per-sheet gen + type; no cross-store invalidation hooks (gen self-invalidates).
Lock ordering: sheet lock -> baseline lock, never reversed.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from . import campaigns, modules, sheets

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(cid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.Lock())


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "sheet_baselines.json"


def read_baselines(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    p = _path(cid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def schema_stamp(mid: str) -> dict:
    """Content hash + sheets.json mtime: an in-place pack edit changes the
    hash; an A->B->A reversion restores the hash but not the mtime."""
    sheets_def = modules.load_pack(mid)["sheets"]
    digest = hashlib.sha256(
        json.dumps(sheets_def, sort_keys=True).encode("utf-8")).hexdigest()
    try:
        mtime = (modules.pack_root(mid)[0] / "sheets.json").stat().st_mtime_ns
    except OSError:
        mtime = 0
    return {"hash": digest, "mtime": mtime}


def capture_baseline(cid: str, sid: str) -> None:
    """Snapshot every campaign sheet at scene creation. Never raises -- a
    capture failure must not fail scene creation."""
    try:
        mid = modules.resolve(cid)
        if mid is None:
            return
        with sheets.lock_for(cid):          # consistent multi-file snapshot
            snap: dict = {}
            croot = campaigns.campaign_root(cid)
            for kind, eid in sheets.list_refs(cid):
                try:
                    raw = json.loads((croot / "sheets" / f"{kind}--{eid}.json")
                                     .read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(raw, dict):
                    snap[f"{kind}--{eid}"] = {
                        "sheet_type": raw.get("sheet_type"), "gen": raw.get("gen"),
                        "fields": raw.get("fields") if isinstance(raw.get("fields"), dict) else {}}
            entry = {"module": mid, "schema": schema_stamp(mid), "sheets": snap}
            with _lock(cid):                # sheet lock -> baseline lock
                data = read_baselines(cid)
                data[sid] = entry
                _write(cid, data)
    except Exception:  # noqa: BLE001 — never fail the caller
        return


def baseline_entry_valid(cid: str, sid: str, kind: str, eid: str,
                         mid: str, sheet: dict) -> bool:
    """Shared validity predicate: scene entry exists, module + schema stamp
    match, entity entry exists, and its sheet_type AND gen equal the live
    sheet's. `sheet` is a sheets.read() result (must be non-None)."""
    scene = read_baselines(cid).get(sid)
    if not isinstance(scene, dict) or scene.get("module") != mid:
        return False
    if scene.get("schema") != schema_stamp(mid):
        return False
    entry = scene.get("sheets", {}).get(f"{kind}--{eid}")
    if not isinstance(entry, dict):
        return False
    return (entry.get("sheet_type") == sheet["sheet_type"]
            and entry.get("gen") == sheet["gen"])


def baseline_field(cid: str, sid: str, kind: str, eid: str, field_key: str):
    """The scene-start value for a field, or None when no valid baseline
    covers it (report-only)."""
    mid = modules.resolve(cid)
    if mid is None:
        return None
    sheet = sheets.read(cid, kind, eid)
    if sheet is None or sheet["errors"]:
        return None
    if not baseline_entry_valid(cid, sid, kind, eid, mid, sheet):
        return None
    entry = read_baselines(cid)[sid]["sheets"][f"{kind}--{eid}"]
    fields = {**sheets.default_fields(modules.load_pack(mid)["sheets"],
                                      entry["sheet_type"]), **entry["fields"]}
    return fields.get(field_key)


def clear_baselines(cid: str) -> None:
    with _lock(cid):
        _write(cid, {})


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    with _lock(cid):
        data = read_baselines(cid)
        hit = False
        for old, new in mapping.items():
            if old in data:
                data[new] = data.pop(old)
                hit = True
        if hit:
            _write(cid, data)
