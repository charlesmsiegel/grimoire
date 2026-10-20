# Read precedent and inspect incoming cards

## Before anything: read the precedent

The store is a git repo with its own history. Resolve its root the way the app does
rather than assuming `~/.grimoire` — `GRIMOIRE_HOME` or the bootstrap pointer may name
somewhere else (AGENTS.md, "the store"):

```bash
STORE=$(python -c "import sys; sys.path.insert(0,'backend/src'); from grimoire.store.paths import home; print(home())")

git -C "$STORE" log --oneline -- worlds/<world-id>/ | head -30
```

Commits whose subject says "integrate" are earlier runs of this workflow. Read the two most
recent in full. They carry the house voice for the commit message, the level of detail the
user expects to be told, and judgment calls already settled — if a previous commit says two
same-named entries are genuine homonyms and were deliberately kept apart, don't undo it.

## Step 1: See what arrived

```bash
git -C "$STORE" status --porcelain worlds/<world-id>/
```

Untracked character directories are the new cards. Untracked lore files are drops from the
import. Some "new" drops are regenerations of material an *earlier* integration already
improved — they look new and are not.

Then read the cards:

```bash
python .agents/skills/world-card-integration/scripts/inspect_cards.py <world-id> --out <scratchpad>
```

It writes one readable dump per character and prints how every lorebook entry compares to
what the World already holds. Read the dumps properly — the description carries a
relationships block in which each character describes every other character in the batch, and
that block is the difference between eight independent entries and one integrated group.

Skip `creator_notes` when reading; it is mostly a wall of CSS. The inspector already extracts
the two useful things from it — the card's own tagline, and its label for each greeting.
