---
name: world-card-integration
description: Use when newly-imported character cards need folding into a World by hand — reconciling their lorebook against existing lore entries, rewriting the cast entries against the cards, and writing the greetings, groups, locations, items, tags, plot-map chains and voice anchors that follow. Triggers on "integrate their lore", "we just added N characters", "fold these cards in", "populate ran, now clean it up", "these new characters need locations and items", on reconciling duplicate `-2`/`-3` lore entries, and on auditing a World after an import.
---

# Integrating imported cards into a World

A World is a hand-edited model of a setting. Cards arrive from outside it — someone imports
a SillyTavern card, and its embedded lorebook, greetings and description come with it.
`populate-world-content` turns a roster of those cards into entities and greetings at scale;
it is deliberately mechanical, and it leaves behind the judgment calls it cannot make:
duplicate slugs, cast entries written from a summary before the card existed, mechanics the
cards name but the World has never defined.

This skill is that editorial pass, for a batch small enough to read properly — the handful of
cards someone just added, not a sixteen-World sweep. If the World has never been populated at
all, run `populate-world-content` first and then come back here.

The output is one commit against the user's store, in the style of the previous integration
commits in that store's history.

## Privacy

Worlds hold the user's private content and live outside this repo by design. Two rules
follow, and the second is the one that gets forgotten:

- Everything you write lands in the store, never in this repo. If you need a scratch script,
  put it in the session scratchpad.
- **Never carry a real World, character, entity or campaign name back into this repo** — not
  into a commit message here, a doc, a test fixture, or an example in this skill. The
  examples below use invented names for exactly that reason; keep it that way when editing
  them.

Talking about real names *in the conversation* is fine — that's the work. The boundary is
what gets committed here.

## Work through the store, not the filesystem

Write through `grimoire.store`. Hand-writing markdown into the store looks easy and is a
reliable way to produce files the app then disagrees with — slug collisions, frontmatter
quoting, `{{char}}` baking, and newline translation are all already solved:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(REPO) / "backend" / "src"))

from grimoire.store import characters, entities, greetings, tags, voice_anchors
from grimoire.store.paths import home

root = home() / "worlds" / world_id
```

- `entities.create_entity(root, kind, name, body=..., keys=..., owners=...)` —
  `kind` is one of `entities.ENTITY_KINDS`; returns the slug it allocated.
- `entities.update_entity(root, kind, eid, body=...)`, `entities.list_entities(root, kind)`,
  `entities.read_entity(root, kind, eid)`, `entities.delete_entity(root, kind, eid)`.
- `greetings.create_greeting(root, name, character, version, body=..., present=[...],
  requires_tags=[...])` — bakes `{{char}}` at write time, which is why you should not
  hand-roll this.
- `greetings.set_edges(root, gid, leads_to=[...])` for plot-map chains.
- `tags.add_tag(root, display_name)` → tag id; `tags.read_tags(root)`.
- `voice_anchors.write(root, char_id, text)`; `voice_anchors.anchorless_ids(root, ids)`.
- `characters.read_card(root, cid, vid)` → the card dict; `characters.list_characters(root)`.

`atomic.write_text` deliberately keeps the platform's newline translation, so a store written
on Windows stays CRLF and you never think about it. Bypassing it with explicit `newline=`
rewrites whole files and buries your actual edit in the diff.

## Before anything: read the precedent

The store is a git repo with its own history. Resolve its root the way the app does
rather than assuming `~/.grimoire` — `GRIMOIRE_HOME` or the bootstrap pointer may name
somewhere else (CLAUDE.md, "the store"):

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
python .claude/skills/world-card-integration/scripts/inspect_cards.py <world-id> --out <scratchpad>
```

It writes one readable dump per character and prints how every lorebook entry compares to
what the World already holds. Read the dumps properly — the description carries a
relationships block in which each character describes every other character in the batch, and
that block is the difference between eight independent entries and one integrated group.

Skip `creator_notes` when reading; it is mostly a wall of CSS. The inspector already extracts
the two useful things from it — the card's own tagline, and its label for each greeting.

## Step 2: Reconcile the lorebook against existing lore

Every entry is one of four things, and the inspector tells you which:

**Identical** to an existing entry. Nothing to do; this is most of them.

**New.** Keep it, after checking its `keys` are sensible.

**Differing, and the card's text is fuller.** Merge the new material into the existing entry
with `entities.update_entity` and delete the duplicate. Aim for one good paragraph — you are
editing an encyclopedia, not appending a changelog.

**Differing, and the existing entry is already better.** This is the trap. The import
re-derives from the card, so an entry a previous integration *improved* comes back as a thin
duplicate. If the existing entry strictly subsumes the newcomer, delete the newcomer and say
so in the commit message.

And one that is none of the four: **genuine homonyms**. Game lines reuse names across
subsystems — the same word is a spirit power in one and a personality template in another.
Two entries with one name are not automatically a duplicate. Diff before deciding, and record
the call in the commit so the next run doesn't relitigate it.

## Step 3: Rewrite the cast entries

The World probably already holds thin entries for the new characters, written from a roster
summary before the cards existed. Find them and rewrite each against its card, following the
cast format the World already uses — `references/house-formats.md` documents the one this
store uses, but read a neighbouring entry first, because the format is the World's, not this
skill's.

Write what the card supports and no more. Where the card contradicts the old entry, take the
card and **note the correction in the commit message** — corrections are the most useful thing
in it, because they are what a reader of the World would otherwise still get wrong.

Cross-reference by name. If the cards establish that one character is another's closest
friend, or that a third is immune to a fourth's manipulation, those facts belong in both
entries. A cast that refers to itself reads as a world; a cast of independent stat blocks
reads as a download.

## Step 4: Define the mechanics the cards use

Cards lean on named powers — disciplines, arts, gifts, whatever the line calls them. Check
each against existing lore and write an entry for every one the World lacks. This is usually
the largest group of new entries, and it is what makes the cast's abilities legible to a
reader who doesn't own the sourcebook.

Two things keep it honest:

- **Write only what you can support.** A short accurate entry beats an invented rules
  citation. Say what the power does and who in this World wields it; leave the dice out.
- **A name collision gets a sentence, not a new slug.** When a new power shares its name with
  an existing entry, extend that entry rather than forking it. Someone looking the word up
  wants both meanings in one place.

Also write the connective entries the new material assumes but never defines: the
organisation that recruited them, the mechanism they use to do the job, the named NPC who
runs the place. These are cheap and they are what turns a cast into a setting.

## Step 5: Groups, locations, items

Take these from the description *and* the greetings — greetings are usually where a setting's
furniture actually appears, with a name and a reason to exist.

The bar is load-bearing, not merely mentioned. A recurring workplace, a signature possession,
a site with a job attached: yes. A shop named once in passing: no, unless a cast entry already
leans on it.

Prefer one rich group entry naming the whole cell and what each member does over one thin
entry per faction — the group entry is where a reader learns how these people relate. And
extend existing entries the new material touches: if a card reveals that an established
organisation ran a second programme, that belongs in the organisation's entry.

## Step 6: Tags

Read the tag vocabulary. If every new greeting presupposes the player character is something
the vocabulary has no word for, add one tag and gate the greetings on it. Don't add tags for
things a single greeting mentions — this is a player-character vocabulary, not a keyword
index.

## Step 7: Greetings

One greeting per `first_mes` and per entry in `alternate_greetings`. The body is the card's
text **verbatim** — keep `{{user}}`, keep the character's voice, keep the typos inside
dialogue. Fix only extraction artifacts: stray backslashes, doubled whitespace, `\r`.

Titles are yours. Cards routinely label every intro the same generic thing; the World names
greetings for what happens in them. Use the card's own scenario blurb to understand the scene,
then title it the way the World's existing greetings are titled.

## Step 8: Plot-map chains

Add a chain only where a later greeting explicitly refers back to something an earlier one
established — "the one assigned to my mentorship", "after last night", "rookie lesson number
one". Two greetings featuring the same character is not a chain.

Be conservative on purpose: a wrong edge silently forces a story order the text doesn't
support, and nobody notices for months.

## Step 9: Voice anchors

Every new character needs one — `voice_anchors.anchorless_ids(root, ids)` tells you who is
missing. Follow the shape the World's existing anchors use; typically a few lines of speech
habits with one short quoted line from the card, chosen because it demonstrates a habit rather
than because it is memorable.

Aim at what someone would need to *reproduce* the voice — sentence length, what they open
with, what they refuse to say, where an accent surfaces — not at their personality, which the
cast entry already covers.

## Step 10: Verify, then commit

Referential integrity is already implemented; don't reimplement it:

```bash
python - <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, "backend/src")
sys.path.insert(0, ".claude/skills/populate-world-content/scripts")
from populate_world_content import verify_manifest
from grimoire.store.paths import home
print(verify_manifest(home() / "worlds" / "<world-id>"))
PY
```

That checks greeting→character, `requires_tags`→tag, `present`→character, plot-map edges→
greeting, and `owners`→entity or character. Then the check it cannot do for you:

```bash
git -C "$STORE" status --porcelain worlds/<world-id>/ | grep '^ M'
```

Every modified file should be one you deliberately edited. A list longer than your intent
almost always means something rewrote line endings or trailing bytes across a directory —
revert the strays with `git checkout --` before committing rather than shipping the churn.

Then commit in the store, in the house style its previous integration commits establish: a
subject naming the World and what arrived, a paragraph on who these characters are and how
they relate, then sections for what you merged, what you deleted and why, what you corrected,
and which judgment calls you made. The user reads that message to check your work, so the
reconciliations and corrections are the substance — not the file counts.

## Reference

- `references/house-formats.md` — the entry shapes this store uses, with invented examples
- `scripts/inspect_cards.py` — dump cards to readable text, diff their lorebook against lore
- `.claude/skills/populate-world-content/` — the bulk import this skill is the follow-up to
