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


def _is_header(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
        return _HEADERS[stripped[3:].strip().lower()]
    return None


def _parse_body(body: str) -> dict:
    fields = {"current_state": "", "knows": "", "suspects": ""}
    lines = body.splitlines()
    # Structured only when the FIRST non-empty line is a recognized header. Otherwise the
    # body is a legacy / current_state-only snapshot and is taken wholesale — so prose that
    # merely contains a "## Knows"-looking line mid-text is never split or lost.
    first = next((ln for ln in lines if ln.strip()), "")
    if _is_header(first) is None:
        fields["current_state"] = body.strip()
        return fields

    cur, buf = None, []

    def flush():
        if cur is not None:
            fields[cur] = "\n".join(buf).strip()

    for line in lines:
        head = _is_header(line)
        if head is not None:
            flush()
            cur, buf = head, []
            continue
        buf.append(line)
    flush()
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
