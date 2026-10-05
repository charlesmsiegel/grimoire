# Lore activation controls, provenance, and suggestions from play

**Date:** 2026-10-05
**Status:** Design approved in conversation; awaiting Codex adversarial review before planning.
**Touches:** #20 (lorebook import stash), #128 (why each chunk is in the prompt — world-info half), #220 (items, groups and creatures as lore owners), #124 (promote toward the library — the absorb door into it).

---

## 1. Why

SillyTavern world info decides which entries enter the prompt with primary and
secondary keys under a logic operator, a per-entry scan depth, recursion,
insertion order and priority, probability, and timed effects (sticky, cooldown,
delay). Grimoire imports lorebooks, and until now how much of that survived was
unclear.

The answer, from reading the code:

- `store/lorebook.py` keeps an entry's name, primary keys, body and `constant`
  (as keyless). Everything else it recognises is stashed verbatim in one JSON
  frontmatter key, `st_extensions` (#20) — **and nothing reads it.** The comment
  says so: "the context builder does not honor them yet".
- Activation (`store/context/world_state.activate`) is: gm-only dropped →
  reader excludes → reader pins → owner gate → keyless always-on, else any
  primary key whole-word, case-insensitive, in the last `context_scan_depth`
  posts (default 8). No secondary keys, no recursion, no timed effects, no
  priority. Semantic recall (`semantic.recall`) then gets what the keyword rule
  rejected.
- The packer (`store/context/pack.py`) drops **the whole World info section**
  under pressure. One unimportant entry can cost every entry.
- `owners` gates by presence for every kind in the backend, but presence is only
  the cast plus the current location, so lore owned by an item, group or
  creature can never activate, and the Owners picker does not offer them (#220).
- On per-character calls, `actor.known_entries` passes an entry if the actor
  owns it, or it is unowned and public. Lore owned by a *location* is therefore
  dropped from every NPC call, including the NPC standing in that location.
  `owners` is doing two jobs ("activates when X is here" and "only X knows
  it") and cannot express a secret about one character known to another.
- The inspector reports sections, never entries. Nothing says which key hit, in
  which post, or why an owned entry was unlocked (#128).
- Absorb already proposes new lore (`new_lore`, `new_locations`) with a cited
  quote. But the quote is discarded on apply, keys cannot be edited in review,
  a group, item or creature can only be proposed as `lore`, and an accepted
  entry always lands in the campaign. Reaching the world needs a separate trip
  to the entity editor's *Publish to library*.

**The outcome this design commits to:** in a long campaign, lore enters the
prompt for reasons the author controls, those reasons are visible per entry,
the budget sheds the least important entries first instead of all of them, and
the lore that play discovers is captured with its evidence and can be sent
straight to the world.

### What changes for an existing store, and what does not

With none of the new fields set and recursion left at its default (0), **every
narrator prompt is byte-identical to today's**, with one exception: budgeted
prompts that today lose World info whole now keep part of it (§6). Two
intentional changes reach NPC (per-character) calls. Public lore owned by a
location, item, group or creature now reaches the NPCs present (§8.2). Lore
owned by items, groups or creatures can activate at all, but the UI could not
author such owners before, so in practice only hand-edited stores see it. If
`backend/tests/fixtures/frozen_campaign/snapshot.json` moves, it is regenerated
deliberately and the diff reviewed, per that directory's README.

### Non-goals

- **Probability** (`probability`/`useProbability`): a dice roll per entry per
  turn makes the prompt, and therefore the inspector, unreproducible. Kept in
  the stash; not honoured.
- **Insertion position, depth and role** (`position`, `depth`, `role`,
  insertion `order` as *placement*): grimoire composes sections from templates,
  and World info has one place in it. `order` is honoured only as a budget
  priority (§6).
- **Matching options**: case-sensitive, whole-word off, and regex keys
  (`caseSensitive`, `matchWholeWords`, `use_regex`). Matching stays one rule
  (`keyword_hit`), shared by archive recall and mechanics rules.
- **`delay`, `delayUntilRecursion`, inclusion groups** (`group`,
  `groupOverride`, `groupWeight`, `useGroupScoring`), `characterFilter`,
  `triggers`, `vectorized`, `automationId`. Stashed where present; not honoured.
- **An on-demand "suggest lore" button.** Absorb stays the one source of
  suggestions (§9).
- **Cast reasons** (why a *character* is in the prompt) — the other half of
  #128. Different data (cast tiers), a separate change.
- **Group membership.** Groups record `leader` and `headquarters`, not members,
  so "everyone in the Guild knows this" is out until membership exists.

---

## 2. Shape

One new pure module, `store/context/activation.py`. Its input is the candidate
entries, the scene's posts as `(transcript_index, text)` pairs, the
turn's seed text (opener prompt / director note), the base present set, the
reader's pins and excludes, and the settings. Its output is an `Activation` result:

```
Activation(
    activated: list[Hit],      # in today's prompt order (kind, then id)
    held_back: list[Held],     # e.g. on cooldown -- inspector only
    present: dict[ref, PresenceReason],
)
Hit = entry + reason
```

It reads nothing from disk and holds no state between calls. Everything
time-dependent is derived from the posts it is handed (§5.3), so a cut, a
retcon, an edit or a reroll changes its answer exactly as it changes the
transcript, with no second store to keep consistent.

`world_state.activate` stays the public seam and keeps its documented order:
gm-only → excludes → pins → owner gate → keys. It becomes a thin wrapper that
calls the engine and unwraps `Hit`s to entries for callers that only want the
entries. `_world_info` keeps its `(keyword, recalled)` return for
`assemble._assemble`, and gains a third value: the `Activation` record, for the
inspector and the packer. **Reasons never reach the prompt.** Templates receive
entry bodies exactly as today. A test renders a prompt with every reason type
present and asserts none of their text appears (the #131 invariant).

---

## 3. Data

All new fields are **optional frontmatter scalars** on every world-info kind
(`lore`, `locations`, `items`, `groups`, `creatures`), at world or campaign
scope, flowing through `overlay` like every other field. Frontmatter is flat
single-line strings (`store/frontmatter.py`), so lists are comma-joined and
numbers are decimal strings. **Absent means today's behaviour.** A value that
does not parse is treated as absent at activation time, never raised. The editor
validates on save and refuses it (400 with the field named).

| Field | Values | Meaning |
|---|---|---|
| `secondary_keys` | comma list | Second key list, combined with the primary match by `key_logic`. Ignored on a keyless entry. |
| `key_logic` | `and_any` (default) · `and_all` · `not_any` · `not_all` | With primary matched: `and_any` = any secondary matches; `and_all` = every secondary matches; `not_any` = no secondary matches; `not_all` = not every secondary matches. Empty `secondary_keys` = primary alone, as today. |
| `scan_depth` | int 0–100 | Posts this entry scans, overriding `context_scan_depth`. 0 = only the turn's seed text. |
| `sticky` | int 0–50 | After a direct activation, stays active for this many further posts without needing its keys. |
| `cooldown` | int 0–50 | After it stops being active (sticky ended, or immediately when not sticky), cannot activate for this many posts. |
| `priority` | int 0–1000, default 100 | Budget shedding order: lower sheds first (§6). Never affects prompt order. |
| `keep` | `true` | Never shed for budget, like a pin but authored on the record. |
| `recursion` | `both` (default) · `pulled_only` · `pulls_only` · `none` | `pulled_only`: other entries' bodies can activate it, its own body activates nothing. `pulls_only`: it activates only directly, its body can pull others in. `none`: neither. |
| `known_by` | comma list of `characters:<id>` / `pcs:<id>` | Who knows it on per-character calls (§8). |

Bounds are deliberate. `sticky` and `cooldown` stay small so a carried entry cannot outlive a scene by accident.
`scan_depth ≤ 100` bounds the window. A value outside its bound is a validation
error, not clamped. Numbers are justified structurally, not measured, and can
be tuned later.

`known_by` names only actors (`characters:` / `pcs:`). Reclassification
(`store/reclassify.py`) moves only the five generic kinds and never an actor,
so no reclassify can change a ref `known_by` accepts, and the field needs no
rewrite sweep. A `known_by` ref to an actor that has since been deleted is
inert: it names nobody who can be on a call, the same way a dangling `owners`
ref names nobody who can be present. The editor shows it as a missing ref
rather than dropping it.

The Owners picker (`frontend/src/api/loreOwners.ts`) widens to items, groups and
creatures (#220). The kinds were deferred only because nothing could make them
present, which §7 supplies.

### Editor

`EntityEditor`'s form gains a collapsed **Activation** disclosure holding the
fields above. The read-only detail
sidebar gets one `.side-section` per set field, as `chip on` spans, with
`known_by` refs as clickable chips that navigate (the list/detail rule in
CLAUDE.md). This adds no new page and no new pattern.

---

## 4. Import audit and adopt (#20 completed)

### 4.1 The audit

The spec of record is this table. `test_lorebook.py` pins it with hand-authored
fixtures covering **both spellings** (V3 `character_book` and standalone ST
world-info) and **both locations**: top level, and inside the V3 entry's own
`extensions` object, which is where ST writes most advanced fields when it
exports a card.

| ST field (spellings) | Today | After |
|---|---|---|
| `content` | kept (body) | kept |
| `keys` / `key` | kept (`keys`) | kept |
| `comment` / `name` | kept (name) | kept |
| `enabled` / `disable` | honoured (skipped) | unchanged |
| `constant` | mapped → keyless; raw keys in stash | unchanged |
| `secondary_keys` / `keysecondary` | stashed | **mapped** → `secondary_keys` |
| `selective` + `selectiveLogic` (0 AND_ANY, 1 NOT_ALL, 2 NOT_ANY, 3 AND_ALL) | stashed | **mapped** → `key_logic`; `selective: false` → secondary ignored (no `secondary_keys` written) |
| `scanDepth` / `scan_depth` | stashed | **mapped** → `scan_depth` (null = absent) |
| `sticky` | stashed only when nested in `extensions`; **lost** at top level | **stashed both places, mapped** → `sticky` |
| `cooldown` | as `sticky` | **mapped** → `cooldown` |
| `priority` (V3), `order` (ST), `insertion_order` (V3) | stashed | **mapped** → `priority`, first present in that order |
| `ignoreBudget` / `ignore_budget` | **lost** | stashed, **mapped** → `keep: true` |
| `excludeRecursion`/`exclude_recursion`, `preventRecursion`/`prevent_recursion` | stashed | **mapped** → `recursion` (exclude → not pulled; prevent → doesn't pull) |
| `delay`, `delayUntilRecursion`/`delay_until_recursion` | `delay` lost at top level | stashed; not honoured |
| `probability`, `useProbability` | stashed | stashed; not honoured |
| `position`, `depth`, `role` | stashed | stashed; not honoured |
| `caseSensitive`/`case_sensitive`, `matchWholeWords`/`match_whole_words`, `use_regex`/`useRegex` | stashed | stashed; not honoured |
| `group`, `groupOverride`, `groupWeight`, `useGroupScoring` | **lost** | stashed; not honoured |
| `vectorized`, `characterFilter`, `triggers`, `automationId` | **lost** | stashed; not honoured |
| `uid`, `displayIndex`, `addMemo`, `id` | lost | lost (bookkeeping) |

The stash gains the "lost" fields above, so a future change can honour them
without a re-import. A simple import still writes no `st_extensions`.

### 4.2 Adopt

`lorebook.adopt(stash: dict) -> AdoptResult(fields: dict[str, str],
unmapped: list[str])` is the **single** mapping from a stash to native fields.
The importer calls it for new imports, writing native fields *and* keeping the
stash. The two adopt routes call it for old ones:

- **Per entry.** When a record has `st_extensions` and `adopt` would set at
  least one field the record does not already have, the editor shows *Imported
  SillyTavern settings available* with the fields it would set and an **Apply**
  button.
  `POST /worlds/{wid}/{kind}/{eid}/adopt-st` and
  `POST /campaigns/{cid}/{kind}/{eid}/adopt-st`.
- **In bulk.** *Apply imported settings to all* on the world's lore section
  and on the campaign's. It covers only records that root holds itself, not
  records a campaign inherits from its world, which remain the world's to change.
  It spans every world-info kind, since an import can file entries under any.
  `POST /worlds/{wid}/adopt-st` and `POST /campaigns/{cid}/adopt-st` return
  `{applied: [...], skipped: [...]}`.

**Adopt never overwrites a field the record already has**, because an author's
edit beats a stale import. Campaign-side writes go through `overlay.update_entity` under
`locks.campaign_lock(cid)` inside `store.undo.journalled`, so they can be
reversed from Changes. World-side writes take the same locking an ordinary
world entity edit takes. Nothing applies automatically: until someone presses
Apply, an old import behaves exactly as it does today.

---

## 5. Activation

### 5.1 Windows

Each post is `(transcript_index, text)` from the same message list the scan
window reads today. An entry's window is its last `scan_depth` posts (its own,
else `context_scan_depth`), plus the turn's seed text, which today's code always
appends. Window texts are memoised per distinct depth. Key patterns are compiled
once per key per call, since a long lorebook and the replay of §5.3 call
`keyword_hit` many more times than today.

A direct match records the **matched primary key** (and secondary key when the
logic required one) and the **newest post** whose text matched it, or `seed`.
That is the inspector's "key 'Saltmarch' in post #41".

### 5.2 One turn, in levels

```
P  = base present set                                  (§7.1)
A  = {}                                                 activated
level 0:  for each candidate (gm-only, excluded already removed; pins added
          with reason=pinned):
            owner gate against P → key rule against its window
            → timed-effect state (§5.3) → activate or hold back
P += activated items/groups/creatures                   (§7.2)
level n ≥ 1 (while something new activated, n ≤ R + 1):
          candidates not yet in A, owner gate against the grown P:
            - key rule against its window   (an entry whose owner just arrived)
            - if n ≤ R and recursion allows: key rule against the bodies of
              entries activated at level n-1 whose recursion allows pulling
          P += newly activated items/groups/creatures
recall:   semantic.recall(entries never activated and passing the owner gate
          against final P, window text) → appended, reason=recall
```

- `R` is a new global setting `lore_recursion_depth` (config.md, Settings beside
  scan depth), **default 0**, max 3. At 0 no body is scanned. The extra level
  that lets a newly present item, group or creature unlock its owned lore still
  runs, so #220 works without recursion. That level is cheap: only owner-gated
  candidates whose gate was closed at level 0 are re-checked.
- **Pinned** entries are activated at level 0 with reason `pinned`. They take
  part in presence and pulling like any other activated entry.
- **Recall** hits are terminal: they add no presence, pull nothing, and are never
  sticky. This keeps the layer additive, the promise `pack.RECALLED` exists to
  keep.
- **Order** of `activated` is today's order (kind, then id), whatever level an
  entry came from, so prompt bytes do not depend on discovery order.

### 5.3 Sticky and cooldown, derived

Timed effects are a property of the transcript, not of a stored timer. For each
candidate with `sticky` or `cooldown` set (and only those), the engine replays
its **direct** key rule at each post boundary **from the start of the scene**,
and runs a three-state machine. A shorter replay window is not exact: an
activation just before the window's start can still be sticky or cooling inside
it, and a replay that started "ready" there would re-trigger where the real
history would not. The states:

- **ready** → **active** on a direct match.
- **active** → stays active for `sticky` posts after the triggering post, with no
  key needed.
- **active** → **cooling** for `cooldown` posts once it stops being active.
  While cooling, a match is ignored.

The state at the current boundary decides the entry:

- **active** with a fresh match: reason `key`.
- **active** carried by sticky: reason `sticky`, with posts remaining and the
  post that started it.
- **cooling**: the entry goes to `held_back` with posts remaining. The key rule
  may say yes now; the cooldown says no.

Consequences, all intended:

- **Scenes reset it.** Replay sees only this scene's posts.
- **Only a direct match starts sticky.** Recursion, presence unlock, pins and
  recall do not. This keeps the machine a function of the transcript alone and
  not of the other entries' replays.
- **The owner gate is checked at the current turn, not replayed.** A sticky
  entry whose owner left is out, because an absent owner's lore must not leak,
  and that rule is older than this one.
- **Per-character calls replay over that actor's observed history**
  (`actor.observed_history`), the same history its scan window already uses.
- **The unit is a message of the list the scan window already reads**, which
  is also what a pin's post-count TTL counts (`pins.active(cid, sid,
  len(history))`). That list includes stored director notes today, and they
  match keys today, so they keep doing both. A note is how the player steers,
  and lore it names arriving, and sticking, is the point. Excluding them would
  change today's window and break §1's byte-identity promise.

### 5.4 Cost

Matching runs **once per key per post**, giving a per-key bitmap. An entry's
window match at any boundary is then "any set bit among the last `d` posts".
This is exact, not an approximation, because a key is escaped and
single-line: no key can match across the newline that joins two posts, so
matching each post separately gives the same answer as matching their joined
text, which is what today's code does. The cost of a turn is therefore one
regex search per (key, post) plus bit lookups, whatever the timed-effect
settings, and recursion adds at most `R ≤ 3` levels of searches over activated
bodies. The plan includes a test on a synthetic large lorebook that asserts a
call-count bound, not wall time.

---

## 6. Priority and per-entry shedding

Prompt order is unchanged (§5.2). Priority only decides what goes when World
info has to give way.

The `world_info` section is rendered from a list of entries rather than a fixed
string, and carries a re-render function. Today the packer drops spotlight
sections largest-first. When its turn reaches `world_info`, **instead of
dropping it whole the packer sheds entries one at a time**, re-measuring the
composed system message after each, until the prompt fits. Shed order:

1. lowest `priority` first;
2. on a tie, an entry that activated only by recursion or presence-unlock before
   one that matched directly;
3. then by **match age**, oldest first. Every entry has a total, comparable
   age, defined as a post index where higher means newer:
   - a transcript match: the index of the newest matching post;
   - sticky carry: the index of the post that started it;
   - a seed-only match (opener prompt, director note): one past the last post,
     i.e. newest of all, because the seed is this turn's own input;
   - no match of its own (keyless always-on, recursion pull, presence unlock):
     **-1**, i.e. oldest of all. It is standing context rather than something
     the conversation just raised. A recursion or presence hit has already
     sorted ahead at step 2, so this only orders entries within the same group.
4. then reverse prompt order, so a store always packs the same way.

Never shed: `keep: true` entries and pinned entries. If only those remain and the
prompt still does not fit, `world_info` is treated like a pinned section and the
packer moves on, as today. If every entry is shed, the section is `dropped` as
today. Public and secret blocks re-render from the surviving entries, so a
secret still lives or dies with its own entry, not with the section.
`recalled_lore` keeps whole-section dropping in its own tier. Unbounded budgets
(`context_budget: 0`) skip all of this, as today.

The packer change is local. `pack.pack` learns one optional section key,
`shed: {"entries": [...], "order": [...], "render": fn}`. Any section without
it behaves exactly as before, which a test asserts for a section list with no
`shed` key.

---

## 7. Presence (#220)

### 7.1 Base presence, structural

Today `present` = cast refs + `locations:<current>`. It grows by what the scene's
seated actors and current location *structurally imply*, from the ref fields
`entity_schema.FIELDS` already declares:

| Becomes present | Because |
|---|---|
| an item | its `holder` is present (a character, PC or group present, or the current location) |
| a group | its `leader` is present, or its `headquarters` is the current location |
| a creature | one of its `habitat` locations is the current location |

The rules chain: Mara present → the group she leads present → the item that
group holds present. So structural presence is computed **to a fixed point**,
not in one pass. Repeat the three rules over the world-info candidates
(which `_world_info` already reads) until a pass adds nothing. Each pass adds
at least one ref or stops, so this terminates within the number of candidate
items, groups and creatures. Iteration order therefore cannot change the
result, which a test holds by shuffling the candidates. Presence does **not** activate the item's own entry. "Mara holds the
lantern" makes the lantern present for unlocking lore it *owns*. The lantern's
own entry still needs its keys, a pin, or always-on, exactly as today.

### 7.2 Presence by activation

Any item, group or creature entry that activates at a level (§5.2) is present
from the next level on. "Someone mentioned Saltmarch's harbour guild" makes the
guild present, so the guild's owned lore can unlock.

### 7.3 Reasons

`present` maps each ref to why: `cast`, `current location`, `held by <ref>`,
`led by <ref>`, `headquartered here`, `habitat`, or `activated`. An owner-gated
hit's reason names the owner that satisfied its gate and that owner's presence
reason ("owner present: the lantern, held by Mara").

---

## 8. Per-character calls

### 8.1 `known_by`

`actor.known_entries` is replaced by one rule, applied to everything a
per-character call activates (keyword, recalled, the current setting):

1. `gm-only` → never.
2. `known_by` set → only if the actor's ref is in it. Secrecy does not matter:
   a secret known by Winifred reaches Winifred's call.
3. `known_by` unset → today's rule plus the fix below.

**The narrator always receives** a `known_by` entry that activated: the
narrator knows the world. `known_by` does not gate activation; the owner gate
still does.

**On a per-character call the owner gate is checked against the scene's full
present set**, not the narrowed cast. Today `_assemble` replaces `cast` with
the one selected NPC before `present` is built, so on an NPC call only that NPC
and the location are present, and lore owned by anyone else in the room fails
the gate. That was harmless while the knowledge filter dropped such lore anyway.
With `known_by` it is wrong: Winifred's call must be able to activate an entry
gated on Mara being present. So `present` (and §7's structural presence) is
computed from the unnarrowed, exclude-filtered cast. What the call may *see* is
then the knowledge rule's job. For every entry without `known_by` this rule
returns what the narrowed gate plus `known_entries` returned before. The one
exception is §8.2, which is intended. So "a secret about Mara that only Winifred knows" is
`owners: characters:mara`, `known_by: characters:winifred`, `secrecy: secret`.
It activates when Mara is present, the narrator gets it as a secret, Winifred's
own call gets it, and Mara's own call does not.

### 8.2 The location/object fix

With `known_by` unset, an entry reaches an actor's call if the actor is an
owner, **or** it is public and **every** owner is a non-actor ref (location,
item, group, creature), **or** it is public and has no owners. An entry owned by
a character still reaches only that character's calls. Public lore owned by the
place the scene is standing in reaches the NPCs standing there.

---

## 9. Suggestions from play: hardening absorb

Absorb stays the one source of suggestions.

### 9.1 Contract

`new_lore` gains an optional `kind`: `lore` (default) · `items` · `groups` ·
`creatures`. `parse.py` clamps anything else to `lore`. The materializer's
existing name/slug de-duplication runs per kind. `templates/absorb/system.j2`
line 19 is reworded to ask for the kind. Following CLAUDE.md's template rule:
`scripts/verify_templates.py`, the eval suite's absorb-contract case, and the
`campaign_flow` cassette matchers (`test_llm_fakes.py`) are updated in the same
change.

### 9.2 Review row

On `new_lore` and `new_locations` rows (`AbsorbEditRow.tsx`):

- **Keys** become an editable input, beside the name.
- **Kind** is a select on `new_lore` rows.
- **Destination**: *Campaign* (default) or *World library*.

The edited values travel in the staged edit's `payload` the way the name already
does.

### 9.3 Provenance

**Reuse `store/provenance.py`; add no new frontmatter key.** The repo already
keeps absorb citations. `provenance.json` is a campaign-local rolling map keyed
`"<kind>/<id>#<field>"` holding the quote, speaker and certainty of the edit
that set each field. `apply_edits` records a row for every applied edit.
`GET /campaigns/{cid}/provenance` labels each row with its scene's current
title at read time, and `RecordDrawer` already renders it. `repoint_scenes`
and `forget_scene` keep it correct across a scene rename or delete.

New records are absent from it today for one reason. Their staged edit carries
`target: {kind, id: ""}`, because the id does not exist until apply. So
`provenance.key(e)` returns None and the citation is dropped.

The fix: when `_apply_one` creates a record (`new_lore` of any kind, and
`new_location`), its outcome carries the created `{kind, id}`. The provenance
step keys the citation `"<kind>/<new id>#body"` from that outcome rather than
from the staged target. Nothing else changes, and the existing panel shows the
citation wherever that campaign shows the record.

This also settles scoping. Provenance is campaign data, so a record published
to the world shows its citation in the campaign it came from and nowhere else.
The world editor and other campaigns that inherit the record show none, rather
than a link that might resolve to an unrelated scene. Provenance is never read
by the context builder. A test asserts the quote is absent from a prompt that
activates the record.

### 9.4 Destination: world

A row marked *World library* is created campaign-local exactly as today, inside
the chronicle save. It is then published with `sync.promote(cid, kind, eid)`
as **a journalled step of that same commit** (`store/commits.py`, #271), not as
an after-the-fact call. The commit journal exists because `PUT /chronicle` is a
multi-step, non-idempotent write. Publishing is one more such step, and doing
it outside the journal reopens exactly the hole the journal closes. If a
response is lost after promote wrote the world file, the retry must not see its
own record in the world and report a collision. So:

- **Before** calling promote, the commit journals `publish:<kind>/<eid>` as
  attempted. **After** it returns, the commit journals the outcome
  (`published`, or `failed` with the reason).
- A retry that finds the step's outcome in the journal reuses that outcome and
  does not call promote again.
- A retry that finds the step only *attempted* (a crash between the call and
  the outcome) asks whether the world already holds this record and the
  campaign's sync base for it matches. That base is what `promote` writes first,
  and the world file second. If both are present, the retry records
  `published`; otherwise it calls promote again. Promote's own precondition (no
  world record under that id) makes the base-without-world-file residue retry
  cleanly, as `sync.promote`'s docstring already describes.
- A spent token replays the stored result, `published` lists included, as it
  already does for the rest of the save.

Promote takes `campaign_lock(cid)` itself, and that lock is a re-entrant RLock,
so calling it inside the chronicle save's own hold on the same campaign
acquires nothing new and adds no lock-order edge. A promote that fails never
fails the save. The
save response gains `published: [...]` and
`publish_failed: [{kind, id, reason}]`. The review shows any failure ("kept in
the campaign: the world already has a record named X — publish from the editor
after resolving it"). That covers `promote`'s precondition that the world holds
no record under that id.

Undo needs nothing new. A created record is already in `undo.NOT_UNDOABLE`
(`new_lore`, `new_location`): undoing a creation would mean deleting the record.
So the Changes panel already declines it, publish or no publish. The review row
says, under the destination control, that publishing to the library is undone
from the world page (demote), not from Changes.

---

## 10. Inspector (#128, world-info half)

`assemble.describe` rows for `world_info` and `recalled_lore` gain
`entries: [...]`, and `world_info` gains `held_back: [...]`:

```
{ref, name, kind, secrecy, priority, keep, level,
 reason: {type: key|keyless|pinned|sticky|recursion|owner_unlocked|recall,
          key?, secondary?, post?|seed?, via?, owner?, owner_presence?,
          sticky_remaining?, score?},
 shed: bool}
held_back: {ref, name, reason: {type: cooldown, remaining}}
```

`ContextBreakdown.tsx` renders these under the expanded section: one line per
entry with its name as a chip (not a link: `RecordDrawer` opens only actors and locations) and its reason in words:

- "key 'Saltmarch' in post #41"
- "pulled in by *Realm charter*"
- "always on"
- "pinned"
- "owner present: the lantern (held by Mara)"
- "sticky — 2 posts left (from post #38)"
- "recalled (similarity 0.52)"

Shed entries are struck through with "shed: budget". Held-back entries are listed
under them ("on cooldown — 3 posts"). A post number is a transcript index and
links to the post where the play view supports it. The live view
(`context_breakdown`) and a sent turn's capture are both described by the same
code, so they cannot disagree about a reason.

---

## 11. Delivery

Six slices, each independently shippable with its own tests, in this order:

1. **Audit, fields, adopt.** The §4 table and tests, the widened stash, native
   field mapping on import, `lorebook.adopt` with both routes and the editor
   affordances, and the field definitions and validation of §3 (stored and
   displayed, not yet honoured).
2. **Activation engine and reasons.** `activation.py`, key logic, per-entry
   scan depth, sticky/cooldown replay, recursion and `lore_recursion_depth`.
   Reasons are returned but not yet rendered.
3. **Presence and `known_by`.** §7 and §8, the Owners picker widened, ref
   rewrites, the actor rule.
4. **Shedding.** §6.
5. **Inspector UI.** §10.
6. **Absorb.** §9: contract and template, review row, provenance, publish to
   world.

Slices 2–4 are backend-only and change no prompt for a store that sets no new
field, which the byte-identity test of §1 holds them to.

### Tests that carry the design

- **Byte identity.** A store with no new fields and recursion 0 composes the
  same narrator prompt as before slice 2, for every case `test_context` already
  builds. The frozen-campaign sweep is regenerated only if it moves, with the
  move explained.
- **Key logic.** All four operators, plus an empty secondary list.
- **Timed effects.** Sticky carry, cooldown hold, a scene boundary resetting
  both, a cut and a reroll changing the outcome as the transcript does, and a stored
  director note counting as one message for timers and matching keys exactly as
  it does in today's scan window (§5.3).
- **Recursion.** The depth cap; each of the four `recursion` values; a cycle
  (A pulls B pulls A) terminating; recall not pulling.
- **Presence.** Each structural rule; a chained case (leader → group → item
  the group holds) reaching its fixed point under a shuffled candidate order;
  presence by activation unlocking owned lore with recursion 0; and an absent
  owner beating sticky.
- **`known_by`.** Narrator vs the named actor vs another actor. Location-owned
  public lore reaches an NPC; character-owned lore still does not. A `known_by`
  naming a deleted actor reaches no call and does not raise.
- **Shedding.** Priority order and every tie-break, including keyless, seed-only
  and recursion entries at equal priority; `keep` and pins never shed; a
  secret shed with its entry; a section without `shed` packing exactly as
  before; an unbounded budget untouched.
- **Reasons out of the prompt.** Reason and citation text absent from every
  composed prompt.
- **Absorb.** Kind clamped; a created record's citation recorded in
  `provenance.json` under its new id (it is dropped today); publish success and the precondition
  failure; a lost-response retry after a successful publish replaying
  `published` rather than `publish_failed`; a crash between promote and its
  journalled outcome resolved by the base-and-file check.
- **Frontend.** Following the list/detail rule: the Activation disclosure in
  edit, the chips in view, the adopt banner, the inspector entry lines, and the
  review row's keys, kind and destination.

### Owned-lore eval

`evals/cases.py` case 4 (owned-lore containment) gains a `known_by` variant.
Lore owned by Seraphine and known by Mara must reach Mara's call and the
narrator, and must not reach Seraphine's own call.
