"""Per-character campaign play-state: a `current_state` snapshot stored beside the
character copy at <root>/characters/<cid>/state.md. Snapshot only — rewritten each
absorb (discrete events live in the chronicle timeline). Mirrors briefs.py.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso


def state_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "state.md"


def read_state(root: Path, cid: str) -> dict | None:
    p = state_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"current_state": body.strip(), "updated": meta.get("updated", "")}


def write_state(root: Path, cid: str, current_state: str) -> None:
    p = state_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"updated": now_iso()}, current_state.strip() + "\n"),
                 encoding="utf-8")
