"""``python -m grimoire.migrate_v1 scan``: a read-only inventory of a frozen 0.2.1 library.

Exit 0 when the inventory is ready for review, 2 for unusable arguments, paths
or report locations, 3 when the source and destination overlap or a V2 path
would touch V1, 4 when the inventory is blocked (the report is still written),
5 when the source changed while it was read (no report is written).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..library import LibraryPathError, V1IsolationError, check_report_destination
from . import (EXIT_CHANGED, EXIT_ISOLATION, EXIT_USAGE, NotAV1Library, SourceChanged, migration_roots,
               render_json, render_summary, scan)
from ._report import ReportWriteError, parent_identity, write_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m grimoire.migrate_v1",
                                     description="Read-only inventory of a frozen Grimoire 0.2.1 library.")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("scan", help="inventory a V1 library without changing it")
    s.add_argument("--source", required=True, help="the frozen V1 library or extracted backup (no default)")
    s.add_argument("--archive-id", required=True, help="a lowercase id naming this frozen source")
    s.add_argument("--destination", help="the planned V2 library (default: the V2 library in use); never touched")
    s.add_argument("--report", help="write the JSON manifest here (outside both libraries)")
    s.add_argument("--overwrite", action="store_true", help="replace an existing report file")
    s.add_argument("--summary", choices=("full", "counts"), default="full",
                   help="'counts' prints no names or paths")
    return parser


def _say(text: str, stream) -> None:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(encoding, "backslashreplace").decode(encoding))
    stream.flush()


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         home: Path | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        source, destination = migration_roots(args.source, args.destination, environ=environ, home=home)
    except V1IsolationError as error:
        _say(f"refused: {error}\n", sys.stderr)
        return EXIT_ISOLATION
    except LibraryPathError as error:
        _say(f"unusable path: {error}\n", sys.stderr)
        return EXIT_USAGE
    target = expected_parent = None
    if args.report:
        try:
            target = check_report_destination(
                args.report, protected=[("the migration source", source), ("the V2 destination", destination)],
                overwrite=args.overwrite, environ=environ, home=home)
            expected_parent = parent_identity(target)
        except (LibraryPathError, OSError) as error:
            _say(f"refused report path: {error}\n", sys.stderr)
            return EXIT_USAGE
    try:
        result = scan(args.source, archive_id=args.archive_id, destination=args.destination,
                      environ=environ, home=home)
    except V1IsolationError as error:
        _say(f"refused: {error}\n", sys.stderr)
        return EXIT_ISOLATION
    except SourceChanged as error:
        _say(f"the source changed during the scan ({error}); stop Grimoire 0.2.1 or scan a frozen "
             "copy. No report was written.\n", sys.stderr)
        return EXIT_CHANGED
    except (LibraryPathError, NotAV1Library, ValueError) as error:
        _say(f"cannot scan: {error}\n", sys.stderr)
        return EXIT_USAGE
    except OSError as error:
        _say(f"cannot scan: {error.strerror or error}\n", sys.stderr)
        return EXIT_USAGE
    if target is not None:
        try:
            check_report_destination(
                target, protected=[("the migration source", source), ("the V2 destination", destination)],
                overwrite=args.overwrite, environ=environ, home=home)
            write_report(target, render_json(result.manifest), expected_parent)
        except (LibraryPathError, ReportWriteError) as error:
            _say(f"refused report path: {error}\n", sys.stderr)
            return EXIT_USAGE
    _say(render_summary(result.manifest, mode=args.summary), sys.stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
