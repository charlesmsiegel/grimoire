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

    # Claude Code's auto-memory directory lives outside the repo at
    # ~/.claude/projects/<slug>/memory/ — writes there are always allowed,
    # they're how the assistant maintains its cross-conversation memory.
    home = os.path.expanduser("~")
    memory_root = os.path.normcase(
        os.path.abspath(os.path.join(home, ".claude", "projects"))
    )
    if os.path.normcase(abs_path).startswith(memory_root + os.sep):
        return

    try:
        cwd_repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return

    cwd_repo_root = os.path.abspath(cwd_repo_root)

    # Resolve the worktree that owns `abs_path` (may differ from CWD's
    # worktree when writing into `.worktrees/<name>/...`). Using the file's
    # own worktree means check-ignore consults the right .gitignore stack —
    # `.worktrees/` is ignored from the main worktree's POV but the files
    # inside each worktree are normally tracked there.
    target_dir = os.path.dirname(abs_path) or "."
    try:
        target_repo_root = subprocess.check_output(
            ["git", "-C", target_dir, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        target_repo_root = os.path.abspath(target_repo_root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        target_repo_root = cwd_repo_root

    try:
        rel = os.path.relpath(abs_path, target_repo_root)
    except ValueError:
        emit_deny(f"Refusing to write outside the repo (different drive): {abs_path}")
        return

    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        emit_deny(
            f"Refusing to write outside the repo. Path resolves to {abs_path}, "
            f"which is outside {target_repo_root}."
        )
        return

    result = subprocess.run(
        ["git", "-C", target_repo_root, "check-ignore", "-q", "--", abs_path],
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
