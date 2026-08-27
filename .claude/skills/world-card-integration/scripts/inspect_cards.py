"""Dump a World's newly-imported cards to readable text and diff their embedded
lorebooks against the lore the World already holds.

The reading half of `world-card-integration`: it answers "what did these cards bring,
and what does the World already say about it?" so the editorial decisions that follow
are made against evidence rather than a guess. It only reads — every write in that
workflow goes through `grimoire.store`.

    python .claude/skills/world-card-integration/scripts/inspect_cards.py <world-id> \
        --out <scratchpad-dir> [--chars a,b,c]

With no `--chars` it inspects the character directories git reports as untracked, which
is normally exactly the batch that just arrived.

Per character it writes `<out>/<id>.txt` (description, greetings, card tagline, the
card's own scenario labels) and `<out>/<id>_book.txt` (one line per lorebook entry).
Then it prints the reconciliation table:

    card entry                      lore entry                 status
    'Parting'                       parting                    identical
    'Ember'                         ember                      NEW
    'Reckoning'                     reckoning                  DIFFERS  (also: reckoning-2)

  identical  the World already says exactly this — nothing to do
  NEW        no such entry — keep the drop
  DIFFERS    an entry exists with different text, so decide: merge the card's fuller
             material in, drop a thin re-derivation of an entry a previous pass already
             improved, or keep both because they are genuine homonyms

The `also:` note lists existing entries whose slug differs only by a numeric suffix —
the ones most likely to need that decision.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend" / "src"))

from grimoire.store import characters, entities
from grimoire.store.paths import home, slugify

GREETING_TITLE_RE = re.compile(r'greeting-title">(.*?)</div>')
GREETING_DESC_RE = re.compile(r'greeting-description">(.*?)</div>')
TAGLINE_RE = re.compile(r'character-tagline">(.*?)</div>')


def untracked_character_ids(world_root: Path) -> list[str]:
    """Character ids git reports as untracked under the World, newest import first."""
    repo = world_root
    while repo != repo.parent and not (repo / ".git").is_dir():
        repo = repo.parent
    if not (repo / ".git").is_dir():
        print("store is not a git repo; pass --chars", file=sys.stderr)
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", str(world_root)],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"could not read git status ({exc}); pass --chars", file=sys.stderr)
        return []
    found = []
    for line in out.splitlines():
        if not line.startswith("??"):
            continue
        m = re.search(r"characters/([^/]+)/?$", line[3:].strip().strip('"').replace("\\", "/"))
        if m:
            found.append(m.group(1))
    return sorted(found)


def dump_card(world_root: Path, cid: str, out_dir: Path) -> tuple[dict, list[dict]]:
    """Write the readable dumps for one character; return its card data and lorebook."""
    meta = characters.read_character(world_root, cid)
    vid = meta.get("default_version") or "default"
    data = characters.read_card(world_root, cid, vid)["data"]

    notes = data.get("creator_notes") or ""
    tagline = TAGLINE_RE.findall(notes)
    titles = GREETING_TITLE_RE.findall(notes)
    descs = GREETING_DESC_RE.findall(notes)

    chunks = [
        f"### NAME: {data.get('name')}",
        f"### TAGS: {data.get('tags')}",
        f"### CREATOR: {data.get('creator')}   version={data.get('character_version')}",
        f"### CARD TAGLINE: {tagline[0] if tagline else '(none)'}",
        "### DESCRIPTION:\n" + (data.get("description") or ""),
    ]
    chunks.extend(
        f"### {field.upper()}:\n{data[field]}"
        for field in ("personality", "scenario")
        if (data.get(field) or "").strip()
    )
    chunks.append("### FIRST_MES:\n" + (data.get("first_mes") or ""))
    for i, greeting in enumerate(data.get("alternate_greetings") or [], start=1):
        chunks.append(f"### ALT_GREETING_{i}:\n{greeting}")
    if titles:
        padded = descs + [""] * (len(titles) - len(descs))
        labels = "\n".join(f"  {t:<26} :: {d}" for t, d in zip(titles, padded, strict=True))
        chunks.append(
            "### CARD SCENARIO LABELS (context for the scene; title the greetings "
            f"yourself in the World's style):\n{labels}"
        )

    book = data.get("character_book") or {}
    book_entries = book.get("entries") or []
    chunks.append(f"### BOOK: {book.get('name')}, {len(book_entries)} entries "
                  f"(see {cid}_book.txt)")

    (out_dir / f"{cid}.txt").write_text("\n\n".join(chunks), encoding="utf-8")
    (out_dir / f"{cid}_book.txt").write_text(
        "\n".join(
            f"{e.get('comment')!r:<40} keys={e.get('keys')} len={len(e.get('content') or '')}"
            for e in book_entries
        ),
        encoding="utf-8",
    )
    return data, book_entries


def reconcile(world_root: Path, book_entries: list[tuple[str, str]]) -> list[tuple[str, ...]]:
    """Classify each (comment, content) against the World's existing lore entries."""
    existing = {e["id"]: e for e in entities.list_entities(world_root, "lore")}
    rows = []
    for comment, content in book_entries:
        eid = slugify(comment)
        siblings = sorted(
            other for other in existing
            if re.fullmatch(rf"{re.escape(eid)}-\d+", other)
        )
        if eid not in existing:
            status = "NEW"
        else:
            body = (entities.read_entity(world_root, "lore", eid).get("body") or "").strip()
            status = "identical" if body == (content or "").strip() else "DIFFERS"
        rows.append((comment, eid, status, ", ".join(siblings)))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("world", help="World id under <grimoire home>/worlds/")
    ap.add_argument("--out", required=True, type=Path, help="scratchpad dir for the dumps")
    ap.add_argument("--chars", help="comma-separated character ids; default: untracked ones")
    args = ap.parse_args(argv)

    world_root = home() / "worlds" / args.world
    if not world_root.is_dir():
        print(f"no such World: {world_root}", file=sys.stderr)
        return 2

    cids = ([c.strip() for c in args.chars.split(",") if c.strip()] if args.chars
            else untracked_character_ids(world_root))
    if not cids:
        print("no newly-imported character directories found; pass --chars")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = {}
    for cid in cids:
        data, book_entries = dump_card(world_root, cid, args.out)
        print(f"{cid:<16} book={len(book_entries):<4} "
              f"desc={len(data.get('description') or ''):<5} "
              f"alts={len(data.get('alternate_greetings') or [])}")
        for entry in book_entries:
            comment = entry.get("comment") or ""
            # The batch usually shares one lorebook; classify each entry once.
            seen.setdefault(comment, entry.get("content") or "")

    rows = reconcile(world_root, list(seen.items()))
    print(f"\n{'card entry':<36} {'lore entry':<30} status")
    print("-" * 96)
    for comment, eid, status, siblings in rows:
        note = f"  (also: {siblings})" if siblings else ""
        print(f"{comment!r:<36} {eid:<30} {status}{note}")

    counts: dict[str, int] = {}
    for _, _, status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    print(f"\n{len(rows)} distinct entries: {tally}")
    print("\nDIFFERS and any 'also:' siblings need a decision: merge the card's fuller text "
          "into the existing entry, drop a thin re-derivation of one a previous pass already "
          "improved, or keep both as genuine homonyms.")
    print(f"Dumps written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
