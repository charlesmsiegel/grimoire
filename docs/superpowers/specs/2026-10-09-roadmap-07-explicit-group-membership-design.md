# 07. Explicit group membership

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 07 in `ROADMAP-CHECKLIST.md`. Lane: "Now" (no hard upstream);
feeds the cache lane at 08 and the retrieval lane at 09 and 11.
**Baseline:** `main` at `35c1fb7`.
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
| 03-C1 composite keys over collection digests and non-file inputs | 03 | The persistent tier of the inverse index (section 7.4): a key over the world's and the campaign's `groups/*.md` digests plus the tombstone ledger | Soft. Section 7.3's in-process tier answers every caller without it |
| 03-C2 liveness by construction | 03 | An edit to any group file moves the key, so a stale inverse is unreachable rather than invalidated | Soft, same reason |
| 03-C3 the `materialized` record | 03 | Lets 05 rebuild a hot inverse eagerly after an external edit | Soft. Without it the inverse is rebuilt lazily on the next read |
| Continuity graph (capstone Slices A-G, landed) | not a roadmap contract | Section 10 adds one node family and two edge kinds to `store/continuity/graph.py` | Existing code, not an edge |

There is no hard upstream. Nothing here calls a model, so nothing depends on 01
or any 01x item.

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 07-C1 authoritative members and leader on the group record, with overlay semantics | 11; every reader below | The stored fact everything else derives from; 11 needs its secrecy and "membership is not knowledge" rules |
| 07-C2 derived inverse membership, cacheable under 03 | 08, 09 | Actor -> groups and group -> members without re-reading prose; a digest to key derived documents on |
| 07-C3a absorb membership proposals | (play) | Membership that changes in the story reaches the record through review, never silently |
| 07-C3b Story Graph group nodes and edges | (UI) | `member_of` and `leads` edges on the existing graph payload |
| 07-C3c retrieval projections and their rules | 08, 09, 11 | Per-scene "groups relevant", co-membership, and the visibility rules a prompt-bound consumer must apply |
| 07-C3d structural presence through members | (prompt) | A group whose member is in the scene opens the gate on lore the group owns |

C3 is split into C3a-C3d (the checklist lists one C3, "absorb, graph and
retrieval integration points"). The split lets 08, 09 and 11 cite the one
projection they consume (C3c) rather than all of C3. The final report says so,
and the checklist should be amended in the same PR that accepts this spec.

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
change.

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

### 4.4 Leader and members are independent

`leader` stays its own field and is never written by anything this spec adds:

- naming a leader does not add them to `members`;
- a leader need not be a member (a patron, a figurehead, an outside handler);
- removing a member never touches `leader`, and vice versa.

These are fiction facts the author controls. Derived views report **roles**
(`member`, `leader`, or both) rather than folding one into the other, and
consumers choose. Section 7.2's `affiliated` is the one helper that unions
them, for the retrieval signals where the distinction does not matter.

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
- `IncomingReview.rowsOf` (`IncomingReview.tsx:46-64`) appends a row per field
  after Name and before Body, and `pairs` lines them up as it already does.
- `invisibleChangeHint`'s entity sentence drops "fields" from what it cannot
  show; it still names keys, owners and secrecy, which remain unsent.

This is general to every entity field (a world-side `leader` or `climate`
change becomes visible too), because a membership-only rule would be a second
code path for one key.

### 5.4 Pins and composition

Nothing new. A sync pin (`sync_pins.json`) freezes a group against world
updates, membership included, and `composition` reports its state from the
same hashes.

### 5.5 Push and promote: no strangers in the library

Publishing a group whose `members` names this campaign's own actor is the
greeting failure in a different shape. A campaign-made actor's id names nothing
in the world at birth and is marked `detached` (`overlay._mark_campaign_owned`,
`:1514-1566`); once the group is in the library, a sibling campaign that made
its own actor under the same slug reads that stranger as a member, and the
world reads a dangling chip.

So `promote` and `push` of a `groups` record run a check beside the greeting
one (`sync._promote_flat`, `:751-779`; `_push_locked`, `:1070-1095`):
`_require_world_members(cid, wroot, text, ref)` raises `DanglingReferenceError`
naming the first member ref that either has no world record
(`wroot/<kind>/<id>/character.md` or `pc.md` absent) or is in this campaign's
`detached` set. The message tells the user to promote that actor first or
remove them from the group. A ref that does not parse is ignored here, as the
greeting check ignores a hand-edited name (`sync.py:861-862`).

Whether the same check should cover `leader` is open (section 19, Q4): it is
the same failure, but adding it would refuse pushes that succeed today.

`demote` (world -> dependent campaigns) copies the whole record down and needs
nothing.

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

---

## 7. Derived inverse membership (07-C2)

### 7.1 The shape

`store/membership.py` (read-only; it writes nothing and takes no lock):

```python
@dataclass(frozen=True)
class Roster:
    ref: str                     # "groups:salt-circle"
    name: str                    # frontmatter name, else the id
    secrecy: str                 # entities.normalize_secrecy(...)
    leader: str | None           # parse_leader
    members: tuple[str, ...]     # parse_members, stored order
    headquarters: str | None     # first well-formed "locations:<id>", or None

@dataclass(frozen=True)
class Affiliation:
    group: str                   # "groups:<id>"
    roles: tuple[str, ...]       # ("leader",), ("member",) or ("leader", "member")

@dataclass(frozen=True)
class Index:
    groups: Mapping[str, Roster]                     # by group ref, sorted by ref
    by_actor: Mapping[str, tuple[Affiliation, ...]]  # by actor ref; groups sorted by ref
    digest: str                                      # section 7.2
```

### 7.2 The functions

```python
def index_from_rows(rows: Iterable[Mapping[str, object]]) -> Index
    # Pure. `rows` are `list_entities` summaries of kind "groups" (each carries
    # `id`, `name` and its frontmatter). No I/O, no clock.

def world_index(wroot: Path) -> Index          # entities.list_entities(wroot, "groups")
def campaign_index(cid: str, *, v: overlay.View | None = None) -> Index
                                                # overlay.list_entities(cid, "groups", v=v)

def groups_for(index: Index, actor: str) -> tuple[Affiliation, ...]
def members_of(index: Index, group: str) -> tuple[str, ...]
def affiliated(index: Index, group: str) -> tuple[str, ...]
    # members, plus the leader when not already one, leader first
def co_affiliates(index: Index, actor: str) -> dict[str, tuple[str, ...]]
    # other actor -> the groups the two share (by `affiliated`), each tuple sorted;
    # the actor itself excluded; empty when the actor is in no group
```

`digest` is the SHA-256 of the canonical JSON of
`[(ref, secrecy, leader, members) for each roster in ref order]`. It covers
only what membership consumers read, so a body edit to a group does not move
it. Member order is included because 08 may render members in stored order.

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
for `search.py`'s reason, `store/search.py:10-14`), keyed `("membership",
scope, id)`. The compute returns the stamps of:

- the world's `groups/` directory and each `groups/*.md` it read;
- for a campaign, the campaign's `groups/` directory and each of its
  `groups/*.md`, `deleted.json` (or its parent directory when absent), and
  `campaign.md` (which names the world).

Each stamp is taken before what it vouches for is read, per `memo_stamped`'s
contract (`statcache.py:141-170`). A group's record directory
(`groups/<id>/state.md`, assets) is not an input: an absorb's group-state write
must not recompute the index.

Cost without the memo is one `list_entities` of groups, which parses each group
file once and is itself memoized per file. That is already paid on every turn.

### 7.4 With 03: the persistent tier

Once 03 lands, the index is also a 03 artifact kind, `membership_index`,
version 1:

```
kind    = "membership_index"
params  = {"scope": "campaign" | "world"}
inputs  = [("world_groups",    collection_digest(<wroot>/groups, "*.md", safe_id stems)),
           ("campaign_groups", collection_digest(<croot>/groups, "*.md", safe_id stems)),  # campaign only
           ("tombstones",      content_hash(<croot>/deleted.json) or "absent")]           # campaign only
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
with and without it and requires equal output (section 17.2).

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
  else 400. An actor that does not exist is not an error: it answers no groups,
  because existence is not this index's question.
- 404 for an unknown world or campaign, through the existing
  `_world_root_or_404` / `_campaign_root_or_404`.
- A group file that cannot be parsed fails the read as `GET /{scope}/groups`
  fails today: the same reader raises. The graph and absorb wrap it fail-soft
  (sections 9 and 10).
- The response is a plain dict; no new pydantic model.

---

## 8. Integration overview (07-C3)

| Item | Where | Model call? | Changes a prompt? |
|---|---|---|---|
| C3a absorb proposals | `absorb/*`, `templates/absorb/*` | No new call: one more section in the existing extraction | Yes: absorb's system prompt always; its context line only when a present actor is a member |
| C3b graph | `continuity/graph.py`, `components/storyGraph/*` | No | No |
| C3c retrieval projections | `store/membership.py` | No | Not by itself; 08/09/11 decide |
| C3d structural presence | `context/activation.py`, `context/world_state.py` | No | Only where a group records members |

Every item is inert on a store that records no members: no node, no edge, no
presence, no snapshot text, no proposal the model can ground. That is what lets
`test_lore_golden.py` and the frozen campaign's snapshot stand unchanged.

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
> members is not joining it. Do not report a membership the "Groups:" line
> already shows. Membership is not group state: what the group wants or holds
> goes in group_state_edits.)

Each row carries the citation fields every staged section carries
(`quote`, `speaker`, `certainty`, `parse.CITATION_FIELDS`), through `_cite`.

The wording is a starting point. `evals/run.py --live` is the only check that a
model follows it; the offline suite proves only that the instruction is in the
prompt (section 17.4).

### 9.2 What the model is shown

`group_snapshot(cid)` becomes `group_snapshot(cid, cast=())`, called with
`facts["cast"]` at `routes/scenes.py:3089`. For each listed group, when at
least one present actor is in its `affiliated` set, the line gains one segment:

```
- groups/salt-circle (Salt Circle): Goals: ... | Members here: Mara (characters/mara)
```

- Only present members are named. They are the only actors the transcript can
  give evidence about, and naming them is enough for the model to avoid
  re-proposing a standing membership. The segment's length is bounded by the
  cast, not by the group's size, so a large group costs nothing extra.
- An actor is spelled `<kind>/<id>`, the spelling of the `Present:` line, so
  the model never has to translate between two notations.
- A group with no present member renders exactly as today. A store with no
  members therefore produces a byte-identical context block.
- gm-only groups stay out (`snapshots.py:97-104`), and so does their
  membership.
- A leader present but not a member is named as `Seraphine (characters/seraphine,
  leader)`: a leader's membership is unrecorded by design (section 4.4), and
  telling the model they lead keeps it from proposing they "join".

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
  (`continuity.canon.actor_ref`'s rule) and must exist through the overlay
  (`materializer._actor_exists`, `:96-111`); otherwise dropped.
- A `join` for an actor already in `members`, or a `leave` for one not in it,
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
 "field": "members",
 "before": "not a member", "after": "member",
 "authored": False,
 "payload": {"group": "salt-circle", "actor": "characters:mara", "change": "join"}}
```

routed with `routing.review(index, row, subjects=(actor,))`. The actor is the
subject: an actor saying "I have joined the Circle" is first-hand about
themself (the `SELF` tier, `absorb/routing.py`), and a third party's claim
about it is a claim, which is exactly how the existing tiers already read.

`before`/`after` are a rendered status, not the stored line. That is what makes
two rows on one group independent (section 9.4) and is why the kind is not
`MERGEABLE` (`conflicts.py:98-105`).

### 9.4 Applying

`apply._apply_one` gains a `membership` branch, under the campaign lock the
chronicle save already holds across the whole batch:

1. Read the group through the overlay; gone -> `failed`, "that group no longer
   exists in this campaign".
2. For a `join`, the actor must still exist (`_actor_exists`); gone ->
   `failed`, "that character no longer exists in this campaign". A `leave`
   does not check: removing a dangling ref is legitimate cleanup.
3. Re-read `members`, apply the **delta** (append the actor for a join, remove
   every occurrence for a leave), and write
   `overlay.update_entity(cid, "groups", gid, fields={"members": new_line})`.
   An empty `new_line` removes the key (`entities.update_entity`'s rule,
   `:254-258`).
4. Already in the desired state (step 3 would write the same line) ->
   `skipped`, nothing written.

**Why a delta and not the staged `after`.** Two approved rows on one group
("Mara joins", "Winifred leaves") were staged against the same `before`.
Writing either row's full line would erase the other's change. Applying deltas
makes the rows commute.

The write materializes the group if the campaign still inherits it (section
5.1). That is the same consequence an approved `lore_edits` append has, and the
review is where the user sees and approves it.

The apply branch never reads the review block (`routing.py`'s rule: "nothing
here is permission").

### 9.5 Conflicts, journal, undo

- **Conflict.** `conflicts._REASONS["membership"] = "this person's membership
  in this group changed since the scene was absorbed"`; `current_value` reads
  the actor's status ("member" / "not a member", or None if the group will not
  read); `target_key` is `("membership", gid, actor)`. A stored status that
  already equals `after` is **not** a conflict: the edit already holds, and
  step 4 skips it. That needs one small addition to `survey`
  (`conflicts.py:414-451`): a kind in a new `SETTLED_WHEN_AFTER =
  frozenset({"membership"})` returns no conflict when `stored == after`. For
  every other kind nothing changes.
- **Journal.** Recorded as kind `membership`, `ref {"kind": "groups", "id":
  gid}`, field `members`, label as staged. It is not in
  `changes.BROWSABLE_KINDS` (that log is for bodies, `changes.py:20-27`).
- **Undo.** A new descriptor in `undo.probe`:
  `{"w": "membership", "kind": "groups", "id": gid, "actor": ref}`.
  `read_value` returns `True`/`False` (is a member now); `restore` re-applies
  the inverse delta through `overlay.update_entity`. The compare-and-swap
  `expect` is that one actor's status, so undoing "Mara joins" is refused only
  if Mara's own membership moved since, not because Winifred's did. The
  generic `entity_fields` descriptor (`undo.py:298-304`, `:355-358`) would
  restore the whole line and refuse whenever any member changed; it stays for
  the editor's adopt path, which writes whole keys. A restore never
  de-materializes the group (the module's stated limit, `undo.py:53-60`).

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
whole-kind entity listing through the overlay. It runs in its own `_attempt`
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
def prompt_visible(roster: Roster) -> bool:
    return roster.secrecy != entities.GM_ONLY
```

"Never sent to the model" means every model or it means nothing
(`snapshots.py:97-104`), and an embedding provider is a model. So 08's
SearchDocument text, 09's history section and any decide item drop gm-only
rosters before rendering or embedding a name.

A `secret` roster stays visible to the narrator perspective. For an actor
perspective (11's `perspective = actor:<ref>`), a secret group's membership is
narrator-only until 11 decides otherwise. 07 marks the row; 11 owns the rule.

Every consumer intersects member refs with its own roster before rendering a
name: a dangling ref is a missing person, not a member to describe.

### 12.2 The per-scene projection (for 08)

```python
@dataclass(frozen=True)
class SceneGroup:
    group: str                      # "groups:<id>"
    name: str
    present: tuple[str, ...]        # affiliated actors in the cast, sorted
    leader_present: bool

SCENE_GROUP_LIMIT = 8

def scene_groups(index: Index, cast: Iterable[str], *,
                 visible: Callable[[Roster], bool] = prompt_visible,
                 limit: int = SCENE_GROUP_LIMIT) -> tuple[SceneGroup, ...]
```

The groups at least one of whose affiliated actors is in `cast` (actor refs in
`<kind>:<id>` form; a `characters/<id>` token is normalized), ordered by the
number of present affiliates descending, then name, then ref, and cut at
`limit`. Deterministic, and bounded by the cast rather than the library.

`SCENE_GROUP_LIMIT` is structural, not measured: it caps one metadata line of a
document that must stay bounded by design (08's draft, §7), at a size where a
scene with a large cast still lists the groups that most of it shares. It is
tuned against real prompts later, in conversation, with nothing committed.

**For 08's key.** A SearchDocument that renders "Groups relevant" must change
when that scene's projection changes. 08 hashes the canonical JSON of
`scene_groups(...)` as a non-file input (03-C1 `params`/`inputs`). Keying on
`Index.digest` instead would also be correct, but it would re-render every scene
document when any group anywhere changes. The per-scene projection moves only
the scenes it affects.

### 12.3 Co-affiliation and membership lookups (for 09)

09's structural candidate generation may use, through `co_affiliates`,
`groups_for` and `members_of`:

- other actors sharing a group with the speaker or addressee;
- scenes in which several affiliates of one group appear (09 computes this from
  08's per-scene metadata; 07 provides the projection, not the scene search);
- a group's `headquarters` (on `Roster`), as a location signal.

**These are recall signals, not evidence.** Co-membership says two people are
connected; it does not say either knows what the other saw. 09 weights them;
07 asserts nothing about relevance.

### 12.4 Rules for 11 (epistemic retrieval)

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
  clear.
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

## 14. Slicing (for the plan)

Each slice ships alone and leaves the tree green.

1. **A. Field, reader, inverse, routes, editor, actor sections** (C1 minus
   sync, C2 in-process tier). The smallest slice with user value: record
   members, see them on both sides.
2. **B. Sync visibility and push/promote check** (rest of C1).
3. **C. Structural presence** (C3d). Small, prompt-affecting, isolated.
4. **D. Absorb** (C3a). The largest; touches templates, evals and the review
   UI.
5. **E. Graph** (C3b).
6. **F. Projections** (C3c), landing before 08's plan starts.
7. **G. Persistent tier** (C2 with 03), once 03-C1 has landed.

---

## 15. Contract

### 07-C1: Authoritative members and a leader on the group record

**Provides.**
- `entity_schema.FIELDS["groups"]` declares `members` (section 4.1), mirrored in
  `types.ts`.
- Stored as one comma-separated frontmatter line of `characters:<id>` /
  `pcs:<id>` refs on `<root>/groups/<id>.md`.
- `membership.parse_members(meta)` and `parse_leader(meta)`: lenient, never
  raise (section 4.3).

**Guarantees.**
- The group is the only place a membership is stored. No actor record, sidecar
  or campaign ledger holds a copy.
- Save-boundary validation is format-only (kind and id), never existence.
- `leader` and `members` are independent, and nothing in this spec writes
  `leader`.
- Campaign effective membership is the `members` line of the file the overlay
  resolves, with no merge across layers. A campaign change materializes the
  group (whole-record copy-on-write) and diverges it from the world.
- Sync shows declared entity fields, `members` included, on both sides of a
  pending change (section 5.3).
- `promote` and `push` of a group refuse a member ref that names no library
  actor or names this campaign's own detached actor (section 5.5).

**Failure behaviour.** A malformed stored value contributes no members and
never fails a read. A malformed submitted value is a 400 naming `members`.

### 07-C2: Derived inverse membership, cacheable under 03

**Provides.** `membership.Index` with `groups`, `by_actor` and `digest`;
`world_index(wroot)`, `campaign_index(cid, v=None)`, `index_from_rows(rows)`;
`groups_for`, `members_of`, `affiliated`, `co_affiliates`; the two
`GET .../membership` routes (sections 7.1-7.5).

**Inputs.** The world's `groups/*.md` (stems passing `safe_id`); for a
campaign, also the campaign's `groups/*.md`, `deleted.json` and the world the
campaign names.

**Guarantees.**
- Deterministic: the same files give the same index and the same `digest`, in
  any process, with or without 03.
- `digest` moves if and only if some roster's `(ref, secrecy, leader,
  members)` moves. A body, key or group-state edit does not move it.
- Refs are reported as stored, existence unchecked; gm-only rosters are
  included and marked.
- Never stale: the in-process tier re-stats its inputs on every call, and the
  persistent tier (03) is keyed on content digests, so an edit by any writer
  (the app, an editor, a sync client) is seen on the next read.
- The persistent tier changes cost, never output.

**Failure behaviour.** An unreadable group file raises from the listing, as
`GET /{scope}/groups` does; callers wrap it per their own fail-soft policy.
03 being off or absent costs the persistent tier only.

### 07-C3a: Absorb membership proposals

**Provides.** The `membership_changes` section (prompt, parse, materialize,
apply, conflict, journal, undo, review drawer), section 9.

**Guarantees.**
- No new model call: the section rides the existing extraction.
- Nothing is written without an approved row in a saved review.
- Only known groups and known actors are staged; joins of members and leaves
  of non-members are never staged.
- Apply is a per-(group, actor) delta, so approved rows on one group commute.
- The absorb context names only present affiliates, never gm-only groups.

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
- `scene_groups` is deterministic and bounded by `limit`.
- A consumer that follows `prompt_visible` sends no gm-only group's name or
  membership to any model.
- Membership is never offered as knowledge, and never as historical fact.

**Consumers' obligations.** 08 keys a SearchDocument that renders groups on
the scene's `scene_groups` output. 09 treats co-affiliation as a recall signal.
11 classifies knowledge without reading membership as evidence of it.

### 07-C3d: Structural presence through members

**Provides.** The `("members", "member_present", True)` row and the
`"member_present"` reason (section 11).

**Guarantees.** A store that records no members composes byte-identical
prompts (`test_lore_golden.py` stands). Presence opens owner gates only; it
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
  through `overlay.list_entities`, never off a raw campaign root. The 03
  persistent tier's digests are computed by 03's own reader, which owns its
  marker if one is needed.
- **Paths guard.** All paths come from `entities`, `overlay` and
  `campaigns_paths`; nothing joins a home-relative path by hand.
- **Lock domain (`test_lock_domain_guard.py`).** `store/membership.py` mutates
  nothing and needs no classification. Writes happen in `absorb.apply` (inside
  the chronicle save's campaign lock), in `undo` (journalled, as today), and in
  the entity routes, which stay as they are: `store.overlay` is in `UNREVIEWED`
  (`locks.py:548`) and campaign entity saves are guarded by the `rev`
  precondition (`routes/entities.py:203-204`), not a lock. This spec does not
  widen that backlog.
- **Import guard (`test_import_guard.py`).** `membership.py` imports
  `entities`, `entity_schema`, `overlay` and `statcache` at module scope;
  `continuity/graph.py` and `absorb/*` bind `from .. import membership`. No
  cycle: none of those import `membership`.
- **pydantic v1/v2 and Android.** No new dependency, no new request model; the
  routes return plain dicts. Nothing here assumes a desktop path.
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
- **Frontend list/detail pattern.** Groups use `EntityEditor` unchanged;
  actor pages gain a read-only `ColumnSection`. No page builds a second rail.
- **Keyboard registry.** No new binding.
- **Frozen campaign.** Not edited; its snapshot does not move (section 13.2).

---

## 17. Tests and acceptance

### 17.1 Field and validation (C1)

- `test_entity_schema.py`: `members` is a `multi` ref over `("characters",
  "pcs")`, after `leader`; a list of actor refs passes; `groups:`,
  `locations:` and a bare name are refused; a blank clears; duplicates pass
  the boundary.
- New `test_the_frontend_ships_the_same_field_table` (in
  `test_entities_store.py`): parses `ENTITY_FIELDS` from `types.ts` and holds
  every kind's keys, order, widgets, `kinds`, `multi`, `min`/`max`, `options`
  and `source` to `entity_schema.FIELDS`.
- `test_membership.py::test_parse_members_is_lenient`: non-strings, legacy
  names, non-actor refs, unreferenceable ids and duplicates; never raises.
- A legacy `members: Mara, Seraphine` survives an unrelated body save through
  the route byte for byte, and a route save that sets it to a legacy value is
  a 400.
- Reclassify a group with members to lore and back: the line is untouched and
  the index answers nothing, then the same rosters again.

### 17.2 Inverse (C2)

- `index_from_rows` on Salt Circle (leader Seraphine, members Mara, Winifred,
  Seraphine) gives the `by_actor` roles of section 7.5.
- Campaign overlay: a world group inherited, then a campaign copy with a
  different `members`, then a tombstone; `campaign_index` follows each step.
- `digest` moves on a members edit and not on a body edit or a
  `groups/<id>/state.md` write.
- The in-process memo recomputes after an external write to a group file
  (written with `os.utime` moved, no app call).
- When 03 lands: every consumer's output is equal with the persistent tier on
  and off (`test_membership_cache_equivalence`).
- Routes: `GET .../membership` and `?actor=`; 400 on `actor=groups:x` and on a
  malformed ref; 404 for an unknown campaign; route order holds
  (`test_route_order.py`).

### 17.3 Sync (C1)

- An incoming world change that touches only `members` carries a `fields` row
  whose two sides differ, and `IncomingReview` renders it (no
  "invisible change" hint).
- `promote` and `push` of a group naming a campaign-made Mara raise
  `DanglingReferenceError`; with Mara promoted first, they succeed.

### 17.4 Absorb (C3a)

- `parse_output("{}")` gains `membership_changes`;
  `test_identity_fields_live_inside_rows_not_the_top_level`'s key set is
  updated in the same change.
- Every absorb recording in `evals/recordings/` (`absorb.compliant`,
  `absorb.truncated`, `absorb.no-summary`, `absorb.laundered`) gains
  `"membership_changes": []`. Without it each counterexample trips an extra
  check and fails its declared set equality (`evals/README.md:383-412`). A new
  counterexample, `absorb.null-membership`, holds `[null]` and must trip
  exactly `absorb.membership_changes`.
- The `campaign_flow` cassette's absorb reply is unchanged (an absent section
  is `[]`), and `test_llm_fakes.py` still matches the re-rendered prompt.
- Materialize: a join for Mara stages one row with subject Mara; a join for a
  member, a leave for a non-member, an unknown group, an unknown actor and a
  bad `change` word stage nothing; a reply that says join and leave for one
  pair stages the first.
- `group_snapshot(cid, cast)`: unchanged when no present actor is affiliated;
  names present affiliates only; a leader-only line says "leader"; gm-only
  groups absent.
- Apply: two approved rows on one group both land (deltas commute); a row
  whose target already holds is `skipped`; a join for a deleted actor is
  `failed`; a leave of a dangling ref lands.
- Conflict: an outside add of Mara before save makes her join row settled, not
  conflicted; an outside removal of Winifred makes her leave row settled; an
  outside edit to another member conflicts nothing.
- Undo: undoing "Mara joins" after "Winifred leaves" landed succeeds; undoing
  it after Mara was removed by hand is refused with `CONFLICT`.
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
- `knows` is unchanged for every combination above
  (`test_context_actor.py`-style table).
- Frontend: `loreReasons.test.ts` row for `member_present`.

### 17.7 Projections (C3c)

- `scene_groups` ordering, `limit`, `characters/<id>` normalization, and the
  `prompt_visible` default dropping gm-only rosters.
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
11. **The checklist split of 07-C3 into C3a-C3d**, and the edges 08 <- 07-C3c,
    09 <- 07-C3c, 11 <- 07-C3c in addition to the listed 07-C2 / 07-C1 edges.
    **Recommend: accept, and amend `ROADMAP-CHECKLIST.md` in the PR that
    accepts this spec.**
