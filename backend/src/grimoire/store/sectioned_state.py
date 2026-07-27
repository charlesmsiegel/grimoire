"""Shared machinery for `## `-headed prose snapshot files.

Both playstate.py (per-character) and groupstate.py (per-group) store a standing
snapshot at <root>/<subdir>/<id>/state.md: optional `## `-headed prose sections
with frontmatter carrying `updated`. They differ only in which sections exist,
which one a header-less body falls back to, and which directory holds them —
so the parse/compose/read/write behavior lives here once, and each module binds
a `Sections` describing its own shape.

A body whose first non-empty line is not a recognized header is read wholesale
into the fallback field, so legacy single-field snapshots keep loading and prose
that merely *contains* a `## Knows`-looking line mid-text is never split.
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso


class Sections:
    """One snapshot shape: where it lives, its sections, and its fallback field."""

    def __init__(self, subdir: str, labels: dict[str, str], fallback: str):
        self.subdir = subdir
        self.labels = labels
        self.fields: tuple[str, ...] = tuple(labels)
        self.fallback = fallback
        self._headers = {label.lower(): key for key, label in labels.items()}

    def state_path(self, root: Path, eid: str) -> Path:
        return root / self.subdir / eid / "state.md"

    def _is_header(self, line: str) -> str | None:
        stripped = line.strip()
        if stripped.startswith("## "):
            return self._headers.get(stripped[3:].strip().lower())
        return None

    def parse_body(self, body: str) -> dict:
        fields = {k: "" for k in self.fields}
        lines = body.splitlines()
        # Structured only when the FIRST non-empty line is a recognized header.
        first = next((ln for ln in lines if ln.strip()), "")
        if self._is_header(first) is None:
            fields[self.fallback] = body.strip()
            return fields

        cur, buf = None, []

        def flush():
            if cur is not None:
                fields[cur] = "\n".join(buf).strip()

        for line in lines:
            head = self._is_header(line)
            if head is not None:
                flush()
                cur, buf = head, []
                continue
            buf.append(line)
        flush()
        return fields

    def compose_body(self, values: dict[str, str]) -> str:
        """Render sections, omitting empty ones. A snapshot carrying only the
        fallback field is written bare, with no header, matching how a legacy
        body reads back."""
        vals = {k: (values.get(k, "") or "").strip() for k in self.fields}
        non_empty = [k for k in self.fields if vals[k]]
        if non_empty == [self.fallback]:
            return vals[self.fallback]
        return "\n\n".join(f"## {self.labels[k]}\n{vals[k]}" for k in non_empty)

    def read_state(self, root: Path, eid: str) -> dict | None:
        p = self.state_path(root, eid)
        if not p.exists():
            return None
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        return {**self.parse_body(body), "updated": meta.get("updated", "")}

    def write_state(self, root: Path, eid: str, body: str) -> None:
        p = self.state_path(root, eid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"),
                     encoding="utf-8")
