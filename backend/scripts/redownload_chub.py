"""Re-download every chub-linked character version, world by world.

Each linked version is re-imported from its stored chub_source — the store
overwrites the version in place and overlays chub's current API definition
(greetings, description, lorebook), then localizes image references into the
version's asset store. Failures are per-version: one bad card never stops the
sweep.

Usage (from the repo root):
  backend/.venv/Scripts/python.exe backend/scripts/redownload_chub.py --list
  backend/.venv/Scripts/python.exe backend/scripts/redownload_chub.py my-world other-world
  backend/.venv/Scripts/python.exe backend/scripts/redownload_chub.py --all
Options:
  --list         show each world's chub-linked version count and exit
  --dry-run      print what would be re-downloaded, no network calls
  --no-localize  skip the image-localization pass after each re-download

Respects GRIMOIRE_HOME / the ~/.grimoire.json pointer, same as the app.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import characters, localize, worlds


def linked_versions(root: Path):
    """Yield (cid, vid, chub_source) for every version linked to a chub URL."""
    for summary in characters.list_characters(root):
        detail = characters.read_character(root, summary["id"])
        for v in detail["versions"]:
            if v.get("chub_source"):
                yield summary["id"], v["id"], v["chub_source"]


def run_world(wid: str, *, do_localize: bool, dry_run: bool) -> tuple[int, int]:
    root = worlds.world_root(wid)
    ok = failed = 0
    for cid, vid, source in linked_versions(root):
        label = f"{wid}/{cid}@{vid}"
        if dry_run:
            print(f"  would re-download {label}  <-  {source}")
            continue
        try:
            characters.import_from_chub(root, source, into_cid=cid, into_vid=vid)
            line = f"  ok    {label}"
            if do_localize:
                card = characters.read_card(root, cid, vid)
                summary = {}
                for ev in localize.localize_card(card, root, cid, vid, wid):
                    summary = ev.get("summary", summary)
                if summary.get("localized"):
                    characters.update_version(root, cid, vid, card)
                line += (f"  (localized {summary.get('localized', 0)}"
                         f", skipped {summary.get('skipped', 0)}"
                         f", failed {summary.get('failed', 0)})")
            print(line, flush=True)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — per-version: log and continue
            print(f"  FAIL  {label}: {exc}", flush=True)
            failed += 1
        time.sleep(1.0)  # be polite to chub's API between cards
    return ok, failed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-download all chub-linked character versions.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("worlds", nargs="*", metavar="WORLD", help="world id(s) to sweep")
    ap.add_argument("--all", action="store_true", help="sweep every world in the store")
    ap.add_argument("--list", action="store_true",
                    help="list worlds with their chub-linked version counts and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be re-downloaded, make no network calls")
    ap.add_argument("--no-localize", action="store_true",
                    help="skip the image-localization pass after each re-download")
    args = ap.parse_args()

    known = [w["id"] for w in worlds.list_worlds()]
    if args.list:
        for wid in known:
            n = sum(1 for _ in linked_versions(worlds.world_root(wid)))
            print(f"{wid}: {n} chub-linked version(s)")
        return 0

    targets = known if args.all else args.worlds
    if not targets:
        ap.error("pass world id(s), --all, or --list")
    unknown = [w for w in targets if w not in known]
    if unknown:
        ap.error(f"unknown world(s): {', '.join(unknown)} (known: {', '.join(known)})")

    total_ok = total_failed = 0
    for wid in targets:
        print(f"{wid}:", flush=True)
        ok, failed = run_world(wid, do_localize=not args.no_localize, dry_run=args.dry_run)
        total_ok += ok
        total_failed += failed
    if not args.dry_run:
        print(f"done: {total_ok} re-downloaded, {total_failed} failed")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
