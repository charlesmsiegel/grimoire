"""Per-character campaign play-state stored beside the character copy at
<root>/characters/<cid>/state.md: a standing snapshot of `current_state` plus what the
character `knows` / `suspects`, as optional `## `-headed prose sections. A body with no
recognized header is read wholesale as `current_state` (Phase-2 back-compat). Snapshot
only — rewritten each absorb (discrete events live in the chronicle timeline). Mirrors
dossiers.py: a per-character, campaign-local markdown artifact filed beside the character
copy; groupstate.py is the same shape for groups.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso
from . import atomic


_HEADERS = {"current state": "current_state", "knows": "knows", "suspects": "suspects"}


def state_path(root: Path, cid: str) -> Path:
    return root / "characters" / cid / "state.md"


def _is_header(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
        return _HEADERS[stripped[3:].strip().lower()]
    return None


def parse_body(body: str) -> dict:
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


def fold_fields(current_state: str, fields: dict[str, str]) -> str:
    """Set `Label: value` lines inside a `current_state` body.

    The write side of #121's promotion: a transient value reinforced across
    enough posts becomes a labelled line in the standing snapshot. A line
    already carrying that label is REPLACED in place, keeping its own spelling
    of the label and its position; only a genuinely new label is appended. That
    is what makes promotion idempotent — the second absorb over the same ledger
    composes the identical body, and `materialize` drops an edit whose
    `before == after`, so nothing is staged twice.

    Matching is on the text before the first colon, case-insensitively, and
    only on a line that has one. Prose is left alone: a narrative line has no
    leading `Word:` label, and one that happens to (`Mood: still furious`) is
    exactly the line this is meant to update.
    """
    lines = current_state.strip().splitlines()
    pending = {k.casefold(): (k, v) for k, v in fields.items() if v.strip()}
    out = []
    for line in lines:
        label, sep, _ = line.partition(":")
        key = label.strip().casefold()
        if sep and key in pending:
            out.append(f"{label.strip()}: {pending.pop(key)[1].strip()}")
        else:
            out.append(line)
    for key, value in fields.items():
        held = pending.pop(key.casefold(), None)
        if held is not None:
            out.append(f"{held[0][:1].upper()}{held[0][1:]}: {value.strip()}")
    return "\n".join(out).strip()


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
    return {**parse_body(body), "updated": meta.get("updated", "")}


def write_state(root: Path, cid: str, body: str) -> None:
    p = state_path(root, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"))
