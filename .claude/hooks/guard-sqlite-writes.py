"""PreToolUse hook: flag raw SQLite write-SQL added outside the storage layers.

Grimoire's #1 storage rule (CLAUDE.md) is *files are the source of truth;
SQLite is a derived cache*. Direct `INSERT`/`UPDATE`/`DELETE`/`REPLACE` against
file-backed tables should go through the owning module, not be hand-written into
arbitrary domain modules.

This hook inspects the *new* text of a Write/Edit/MultiEdit. If it adds raw
write-SQL and the target is a backend source file OUTSIDE the sanctioned
persistence layers, it returns `permissionDecision: "ask"` so you get a
confirmation prompt with the rule quoted — NOT a hard deny, because legitimate
derived-cache writes (embeddings, facts, relationships, inventory_holdings) do
live in SQLite. It's a speed bump, not a wall.

Tuning:
  * Add a module dir to ALLOWED_DIRS to exempt it (e.g. a new persistence layer).
  * Set GRIMOIRE_SKIP_SQL_GUARD=1 in the env to disable entirely.
  * Change DECISION to "deny" if you want a hard block instead of a prompt.
"""

from __future__ import annotations

import json
import os
import re
import sys

DECISION = "ask"

# Backend source subtrees where raw SQL is expected and exempt. Paths are
# relative to backend/src/grimoire/ and matched as path prefixes.
ALLOWED_DIRS = (
    "storage",  # the SQLite database layer + migrations
    "state_store",  # the file+SQLite hybrid that owns derived writes
    "testing",  # db templating / fixtures
)

# Only guard files under this subtree; SQL anywhere else (tests, scripts,
# bundled_plugins, migrations checked in elsewhere) is left alone.
GUARDED_SUBTREE = os.path.join("backend", "src", "grimoire")

WRITE_SQL = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b",
    re.IGNORECASE,
)


def added_text(tool_input: dict) -> str:
    """Concatenate the text this tool call would introduce."""
    parts: list[str] = []
    if isinstance(tool_input.get("content"), str):  # Write
        parts.append(tool_input["content"])
    if isinstance(tool_input.get("new_string"), str):  # Edit
        parts.append(tool_input["new_string"])
    for edit in tool_input.get("edits") or []:  # MultiEdit
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            parts.append(edit["new_string"])
    return "\n".join(parts)


def emit_ask(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": DECISION,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def main() -> None:
    if os.environ.get("GRIMOIRE_SKIP_SQL_GUARD"):
        return
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return

    norm = os.path.normcase(os.path.abspath(file_path))
    if not norm.endswith(".py"):
        return

    marker = os.path.normcase(os.sep + GUARDED_SUBTREE + os.sep)
    idx = norm.find(marker)
    if idx == -1:
        return

    rel = norm[idx + len(marker) :]
    top = rel.split(os.sep, 1)[0]
    if top in {os.path.normcase(d) for d in ALLOWED_DIRS}:
        return

    if not WRITE_SQL.search(added_text(tool_input)):
        return

    emit_ask(
        "This edit adds raw write-SQL (INSERT/UPDATE/DELETE/REPLACE) to a domain "
        f"module ({rel.replace(os.sep, '/')}). Grimoire's storage rule: files are "
        "the source of truth; SQLite is a derived cache. Write the file and let the "
        "owning module / watcher update the index, or route the write through the "
        "storage / state_store layer. Confirm only if this is a legitimate "
        "derived-cache write (embeddings, facts, relationships, inventory_holdings)."
    )


if __name__ == "__main__":
    main()
