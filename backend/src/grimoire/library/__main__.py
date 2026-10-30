"""``python -m grimoire.library``: which V2 library this process would use.

Exit 0 with the location, 2 when the path is unusable, 3 when it would be a
V1 library. Read-only: it creates nothing, so asking is always safe.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .paths import V1_HOME_ENV, V2_HOME_ENV, LibraryPathError, V1IsolationError, resolve_library

USAGE = f"""usage: python -m grimoire.library

Print the V2 library this process would use: {V2_HOME_ENV} when set, else
~/.grimoire-v2. V1's {V1_HOME_ENV}, ~/.grimoire and ~/.grimoire.json are never
used, and a path overlapping a V1 library is refused. Nothing is created.
"""


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         home: Path | None = None, platform: str | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print(USAGE, end="")
        return 0
    if args:
        print(USAGE, end="", file=sys.stderr)
        return 2
    try:
        location = resolve_library(environ=environ, home=home, platform=platform)
    except V1IsolationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    except LibraryPathError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"library: {location.path}")
    print(f"origin: {location.origin}")
    print(f"exists: {'yes' if location.path.is_dir() else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
