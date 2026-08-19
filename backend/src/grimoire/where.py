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

Printing deliberately creates nothing. The point is to say where the library
*will* be while there is still time to repoint it before the first run.
"""

from __future__ import annotations

from .store import paths


def _source_note(source: str, pointer: str) -> str:
    """How the resolved path was arrived at, in the reader's terms."""
    return {
        "default": "the default location",
        "custom": f"your chosen location, recorded in {pointer}",
        "env": "named by the GRIMOIRE_HOME environment variable",
    }.get(source, source)


def describe() -> str:
    """The "where is my data" blurb both installers print when they finish."""
    info = paths.data_dir_info()
    pointer = paths.pointer_path()
    state = "already exists" if info["exists"] else "created on first use"
    return "\n".join([
        f"Your grimoire library lives in: {info['data_dir']}",
        f"  ({_source_note(info['source'], str(pointer))}; {state})",
        "",
        # ASCII only, on purpose: this is printed by the Windows installer
        # too, where a cmd.exe console still encodes stdout as the active code
        # page -- an em dash there is a UnicodeEncodeError, not a typo.
        "Worlds, campaigns and characters are stored there, outside this",
        "checkout -- an update or a reinstall never touches them. To keep the",
        "library somewhere else (a synced folder shared between devices,",
        "another drive), change it on the app's Configuration page, or, before",
        "the first run, write the path into the bootstrap pointer:",
        "",
        f'  {pointer}  ->  {{"data_dir": "/path/you/want"}}',
    ])


def main() -> None:
    print(describe())


if __name__ == "__main__":
    main()
