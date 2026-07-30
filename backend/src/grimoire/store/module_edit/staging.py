"""The staging area every publishing writer works in: the global module-edit
lock, the staging root, id allocation and the single-rename publish step.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from .. import locks
from ..modules import pack as modules_pack
from ..paths import home, slugify, uniquify

# Cross-process (#234): pack publication rewrites a whole directory in the
# shared user library, so a second backend must be excluded, not just a second
# thread. Reentrant, which _apply -> recover() requires.
_M = locks.module_edit_lock()


@contextmanager
def locked():
    """The global module-edit lock; export and multi-file pack readers wrap
    themselves in this for a swap-coherent view."""
    with _M:
        yield


def _staging_root() -> Path:
    return home() / ".module-staging"


def new_mid(name_or_id: str) -> str:
    """The one id allocator for create/duplicate/import: slugify, reject
    empty, reserve 'none', dedupe against builtin + user ids (mirrors
    modules.create_module's predicate)."""
    base = slugify(" ".join(str(name_or_id).split()) or "module")
    return uniquify(base or "module",
                    lambda i: i == "none" or (modules_pack.user_dir() / i).exists()
                    or (modules_pack.builtin_dir() / i / "module.md").exists())


def _publish(staging: Path, mid: str) -> str:
    dest = modules_pack.user_dir() / mid
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(dest)
    return mid
