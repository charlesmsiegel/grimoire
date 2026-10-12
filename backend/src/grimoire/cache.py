"""`python -m grimoire.cache sync`: warm what was hot after direct edits.

Spec 05-C2. An agent or a script that edits the store outside the running app
-- by hand, or through `grimoire.store` with no server involved -- runs this
afterwards, naming what it touched, so the derived representations that were
already in use are rebuilt now rather than on the next read that needs them.
It is a warm-up: forgetting it costs a cold read, never a wrong one.

From a checkout, with the backend venv (`PYTHONPATH` in every form, because the
venv's editable install points at whichever checkout created it):

    PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync ...          # macOS/Linux
    PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.cache sync ...  # Windows (Git Bash)
    $env:PYTHONPATH="backend/src"; backend\\.venv\\Scripts\\python.exe -m grimoire.cache sync ...  # Windows (PowerShell)

The root is `paths.home()`, the resolver every entry point uses; a different
library is `GRIMOIRE_HOME`, never a flag. The report names the root and where
it came from, because an agent whose shell and server disagree about
`GRIMOIRE_HOME` would otherwise sync a tree nobody reads.

Exit status: 0 when no path is `failed`, `refused`, `missing`,
`verify_failed` or left `unsynced` (cache off, cold paths and deferred vectors
are success); 1 when one is; 2 for a usage error; 3 when the root cannot be
used at all. `sync` is a subcommand so a later admin surface can add `status`
or `doctor` beside it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .store import cache_sync, logs, paths

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_ROOT = 0, 1, 2, 3


def _renamed(text: str) -> tuple[str, str]:
    old, sep, new = text.partition("=")
    if not sep or not old or not new:
        raise argparse.ArgumentTypeError(f"expected OLD=NEW, got {text!r}")
    return old, new


def build_parser() -> argparse.ArgumentParser:
    """The parser 06's store-editing skill is held to by its drift test."""
    parser = argparse.ArgumentParser(prog="python -m grimoire.cache",
                                     description="Warm grimoire's derived caches after direct edits.")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="rebuild what was hot for the named paths and scopes")
    sync.add_argument("paths", nargs="*", metavar="PATH",
                      help="a store-relative or absolute path inside the store; a directory expands")
    sync.add_argument("--campaign", action="append", default=[], metavar="CID",
                      help="every file under campaigns/CID that may have changed")
    sync.add_argument("--world", action="append", default=[], metavar="WID",
                      help="every file under worlds/WID that may have changed")
    sync.add_argument("--all", action="store_true", dest="all_records",
                      help="every syncable record directory")
    sync.add_argument("--renamed", action="append", default=[], type=_renamed, metavar="OLD=NEW",
                      help="a rename you made: NEW inherits what OLD had materialized")
    sync.add_argument("--deleted", action="append", default=[], metavar="PATH",
                      help="a path you removed on purpose (a world or campaign root purges the cache)")
    sync.add_argument("--no-embed", action="store_false", dest="embed",
                      help="rebuild everything local and leave embeddings to lazy")
    sync.add_argument("--dry-run", action="store_true",
                      help="validate, classify and plan; build, store, embed and bump nothing")
    sync.add_argument("--verify", action="store_true",
                      help="recompute each rebuilt kind with the cache bypassed and compare")
    sync.add_argument("--json", action="store_true", help="print the report as one JSON object")
    sync.add_argument("--quiet", action="store_true", help="print only the counts")
    return parser


def _root_problem(root: Path) -> str | None:
    if not root.exists():
        return "does not exist"
    if not root.is_dir():
        return "is not a directory"
    if not os.access(root, os.R_OK | os.X_OK):
        return "is not readable"
    return None


def _counts_line(report: dict) -> str:
    counts = " ".join(f"{name}={n}" for name, n in report["counts"].items() if n)
    return counts or "nothing to sync"


def _text(report: dict, *, quiet: bool) -> str:
    lines = [_counts_line(report)]
    if quiet:
        return lines[0]
    lines.insert(0, f"root: {report['root']} ({report['root_source']})")
    lines.insert(1, f"cache: {report['cache']}" + (f" ({report['cache_reason']})" if report["cache_reason"] else ""))
    if report["dry_run"]:
        lines.append("dry run: nothing was stored, embedded or bumped")
    embedding = report["embedding"]
    if any(embedding.get(k) for k in ("would_send", "sent", "requests", "stale_space", "embedding_off", "deferred")):
        lines.append("embedding: " + " ".join(f"{k}={v}" for k, v in embedding.items() if v))
    lines.append(f"tokens bumped: {report['tokens_bumped']}"
                 + (f"; purged: {report['purged']}" if report["purged"] is not None else ""))
    if report["truncated"]:
        lines.append("truncated: a bound was hit, so not everything was looked at")
    if report["stopped"]:
        lines.append(f"stopped: {report['stopped']}")
    for item in report["paths"]:
        detail = item.get("reason") or ", ".join(f"{k}:{v}" for k, v in item.get("kinds", {}).items())
        lines.append(f"  {item['status']:<14} {item['path']}" + (f"  [{detail}]" if detail else ""))
    if any(item["status"] == "missing" for item in report["paths"]):
        lines.append("a missing path was not synced; if you removed it on purpose, name it with --deleted")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:          # absent when stdout is a capture object
        reconfigure(errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.paths or args.campaign or args.world or args.all_records or args.deleted or args.renamed):
        parser.error("name at least one PATH, --campaign, --world, --all, --deleted or --renamed")
    # Before anything can log: a failure's meter row and its log line land in
    # the store's own `logs/`, through the one writer, as the server's would.
    logs.install()
    root = paths.home()
    problem = _root_problem(root)
    if problem is not None:
        print(f"the library root {root} {problem}", file=sys.stderr)
        return EXIT_ROOT
    report = cache_sync.run(args.paths, campaigns=args.campaign, worlds=args.world,
                            all_records=args.all_records, renamed=dict(args.renamed),
                            deleted=args.deleted, embed=args.embed, dry_run=args.dry_run,
                            verify=args.verify, root=root)
    out = report.as_dict()
    if args.json:
        print(json.dumps(out, ensure_ascii=True, sort_keys=False))
    else:
        print(_text(out, quiet=args.quiet))
    return EXIT_OK if report.ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
