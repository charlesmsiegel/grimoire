"""Per-group campaign state stored beside the campaign's group records at
<root>/groups/<gid>/state.md (a sibling directory of the flat groups/<gid>.md,
like <kind>/<eid>/assets/): a standing snapshot in optional `## `-headed prose
sections. A body whose first non-empty line is not a recognized header is read
wholesale as `goals`. Snapshot only — rewritten each absorb. Mirrors
playstate.py; state is campaign-local by definition (never world-side).
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso
from . import atomic

LABELS: dict[str, str] = {
    "goals": "Goals", "resources": "Resources", "focus": "Focus",
    "public_perception": "Public perception", "secrets": "Secrets",
}
FIELDS: tuple[str, ...] = tuple(LABELS)
_HEADERS = {label.lower(): key for key, label in LABELS.items()}


def state_path(root: Path, gid: str) -> Path:
    return root / "groups" / gid / "state.md"


def _is_header(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
        return _HEADERS[stripped[3:].strip().lower()]
    return None


def _parse_body(body: str) -> dict:
    fields = {k: "" for k in FIELDS}
    lines = body.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    if _is_header(first) is None:
        fields["goals"] = body.strip()
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


def compose_body(values: dict[str, str]) -> str:
    vals = {k: (values.get(k, "") or "").strip() for k in FIELDS}
    non_empty = [k for k in FIELDS if vals[k]]
    if non_empty == ["goals"]:
        return vals["goals"]
    return "\n\n".join(f"## {LABELS[k]}\n{vals[k]}" for k in non_empty)


def read_state(root: Path, gid: str) -> dict | None:
    p = state_path(root, gid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {**_parse_body(body), "updated": meta.get("updated", "")}


def write_state(root: Path, gid: str, body: str) -> None:
    p = state_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"))
