"""Registry administration: scaffold a new user module, delete one."""

from __future__ import annotations

import shutil

from .. import atomic
from ..frontmatter import dump_frontmatter
from ..paths import slugify, uniquify
from .pack import ModuleError, builtin_dir, pack_root, user_dir


def create_module(name: str) -> str:
    """Scaffold a minimal valid module pack in user_dir(), return its id."""
    # Normalize: collapse newlines/whitespace, then default to "Untitled"
    name = " ".join(name.split())
    name = name or "Untitled"
    mid = uniquify(slugify(name), lambda i: i == "none" or (user_dir() / i).exists()
                   or (builtin_dir() / i / "module.md").exists())
    d = user_dir() / mid
    d.mkdir(parents=True)
    atomic.write_text(d / "module.md", dump_frontmatter({"name": name, "description": "", "version": "0.1"}, ""))
    atomic.write_text(d / "sheets.json", '{\n  "groups": {},\n  "sheet_types": {}\n}\n')
    return mid


def delete_module(mid: str) -> None:
    """Delete a user module. Raises ModuleError if builtin, ModuleNotFound if absent."""
    root, source = pack_root(mid)
    if source != "user":
        raise ModuleError("built-in modules cannot be deleted")
    shutil.rmtree(root)
