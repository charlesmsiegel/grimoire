"""PostToolUse hook: auto-format the file Claude just edited.

Reads the PostToolUse JSON payload from stdin, looks at the edited file's
extension, and runs the project's configured formatter:

  *.py                      -> `uv run ruff format` + `ruff check --fix`  (backend/)
  *.ts *.tsx *.css *.json   -> `pnpm exec prettier --write`               (frontend/)
  *.md *.yaml *.yml         -> prettier (only under frontend/, which is the
                               only tree with a prettier config)

This is best-effort and never blocks: any failure (formatter missing, syntax
error mid-edit, file already gone) exits 0 silently so the edit still stands.
CI's `ruff format --check` / `prettier --check` remain the source of truth.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys

# Extension -> ("backend"|"frontend", [argv-without-target]). The target path
# is appended as the final arg. cwd is the matching package dir so the tool
# picks up that package's pinned version + config.
PY_EXTS = {".py"}
PRETTIER_EXTS = {".ts", ".tsx", ".js", ".jsx", ".css", ".json", ".md", ".yaml", ".yml"}


def repo_root() -> str | None:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root and os.path.isdir(root):
        return os.path.abspath(root)
    try:
        return os.path.abspath(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run(argv: list[str], cwd: str) -> None:
    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=55,
        )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return

    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        return

    ext = os.path.splitext(abs_path)[1].lower()
    root = repo_root()
    if root is None:
        return

    backend = os.path.join(root, "backend")
    frontend = os.path.join(root, "frontend")
    norm = os.path.normcase(abs_path)

    if ext in PY_EXTS and norm.startswith(os.path.normcase(backend) + os.sep):
        run(["uv", "run", "ruff", "format", abs_path], cwd=backend)
        run(["uv", "run", "ruff", "check", "--fix", abs_path], cwd=backend)
        return

    if ext in PRETTIER_EXTS and norm.startswith(os.path.normcase(frontend) + os.sep):
        # Invoke prettier through `node` + its .cjs entry rather than the
        # `pnpm`/`prettier` shim. On Windows those shims are .cmd files that
        # CreateProcess (subprocess without a shell) can't launch, so the call
        # would silently no-op; `node` is a real executable everywhere.
        prettier_cjs = os.path.join(frontend, "node_modules", "prettier", "bin", "prettier.cjs")
        if os.path.isfile(prettier_cjs):
            run(["node", prettier_cjs, "--write", abs_path], cwd=frontend)
        return


if __name__ == "__main__":
    main()
