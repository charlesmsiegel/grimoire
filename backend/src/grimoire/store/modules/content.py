"""Reading one content entry or one rules doc out of a pack.

``load_pack`` keeps only frontmatter for these; the detail routes come back
here for the body.
"""

from __future__ import annotations

import json

from ..frontmatter import parse_frontmatter
from ..paths import safe_id
from .fields import CONTENT_KINDS
from .pack import ContentNotFound, _safe_mid, pack_root


def read_content(mid: str, kind: str, id: str) -> dict:
    root, _source = pack_root(mid)  # raises ModuleNotFound
    if kind not in CONTENT_KINDS or not safe_id(id):
        raise ContentNotFound(f"{kind}/{id}")
    p = root / "content" / kind / f"{id}.md"
    if not p.exists():
        raise ContentNotFound(f"{kind}/{id}")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    out = {"kind": kind, "id": id, "name": meta.get("name", id), "body": body,
           "keys": meta.get("keys", ""), "sheet_type": None, "fields": {}}
    for k, v in meta.items():
        if k not in ("name", "keys"):
            out[k] = v
    sidecar = root / "content" / kind / f"{id}.sheet.json"
    if sidecar.exists():
        try:
            stat = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            stat = {}
        if isinstance(stat, dict):
            out["sheet_type"] = stat.get("sheet_type")
            out["fields"] = stat.get("fields", {}) if isinstance(stat.get("fields"), dict) else {}
    return out


def read_rule(mid: str, rid: str) -> dict | None:
    """Frontmatter + body of one rules doc; load_pack keeps frontmatter only."""
    root, _source = pack_root(mid)  # raises ModuleNotFound
    if not isinstance(rid, str) or not _safe_mid(rid):
        return None
    p = root / "rules" / f"{rid}.md"
    if not p.exists():
        return None
    try:
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        return None
    return {"meta": meta, "body": body}
