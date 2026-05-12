"""PreToolUse hook: refuse writes outside the repo or to gitignored paths.

Reads the PreToolUse JSON payload from stdin. If the tool's target path falls
outside the git repo root, or is matched by .gitignore, emits a JSON deny.
Otherwise exits silently and the tool proceeds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def emit_deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return

    abs_path = os.path.abspath(file_path)

    try:
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return

    repo_root = os.path.abspath(repo_root)

    try:
        rel = os.path.relpath(abs_path, repo_root)
    except ValueError:
        emit_deny(f"Refusing to write outside the repo (different drive): {abs_path}")
        return

    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        emit_deny(
            f"Refusing to write outside the repo. Path resolves to {abs_path}, "
            f"which is outside {repo_root}."
        )
        return

    result = subprocess.run(
        ["git", "-C", repo_root, "check-ignore", "-q", "--", abs_path],
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        emit_deny(
            f"Refusing to write to gitignored path: {rel.replace(os.sep, '/')}. "
            "If this is intentional, either remove the matching .gitignore entry "
            "or edit .claude/hooks/guard-writes.py to whitelist it."
        )
        return


if __name__ == "__main__":
    main()
