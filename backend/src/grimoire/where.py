"""Report where this machine's grimoire library lives.

The store is created lazily -- `store.paths.ensure_home()` makes `worlds/` and
`campaigns/` on the first API call that needs them -- so an installer finishes
with nothing on disk to point at, and a fresh clone gives no hint that the
library is going somewhere outside the checkout at all. Both installers end by
running ``python -m grimoire.where``, which answers that question in the one
place able to answer it correctly: the resolver itself. Hard-coding
``~/.grimoire`` into the two install scripts would have been a lie on exactly
the machines whose owner most needs to be told -- the ones where
``GRIMOIRE_HOME`` or the bootstrap pointer already names somewhere else.

Which is also why the advice at the bottom is per-source rather than one fixed
paragraph. `home()` reads the env var *before* the pointer, so telling someone
running under ``GRIMOIRE_HOME`` to edit ``~/.grimoire.json`` is telling them to
edit a file that will be ignored -- wrong in precisely the case this module
exists for.

Printing deliberately creates nothing. The point is to say where the library
*will* be while there is still time to repoint it before the first run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .store import paths


def _state(target: Path) -> str:
    """Whether the library is already on disk -- and whether it *can* be.

    A path that exists as a file is neither "there" nor "coming": the first
    write will fail on it. Saying so here costs two lines and saves the user
    an error from deep inside the store.
    """
    if target.is_dir():
        return "already exists"
    if target.exists():
        return "WARNING: this path is a file, not a directory"
    return "created on first use"


def _how_to_move(source: str, pointer: Path) -> list[str]:
    """How to put the library somewhere else, given how it is resolved now.

    Ordered by what actually wins: `home()` checks GRIMOIRE_HOME first, so
    under that source no edit to the pointer file changes anything, and the
    generic advice would be actively misleading.
    """
    if source == "env":
        return [
            "That path comes from the GRIMOIRE_HOME environment variable, which",
            "overrides everything else -- change or unset it to move the library.",
        ]
    return [
        "To keep the library somewhere else (a synced folder shared between",
        "devices, another drive), change it on the app's Configuration page,",
        "or, before the first run, write the path into the bootstrap pointer:",
        "",
        f'  {pointer}  ->  {{"data_dir": "/path/you/want"}}',
    ]


def describe() -> str:
    """The "where is my data" blurb both installers print when they finish."""
    info = paths.data_dir_info()
    pointer = paths.pointer_path()
    target = Path(info["data_dir"])      # the resolver's answer, not a path found here
    source = {
        "default": "the default location",
        "custom": f"your chosen location, recorded in {pointer}",
        "env": "named by the GRIMOIRE_HOME environment variable",
    }.get(info["source"], info["source"])
    return "\n".join([
        f"Your grimoire library lives in: {target}",
        f"  ({source}; {_state(target)})",
        "",
        # ASCII only, here and below: the Windows installer prints this into
        # a console that still encodes stdout as the active code page, where
        # an em dash is a UnicodeEncodeError rather than a typo.
        "Worlds, campaigns and characters are stored there, outside this",
        "checkout -- an update or a reinstall never touches them.",
        "",
        *_how_to_move(info["source"], pointer),
    ])


def main() -> None:
    """Print the blurb, on a console that may not be able to spell the path.

    The blurb itself is ASCII, but the *path* in it is whatever the user's home
    directory is called, and the encoding stdout was opened with is whatever
    the locale said -- ANSI_X3.4-1968 under `LC_ALL=C`, a code page on Windows.
    An accented home directory there makes `print` raise UnicodeEncodeError,
    which under `set -e` ends a successful install on a traceback about the
    last, purely informational line. Escaping the characters the console cannot
    spell keeps the path readable and the exit status 0.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:      # absent when stdout is a capture object
        reconfigure(errors="backslashreplace")
    print(describe())


if __name__ == "__main__":
    main()
