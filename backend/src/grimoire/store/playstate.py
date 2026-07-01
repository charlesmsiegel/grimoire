"""Per-character campaign play-state stored beside the character copy at
<root>/characters/<cid>/state.md: a standing snapshot of `current_state` plus what the
character `knows` / `suspects`, as optional `## `-headed prose sections. A body with no
recognized header is read wholesale as `current_state` (Phase-2 back-compat). Snapshot
only — rewritten each absorb (discrete events live in the chronicle timeline). Mirrors
briefs.py.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso


_HEADERS = {"current state": "current_state", "knows": "knows", "suspects": "suspects"}


def state_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "state.md"


def _parse_body(body: str) -> dict:
    fields = {"current_state": "", "knows": "", "suspects": ""}
    cur, buf = None, []
    saw_header = False

    def flush():
        if cur and buf:
            fields[cur] = "\n".join(buf).strip()

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
            flush()
            cur, buf, saw_header = _HEADERS[stripped[3:].strip().lower()], [], True
            continue
        buf.append(line)
    flush()
    if not saw_header:  # legacy Phase-2 body: whole thing is current_state
        fields["current_state"] = body.strip()
    return fields


def compose_body(current_state: str, knows: str, suspects: str) -> str:
    current_state, knows, suspects = current_state.strip(), knows.strip(), suspects.strip()
    if not knows and not suspects:
        return current_state
    parts = []
    for label, value in (("Current state", current_state), ("Knows", knows), ("Suspects", suspects)):
        if value:
            parts.append(f"## {label}\n{value}")
    return "\n\n".join(parts)


def read_state(root: Path, cid: str) -> dict | None:
    p = state_path(root, cid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {**_parse_body(body), "updated": meta.get("updated", "")}


def write_state(root: Path, cid: str, body: str) -> None:
    p = state_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"),
                 encoding="utf-8")
