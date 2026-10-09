"""The one way to copy the frozen campaign's `home/` (inference slice I).

`tests/fixtures/frozen_campaign/home/` is never regenerated and never written:
its value is being a store today's code did not write. Every reader works on a
copy -- the frozen tests (`test_frozen_campaign.py`'s `frozen_home`) and the
sweep that regenerates `snapshot.json` (`sweep._write_snapshot`) -- and they
prepare it here, so the copy the snapshot was taken from and the copy the test
compares against are made the same way.

`digest` is how a test proves `home/` itself was left alone (spec §11.5): a
copy that played a turn or ran the migration and retirement must not have
reached back into the checked-in tree.

Deliberately free of the sweep's imports: the sweep imports this module.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

#: The checked-in store. Read, digested and copied; never written.
HOME = Path(__file__).resolve().parent / "fixtures" / "frozen_campaign" / "home"


def copy_home(dst: Path) -> Path:
    """A private copy of `HOME` at `dst` (which must not exist yet); `dst`."""
    shutil.copytree(HOME, dst)
    return dst


def digest(root: Path = HOME) -> dict[str, str]:
    """Every entry under `root`, by its root-relative path: a file's content
    hash, or `<dir>` for a directory (an empty directory is store state too)."""
    return {p.relative_to(root).as_posix():
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
            for p in sorted(root.rglob("*"))}
