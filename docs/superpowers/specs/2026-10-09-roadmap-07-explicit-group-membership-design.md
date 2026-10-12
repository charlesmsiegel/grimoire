# 07. Explicit group membership

**Status:** Approved for 07-S1 planning; human approval and actual Codex gate recorded on 2026-10-11.
**Date:** 2026-10-09
**Review record:** [Codex findings and corrections](../validation/2026-10-11-spec-07-review.md).
**Roadmap:** 07 in `ROADMAP-CHECKLIST.md`. Lane: "Now" (no hard upstream);
feeds the cache lane at 08 and the retrieval lane at 09 and 11.
**Baseline:** Drafted against `main` at `35c1fb7`; implementation worktree
rebased onto `origin/main` at `f816921c8` on 2026-10-11.
**Reconciles:** the bundle draft `07-explicit-group-membership.md` (2026-10-06,
written against `7f80c42`); the "Group membership" non-goal of
`2026-10-05-lore-activation-controls-design.md` (line 84); and the parking of
graph node families in `2026-10-04-continuity-capstone-design.md` (§19.2, §31
ruling f3, §33), which this spec lifts for one family, groups, and says so.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 03-C1 composite keys over collection digests and non-file inputs | 03 | The persistent tier of the inverse index (section 7.4): a key over the world's and the campaign's `groups/*.md` digests plus the tombstone ledger | Soft (S). Section 7.3's in-process tier answers every caller without it |
| 03-C2 liveness by construction | 03 | An edit to any group file moves the key, so a stale inverse is unreachable rather than invalidated | Soft (S), same reason |
| 03-C3 the `materialized` record | 03 | Lets 05 rebuild a hot inverse eagerly after an external edit | Soft (S). Without it the inverse is rebuilt lazily on the next read |
| Continuity graph (capstone Slices A-G, landed) | not a roadmap contract | Section 10 adds one node family and two edge kinds to `store/continuity/graph.py` | Existing code, not an edge |

There is no unlanded hard upstream. This adds no model call: membership
proposals use the existing absorb extraction. Its model-derived structured
schema uses the inference facade and portable-schema contract already landed
on `origin/main` (01f), not a new provider path.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for | Hard or soft |
|---|---|---|---|
| 07-C1 `members` on the group record, with overlay and sync semantics | 11 | Group overrides: the stored fact 11's actor-perspective rules read, with its secrecy and "membership is not knowledge" rules | Hard (H) for group overrides |
| 07-C2 inverse membership, with a digest and routes | 08 | Actor -> groups without re-reading prose; a digest to key derived documents on | Soft (S) |
| 07-C2 inverse membership, with a digest and routes | 09 | Co-membership and group lookups as structural recall signals | Soft (S) |
| 07-C3c retrieval projections (`scene_groups`, `co_affiliates`, `prompt_visible`) | 08 | Per-scene "groups relevant", keyed on the scene's projection, gm-only filtered | Soft (S) |
| 07-C3c retrieval projections | 09 | Co-affiliation candidates and the visibility rule for prompt-bound text | Soft (S) |
| 07-C3c retrieval projections | 11 | Visibility and "membership is not knowledge, not history" rules | Soft (S) |
| 07-C3a absorb join/leave proposals | (no roadmap consumer; play) | Membership that changes in the story reaches the record through review | n/a |
| 07-C3b graph group nodes | (no roadmap consumer; UI) | `member_of` and `leads` edges on the Story Graph payload | n/a |
| 07-C3d a present member makes their group present for lore | (no roadmap consumer; prompt) | A group whose member is in the scene opens the gate on lore the group owns | n/a |

---

## 1. Current state (reconciled against main)

### 1.1 How a group is stored

A group is one of the five generic entity kinds
(`store/entities.py:20`, `ENTITY_KINDS`), a flat markdown file at
`<root>/groups/<id>.md` with frontmatter and a body. `<root>` is a world or a
campaign. Beside the file sits a record directory, `<root>/groups/<id>/`, which
holds assets and, campaign-side only, the per-group state sidecar
`groups/<id>/state.md` (`store/groupstate.py:1-7`, written by absorb).

Typed per-kind fields live in `store/entity_schema.py`. For groups
(`entity_schema.py:145-151`):

```python
"groups": (
    {"key": "group_type", "label": "Type", "widget": TEXT},
    {"key": "leader", "label": "Leader", "widget": REF, "kinds": ("characters", "pcs")},
    {"key": "headquarters", "label": "Headquarters", "widget": REF, "kinds": ("locations",)},
),
```

There is **no members field**. The lore-activation spec says so as a non-goal
(`2026-10-05-lore-activation-controls-design.md:84`: "Groups record `leader` and
`headquarters`, not members").

The machinery a members field would ride on already exists and is general:

- A `ref` field is a single frontmatter line of `<kind>:<id>` refs,
  comma-separated when the spec says `multi` (`entity_schema.py:27-47`,
  `parse_refs` at `:214-226`). `creatures.habitat` is the one `multi` ref today
  (`:158-159`).
- The save boundary checks format, never existence: a string, at least one ref,
  one ref unless `multi`, each ref of an accepted kind with a `referenceable` id
  (`_valid_ref_value`, `:312-339`). Existence is deliberately not checked
  (`:72-74`), and deleting a target leaves refs dangling on purpose
  (`:49-66`).
- A key the table newly claims may already hold a legacy value; it is left
  where it is, refused only if a request tries to set it (`:76-92`).
  `EntityEditor` sends only the fields that changed
  (`frontend/src/components/EntityEditor.tsx:417`, `:817`), so an untouched
  legacy value is never resubmitted.
- The frontend mirrors the table by hand as `ENTITY_FIELDS`
  (`frontend/src/api/types.ts:1051-1075`). Only the kind list has a drift test
  (`backend/tests/test_entities_store.py:337-361`); the field table has none.

### 1.2 How actors reference groups today

They do not, structurally. Neither a character card nor a PC persona carries a
groups or affiliations field (no `affiliat` or `faction` field anywhere in
`backend/src`). Three things come close, and none is membership:

- **Prose.** The house format for a group writes its roster into the body:
  "Multi-paragraph is right when the group has a roster -- the second paragraph
  is where each member gets a clause saying what they do"
  (`.claude/skills/world-card-integration/references/house-formats.md:95-96`).
  This is the only place membership exists today, and nothing can read it
  without a model.
- **`owners:` on the group record.** An owner gates activation and per-actor
  knowledge (`context/activation.py:390-399`, `context/actor.py:56-76`). It
  says what puts an entry in the prompt, not who belongs; a group owned by one
  member is a hack that cannot name the rest.
- **`leader`.** One actor, and the structural presence rule below reads it.

### 1.3 Where group fields already reach composition

`entities.rewrite_ref_fields`'s docstring says "these fields never reach a
prompt (the entity-kinds design settled that)" (`entities.py:420`). That is
true of their *values* (nothing renders "Leader: Seraphine") and no longer true
of their *effect*. World-info activation reads them as structural presence
(`context/activation.py:107-111`):

```python
_STRUCTURAL = {
    "items": (("holder", "held_by", True),),
    "groups": (("leader", "led_by", True), ("headquarters", "headquarters", False)),
    "creatures": (("habitat", "habitat", False),),
}
```

A group whose leader is present, or whose headquarters is the current location,
is itself present, which opens the owner gate on lore the group owns
(`structural_presence`, `:361-372`). `world_state` feeds the parsed refs in
(`_STRUCTURAL_FIELDS`, `context/world_state.py:41`, used at `:242-244`). This is
the seam section 11 extends. The stale docstring is corrected in the same
change, together with the same docstring's other stale claim, "the editor sends
every declared field on every save", which `EntityEditor`'s `changedFields`
contradicts (`EntityEditor.tsx:158`, `:418`, `:817`).

### 1.4 Campaign overlay and sync

A campaign reads a group through the overlay: its own file wins, else a
tombstone means absent, else the world's file (`overlay.py:1-25`,
`list_entities` at `:697-704`). Any campaign write to an inherited record
materializes a whole-file copy first and records its sync base
(`overlay.update_entity`, `:786-794`; `_materialize_flat`, `:650-669`).

Sync compares three whole-file hashes per ref (`sync.py:1-16`): world, base,
mine. A change on both sides is a `conflict`, and the only two resolutions are
take-world (`accept`, which drops the copy) and keep-mine (`reject`, which
advances the base) (`sync._advance`, `:214-287`). There is no field-level
merge.

What the review panel can *show* is narrower than what the hash covers.
`sync._entity_blob` sends only `{name, body}` (`sync.py:57-62`), and the panel
says so when every compared field matches: "the record's keys, owners, or
secrecy -- front matter this view is not sent"
(`frontend/src/components/IncomingReview.tsx:84-97`). Today a world-side change
to `leader` arrives as a pending change whose visible fields are identical.
Membership would make that the common case, not the corner one.

`promote` and `push` copy the whole record into the library. They check a
greeting's character refs (`sync._require_world_character`, called at `:762`
and `:1093`) and nothing else; a group's `leader` naming a campaign-made actor
is published as is.

### 1.5 Absorb and groups

Absorb touches groups three ways, none of them membership:

- **Group state** (`group_state_edits`): the campaign-local snapshot sidecar,
  five prose fields (`groupstate.LABELS`). Parsed at
  `absorb/parse.py:218-226`, staged at `absorb/materializer.py:986-1009`,
  applied at `absorb/apply.py:205-206`.
- **Body appends** (`lore_edits`) onto any entity kind, groups included
  (`materializer.APPEND_KINDS`, `:132`), written through
  `overlay.update_entity`, which materializes the group into the campaign
  (`apply.py:333-334`).
- **New groups** (`new_lore` with `kind: "groups"`, `parse.NEW_LORE_KINDS`,
  `:177`), created with a name, body and keys and no fields.

The model sees groups through `absorb.group_snapshot`
(`absorb/snapshots.py:93-117`): one line per non-gm-only group,
`- groups/<gid> (<Name>): <state>`, rendered under `Groups:`
(`templates/absorb/user.j2:43`). The prompt tells it a group's record "is what
that group permanently IS" and its state is "where it stands this week"
(`templates/absorb/system.j2:10-11`). Nothing asks who joined or left.

### 1.6 The Story Graph

`store/continuity/graph.py` builds one deterministic payload of nodes and edges.
`NODE_KINDS` (`:86-87`) excludes groups and facts, which the capstone made
optional (§19.2) and then parked with every other new node family (§31 ruling
f3; §33's stopping rule). `test_continuity_graph.py:112-130` pins the tuples,
including `set(NODE_KINDS) == set(canon.KIND_OF_PREFIX.values()) - {"group",
"fact"}`. The ref grammar already has `groups:` (`continuity/canon.py:42`).
Actor nodes exist only when some edge names them (§19.2), and labels come from
roster reads outside the lock hold (`_ROSTERS`, `graph.py:339-343`, the
location listing included).

### 1.7 What the frozen campaign holds

`backend/tests/fixtures/frozen_campaign/home/` has no `groups/` directory at
all (README, "What is in it"). Every reader here therefore sees an empty group
listing for it, and its `snapshot.json` must not move. The README's rule also
settles a temptation in advance: membership coverage is not added to `home/`.

### 1.8 Where the draft disagrees with the code

| Draft said | Code says | Consequence here |
|---|---|---|
| Fields never reach prompts | `leader`/`headquarters` drive structural presence | Membership extends that rule (section 11) rather than inventing one |
| Graph should add `member_of` | Group nodes are parked by capstone §33 and pinned by a test | This spec lifts the parking for groups explicitly (section 10) |
| Campaign divergence uses "normal" copy-on-write | Normal CoW is whole-record, and the sync panel cannot show field diffs | Section 5 adds field visibility to sync; field-level merge is an open question |
| Inverse "invalidated" on external edit | 03 makes stale artifacts unreachable, not invalidated | Section 7.4 keys the inverse on content digests |
| Removing an absent member is "a low-value row" | The materializer drops rows whose before equals after | Such rows are never staged (section 9.3) |

Three of the draft's items are dropped, each deliberately: "group-related
threads and commitments" as a retrieval signal (draft section 9) needs a
thread-to-group link nothing stores, and building one is not membership; "an
actor node lists groups" (draft section 8) is served by `member_of` edges and
the node detail rather than a field on the actor node; and "continuity
reconciliation" (draft section 1) would add a candidate kind to a capstone that
§33 holds closed, for no consumer that asked.

---

## 2. Goal (and what is explicitly not the goal)

**Goal.** Make "who belongs to this group" a structured, authoritative fact on
the group record, readable without a model, so that:

1. a person editing a world or campaign can record it in the normal list/detail
   editor;
2. any reader can ask the inverse, "which groups is Mara in", cheaply and
   consistently, with or without 03's cache;
3. play can move it, through the absorb review, never through a silent write;
4. the Story Graph, world-info activation, and the retrieval specs (08, 09, 11)
   consume one projection with one set of visibility rules.

**Not the goal.**

- Membership is **not knowledge**. Belonging to a group does not make an actor
  know the group's secrets, its lore, or what another member saw. That question
  is 11's (section 12.4).
- Membership is **current state, not history**. No join date, no "was a member
  in scene 10". Retrieval must not read it as historical evidence (section
  12.4).
- **No mining of prose** into members, at startup or anywhere automatic. An
  existing roster paragraph stays prose until a person (or a reviewed absorb
  proposal) records it.
- **No nested groups**, no group-of-groups membership.
- **No mechanics.** Faction resources, eligibility and clocks are 13's, and may
  read the inverse later.

---

## 3. Direction of authority: the group holds the list

Membership is stored **on the group**, as `members`. The inverse ("Mara's
groups") is derived, never stored. There is exactly one writable copy of each
membership fact.

Why the group and not the actor, in order of weight:

1. **The refs a group holds cannot move; the refs an actor would hold can.**
   Actors are never reclassified, and `lore_fields` already relies on that
   ("the five generic kinds can be reclassified and an actor cannot, so a ref
   to one of those would be a ref that can move", `store/lore_fields.py:36-38`).
   A group can be reclassified (`store/reclassify.py`). Stored on the group,
   `members` names stable actor refs and travels with the group file through a
   reclassify untouched. Stored on actors, every reclassify of a group would
   need a rewrite across actor cards, a sweep that does not exist
   (`rewrite_ref_fields` covers entity fields only, `entities.py:391-444`).
2. **One membership change touches one flat file.** A campaign edit to a group
   materializes one `groups/<id>.md`. A campaign edit to an actor materializes
   the whole actor directory with every version
   (`overlay.materialize_actor`, `copy_record_dir_down`), and actors sync
   through the version-locked flow. Membership is exactly the kind of fact that
   changes in play, so the cheap side has to be the writable side.
3. **The actor's record is a character card.** A card is the SillyTavern V3
   shape and is exported as such; a `groups` key in it would leak a
   grimoire-store relation into every exported card and be per-version, while
   membership is not a property of one version.
4. **The roster already lives there.** The house format puts the roster in the
   group's body (section 1.2). Authors look for members on the group.
5. **The existing field machinery is for entity kinds.** `entity_schema.FIELDS`
   validation, the editor's ref picker, sync, copy-on-write, reclassify
   handling and `create_world.py`'s field resolution (`backend/scripts/
   create_world.py:317-331`) all cover a group field with no new mechanism.

Two-way authority (a `groups:` list on actors too) is rejected: two copies of
one fact need a reconciler, and every reconciler in this store has been the
source of its own bug class (`sync`, `detached`, the scene-id repoint fan-out).

The cost of this direction is that the inverse needs a scan of every group. It
is one whole-kind listing, which the turn loop already pays per turn
(`world_state`, `:216-244`), memoized per file (section 7.3), and cacheable
across restarts (section 7.4).

---

## 4. The field (07-C1)

### 4.1 Declaration

One new spec in `entity_schema.FIELDS["groups"]`, after `leader`:

```python
{"key": "members", "label": "Members", "widget": REF,
 "kinds": ("characters", "pcs"), "multi": True},
```

Order is the form's order (group type, leader, members, headquarters), and the
frontend mirror in `types.ts` follows it key for key.

Stored as one frontmatter line, exactly as `habitat` is:

```markdown
---
name: Salt Circle
group_type: guild
leader: characters:seraphine
members: characters:mara, pcs:winifred, characters:seraphine
headquarters: locations:the-counting-house
---
```

**Why a single line and not a YAML list.** `entity_schema`'s module docstring
gives the reason for every ref field (`:30-33`): `entity_hash`, sync conflict
detection and copy-on-write cover a flat string for free, and a YAML list would
need its own answer to each. Nothing about membership changes that.

### 4.2 Validation

The save boundary is the generic one (`routes/entities.py:66-83`,
`entity_schema.invalid_values`), with no membership-specific rule:

- a string that parses to at least one ref (a blank string clears the field,
  per `invalid_values`, `:296-299`);
- every ref `characters:<id>` or `pcs:<id>` with a `referenceable` id;
- existence is **not** checked, for the reasons `entity_schema` gives
  (`:49-74`): a ref may be written before its actor, a campaign delete must not
  rewrite a world record, and a re-created slug picks its refs back up.

**Duplicates are not refused at the boundary.** The checkbox picker cannot
produce one, and refusing would be a new rule for every `multi` field
(`habitat` included). The reader collapses them (section 4.3), and the two
writers this spec adds (absorb apply, undo) never write one.

**A group is not a member.** `groups:` is outside `kinds`, so the boundary
refuses it. Nested groups are a non-goal.

### 4.3 The lenient reader

One parse, in a new read-only module `store/membership.py`:

```python
ACTOR_KINDS = ("characters", "pcs")   # == the spec's `kinds`; a test holds them equal

def parse_members(meta: Mapping[str, object]) -> tuple[str, ...]:
    """The member refs a group's frontmatter names: `entity_schema.parse_refs`
    on `members`, keeping only `<kind>:<id>` refs whose kind is an actor kind and
    whose id is `referenceable`, de-duplicated in first-seen order. Never
    raises; anything else in the line is not a member."""

def parse_leader(meta: Mapping[str, object]) -> str | None:
    """The first well-formed actor ref in `leader`, or None."""
```

Lenient on read and strict on write, the split `entity_schema.invalid_values`
already documents (`:290-295`). A legacy value such as a hand-typed
`members: Mara, Seraphine` produces no members in any derived view, stays in
the file byte for byte, and shows in the editor as two dangling chips, which is
what it is (section 13.1).

**Writers edit the raw token list, never the parsed one.** `parse_members` is
for readers. The two writers this spec adds (absorb apply, section 9.4, and
the membership undo, section 9.5) go through one helper:

```python
def edited_line(raw: object, actor: str, change: Literal["join", "leave"]) -> str:
    """`members` after one change, on `entity_schema.parse_refs(raw)` -- the
    raw tokens, stripped, in order. Every token the change does not touch is
    kept verbatim (a legacy name, a duplicate, a non-actor ref). A join appends
    `actor` only when `parse_members` does not already hold it; a leave removes
    exactly the tokens equal to `actor`. Joined with ", "."""
```

Rewriting from `parse_members` instead would turn `Mara, characters:seraphine`
plus a join for Winifred into `characters:seraphine, pcs:winifred`, deleting
the hand-written `Mara` with no review row saying so. The cost of the raw rule
is that whitespace around commas is normalised to ", " on a write, which
changes no token.

### 4.4 Leader and members are independent

`leader` stays its own field and is never written by anything this spec adds:

- naming a leader does not add them to `members`;
- a leader need not be a member (a patron, a figurehead, an outside handler);
- removing a member never touches `leader`, and vice versa.

These are fiction facts the author controls. Derived views report **roles**
(`member`, `leader`, or both) rather than folding one into the other, and
consumers choose. Section 7.2's `affiliated` is the one helper that unions
them, for the retrieval signals where the distinction does not matter.

So **`members` excludes a leader nobody listed**, and a consumer that means
"everyone in the Salt Circle" must say which set it means. A rule that expands
a group audience (11's group overrides) reads `members_of` and silently leaves
out an unlisted leader; reading `affiliated` includes them. 07 recommends
`affiliated` for audience expansion and leaves the choice to the consumer
(section 12.4).

### 4.5 What reclassify, delete and rename do

- **The group is reclassified** (groups -> lore, say): `members` becomes an
  undeclared key on a lore record. It is preserved across every edit, shown by
  nobody, and absent from every derived view, because only groups are indexed.
  Reclassified back, it is live again. No rewrite, because no ref moved.
- **Another record is reclassified**: nothing to do. `members` names actors,
  which cannot be reclassified, so `rewrite_ref_fields` never needs to visit it.
  Its skip rule (only rewrite a field whose `kinds` include the destination,
  `entities.py:405-419`) already makes this a no-op.
- **A member actor is deleted** (world-side, or tombstoned in a campaign): the
  ref dangles. The editor shows a dangling chip, and derived views keep the ref
  as stored; consumers that draw it intersect with their roster first (section
  10.2, section 12.1).
- **A group is renamed**: ids are stable for life (`entities.py:1-5`), so
  nothing changes.
- **A group with a membership journal entry is reclassified**: extend
  `journal.repoint_records` to follow `group_members` (section 6.4) and
  `membership` (section 9.5) targets as well as `entity`. Each target carries
  the original record's current `kind` and `id`, including a suffixed id if
  its destination slug is occupied. Follow only the record actually moved
  by the existing campaign/world sweep; an independent owned copy retains
  its own identity. Keep actor refs in `inherited`, `bindings` and `actor`
  unchanged: these are not refs to the moved group.
  Membership probing/restoration checks that the target's current kind is
  `groups` before resolving it; otherwise refuse with `UndoConflict` before
  record, reversal-journal or citation writes. Never ignore the recorded kind
  and look up `groups:<old id>`. Reclassifying the original back to groups
  repoints the same target to its actual new id and restores the offer when
  its compare-and-swap still matches. Another group created at the vacated
  slug is never a substitute for the original record.
  Repointing after the move is insufficient: the existing best-effort sweep
  can warn about an unwritable journal after freeing the source slug. Before
  any campaign/world record move, strictly preflight every affected journal
  (and S5's unfinished membership progress), then durably suspend its matching
  membership targets under the same campaign/all-dependent hold. A suspension
  carries a relocation operation token, the original kind/id, intended new
  kind and destination id when known. Suspended targets/positions cannot
  restore, apply or publish membership citations/diffs. If preflight or
  suspension persistence fails, move no record; previously suspended offers
  may remain conservatively unavailable, without claiming a multi-file
  transaction. On a successful move, atomically record the actual landed
  kind/id (including suffixes) and complete only that operation's suspension.
  A crash or failed completion keeps the offers suspended, even if another
  group occupies the old slug. Recovery may complete from a verified durable
  relocation receipt; otherwise leave suspended or retire, never infer the
  original's identity from an occupied ref or matching bytes. A generic
  repoint, inverse-entry creation or subsequent slug reuse cannot clear an
  unrelated suspension. This ordering belongs to S1's new journal writer;
  S5 adds the same fence for unfinished positions and deferred reversals.
- **The record named by a membership journal target is deleted**: persist
  retirement of every `group_members` and `membership` Undo/Redo offer for
  that actual `(kind, id)` under `campaign_lock` **before removing the
  record or freeing its slug**. This includes deleting the original after
  reclassifying it to another flat kind. Retirement is permanent journal
  target metadata (`retired: true`), not deletion of history; repointing,
  inverse-entry creation and slug reuse never clear it. Membership probes
  and restoration refuse a retired target with `UndoConflict` before record,
  reversal-journal or citation writes, even if a replacement has matching
  Members and the recorded current kind is groups.
  Retirement reads the journal strictly: genuine absence is an empty journal;
  malformed JSON, invalid document/entries structure, decode/access errors
  or an atomic retirement-write failure refuse deletion. Never use the
  display reader's fail-soft empty document to conclude that no offers exist.
  Preflight before mutation, and keep the same campaign hold through durable
  retirement and deletion. A failed later deletion may leave offers
  conservatively retired; it never re-enables them for a replacement.
  World deletion/demotion uses the existing all-dependent hold and preflights
  affected retirement journals before its first world/copy/asset mutation.
  Retire only offers for records actually disappearing: an owned campaign
  copy that remains, including a safe copy-down, keeps its offers. This is
  event-driven retirement for these two new writer tags, not a new record-id
  scheme, a rewrite of older journal writers or a multi-file crash transaction.
  The same retirement applies before removing an owned campaign copy by
  sync take-world/dematerialization or migration pruning, even when the world
  replacement has identical Members. Retire before unlink or manifest changes
  under the same campaign hold; strict journal preflight/write failure leaves
  that file and manifest unchanged. A later new copy never reactivates the old
  offers. Both new writer tags seal an owned-record scope after their successful
  write: host-derived resolved store/campaign roots, campaign layer, current
  kind/id. Restoration requires that same owned record in that scope; it cannot
  fall back to a currently inherited world record when the owned copy is absent.
  Retained owned originals survive a world-binding change, subject to the
  separate original-world actor-reference checks in section 6.4.
- **A membership proposal has not been saved yet (S5)**: capture a host-
  generated group lifecycle token while reading the effective groups for the
  extraction context, under the campaign lock, before model generation.
  Carry the token out-of-band through staging into the membership review row;
  never ask the model to mint or supply it. A campaign-local
  `membership_review_tokens.json` maps current group refs to opaque random
  tokens. This is review identity bookkeeping, not membership facts, record
  frontmatter, the inverse/digest, or a startup identity migration. Register
  a missing token only when capturing that context against the actual current
  group; a first save must never register/adopt its submitted token.
  Deletion and reclassification strictly preflight and durably invalidate the
  affected original's token **before removing/moving the record** under the
  same campaign/all-dependent hold. Reclassification invalidates unsaved
  proposals rather than guessing how to retarget them; a fresh review can
  capture the moved original. Owned campaign copies that remain independent
  of a world deletion/move keep their tokens. Ordinary field edits and a
  safe first copy preserve the same logical group's token. A failed later
  move/delete may leave old proposals conservatively invalidated.
  Before initial reservation, verify each approved membership row's token
  against the current effective group under the campaign lock. A missing,
  invalidated, mismatched or malformed token fails that row without record,
  citation or reversal writes; refs, names and matching bytes never prove
  continuity. Pending commit targets are then captured in that initial
  reservation, so section 4.5's unfinished-commit fences take over. A token
  invalidated after capture but before staging/generation completes likewise
  cannot become a valid proposal on a replacement.
  All token-ledger reads and mutations are strict and preserve unrelated
  entries: genuine absence is empty, while malformed JSON/structure, decode,
  access or atomic-write failure refuses capture/save/lifecycle mutation
  before effects. No caller drops the ledger and reconstitutes identity from
  a ref. A recreated group receives a new token at its next capture, never
  the revoked one. S5 adds this ledger and host-generated payload field with
  its new proposals; S1 has no unsaved membership proposals to register.
  S5 adds a shared, process-scoped **world lifecycle barrier**, keyed by the
  resolved world/store root. World group/flat-record deletion, demotion and
  reclassification acquire it **before enumerating dependents**, retaining
  it through their preflight, invalidation/fencing and record mutation.
  Discovery includes the union of current dependents and campaigns with
  outstanding captured-source references to that world in the token or
  unfinished-commit ledgers, including campaigns currently bound elsewhere.
  Scan this source bookkeeping strictly under the world barrier, then acquire
  `hold_all` for the resulting union. Unreadable bookkeeping that prevents
  excluding a campaign refuses the lifecycle operation before effects.
  Capture registers its source reference under the same barrier, so discovery
  cannot miss a concurrent registration. Compare canonical host-captured root
  identifiers; never open a path supplied by journal/client metadata. Lifecycle
  invalidation follows the captured inherited source even across A -> B -> A
  binding changes with no intervening save/provenance probe. Do not move or
  retire a verified independent owned original merely because an old inherited
  capture mentions that world: invalidate its outstanding inherited work only.
  Effective-group context/token capture and initial membership reservation
  use the same barrier. A campaign created after enumeration can exist, but
  cannot capture the old inherited group while that world operation is in
  flight; after the barrier opens it captures only the current record.
  Lock order is module-edit (when needed), world lifecycle, then the one
  campaign lock or `hold_all`'s sorted campaign locks. Never acquire the
  barrier from inside a campaign or world-actor hold: acquire at the outer
  capture/save/world-operation entry point, and let lower helpers reuse it.
  Recheck the campaign's world/root binding after acquiring the ordered
  holds; a changed binding refuses instead of using a different world's
  barrier. Keep capture/reservation within the barrier and campaign hold,
  release the capture hold before model generation, and reacquire in the
  same order for first save/resume. Token entries and initial commit targets
  persist frozen host source provenance: resolved store/campaign roots,
  source layer, and the captured world root for inherited work. Never trust
  client assertions of these values. Capture/staging/save/resume compare
  that provenance with the current effective target under the ordered holds.
  A cross-request world/root change invalidates inherited work even with
  matching group refs, tokens or bytes; observed mismatch is durably retired
  or invalidated and cannot reactivate if the binding later returns. Fresh
  capture cannot overwrite an old token's provenance with a new world's
  roots. Originally owned records use campaign-root provenance and survive
  world-binding changes while that same owned record/token remains. An
  inherited-origin offer survives only with a trusted retained-copy receipt
  tied to its original token/source scope, never an own file or matching
  bytes alone; otherwise require fresh review conservatively. This coordinates app server processes,
  not external editors or other devices. Distinct store/world roots remain
  independent; the existing StoreBusy 409 behavior applies on timeout.
- **An unfinished absorb commit holds deferred membership work (S5)**:
  `commits.json` progress is another durable location for reversal plans,
  pending membership positions and unpublished citations/diffs. S5 extends
  the same reclassification and retirement fan-out to it, under the existing
  campaign hold. Its per-position `membership_targets` metadata records the
  validated group's current kind/id and a permanent retirement flag in the
  initial atomic commit reservation, before any attempted write/checkpoint.
  A reservation cannot exist without these targets for its new membership
  positions; resume never fills missing identity bookkeeping against a
  replacement and calls that a fresh claim. This is commit bookkeeping, not
  another source of membership or a record-format identity scheme.
  Reclassification follows those targets and the matching deferred reversal
  targets, display refs and citation/diff keys. Resume uses the current
  recorded target rather than the original request's vacated ref, without
  changing the request hash or edit-position identity. A target currently
  outside groups cannot apply a membership write.
  Deletion strictly preflights and durably retires matching unfinished
  positions and `progress.journalled` reversal targets before removing the
  record; corrupt/unreadable progress or retirement-write failure refuses
  deletion. Resume preserves retirement: an unattempted retired position
  fails without applying, and an already-applied outcome stays a historical
  result rather than writing to the replacement. Deferred history may be
  appended with its retired target, but no active Undo/Redo offer, citation
  or rolling diff for the removed target may be published. Reclassification
  and retirement cover every unfinished token with these new membership
  targets, not only the commit currently being resumed. S1 establishes the
  journal lifecycle rule; S5 adds this progress handling with its new writer.
  S5 also replaces `commits._read`'s fail-soft policy for resume and **every
  commit-ledger read-modify-write**, including unrelated saves. Only genuine
  absence is empty. Preserve the supported pre-#271 flat token map and v2
  nested token/scenes format, including legacy tokens named `tokens` or
  `scenes`; validate without dropping entries. Malformed JSON, unsupported
  document/entry structure or version, decode/access failures raise a repair
  error before reservation, effects or ledger writes. Never convert unreadable
  account state into an unseen token or replace it with a new empty account.
  Retirement/suspension survives ordinary reserve/checkpoint/finalize/prune
  paths and resumed work; none may silently clear that identity metadata.

---

## 5. Campaign overlay, divergence and sync (07-C1, continued)

### 5.1 Semantics: whole-record copy-on-write, unchanged

World membership is the setting's baseline. A campaign that changes a group's
membership, by the editor or by an approved absorb row, **materializes that
group** into the campaign and diverges from the world exactly as any other
field edit does. The world record is never written by a campaign action other
than an explicit `push` or `promote`.

The effective membership of a campaign is therefore the `members` line of
whichever file `overlay.list_entities(cid, "groups")` resolves, with no merge
of the world's list and the campaign's. That is deliberate:

- A delta layer (a campaign sidecar of "adds" and "removes" over the inherited
  list) would make membership the one field resolved from two files, need its
  own conflict rules when the world later adds the same actor the campaign
  removed, and break the rule that `entity_hash` covers what a reader sees.
- Absorb already materializes groups whenever a scene appends to their body
  (`apply.py:333-334`), so whole-record divergence for groups that evolve in
  play is today's behaviour, not a new cost.

### 5.2 The cost, stated

Once a campaign has diverged a group, every later world-side edit to that group
(a body typo fix, a new keyword) arrives as a sync `conflict`, and the two
resolutions are whole-record. Taking the world's copy loses the campaign's
membership; keeping the campaign's loses the world's edit until it is redone.
That is the existing contract for every field (section 1.4). Section 19, Q3,
asks whether a field-aware merge is worth adding; the recommendation is not
yet.

### 5.3 What this spec changes in sync: the reviewer can see fields

Without this, a membership change is invisible in the review panel (section
1.4). So:

- `sync._entity_blob` gains a `fields` list for entity kinds (not greetings,
  not the plot map):

  ```python
  {"name": ..., "body": ...,
   "fields": [{"key": "members", "label": "Members",
               "value": "characters:mara, pcs:winifred",
               "shown": "Mara, Winifred"}, ...]}
  ```

  One row per key declared in `entity_schema.FIELDS[kind]` that either side
  holds, in declaration order. `value` is the stored string. `shown` resolves
  each ref of a `ref` field to its record's name through the campaign's view
  (`overlay.character_roster`, `overlay.pc_roster`, `overlay.list_entities` for
  entity kinds), leaving an unresolved ref spelled as stored. It is display
  text and nothing compares it.
- `incoming()` builds **one** `overlay.View` and one name map per call and
  hands it to every blob, so the cost is a constant number of roster reads per
  sync read, not one per pending item.
- Both sides are named through the **campaign's** view, on purpose: the
  reviewer is deciding what the campaign will read. A world-side ref naming an
  id this campaign has detached (section 7.6) is therefore shown with the
  campaign's own actor's name, and marked "(this campaign's own; not the
  library's)" so the reviewer can see the stranger before taking the world's
  copy.
- `IncomingReview.rowsOf` (`IncomingReview.tsx:46-64`) appends a row per field
  after Name and before Body, and `pairs` lines them up as it already does.
- `invisibleChangeHint`'s entity sentence today names "keys, owners, or
  secrecy". Slice A (which ships the field before this panel can show it)
  extends it to "keys, owners, secrecy, or typed fields such as its members",
  so a world-side membership change is never presented as a change with
  nothing in it; this slice then drops "typed fields" again once they are
  sent.

This is general to every entity field (a world-side `leader` or `climate`
change becomes visible too), because a membership-only rule would be a second
code path for one key.

### 5.4 Pins and composition

Nothing new. A sync pin (`sync_pins.json`) freezes a group against world
updates, membership included, and `composition` reports its state from the
same hashes.

### 5.5 Push and promote: no strangers in the library

These identity guards land in **S1**, with the editable Members field. No
shipped slice may let that field publish campaign identities into the world
before its publication guard exists. S3 adds display, not this safety boundary.

Publishing a group whose `members` names this campaign's own actor is the
greeting failure in a different shape. A campaign-made actor's id names nothing
in the world at birth and is marked `detached` (`overlay._mark_campaign_owned`,
`:1514-1566`); once the group is in the library, a sibling campaign that made
its own actor under the same slug reads that stranger as a member, and the
world reads a dangling chip.

So `promote` and `push` run a check beside the greeting one for `groups`
records **and well-formed dormant `members` refs on every other flat kind** (`sync._promote_flat`, `:751-779`; `_push_locked`, `:1070-1095`):
`_require_world_members(cid, wroot, text, ref)` raises `DanglingReferenceError`
naming the first member ref that either has no world record
(`wroot/<kind>/<id>/character.md` or `pc.md` absent) or is in this campaign's
`detached` set. The message tells the user to promote that actor first or
remove them from the group. A ref that does not parse is ignored here, as the
greeting check ignores a hand-edited name (`sync.py:861-862`).

`_require_world_members` uses section 7.7's strict detachment-ledger reader
inside the existing campaign lock, before world-record, sync-base, detachment
or asset writes. Only genuine absence means empty. A malformed, wrong-shaped,
undecodable or unreadable present ledger raises `GroupIdentityConflict` with
its repair explanation; `force` never bypasses this identity check. Reading
it through the ordinary fail-soft `overlay.detached` is forbidden: world
existence alone cannot distinguish an unrelated world actor sharing the
campaign actor's slug. This publication direction has no slot-consent
exception; the campaign actor must be promoted first or its ref removed.

Reclassification deliberately preserves dormant typed fields (4.4), so the
publication guard follows the stored actor refs, not only the current kind.
Group -> lore -> publish -> world group cannot launder a campaign identity.
For a non-group flat record with no well-formed Members refs, the helper is a
no-op and does not impose a new ledger-read prerequisite on unrelated records.
If it holds such refs, apply exactly the same strict read and world-identity
refusal before any publication write. Invalid legacy tokens remain ignored.

Whether the same check should cover `leader` is open (section 19, Q4): it is
the same failure, but adding it would refuse pushes that succeed today.

`demote` (world -> dependent campaigns) copies the whole record down and
uses section 7.7's strict, all-dependent preflight before any copy or deletion.

---

## 6. The editor (07-C1, UI)

### 6.1 Group records

Groups are already edited with `EntityEditor`, the canonical list/detail
implementation (CLAUDE.md, "the list/detail page pattern"). Declaring the field
is enough to get every behaviour that pattern requires:

- **View** (`.detail-view`): the sidebar gains a **Members** `.side-section`
  under **Leader**, one clickable `chip` per member that navigates to the
  character or PC page, and a dangling chip for an unresolved ref with the
  existing "not found" hint. Metadata that references other records renders as
  clickable chips; that is the pattern's rule, and the leader field already
  follows it.
- **Edit** (the form): the existing `multi` ref picker
  (`EntityEditor.tsx:330-347`), a checkbox per candidate over the two actor
  kinds, with dangling refs shown as pre-checked rows the user can clear.
- **Save** sends only changed fields, as today, so a group whose legacy
  `members` value nobody touched saves as it always did.

The picker lists every actor in scope. That is today's behaviour for
`habitat` and `known_by`, and a large cast makes it a long list. Section 19,
Q9, asks whether a filter box is needed; the recommendation is to ship with
the existing picker and add a filter only if it is missed.

### 6.2 Actor pages: the derived inverse

`CharacterPage` and `PCPage` put their record's metadata in the 274px context
column as `ColumnSection`s (`CharacterPage.tsx:529`, `:597`;
`PCPage.tsx:266-292`). Both gain a **Groups** `ColumnSection`:

- one chip per group from `GET .../membership?actor=<ref>` (section 7.5),
  labelled with the group name and, when the role is `leader`, a "leads"
  suffix; a chip navigates to the group's record in the same scope
  (`sectionHref`);
- in a campaign, the campaign's effective membership; in a world, the world's;
- the section is absent when the actor is in no group (an empty section is
  noise; a row whose `to()` is null is not rendered, the rail's rule one level
  down);
- read-only. Membership is edited on the group (section 3); the section's
  chips are the way there.

A failed read hides the section and logs nothing to the user, the same choice
the page makes for its owned-lore count (`CharacterPage.tsx:294-302`).

### 6.3 Frontend mirror and drift

`ENTITY_FIELDS.groups` in `types.ts` gains the member spec in the same
position. This spec adds the missing drift test (section 17.1): a backend test
that parses `ENTITY_FIELDS` out of `types.ts` and holds every kind's keys,
widgets, `kinds`, `multi`, bounds and option sources to
`entity_schema.FIELDS`, the way `test_the_frontend_ships_the_same_kind_list`
holds the kinds.

### 6.4 Hand edits: journal and lock

**Journal.** The campaign entity route's existing `{"w": "entity"}`
descriptor snapshots the body only, so a members-only edit needs its own
journal row. Nest a `journalled` block with the new whole-field descriptor
`{"w": "group_members", "kind": "groups", "id": eid}`, kind `lore`, field
`members`, label "<name> -- members", when the request explicitly submits
`members`. Its target also carries `bindings`, a JSON-native list of the
explicitly consented Members refs from `group_ref_bindings` (never Leader
consent). The target also carries `inherited`, a JSON-native list of all
well-formed member refs in the original world-layer raw line, including
currently resolved ones; it is empty for an originally owned group. Derive
these lists from the locked before-read, not submitted snapshot metadata.
Also freeze the original world-root identifier for inherited actor refs; an
originally owned group has no actor-world origin. This origin and both lists
persist unchanged through undo/redo. After the successful owned write, seal
section 4.5's owned-record scope from the host's actual target, before journalling.
The before-read may have been world-layer; the sealed restoration target is the
owned record just written. Both membership writer tags use this source rule. New refs authored by the field edit
are not inherited refs. It records nothing when raw, effective and unresolved membership
stay unchanged. World-scope edits remain unjournalled.

`read_value` for `group_members` returns
`{"raw": <stored members string or None>, "effective": <list of member refs>,
"unresolved": <list of inherited unresolved member refs>}`. The unresolved
list comes from section 7.7's strict identity context and is empty for owned
groups. Both ref lists are JSON-native, so a persisted journal's
compare-and-swap equals the next read; do not return a tuple in that value.
Read it through an overlay helper for one group, applying section 7.6's
layer/detached rule, without building the entire inverse. Its compare-and-swap
covers the whole field: any later raw-line, effective-membership or
unresolved-identity change refuses undo, including another actor's edit. An inherited filtered ref
becoming a campaign binding is recorded even when the raw string stays equal.

A restore starts with the snapshotted raw line and removes each well-formed
actor token absent from the snapshotted effective members **or present in the
snapshotted unresolved list**, using `edited_line`. Those include filtered world
refs and non-detached refs naming no world actor. Restoring either into an owned
copy would preserve a newly reviewed present/future campaign binding after Undo.
Preserve all other tokens, including legacy tokens. Undo restores the original
**resolved effective membership**, conservatively omitting originally unresolved
world slots even when those slots were previously effective dangling refs. It
revokes an identity-only opt-in, keeps the owned copy, and does not dematerialize.
Redo restores the reviewed after-value with its original exact-slot consent. The full-field edit changes the whole Members field, and each retained
colliding ref required separate consent (7.7). Its undo remains independent
of leadership.

After removing the originally filtered or unresolved tokens, revalidate every candidate
member in the target's `inherited` list under the undo campaign lock, using
section 7.7's strict current detachment read and world actor resolvers. A
retained inherited ref requires the target's exact Members-slot `bindings`
consent if it was unresolved in the restore snapshot **or is unresolved now**
(detached, naming no current world actor, or the campaign's current world-root
identifier differing from the frozen actor-world origin). Compare identifiers;
never resolve an arbitrary persisted path to read another world. An identically
named actor in a different bound world does not prove original actor continuity.
The recorded inherited-origin
list is essential: an originally resolved world actor can be deleted and
replaced after the edit even though the now-owned group's compare-and-swap
value has not moved. An unreadable current identity ledger likewise refuses,
even with recorded bindings. Newly authored refs and originally owned refs
retain ordinary authored semantics and are not mislabeled as inherited.

Without the required recorded consent, raise `UndoConflict` before record,
reversal-journal or citation writes; explain that the user must explicitly
add/bind that ref in the group editor. Undo never infers identity consent from
a restore value or from pressing Undo. The existing undo route offers no new
consent parameter. Clearing an unresolved world ref can be saved safely, but
cannot be undone by silently binding it to a present/future campaign actor.
Check again even when the destination group is already owned: this is a
restoration boundary, not merely the first-copy guard. A fully reviewed
whole-field binding can be reversed and redone with its original exact-slot
consent. Journal metadata records that review and origin, not another source
of membership on disk.

The existing generic `entity_fields` descriptor remains for adoption and
other typed fields; use `group_members` for new editor membership rows. The
one-group effective reader and central copy guard land with S1, below both
`overlay` and `membership` in the import graph. The inverse/memo in S2 uses
the same rule. This keeps S1's journal implementable without an S2 dependency.

**Lock.** Group edits take `locks.campaign_lock(cid)` around the route's
revision check, before-value journal read, update, after-value seal and journal
append. The store's group-copy boundary also holds that same reentrant lock
from checking detached refs and preparing final text through manifest update
and atomic publication. It must check whether the campaign copy already
exists inside the hold, not rely on an earlier unlocked check.

This includes materialization reached by body-only saves, absorb, undo,
reclassification and automatic copy-down. Actor creation already uses the
campaign lock, so it cannot mark a colliding actor detached between the copy
check and publication. A group edit under an existing absorb/undo lock reuses
the same reentrant domain. World demotion preflights groups and records carrying valid dormant group
actor refs before any dependent copy or asset write, inside
its existing `locks.hold_all` span over all affected campaigns; never acquire
multiple distinct campaign locks by an ad hoc loop.

07 closes group-edit and group-copy races only. Other campaign entity routes
retain their existing unlocked revision-check residual; auditing all of
`store.overlay` out of `UNREVIEWED` is separate work (section 19, Q12). The
new and changed group writer paths must still be verified by behavioral lock
and interleaving tests; do not expand the backlog or add a reasonless marker.

---

## 7. Derived inverse membership (07-C2)

### 7.1 The shape

`store/membership.py` (read-only; it writes nothing and takes no lock):

```python
from pydantic import BaseModel

class Roster(BaseModel):
    ref: str                     # "groups:salt-circle"
    name: str                    # frontmatter name, else the id
    secrecy: str                 # entities.normalize_secrecy(...)
    group_type: str              # stored text, else ""; the graph displays it
    leader: str | None           # parse_leader
    members: tuple[str, ...]     # parse_members, stored order
    headquarters: str | None     # first well-formed "locations:<id>", or None

class Affiliation(BaseModel):
    group: str                   # "groups:<id>"
    roles: tuple[str, ...]       # ("leader",), ("member",) or ("leader", "member")

class Index(BaseModel):
    groups: dict[str, Roster]                       # by group ref, sorted by ref
    by_actor: dict[str, tuple[Affiliation, ...]]    # by actor ref; groups sorted by ref
    digest: str                                      # section 7.2
```

These are plain Pydantic models, not dataclasses. They provide typed,
serializable nested data for the API and downstream consumers, while keeping
the repository's Pydantic v1/v2 contract: no `Field`, validators or
`ConfigDict`, and route serialization through `routes.common._dump`.

Plain models are mutable. The memo owns its `Index` privately, and
`world_index` and `campaign_index` return `copy.deepcopy` of the memo's value
on every path, including a miss. A caller may change its own model or dict
without changing a subsequent caller's answer. Tuples keep sequence order
stable; they do not make the containing models immutable. Do not use
`MappingProxyType`, which would obstruct Pydantic serialization, or introduce
a frozen-model configuration that breaks the plain-fields convention.

### 7.2 The functions

```python
def index_from_rows(rows: Iterable[Mapping[str, object]]) -> Index
    # Pure. `rows` are `list_entities` summaries of kind "groups" (each carries
    # `id`, `name` and its frontmatter). No I/O, no clock.

def world_index(wroot: Path) -> Index          # entities.list_entities(wroot, "groups")
def campaign_index(cid: str) -> Index
    # overlay.group_listing(cid): effective rows and their pre-read stamps,
    # each row marked with its layer; section 7.6's detached filter applies

def groups_for(index: Index, actor: str) -> tuple[Affiliation, ...]
def members_of(index: Index, group: str) -> tuple[str, ...]
def affiliated(index: Index, group: str) -> tuple[str, ...]
    # members, plus the leader when not already one, leader first
def co_affiliates(index: Index, actor: str) -> dict[str, tuple[str, ...]]
    # other actor -> the groups the two share (by `affiliated`), each tuple sorted;
    # the actor itself excluded; empty when the actor is in no group
```

`digest` is the SHA-256 of the canonical JSON of every `Roster` field,
`[(ref, name, secrecy, group_type, leader, members, headquarters) for each roster in ref
order]`. It covers everything the index hands out, so a consumer keying an
artifact on it can never keep a renamed group's old name or a moved
headquarters. A body, keys or group-state edit does not move it. Member order
is included because a consumer may render members in stored order. A consumer
that keys on less than the whole index (08's per-scene projection, section
12.2) keys on its own projection instead, and that is the recommended form:
the digest moves whenever any group anywhere is renamed.

**Refs are reported as stored, existence unchecked.** The index answers what
the records say. A consumer that draws a ref (the graph, a prompt) intersects
with its own roster; a consumer that counts ("is Mara in any group") gets the
stored truth. Pushing an existence filter into the index would make it depend
on actor files and break the digest's "group files only" property.

**gm-only rosters are in the index**, marked by `secrecy`. The index is a GM
view; section 12.1 says which consumers must drop them.

### 7.3 Without 03: the in-process tier

`campaign_index` and `world_index` memoize through
`statcache.memo_stamped` in a pool this module owns (never the shared pool,
for `search.py`'s reason, `store/search.py:10-14`), with `max_entries = 64`:
one entry per world or campaign touched in a process, and a process rarely
holds more than a handful open (a structural bound, tuned later).

**The key carries the resolved roots, never the ids:**
`("membership", "world", os.fspath(wroot))` and
`("membership", "campaign", os.fspath(croot), os.fspath(wroot))`.
`memo_stamped` reuses an entry whenever its stamps re-stat unchanged
(`statcache.py:141-170`), and the stamps are absolute paths. Keyed by id, a
`PUT /config/data-dir` to a copy of the same library (the old tree still on
disk), or two tests building the same cid under different `GRIMOIRE_HOME`s,
would be served the other store's index until one of its files moved. Every
existing `memo_stamped` caller keys on the root for this reason
(`store/characters.py:545`, `:674`).

Rows and stamps come from one overlay operation, not from separate reads:
`overlay.group_listing(cid)` returns
`(rows, stamps, roots, ledger_digest, groups_digest)`,
where `roots`
identifies the resolved campaign and world roots used for those rows.
`ledger_digest` is the canonical digest of the exact strict deleted/detached
sets used for those rows, not a later ledger read. `groups_digest` fingerprints
those exact effective group-file bytes and their source layer/ref, using the
same reads that produce the rows, never a later fingerprint sweep.
The returned rows keep ordinary group metadata but their `members`/`leader`
values reflect the shared effective rule; raw tokens remain on disk and in
normal entity reads. `index_from_rows` therefore needs no second ledger read.
The operation creates its own fresh `View`; it never accepts a caller's cached `View`.
`campaign_index` likewise has no `v` parameter. A previously populated
`View.gone` or `View.off` must never be paired with fresh ledger stamps.

The overlay operation captures each stamp before the read it vouches for:

- the campaign root and `campaign.md`, before resolving the world from it;
- `deleted.json` and `detached.json`, before loading either ledger, with the
  campaign root vouching for each absent file;
- the campaign's `groups/` directory before its listing, or the campaign root
  when the directory is absent, then each listed `groups/*.md` before bytes;
- the world's `groups/` directory before its listing, or the world root when
  absent, then each world `groups/*.md` before bytes.

Each effective row carries its layer (`"campaign"` or `"world"`). The rows,
ledger values and roots used to compute them belong to this one read sweep.
A concurrent change may affect this answer, but must invalidate the captured
stamps on the next read; no new stamp may certify an old cached value. A
vanished or otherwise unvouchable input returns `stamps=None` and the value
is not memoized, as `memo_stamped` requires.

Membership projections require readable identity and visibility ledgers.
`group_listing` reads `detached.json` strictly as section 7.7 specifies and
`deleted.json` strictly as a JSON list of strings, retaining every existing
flat/asset/library tombstone spelling and accepting duplicates. Only genuine
absence means empty. Wrong JSON/types, decode failures and other read errors
raise `GroupIdentityConflict`; they never supply empty ledgers or a partial
index. Ordinary overlay readers outside membership keep their existing
fail-soft policy. Owned-group refs remain authored, not filtered: failure
makes the campaign projection unavailable rather than rewriting or
reclassifying those refs. An owned group can still be read by the entity
editor without an inherited identity lookup.

Before serving **any campaign memo hit**, strictly verify both current ledgers
and compute their canonical digest. The private memo value records the
`ledger_digest` coupled to its rows. A read failure raises even when file
stats are unchanged; it never serves the old index as fallback. A valid
current digest different from the cached one invalidates that entry and
computes a fresh listing, even when external edits preserve file timestamps.
Do not attach a new ledger digest to old rows. If the preflight digest and a
fresh computation differ during concurrent mutation, return that computation
without caching it; do not loop indefinitely. Pool keys still carry only the
resolved roots, with one bounded entry per scope, and returned index snapshots
remain isolated deep copies. World-only reads need no campaign ledger.
Before any world/campaign memo hit, also fingerprint current effective group
contents, layer/refs and collection membership through overlay/`entities`.
Stat stamps alone are not proof: editors/sync restores may preserve the full
signature. The memo stores `groups_digest` coupled to its own rows and
invalidates on current content changes even with unchanged stats. Never
certify old rows with a later digest. Preflight/compute fingerprint mismatch
returns the fresh computation uncached rather than looping. Current root/
layer/ledger selection belongs to that fingerprint sweep. World listing has
the same bytes-to-rows coupling. Hits read/hash effective group bytes; they
save parsing/index construction, not file-content I/O.
Every consumer caching a membership-derived payload likewise calls this
current membership preflight before serving its own cache hit; a graph,
snapshot or compiled payload cache cannot bypass ledger availability or
current group content, including secrecy changes with preserved stats.

The roots returned by the compute are checked against those used for the memo
key. If they differ because `campaign.md` changed during root resolution,
return the fresh answer without caching it under the earlier roots; the next
call resolves its own key. These helpers live in `store/overlay.py` because
raw campaign-path reads elsewhere would violate `test_overlay_guard.py`.
`world_index` uses the same pre-read directory/file discipline through
`entities`, including the parent stamp for an absent `groups/` directory.

A group's record directory (`groups/<id>/state.md`, assets) is not an input:
an edit to an existing group-state file does not invalidate group-file
stamps. Creating a record directory may conservatively move the parent
listing stamp and rebuild the memo once; it never moves the index digest.

Cost without the memo is one effective group listing reading/parsing each
file once. Hits still read/hash contents to prove freshness, avoiding parsing
and inverse construction. This correctness cost replaces stat-only reuse.

### 7.4 With 03: the persistent tier

Once 03 lands, the index is also a 03 artifact kind, `membership_index`,
version 1. Current effective group-content preflight remains mandatory on
artifact hits. Read/hash collection bytes freshly instead of reusing a
stat-certified collection digest; derive the artifact's inputs and computed
index from the same captured group/ledger snapshot. An artifact lookup or
compute cannot attach current fingerprints to older rows, or cache rows from
a later read under earlier input hashes. If a snapshot cannot be vouched for,
return it uncached. This keeps S2 and S8 equivalent on stat-preserving edits.

```
kind    = "membership_index"
params  = {"scope": "campaign" | "world"}
inputs  = [("world_groups",    collection_digest(<wroot>/groups, "*.md", safe_id stems)),
           ("campaign_groups", collection_digest(<croot>/groups, "*.md", safe_id stems)),  # campaign only
           ("tombstones",      content_hash(<croot>/deleted.json) or "absent"),           # campaign only
           ("detached",        content_hash(<croot>/detached.json) or "absent")]          # campaign only
```

- The collection digest (03 §7) covers only top-level `*.md` files whose stem
  passes `safe_id`, the exact set `entities.list_entities` reads
  (`entities.py:100-122`). Hashing the whole `groups/` tree would move the key
  on every group-state write.
- The world identity is a path-derived input and goes in `params`
  (03 §6), or the compute returns the path-independent index and the caller
  adds nothing path-derived; either satisfies 03's rule.
- 03 §1 says the cache is "not an index of membership". That sentence is about
  **directory** membership (which files exist), which 03 never answers. This
  artifact is a derivation over file bytes, the "predicate over members'
  content" class 03 §7 says belongs in the cache. The listing is still read
  live every time to compute the digest.
- Liveness is by construction (03-C2): an edited group file moves its digest,
  so a stale index has no live key. Registered as a materialized kind (03-C3),
  05 can rebuild it eagerly after an external edit.

The persistent tier changes cost, never answers: a test runs every consumer
with and without it and requires equal output (section 17.2). Campaign strict
ledger preflight still runs before any persistent artifact hit; unreadable
ledgers never authorize a stale artifact or an empty-input fallback. Its digests are
computed by 03's reader through the same overlay helpers, never off a raw
campaign root.

### 7.5 The API

Two read routes, registered before the generic entity router (whose
`/worlds/{wid}/{kind}` and `/campaigns/{cid}/{kind}` capture any third segment; `test_route_order.py`
guards it):

```
GET /api/worlds/{wid}/membership[?actor=<ref>]
GET /api/campaigns/{cid}/membership[?actor=<ref>]
```

Without `actor`:

```json
{"digest": "9f3c...",
 "groups": [{"ref": "groups:salt-circle", "name": "Salt Circle", "secrecy": "public",
             "group_type": "guild", "headquarters": "locations:the-counting-house",
             "leader": "characters:seraphine",
             "members": ["characters:mara", "pcs:winifred", "characters:seraphine"]}],
 "by_actor": {"characters:mara": [{"group": "groups:salt-circle", "roles": ["member"]}],
              "characters:seraphine": [{"group": "groups:salt-circle",
                                        "roles": ["leader", "member"]}],
              "pcs:winifred": [{"group": "groups:salt-circle", "roles": ["member"]}]}}
```

With `actor=characters:mara`:

```json
{"actor": "characters:mara",
 "groups": [{"ref": "groups:salt-circle", "name": "Salt Circle", "secrecy": "public",
             "roles": ["member"]}]}
```

- `actor` must be `characters:<id>` or `pcs:<id>` with a `referenceable` id,
  else 400. An actor that does not exist is not an error: it answers the
  affiliations recorded for that ref, potentially empty. No actor-existence
  read is performed; a dangling ref retained in the index is retained in this
  route too. Display consumers intersect refs with their own roster.
- 404 for an unknown world or campaign, through the existing
  `_world_root_or_404` / `_campaign_root_or_404`.
- A group file that cannot be parsed fails the read as `GET /{scope}/groups`
  fails today: the same reader raises. Group-file failures may retain the
  consumer's existing handling. Identity/visibility-ledger failures instead
  raise `GroupIdentityConflict` and propagate before graph payload or model
  generation (7.3); no cached, empty-ledger or raw-world-ref fallback.
- Responses have explicit Pydantic models: `MembershipResponse` with
  `digest`, `groups: tuple[Roster, ...]` and
  `by_actor: dict[str, tuple[Affiliation, ...]]`, and
  `ActorMembershipResponse` with `actor` and
  `groups: tuple[ActorGroup, ...]`. `ActorGroup` carries `ref`, `name`,
  `secrecy` and `roles`. The routes declare their response model and use
  `routes.common._dump` when a plain dict is needed. The JSON shapes above
  stay the public contract; Pydantic supplies typed structured output and
  serialization instead of manually rebuilding untyped response dicts.

### 7.6 A campaign's own actors are not members of the world's groups

The failure, in order:

1. A campaign creates its own Winifred while the world has none, and
   `_mark_campaign_owned` detaches `characters/winifred`
   (`overlay.py:1514-1566`).
2. The world author later creates an unrelated Winifred under the same id and
   adds `characters:winifred` to the Salt Circle, which the campaign inherits.
3. Read naively, the campaign's own Winifred is now a member. Her presence
   opens the group's owner gate (C3d), absorb names her among "Affiliates here",
   the graph draws `member_of`, and her page lists the group.

That is the stranger-by-slug harm `detached` exists to stop (#225,
`overlay.detached`, `:286-300`), and membership would make it routine where
`leader` made it rare. So `campaign_index` applies one rule:

> For a roster the campaign **inherits** (the row's layer is `"world"`), every
> `members` or `leader` ref whose `<kind>/<id>` is in `overlay.detached(cid)`
> is dropped from the index. A roster the campaign holds its own copy of is
> taken as written: the campaign's own file naming its own actor means its own
> actor.

The ref stays in the world file and in the campaign editor. S1's one-group
`membership_context` marks filtered refs as unresolved world references,
rather than naming the campaign's unrelated actor (section 7.7).
Applying the rule to `leader` too is a correctness fix for the same failure
the field has carried since it was declared. `detached.json` is a memo input
(section 7.3) and a persistent-tier input (section 7.4).

Bulk consumers (activation, graph, absorb snapshots, API and actor pages)
read `campaign_index`. The one-group editor context, journal, conflict and
apply/undo checks use the one-group effective overlay reader instead of
rebuilding the inverse for each delta. Both readers share one pure
layer/detached filtering rule below `overlay` and `membership`; neither
reimplements it. Activation (11.1) no longer parses raw group actor refs.

### 7.7 Materialization must not approve an unrelated identity change

Copying an inherited group removes section 7.6's world-layer filter. Raw
member/leader refs can then silently bind unrelated detached campaign actors.
A body save, another actor's delta, reclassification or world-delete copy-down
must not grant that membership or leadership.

07 chooses a fail-closed copy boundary rather than adding origin metadata to
group records. Before creating a campaign copy, inspect the same world text
that will be copied. Every retained valid `members`/`leader` ref either in
`detached.json` **or naming no actor in the world** requires explicit consent
for its `(field, actor)` slot. A missing world actor is an unresolved foreign
identity even before a campaign actor claims its slug: allowing that copy
first would evade the detached check when the actor is created later. Check
world actor existence through the world actor resolvers, with safe refs, at
the copy boundary under the same campaign lock. A new ref the
author explicitly adds to a submitted field is an ordinary membership/leader
edit, not consent for any retained ref. A reviewed member slot does not also
review the
leader slot for the same actor. Invalid legacy tokens stay untouched.

- S1's campaign group GET includes a typed `membership_context`, produced
  by its one-group effective reader: `layer`, effective `members`/`leader`,
  `filtered_members`/`filtered_leader` and
  `unresolved_members`/`unresolved_leader`. The filtered fields identify
  section 7.6's detached refs; unresolved fields include those plus refs
  naming no world actor and identify every retained slot needing consent.
  `GroupMembershipContext` is a
  plain Pydantic model, serialized through the route shim. The context is
  a top-level sibling of `meta`, `body`, `tokens` and `rev`, never stored in
  frontmatter. Its types are `layer: Literal["world", "campaign"]`,
  `members: tuple[str, ...]`, `leader: str | None`,
  `filtered_members: tuple[str, ...]`, `filtered_leader: str | None`,
  `unresolved_members: tuple[str, ...]` and `unresolved_leader: str | None`,
  plus `unresolved_leaders: tuple[str, ...]` -- every unresolved Leader token in
  raw order, because the copy guard checks every token and each guarded slot
  needs a visible opt-in (a legacy second token can only be resolved by
  choosing one Leader, since the field is single-valued on save).
  All four diagnostic fields are empty for a campaign-owned group: its refs
  are taken as authored, including intentionally dangling refs.
  These extra identity diagnostics do not change the index's effective
  membership or memo inputs: the index still reports dangling refs except
  for detached inherited refs. World actor reads are confined to this
  one-group context and copy validation, not the inverse or its digest.
  This does not require S2's full index or membership endpoint.
- S1's editor shows unresolved refs as world references and warns
  that binding them will use present or future campaign actors. Each affected field/ref
  has an explicit opt-in; saving the field alone does not provide consent.
  Missing context cannot silently resolve raw chips to campaign actors or
  manufacture consent. A body-only save grants no ref review.
- `EntityUpdate` adds `group_ref_bindings: list[GroupRefBinding] = []`, where
  the plain Pydantic `GroupRefBinding` has
  `field: Literal["members", "leader"]` and `actor: str`. The editor sends
  only opted-in slots and includes their field in `fields` even when its raw
  text is unchanged. The route validates each ref and that its field was
  submitted, then passes those exact slots to the store. It never infers
  consent from submitting an entire field. Non-group requests carrying
  bindings are refused. A newly detached ref, or a world actor removed after
  the GET, absent from the reviewed context therefore fails the store's
  recheck rather than gaining consent. Consent to a still-dangling ref
  deliberately authorizes future campaign use; it does not create an actor
  or a new current membership fact. Undo never dematerializes the record.
- An approved absorb join or leave reviews only its `(members, actor)` slot.
  Another actor's delta grants no review of a colliding token it preserves.
- Generic materialization, reclassification and automatic copy-down grant no
  actor-ref review. They refuse colliding records until the author resolves
  the affected fields in the existing editor. World-delete copy-down
  preflights all affected campaigns before any copy or deletion, so this
  refusal cannot leave a partially deleted world record.

Use an explicit store-level keyword for reviewed `(field, actor)` slots;
merely passing `fields={"members": line}` is not sufficient, since it
includes unrelated tokens. Defaults grant no review. A whole-field editor
save still changes the whole Members line, but binding a colliding ref
requires its separate `group_ref_bindings` consent. Check the boundary
centrally, including `materialize_entity` callers bypassing `update_entity`.
Shared pure actor-ref parsing lives below both `overlay` and `membership`;
`overlay` must not import `membership` and create a module cycle.

The first-copy boundary also covers well-formed dormant `members`/`leader`
refs on other inherited flat kinds. Otherwise World group -> World lore ->
campaign body copy -> campaign group would activate the same unreviewed
identity after the source stopped being a group. Validate the actual retained
fields before creating that first owned copy, under the campaign lock. A
non-group source with no valid dormant actor refs retains its existing
materialization behavior. Non-group requests still cannot submit binding
consent; resolve the dormant fields in the library record (using its group
editor after reclassifying it there when necessary) before copying. Generic
reclassification itself supplies no actor-slot review. Existing owned refs
remain authored as stated in 7.6; there is no scan or retroactive migration.

A save clearing a collision prepares its final text before publishing the
campaign copy. Never materialize unsafe world bytes and then rewrite the
field: a crash between those writes would grant the identity change this
boundary refuses. Record the sync base from the original world bytes and
publish the final campaign text atomically.

Copy validation and automatic copy-down preflight use a **strict** detachment
ledger read under the campaign lock, never `overlay.detached`'s fail-soft
empty fallback. A genuinely absent `detached.json` means no detachments; catch
only `FileNotFoundError` for that case, not an `exists()` result that may hide
an access error. A present ledger must decode as a list of well-formed
`<kind>/<id>` strings in the existing ledger format, preserving non-actor
kinds (including `plotmap/plotmap`) and duplicates without dropping entries.
Malformed JSON, wrong outer/entry types, invalid refs, decoding errors and
other read errors refuse with `GroupIdentityConflict` before record, manifest
or copy-down writes, even if a request supplies binding opt-ins. The error
explains that identity cannot be verified until the ledger is repaired.
Consent diagnostics must likewise not manufacture a valid empty context from
a ledger error; report that error rather than identifying foreign refs as
resolved. Membership projections use the strict policy in section 7.3;
existing overlay reads outside membership keep their documented fail-soft
behavior. Neither a projection nor a permanent copy may treat an unreadable
identity ledger as empty.

The strict detachment policy also governs **every read-modify-write of that
ledger**, including `add_detached`, removal/reattachment and `repoint_record`.
A fail-soft read may never supply the set to rewrite: replacing a corrupt
ledger with only the new ref would erase unrelated markers and make later
strict reads accept a false identity. Validate the complete existing set and
the refs being added/repointed under the same campaign lock before any
ledger, manifest or associated business-record mutation. Retain every
unrelated marker; malformed/unreadable input is not automatically repaired.

Campaign character and PC creation preflight the strict detachment and
visibility ledgers inside their existing lock **before allocating/publishing
an actor record**. A preflight failure leaves no actor record, marker,
manifest or journal change, preserving the original ledger bytes. Workflows
that remove, reattach or repoint detachment entries likewise preflight before
their first record move/delete/publication; world workflows preflight every
affected campaign under `hold_all` before changing the world record. Reuse
validated sets within that hold where appropriate rather than laundering a
later read error through an empty fallback. Individual writes remain atomic;
this does not add a multi-file crash transaction or coordination with
external filesystem writers. Ordinary overlay **reads** outside membership
may retain fail-soft behavior; ledger mutations may not. This shared-ledger
integrity correction lands in S1 and does not classify the entire overlay
backlog as reviewed.

An inherited group read or a first copy carrying group/dormant-group refs
also verifies the visibility ledger through a strict `deleted.json` list-of-
strings reader before publishing. Preserve its existing flat, asset and
library-image ref spellings without dropping entries; genuine absence is
empty, while malformed JSON/types, decode and access failures raise
`GroupIdentityConflict`. A tombstone read error must not resurrect a hidden
group as a permanent own copy. Owned group entity reads retain authored refs
and need no inherited-ledger diagnosis.

Every `deleted.json` read-modify-write also holds the campaign lock across a
strict complete read and atomic replacement: `add_deleted`, `_drop_deleted`,
library/asset tombstone removal, reattachment cleanup and record repointing.
Preserve unrelated flat, asset and library markers; never rewrite from a
fail-soft empty/subset or an earlier unlocked selection. Associated record/
asset mutation preflights before its first effect. Corrupt/unreadable input
is preserved and refused even for unrelated library-image work. World
workflows retain all-dependent preflight. This visibility integrity fix
lands in S1 with detachment integrity; manifest and other unreviewed overlay
races retain their stated residual.

Hold `campaign_lock(cid)` across
collision validation, manifest update and publication (section 6.4), including
copies reached outside a route. The lock does not coordinate external
filesystem edits or other devices.

A refusal raises `GroupIdentityConflict` before record or manifest writes.
Routes report 409 with affected fields/refs and the editor action needed to
resolve them. Absorb marks the approved row `failed` with that reason,
without a membership citation or undo row. A successful delta still changes
only its target actor's raw tokens.

Staging, conflict reading, apply no-op checks and undo read **effective**
membership through that shared one-group rule. An explicit join for a filtered actor is a real change even when
their ref is already in the inherited raw line. If no other unreviewed
collision remains, it may atomically create the copy with that reviewed
binding and unchanged member tokens. Undoing that join removes its target
ref to restore effective non-membership; it does not dematerialize the file.
A blocked proposal may stage normally, but its apply failure identifies the
unreviewed collision instead of granting another actor's affiliation.

---

## 8. Integration overview (07-C3)

| Item | Where | Model call? | Changes a prompt? |
|---|---|---|---|
| C3a absorb proposals | `absorb/*`, `templates/absorb/*` | No new call: one more section in the existing extraction | Yes: absorb's system prompt always; its context line only when a present actor is a member |
| C3b graph | `continuity/graph.py`, `components/storyGraph/*` | No | No |
| C3c retrieval projections | `store/membership.py` | No | Not by itself; 08/09/11 decide |
| C3d structural presence | `context/activation.py`, `context/world_state.py` | No | Only where a group records members |

The new member-presence rule is inert on a store that records no members.
Existing leadership is still an affiliation: a leader-only group can gain a
graph node and a "Affiliates here" snapshot segment. Absorb's system prompt
changes for every store. A store with no groups gains no group-derived output;
the turn-prompt golden and the frozen campaign's snapshot stand unchanged.

---

## 9. Absorb: reviewed membership changes (07-C3a)

### 9.1 What the model is asked

A new top-level section, `membership_changes`, added to `parse_output` (and so
to the derived contract the graders score, `evals/graders.py:254-256`) and
described in `templates/absorb/system.j2` after `group_state_edits`:

> "membership_changes" (list of {"group","actor","change"} -- ONLY when the
> scene shows a character joining or leaving one of the groups on the
> "Groups:" context line: sworn in, recruited, admitted, expelled, resigned,
> defected. "group" is the "groups/<id>" from that line, "actor" is the
> character's id from the "Present:" line, and "change" is "join" or "leave".
> Working beside a group, helping it once, or being taken for one of its
> members is not joining it. Do not propose a join for an actor already
> marked "member" on the "Groups:" line; a "leader" alone may still join.
> Membership is not group state: what the group wants or holds
> goes in group_state_edits.)

Each row carries the citation fields every staged section carries
(`quote`, `speaker`, `certainty`, `parse.CITATION_FIELDS`), through `_cite`.

The wording is a starting point. `evals/run.py --live` is the only check that a
model follows it; the offline suite proves only that the instruction is in the
prompt (section 17.4).

### 9.1.1 Pydantic models and LLM structured output

Use plain Pydantic models for the extraction contract as well as the derived
membership data. `MembershipChange` declares `group: str`, `actor: str`,
`change: Literal["join", "leave"]` and nullable citation fields (`quote`,
`speaker`, `certainty`). `AbsorbExtraction` declares every existing absorb
section with its typed row model and adds
`membership_changes: list[MembershipChange] = []`. It must retain the complete
current contract, including optional knowledge, group-state, identity,
weather and deadline fields; an unconstrained `dict` is not a structured row.

Generate the extraction's JSON Schema from those models through a v1/v2
compatibility helper. The provider schema must satisfy the existing
`schemas.check` portable subset: inline local definitions/references, turn a
single literal into an enum, remove schema metadata (`title` and `default`),
make every object closed with every property required, and represent an
optional value with a nullable `anyOf`. Pydantic v1 omits nullability from
some generated field schemas, so the helper must consult the declared field
types as well. Reject unsupported shapes and constraints rather than silently
dropping them. Keep `schemas.py` standard-library-only; the Pydantic adapter
is a separate leaf module, with no dependency on routes or the store.

The relationship row's Python field `from_` has an explicit wire-name mapping
to the existing JSON key `from`; do not change that public key or introduce
`Field`/model configuration to express an alias. No handwritten parallel
membership schema may drift from `MembershipChange`.

Render this exact portable schema in the absorb system prompt using
`schema_json`, and pass the same schema to the existing
`operations.generate("absorb", ..., schema=...)` call. The current inference
facade owns provider capability selection, refusal fallback, retries, budgets
and metering; this change adds no call or separate provider path. Prompt-only
attempts receive the same schema text as structured-mode attempts.

The existing tolerant `parse_output` remains the normalization boundary for
old recordings and prompt-only replies: absent or malformed sections do not
invalidate unrelated usable edits. Nullable fields meaning "not specified"
must preserve omission semantics: null `knows`, `suspects`, group-state axes
or `due` keeps the stored value, while an explicit empty string may clear it.
Pydantic defaults must not be dumped into that raw reply before this decision.
Format validation does not establish actor existence or narrative evidence;
staging, routing and apply retain those checks.

Acceptance includes portable-schema validation and schema parity on Pydantic
v1/v2, complete section/field coverage, unchanged wire names, the same schema
in prompt and generation arguments, null-versus-empty preservation, and the
existing structured-mode/refusal paths exercised with the shared LLM fakes.

### 9.2 What the model is shown

`group_snapshot(cid)` becomes `group_snapshot(cid, cast=())`, called with
`facts["cast"]` at `routes/scenes.py:3089`. It reads affiliations from
`membership.campaign_index(cid)`, so section 7.6's detached rule holds here
too. For each listed group, when at least one present actor is in its
`affiliated` set, the line gains one segment:

```
- groups/salt-circle (Salt Circle): Goals: ... | Affiliates here: Mara (characters/mara, member)
```

- Only present affiliates are named; every entry states its member/leader
  roles. They are the only actors the transcript can give evidence about, and naming them is enough for the model to avoid
  re-proposing a standing membership. The segment's length is bounded by the
  cast, not by the group's size, so a large group costs nothing extra.
- An actor is spelled `<kind>/<id>`, the spelling of the `Present:` line, so
  the model never has to translate between two notations.
- A group with no present affiliate renders exactly as today. A leader-only
  group with a present leader gains the segment too; the no-members
  compatibility promise applies to turn composition, not this absorb context.
- gm-only groups stay out (`snapshots.py:97-104`), and so does their
  membership. `prompt_visible` lands with S2 so absorb can apply it in S5.
- Public groups render in the ordinary block. Secret groups, including their
  state and affiliations, render only in a separate `Secret groups` block
  prefaced with: "GM context: these groups and affiliations are unknown to
  uninvolved characters; do not have them voice or act on this information."
  Only that block calls `prompt_visible(..., secret_framed=True)`. If the
  framing is omitted, the secret rows are omitted too. This intentionally
  changes the old unframed secret snapshot, even with no present affiliate.
- A present leader is named once, as `Seraphine (characters/seraphine,
  leader)` when unlisted, or `Seraphine (characters/seraphine, leader, member)`
  when listed. Other present members carry the `member` label. Leadership
  alone must not suppress a genuine join, and leaving membership must not
  remove leadership. Order: the leader first, then members in stored order.

### 9.3 Parsing and staging

`parse_output`:

```python
membership = []
for e in _rows(obj, "membership_changes"):
    if not isinstance(e, dict):
        continue
    change = _str(e, "change").lower()
    if change not in ("join", "leave"):
        continue                               # an unknown word is no proposal
    membership.append({"group": _str(e, "group"), "actor": _str(e, "actor"),
                       "change": change} | _cite(e))
```

`materialize` stages one `StagedEdit` per row that survives these drops, each
of which is "tolerated, not an error", the module's rule for targets that do
not exist (`materializer.py:877-878`):

- `group` resolves through `groups/<id>`, `groups:<id>` or a bare id to a group
  this campaign can see (`overlay.read_entity(cid, "groups", gid)`); otherwise
  dropped. A group created by a `new_lore` row in the same reply does not exist
  yet and is dropped (section 19, Q5).
- `actor` normalizes `characters/<id>` to `characters:<id>`
  (`continuity.canon.actor_ref`'s rule), must be `characters:<id>` or
  `pcs:<id>` with `entity_schema.referenceable(id)`, and must exist through the
  overlay (`materializer._actor_exists`, `:96-111`); otherwise dropped.
  Existence alone is not enough: an imported actor whose id holds a comma
  exists, and writing its ref would store a line that reads back as two refs
  (`entity_schema.py:172-211`).
- A `join` for an actor already in effective `members`, or a `leave` for one not in it,
  is dropped: its before equals its after, and the materializer stages no
  no-op anywhere else either.
- Two rows for the same (group, actor) collapse to the first, so a reply that
  says both "join" and "leave" for one person stages the one it said first and
  the reviewer sees one row.

The staged edit:

```python
{"id": "membership:salt-circle:characters:mara",
 "kind": "membership",
 "target": {"kind": "groups", "id": "salt-circle"},
 "label": "Salt Circle -- Mara joins",
 "field": "members:characters:mara",
 "before": "not a member", "after": "member",
 "authored": False,
 "payload": {"group": "salt-circle", "actor": "characters:mara", "change": "join",
             "group_lifecycle_token": "<host-generated token>"}}
```

The token comes from host context-time capture (4.5), not extraction; frozen
source provenance stays in host bookkeeping. The row is routed with
`routing.review(index, row, subjects=(actor,))`. The actor is the
subject: an actor saying "I have joined the Circle" is first-hand about
themself (the `SELF` tier, `absorb/routing.py`), and a third party's claim
about it is a claim, which is exactly how the existing tiers already read.

`before`/`after` are a rendered status, not the stored line. That is what makes
two rows on one group independent (section 9.4) and is why the kind is not
`MERGEABLE` (`conflicts.py:98-105`).

`field` is per actor, `members:<actor ref>`, not the bare `members`. Provenance
is keyed `<kind>/<id>#<field>` (`store/provenance.py:43-61`), and the save
upserts a citation per key (`absorb/apply.py:844-847`): with a shared `field`,
"Mara joins" and "Winifred leaves" saved together would overwrite each other's
citation, and undoing one would `provenance.forget` the other's
(`undo.py:620-631`). Nothing else reads `field` as a frontmatter key for this
kind: the apply branch writes `members` by name.

### 9.4 Applying

`apply_edits` prepares a group context from the complete frozen set of
approved edits, under the campaign lock the chronicle save already holds.
Its private `_apply_one` membership branch uses that context; 9.4.1 specifies
how the first group copy preserves other approved actors' effective state:

0. **Shape, before any read.** The row comes off the `PUT /chronicle` body,
   which is validated only as "a dict" (the reason the dossier and voice-drift
   branches re-guard their rows). `payload.actor` must be `characters:<id>` or
   `pcs:<id>` with a `referenceable` id, `payload.change` must be `join` or
   `leave`, and `target.kind` must be `groups` with a `safe_id` id equal to
   `payload.group`. Require `field == "members:" + payload.actor` exactly;
   a different actor, a blank or a non-string field is not a valid row.
   Otherwise `skipped` before any read, as a forged or corrupt row is today.
   The same check applies to batch preparation and resumed rows. Build the
   journal field, provenance key and undo citation reference from this validated
   actor/target pair; no unvalidated row field can select another actor's key.
   This is the boundary `routes.entities._check_fields` would have applied,
   which `overlay.update_entity` does not.
1. Read the group through the overlay; gone -> `failed`, "that group no longer
   exists in this campaign".
2. For a `join`, the actor must still exist (`_actor_exists`); gone ->
   `failed`, "that character no longer exists in this campaign". A `leave`
   does not check: removing a dangling ref is legitimate cleanup.
3. Re-read the raw `members` value. For an inherited group, use 9.4.1's
   neutral baseline for approved colliding member slots before applying this
   actor's `membership.edited_line(raw, actor, change)`. For an own copy,
   use its raw line directly. Write through `overlay.update_entity` with
   this actor's exact reviewed slot and the validated batch preparation.
   Every token outside the approved actors' slots, including legacy tokens,
   is kept.
   An empty `line` removes the key (`entities.update_entity`'s rule,
   `:254-258`).
4. Already in the desired **effective** state -> `skipped`, nothing written.
   Compare effective membership before applying the raw-token delta. A join
   of a filtered inherited ref must not be skipped because its raw token is
   already present; section 7.7 governs its reviewed materialization.

**Why a delta and not the staged `after`.** Two approved rows on one group
("Mara joins", "Winifred leaves") were staged against the same `before`.
Writing either row's full line would erase the other's change. Deltas and
9.4.1's neutral preparation make distinct actors' effective membership,
row outcomes and per-actor citations independent of processing order. Newly
appended refs retain the helper's append order, which is intentionally part
of the digest; commuting membership facts does not promise identical order
for two newly appended members.

The write materializes an inherited group subject to section 7.7's identity
boundary and the complete batch's validated member slots. No rejected,
malformed or missing-actor join supplies preparation authority. An unrelated
standalone body append supplies none; an approved body edit in the same batch
may use the neutral copy preparation without applying future membership rows.

The apply branch never reads the review block (`routing.py`'s rule: "nothing
here is permission").

### 9.4.1 Neutral copy preparation for an approved batch

Passing another row's join consent directly to a copy would bind that actor
before their row executes. Ignoring the consent would block unrelated approved
deltas depending on order. Instead, prepare a copy whose **effective membership
is unchanged** until each actor's own delta executes.

- Group the approved membership rows by group. Reuse the row shape, target,
  group-read and join-existence checks from 9.4; never treat an id alone, a
  citation/review-policy block or a rejected/deferred row as consent. Preserve
  the materializer's first-row rule for duplicate `(group, actor)` targets.
  Invalid rows keep their own skipped/failed outcomes and provide no slots.
- On the first actual write that would materialize a group, revalidate its
  selected rows under the same campaign lock. In the in-memory world-text
  baseline, remove filtered member tokens only for those approved actors,
  using `edited_line(..., actor, "leave")`. They were effectively absent, so
  this changes no actor's effective membership. Leave every unapproved token,
  including legacy tokens, untouched. Membership approvals never neutralize
  the leader field.
- Apply only the current row's delta to that baseline. Its join may append
  its own actor and bind them with that row's exact slot consent; another
  approved join stays absent until its row runs. An approved body edit can
  use the same neutral baseline while leaving all memberships unchanged.
  Unapproved member/leader collisions remaining in the final text still
  refuse materialization.
- Publish the neutral baseline plus the current row's actual mutation in one
  atomic copy, recording the base from the original world bytes. Do not write
  a separate preparation-only copy. A batch of entirely settled rows does not
  materialize or normalize a record. A settled approved leave can authorize
  removal of its filtered raw token when another row needs the safe copy;
  it stays effectively absent and receives no applied membership row/citation.
- Continue the existing per-position commit checkpoints, before/after actor
  snapshots, journals and provenance for each actual delta. Never apply all
  future membership facts ahead of their receipts or mark those rows applied
  during preparation. Preparation preserves their before-status readings, so
  `_outside_drift` and resume remain valid. Rebuild preparation from the
  fingerprinted frozen approvals on resume and revalidate current targets;
  an unconfirmed earlier row is not assumed to have applied.
- A missing-world ref that is not detached remains a recorded member in the
  inverse. Do not neutralize it: that would apply its leave before its own
  receipt. If the default next row cannot safely copy, choose a validated
  approved row that can make the first copy safely on its own, after the
  neutral detached-ref preparation. For example, execute an approved dangling
  Winifred leave before Mara's leave when Winifred's retained world ref is the
  only unresolved slot. Choose the candidate deterministically by canonical
  `(kind, group, actor, change)` with original position only as a duplicate
  tie-breaker; keep the materializer's first-target rule. Validate final text
  with only that candidate's own consent, never another row's intended leave.
  A body row is a candidate only if its own mutation makes a safe final copy.
  If no pending row can create a safe first copy, fail that group's rows
  requiring a flat-record copy with `GroupIdentityConflict` without publishing
  anything. Group-state writes do not copy the flat group and keep their
  existing behavior. Several
  unresolved dangling refs needing separate leaves may require editor cleanup
  first; an unsafe intermediate copy is never permitted.
- Scheduling changes processing order only. Keep frozen approvals and the
  fingerprint in original order, store checkpoints/outcomes by original
  position and return outcomes in that order. Run the selected row through
  the complete existing per-position intent, apply and receipt path before
  any dependent row. Do not reorder past an unconfirmed slot by pretending it
  applied; resume revalidates the pending rows and existing copy. Independent
  rows outside that group retain their ordering.
- Once the group is owned, later rows use ordinary raw deltas; never neutralize
  an owned group's refs. No origin metadata or second membership authority is
  persisted. Raw foreign tokens removed from a campaign copy remain in the
  world file; undo restores effective actor states without dematerializing.

With inherited `Mara, Winifred`, where Mara is effective and Winifred filtered,
approved `Mara leaves` and `Winifred joins` now both land in either order.
The first copy starts with Mara only before its current delta; Winifred joins
only at her own row, with a false-to-true journal. The final member is Winifred,
and per-row outcomes, undo snapshots and citations agree in both orders.
A missing Winifred join, an invalid join or an unapproved colliding leader
still blocks the unsafe copy.

### 9.5 Conflicts, journal, undo

- **Conflict reading.** `current_value` reads the actor's effective status ("member" /
  "not a member", or None if the group will not read), and `target_key` is
  `("membership", gid, actor)`. A stored status that equals `after` is not a
  conflict: the edit already holds, and step 4 skips it. That needs one small
  addition to `survey` (`conflicts.py:414-451`): a kind in a new
  `SETTLED_WHEN_AFTER = frozenset({"membership"})` returns no conflict when
  `stored == after`. For every other kind nothing changes.
  **The status is binary, so a membership row never reaches the reviewer as a
  conflict**: the stored value always equals `before` (no drift) or `after`
  (settled). The `_REASONS["membership"]` entry ("this person's membership in
  this group changed since the scene was absorbed") exists because
  `target_key` covers exactly the kinds in `_REASONS`
  (`test_every_judged_kind_has_a_target_key`), and the reading is what a
  resumed commit's `_outside_drift` compares against. Nothing should be
  written expecting the sentence to be shown.
- **Journal.** Recorded as kind `membership`, `ref {"kind": "groups", "id":
  gid}`, field `members:<actor ref>`, label as staged. It is not in
  `changes.BROWSABLE_KINDS` (that log is for bodies, `changes.py:20-27`).
- **Undo.** A new descriptor in `undo.probe`:
  `{"w": "membership", "kind": "groups", "id": gid, "actor": ref,
  "binding_reviewed": True}`. The review marker is derived only from the
  validated approved delta for that exact actor (9.4), never an unchecked
  payload. Its inverse retains that exact Members-slot consent; it does not
  approve any other actor or the Leader slot. A restore needing an inherited
  unresolved identity with no such recorded review refuses before any write.
  `read_value` returns `True`/`False` (is an effective member now); `restore`
  re-applies
  the inverse delta through `overlay.update_entity`. The compare-and-swap
  `expect` is that one actor's status, so undoing "Mara joins" is refused only
  if Mara's own membership moved since, not because Winifred's did. The
  generic `entity_fields` descriptor (`undo.py:298-304`, `:355-358`) would
  restore the whole line and refuse whenever any member changed; it stays for
  the editor's adopt path and other typed fields. New hand membership edits
  use the whole-field `group_members` descriptor (section 6.4). A per-actor
  restore uses `edited_line`, so it too keeps every other token, and
  never de-materializes the group (the module's stated limit,
  `undo.py:53-60`). Undo clears only this row's citation, because the key is
  per actor (section 9.3).
- **Older builds sharing the store.** An older build asked to undo a
  `{"w": "membership"}` or `{"w": "group_members"}` entry raises the base
  `UndoError` from `read_value`,
  which `post_journal_undo` does not map (`routes/campaigns.py:1305-1315`), so
  the older build answers 500. This is the pre-existing cost of any new writer
  tag in a synced store; it is named here, not fixed.

### 9.6 The review panel

`frontend/src/components/review/editRows.ts`'s `EDIT_GROUPS` gains a drawer
`{key: "groups", label: "Group membership", kinds: ["membership"]}`.
`AbsorbEditRow` renders a membership row like a bond (`AbsorbEditRow.tsx:211-
215`): before and after as spans, no textarea, because the only edit a
reviewer can make to "Mara joins" is to approve or reject it. The `StagedEdit`
kind union in `api/types.ts` gains `"membership"`.

### 9.7 What absorb still does not do

- Change `leader` (section 19, Q5).
- Record members on a group it creates in the same review (Q5).
- Propose a change for an actor who is not on the `Present:` line. A scene
  that reports an absent member's expulsion ("word came that Seraphine had been
  cast out") cannot be proposed; the reviewer records it in the editor.
- Infer membership from prose already on disk. Absorb reads a scene, not the
  library.

---

## 10. Story Graph (07-C3b)

### 10.1 Lifting the capstone's parking, for groups only

Capstone §33 parks "more graph node families" unless a correctness defect is
found, and §19.2 lists groups as optional. This spec lifts that for one
family, as roadmap work rather than continuity expansion: groups now have a
structured relation to the actors already on the graph, and the graph is
where a reader looks for "who is connected to whom". Standing facts stay
parked. `test_continuity_graph.py`'s pins change in the same PR, and the
capstone acceptance row for §33 keeps citing the same test.

### 10.2 Nodes and edges

- `NODE_KINDS` gains `"group"` after `"location"`. The pin becomes
  `set(NODE_KINDS) == set(canon.KIND_OF_PREFIX.values()) - {"fact"}`.
- `EDGE_KINDS` gains `"member_of"` (actor -> group) and `"leads"` (actor ->
  group), after `"birthday_of"`. Both are `source: "structural"`, ids from
  `edge_id(kind, actor, group)`.
- `PARTS` gains `"groups"` before `"names"`.
- **Which groups are nodes:** only a group with at least one edge to an actor
  node already in the payload. The capstone's rule for actors and locations
  (§19.2: only what some edge names) applies, and so does "do not dump": a
  group nobody in the story belongs to is not a node.
- **Which edges:** for each roster, `member_of` from each member that is an
  actor node, and `leads` from the leader when it is an actor node. The family
  never creates an actor node. An actor who belongs to a group but never
  appeared, felt or bonded stays off the graph, so membership cannot grow the
  Cast lens beyond the people in the story.
- **gm-only groups are drawn.** The graph is a GM-side page, and the editor
  already shows these records to the same reader. Their node carries
  `secrecy`, and the frontend marks it.
- Node fields (typed, no `meta` bag, §20):
  `{"id": "groups:salt-circle", "kind": "group", "label": "Salt Circle",
  "group_type": "guild", "secrecy": "public"}`.

### 10.3 Where it reads, and what it costs

The group listing is read in phase C beside `_ROSTERS` (`graph.py:339-343`),
because it is the same kind of read as the location listing there: a
whole-kind entity listing through the overlay. It is read as
`membership.campaign_index(cid)`, so section 7.6's detached rule holds on the
graph too. It runs in its own `_attempt`
with part `"groups"`, so an unreadable group file costs group nodes and edges
only. The family runs in `_assemble` after `"chronicle"` (which creates the
actor nodes) and before `"scene_ideas"`, reading `nodes.keys()` as the ideas
family already does.

Cost is one whole-kind listing per graph read, constant per request: the
"no N+1" rule of §19.7, pinned by a read-cost test beside
`test_the_graph_reads_location_names_once`.

### 10.4 Frontend

- `NodeKind` and `EdgeKind` in `api/types.ts` gain the new members, and
  `GraphPart` gains `"groups"`; the existing `ts_union` pin
  (`test_continuity_graph.py:1427-1432`) holds them to the tuples.
- `components/storyGraph/model.ts`: `Show` gains `groups` (label "Groups"),
  off by default. The Cast lens turns it on, alongside actors. `member_of` and
  `leads` join the edges drawn when both ends are visible.
- `layout.ts` places group nodes in their own band below locations, sorted by
  label, the way locations are stacked today.
- Node detail lists the group's members and leader as they appear on the
  graph, and links to the group record in the campaign.
- No new keyboard binding.

---

## 11. Structural presence through members (07-C3d)

### 11.1 The rule

One row added to the existing table (`context/activation.py:107-111`):

```python
"groups": (("leader", "led_by", True),
           ("members", "member_present", True),
           ("headquarters", "headquarters", False)),
```

and `"members"` added to `world_state._STRUCTURAL_FIELDS` (`:41`), so the
entry's `refs` carry it.

**For groups, `world_state` takes `leader` and `members` from the index, not
from the frontmatter.** Today it fills every structural field with
`entity_schema.parse_refs` (`world_state.py:242-244`), which keeps any ref of
any kind. A hand-written `members: groups:other` would then make a group
present whenever `groups:other` activates, which brings nested groups in
through the back door (section 2), and it would bypass section 7.6's detached
rule. So for a `groups` entry, `refs["leader"]` and `refs["members"]` are the
roster's `leader` and `members` from `membership.campaign_index(cid)` (one
index per turn, with its own fresh stamped listing on a memo miss);
`headquarters` and the other kinds' fields are parsed as today. A store with
no members, well-formed leader fields and no detached leader collisions
composes exactly as before. Removing a malformed/non-actor leader or a
colliding inherited leader is an intentional presence correctness change,
including in a store that records no members.

A group with a present member is present, with reason
`{"type": "member_present", "via": "characters:mara"}`. Field order makes a
present leader win over a present member, which wins over the headquarters.

### 11.2 What it does and does not change

- It opens the **owner gate** on lore owned by `groups:<id>`
  (`activation._owner_gate`). Those entries still need their keys, their
  timing and their budget, exactly as with a present leader. A keyless entry
  the group owns becomes always-on while any member is present, which is what
  "owned by the group" already means.
- It does **not** activate the group's own entry, and so does not pull in the
  group's state section (`assemble.py:751-761` pulls state for keyword-activated
  groups only).
- It does **not** change who **knows** anything. `actor.knows` treats a group
  owner as an object owner (`context/actor.py:41`, `:70-72`), and structural
  presence was already the scene's, not the NPC's (`world_state.py:208-214`).
- A gm-only or excluded group confers no presence, by the engine's existing
  rule (`activation.py:79-80`).
- **It cascades, as a present leader's group already does.** Through the
  structural fixed point (`structural_presence`, `activation.py:361-385`), a
  member-present group makes an item whose `holder` is that group present,
  which opens the gate on that item's owned lore too. The recall stage's owner
  gate reads the same present set, so recalled entries owned by the group or
  its items become eligible the same way. Both follow from one rule and are
  stated so nobody is surprised by them; section 17.6 tests the item cascade.

### 11.3 Inspector and wording

`loreReasons.ts` gains `case "member_present": return via ? \`member here:
${via}\` : "member here";`, and the reason union in `api/types.ts:1681` gains
`"member_present"`.

### 11.4 Why include it

The lore-activation spec named exactly this as the thing waiting on membership
("everyone in the Guild knows this is out until membership exists"). The
knowledge half of that sentence stays out (section 12.4). The presence half is
one table row on a seam built for it, it is inert until a user records members,
and without it a recorded membership changes nothing a player sees during play.
Section 19, Q1, asks the user to confirm.

---

## 12. Retrieval projections and their rules (07-C3c)

### 12.1 Visibility: what may reach a model

Any consumer that sends text to a model (generate, decide, or embed) applies
one filter before using a roster or an affiliation:

```python
def prompt_visible(roster: Roster, *, secret_framed: bool = False) -> bool:
    if roster.secrecy == entities.GM_ONLY:
        return False
    if roster.secrecy == entities.SECRET:
        return secret_framed
    return True
```

"Never sent to the model" means every model or it means nothing
(`snapshots.py:97-104`), and an embedding provider is a model. So 08's
SearchDocument text, 09's history section and any decide item drop gm-only
rosters before rendering or embedding a name.

**A `secret` roster is off by default too.** `secret` means the entry
"renders under a heading telling the model not to let uninvolved characters
voice or act on it" (`entities.py:26-31`). A narrator-prompt consumer that
rendered "Mara, of the Salt Circle" in plain history would put a secret
affiliation into the turn prompt with none of that framing. So a consumer
passes `secret_framed=True` only when it renders the name inside the same
secret-framed block the entry itself gets; otherwise a secret roster's name
and membership do not reach a prompt. Until 11-C2 rules on actor perspectives,
no actor-perspective consumer passes it. 07 marks the row; 11 owns the rule
past that default.

Metadata that is never rendered or embedded (08's `groups` field) is not a
prompt and may carry secret rosters' refs; the filter applies where the text is
built.

Every consumer intersects member refs with its own roster before rendering a
name: a dangling ref is a missing person, not a member to describe.

### 12.2 The per-scene projection (for 08)

```python
class SceneGroup(BaseModel):
    group: str                      # "groups:<id>"
    name: str
    secrecy: str
    present: tuple[str, ...]        # affiliated actors in the cast, sorted
    leader_present: bool

SCENE_GROUP_LIMIT = 8

def scene_groups(index: Index, cast: Iterable[str], *,
                 visible: Callable[[Roster], bool] | None = None,
                 limit: int | None = None) -> tuple[SceneGroup, ...]
```

The groups at least one of whose **affiliated** actors (members or leader) is
in `cast` (actor refs in `<kind>:<id>` form; a `characters/<id>` token is
normalized), ordered by the number of present affiliates descending, then
name, then ref. `visible` filters rosters (None keeps all, gm-only included,
marked by `secrecy`), and `limit` cuts the tuple (None does not). Deterministic,
and bounded by the cast times the groups each actor is in, not by the library.

**What 08 stores.** 08 settled that groups are metadata only, never document
text or embedded text (08 §4.6), and its `groups` field is "groups any cast
member belongs to now". So 08 calls `scene_groups(index, cast)` with
`visible=None` and `limit=None`: every affiliated group, uncapped, gm-only
rows included and marked, so 09's structural prefilter misses no group because
of a cap nobody decided on. 07 recommends affiliation (leader included) as the
meaning of "belongs to"; 08 should say so (cross-spec item).

**What a renderer passes.** A consumer that renders group names into a prompt
(09's history section) passes `visible=prompt_visible` and
`limit=SCENE_GROUP_LIMIT`. The constant is structural, not measured: it caps
one rendered line at a size where a scene with a large cast still lists the
groups most of it shares. It is tuned against real prompts later, in
conversation, with nothing committed.

**For 08's key.** 08's `groups` metadata must move when that scene's
projection moves. 08 hashes the canonical JSON of its `scene_groups(...)`
output as a non-file input (03-C1 `params`/`inputs`). Keying on `Index.digest`
would also be correct but would rebuild every scene's metadata when any group
anywhere changes; the per-scene projection moves only the scenes it affects.

### 12.3 Co-affiliation and membership lookups (for 09)

The exact calls 09 makes, by their 07 names:

| 09 needs | 07 call | Membership meaning |
|---|---|---|
| Group seeds for each present actor (09's table calls it `groups_for_actor(ref)`, the bundle draft's name) | `groups_for(index, actor)` | affiliated (roles say which) |
| Actors sharing a group with the speaker or addressee | `co_affiliates(index, actor)` | affiliated |
| The `group` relation: scenes where two or more people of a seeded group stood | `affiliated(index, group)`, joined with 08's per-scene metadata or `scene_actors` | affiliated, so a leader nobody listed still counts |
| A group's place, as a location signal | `index.groups[ref].headquarters` | n/a |

09's relation text says "two or more members"; read through 07 that is
`affiliated`, not `members_of`, unless 09 deliberately wants to exclude an
unlisted leader (cross-spec item). 07 provides the projections, not the scene
search.

**These are recall signals, not evidence.** Co-membership says two people are
connected; it does not say either knows what the other saw. 09 weights them;
07 asserts nothing about relevance.

### 12.4 Rules for 11 (epistemic retrieval)

- **Which set a group audience expands to.** 11's group overrides expand a
  `groups:<id>` audience through "effective members (07-C1)". 07-C1's
  `members` excludes a leader nobody listed (section 4.4), so "the Watch knows"
  read through `members_of` silently leaves out the Watch's leader. 11's call
  is `membership.campaign_index(cid)` (07-C2, which applies section 7.6's
  detached rule) followed by `affiliated` (recommended) or `members_of`; 11
  decides and says which (cross-spec item). Reading `parse_members` off
  `overlay.read_entity` directly would skip the detached rule and is not a
  supported path.
- **Membership never yields `ACTOR_KNOWN`.** A retrieval result may carry a
  `basis` such as `"co_affiliate"` as candidate provenance; it is never the
  basis of a knowledge classification on its own. A module or world rule that
  says "members of the Salt Circle know X" is a separate, explicit rule (11
  draft §8), and nothing in 07 creates one.
- **Membership is now, not then.** The index is current state. An actor who
  joined in scene 40 is in `members` when scene 10 is retrieved. A consumer
  must not describe a past scene with present membership ("Mara, of the Salt
  Circle, ...") as though it held then. History is a non-goal (section 19,
  Q7).
- **The secret-lore gap is 11's.** Today secret lore owned only by a group is
  known by no actor's own call: `actor.knows` returns True for a secret entry
  only when the actor is itself an owner (`context/actor.py:63-72`), and a
  group is not an actor. Membership makes a fix expressible ("an affiliate of an
  owning group knows it"), and that fix changes NPC prompts, so it is 11's
  decision (Q6), not a side effect of this spec.

### 12.5 Scene suggestions

No prompt change in 07. `suggest.py`'s status-annotated cast could name a
character's groups, and a cast suggestion could prefer co-affiliates, but both
change a prompt that the capstone keeps byte-stable for a campaign with no
controls set (`suggest.py:17-21`), and both are better judged once 09 shows
which signals help. The seam is `groups_for` (Q10).

---

## 13. Migration and existing data

### 13.1 No rewrite

- A store with no `members:` key anywhere means "no members recorded". Nothing
  runs at startup, after a data-dir move, or on read.
- A group whose body names its roster in prose keeps it there. Nothing parses
  the prose. The user records members in the editor when they want them, and
  the skills say how (section 13.3).
- A pre-existing `members:` line that is not refs (a hand edit, an import that
  passed frontmatter through) is the case `entity_schema` documents for
  `holder`, `leader`, `headquarters` and `habitat` (`:76-92`): left exactly
  where it is, refused only if a request sets it, contributing no members to
  any derived view, and shown in the editor as dangling chips the user can
  clear. An approved absorb row or a membership undo on such a group keeps
  every legacy token (section 4.3, `edited_line`); only the spacing around
  commas is normalised.
- A group file with no `members` key and one with an empty line read the same.
  The writers here never write an empty line; they remove the key.

### 13.2 The frozen campaign

It holds no groups (section 1.7), so every reader sees an empty listing:
no graph node, no presence reason, no snapshot segment, no index row.
`snapshot.json` must not move; section 17.6 makes that an acceptance check
rather than an expectation. `home/` is not edited to add membership coverage
(its README's rule); that coverage lives in ordinary tests built by the code
under test.

### 13.3 Skills and scripts that write groups

- `backend/scripts/create_world.py` resolves ref fields from
  `entity_schema.ref_fields` generically (`:317-331`, `:821`, `:906-908`), so
  a plan's `"fields": {"members": ["characters:Mara", ...]}` works once the
  spec is declared. The `create-world` skill's per-kind table and its groups
  example gain `members`.
- `world-card-integration`'s house format for groups keeps the roster
  paragraph and adds the `members` field beside it: prose says what each
  member does, the field says who they are.
- `populate-world-content` is not changed. It may leave `members` unset, which
  is the no-members state.

---

## Slices

Landing order within this spec: S1 -> S2 -> S3 -> S4 -> S5 -> S6 -> S7 -> S8.
S3 needs only S1. S4, S5, S6 and S7 each need S2 and can go in parallel after
it. S8 waits on 03 and can land at any point after S2.

### 07-S1: The members field, its reader and writer, and the editor

- **Delivers:** 07-C1 (part: the `members` declaration and validation, `parse_members`, `parse_leader`, `edited_line`, the editor, hand-edit journalling and safe copy/publication boundaries)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Needs (slices):** none
- **Scope:** `entity_schema.FIELDS["groups"]` gains `members` (section 4.1), with the `types.ts` mirror and the new field-table drift test (6.3). The new read-only module `store/membership.py` lands with `parse_members`, `parse_leader` and `edited_line` (4.3). `EntityEditor` shows Members chips and the picker through the mirror (6.1), plus S1's one-group collision diagnostics and explicit per-ref opt-ins (7.7). The campaign group GET returns Pydantic `membership_context`; `EntityUpdate` carries typed `group_ref_bindings` (7.7). The campaign entity route journals members with the raw/effective/unresolved `group_members` descriptor and exact-slot consent (6.4); its one-group overlay reader and central materialization guard, including dormant group refs on other flat kinds, land here (7.6, 7.7). Strict detachment read-modify-write and actor-creation/lifecycle preflight preserve identity markers here (7.7). Strict `_require_world_members` checks for group and dormant-Members push/promote also land here before membership can be published (5.5), including forced push and ledger-error refusal. `invisibleChangeHint` names typed fields, so a world-side membership change is never shown as an empty change before S3 (5.3). The `entities.py` docstring corrections (1.3) land here. Nothing reads membership yet, so no prompt moves.
- **Acceptance:** 17.1 (field, validation, leniency, legacy value survives, reclassify round trip, `edited_line`, hand-edit journal row); 17.3's push/promote identity and strict-ledger cases (including force); 17.8's group-editor cases; `test_lore_golden.py` and `test_frozen_campaign.py` unchanged.
- **Size:** M

### 07-S2: The inverse index, its routes and the actor pages

- **Delivers:** 07-C2 (full); 07-C1 (part: campaign effective membership with section 7.6's detached rule)
- **Needs (this spec):** 07-S1 (H)
- **Needs (other specs):** none
- **Needs (slices):** 07-S1 (H)
- **Scope:** Pydantic membership models, `prompt_visible`, `membership.Index`, `index_from_rows`, `world_index`, `campaign_index`, `groups_for`, `members_of`, `affiliated`, `co_affiliates` and the digest (7.1, 7.2). The root-keyed in-process memo (7.3). One overlay operation, `overlay.group_listing`, returning rows, pre-read stamps, roots and coupled ledger/group content digests from a fresh view (7.3). The detached rule and central materialization boundary (7.6, 7.7). The two `GET .../membership` routes, registered before the entity router (7.5). The Groups `ColumnSection` on `CharacterPage` and `PCPage` (6.2). Collision diagnostics already landed in S1 (7.7). No prompt reads the index yet.
- **Acceptance:** 17.2 (roles, overlay steps, digest movement, memo recompute and root stamp, two-store test, detached cases for the index, cache isolation, Pydantic serialization, overlay guard, routes and route order); 17.8's actor-page cases.
- **Size:** L

### 07-S3: Sync shows typed fields

- **Delivers:** 07-C1 (full)
- **Needs (this spec):** 07-S1 (H)
- **Needs (other specs):** none
- **Needs (slices):** 07-S1 (H)
- **Scope:** `sync._entity_blob` carries `fields` with `shown` names, resolved through one `overlay.View` per `incoming()` call, with the detached stranger marked (5.3). `IncomingReview` renders the field rows and drops "typed fields" from the hint again. The S1 push/promote identity guards remain in place; they are not deferred to this display slice. If Q4 is accepted, extending publication validation to `leader` belongs with the S1 guard and is named as a behaviour change there.
- **Acceptance:** 17.3's incoming field-diff rendering case; existing S1 publication safety tests stay green.
- **Size:** M

### 07-S4: Structural presence through members

- **Delivers:** 07-C3d (full)
- **Needs (this spec):** 07-S2 (H)
- **Needs (other specs):** none
- **Needs (slices):** 07-S2 (H)
- **Scope:** The `("members", "member_present", True)` row in `activation._STRUCTURAL`. `world_state` takes `leader` and `members` for groups from `membership.campaign_index` rather than raw `parse_refs` (11.1). The `member_present` reason lands in `loreReasons.ts` and the `types.ts` union (11.3). The member-presence addition has no switch and is inert without recorded members. Correctly formed, noncolliding leader fields keep their old composition; invalid or detached leader refs lose their accidental presence (11.1).
- **Acceptance:** 17.6 (golden and frozen snapshot unchanged, activation cases, item cascade, no presence through `groups:` members, `knows` unchanged, detached Winifred confers nothing, `loreReasons.test.ts`).
- **Size:** S

### 07-S5: Absorb membership proposals

- **Delivers:** 07-C3a (full)
- **Needs (this spec):** 07-S1 (H: `edited_line`); 07-S2 (H: `campaign_index` for the snapshot and the detached rule)
- **Needs (other specs):** none
- **Needs (slices):** 07-S1 (H), 07-S2 (H)
- **Scope:** Plain Pydantic extraction models and their portable structured-output schema, rendered in the prompt and passed to the existing generation call (9.1.1). The `membership_changes` section in `parse_output` and `templates/absorb/system.j2` (9.1). `group_snapshot(cid, cast)` with the "Affiliates here" segment (9.2). Staging, apply, conflict reading, journal, per-actor provenance, the `membership` undo descriptor and the review drawer (9.3 to 9.6). Host-generated lifecycle tokens bind proposals to their context-time groups before first save; their strict campaign ledger, lifecycle invalidation and ordered world dependency/capture barrier land here (4.5). Deferred commit targets, reversals and citation/diff publication follow section 4.5's reclassification/retirement rule, including resumed commits. The repo's prompt rules force the eval recordings, `build_absorb`'s member seed and needle, the new `absorb.null-membership` counterexample, and the top-level contract key-set test into this same PR. Without them the offline eval and `test_absorb_store.py` go red. The absorb system prompt changes for every store. No turn prompt changes.
- **Acceptance:** 17.4 in full.
- **Size:** L

### 07-S6: Story Graph group nodes and edges

- **Delivers:** 07-C3b (full)
- **Needs (this spec):** 07-S2 (H)
- **Needs (other specs):** none
- **Needs (slices):** 07-S2 (H)
- **Scope:** `NODE_KINDS` gains `group`, `EDGE_KINDS` gains `member_of` and `leads`, and `PARTS` gains `groups`. The family reads `campaign_index` in phase C (10.2, 10.3). The TS unions, the Groups Show toggle, the Cast preset and the layout band land in the same PR, because the `ts_union` pin fails if the unions and the tuples land apart (10.4). The capstone §33 lift for groups lands here, and only here.
- **Acceptance:** 17.5; the frozen campaign's graph entry unchanged.
- **Size:** M

### 07-S7: Retrieval projections

- **Delivers:** 07-C3c (full)
- **Needs (this spec):** 07-S2 (H)
- **Needs (other specs):** none
- **Needs (slices):** 07-S2 (H)
- **Scope:** Extend S2's `prompt_visible` coverage for retrieval, and add Pydantic `SceneGroup`, `scene_groups` and `SCENE_GROUP_LIMIT` in `store/membership.py` (12.1, 12.2), with the rules of 12.3 and 12.4 in its docstrings. `scene_groups` has no app caller yet; 08, 09 and 11 are its first callers. Absorb already calls `prompt_visible` through S5. Land it before 08's plan starts.
- **Acceptance:** 17.7.
- **Size:** S

### 07-S8: Persistent tier of the inverse index

- **Delivers:** none. 07-C2 is full at S2; this slice adds the optional cost-only tier the C2 contract allows.
- **Needs (this spec):** 07-S2 (H)
- **Needs (other specs):** 03-C1 (H: composite keys over collection digests, with a member filter for top-level `*.md` with `safe_id` stems); 03-C2 (H: liveness by construction); 03-C3 (S: the `materialized` record; until it lands, 05 cannot rebuild the index eagerly and it is rebuilt lazily on the next read)
- **Needs (slices):** 03-S2 (H), 03-S3 (H), 03-S4 (S), 07-S2 (H)
- **Scope:** Registers `membership_index` as a 03 artifact kind, keyed as section 7.4 says, with digests computed through the overlay helpers from S2. The edge is soft at spec level because the slice is optional: every consumer works from S2's in-process tier, and this slice only changes cost.
- **Acceptance:** `test_membership_cache_equivalence` (17.2): every consumer's output is equal with the tier on and off.
- **Size:** S

## 15. Contract

### 07-C1: Authoritative members and a leader on the group record

**Provides.**
- `entity_schema.FIELDS["groups"]` declares `members` (section 4.1), mirrored in
  `types.ts`.
- Stored as one comma-separated frontmatter line of `characters:<id>` /
  `pcs:<id>` refs on `<root>/groups/<id>.md`.
- `membership.parse_members(meta)` and `parse_leader(meta)`: lenient, never
  raise (section 4.3). `membership.edited_line(raw, actor, change)`: the one
  writer-side edit, on raw tokens.

**Guarantees.**
- The group is the only place a membership is stored. No actor record, sidecar
  or campaign ledger holds a copy.
- Field-format validation is format-only (kind and id), never existence.
  The separate first-copy identity boundary inspects world actor existence
  and strict detached/visibility refs (7.7); it does not add actor files to the inverse inputs.
- `leader` and `members` are independent, and nothing in this spec writes
  `leader`. `members` excludes a leader nobody listed; a consumer expanding a
  group audience says whether it means `members_of` or `affiliated`
  (section 4.4).
- Every writer this spec adds keeps every token of `members` it does not
  change, a legacy non-ref value included (section 4.3).
- Campaign effective membership is the `members` line of the file the overlay
  resolves, with no merge across layers, minus section 7.6's detached refs on
  an inherited roster. A campaign change materializes the group (whole-record
  copy-on-write) and diverges it from the world.
- A hand edit of `members` in a campaign is journalled and undoable
  (section 6.4). Group revision checks, journals and copies share the campaign
  lock; other entity kinds retain their existing unlocked residual (Q12).
- Sync shows declared entity fields, `members` included, on both sides of a
  pending change (section 5.3).
- `promote` and `push` of a group refuse a member ref that names no library
  actor or names this campaign's own detached actor (section 5.5). The strict
  identity ledger must be readable before publication; force cannot bypass it. Dormant
  Members refs on other flat kinds receive the same check before publication,
  so reclassification cannot bypass it.

**Failure behaviour.** A malformed stored value contributes no members and
never fails a read. A malformed submitted value is a 400 naming `members`.

### 07-C2: Derived inverse membership, cacheable under 03

**Provides.** `membership.Index` with `groups`, `by_actor` and `digest`;
`world_index(wroot)`, `campaign_index(cid)`, `index_from_rows(rows)`;
`groups_for`, `members_of`, `affiliated`, `co_affiliates`; the two
`GET .../membership` routes (sections 7.1-7.5).

**Inputs.** The world's `groups/*.md` (stems passing `safe_id`); for a
campaign, also the campaign's `groups/*.md`, `deleted.json`, `detached.json`
and the world the campaign names, all reached through overlay helpers.
Membership reads require strict current deleted/detached ledgers, including
memo/artifact hits (7.3). Failure reports an unavailable projection and a
repair error, never invented affiliations or stale fallback.

**Guarantees.**
- Deterministic: the same files give the same index and the same `digest`, in
  any process, with or without 03.
- `digest` moves if and only if some roster's `(ref, name, secrecy, group_type,
  leader, members, headquarters)` moves, which is everything the index hands out. A
  body, keys or group-state edit does not move it.
- In a campaign, a member or leader ref naming one of the campaign's detached
  actors is absent from an inherited roster (section 7.6).
- The memo is keyed on resolved roots, so a data-dir move or a second store
  holding the same id is never served another store's index.
- Every caller owns its Pydantic index. Mutating its nested models or dicts
  cannot mutate the memo or another caller's index.
- Refs are reported as stored, existence unchecked; gm-only rosters are
  included and marked.
- Never stale: both tiers freshly fingerprint effective group contents;
  the in-process tier also re-stats and the persistent tier uses those current
  content digests, so an edit by any writer
  (the app, an editor, a sync client) is seen on the next read.
- The persistent tier changes cost, never output. It is an optional addition
  once 03 lands (07-S8), not part of what C2 delivers: C2 is complete with the
  in-process tier.

**Failure behaviour.** An unreadable group file raises from the listing, as
`GET /{scope}/groups` does. Ledger failures raise `GroupIdentityConflict`,
reported as 409 with a repair explanation by the API. Membership-dependent
prompt/graph reads propagate failure before producing a payload or generation;
no caller may substitute an empty ledger, old index or raw-world actor refs.
03 being off or absent costs the persistent tier only.

### 07-C3a: Absorb membership proposals

**Provides.** The `membership_changes` section (prompt, parse, materialize,
apply, conflict, journal, undo, review drawer), section 9.

**Guarantees.**
- No new model call: the section rides the existing extraction.
- Nothing is written without an approved row in a saved review.
- Only known groups and known actors are staged; joins of members and leaves
  of non-members are never staged.
- Apply re-validates the row's shape (actor ref kind and id, group id)
  before any read, and is a per-(group, actor) delta on raw tokens, so
  distinct approved actors' effective states and outcomes commute. The
  neutral copy preparation uses only validated approved slots; untouched
  tokens outside those slots are preserved (9.4.1).
- Each row's citation and undo are per (group, actor): one row's undo never
  clears another's citation.
- The absorb context names only present affiliates with distinct roles,
  never gm-only groups; secret affiliations require the explicit secret block.

**Failure behaviour.** A missing or malformed section is `[]`. A row naming a
missing group or actor is dropped at staging and reported `failed` at apply.

### 07-C3b: Story Graph group nodes and edges

**Provides.** Node kind `group`; edge kinds `member_of`, `leads`; part
`groups` (section 10).

**Guarantees.** A group is a node only when an edge reaches it from an actor
node already in the payload; the family creates no actor node; one whole-kind
listing per read; two reads of an unchanged campaign are equal.

**Failure behaviour.** An unreadable listing costs group nodes and edges and
adds `groups` to `omitted`.

### 07-C3c: Retrieval projections and their rules

**Provides.** `prompt_visible`, `scene_groups` and `SCENE_GROUP_LIMIT`, plus the
C2 lookups, with the rules of section 12.

**Guarantees.**
- `scene_groups` is deterministic, affiliation-based, and bounded by the cast
  times groups per actor; `limit` cuts it only when a caller passes one.
- A consumer that follows `prompt_visible` sends no gm-only group's name or
  membership to any model, and no secret group's outside a secret-framed
  rendering.
- Membership is never offered as knowledge, and never as historical fact.

**Consumers' obligations** (section 12, by their 07 names):
- **08** stores `scene_groups(index, cast)` with `visible=None, limit=None` as
  metadata, never as document or embedded text, and keys that metadata on the
  projection's canonical JSON.
- **09** seeds with `groups_for`, finds co-affiliates with `co_affiliates`,
  and reads its `group` relation through `affiliated`; it renders names only
  through `prompt_visible` and `SCENE_GROUP_LIMIT`; co-affiliation is a recall
  signal.
- **11** expands a group audience from `campaign_index` plus `affiliated`
  (recommended) or `members_of`, stating which, and classifies knowledge
  without reading membership as evidence of it.

### 07-C3d: Structural presence through members

**Provides.** The `("members", "member_present", True)` row and the
`"member_present"` reason (section 11).

**Guarantees.** Without members, valid noncolliding leader fields compose
byte-identical prompts (`test_lore_golden.py` stands). Filtering malformed or
detached inherited leader refs intentionally corrects presence (11.1).
Presence opens owner gates only; it
never activates a group's own entry and never changes `actor.knows`.

---

## 16. Interaction with repo rules

- **Privacy.** Every fixture, example and test uses invented placeholders
  (Salt Circle, Seraphine, Mara, Winifred, Saltmarch). No constant here was
  measured on a real store: `SCENE_GROUP_LIMIT` is justified structurally and
  tuned later in conversation. gm-only membership never reaches a model
  (section 12.1).
- **Atomic writes (`test_atomic_guard.py`).** Every write goes through
  `entities.update_entity` / `overlay.update_entity`, which use
  `atomic.write_text`. No new writer touches a file directly.
- **Overlay guard (`test_overlay_guard.py`).** `campaign_index` reads groups
  and their stamps through two new overlay helpers, `overlay.group_listing`
  (rows and pre-read stamps together, section 7.3), so `membership` never builds a
  campaign path. The 03 persistent tier's digests go through the same helpers.
- **Paths guard.** All paths come from `entities`, `overlay` and
  `campaigns_paths`; nothing joins a home-relative path by hand.
- **Lock domain (`test_lock_domain_guard.py`).** `store/membership.py` is
  read-only. Absorb and undo retain their existing campaign lock; group edits
  and every group or dormant-group materialization now serialize the collision check and
  publication in that same domain (6.4, 7.7). Group editor journaling and
  revision checks are inside the hold. World demotion retains `hold_all`.
  Other entity-write races remain Q12's residual; do not claim the whole
  overlay backlog has been reviewed.
- **Import guard (`test_import_guard.py`).** `membership.py` imports
  `entities`, `entity_schema`, `overlay` and `statcache` at module scope;
  `continuity/graph.py` and `absorb/*` bind `from .. import membership`. No
  cycle: none of those import `membership`.
- **Pydantic v1/v2 and Android.** Plain BaseModel membership, API response
  and extraction models; no new dependency or desktop-path assumption. The
  response models serialize through the existing route shim. The new schema
  adapter is a leaf; `schemas.py` stays standard-library-only (9.1.1).
- **Detached runs.** No new run class and no new handler. Absorb stays a
  `review`-class run; its payload gains a section. The graph read is
  unchanged in class.
- **Metering and inference.** No new LLM call, route, task or embed call. The
  absorb system prompt grows by one paragraph on every absorb, a small fixed
  cost on a call that is already the longest generation in the app.
- **Regex view.** Absorb's transcript still goes through
  `regex.view.view(..., phase="prompt")` (`routes/scenes.py:3080`); nothing
  here reads transcript text.
- **Write token (`store/revision.py`).** Membership edits through the campaign
  entity routes are stamped by the activity middleware; absorb writes are
  inside `PUT /chronicle`, stamped the same way. World edits do not touch a
  campaign's token, as today.
- **Templates (`verify_templates.py`, evals).** The new section is in
  `absorb/system.j2`, and the group line is built in Python as it is today, so
  `verify_templates.py` has no new pair to compare. The offline eval requires
  every key of the absorb contract in the prompt (`evals/README.md:96-103`),
  which makes forgetting the template paragraph fail immediately.
- **Frontend list/detail pattern.** Groups use the existing `EntityEditor`, adding the collision diagnostics of 7.7;
  actor pages gain a read-only `ColumnSection`. No page builds a second rail.
- **Keyboard registry.** No new binding.
- **Frozen campaign.** Not edited; its snapshot does not move (section 13.2).

---

## 17. Tests and acceptance

### 17.1 Field and validation (C1)

- S1 alone delivers collision context and explicit binding controls. Adding
  Mara while retaining a filtered Winifred ref without its opt-in is refused
  before materialization; saving Members alone does not authorize that ref.
  Explicitly clearing it or opting into that exact slot permits the intended
  save. An opt-in for Members does not authorize the Leader slot.
- A detachment arriving after the editor's GET is rechecked under the save
  lock. Without consent for that new slot, the save refuses. Bindings for an
  invalid actor, an unsubmitted field or a non-group request are rejected.
  Older clients sending no binding list remain safe and can clear collisions.


- A hand save records a raw/effective `group_members` journal row, including
  when a filtered raw ref becomes effective with an unchanged string. Whole-
  field undo refuses later changes to any member, preserves legacy tokens,
  and restores effective non-membership without reintroducing filtered refs
  into the campaign copy. Snapshots also preserve unresolved inherited slots
  and exact Members binding consent. Clear a missing-world, non-detached ref,
  then attempt undo and create that campaign actor: undo conservatively omits
  that originally unresolved slot (section 6.4) and the actor acquires no
  unreviewed affiliation. Also
  clear an originally resolved world ref, delete its world actor, then create
  the replacement campaign actor before or after the undo attempt: current
  identity revalidation refuses the unreviewed restore in both orders. A
  malformed/unreadable ledger at restoration likewise refuses without writes,
  even with original bindings. Newly authored refs survive undo/redo without
  acquiring inherited-origin restrictions. A
  consented binding with unchanged raw/effective refs still journals the
  identity change. Opt into a missing-world Winifred slot, Undo, then create
  campaign Winifred: the conservative inverse removes that ref, so the actor
  acquires no affiliation or owner-gate effects. Redo restores the exact
  reviewed binding. It leaves leadership and unrelated fields alone.

- Reclassify a journalled group from groups to lore, recreate a different
  group at the old slug with the same Members value, and attempt Undo and
  Redo. Both refuse while the original is lore and leave the replacement
  untouched. Return the original to groups while the old slug is occupied:
  its target follows the suffixed id, and Undo/Redo affect only that original.
  Cover campaign and world-driven moves and preserve an independent owned
  copy's target. S1 covers `group_members`; S5 repeats the identity cases for
  the per-actor `membership` descriptor, whose Boolean CAS alone cannot prove
  group identity. Inject a journal suspension-write failure before the move:
  no record moves. Crash after the move but before completion, recreate the
  old slug with matching Members, and attempt Undo/Redo (and S5 resume):
  suspended targets refuse without replacement writes. Unverified recovery
  cannot reactivate them by matching bytes; successful completion follows
  only its original operation and preserves all actor-origin/consent data.
- Delete the original campaign group, then recreate its slug with matching
  Members: old Undo and Redo offers refuse without touching the replacement,
  journal or citations. Repeat with group -> lore -> delete -> recreate ->
  groups, and with world deletion where an inherited original disappears.
  Retained own copies and safe copy-down keep their offers. A corrupt,
  unreadable or unwritable retirement journal refuses deletion before any
  record removal or slug reuse; later repair cannot resurrect a retired offer.
  S1 covers `group_members` retirement and S5 repeats it for `membership`.
  Also accept take-world with matching Members, bind to another world containing
  the same group ref/Members, and attempt Undo/Redo: copy-removal retirement
  refuses both without changing the inherited replacement. Cover sync
  dematerialization and migration pruning; journal read/write failure preserves
  the owned file and manifest. A missing owned file never permits overlay
  fallback. Retained owned originals remain reversible across world rebinding,
  but restoring original-world actor refs against a different world requires
  the original exact-slot binding consent rather than matching actor ids.
- S5 captures a group token and stages a membership proposal, deletes or
  reclassifies the original, recreates the old slug with matching Members,
  then saves that review for the first time: the stale token fails without
  replacement record/citation/reversal writes. Repeat a lifecycle change
  during model generation and before staging, missing/malformed tokens,
  ledger read/write corruption and independent retained-owned copies. Fresh
  capture for the recreated group has a different token; ordinary field
  edits and safe copy-on-write preserve the original token. Token data never
  enters the model's extraction schema or prompt text. Pause a world move
  after dependent enumeration, create a new dependent campaign, and attempt
  capture before the move completes: the shared barrier blocks that capture.
  Recreate the vacated slug and attempt first save of an older review: it
  fails, or a post-move capture names the actual replacement with a fresh
  token. Verify deletion/demotion too, timeout without effects, root-binding
  changes, distinct-store isolation and capture/save/world lock order without
  holding the barrier across model generation. Change world binding during
  generation, between staging/first save and between reservation/resume, with
  matching refs/bytes in both worlds: captured-world work refuses without
  replacement writes/citations. Returning to the first binding cannot revive
  observed invalidation. Verified originally owned copies survive; inherited-
  origin copies without trusted receipts require fresh review. New capture
  cannot mutate old token provenance. Capture against A, bind to B, delete/
  recreate A's group, return to A before any provenance probe, and attempt
  staging, first save or resume: outstanding-source discovery invalidated the
  old work, so replacement writes/citations refuse. An unreadable captured-
  source ledger cannot silently omit a campaign from that lifecycle sweep.
- S5 checkpoints an applied membership row before publishing its journal and
  citation, then simulates a crash. Delete the group, recreate its slug with
  matching Members and resume: the saved target stays retired, no replacement
  citation/diff is overwritten, and old Undo/Redo refuse without writes.
  Repeat across reclassification, a suffixed return to groups and a crash
  before a later membership position was attempted. Resume follows the
  original's current target or fails a retired/non-group position; it never
  replays into a replacement. Corrupt/unreadable/unwritable matching commit
  progress refuses deletion before record removal. After retirement and slug
  recreation, corrupt progress and retry or make an unrelated save: strict
  commit reads refuse both without effects or rewriting the bad ledger. After
  repairing the ledger, retirement is still enforced. Supported legacy flat
  token maps (including tokens named `tokens` and `scenes`) and v2 documents
  retain their full account through ordinary mutations. A crash immediately
  after initial reservation already has durable membership targets to retire.

- `test_entity_schema.py`: `members` is a `multi` ref over `("characters",
  "pcs")`, after `leader`; a list of actor refs passes; `groups:`,
  `locations:` and a bare name are refused; a blank clears; duplicates pass
  the boundary.
- New `test_the_frontend_ships_the_same_field_table` (in
  `test_entities_store.py`): parses `ENTITY_FIELDS` from `types.ts` and holds
  every kind's keys, order, widgets, `kinds`, `multi`, `min`/`max`, `options`
  and `source` to `entity_schema.FIELDS`.
- Interleave group tombstone addition with library/asset tombstone removal:
  campaign locking serializes both and both final markers survive. Corrupt,
  wrong-type, undecodable or unreadable `deleted.json` refuses every mutation
  before associated record/asset effects and preserves its bytes. Unrelated
  marker preservation covers repoint and reattachment too.
- `test_membership.py::test_parse_members_is_lenient`: non-strings, legacy
  names, non-actor refs, unreferenceable ids and duplicates; never raises.
- A legacy `members: Mara, Seraphine` survives an unrelated body save through
  the route byte for byte, and a route save that sets it to a legacy value is
  a 400.
- Reclassify a group with members to lore and back: the line is untouched and
  the index answers nothing, then the same rosters again.
- `edited_line`: a join on `Mara, characters:seraphine` gives
  `Mara, characters:seraphine, pcs:winifred` (the legacy token kept); a leave
  removes only tokens equal to the actor; a join of an actor already present
  returns the tokens unchanged.
- Hand edit: a members-only save through the campaign entity route writes one
  journal row (field `members`), and its undo restores the line; a body-only
  save writes no members row.

### 17.2 Inverse (C2)

- Corrupt a writable detachment ledger containing Winifred's marker, then
  attempt campaign character and PC creation: both refuse before actor files
  or other mutations and leave its bytes unchanged. Add/remove/reattach/
  repoint paths cannot replace it with a valid set that drops Winifred.
  Lifecycle preflight happens before record moves/deletes/publication; a world
  operation checks every affected campaign before changing the world record.
  Repair the ledger while retaining Winifred, then create Mara: both markers
  survive, inherited Winifred remains filtered, and unsafe copy/publication
  still refuses. Include malformed JSON, invalid entries and read failures,
  with a real sibling campaign so a late preflight error leaves neither world
  nor earlier dependents changed.
- Malformed, wrong-shaped, undecodable or unreadable identity/visibility
  ledgers refuse membership projections with `GroupIdentityConflict`, even
  with a warm memo and unchanged stat values. No stale/empty/partial fallback
  is returned. Repair permits fresh reads; a valid ledger change with its
  timestamps preserved still invalidates the cached ledger digest. Concurrent
  ledger changes cannot pair new digests with old rows or cause retry loops.
  Owned refs remain authored and their entity reads still work; World reads
  are independent of campaign ledgers. Include duplicate and legitimate
  asset/library tombstone spellings.
- With unrelated campaign/world Winifreds, ledger failures yield no foreign
  `member_present` reason, absorb standing affiliation or graph edge: their
  membership-dependent read fails before a payload or LLM generation is
  produced. Assert shared fake LLMs receive no call. This behavior holds on
  cold/warm memo, warm consumer-payload caches and, when S8 lands, persistent
  artifact hits.
- An inherited member/leader ref naming no world actor requires consent even
  before a campaign actor exists. An unrelated body copy followed by campaign
  actor creation cannot grant affiliation: the copy refuses first. Creation
  followed by copy likewise refuses on detachment. Explicitly removing the
  slot or consenting to present/future campaign binding permits the copy.
  Neither copy order can silently grant membership or leadership.
- Interleave campaign actor creation with an inherited group body save:
  creation cannot land between collision validation and copy publication.
  Both lock orders refuse an unresolved slot without consent; a valid reviewed
  copy holds the lock through manifest/final publication. Group route revision
  checking and journal reads/sealing also share the hold.


- A malformed, wrong-shaped or unreadable present detachment or visibility
  ledger refuses group materialization, body saves and automatic copy-down before record,
  manifest, journal or citation writes. Repairing it restores the inherited
  detached filter: no own copy can have made the collision permanent. An
  absent ledger and a valid empty list remain legitimate. Include non-string
  entries, malformed ref strings, decode/access errors, legitimate non-actor
  entries and a fully opted-in request; none may turn a read error into empty.
- A world group reclassified to lore retains its dormant refs. Its campaign
  body copy and subsequent campaign group conversion cannot bind detached or
  missing-world actors without review: the first copy refuses before record
  or manifest writes, including on ledger errors. Automatic copy-down of a
  dormant-ref record preflights every affected campaign before any dependent
  record or asset copy and before world deletion. Resolve the library fields
  before retry. Ordinary non-group records without dormant actor refs retain
  existing copy behavior.
- An inherited group with a colliding member/leader refuses unrelated body
  saves, another actor's delta, reclassification and automatic copy-down
  without record, manifest or citation changes. Explicitly clearing a collision or consenting to every retained
  `(field, actor)` slot permits an atomic final copy. A world-delete
  refusal happens before any dependent is copied or any world record removed.
- Joining a filtered raw ref changes effective membership if it is the only
  affected slot. A second unreviewed member or leader collision blocks it.
  Per-actor undo restores non-membership while preserving other actors'
  tokens and citations; leader/member slots remain independent.

- A cached/populated caller `View` followed by a detachment or tombstone edit
  cannot poison membership memo hits. Assert the new answer on multiple
  reads after the racy window and verify pre-read stamps/absence parents.

- `index_from_rows` on Salt Circle (leader Seraphine, members Mara, Winifred,
  Seraphine) gives the `by_actor` roles of section 7.5.
- Campaign overlay: a world group inherited, then a campaign copy with a
  different `members`, then a tombstone; `campaign_index` follows each step.
- `digest` moves on a members, name, group type or headquarters edit and not on a body
  edit or a `groups/<id>/state.md` write.
- The in-process memo recomputes after an external write to a group file
  (written with `os.utime` moved, no app call), and after the first
  materialization creates a thin campaign's `groups/` (the root stamp).
- Two stores holding the same cid with different `members`: switching
  `GRIMOIRE_HOME` between them (files older than `RACY_WINDOW_NS`) gives
  different answers (the root-keyed memo, B1).
- Detached: a campaign-made Winifred and a world group listing a later world
  `characters:winifred` give no affiliation for her in `campaign_index`, no
  `member_present` reason, no "Affiliates here" entry, and no graph edge; the
  same ref in the campaign's own copy of the group is kept.
- Mutating a returned index's dicts, nested rosters or affiliations does not
  change the next answer, on a memo miss or hit. The Pydantic models serialize
  with the same JSON field names and values on v1 and v2.
- `test_overlay_guard.py` passes with `membership.py` present (no raw campaign
  root).
- When 03 lands: every consumer's output is equal with the persistent tier on
  and off (`test_membership_cache_equivalence`).
- Routes: `GET .../membership` and `?actor=`; 400 on `actor=groups:x` and on a
  malformed ref; 404 for an unknown campaign; route order holds
  (`test_route_order.py`).
  A well-formed dangling actor ref returns its stored affiliations, without
  an actor-existence read, matching `groups_for`.

- S2/S8: edit Members and change `secrecy: public` to `secrecy: secret`
  while preserving the complete stat signature. World/campaign memo hits and
  consumer caches see fresh membership/secret framing on the next read and
  agree with the persistent tier. Fingerprints derive from exact row bytes;
  concurrent preflight/compute mismatch returns uncached without a later
  digest certifying earlier rows.

### 17.3 Sync (C1)

- An incoming world change that touches only `members` carries a `fields` row
  whose two sides differ, and `IncomingReview` renders it (no
  "invisible change" hint).
- `promote` and `push` of a group naming a campaign-made Mara raise
  `DanglingReferenceError`; with Mara promoted first, they succeed.
- Reclassify a campaign-owned group naming detached Mara to lore, then
  promote or push it and attempt world reclassification back to groups:
  publication refuses its dormant member before any world/manifest writes.
  Include colliding world/campaign actors and malformed/unreadable ledgers;
  current kind and force cannot bypass identity validation. A non-group
  record without valid dormant Members refs retains existing publication.
- For both push (including `force`) and promote, unrelated campaign/world
  actors sharing Winifred's slug plus a malformed or unreadable detachment
  ledger raise `GroupIdentityConflict` before world-record, manifest,
  detachment or asset writes. Repairing the ledger still refuses that
  campaign-owned ref as `DanglingReferenceError`; removing it or promoting
  the actor permits retry. Include invalid entry types/refs and decode/access
  failures, while an absent or valid empty ledger remains legitimate.

### 17.4 Absorb (C3a)

- Permute approved `Mara leaves` / `Winifred joins` on an inherited group
  with Mara effective and Winifred filtered: both orders have the same final
  effective members, applied/failure results by edit identity, per-actor
  citations and undo snapshots. Each undo preserves the other actor's change.
- Reject, corrupt or remove the actor behind the Winifred join: it supplies
  no preparation authority. Remaining unapproved member/leader collisions
  still refuse the copy. A same-batch body edit can prepare a safe baseline
  without making any later actor a member early.
- With inherited effective Mara and dangling, non-detached Winifred, permute
  their approved leaves: Winifred's safe cleanup executes first, both rows
  retain original-position receipts and both leave in either input order.
  The reviewed cleanup leave retains exact-actor consent in its per-actor
  reversal; undo never reintroduces an unresolved slot without that review.
  Two unresolved dangling refs with no safe single-row copy fail the group
  consistently without copying. A pending/unconfirmed cleanup is not treated
  as an applied prerequisite on resume. Body rows cannot bypass this ordering.
- Interrupt after the first group write but before the next row: resume uses
  the frozen approvals, does not mistake neutral preparation for an applied
  join or outside drift, and retains the existing unconfirmed-row behavior.
  Entirely settled batches make no copy; no membership is applied ahead of
  its per-position receipt. Preserve legacy tokens outside approved slots.


- A leader-only actor may join membership; a listed leader may leave while
  retaining leadership. The snapshot spells those standing roles differently.
- Secret group names, state and affiliations appear only with the secret
  warning block; gm-only group names never appear anywhere in absorb text.
- The model-derived schema satisfies `schemas.check` on Pydantic v1/v2,
  covers every extraction section/field, preserves wire names and is identical
  in the prompt and generation arguments. Null optional updates keep the
  stored value; an explicit empty string retains its documented clear meaning.

- `parse_output("{}")` gains `membership_changes`;
  `test_identity_fields_live_inside_rows_not_the_top_level`'s key set is
  updated in the same change.
- The absorb recordings that parse as JSON (`absorb.compliant`,
  `absorb.no-summary`, `absorb.laundered`) gain `"membership_changes": []`.
  Without it each trips an extra check and a counterexample fails its declared
  set equality (`evals/README.md:383-412`). `absorb.truncated` needs nothing:
  the grader stops at `absorb.json` when no object is recoverable. A new
  counterexample, `absorb.null-membership`, holds `[null]` and must trip
  exactly `absorb.membership_changes`.
- `build_absorb` (`evals/cases.py:325`) already creates the Salt Circle; it
  gains Mara as a member, so offline replay renders the "Affiliates here"
  segment, and the case adds that segment as a prompt needle.
- The `campaign_flow` cassette's absorb reply is unchanged (an absent section
  is `[]`), and `test_llm_fakes.py` still matches the re-rendered prompt.
- Materialize: a join for Mara stages one row with subject Mara and field
  `members:characters:mara`; a join for a member, a leave for a non-member, an
  unknown group, an unknown actor, an actor whose id is not `referenceable`
  and a bad `change` word stage nothing; a reply that says join and leave for
  one pair stages the first.
- `group_snapshot(cid, cast)`: unchanged when no present actor is affiliated;
  names present affiliates only; a present leader appears once, marked
  "leader", whether or not also listed; gm-only groups absent.
- Apply: two approved rows on one group both land (deltas commute); a join on
  a group whose line is `Mara, characters:seraphine` keeps `Mara`; a row whose
  target already holds is `skipped`; a join for a deleted actor is `failed`; a
  leave of a dangling ref lands; forged rows (`actor: "groups:x"`, an actor id
  with a comma, a target kind other than `groups`) are `skipped` before any
  read.
- A row for `pcs:winifred` with field `members:characters:mara`, a blank field
  or a non-string field is skipped before reads/writes. It supplies no batch
  preparation authority, no journal or citation and cannot overwrite or forget
  Mara's existing provenance. Repeat at the resumed/frozen-row boundary.
- Settled, never conflicted: an outside add of Mara before save makes her join
  row settled and `skipped`; an outside removal of Winifred makes her leave
  row settled; an outside edit to another member touches neither. No
  membership row ever appears in `check_conflicts`.
- Undo: undoing "Mara joins" after "Winifred leaves" landed succeeds and leaves
  Winifred's citation in `provenance.json`; undoing it after Mara was removed
  by hand is refused with `CONFLICT`.
- Frontend: `SceneReview.test.tsx` shows a membership row in the "Group
  membership" drawer, with no textarea.

### 17.5 Graph (C3b)

- `test_graph_tuples_are_pinned` updated (section 10.2), and the `ts_union`
  pin covers `NodeKind`, `EdgeKind` and `GraphPart`.
- A group with Mara as a member and Mara appearing in a scene gives one group
  node and one `member_of` edge; a member who never appeared gives no edge and
  no actor node; a group with no actor-node affiliate gives no node.
- An unreadable group file adds `groups` to `omitted` and costs nothing else.
- Read cost: the group listing is called once per `graph.build` at both sizes
  (beside `test_the_graph_reads_location_names_once`).
- Frontend: `model.test.ts` for the Groups toggle and the Cast preset;
  `layout.test.ts` for the band.

### 17.6 Presence and prompts (C3d)

- `test_lore_golden.py` passes unchanged.
- `test_frozen_campaign.py` passes with `snapshot.json` unchanged. This is an
  acceptance criterion: if it moves, the change has altered something a store
  without groups reads, and that is a defect here, not a snapshot to
  regenerate.
- Activation: lore owned by `groups:salt-circle` with key "ledger" activates
  when Mara (a member) is present and the key matches, with reason
  `member_present` via `characters:mara`; it does not when no affiliate is
  present; a present leader reports `led_by`, not `member_present`; a gm-only
  group with a present member confers nothing.
- Cascade: an item whose `holder` is `groups:salt-circle` becomes present
  when Mara is, and its owned lore activates on its key.
- A hand-written `members: groups:other` confers no presence through
  `groups:other` (activation reads the index, not raw refs).
- `knows` is unchanged for every combination above
  (`test_context_actor.py`-style table).
- Frontend: `loreReasons.test.ts` row for `member_present`.

### 17.7 Projections (C3c)

- `scene_groups` ordering, uncapped by default, `limit` when passed,
  affiliation (a leader-only affiliate counts), and `characters/<id>`
  normalization.
- `prompt_visible`: public kept, gm-only dropped, secret dropped unless
  `secret_framed=True`.
- `co_affiliates` excludes the actor itself and unions member and leader.

### 17.8 UI pattern (C1)

Per CLAUDE.md's list/detail tests: in `EntityEditor.test.tsx`, clicking a
group row shows the read-only view with a Members side-section of chips (no
textarea), a chip navigates to the character page, a dangling member renders
as a dangling chip, **Edit** reveals the members picker, and `+ New` opens the
form directly. In `CharacterPage.test.tsx` and `PCPage.test.tsx`, the Groups
column section lists the actor's groups with the leader suffix, and is absent
for an actor in no group.

### 17.9 Gate

`make check` green, with the ratcheted lint baselines re-recorded only where a
finding was actually resolved.

---

## 18. Non-goals

- Membership as knowledge, and `known_by: groups:<id>` (11, or a follow-up).
- Membership history (join and leave dates, "member as of scene N").
- Mining existing prose for members, at startup, in a maintenance run, or in
  absorb.
- Nested groups or group-to-group relations.
- Rendering a member list into the turn prompt beside an activated group.
- A field-level sync merge for `members`.
- Absorb proposals for `leader`, or for members of a group created in the same
  review.
- Faction mechanics (13).
- A "join group" action on actor pages.
- Scene-suggestion prompt changes.

---

## 19. Open questions

Each with a recommendation, so it can be decided quickly.

1. **Should a present member make the group present (C3d)?** It is what makes
   recorded membership visible in play, and it is inert until members are
   recorded. **Recommend: yes, as specified.**
2. **Should the leader count as a member?** Folding them would lose "leads but
   is not one of them". **Recommend: no; report roles separately, and use
   `affiliated` (members plus leader) where the distinction does not matter.**
3. **Field-level sync merge** ("take the world's copy but keep my members")?
   It is the one place whole-record CoW hurts membership. It is also new merge
   semantics for one field in an engine that has none. **Recommend: not in
   07; revisit if diverged groups turn out to conflict often in practice.**
4. **Should push/promote also check `leader`?** Same failure as members, but it
   would refuse pushes that succeed today. **Recommend: yes, in slice B, named
   in the PR as a behaviour change.**
5. **Absorb scope: leader changes, and members of a group created in the same
   review?** Both are real story moments; both add apply ordering or a second
   field to an already large section. **Recommend: defer both; ship join and
   leave first, and judge from `--live` evals whether models report membership
   reliably at all.**
6. **Should affiliates know secret lore their group owns?** Today no actor
   does (section 12.4). The fix changes NPC prompts. **Recommend: 11 decides,
   with this as a named input; not a 07 change.**
7. **Membership history?** Retrieval over old scenes would benefit, and 11's
   epistemics might. It is a second store to keep consistent with the field.
   **Recommend: defer; the change journal already records reviewed
   membership changes with their scene, which is enough to build history later
   if 09 or 11 shows the need.**
8. **Render members in the turn prompt beside an activated group?** It
   duplicates the roster paragraph most groups already carry and grows every
   activation. **Recommend: no in 07; 09's history section is the better place
   to test whether structured rosters help.**
9. **A filter box on the members picker** for a large cast? **Recommend: ship
   with the existing picker; add a filter if it is missed.**
10. **Scene suggestions using membership?** **Recommend: after 09, using
    `groups_for`, behind the suggestion prompt's existing byte-stability
    rule.**
11. (Settled: `ROADMAP-CHECKLIST.md` now carries the C3a-C3d split and the
    C3c edges.)
12. **Lock campaign entity routes?** **Settled for groups: yes in 07.**
    Hold the campaign lock across group revision checking, journals and
    writes, and across every inherited-group collision check/publication,
    including manifest updates (6.4, 7.7). Other kinds remain outside this
    change; reviewing and locking their overlay writes is separate work.

---

## 20. Review record

The spec gate was run with a substitute adversarial reviewer (2026-10-10):
2 blocking, 8 should-fix and 14 minor findings. Each was checked against the
code before it was folded in.

**Changed.**

| Finding | Resolution | Where |
|---|---|---|
| B1 memo keyed by id, so a data-dir move or a second store with the same cid is served the other's index | Keyed on resolved roots; a two-store test | 7.3, 15 (C2), 17.2 |
| B2 a delta written from `parse_members` erases legacy tokens | `edited_line` edits the raw token list; apply and undo use it; test | 4.3, 9.4, 9.5, 13.1, 17.1, 17.4 |
| S1 a campaign's detached actor reads as a member of an inherited world group | Detached refs dropped from inherited rosters in `campaign_index`; activation, absorb and the graph all read through it; `detached.json` is an input | 7.6, 7.3, 7.4, 9.2, 10.3, 11.1, 17.2 |
| S2 apply trusts client `actor`/`group` | Shape check before any read, `referenceable` at staging too | 9.3, 9.4, 17.4 |
| S3 memo needs raw campaign paths; absent `groups/` not vouched for | `overlay.group_listing` returns rows, stamps and roots from a fresh view; parent stamps cover absence | 7.2, 7.3, 16 |
| S4 digest covers less than the index hands out | Digest covers every `Roster` field; per-scene projection recommended for keys | 7.2, 15 (C2) |
| S5 consumer contracts imprecise against 08, 09, 11 | Exact calls and membership meaning per consumer; `members` excludes an unlisted leader; 08 stores uncapped metadata | 4.4, 12.2, 12.3, 12.4, 15 (C3c) |
| S6 secret rosters reach prompts without secret framing | `prompt_visible` drops secret unless `secret_framed=True` | 12.1, 15 (C3c), 17.7 |
| S7 hand membership edits neither journalled nor locked | Route journals raw/effective members with `group_members`; group checks/journals/copies now lock; other entity kinds remain Q12's residual | 6.4, 15 (C1), 16, 19 |
| S8 one provenance key for every membership row on a group | `field` is `members:<actor ref>` | 9.3, 9.5, 17.4 |
| M1 Q11 stale | Marked settled | 19 |
| M2 recordings list wrong; replay never renders "Affiliates here" | `truncated` excluded; `build_absorb` seeds Mara as a member and a needle | 17.4 |
| M3 membership conflict can never fire | Stated; `_REASONS` entry kept for `target_key` and `_outside_drift` | 9.5, 17.4 |
| M4 activation's raw `parse_refs` admits `groups:` members | Activation reads `leader`/`members` from the index | 11.1, 17.6 |
| M5 presence cascades unstated | Item-holder and recall cascades stated; item cascade tested | 11.2, 17.6 |
| M6 slice A leaves world-side membership changes invisible | Hint wording moved into slice A (07-S1) | 5.3, Slices |
| M7 older builds 500 on a membership undo | Named as the cost of any new writer tag | 9.5 |
| M8 journal repoint skips membership targets | Corrected: both membership writer tags follow the original record; restoration refuses non-group targets | 4.5, 17.1 |
| M9 absent actors cannot be proposed | Listed | 9.7 |
| M10 shared memoized index mutable; pool size unstated | Immutable index; `max_entries = 64` | 7.1, 7.3 |
| M11 per-item roster reads in sync; world side named via campaign view | One view per `incoming()`; detached stranger marked | 5.3 |
| M12 leader-and-member rendering unspecified | Named once with distinct leader/member role labels | 9.2 |
| M13 draft items dropped silently | One sentence each | 1.8 |
| M14 second stale claim in the same docstring | Corrected with the first | 1.3 |

Slices added (8 slices). 07-C2 was re-worded so that the 03-backed
persistent tier is an optional addition (07-S8) and not part of what C2
delivers; C2 is full at 07-S2.

**Rejected.** None. S1's identity filter is applied to inherited rosters;
a campaign's own copy names campaign actors. The actual Codex gate required
locking all group-copy validation/publication spans, superseding the earlier
substitute-review decision to leave group writes unlocked. Other entity kinds
remain Q12's residual; the central group guard covers the layer transition.

**Cross-spec items for the coordinator** (07's side is stated; the other
specs are not edited here):
- 08: store `scene_groups(index, cast)` uncapped and unfiltered as metadata,
  and state that "belongs to" means affiliated (members plus leader).
- 09: rename `groups_for_actor(ref)` to 07's `groups_for(index, actor)`, and
  state whether the `group` relation's "two or more members" means
  `affiliated` (07 recommends) or `members_of`.
- 11: group-audience expansion reads `membership.campaign_index` then
  `affiliated` (recommended) or `members_of`, stated explicitly; secret
  rosters stay out of prompts unless secret-framed until 11-C2 rules.
