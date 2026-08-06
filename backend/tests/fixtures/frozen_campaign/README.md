# The frozen campaign

A complete grimoire store, frozen. `test_frozen_campaign.py` copies `home/` into
`tmp_path`, points `GRIMOIRE_HOME` at the copy, and reads it — see that module's
docstring for what each of its tests is for.

## Why a checked-in store at all

Every other backend test builds its fixture *with the code under test*, moments
before reading it. That arrangement can never catch a change which breaks
reading data an older version wrote: the setup would simply write the new shape
too, and the test would stay green while every existing user's library broke.
`home/` is the only store in this repository that today's code did not write.

## What is in it

One world (`saltmarch`) and one campaign (`the-drowned-ledger`), sized to touch
most store modules rather than to be realistic:

- world: two characters (one with two versions), a location, a keyed lore entry,
  two greetings joined by a plotmap edge, a tag vocabulary
- campaign: a PC, campaign-scoped lore *owned by that PC*, a bound mechanics
  module (`d20-basic`) with one filled sheet, a dossier, a chronicle with a
  timeline, an open and a closed plot thread, an open commitment, a feeling and
  a bond
- scene 1 — current id grammar, cast, location, two messages
- scene 2 — **the pre-migration real-date filename** (`2026-01-02-the-long-quay.md`),
  referenced by the chronicle, a plot beat and an appearance, so
  `migrations.migrate_scene_ids()` has both a rename and a repoint sweep to do

All names are invented placeholders reusing the ones the codebase already uses
for fixtures. No real campaign content is in this repository, and none may be
added here — see CLAUDE.md, "Privacy".

## The two rules

**`home/` is never regenerated.** Not to add coverage, not to make a test pass.
Its entire value is being old. `build.py` records how it was minted and can mint
a *new* fixture at a newer on-disk format — as a sibling directory, beside this
one, never over it. A rebuild is deliberately not byte-identical anyway: sheet
`gen` tokens and connection `rev` stamps are minted per write, so a "refresh"
would show up as a diff even where nothing changed.
`test_the_builder_still_mints_a_fixture` keeps the script honest by running it
into a scratch directory — it never touches `home/`.

**`snapshot.json` is regenerated deliberately.** It is the expected output of
the read-only sweep, so it legitimately moves when a template or a render
changes on purpose. Then, and only then:

```
cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep
```

The regenerated snapshot belongs in the same commit as the change that moved it,
with its diff reviewed. Regenerating it to turn a red test green throws away the
only thing standing between a store refactor and a silently broken read — and
the harness's semantic assertions (`test_the_assembled_prompt_still_carries_the_campaign_state`,
`test_owned_lore_stays_in_the_scene_its_owner_is_in`) exist precisely so that
doing it anyway still leaves a failing test behind.
