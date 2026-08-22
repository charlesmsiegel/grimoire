# Moving records the other way: promote, push, demote (#52, #53, #60)

Campaign↔world movement is one-way today. `store/sync.py` compares three
hashes per ref — world / base (`sync.md`) / mine — and `accept`/`reject` only
ever advance the campaign. Nothing carries a record the other way, so a
location invented at the table, an NPC who walked on mid-scene, or a campaign's
better wording of a library entry all stay where they were written.

This adds the reverse direction as three explicit, user-driven operations, and
one missing create route.

## What the overlay changed about the question

All three issues were written before the copy-on-write overlay landed, and
that rebuild moves two of their premises:

- **A world record reaches a campaign without being copied.** A campaign
  materializes a record only when it diverges; everything else resolves
  through to the world live (`store/overlay.py`). So a promoted record does
  *not* need to be offered to sibling campaigns as a sync `"new"` — they read
  it the moment it lands in the world. (`sync.incoming` never emits `"new"`
  at all; the key in `campaigns_for_world`'s counter is vestigial.)
- **"Dependent campaigns" means every campaign of the world**, not just the
  ones holding a manifest ref. Under the overlay, a campaign with no copy is
  the one that depends on the world record *most* — it has nothing else.

`overlay.forget_world_record` already sweeps what a world-side delete strands,
and its docstring names #52 as the issue that carries the dependents design.
Demote is therefore built on top of that sweep rather than beside it.

## The three operations

Every one of them is explicit. Nothing here ever runs on its own, because all
three relax the invariant that library drift becomes a campaign-local override
and never a library edit (#113).

### Promote — a campaign-local record becomes library content (#52, #60)

Precondition: **the world holds no record under that id.** That, not the
absence of a manifest ref, is the gate — see *Crash ordering* below.

Copy the campaign record into the world byte-for-byte, then record
`manifest[ref]` as the hash of the bytes copied. `mine == world == base`
holds, so the promoting campaign sees nothing incoming, and later world edits
reach it as ordinary updates. A detached ref (`detached.json`) is re-attached:
the campaign's copy now *is* the world record's ancestor, which is exactly what
detachment denied.

Applies to the flat kinds and to actors. An actor carries its meta, every
version card, `assets/`, and the two sidecars the overlay declares world-level
identity (`tagline.md`, `voice_anchor.md`). It does not carry the
campaign-local ones (`dossier.md`, `state.md`, `voice_drift.md`).

### Push — a campaign's override is saved back to the library (#53)

Precondition: the world holds the record, and the campaign holds a
materialized copy with a base.

If the world moved since that base, the push is a conflict — the mirror of
sync's pull conflict — and is refused unless the caller forces it. Otherwise
the campaign's bytes become the world's, and the base advances to match, which
clears the override. Sibling campaigns that hold their own copies see an
ordinary `update`; siblings without one just read the new text.

Flat kinds only. A version-locked actor's base lives in `appearances.json`
rather than `sync.md`, and pushing one means minting a *new world version*
(#53's Option B) — a different operation, refused here with a message that
says so rather than silently doing the wrong thing.

### Demote — a library record becomes campaign-local (#52)

`dependents()` reports every campaign that would notice, and whether each one
holds its own copy. `demote()` optionally copies the record down into the
campaigns that would otherwise lose it, then deletes the world record and runs
the existing `forget_world_record` sweep — which detaches the campaigns that
hold copies and drops their now-meaningless bases.

Copy-down materializes through `overlay.materialize_entity`, so it reuses the
base-recording discipline rather than re-implementing it. A campaign that
tombstoned the record is not given one back.

Flat kinds only, per #52's own v1 boundary.

## Crash ordering

The two writes each operation makes cannot be made one, so each has to fail
onto the harmless side — the same argument `overlay._recorded_base` makes for
#247, applied in both directions.

**Promote writes the base first.** The residue is *base, no world record*:
`sync.incoming` skips it (`world_h is None`), the record still reads
campaign-side, and a retry finds no world record, so it completes and
overwrites the base. Self-healing. The other order strands the record — a
world copy no manifest ref names, which promote then refuses as a collision
and push refuses as having no base. Nothing gets it out of that.

Gating promote on the *world's* state rather than on the manifest ref is what
makes the retry work; a ref-based gate would reject the very state the crash
left behind.

**Push writes the world record first.** Its residue is *world updated, base
stale*, which surfaces as a conflict whose two sides happen to be identical —
noisy, and rejecting it advances the base and clears it. The other order
records a base for content the world does not have, and sync then offers to
overwrite the campaign's edit with the world's older text.

**Demote copies down before deleting.** A crash mid-way leaves campaigns that
already have their copy plus a world record still standing — the state before
the demote, for the campaigns not yet reached.

## What the copy-down has to carry

Assets overlay *per file*, from the world, which is why `materialize_entity`
deliberately does not copy them: a campaign that diverged on wording should go
on reading the world's pictures rather than forking them.

That reasoning ends at demote. The delete takes the world's record directory
with it (`overlay.forget_world_record`), so a copy-down that moved only the
text would leave every dependent campaign holding a record whose art is gone
for good, silently. So the art goes down too — for every dependent, including
the ones that already have their own text.

Two rules govern that copy, and both are the overlay's own rather than this
feature's: a file the campaign already has is never overwritten (the per-file
overlay rule), and a tombstoned asset stays gone (`image_root` checks the
campaign's file *before* the tombstone, so a blind copy would hand back exactly
the image the user deleted).

## Referential integrity

A greeting names the character it belongs to. Promoting one whose character is
still campaign-local would publish a library greeting pointing at nothing, so
that is refused, naming the character to promote first.

A *detached* character is refused for the opposite reason: the world does hold
that slug, but detachment is the statement that whatever holds it is a stranger
to this campaign's character of the same id (#225). Filing the greeting against
that stranger is worse than a dangling reference, because it reads as working.

## Two guards every move takes first

- **The id, before it is joined onto a world root.** `kind` and `eid` arrive as
  path parameters and a path parameter can carry an encoded slash. The
  campaign-side readers do refuse an unsafe id, but relying on that would make
  these functions safe only for as long as the order of their own checks never
  changed — and everything here writes to the world, which no campaign resolver
  guards.
- **The world, before anything writes into it.** `campaigns.read.world_root_of`
  answers with a path whether or not a world is there. Every move then does
  `mkdir(parents=True)` on the way to its write, which would rebuild a deleted
  world's directory around the record: a tree with no `world.md`, which nothing
  lists as a world and no route can reach, holding the only copy of a record
  the campaign had just recorded a sync base for.

`demote` also refuses a `target` that is not a dependent rather than filtering
to nothing — the delete runs regardless of `target`, so a typo otherwise meant
"copy this down nowhere, then take it away from every campaign", the most
destructive reading of the request, chosen silently.

## Locking, and the one gap left open

`demote` mutates several campaigns, so it takes `locks.hold_all` over the whole
dependent set — the only sanctioned way to hold more than one campaign lock, and
the one that sorts (#267). `promote` and `push` take `campaign_lock(cid)`,
because `sync.md` is rewritten whole and two moves on one campaign would
otherwise each read it and each publish it back, the later dropping the other's
ref.

What that does **not** close: two *different* campaigns pushing the same world
record hold different locks, so nothing excludes them from each other. The
conflict check is re-taken immediately before the write, which narrows the
window to the gap between the two, and the remaining race is documented here
rather than fixed. Closing it needs a world-scoped lock, and this store has
none for anything — world create, rename and delete are all unserialized today
— so introducing one for a single call site would add a second lock domain and
a new ordering pair to deadlock on.

`worlds.touch` sidesteps the same problem differently: it re-reads before
writing and skips if the file moved, because it only carries a sort key and
losing one is cheaper than reverting somebody's rename.

## Deleting what a campaign created

`POST /campaigns/{cid}/characters` needed a matching delete, and there wasn't
one — the world side had always had it, and version-delete refuses the last
version, so an NPC invented by mistake could not be removed at all.
`overlay.delete_actor` is the actor twin of `delete_entity` and makes the same
three distinctions: an inherited actor is tombstoned, a campaign copy is
dropped and tombstoned, and an emergent one is simply removed with no tombstone
because there is nothing to hide. The appearance record goes with it either
way — it holds a version lock and a per-version base, and `_actor_incoming`
prefers it to `sync.md`.

## Not in scope

- Pushing a version-locked actor (#53 Option B) — refused explicitly.
- Demoting actors — #52 limits v1 to the flat kinds.
- Plot-map edges do not travel with a promoted greeting; the campaign's map
  keeps them and the world's is untouched.
