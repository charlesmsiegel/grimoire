# Reconcile lore, cast and mechanics

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
