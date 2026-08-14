# The scene ledger

Issues #88 (persistent per-campaign curated scene ideas) and the unfinished
half of #89 (the new-scene picker mixing *saved* ideas with fresh LLM
suggestions and greetings).

#89's other halves shipped in #318 as its Option B: the ↻ Regenerate control,
the free-text "Your own" card, and free-text direction. That PR deliberately
persisted nothing, so the "mixing saved ledger ideas" half of #89's title
stayed open behind #88. This is both, taken as #88's Option A.

## Problem

A generated scene idea was ephemeral. `POST /scene-suggestions` built a
snapshot, asked the model for openings, handed them to `SceneIdeaPicker` and
kept nothing — so every open of the chooser regenerated from scratch, and every
idea the reader liked but did not pick that minute was gone. ↻ Regenerate made
it worse in one specific way: it was the only way past a card, and it destroyed
everything it replaced.

Greeting ideas already had the opposite property. `played.json` gives each one
a durable lifecycle — played / completed / skipped — and `available_greetings`
computes startability from the plot map and the player's tags.

So the campaign held two kinds of scene idea with nothing in common: one that
could not be kept, and one that could not be anything else.

## Scope

In: a stored ledger for LLM-sourced and user-authored ideas; a composed,
never-stored greeting half; the route surface for both; and the picker's Saved
group, with Save affordances on generated cards and on the typed box,
dismiss/restore, and mark-used on pick.

Out: a management screen for the ledger (#88 defers it to a later list/detail
editor); per-slot regeneration (#89 Option C, considered and not taken);
adapted-greeting first posts (#91).

## Decisions

### Not `ledger.json`, not `/campaigns/{cid}/ledger`

#88 proposed both names. `GET /campaigns/{cid}/ledger` already exists and is
the **continuity** ledger — facts, commitments, plot threads and relationships
joined in one read (`routes.campaigns.get_ledger` → `LedgerView`). The store
module is `store/scene_ideas.py`, the file `<campaign>/scene_ideas.json`, the
routes `/campaigns/{cid}/scene-ideas`. Same feature, unambiguous names.

### Greetings are composed, not seeded (Option A, not B)

`playing.greeting_ideas` derives a ledger row per greeting at read time:
played or completed → `used`, skipped → `dismissed`, otherwise startable →
`active`. A greeting that is neither marked nor startable is omitted — it is
not an idea anyone can act on, and its gating is the plot map's business.

Materializing greetings into `scene_ideas.json` (#88's Option B) would
duplicate `played.json` and a plot map whose gates and unlocks move underneath
a snapshot, and would need a migration for every existing campaign. Composing
costs a merge at read time and buys the guarantee that the two can never
disagree. Status writes against a `greeting:<gid>` id delegate to
`playing.mark_greeting`, so the marks stay the single source of truth in both
directions.

### References are validated twice

An idea is durable and a campaign is not: the character it casts can be deleted
and the location it names can be renamed between the day it was saved and the
day it is picked. So `POST /scene-ideas` validates on write (`suggest.
valid_refs`) and `GET /scene-ideas` validates again on read
(`suggest.validate_ideas`), both through the validators that already police a
fresh suggestion. The stored record keeps whatever it was given — a dangling
id is data, not an error, and the campaign can get that record back — while the
read refuses to hand a picker an id that would fail the moment it was used.

Each idea carries its own `pcless`, which decides which player tokens are legal
for it, exactly as `offscreen` does for a fresh suggestion. One read can hold
ideas of both modes; the picker shows the ones matching its own.

### The store module is a leaf

`scene_ideas` stores and coerces; it validates nothing and composes nothing.
That is not tidiness — `suggest` reaches `playing`, which reaches
`scenes.lifecycle`, which reaches `scene_refs`, which has to reach *here* for
`used_scene` repointing. Importing the validators into the store module closes
that cycle, and `test_import_guard.py` says so. The split puts validation in
`suggest`, greeting composition in `playing` (beside the marks it derives
from), and the join in the route — which is where `get_ledger` already composes
the other ledger.

### `used_scene` is provenance, not a link

Scene renames are followed (`repoint_scenes`, registered in
`scene_refs.repoint`, which routine first-date stamping makes necessary).
Deletions are not: `delete_scene` recycles the id, so a used idea can end up
naming a scene that is not the one it became. Reviving the idea on delete is
the alternative and it is worse — a cleanup would put a played idea back on the
list.

## The picker

A **Saved** group above the greeting and generated ones, showing active entries
for the current mode, newest first, with the same 4-slot budget the other
groups share and a "show all" toggle past it. Picking one emits a draft
carrying its ledger id; `SceneConfirmForm` marks it used as the last step of
its create sequence, against the scene's *final* id (the date stamp renames).
A failed mark is soft — the scene is real — but reported, because an idea
silently left active is indistinguishable from one deliberately kept.

Saving happens in two places: a **Save** button beside each generated card, and
**Save for later** beside the typed box. The typed path skips the scene-intent
extraction deliberately — that call exists to pre-fill the confirm form, and
this path is not going there; the date and place are read from the text on the
day the idea is actually picked. An idea saved with no title is named from the
head of its premise, since the typed box has no title field.

Dismissed entries move behind a toggle with a Restore button, which is the only
route back until the management surface exists.
