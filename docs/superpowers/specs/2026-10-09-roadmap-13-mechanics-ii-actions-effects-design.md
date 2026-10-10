# 13. Mechanics II: Actions and Effects

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 13 in `ROADMAP-CHECKLIST.md`. Lane: mechanics.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the directional repo spec
`docs/superpowers/specs/2026-10-04-mechanics-ii-design.md` (cited below as
**MII**, by its section numbers) and the roadmap bundle draft
`13-mechanics-II-actions-effects.md` of 2026-10-06 (cited as **the draft**).
This spec is the design both point at. Section 3 says, section by section,
which parts of MII it keeps, refines or supersedes. MII stays in the tree as
the record of the direction; where the two disagree, this spec wins.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. Preserve landed behaviour unless this
> spec explicitly supersedes it.

## Depends on

The 13 edge, as settled with 01e: **nothing for II-A to II-D; 01c-C2/C3/C4
(H for C3); 01e-C3a (H for multi-target Actions only); 01e-C3b, 01c-C1,
01b-C1, 01a-C1, 02-C2, 02-C3 (S).** A legal set past 254 options needs nothing
from 01e: it is asked in two steps, the action and then its target (24.3).

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `inference.decide`, `routing.ROUTES` with `operation="decide"`, `require_inference(..., operation="decide")` | 01 (landed) | The `npc-action` decide route of 13-C3 (section 24) | Hard, and met |
| 01c-C2: the `draws.py` draw `(distribution, seed) -> selected` | 01c | Drawing an NPC's Action from the Decision model's distribution (24.4) | Hard, for 13-C3 only |
| 01c-C3: the replay record, stored with the outcome and never re-drawn | 01c | Persisted on the action proposal, the round and the transaction (24.5) | Hard, for 13-C3 only |
| 01c-C4: abstain, refuse, unreadable, error or no usable distribution is never sampled | 01c | What an NPC turn does when the Decision model gives no usable distribution (24.4) | Hard, for 13-C3 only |
| 01e-C3a: `MultiSelect`, where an empty selection is a real answer | 01e | The target set of an Action with `targets.max > 1`, asked as the second step after the action is drawn (24.3) | Hard, for multi-target Actions only |
| 01e-C3b: `Joint`, one action plus one target in one flattened choice, keyed by `Pair.key` (`head=>tail`) | 01e | The single-step form for a single-target legal set of at most 254 pairs, with head-level readings (24.3) | Soft: a plain flat `Choice` over the same pairs exists today (`decisions.py:189-196`) |
| 01c-C1: the distribution policy (`TaskPolicy`, `reports_distribution`) | 01c | Whether a structured Decision model can drive sampling at all | Soft: without it, everything takes the unsampled path of 24.4 |
| 01b-C1: decision capture at every decide site | 01b | Capturing the `npc-action` decision to the prompt log | Soft |
| 01a-C1: eval cost, latency and token reporting | 01a | Reporting on the `npc-action` eval gate (24.7) | Soft |
| 02-C2: a sampled next speaker and a turn intent (02-C2b) | 02 | The intent that conditions an NPC's Action distribution (24.6) | **Soft seam**: an intent never changes legality |
| 02-C3: the per-contribution turn plan's `extra` slot | 02 | Carrying the NPC Action question in the plan's batched decide (24.6) | Soft: without it the question is its own `npc-action` call |

**II-A through II-D (13-C1, 13-C2) start now**; only 13-C3 waits for 01c
(section 4).

## Required by

None. No other roadmap spec consumes 13. (Inside this spec, 13-C3 builds on
13-C1a and 13-C1c, and 13-C2a/b/c on 13-C1b.)

---

## 1. Current state (reconciled against main)

Everything in this section was read at `35c1fb7`. It is the ground the design
stands on, and several facts here contradict MII (1.9).

### 1.1 Module packs

- A module is a declarative pack: `module.md`, `sheets.json`, optional
  `checks.json`, `rules/*.md`, `content/`, `layout.json`, `theme.json`
  (`store/modules/pack.py:171-250`). No module code runs
  (`store/modules/__init__.py:1-8`). Packs live in the built-in directory or the
  user library `<home>/modules/`, and a user pack shadows a built-in one of the
  same id (`pack.py:66-76`). **A pack is global**: every campaign bound to it
  reads the same files.
- `load_pack` never raises; problems accumulate in `pack["errors"]`, and
  `binding.resolve` treats a pack with errors as no module at all
  (`store/modules/binding.py:52-73`). So "the module is invalid" already
  degrades to "no mechanics", never to a half-working pack.
- Field types are `number`, `dots`, `track`, `resource`, `text`, `list`, `ref`
  (`store/modules/validate.py:14`). `dots`/`track`/`resource` require an
  integer `max` (`validate.py:70-73`). Reserved names are `difficulty`,
  `modifier`, `new` and the expression functions (`validate.py:16`, `56-61`).
- Checks carry `label`, `roll` (dice notation with `{expr}` placeholders),
  `requires` (groups), `difficulty`, `rules` and an optional `outcomes` ladder;
  `_defaults` carries a default difficulty and ladder (`validate.py:220-276`,
  `pack.py:214-224`). A ladder entry is `{label, when}` and `when` may name only
  `ROLL_SCOPE_NAMES = total, natural, margin, successes, ones, dice`
  (`validate.py:192-217`).
- Module edits are staged, validated and published under the global
  module-edit lock **and every campaign's lock** (`module_edit/migrate.py:418-432`,
  the `with _campaign_locks()` hold at `494`). A check rename is refused while a non-terminal roll proposal names
  the check (`module_edit/renaming.py:75-98`).

### 1.2 Sheets, and what `gen` does and does not mean

- A campaign sheet is `<campaign>/sheets/<kind>--<id>.json`,
  `{"sheet_type", "fields", "gen", "creation"?}` (`store/sheets/paths.py:66-73`,
  `92-93`). Derived values are computed on read, never stored.
- **`gen` is an identity nonce, not a version.** It is preserved across every
  same-type value write (`sheets/paths.py:27-39`; `writer.py:64-68`,
  `202-204`) and re-minted on creation, on a type change, and by a module
  edit's sheet migration for every sheet a field, sheet-type or content rename
  rewrites (`module_edit/migrate.py:234`). A value edit leaves `gen` exactly as
  it was.
- `MUTABLE_TYPES = ("resource", "track", "list")` (`sheets/schema.py:187`).
  `set_field_locked` is the per-field strict-CAS writer and refuses any other
  type (`writer.py:169-204`); the audit writes through it and draws the same
  line (`audit/apply.py:110`). `number` and `dots` change only through a
  whole-sheet write, creation or advancement.
- `canonical_field_value` keeps a resource's **live** `max` (`schema.py:190-202`).
- `validate_sheet_values` bounds `dots`/`track` to `0..max` and `number` to its
  optional `min`/`max`, but **does not bound a resource's `current` at all**
  (`validate.py:296-314`). A resource at `-3/8` or `11/8` is a valid sheet today.

### 1.3 Checks, dice, rolls

- `checks.resolve_check(cid, check_id, actor_ref, difficulty, modifier, seed)`
  is pure apart from the dice draw and takes the campaign lock
  (`store/checks.py:119-180`). It substitutes the sheet's numeric scope into the
  roll template, rolls, grades the result with `evaluate_tier`, and returns
  `{check, check_label, actor, actor_label, notation, result, tier, difficulty,
  modifier, tier_warnings}`.
- The tier is the first matching ladder label (the check's own ladder,
  exclusively, if it has one; else `_defaults`'), else the dice engine's own
  `outcome` (`success`/`failure`, set only for a `vs` sum roll), else `None`
  (`checks.py:57-92`, `173-174`; `dice.py:128-134`).
- `dice.roll(notation, seed)` replays bit for bit from a 53-bit seed
  (`store/dice.py:114-135`). Whether a roll is a pool (`tN`) or a sum, and
  whether it has `vs`, is a property of the notation's grammar, so it is known
  from the check's template before any roll (`dice.py:36-70`).
- `rolls.json` is append-only; `find_or_append_by_proposal` makes projection
  idempotent by proposal tag (`store/rolls.py:94-112`). Branching re-finds a
  roll by "label in the line, and dice segment in the line"
  (`store/branch.py:67-105`).
- `available_checks(cid, sid)` enumerates the sheeted scene cast plus the
  sheeted current location, each with the checks its sheet type's groups satisfy
  (`checks.py:183-227`).

### 1.4 The proposal state machine

`store/proposals.py` keeps one record per scene in `<campaign>/proposals.json`:
`pending -> resolving -> resolved`, `pending -> declined`, `resolved|declined
-> narrated`, and `superseded` (terminal). The legal CAS edges are enumerated
(`TRANSITION_EDGES`, `proposals.py:64-69`), only `resolving -> resolved` may
carry a resolution (`RESOLUTION_EDGES`, `83`), `update_resolution` refuses to
change a stored `result` (`219-234`), and every exit from a projectable state
heals first (`new` `117-145`, `supersede` `237-247`, `commit_narration`
`352-394`, `heal` `305-349`). `project` writes the roll entry and the 🎲 line
idempotently and bumps the revision in a `finally` (`250-302`).

The adjudication route `POST .../roll-proposal` (`routes/mechanics.py:125-348`)
reserves a `continuation` turn keyed on the proposal, claims, calls
`resolve_check`, and on any failure reverts `resolving -> pending`
(`252-276`). A request that finds the record `resolving` answers **409
"adjudication in progress"** (`237`, `249`). Its body is
`ProposalAction{proposal, action: "accept"|"decline", check, actor,
difficulty, modifier}` (`routes/models.py:422-428`) — **`action` there already
means accept or decline**, which section 8.4 has to work around.

### 1.5 The fence, the prompt section, the continuation

- The model requests a check with one fenced block, ` ```roll {json} ``` `,
  then stops (`templates/scene/sections/mechanics_response_format.j2`). One
  `FenceWatcher` with one opener grammar (`store/fence.py:15`, `49-149`) closes
  the stream on the first fence; `parse_roll_body` is tolerant JSON-then-regex
  (`fence.py:152-177`).
- `streaming._make_proposal` turns the fence into a payload, resolving the
  actor against `available_checks` and collecting `problems`; a bad proposal
  is never dropped, it opens the chip in Modify (`routes/streaming.py:1395-1450`).
  `_finalize_locked` then calls `proposals.new` (`1107-1120`).
- The section is `mechanics_response_format` in the LOCK_IN pack
  (`store/context/assemble.py:1050-1051`); its data comes from
  `context.mechanics._mechanics`, which takes the campaign lock so a
  half-published pack is never read (`store/context/mechanics.py:55-109`).
- An accepted check's continuation appends `scene/roll_result.j2` as an
  ephemeral system block with the `on_roll` rules and the check's linked rules
  (`routes/mechanics.py:69-104`).
- A paused character round resumes through `character_turns.resume_roll`
  (`routes/character_turns.py:1317`), with the roll block appended.

### 1.6 Audit, journal and undo, tracker

- The end-of-scene mechanics audit shows each mutable field as `start X -> now
  Y` against a scene-start baseline, lists the scene's roll log, and turns the
  model's `sheet_deltas` into staged edits through a deterministic gate that
  drops agreements as no-ops (`store/audit/prompt.py:52-105`,
  `audit/apply.py:83-136`).
- The change journal (`store/journal.py`) and `store/undo.py` make journalled
  writes reversible by snapshot plus CAS, and **deliberately decline the
  `sheet` kind** because the audit owns its own conflict contract
  (`undo.py:140-142`). Journal rows are capped (`RETENTION`, `MAX_BYTES`).
- The scene tracker is narrative state keyed by scene identity
  (`store/tracker/records.py`). Nothing writes mechanics into it or reads
  mechanics out of it.

### 1.7 Locks, revision, fan-outs, guards

- Every campaign-scoped mutator takes `locks.campaign_lock(cid)` and is
  classified in `locks.DOMAIN_MODULES` (`store/locks.py:167`), held by
  `test_lock_domain_guard.py`. Only `locks.hold_all` holds two.
- `revision.bump` is stamped by the activity middleware for 2xx answers, and
  by the writer itself where a write can land and the request then fail, or
  where no request covers it (`store/revision.py:1-60`; `proposals.project`'s
  `finally` is the precedent).
- Scene ids move on rename and are followed by `scene_refs.repoint`
  (twenty-two stores, `store/scene_refs.py:1-40`); record refs move on
  reclassify and are followed by `record_refs.repoint` (five stores,
  `store/record_refs.py:1-48`).
- **No store write is atomic across files** (`docs/store-guarantees.md`, "Atomic
  writes"). Phase 4 accepted two microsecond crash windows on that basis
  (`2026-07-12-mechanics-phase4-play-integration-design.md`, "Crash-window
  disclosure"). A multi-sheet Action cannot accept them: a lost window there is
  half an attack applied.

### 1.8 Decide and group play (for 13-C3)

- `inference.decide(task, items, *, client, resolved, explain, campaign,
  scene, post, round_id, capture, around)` (`backend/src/grimoire/inference.py:684-688`).
  Questions are `Predicate | Choice(allow_none) | Score`; an `Answer` carries a
  `distribution` only when a backend reported one, never a stand-in
  (`decisions.py:181-244`). A native choice holds at most 255 options
  (`decisions.py:140`).
- The speaker pick is a decide call over the eligible refs
  (`routes/character_turns.py:728-761`); group play's planner is pure and takes
  injected randomness, storing its plan on the round record so a retry never
  re-rolls (`store/group_play.py:1-20`). That is the pattern 13-C3 follows.
- Routes are declared in `routing.ROUTES` (`store/routing.py:32-139`); the
  Models page draws its rows from that tuple (`store/inference/settings.py:305`).

### 1.9 Where the code contradicts the prior documents

1. **MII section 13's `expected_gen` / `result_gen` cannot detect a value
   change**, because `gen` does not move on a value write (1.2). The transaction
   CAS must compare the touched field values, and the sheet type and `gen`
   only to detect a re-created or retyped sheet (section 11.2).
2. **MII section 7's `add` on "numeric/dots/track"** would let an Effect move
   `number` and `dots` fields, which today only creation, advancement and a
   whole-sheet write may change, and which neither `set_field` nor the audit
   will touch (1.2). II-A restricts Effects to `resource` and `track` (7.1).
3. **A resource's `current` is unbounded by validation** (1.2), so "clamp/reject
   must follow field schema" has no schema to follow for the most common
   target. The engine states the bounds itself (7.2).
4. **MII 10.1 left the fence wire format open.** One `FenceWatcher`, one opener
   and a one-fence-per-reply rule already exist (1.5); an Action rides the same
   ` ```roll ` fence (8.2), which also settles MII's "do not let both action and
   raw roll proposals describe the same interrupted generation".
5. **The start condition**: II-A to II-D need nothing beyond landed code;
   only 13-C3 waits for 01c (Depends on; the checklist now agrees).
6. **`ProposalAction.action` is taken** (1.4).
7. **MII's single `mechanics_transactions.json`** would rewrite the whole
   history on every status change and collide across synced devices; and a
   scan for unfinished transactions would cost the campaign's age (section 10.1
   chooses a per-transaction layout with an `open/` directory).

## 2. Goal

Give a module a way to say **what an actor may do and what that formally
changes**, and give Grimoire one engine that resolves it the same way whether a
player clicked it, the play model proposed it, or (13-C3) a Decision model
picked it for an NPC:

```text
intent (player, play model, or NPC seam)
   -> action proposal                      (one state machine: proposals.py)
   -> deterministic validation             (availability, targets, costs)
   -> engine roll, when the Action has one (checks.resolve_check)
   -> concrete operations from the outcome (the Effect DSL)
   -> transaction recorded, then applied   (roll durable before any effect)
   -> projection and narration of what already happened
   -> end-scene audit verifies the exceptions
```

The law, from the draft and MII 4: **models interpret intent and choose among
permitted structures; the engine owns legality, randomness and state
transitions.** A model never writes a sheet, never rolls, never invents an
Action and never redefines an outcome.

What this is not (the full list is section 28): not a combat engine, not
initiative, not executable module code, not a replacement for the audit, and
not automatic NPC play in II-A.

## 3. Reconciliation: what supersedes what

| MII section | Status here | Where |
|---|---|---|
| 1-5 (motivation, goal, non-goals, invariants, milestone order) | **Kept.** Restated in 2, 4, 28 | |
| 6 `actions.json` | **Refined**: adds `values`, `targets.self`, `adjustable`, the `_otherwise` branch and the reachable-tier rule | 5 |
| 6.3 unknown outcome tiers | **Superseded** by the reachable-tier computation | 5.2 |
| 7 Effect DSL | **Superseded** in field types (resource/track only), bounds (engine-stated, `add` clamps by default), cost ops (`spend`, and `add` with `bounds: reject`), and evaluation order (amounts against the claim-time snapshot, application chained). Fan-out **kept** | 7 |
| 7 reference/list ops "later in II-A if straightforward" | **Superseded**: a named later slice, II-A2 | 7.7 |
| 8 expression scope ("no cross-actor arithmetic in v1") | **Superseded**: actor-scope `values` are the bounded way an actor's numbers reach a target's effect | 5.3, 7.3 |
| 9 availability | **Refined**: reason codes, target pools, and the legal set with a digest | 6 |
| 10, 10.1 proposals and fence | **Refined; 10.1 resolved**: same ` ```roll ` fence, `kind: "action"` payload | 8 |
| 11 player-initiated actions | **Refined**: a player Action is a proposal accepted through the one adjudication route | 8.3 |
| 12 resolution object | **Refined** to the exact shape | 9 |
| 13 transactions | **Superseded** in layout (per-file, `open/` and `done/`), CAS basis (values, not `gen`), unit of application (one file), and stall handling | 10, 11 |
| 13.1 roll/record/apply/commit | **Kept**, with the handoff to the proposal made part of the recoverable sequence | 11 |
| 13.2 one campaign lock | **Kept** | 11.1 |
| 14 undo | **Refined**: the mechanics ledger is authoritative; the journal is not touched | 12 |
| 15 continuation narration | **Kept**, template named | 13 |
| 16 conditions (II-B) | **Refined**: store path, modifier report, action gating | 20 |
| 17 clocks (II-C) | **Refined**: how an Action addresses a clock | 21 |
| 18 contests (II-D) | **Refined**: roll-log tags, outcome keys | 22 |
| 19 encounter (II-E) | **Kept** as a sketch needing its own spec | 23 |
| 20 tracker | **Kept** | 16 |
| 21 audit after actions | **Refined**: a deterministic guard against reversing a transaction | 15 |
| 22 prompt budgeting | **Refined**: a cap, and a byte-identity rule for packs without Actions | 14 |
| 23 UI | **Refined** to the repo's list/detail and rail rules | 19 |
| 24, 25 authoring and validation | **Refined**: rename fan-out and guards named | 5.4, 17 |
| 26, 27 costs, failure branches | **Kept** | 7.4 |
| 28 manual GM override | **Kept** | 8, 12 |
| 29, 30 continuity, Todo | **Kept** as deferred; one chore defined | 11.5 |
| 31 API | **Superseded** | 18 |
| 32-36 tests and acceptance | **Superseded** by | 27 |
| 37 later work | **Kept**, except that NPC Action choice moves from "later" into 13-C3 | 24 |
| 38-39 first target, completion boundary | **Kept** | 4, 27 |

The draft adds nothing MII does not say except the Decision seam (its section
27), which becomes 13-C3.

## 4. Milestones and slices

**II-A (13-C1)** is the substrate and the only milestone this spec details to
plan level. Suggested slices, in dependency order:

| Slice | Content | Sections |
|---|---|---|
| A1 | `actions.json` format, pack validation, load into `pack["actions"]` | 5 |
| A2 | Availability, target pools, the legal set, `GET .../actions` | 6, 18 |
| A3 | The Effect engine: pure expansion and evaluation, no writes | 7 |
| A4 | The transaction ledger, the sheet unit writer, recovery, settle, undo | 10-12 |
| A5 | Proposal integration: payload, adjudication, revert guard, heal, projection, player-initiated route | 8, 9, 11.4 |
| A6 | Fence parsing, prompt section, continuation template, evals | 8.2, 13, 14 |
| A7 | Audit integration | 15 |
| A8 | Frontend: proposal card, Action palette, resolution card, History, authoring | 17, 19 |

A1-A4 can land with no route that resolves an Action (the engine is exercised
by store tests). A5 is the first slice a player can see.

**II-A2** adds `list_add` / `list_remove` (7.7). **II-B** conditions,
**II-C** clocks, **II-D** contests (13-C2a/b/c), in that order, each a new unit
kind and new ops inside the II-A transaction. **II-E** encounter structure needs
its own spec (23). **13-C3**, the NPC seam, needs II-A and the 01c/01e contracts
named above, and nothing from II-B to II-E.

The first demonstration (MII 38, kept): a checked Action that spends a resource
and changes a target on success; a checked Action with a failure branch; and a
no-roll Action that moves a resource.

---

## 5. II-A: the Action definition

### 5.1 `actions.json`

An optional pack file. Absent means the pack has no Actions and every surface
behaves exactly as today. Example, written against the shapes of the shipped
`d20-basic` pack (`hp` is a resource, `spell_slots` a track):

```json
{
  "strike": {
    "label": "Strike",
    "description": "Attack someone within reach.",
    "sheet_types": ["warrior", "adept"],
    "check": "athletics",
    "targets": {"min": 1, "max": 1, "kinds": ["characters", "pcs"], "self": false},
    "values": {"damage": "max(1, 1 + str_mod + margin // 5)"},
    "costs": [],
    "outcomes": {
      "critical success": [
        {"op": "add", "target": "target", "field": "hp", "amount": "-(damage * 2)"}
      ],
      "success": [
        {"op": "add", "target": "target", "field": "hp", "amount": "-damage"}
      ],
      "critical failure": [
        {"op": "add", "target": "actor", "field": "hp", "amount": "-1"}
      ]
    },
    "rules": ["skill-checks"],
    "tags": ["attack"]
  },
  "mend": {
    "label": "Mend",
    "description": "Close someone's wounds with a slot of power.",
    "sheet_types": ["adept"],
    "targets": {"min": 1, "max": 1, "kinds": ["characters", "pcs"], "self": true},
    "costs": [{"op": "spend", "target": "actor", "field": "spell_slots", "amount": "1"}],
    "outcomes": {
      "resolved": [{"op": "restore", "target": "target", "field": "hp", "amount": "2"}]
    },
    "tags": ["healing"]
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `label` | non-empty string | Display name; required |
| `description` | string | One line, shown in the palette and the prompt |
| `sheet_types` | list of sheet-type ids | The actor's sheet type must be one of these. Omitted: any sheet type that can resolve `check` (its groups satisfy `requires`), or any sheet type at all for a no-roll Action |
| `check` | check id | The roll. Omitted: a no-roll Action |
| `targets` | object | `min`, `max` (integers, `0 <= min <= max`), `kinds` (sheet file kinds, `sheets.paths.FILE_KINDS`), optional `sheet_types`, `self` (may the actor be a target; default `false`). Omitted: `{"min": 0, "max": 0}` |
| `values` | map name -> expression | Numbers computed once, in the **actor's** scope (5.3) |
| `adjustable` | list, subset of `["difficulty", "modifier"]` | Which check parameters a proposal may set. Default: both, when there is a check; none otherwise |
| `costs` | list of effect templates | Paid before the roll; must be payable (7.4) |
| `outcomes` | map branch key -> list of effect templates | Branch keys per 5.2 |
| `rules` | list of rule ids | Rules docs added to the continuation (13) |
| `tags` | list of strings | Grouping in the palette; the II-B modifier target; the 13-C3 intent hint |

Key order in the file is the Action order everywhere (palette, prompt, legal
set). JSON object order is preserved by `json.loads`, and an author reads the
file in that order.

### 5.2 Branch keys and the reachable tiers

The draft and MII both said "every outcome key must name a tier the check can
produce" without saying what that set is. It is computable from the pack, and
`checks.evaluate_tier` (1.3) fixes it:

```python
def reachable_tiers(check: dict, defaults: dict) -> set[str]:
    ladder = check.get("outcomes") if "outcomes" in check else defaults.get("outcomes")
    tiers = {t["label"] for t in ladder or [] if isinstance(t, dict) and t.get("label")}
    shape = roll_shape(check)          # from dice.parse of the template, placeholders sampled
    if shape.vs:                       # the dice engine's own outcome is the fallback
        tiers |= {"success", "failure"}
    return tiers
```

- A checked Action's branch keys must be in `reachable_tiers`, or be
  `_otherwise`, which is selected when the resolved tier has no branch of its
  own (including a `None` tier). A tier with no branch and no `_otherwise`
  selects **no outcome effects**; costs still apply (7.4). This is the honest
  reading of an author who wrote only a `success` branch.
- A no-roll Action has exactly one key, `resolved`.
- Branches are exclusive: one tier selects one branch (MII 27, kept). No
  inheritance between `critical success` and `success`.

`roll_shape(check)` is a new pure helper beside `checks.roll_scope`: it parses
the roll template with every placeholder replaced by `3` (the trick
`validate.py:263` already uses) and reports `pool` (a `tN` roll), `vs`, and so
the roll-scope names a roll of that shape can produce: `dice`, `ones` always;
`natural` always (a first die exists); `total` for a sum roll; `margin` for a
sum roll with `vs`; `successes` for a pool roll. Section 7.3 uses it to reject,
at load, an expression that names a roll value the Action's roll can never
have.

### 5.3 `values`: how an actor's numbers reach a target

MII 8 forbade cross-actor expressions in v1 and pushed actor-vs-target
arithmetic into contests. That leaves the most ordinary Action unwritable:
damage that depends on the attacker. `values` is the bounded alternative:

- Each value is an expression evaluated **once**, in the actor's scope (the
  actor's numeric fields and derived values, `sheets.schema.expression_scope`),
  plus the roll scope when there is a roll, plus `difficulty`, `modifier` and
  `targets` (the selected target count).
- Values are evaluated in file order; a value may name an earlier value, to a
  chain depth of at most `MAX_VALUE_DEPTH = 4`. At most `MAX_VALUES = 16`
  values, each expression (here and in every template) at most
  `MAX_EXPRESSION_CHARS = 256`. Every value and every amount result must lie
  within `MAX_EFFECT_MAGNITUDE = 10**9`, or the evaluation fails with
  `amount_out_of_range` (7.3). The language has no power operator, so one
  bounded-length expression over bounded inputs is bounded; only chaining
  could grow a number without limit, and that is what the depth and magnitude
  caps stop. Without them a shared pack could run unbounded big-integer
  arithmetic inside the campaign lock, or produce a roll whose record
  `json.dumps` refuses (an integer past 4300 digits). The constants are
  structural ceilings, far above any game's arithmetic, to be tuned later.
- The resulting integers join the scope of **every** effect expression, actor
  or target.
- A value name may not be a reserved name, an expression function, a
  `ROLL_SCOPE_NAMES` entry, `targets`, or **any name any sheet type in the pack
  puts in scope**: a field, a derived value, or a resource's implicit
  `<key>_max` (`modules/fields.py`, `numeric_names`). The last rule is what keeps a name from meaning one
  thing on the actor and another on a target, and it is static, so the pack
  validator enforces it.

Nothing else crosses: a target's effect cannot read the actor's sheet by name,
and an actor's effect cannot read a target's. Arithmetic that genuinely needs
both sides' sheets (a defence total) is a contest (22).

### 5.4 Pack validation (`modules/validate.py::_validate_actions`)

Load-time errors, which make the pack invalid and so unbound (1.1):

- not an object; an id that fails `safe_id`; a missing or empty `label`;
- an unknown sheet type in `sheet_types` or `targets.sheet_types`; a target
  kind outside `FILE_KINDS`; a target sheet type whose kind is not in `kinds`;
  `min`/`max` not integers or `min > max` or negative;
- an unknown `check`; an unknown rule id; `adjustable` naming anything else;
- a branch key outside 5.2's set; `resolved` on a checked Action; any other key
  on a no-roll Action;
- an effect template that fails 7.2's per-op checks: unknown `op`, an op not
  yet in the DSL (7.7), a `target` selector other than `actor`/`target`, a
  `target` selector on an Action whose `targets.max` is 0, a missing `field`,
  `amount` neither an integer nor an expression string;
- a cost op other than `spend` or `add` with `"bounds": "reject"`, or a cost on
  the `target` selector (7.4);
- an expression that fails `expressions.parse`, or names something outside its
  scope: roll names the check's `roll_shape` cannot produce (or any roll name on
  a no-roll Action or in a cost), `difficulty`/`modifier` on a no-roll Action,
  a value not defined above it;
- a value name collision (5.3); more than `MAX_VALUES` values; a value chain
  deeper than `MAX_VALUE_DEPTH`; an expression longer than
  `MAX_EXPRESSION_CHARS`;
- a cost amount that names `targets`, `difficulty`, `modifier`, a roll name, or
  a value that depends on any of them, directly or through other values. A
  cost is evaluated before the roll and before availability knows the targets
  or parameters (6.1, 7.4), so it may name only the actor's sheet and
  *static* values (values free of all of those);
- an effect `field` that is statically known not to be a mutable numeric field:
  for an `actor` op when `sheet_types` is given, and for a `target` op when
  `targets.sheet_types` is given, every listed type must carry the field as
  `resource` or `track`. When the types are not listed this cannot be known at
  load, and availability checks it per sheet (6.2).

Each error string follows the existing `actions.<id>...: ...` style, so the
authoring UI's per-field error mapping (Phase 8) needs no new mechanism.

---

## 6. II-A: availability and the legal set

### 6.1 One function, every surface

```python
# store/mechanics/availability.py
def available_actions(cid: str, sid: str, actor_ref: str | None = None) -> list[ActorActions]
def legal_set(cid: str, sid: str, actor_ref: str) -> LegalSet
def check_proposal(cid: str, sid: str, proposal: dict) -> list[Problem]
```

`ActorActions = {ref, label, sheet_type, actions: [ActionAvailability]}`, one
per actor in the actor pool, in pool order.

`ActionAvailability`:

```json
{"id": "strike", "label": "Strike", "description": "...", "tags": ["attack"],
 "check": "athletics", "check_label": "Athletics",
 "available": true, "reasons": [],
 "targets": {"min": 1, "max": 1, "eligible": ["characters:seraphine", "characters:winifred"]},
 "costs": [{"op": "spend", "ref": "characters:mara", "field": "spell_slots", "amount": 1,
            "before": 3, "after": 2}],
 "branches": ["critical success", "success", "critical failure"],
 "adjustable": ["difficulty", "modifier"]}
```

The palette (19), the prompt section (14), the fence parser (8.2), the
adjudication route (11.1) and the NPC seam (24) all call these. That is the
draft's requirement that humans, the play model and a Decision model "receive
the same legal Action set", turned into one call graph rather than a promise.

All three take the campaign lock across the read, for the reason
`context.mechanics._mechanics` does (a half-published pack must not be read),
write nothing, and never raise on store content: a problem is a reason, not an
exception.

### 6.2 Reasons

An Action is unavailable for an actor, with these codes, checked in this order
(the first failing gate short-circuits the ones that need it):

| Code | When |
|---|---|
| `no_module` | No valid module resolves (`binding.resolve` is `None`) |
| `no_sheet` / `sheet_invalid` | The actor has no sheet, or `sheet["errors"]` |
| `sheet_type` | The actor's sheet type fails `sheet_types` |
| `check_unresolvable` | The sheet type's groups do not satisfy the check's `requires` (the same test as `available_checks`) |
| `effect_field` | A template addressed to `actor` names a field the actor's sheet type lacks, or one that is not `resource`/`track` |
| `cost_unpayable` | Applying the costs in order to the current snapshot breaks a bound (7.4) |
| `no_eligible_target` | Fewer eligible targets than `targets.min` |

The list is the order a reader fixes them in. An Action can carry more than one
reason only where the gates are independent (`cost_unpayable` and
`no_eligible_target`).

### 6.3 Pools

- **Actor pool**: the sheeted scene cast (`appearances.cast.scene_cast`) and the
  sheeted current location (last of `get_location_history`), exactly
  `available_checks`' sources and order (`checks.py:183-227`). An actor not in
  the pool cannot act in this scene; a player who wants one present adds them
  to the cast first.
- **Target pool**: the same set. A target is eligible for an Action when its
  file kind is in `kinds`; its sheet exists, is valid and its type is in
  `targets.sheet_types` when given; it is not the actor unless `self`; and its
  sheet type carries every field the Action's `target`-addressed templates name,
  across all branches, as `resource` or `track`. The last condition means an
  Action is never offered against someone it could only half-affect.

Off-scene targets (a rival in another city, a faction) are later work; they need
a target source that is not the scene, which is a different question (28).

### 6.4 The legal set

```python
@dataclass(frozen=True)
class LegalOption:
    action: str
    targets: tuple[str, ...]          # in selection order
    multi: bool = False               # targets.max > 1: targets chosen by multi-select (24.3)

@dataclass(frozen=True)
class LegalSet:
    actor: str
    options: tuple[LegalOption, ...]  # deterministic order
    digest: str                       # sha256 over the canonical JSON of (actor, options, pack stamp)
```

`options` enumerates, for each available Action in file order, every target
tuple the count allows, in target-pool order: for `max == 0` the single empty
tuple; for `max == 1`, one option per eligible target (plus the empty tuple when
`min == 0`); for `max > 1`, **not enumerated** (the combinations explode) but
represented by one option with `targets == ()` and a flag `multi: true`, which
13-C3 asks as a two-step question, the second step a `MultiSelect` (01e-C3a, 24.3). The pack stamp is a digest of
the pack's `actions`, `checks` and `sheets` objects, the shape
`audit.baselines.schema_stamp` already uses for `sheets`.

The digest is what makes a stored NPC choice auditable: a replay record (24.5)
names the legal set it was drawn from, and a mismatch at proposal time says the
world moved between the question and the answer.

### 6.5 Validating a concrete proposal

`check_proposal(cid, sid, proposal)` returns problems for one concrete
`(action, actor, targets, difficulty, modifier)`: the Action's reasons for that
actor, plus `target_count`, `target_duplicate`, `target_ineligible:<ref>`,
`parameter_not_adjustable:<name>`. It is called when a fence is parsed (8.2, as
display problems), when a player creates an Action (8.3, as a 400), and again
**inside the lock at accept** (11.1), which is the only call whose answer
decides anything.

---

## 7. II-A: the Effect DSL

### 7.1 What an Effect may touch

II-A Effects address one field of one sheet, and only fields of type
**`resource`** (its `current`; `max` is never moved by an Effect) or
**`track`**. This is the line `set_field` and the audit already draw for
in-play change (1.2), minus `list`, whose ops are II-A2 (7.7). `number` and
`dots` are build statistics: creation, advancement and the sheet editor own
them, and an Action that drains a statistic for a while is a condition's job
(II-B), not a permanent write.

### 7.2 Operations

Every operation names `target` (`actor` or `target`), `field`, and an
`amount` (or `value` for `set`), each an integer literal or an expression
string. Bounds are stated by the engine, not by `validate_sheet_values` (1.9
item 3): a `resource` lives in `lower..upper = 0..max` (its live `max`), a
`track` in `0..max` (the field definition's `max`).

**The bounds constrain only the direction of the change.** A resource's
`current` is unbounded on disk today (1.2), so a field may already sit outside
its bounds (`-3/8` after an overshoot, `11/8` from a hand-entered surplus). An
Effect never moves such a value *against the sign of its own op*, and never
moves a value further outside the bounds than it already is. Inside the
bounds, the bounds hold as usual. With `b` the value before the op and `a` the
amount:

| Op | Amount | Result | Out of bounds |
|---|---|---|---|
| `set` | `value`, an integer | `value` | **Rejects** a `value` outside the bounds: an absolute value outside them is an authoring error, not a game event |
| `add`, `bounds: "clamp"` (the default for outcome effects) | signed | `a < 0`: `min(b, max(lower, b + a))`; `a >= 0`: `max(b, min(upper, b + a))` | Clamps, recording `clamped` when `applied != requested` |
| `add`, `bounds: "reject"` | signed | `b + a` | **Rejects** when `b + a` is outside the bounds *and* further outside than `b` (a move back toward the bounds is always allowed) |
| `spend` | `>= 0` | `b - a` | **Rejects** when `b - a < lower` (an overdrawn `b` therefore cannot pay anything). A negative amount rejects |
| `restore` | `>= 0` | `max(b, min(upper, b + a))` | Always clamps, recording `clamped`. A negative amount rejects |

So Seraphine at `-3/8` struck for 2 stays at `-3` (`applied: 0`, `clamped`),
never healed to `0`; Mara at `11/8` restored by 1 stays at `11`, never wounded
to `8`. Both cases are 7.5's tests.

`spend` and `add -N` differ on purpose (MII 7, kept): `spend` is a cost that
cannot be paid; `add -N` is a consequence that can overshoot. `restore` is
`add +N, clamp` with the sign enforced, which is what makes a healing Action
unable to wound by a sign error.

**Why `add` clamps by default** (a deliberate change from MII's "prefer
reject"): an outcome effect is computed after the roll. Rejecting it there
cannot hand the roll back (11.1), so a reject turns "the blow took Seraphine
below zero" into "the blow did nothing at all", which is the worse lie. A
clamped op records `requested` and `applied` deltas, so the narration, the
History and the audit all see that it overshot. A module that wants overshoot
to be impossible writes `"bounds": "reject"`. See Open question 2.

### 7.3 Evaluation

- An amount evaluates through `expressions.evaluate` (no new evaluator, no
  `eval`). Scope: the **addressed sheet's** numeric fields and derived values
  (`sheets.schema.expression_scope`), the roll scope (`checks.roll_scope` of the
  resolved result) when there is a roll, `difficulty` and `modifier` when there
  is a check, `targets`, and the Action's `values` (5.3).
- **Amounts are evaluated against the snapshot taken at claim** (11.1 step 1),
  never against the running state. Application chains (two ops on one field
  compound), but what an expression *means* does not depend on which template
  came first. MII said operations are "computed against the running state";
  this spec keeps that for application and drops it for evaluation, because an
  author reading `amount: "-damage"` should not have to know where a cost sits
  in the list.
- The result must be an `int` (not `bool`), or a finite `float` with an
  integral value (division yields one), which is converted. Anything else
  rejects with `amount_not_integer`. A value or amount whose magnitude passes
  `MAX_EFFECT_MAGNITUDE` rejects with `amount_out_of_range` (5.3), before it
  can reach a record.
- A name present in two layers of the scope (a field named `total` on a checked
  Action) is `ambiguous_name` at evaluation; the validator reports it at load
  wherever the sheet types are listed.
- A reference to a roll name the roll did not produce cannot happen for a
  checked Action (5.4 rejected it at load); if a pack edit makes it happen it
  rejects with `unknown_name`.

### 7.4 Costs

Costs are templates on the `actor` selector only, using `spend`, or `add` with
`"bounds": "reject"` (a cost that raises strain until a track is full). They:

1. are evaluated against the claim snapshot, with the actor's sheet and static
   values only (5.4 refuses anything else at load), so the same numbers come
   out at availability, at claim and at plan;
2. are checked for payability **before the roll**: applying them in order must
   respect 7.2's rules, or the Action "cannot be attempted"
   (`cost_unpayable`); nothing is rolled (11.1 step 1);
3. **land whatever the outcome, including a rejected one.** If the outcome's
   effects are rejected after the roll (7.5), the costs are still planned as
   units and applied, and only the outcome ops are dropped. The costs were
   proven payable against the same snapshot in the same lock hold, so they
   cannot fail there. Refunding them would let a module whose outcome can
   reject hand out free retries. MII 13.1 ("no operations") and MII 26 ("costs
   normally still apply") contradicted each other on this point; this rule
   settles it in MII 26's favour. A refundable cost is later work.

Costs apply first, ahead of the outcome effects (MII 26 and 27, kept). A cost
on a target is refused at load. Paying with someone else's resource is a
second actor's Action.

### 7.5 Multi-target fan-out

Kept from MII 7, made exact:

- one roll for a non-contested Action; one tier; one branch;
- a `target` template expands once per selected target, in the proposal's
  `targets` order; an `actor` template expands once;
- concrete operations are ordered: costs in template order, then outcome
  templates in template order, and within a `target` template, selection order;
- each expansion evaluates in its own addressed sheet's scope;
- a selection naming one target twice is rejected (`target_duplicate`) at
  validation, before the roll;
- **one failing expansion rejects the whole outcome**: no partial fan-out.
  After the roll that is a `rejected` transaction (11.1) whose only units are
  the costs (7.4), never a partial outcome.

Contests take exactly one target (22).

### 7.6 Expansion into units

```python
# store/mechanics/effects.py  -- pure: no I/O, no lock
def plan(action: dict, *, actor: SheetView, targets: list[SheetView],
         params: dict, roll: dict | None, tier: str | None) -> Plan
```

A rejected outcome is a `Plan` too: `rejection` set, the cost ops and units
present, and no outcome ops (7.4).

`SheetView` is `{ref, kind, id, sheet_type, gen, fields (merged with
defaults), derived, fdefs}`, read once at claim. `plan` evaluates values, picks
the branch, expands and evaluates every template, applies the ops in order to a
copy of the touched fields, checks every bound, and returns:

```python
Plan = {
  "rejection": None,                # or {"code": "amount_out_of_range", "detail": "..."}
  "values": {"damage": 3},
  "branch": "success" | "_otherwise" | "resolved" | None,
  "ops": [Op, ...],                 # display order, as 7.5
  "units": [Unit, ...],             # one per file touched, first-touch order
}
Op = {"op": "add", "selector": "target", "target_index": 0, "ref": "characters:seraphine",
      "field": "hp", "source": "outcome", "template": 0,
      "requested": -3, "applied": -3, "clamped": False, "before": 5, "after": 2}
Unit = {"store": "sheet", "ref": "characters:seraphine", "kind": "characters", "id": "seraphine",
        "sheet_type": "warrior", "gen": "9c1e...",
        "before": {"hp": {"current": 5, "max": 12}},
        "after":  {"hp": {"current": 2, "max": 12}}}
```

**The unit is one file**, and it carries the canonical before and after value
of every field the transaction touches in that file. Several ops on one field
fold into one unit write. This is what makes recovery decidable (11.3): a file
either reads `before`, reads `after`, or has been changed by someone else, and
those three cases never overlap unless the transaction is a no-op on that file,
which `plan` drops (a unit whose `after` equals its `before` is not written).

### 7.7 Operations named for later milestones

The validator refuses these until their milestone lands, because an op without
a store and recovery semantics is a promise the engine cannot keep (MII 7,
kept):

| Op | Milestone | Unit |
|---|---|---|
| `list_add`, `list_remove` (a string into or out of a `list` field; `remove` of an absent entry is a recorded no-op) | II-A2 | the sheet file, as above |
| `condition_add`, `condition_remove` | II-B | `<campaign>/mechanics/conditions.json` |
| `clock_advance`, `clock_set` | II-C | `<campaign>/mechanics/clocks.json` |

`ref` fields (`ref_add`/`ref_remove` in MII) are not in any milestone yet: a
`ref` value names content, and adding one is closer to inventory design than to
a state transition (28).

---

## 8. II-A: proposals

### 8.1 The payload

The proposal record keeps its shape (`proposals.new`); only the payload grows a
discriminator. A payload with no `kind` is a check proposal, so every record
already on disk reads as before.

```json
{"kind": "action", "source": "model" | "player" | "npc",
 "action": "strike", "action_label": "Strike",
 "actor": "characters:mara", "actor_label": "Mara",
 "targets": ["characters:seraphine"], "target_labels": ["Seraphine"],
 "check": null, "action_check": "athletics", "check_label": "Athletics",
 "difficulty": 14, "modifier": 0, "reason": "she swings at Seraphine",
 "available": {"characters:mara": [["strike", "Strike"], ["mend", "Mend"]]},
 "problems": [],
 "selection": null}
```

**`check` is always `null` on an action payload, and the Action's check
travels as `action_check`.** An older build sharing the store reads only
`payload["check"]`; accepting an action proposal there calls `resolve_check`
with `None`, raises `CheckError` and reverts cleanly, rather than resolving the
Action as a bare check and bypassing its legality and effects.

`available` mirrors the check payload's field of the same name so the chip's
Modify menus need no second request; target menus come from
`GET .../actions?actor=` (18). `selection` is the 13-C3 replay record, `null`
for every other source.

### 8.2 The fence

The play model proposes an Action with the **same ` ```roll ` fence**, naming
`action` instead of `check`:

````text
```roll
{"action": "strike", "actor": "characters:mara", "targets": ["characters:seraphine"], "difficulty": 14, "reason": "she swings at Seraphine"}
```
````

- `FenceWatcher` is unchanged; one fence per reply, so a reply can never carry
  both a check and an Action (1.9 item 4).
- `fence.parse_roll_body` gains tolerant patterns for `action` and `targets`
  (a JSON array, or a comma-separated string in the regex path). The `action`
  pattern is anchored on a key boundary, as `check` effectively is, so a
  `"reaction":` key does not match it. The regex path's "roll request had no
  check id" problem (`fence.py:174-176`) is not raised when an `action` was
  found.
- `streaming._make_proposal` branches on `"action" in fields`: actor and targets
  resolve by exact ref, then by case-insensitive label, against the pools
  (6.3). **A label that matches more than one ref is the problem
  `target_ambiguous` (or `actor_ambiguous`)**, never the first match: two cast
  members who share a name must not make the model's target silently the wrong
  one. Problems come from `check_proposal` (6.5) plus the parse problems. A bad
  Action proposal is never dropped; it opens the chip in Modify, the existing
  rule.
- Modify can change the Action itself: the adjudication body accepts an
  `action_id` override (8.4), re-checked by `check_proposal`, so an unknown or
  unavailable Action is repairable rather than only declinable.
- A fence carrying both `check` and `action` is an action proposal with the
  problem `check_and_action` (the `check` is ignored, never resolved).

`length_drift`'s whole-fence pattern is built from `fence.OPENER`
(`fence.py:13-15`), so it covers the Action body without change.

### 8.3 Player-initiated Actions

A player Action is a proposal created by the player and accepted through the
**same** adjudication route, so "one engine path" is structural:

```
POST /campaigns/{cid}/scenes/{sid}/action-proposal
  body: {action, actor, targets, difficulty?, modifier?}
  -> 200 {record}            the new pending proposal (source "player")
  -> 400 {problems}          check_proposal found problems; nothing written
```

The client then calls `POST .../roll-proposal` with `{proposal, action:
"accept", narrate}` (8.4). Two requests rather than one is deliberate: a
proposal that exists before it is accepted is what makes a double tap, a lost
response and a retry all land on the idempotent accept path the model's
proposals already use.

Creating the proposal calls `proposals.new`, which supersedes any pending
proposal for the scene after healing it (1.4). A model chip still pending when
the player acts is thereby retired, which is right: both are about the same
moment, and the player's choice is the newer one. The route holds the campaign
lock across `check_proposal`, `runs.require_scene_free` and
`runs.require_scene_open` (the heal inside `new` can append a projected line,
which is a shape change; `test_scene_freeze.py` gains the door).

**It is refused with 409 `round_open` while a character round is unfinished**
(`responses.unfinished(cid, sid)`, the refusal `routes/scenes.py:5752` already
answers). A paused round has released its scene key, so `require_scene_free`
passes; but the round is paused on its own proposal, and superseding that
would leave every later accept answering "the character round's proposal is
stale" (`routes/mechanics.py:170-199`). The player finishes or stops the round
first.

### 8.4 The adjudication body

`ProposalAction` gains two optional fields; `action` keeps meaning accept or
decline (1.9 item 6), and the Action's id travels in the payload, not the body:

```python
class ProposalAction(BaseModel):      # routes/models.py
    proposal: str
    action: str                       # "accept" | "decline", as today
    check: str | None = None          # refused (400) on an action proposal: the module fixes it
    actor: str | None = None
    targets: list[str] | None = None  # new; action proposals only
    difficulty: int | None = None     # refused unless the Action lists it in `adjustable`
    modifier: int | None = None       # likewise
    action_id: str | None = None      # new; action proposals only: Modify's Action override, re-checked
    narrate: bool = True              # new; False resolves and projects with no continuation
```

`narrate: false` resolves and projects, then ends in
`proposals.commit_narration(cid, sid, pid, lambda: None)`, which heals and
marks the record `narrated` with no new edge, the precedent `_pause` sets
(`routes/character_turns.py:848-849`). Leaving it `resolved` would be wrong:
`resolved` means "narration owed", so a reload or a second device would offer
"Continue narration" (`RollProposal.tsx`) and an accept would stream one. It
answers through `runs.answer_without_running` with one frame carrying the
resolution, and it skips `require_inference("continuation")`, so resolving an
Action without narration works on a store with no usable model. That is the
manual-check experience (`post_scene_check`) on the proposal path.

---

## 9. II-A: the resolution object

The resolution stored on the proposal at `resolving -> resolved`, and the
object narration and the UI read:

```json
{"kind": "action", "action": "strike", "action_label": "Strike",
 "actor": "characters:mara", "actor_label": "Mara",
 "targets": ["characters:seraphine"], "target_labels": ["Seraphine"],
 "check": "athletics", "check_label": "Athletics",
 "notation": "1d20 + 5 vs 14", "result": {"...": "dice.roll output"},
 "tier": "success", "difficulty": 14, "modifier": 0,
 "branch": "success", "values": {"damage": 3},
 "costs":   [{"ref": "characters:mara", "label": "Mara", "field": "spell_slots",
              "field_label": "Spell Slots", "op": "spend", "applied": -1, "before": 3, "after": 2}],
 "effects": [{"ref": "characters:seraphine", "label": "Seraphine", "field": "hp",
              "field_label": "Hit Points", "op": "add", "requested": -3, "applied": -3,
              "clamped": false, "before": 5, "after": 2}],
 "transaction": "mt-4be1...", "status": "committed",
 "rejection": null}
```

- The check fields sit at the top level, where a check resolution has them, so
  `checks.dice_segment` and the existing roll-template expressions read an
  action resolution's roll part unchanged. The roll-log label is
  `mechanics.lines.roll_label(res)`, `"{actor_label} — {action_label}"`, not
  `checks.roll_label` (which needs a `check_label` a no-roll Action lacks).
- A no-roll Action **omits** `result` and `notation` (rather than writing
  `null`), and has `check`, `tier`, `difficulty` and `modifier` `null`, and
  `branch: "resolved"`. An older build's `heal` tests `"result" in
  resolution` (`proposals.py:343-344`); a `null` there would send it into
  `format_check_roll` and fail every later send in the scene.
- `status` is the transaction's (10.3); a `rejected` resolution lists its
  `costs` (they landed, 7.4), an empty `effects` and a `rejection: {code,
  detail}`; a `stalled` one marks each effect `"landed": true|false` (11.5).
- The resolution is built at record time (10.2); the hand-off stamps `status`
  and the per-effect `landed` marks from the record as it then stands (11.4).
- Narration and UI never see an expression, only `requested`/`applied`
  integers and before/after values (the draft's "narration receives concrete
  effects, never unresolved formulas").

---

## 10. II-A: the transaction ledger

### 10.1 Layout

```
<campaign>/mechanics/transactions/open/<id>.json   # lifecycle unfinished
<campaign>/mechanics/transactions/done/<id>.json   # terminal
<campaign>/mechanics/transactions/head.json        # {"seq": n}
```

One small file per transaction, written whole through `atomic.write_text`.
Chosen over MII's single `mechanics_transactions.json` for three reasons:

1. **A status change rewrites one small file**, never the history. A crash
   mid-write damages at most the transaction being written.
2. **Two devices sharing a synced store produce two files** rather than two
   conflicting versions of one (`docs/store-guarantees.md`, "Conflicted
   copies"). That is about conflict files only. It is not a concurrency
   promise: two devices playing one campaign at once is unsupported
   ("Two devices through a synced folder: no"), and 11.3 says what recovery
   does about another device's records.
3. **"Is anything unfinished?" costs the size of `open/`**, which is almost
   always empty, rather than the campaign's age. Startup recovery, the revert
   guard and a Todo count can afford it (the chores module refuses any count
   proportional to library age, `store/chores.py:23-28`).

A transaction is **always written to `open/` first** and moved to `done/` as
the last step of its lifecycle, which includes the proposal handoff (11.1
step 8). "A transaction names this proposal and its lifecycle is unfinished" is
therefore always answerable from `open/` alone.

Readers that need more than one record (the History, the audit's
transaction lines, the cut warning, the reversal flag) do not open every file
in `done/`. Closing a transaction appends one line to
`<campaign>/mechanics/transactions/index.jsonl` through `atomic.append_line`:
`{id, seq, kind, status, committed, scene_identity, refs}`, where `refs` are
the sheets and fields it touched. Readers filter the index and open only the
records they need. The index is derived: a missing, torn or short index is
rebuilt from `done/` (a torn line is skipped, as every `append_line` reader
does), so it can be wrong only by being incomplete, and a rebuild fixes that.

`id` is `mt-<uuid4 hex>`, minted like a proposal id so that a rebuilt directory
can never re-mint an old one (`proposals.py:7-9`). `seq` orders the History: it
is `head.seq + 1`, read and rewritten under the campaign lock; a missing or
unreadable `head.json` is rebuilt as the highest `seq` found in `open/` and
`done/`. `seq` is an ordering, never an identity, so two devices minting the
same `seq` produce two transactions that sort by `(seq, created, id)`.

### 10.2 Record

```json
{"v": 1, "id": "mt-4be1...", "seq": 42, "kind": "action",
 "status": "prepared",
 "scene": "012--the-bridge", "scene_identity": "c8f2...",
 "proposal": "pr-77d0...", "post": 17,
 "device": "5e1a...",
 "module": {"id": "d20-basic", "stamp": "e3b0..."},
 "action": "strike", "actor": "characters:mara", "targets": ["characters:seraphine"],
 "params": {"difficulty": 14, "modifier": 0},
 "check": {"...": "the resolve_check output, or null"},
 "branch": "success", "values": {"damage": 3},
 "ops": [Op, ...], "units": [Unit, ...],
 "landed": [],
 "rejection": null, "conflicts": [], "orphaned": null,
 "resolution": {"...": "section 9, built once at prepare"},
 "selection": null,
 "undoes": null, "undone_by": null,
 "created": "2026-10-09T12:00:00Z", "committed": null}
```

`resolution` is built at prepare and stored, so the proposal handoff after a
crash writes exactly what the uninterrupted run would have written; nothing is
recomputed from the pack, which may have changed. The hand-off stamps only the
fields the record decides after prepare: `status` and each effect's `landed`
mark (9). `device` is `maintenance_reports.device_key(root)`, the per-device
key image collection already uses, so recovery can tell its own records from
another device's (11.3). The record holds ids,
labels already in the campaign, and integers. It holds no prose and is never
logged (26).

### 10.3 Statuses

| Status | Directory | Meaning |
|---|---|---|
| `prepared` | `open/` | Roll, branch and units recorded; units may be partly applied. A record whose outcome was rejected is `prepared` too, with `rejection` set and the costs as its only units |
| `committed` | `open/` then `done/` | Every unit landed and the outcome stood; in `open/` until the proposal hand-off is done |
| `rejected` | `open/` then `done/` | Every unit landed; the outcome could not be applied (7.5), so the units are the costs alone (7.4). The roll stands |
| `stalled` | `open/` | Recovery could not apply a unit: another writer changed it, or the module or sheet can no longer take it. `landed` and `conflicts` say which |
| `partial` | `done/` | A stalled transaction the player settled (11.5) |

`orphaned` is not a status but a note on a terminal record: the transaction's
units landed, but its scene or proposal was gone by the time it could be handed
off, so nothing was projected (11.3). `kind` is `action` or `undo` (12); II-B
adds `manual` (a hand edit of conditions or clocks routed through the ledger,
20.4).

---

## 11. II-A: roll, record, apply, commit, hand off

### 11.1 The sequence

All of it inside **one** `locks.campaign_lock(cid)` hold, in the adjudication
route (`_roll_proposal_run`), for a pending action proposal being accepted:

1. **Claim** `pending -> resolving` (`proposals.claim`). Read the claim
   snapshot: the actor's and each target's `SheetView` (7.6). Re-run
   `check_proposal` against it with the body's edits applied (6.5). Evaluate
   the costs and check payability (7.4). Any failure reverts `resolving ->
   pending` and answers `check_error` with the problem; **nothing was rolled**.
2. **Roll**, for a checked Action: `checks.resolve_check(cid, check, actor,
   difficulty, modifier)`. In memory only.
3. **Plan**: `effects.plan(...)` (7.6) selects the branch and produces ops and
   units. A rejected outcome is a plan with `rejection` set and the costs as
   its only units.
4. **Record.** Write `open/<id>.json`, status `prepared`, carrying the roll,
   the tier, the plan and the resolution. **This is the write that makes the
   roll exist.** Bump `head.seq`.
5. **Apply** each unit through the sheet unit writer (11.2), appending its
   index to `landed` (a rewrite of the open file) after each one lands.
6. **Commit**: rewrite the open file with status `committed`, or `rejected` when
   the plan carried a rejection, and the `committed` timestamp.
7. **Hand off**: `proposals.transition(resolving -> resolved, resolution)`, the
   resolution stamped with the record's status (10.2).
8. **Close**: write `done/<id>.json`, append the index line (10.1), then unlink
   `open/<id>.json`. Close comes **after** the hand-off and never before, so
   the revert guard (11.4) can see the record for as long as the proposal can
   still be `resolving`: there is no window in which an empty `open/` meets a
   `resolving` chip whose re-accept would roll a second time.
9. **Project**: `proposals.project` writes the roll entry (checked Actions
   only) and the transcript line (13), idempotently. A crash between 8 and 9
   leaves a `resolved` record whose resolution is projectable, which every
   existing heal path already projects.

Then, outside the lock, the continuation streams as today (unless `narrate:
false`, 8.4).

Nothing is written between steps 1 and 4 except the claim, so a crash in that
window leaves a `resolving` record and no transaction, and nobody ever saw the
roll. No route reverts such a record: the chip answers 409 "adjudication in
progress" until the next send supersedes it (Phase 4's behaviour, unchanged),
and a later proposal rolls fresh. **From step 4 on, the recorded roll is the
only roll.**

`revision.bump(cid)` runs in a `finally` from step 4 onward, for the reason
`proposals.project` gives (`proposals.py:278-301`): durable writes that do not
land together, under a request that may then answer non-2xx.

### 11.2 The unit and its compare-and-swap

```python
# store/sheets/writer.py
def apply_unit_locked(mid: str, cid: str, kind: str, eid: str, *,
                      sheet_type: str, gen: str | None,
                      before: dict, after: dict) -> Literal["applied", "already"]
```

Caller holds the campaign lock and resolved `mid` once (the `set_field_locked`
split). It reads the stored snapshot and, for every key in `before`, the
canonical live value (schema defaults merged, as `set_field_locked` does):

- stored `sheet_type` or `gen` differs from the unit's -> `SheetConflict("sheet
  replaced")`: the sheet was deleted and re-created, or retyped, since the
  plan, and its values belong to a different sheet;
- every touched key equals `after` -> `"already"` (a retry or a recovery of a
  unit that landed);
- every touched key equals `before` -> write `{**stored_fields, **after}`
  through `validate_sheet_values`, **preserving `gen` and the creation mark**,
  -> `"applied"`;
- anything else -> `SheetConflict("changed since the action was recorded")`.

A touched key the sheet type no longer defines, a field no longer `resource` or
`track`, or a module that no longer resolves raises `SheetError`; the callers
in 11.3 and 12 turn both exceptions into a stall or a 409, never a 500.
`apply_unit_locked` takes `cid` and writes under a lock it does not take
itself, like `set_field_locked` and `write_locked`, so it gets the same
treatment from `test_lock_domain_guard.py` as they do (the existing `_locked`
convention, or a reasoned marker under the cap).

This is the corrected form of MII's `expected_gen`/`result_gen` (1.9 item 1):
the values are the version; `gen` and `sheet_type` say whether it is still the
same sheet.

### 11.3 Recovery

The layering is fixed by the import guard. `store/mechanics/txn.py` owns the
ledger and the units and **never imports `proposals`**; `store/mechanics/resolve.py`
owns everything that touches a proposal and may import both:

```python
# store/mechanics/txn.py  (no proposals import)
def open_for_proposal(cid: str, pid: str) -> dict | None
def complete(cid: str, tid: str) -> dict
    """Apply every unit not yet landed, then commit (or reject, or stall).
    Returns the resolution to hand off, stamped. Never closes. Never raises on
    store content: SheetConflict, SheetError, a missing module or a failed
    validation becomes `stalled` with a conflict code."""
def close(cid: str, tid: str) -> None
    """Write done/, append the index line, unlink open/. Idempotent."""

# store/mechanics/resolve.py  (imports proposals and txn)
def finish(cid: str, tid: str, *, may_project: bool = True) -> FinishResult
    """complete -> fence -> hand off -> close -> project, in that order."""
def recover(cid: str, *, may_project: Callable[[str], bool] = lambda sid: True) -> list[FinishResult]
```

`finish` on one open record, under the campaign lock:

1. **Another device's record**: if `record.device` is not this device's key and
   the record is younger than `FOREIGN_GRACE = 10 minutes`, do nothing and
   report it as `stalled_elsewhere` in the History. Its writes may not have
   synced yet, and acting now could re-apply units and append a second line.
   After the grace period it is recovered like any other. (Two devices playing
   one campaign at once is unsupported; this only keeps a sync lag from
   becoming a duplicate. The constant is structural, longer than any
   adjudication's lock hold, and will be tuned later.)
2. **`txn.complete`**: a `prepared` record applies its remaining units (a unit
   that landed after the last `landed` write answers `"already"`). All land:
   `committed`, or `rejected` for a rejected outcome. Any unit that cannot
   land: `stalled` (11.5). A record already `committed` or `rejected` returns
   its resolution unchanged.
3. **The scene fence.** Before any hand-off or projection, require
   `scenes.identity.scene_identity(cid, record.scene) ==
   record.scene_identity` (resolving the sid through its current id if a
   rename moved it, 16) **and** that the scene's proposal record carries
   `record.proposal`. If either fails, the scene or proposal this transaction
   belonged to is gone: a deleted scene whose sid has since been reused, a
   proposal superseded by an older build that knows nothing of transactions.
   The record is closed with `orphaned: {code}` (its units have landed, the
   roll is kept in the record), **nothing is projected**, and a log line
   carries the campaign id, the transaction id and the code. This is the
   identity fence the rest of the app applies to work that outlives its
   request (CLAUDE.md, "Detached runs"); without it, recovery would append
   "Mara — Strike → Seraphine" into a stranger's transcript.
4. **Hand off**: if the proposal is `resolving`, `transition(resolving ->
   resolved, resolution)`; if it already holds this resolution (`resolved`,
   `narrated` or `superseded`), nothing.
5. **Close** (`txn.close`), and only now (11.1 step 8).
6. **Project** (`proposals.project`) when `may_project(sid)`; otherwise leave
   it, since a `resolved` record with a projectable resolution is projected by
   the next heal or adjudication.

A `stalled` record is handed off with `status: "stalled"` (so the proposal can
leave `resolving`) and stays in `open/` until settled (11.5).

**Recovery never calls `resolve_check`.** The roll, tier and units come from
the record. A store test asserts it with a resolver fake that fails if called.

Where recovery runs, and whether it may project:

- at startup, as one more step of `main._lifespan`'s guarded startup loop,
  beside `module_edit.recover` (`main.py:307-314`), and again after a data-dir
  move, for every campaign whose `open/` is non-empty (the directory check is
  the whole cost for every other campaign), skipped with a warning on
  `StoreBusy` like its neighbours. No run holds a scene at startup, so it may
  project;
- at the head of the adjudication route and inside `proposals.heal` (11.4),
  which run in the scene's own turn or are themselves the scene change, so
  they may project;
- at the head of every other route that writes campaign mechanics state: undo
  and settle, every sheet write (`PUT/DELETE .../sheets/...`, creation,
  advancement, bulk create), the absorb save that applies audit deltas, and the
  module rebind routes (`PUT /campaigns/{cid}/module` and the world-module
  rebind). These apply and commit units, but project only into a scene that
  `runs.require_scene_free` would let them change; a scene held by a detached
  turn is left for its own heal, since a shape change under a held scene is
  what CLAUDE.md's scene-freeze rule forbids;
- inside the module-edit `pre_swap`, the module delete and the module import
  (which can stop a user pack shadowing a built-in), for every campaign bound to
  the module, with no projection (17);
- in the scene-delete route, for records naming the scene being deleted, before
  the delete, so they close as `orphaned` at once rather than at the next
  startup.

A sheet write therefore always finishes an interrupted Action before it
writes, so a stall needs a crash *and* a foreign write (another device, a hand
edit) before the next request. Recovery bumps the revision for every campaign
where it wrote anything, since no request covers a startup pass (the
module-edit migration's precedent, `module_edit/migrate.py:368-391`).

# store/mechanics/txn.py
def recover(cid: str) -> list[RecoveryResult]       # every record in open/
def complete(cid: str, tid: str) -> dict             # one record; returns the resolution to hand off
```

For each open record, under the campaign lock:

| Record | Recovery |
|---|---|
| `prepared` | Apply each unit not in `landed` through `apply_unit_locked` (which also absorbs a unit that landed after the last `landed` write: it answers `"already"`). All land -> step 6 onward. A `SheetConflict` -> `stalled` (11.5) |
| `committed` or `rejected` | Steps 7-9: if the proposal still carries this id and is `resolving`, transition it with the stored resolution; if it is `resolved` or `superseded` with this resolution, nothing; then close and project |
| `stalled` | Nothing until settled (11.5) |
| Present in both `open/` and `done/` | A crash inside step 8: check the hand-off as above, then unlink the open copy |

**Recovery never calls `resolve_check`.** The roll, tier and units come from
the record. A store test asserts it with a resolver fake that fails if called.

Where recovery runs:

- at startup, as one more step of `main._lifespan`'s guarded startup loop,
  beside `module_edit.recover` (`main.py:307-314`), for every campaign whose
  `open/` is non-empty (the directory check is the whole cost for every other
  campaign), skipped with a warning on `StoreBusy` like its neighbours;
- at the head of every mechanics route that resolves, undoes or settles, and
  of every route that writes a sheet (`PUT/DELETE .../sheets/...`, creation,
  advancement, bulk create) and of the absorb save that applies audit deltas.
  A sheet write therefore always finishes an interrupted Action before it
  writes, so a stall needs a crash *and* an external writer (another device, a
  hand edit) before the next request;
- inside `proposals.heal` (11.4), so a supersede never retires a proposal
  whose transaction is unfinished;
- inside the module-edit `pre_swap` for every campaign bound to the module
  being edited (section 17).

Recovery bumps the revision for every campaign where it wrote anything, since
no request covers a startup pass (the module-edit migration's precedent,
`module_edit/migrate.py:368-391`).

### 11.4 Changes to `proposals.py`

The state machine gains no states and no edges. It gains two rules, both
checked under the lock it already takes:

1. **The revert is refused for a proposal an open transaction names.**
   `transition` refuses `resolving -> pending` (returns `False`) when
   `txn.open_for_proposal(cid, pid)` finds a record. Taking that edge would hand
   back a chip whose re-accept rolls again (MII 13.1, kept). Because a record
   leaves `open/` only after its hand-off (11.1 step 8), the guard holds for
   the whole time the chip can be `resolving`.
2. **`heal` completes an open transaction before it retires a record.** For a
   `resolving` record named by an open transaction, `heal` runs, in this order:
   `txn.complete`, the scene fence of 11.3 step 3 (the record is the scene's
   own, since `heal` is called with this scene's record in hand),
   `transition(resolving -> resolved, resolution)`, `txn.close`, then
   `project` as it does today. `heal` lives in `proposals` and calls only
   `txn`, so the import direction holds. Its projectability test widens from
   `"result" in resolution` to `projectable(resolution)`, which is also true
   for an action resolution with a `transaction` and no roll (a no-roll Action
   still owes its transcript line). `txn.complete` never raises on store
   content (11.3), so a rebound or broken module stalls the transaction rather
   than making every later send in the scene answer 500.

`project` branches on `resolution.get("kind") == "action"`: the roll entry is
appended only when there is a `result` (label `"{actor_label} — {action_label}"`,
proposal tag `pid`, tier as today), and the line is
`mechanics.lines.format_line(res)` (13), which formats a check resolution with
`checks.format_check_roll` and an action resolution with the action format.
Every other place that rebuilds a roll line to match it uses `format_line` too:
`_paused_response_round`'s crash-window match (`routes/mechanics.py:194`),
which would otherwise raise on a no-roll resolution and never match a checked
one. `update_resolution`'s `result` guard is unchanged.

The adjudication route, finding a record `resolving`:

- named by an open transaction -> `resolve.finish` (finish, not refuse), then
  continue to the continuation as an accept would. This replaces the 409
  "adjudication in progress" for that case only;
- named by no open transaction -> 409 "adjudication in progress", as today.

Import direction: `proposals` imports `mechanics.txn`; `mechanics.txn` imports
`sheets`, `atomic`, `locks`, `revision`, `campaigns.paths` and
`scenes.identity`, never `proposals`; `mechanics.resolve` imports both;
`module_edit`, `routes` and `main` reach recovery through `mechanics.resolve`.
The graph stays acyclic (`test_import_guard.py`).

### 11.5 Stalls and settling

A stall is the only state where formal history and sheets disagree, and it
requires a crash plus a foreign write before recovery (11.3). The engine never
resolves it by writing over the foreign value:

- the record moves to `stalled` with `conflicts: [{unit, code, live, before,
  after}]`, where `code` is `changed`, `sheet_replaced`, `field_gone` or
  `no_module`; `heal` and the adjudication route hand off a resolution with
  `status: "stalled"` and each effect marked `landed` (9), and project its line,
  so the roll stands and the proposal can leave `resolving`;
- the History shows it with one action, **Settle**: `POST
  .../mechanics/transactions/{tid}/settle` moves it to `partial` in `done/`.
  What landed stays landed; what did not stays unapplied; the player edits a
  sheet by hand if they want more;
- a Todo chore, `mechanics_stalled`, counts stalled records across campaigns
  (cheap, because it reads only `open/`), so a stall is not discoverable only by
  opening the History.

### 11.6 Idempotency, case by case

| Case | Outcome |
|---|---|
| Double accept (two clients) | `reserve_turn` already adopts the second into the first's run (`routes/mechanics.py:151-160`); one claim, one roll, one transaction |
| Lost response, then retry with the same attempt id | The run record replays; or, after it expired, the record is `resolved`/`narrated` and the route takes the existing paths. One transaction |
| Crash after claim, before record | `resolving`, no transaction; the route answers 409 until the next send supersedes it; nothing was rolled that anyone saw |
| Crash after record, before first unit | Adjudication retry, heal or startup: recovery applies every unit once with the recorded roll |
| Crash after some units | Recovery: landed units answer `"already"`, the rest apply |
| Crash after commit, before hand-off | Recovery: fence, hand off the stored resolution, close, project |
| Crash after hand-off, before close | Recovery: the proposal already holds the resolution; close and project |
| Crash after close, before project | `resolved` with a projectable resolution; the next heal or adjudication projects it |
| Rejected outcome after the roll | `rejected` transaction with the roll and the costs applied; the proposal resolves carrying the rejection; re-accepting is impossible (the record is `resolved`) |
| Supersede (a new send) while `resolving` with an open transaction | `heal` completes first; the roll is projected as history; no continuation (Phase 4's superseded rule) |
| Proposal superseded before resolution | `pending` was never claimed: no roll, no transaction, no effects |
| Scene deleted, sid reused, before recovery | The scene fence fails: units land, the record closes `orphaned`, nothing is projected into the new scene |
| Foreign sheet write between crash and recovery | `stalled`; never overwritten |
| Another device's open record, within the grace period | Left alone, shown as `stalled_elsewhere` |
| Module edited, rebound, deleted or re-imported between crash and recovery | That route runs recovery first and refuses while a stalled transaction remains in a bound campaign (11.3, 17); a module that changed anyway (a hand edit of the pack) stalls the transaction with `field_gone` or `no_module` rather than raising |
| Module edited between proposal and accept | Accept re-runs `check_proposal` against the live pack inside the lock (11.1 step 1); a proposal the new pack makes illegal is refused before any roll |

---

## 12. II-A: undo

```
POST /campaigns/{cid}/mechanics/transactions/{tid}/undo
```

- Only a `committed`, `rejected` or `partial` transaction with no `undone_by`
  may be undone (`409 not_undoable` otherwise, with the reason). For a
  `rejected` one this reverses its costs.
- **The whole undo is checked before anything is written.** Every unit of the
  reversal is first compared, dry, against the live sheet with 11.2's rules
  (the original's `after` must still be what is there, under the same
  `sheet_type` and `gen`). Any mismatch refuses the whole undo with
  `409 undo_conflict`, naming the field; nothing is recorded and nothing
  moves. Only then is the undo recorded and applied, all inside the same lock
  hold, so no writer can move a field between the dry check and the write.
- **A module change since the original refuses the undo.** If the module
  stamp's sheet schema differs from the live one (a field, sheet-type or
  content rename re-mints `gen` and may rename keys, 1.2), the undo answers
  `409 not_undoable` ("the module changed since this action"). Any
  `SheetError` from a unit maps to the same 409, never a 500.
- Undo is **a new transaction**, `kind: "undo"`, `undoes: tid`, whose units
  swap each landed unit's `before` and `after`. It runs 11.1 steps 4-6 and 8
  (there is no roll, no proposal and no hand-off) and then rewrites the
  original's `done` record with `undone_by`. Recovery completes that last write
  if a crash interrupts it, since the undo's own record names `undoes`.
- It carries **no scene**: `scene` and `scene_identity` are empty. An undo is
  made from the History, not from a scene, and the audit finds it by time and
  by the sheets it touched, not by scene (15).
- Undoing an undo is a redo, by the same rule.
- Undo appends **no transcript line** and touches no roll entry or proposal:
  the roll happened and the prose said what it said. The History shows the
  pair, and the audit lists both transactions (15).

**The change journal is not involved.** `undo.py` declines `sheet` because the
audit owns its own conflict contract (`undo.py:140-142`); a mechanics
transaction owns one too, and a second, weaker copy of it in the journal would
be two ways to reverse one write. This settles MII 14's "if the journal can
represent it" with a no.

---

## 13. II-A: narration and the transcript line

### 13.1 The line

Projected with `ROLL_SPEAKER`, the speaker every mechanical line already uses,
so `trim_continuation`'s retention rule, absorb's routing, branching and the
synthetic-speaker exclusions all treat it as they treat a roll today
(`scenes/serialize.py:30`, `160`; `scenes/write.py:403-411`). A new synthetic
speaker would have to be taught to each of those readers for no gain.

```text
🎲 **Mara — Strike → Seraphine (diff 14):** [12] + 5 = **17** vs 14 — **success** · *success* · Seraphine Hit Points 5→2
⚙ **Mara — Mend → Winifred:** Mara Spell Slots 3→2 · Winifred Hit Points 4→6
```

A checked Action's line contains its roll-log label and its dice segment, which
is what `branch.match_rolls` needs to copy its roll to a sibling
(`branch.py:98-105`). A no-roll Action's line has no roll entry, and branching
copies the line with no entry, which is correct: there was no roll.

A rejected outcome's line ends `· *effects not applied*`; a stalled one marks
each unlanded effect `(not applied)`.

### 13.2 The continuation

`scene/action_result.j2`, appended exactly as `roll_result.j2` is, with the
resolution (9), the `on_roll` rules, the check's rules and the Action's rules,
and the resulting value of every touched field. Its instruction (MII 15,
kept):

> The action below has already been resolved and its effects are recorded.
> Narrate this result. Do not reverse, add to, or ignore any listed effect; a
> further formal change needs another action.

A rejected outcome renders `scene/action_rejected.j2`: the roll and why its
effects could not be applied, and an instruction to narrate the attempt without
inventing a mechanical consequence; the costs it lists did land (7.4). A
paused character round resumes with `action_result.j2` (or
`action_rejected.j2`) as `resume_roll`'s appended block, in place of
`roll_result.j2`. A declined Action reuses
`scene/roll_declined.j2` with an `action_label` variable added.

`verify_templates.py` and the template README gain both templates.

---

## 14. II-A: prompt context

`mechanics_response_format.j2` gains an Actions block, rendered **only when at
least one actor in the pool has an available Action**:

```text
# Actions
To have a present character attempt one of these actions, emit the same fenced
block with "action" instead of "check", naming the actor and any targets, then
STOP. The engine checks the action is allowed, rolls, and applies its effects;
you will be told what happened.

characters:mara (Mara): strike (Strike: attack someone within reach; 1 target; check athletics), mend (Mend: ...; 1 target; costs spell_slots 1)
```

- **Byte identity.** A pack with no `actions.json`, or one whose Actions are
  unavailable to everyone present, renders the section exactly as today. This
  is the property `test_lore_golden.py` holds for lore and the frozen campaign
  holds for whole prompts, and it is what lets II-A merge without moving a
  prompt anybody is playing with.
- **Only available Actions**, without reasons. Offering the play model an
  Action it may not take invites a proposal that will open in Modify.
- **Bounded**: at most `MAX_PROMPT_ACTIONS = 12` per actor and
  `MAX_PROMPT_ACTIONS_TOTAL = 48` across the section, in pool order then file
  order, descriptions cut to one line. The total cap matters because the
  section is `LOCK_IN` and is never shed (`store/context/pack.py`); a cast of
  many sheeted actors must not grow it without limit. The figure is structural (a palette-sized list,
  well inside one section's LOCK_IN budget) and will be tuned against real
  prompts later.
- Effect maps, values and branch tables are never in the turn prompt. They
  reach the model only in the continuation, as concrete results (13.2), which
  is MII 22's tiering.
- The offline eval suite requires the roll-protocol section verbatim (CLAUDE.md,
  "After editing anything in `templates/`"); its `roll-fence` case gains an
  `action-fence` sibling that checks a closed, parseable fence naming an Action
  and targets that exist.

---

## 15. II-A: audit

The audit stays the exception path (MII 4.7, 21). Sheets are campaign state,
while the audit reads one scene against the baseline taken when that scene was
created (`audit/baselines.py:65-70`). So a transaction from another scene, or
an undo from the History, can move a sheet between this scene's baseline and
its absorb. The audit therefore reads transactions **by time and by sheet, not
by scene**:

1. **The prompt lists every transaction that moved an in-scope sheet since
   the scene began.** `audit/prompt.py` gains `transaction_lines(cid, sid)`:
   every committed, rejected, partial or undo transaction (any scene, undo
   included) committed after the scene's `created` time whose `refs` touch a
   sheet in `sheet_scope(cid, sid)`, read from the index (10.1), in `seq` order.
   It renders after the roll log in `audit/user.j2`: `- Mara — Strike →
   Seraphine: success · Seraphine hp 5→2 (mt-4be1, this scene)` or `(... ,
   another scene)` or `(undo of mt-4be1)`. `audit/system.j2` says a field
   change explained by a listed transaction needs no delta; narration that
   contradicts a transaction, or a transaction this scene's prose never
   mentions, is a warning, not a delta.
2. **A flag, not a drop, on a delta that would reverse one.** In
   `audit.apply.materialize`, a delta that would put a field back to the
   `before` of the latest listed transaction touching it is still staged, but
   **unselected and flagged** with the reason "would reverse a recorded action
   (mt-4be1); undo it from the mechanics History, or accept this if the
   fiction really changed it back". The audit, seeing prose that skipped a
   formal effect, is tempted to "fix" the sheet to match; the formal state is
   the default authority. A silent drop would be wrong the other way: hp 5→2
   by a `strike` and then narrated healing back to 5 is a real change the
   player must be able to accept.

The four classes (explained, unformalized, contradiction, unacknowledged) are
then: no delta; a delta (as today); a warning; a warning. The output schema
(`warnings`, `sheet_deltas`) does not change; the staged edit gains a `flag`.

---

## 16. II-A: tracker, branches, cuts, forks, renames

- **Tracker.** Formal state first, narrative after (MII 20, kept). Nothing
  writes mechanics into the tracker or reads the tracker into mechanics. The
  continuation post schedules `tracker-update` like any post, and the tracker
  may describe a wound in words after an Action lowered `hp`. A typed Effect
  that writes a tracker field needs its own design.
- **Cuts and retcons** do not reverse transactions, as they do not reverse
  rolls today. The History lists what was applied, and Undo is the reversal.
  A cut past an Action's line leaves the transaction and its effect standing;
  the cut confirmation should say so when the cut range contains one
  (a client read of `GET .../mechanics/transactions?scene=`).
- **Branches** copy no transactions. Sheets are campaign state and are not
  forked by a branch, so a transaction already describes the only sheets there
  are. The sibling's copied line keeps its roll entry (13.1). The audit of a
  sibling reads transactions by the sibling's identity *and* its `branch_of`
  identity for lines at or before the branch point; see Open question 7.
- **Forks** copy the campaign tree, transactions included, with their ids;
  nothing in a transaction names the campaign.
- **Scene renames**: `mechanics.txn` joins `scene_refs.repoint` (its
  `repoint_scenes` rewrites `scene` in every record whose `scene` is mapped;
  `scene_identity` is the stable key and never moves). So does `proposals`,
  which is keyed by sid and today is in no fan-out
  (`store/scene_refs.py:1-40`): its `repoint_scenes` moves a record to its
  scene's new key, so recovery's fence (11.3 step 3) finds the proposal after a
  rename instead of orphaning the hand-off. The module docstring's count of
  stores grows by two.
- **Scene deletes** leave the `proposals.json` key behind
  (`store/scenes/lifecycle.py:210`, which drops other sidecars but not that
  one), and a later scene can reuse the sid. Recovery's identity fence (11.3)
  is what keeps an interrupted Action from projecting into that scene, and the
  delete route closes such records first (11.3).
- **Reclassify**: `mechanics.txn` joins `record_refs.repoint` (actor, targets,
  ops and units carry `<kind>:<id>`), so a recovery or an undo resolves to the
  file the sheet moved to.

---

## 17. II-A: module authoring

- `module_edit/edits.py` gains `upsert_action(mid, aid, action)` and
  `delete_action(mid, aid)`, each a `mutate` closure through `_apply`, so they
  are staged, validated by the same `load_pack_at` and published under every
  campaign lock like every other section writer.
- **Rename fan-out** (`module_edit/renaming.py`): a check rename rewrites
  `actions.*.check`; a field rename rewrites effect `field`s and expressions in
  `amount`, `value` and `values` (`_rewrite_exprs`); a sheet-type rename rewrites
  `sheet_types` and `targets.sheet_types`; a rule rename rewrites `rules`; an
  Action rename is a new `_RENAME_KINDS` entry.
- **Recovery first, at every door that can change what a bound campaign's
  open transactions resolve against**: a module edit's `pre_swap`, module
  delete, module import (which can stop a user pack shadowing a built-in), and
  the campaign and world module rebinds. Each runs `resolve.recover` for the
  affected campaigns without projecting, and refuses with 409
  `mechanics_open` while a stalled record remains.
- **Guards**, in the style of `check_proposal_guard`:
  `action_proposal_guard(mid, aid)` refuses an Action rename or delete while a
  non-terminal action proposal names it in a bound campaign; and
  `open_transactions_guard(mid)` runs `resolve.recover` for every campaign bound to
  `mid` (the edit already holds every campaign lock) and refuses any edit while
  a stalled transaction remains, because a field rename would strand its units.
- The impact report gains `actions_newly_invalid` and `actions_dangling`
  (Actions whose check, rule or field a staged edit removes).
- Export and import carry `actions.json` because they carry the pack directory;
  import validates through `load_pack_at`, so an imported pack with a bad
  Action is refused like any other invalid pack.
- **Built-in packs are not changed in II-A.** The frozen campaign fixture binds
  a built-in pack, so adding Actions to it would move its sweep snapshot; II-A
  tests use a fixture pack, and shipping Actions in a built-in is its own
  reviewed change (Open question 6).

---

## 18. II-A: API

| Route | Purpose | Notes |
|---|---|---|
| `GET /campaigns/{cid}/scenes/{sid}/actions[?actor=ref]` | `available_actions` | Read-only |
| `POST /campaigns/{cid}/scenes/{sid}/actions/preview` | `check_proposal` plus the costs and the possible branches for a concrete selection, with no roll | `@computes_only` |
| `POST /campaigns/{cid}/scenes/{sid}/action-proposal` | Create a player Action (8.3) | Lock, scene-free and scene-open checks |
| `POST /campaigns/{cid}/scenes/{sid}/roll-proposal` | Adjudicate (existing route, body per 8.4) | The one resolution path |
| `GET /campaigns/{cid}/mechanics/transactions[?scene=sid]` | History, `seq` descending | Reads `open/` and `done/` |
| `GET /campaigns/{cid}/mechanics/transactions/{tid}` | One record | |
| `POST /campaigns/{cid}/mechanics/transactions/{tid}/undo` | Section 12 | 409 `undo_conflict` / `not_undoable` |
| `POST /campaigns/{cid}/mechanics/transactions/{tid}/settle` | Section 11.5 | Only `stalled` |

No new detached handler: resolution runs in the existing `continuation` turn of
`post_roll_proposal`, and every other route is synchronous and short. The
count of detached handlers in CLAUDE.md does not change.

---

## 19. II-A: UI

- **Proposal card**: `RollProposal.tsx` renders `kind: "action"` with the
  Action, actor, targets (each a select over eligible targets), check and
  adjustable parameters, costs, and the Action's rule summary. Accept, Modify,
  Decline as today; the same idempotent request.
- **Action palette**: beside the dice and check controls in the play view.
  Actor, then Action (grouped by tag, unavailable ones disabled with their
  reason), then targets, then parameters, then a preview (`/actions/preview`)
  of costs and possible outcomes, then **Resolve** (creates and accepts) or
  **Resolve & narrate**. A key binding, if any, goes through `useHotkeys` with
  a `label` and `group`, mirroring the disabled state of the button.
- **Resolution card**: the dice, the tier, the costs and effects as before→after,
  clamped effects marked, and a link to the transaction.
- **History**: a `ColumnSection` in `SheetsView` (the screen-owning page,
  per CLAUDE.md's list/detail rule), listing transactions; the detail in main is
  read-only by default with **Undo** and, for a stalled one, **Settle** in the
  sidebar. No new rail row: Sheets already appears exactly where a module is
  bound (`frontend/src/shell/rail.ts:255-259`).
- **Mechanics status** (the draft's 26, MII 23.4): not a new surface in II-A.
  The play view's existing sheet panel already shows each present actor's
  resources; II-B adds conditions to it and II-C adds active clocks, each in
  the slice that creates that state.
- **Authoring**: an Actions section in the module editor, following
  `EntityEditor`'s pattern: an Effects builder (op, selector, field picked from
  the declared sheet types' mutable fields, amount with inline expression
  validation), costs, branches from the check's reachable tiers, and values.

---

## 20. II-B: conditions (13-C2a)

Named, persistent, formal statuses attached to a sheet-bearing record, with
deterministic check modifiers. Detailed enough here to keep II-A's shapes from
foreclosing it; the II-B plan refines.

### 20.1 Module file: `conditions.json`

```json
{"winded": {"label": "Winded", "description": "Short of breath.",
            "stacking": "count", "max_stacks": 3,
            "applies_to": {"kinds": ["characters", "pcs"]},
            "modifiers": [{"checks": ["athletics"], "tags": [], "amount": -1, "per_stack": true}],
            "rules": ["skill-checks"]}}
```

`stacking` is `none` (one instance; adding again is a recorded no-op) or
`count` (an integer with optional `max_stacks`; adding past it clamps and is
recorded). Modifiers name checks by id and/or by a new optional check field,
`tags`. Free text never decides a modifier (MII 16.3, kept). Validation adds:
unknown check or tag, non-integer amount, bad stacking, unknown rule.

### 20.2 Store and unit

`<campaign>/mechanics/conditions.json`: `{"<kind>:<id>": {"<condition>":
{"stacks": n, "source": "mt-...", "scene_identity": "...", "applied": iso}}}`.
Not in `character_state.md`, which is narrative (MII 16.1, kept). The unit of
application is this file, with `before`/`after` holding exactly the touched
`(ref, condition)` entries, so 11.2's compare-and-swap carries over unchanged:
the touched entries read `before`, `after`, or something else.

### 20.3 Ops

`condition_add {target, condition, stacks = 1}` and `condition_remove {target,
condition, stacks = "all" | expr}`, legal in outcome branches, and in costs
on the `actor` selector ("become winded to do this"), which widens 7.4's cost
list by this one op. Removing an absent condition is a recorded
no-op; it never rejects, since "clear the condition if present" is the common
intent.

### 20.4 Manual edits

A player adding or clearing a condition by hand goes through the ledger as a
`kind: "manual"` transaction (no roll, no proposal), so the audit, the History
and undo see it like any other formal change. `GET/PUT
/campaigns/{cid}/mechanics/conditions` are thin routes over that.

### 20.5 Effects on resolution and availability

- `checks.resolve_check` reads the actor's active conditions and reports
  `base_modifier`, `condition_modifiers: [{condition, stacks, amount}]` and
  `modifier` (the final value, so every existing reader of `modifier` keeps
  working). `roll_result.j2` and `action_result.j2` show the breakdown. Same
  inputs give the same modifier: the conditions file is read under the lock that
  covers the roll.
- An Action may declare `requires_conditions` / `forbids_conditions` for the
  actor, a new availability reason `condition`.
- Retries cannot double-apply: a condition unit lands once by 11.2.

### 20.6 Module edits

A condition rename rewrites every bound campaign's `conditions.json` (the
sheet-migration pattern, under every campaign lock, stamping each changed
campaign). A delete with live instances is refused unless the edit says to
drop them; an instance whose definition vanished another way reads as
`unknown` and applies no modifier, visibly.

## 21. II-C: clocks (13-C2b)

### 21.1 Store, templates, instances

`<campaign>/mechanics/clocks.json`: `{"<clock id>": {"label", "current",
"max", "status": "active"|"complete"|"archived", "template"?, "created": "mt-...",
"scene_identity"}}`. Separate from continuity plot threads (MII 17, kept):
a clock is formal state, a thread is narrative.

A pack may declare `clocks.json` templates `{id: {label, max, overflow:
"stop"|"reject", rules}}`; a campaign may also create an ad-hoc clock by hand (a
`manual` transaction). A template is never required.

### 21.2 Addressing a clock from an Action

`clock_advance {clock, amount}` / `clock_set {clock, value}` name a
**template** id. At proposal time the Action is offered with a `clock`
parameter whose choices are the active instances of that template; one instance
is chosen automatically, more than one makes `clock` a required proposal field
(a select on the card, and a fence key). Zero instances makes the Action
unavailable with reason `no_clock`. An Action never creates a clock as a side
effect in II-C (creation stays explicit).

### 21.3 Completion

`overflow: "stop"` caps at `max` and sets `status: "complete"`; `"reject"`
refuses the outcome like a `set` out of bounds. Reaching `max` records an event
`{"clock_complete": id}` on the transaction and in the resolution; the
continuation is told the clock completed and the template's rules are added. The
fictional consequence is the module's or the narrator's, never the engine's
(MII 17.3, kept).

### 21.4 Extended actions

An extended action is an Action whose success branch advances a clock, taken
again. No second state machine (the draft 20, MII 17.4, kept).

## 22. II-D: contests (13-C2c)

### 22.1 Definition

```json
"grapple": {"label": "Grapple", "targets": {"min": 1, "max": 1, "kinds": ["characters", "pcs"]},
            "contest": {"actor_check": "athletics", "target_check": "athletics",
                        "compare": "total", "tie": "target"},
            "outcomes": {"win": [...], "lose": [...]}}
```

`check` and `contest` are exclusive. `compare` is `total` or `successes`
(validated against both checks' `roll_shape`). `tie` is `actor`, `target` or
`tie`; with `tie`, a `tie` branch is legal. Branch keys are `win`, `lose`,
`tie`, `_otherwise`. A contest Action must have `targets.max == 1` (MII 18.5,
kept). Contest roll values join the effect scope as `actor_total` /
`target_total` (or `_successes`) and `margin` (actor minus target); these names
are reserved from II-D on.

### 22.2 Resolution and recording

Both rolls are `resolve_check` calls, each with its own side's conditions (II-B)
and its own `difficulty`/`modifier` (`adjustable` applies to the actor's side
only; the target's side takes its check's defaults). Both are recorded in the
transaction at 11.1 step 4, before anything applies, so a retry can never
re-roll one side. Projection appends two roll entries, tagged `pid` and
`pid#target`, so `find_or_append_by_proposal` stays idempotent for each. The
line shows both and the winner.

Target eligibility adds: the target's sheet type satisfies `target_check`'s
`requires`.

## 23. II-E: encounter structure (sketch; needs its own spec)

Optional, for modules that need turn order. Not started before II-A to II-D
work (MII 5 and 19, kept).

- Store: `<campaign>/mechanics/encounters/<eid>.json` with `participants`,
  `order`, `round`, `turn`, `status`.
- A module may declare per-turn reset effects (a `manual`-style transaction at
  each turn boundary) and an `initiative` check.
- Availability gains one gate, `not_your_turn`, active only while an encounter is
  running in the scene.
- Initiative is a check, its results are rolls, the order is data; nothing about
  it needs a new random primitive.

A game with no encounter keeps using Actions exactly as in II-A.

---

## 24. The NPC action seam (13-C3)

### 24.1 What it is

A way for a non-player character's turn to **choose a formal Action** before
its prose is written:

```text
legal_set(actor)                        13-C1a: deterministic code owns legality
   -> decide("npc-action", item)        a distribution over the legal options
   -> sample(distribution, seed)        01c-C2, recorded as 01c-C3
   -> re-check under the lock           still legal? (digest and check_proposal)
   -> action proposal, source "npc"     13-C1c: the ordinary path
   -> player accepts, modifies, or declines
   -> resolution and transaction        13-C1b
   -> the NPC's contribution narrates the committed result
```

The Decision model **may not** invent an Action, bypass legality, roll, write a
sheet, or redefine an outcome (the draft 27, kept). It can only put weight on
options the engine enumerated, and the engine re-checks the winner.

### 24.2 Where it runs

In a character round (`routes/character_turns.py`), when the next speaker is a
sheeted NPC in the actor pool, **before** that contribution is generated, and
only when the campaign's `npc_actions` setting is `propose` (default `off`; a
campaign frontmatter key, so II-A ships without it reachable). If the legal set
is empty the step is skipped silently.

When an Action is selected, the round pauses on its proposal through a
pre-generation variant of the existing pause (`_pause`,
`character_turns.py:844`), which today pauses only on a fence inside a
contribution; here no partial contribution is written; acceptance resumes
the round through `resume_roll` with `action_result.j2` as the appended block
(1.5), so the contribution narrates an Action that already happened. A decline
resumes with the declined block, and the NPC writes its turn without the
Action.

II-A ships no `auto` mode. Accepting on the NPC's behalf without the player is
a later decision, after the eval gate has data (Open question 8).

### 24.3 The question

Context is bounded and built off the event loop: the actor's name, sheet
summary (the `mechanics_sheets` line), active conditions (II-B, when present),
the scene's recent turns through `store.regex.view.view` (phase `prompt`, which
the regex guard requires of every LLM reader of transcript text), and the 02-C2
intent when there is one (24.6). The shape of the question follows the legal
set; option ids for pairs are 01e's `Pair.key`, spelled `head=>tail`
(`strike=>characters:seraphine`), whichever form carries them:

- **One step, single-target, at most 254 pairs.** One flat `Choice` over the
  legal pairs plus the reserved none (`allow_none=True`, "no formal action this
  turn"), which exists today (`decisions.py:189-196`, 255 options with none,
  `decisions.py:140`). Where 01e-C3b has landed the same question is a
  `Joint`, which adds the head-level readings (`head_marginal`, `head_first`);
  that is a convenience, so 01e-C3b is soft. An option's description is the
  Action's label and description and the target's name. One `draws.draw`,
  `purpose="action"`.
- **Two steps, past 254 options.** First a `Choice` of Action (ids are Action
  ids, plus none); then, for the drawn Action, a `Choice` of its eligible
  targets. Two decide calls, two draws from one seed with `purpose="action"`
  and `purpose=f"target:{action}"` (01c's `uniform(seed, purpose)` makes each
  replayable alone). This needs nothing from 01e.
- **Multi-target Actions (`targets.max > 1`)**: always two steps, the second a
  `MultiSelect` of targets (**01e-C3a, hard**). A `MultiSelect` answer has only
  marginals, which 01c-C4 never samples, so the target set is always taken
  from the answer (`basis: answer`, `sampled: false` in its record). A set
  whose size falls outside `targets.min..max` is no usable answer: the Action
  is dropped and the turn proceeds as prose. Until 01e-C3a lands, multi-target
  Actions are left out of the question, and the item records that they were.

01e's `Joint` is a single flattened choice that names **one** target, so it
cannot carry a multi-target Action and cannot exceed the choice cap; the two-step
forms are how 13 covers both, per the 01e decision recorded in the checklist.

### 24.4 Selection

Each step's answer goes through 01c's draw, unmodified: `draws.draw(question,
item_result, seed=seed, purpose=..., served=...)`, with `seed =
draws.new_seed()` minted **once per NPC decision occasion** (01c 6.1 rule 3),
shared by both steps. 13 does not restrict, renormalise or otherwise reshape
the reported distribution (01c 5.3, 7); the none option stays in it and a
drawn none means no Action. The `Draw`'s own `record` is the replay record.
Legality is enforced after the draw (24.5), never by editing the distribution.

- **`basis: "sampled"`**: the drawn key is the selection.
- **`basis: "answer"`** (no usable distribution, but a plain answer): act on
  the answer. Its record says `sampled: false`; it is not a draw and nothing is
  replayed from it, the recorded cross-spec decision in `ROADMAP-CHECKLIST.md`
  ("NPC answer with no distribution"), which 01c-C4 states.
- **`basis: "none"`**: abstention, refusal, none, unreadable, or an error: no
  Action. The NPC's turn proceeds as prose (01c-C4).
- **Errors degrade, never fail the round**: `DecideRequestError`, any
  `LLMError`, and the seam's `require_inference` refusals (409 `incapable`,
  `missing_key`) are logged with a code and the turn proceeds
  as prose. This is deliberately more forgiving than the speaker pick, which
  returns control only on `DecideRequestError` and lets an `LLMError`
  propagate (`character_turns.py:728-761`): a missing NPC Action loses a
  flourish, while a missing speaker leaves the round with no one to speak.

### 24.5 Re-check and record

The decide call is async and outside the lock; the world may move. Under the
campaign lock, before creating the proposal: recompute the legal set, compare
digests, and run `check_proposal` on the selection. A mismatch whose selection
is still legal proceeds; one that is no longer legal records
`npc_action: {selection, dropped: "no_longer_legal"}` on the round record and
proceeds as prose.

The replay record is stored where the outcome is (01c-C3), in that same hold:
on the proposal payload's `selection` and on the round record. It is copied
into the transaction (`selection`, 10.2) if the proposal is accepted. Those
are the right places because a **declined** NPC proposal has no transaction,
yet its draw must still be kept. 01c's 6.1 currently names "the
transaction-ledger entry that commits the Action" as 13's persistence point;
that sentence should read "the proposal payload and the round record, copied
into the transaction". This is flagged for the 01c owner (Open question 11).
Mechanical randomness stays separately reproducible through the roll's own
seed; choice and dice never share one.

### 24.6 The 02-C2 soft seam

When 02-C2 has produced a turn intent for this NPC (an evasion, a threat), it
is placed in the item's context and, where the Action declares `tags`, the
option descriptions carry them, so a model can connect "threaten" to an
`intimidate`-tagged Action. An intent **never filters** the legal set and never
names an Action. Without 02-C2 the item simply has no intent line.

02-C3's per-contribution turn plan carries an `extra` slot for 13-C3: when
that plan runs, the first step of the NPC Action question (24.3) rides in it as
one more question of the plan's item, rather than as a call of its own, and its
answer is handled exactly as 24.4 and 24.5 say. It is then routed, metered and
seeded as the plan is (24.7). Without 02-C3 the question is its own
`npc-action` call.

### 24.7 Route, metering, capture, eval gate

- A new route, `Route("npc_action", "NPC actions", ..., ("npc-action",), True,
  operation="decide", default_role="decision", legacy=routing.NO_LEGACY)`,
  added in the same change as its call site, which is
  `test_operation_guard.py`'s rule. `NO_LEGACY` because the route is born at
  format 2 with no legacy key to read; `legacy=""` would make it a legacy route
  and add a key to `LEGACY_ROUTES` and `CONFIG_KEYS` (the checklist's shared
  structure, added by whichever spec lands first). It appears on the Models
  page because that page reads `routing.ROUTES`
  (`store/inference/settings.py:305`).
- Metered by `decide`'s own meter, attributed `campaign`, `scene`, `post` and
  `round_id` (`inference.py:684-688`). When the question rides in 02-C3's turn
  plan instead (24.6), it is metered as `turn-plan` on the `turn_plan` route,
  under the plan's seed and meter, not as `npc-action`; only a second step
  (24.3) is then its own `npc-action` call. A native-only Decision model files
  native rows with no `modelled_usd`, per CLAUDE.md's native decision rule.
- Captured through 01b-C1 when it lands.
- **Eval gate** (`evals/run.py --gate`): a synthetic corpus of NPC situations
  with a module fixture, graded on (a) every selection is a member of the legal
  set (must be 100%, and is enforced by code anyway), (b) a hand-labelled
  plausible-option set per case, and (c) abstention where nothing fits. The
  `propose` setting stays hidden until the gate passes. 01a-C1 reports its cost
  and latency.

---

## 25. Contract

### 13-C1a: Actions and the legal set

- **Inputs**: a campaign whose bound pack is valid and may carry
  `actions.json` (5); a scene; optionally an actor ref.
- **Outputs**: `available_actions(cid, sid, actor_ref=None) ->
  list[ActorActions]`; `legal_set(cid, sid, actor_ref) -> LegalSet` with
  `options` and `digest`; `check_proposal(cid, sid, proposal) ->
  list[Problem]` (6).
- **Guarantees**: one implementation answers the palette, the prompt section,
  the fence parser, accept and the NPC seam. Deterministic for equal pack bytes,
  sheets, cast and location; ordered by Action file order then pool order. Reads
  under the campaign lock; writes nothing; never raises on store content.
- **Failure**: no module or an invalid pack -> empty results. Store problems
  are reason codes, not exceptions.

### 13-C1b: The Effect DSL and transaction ledger

- **Inputs**: a pack's effect templates (7); a claim snapshot; a check
  resolution or none.
- **Outputs**: `effects.plan(...) -> Plan` (pure; a rejected outcome is a
  `Plan` with `rejection` set);
  `txn.open_for_proposal/complete/close/settle/undo` (no `proposals` import),
  `resolve.finish/recover` (the proposal-facing half), and
  `sheets.writer.apply_unit_locked` (10-12).
- **Guarantees**: II-A ops are `set`, `add`, `spend`, `restore` on `resource`
  and `track` fields with the direction-preserving bounds of 7.2, and every
  value and amount within `MAX_EFFECT_MAGNITUDE`. A roll is durable in an
  `open/` record before any unit is written; costs land even when the outcome
  is rejected; recovery never rolls; a unit is
  applied at most once and only over its exact `before`; a value someone else
  wrote is never overwritten (stall, not overwrite); undo is a new transaction
  under the same compare-and-swap. Every transaction is in `open/` until its
  proposal handoff is complete, and nothing is handed off or projected unless
  the record's scene identity and proposal still match (otherwise `orphaned`).
  Revision bumped wherever a transaction writes.
- **Failure**: a precondition fails before the roll (nothing recorded); an
  outcome fails after it (`rejected`, roll and costs kept); a foreign write, a
  changed module or a missing module before recovery (`stalled`, settled by the
  player; never a raise out of `heal`); `StoreBusy` propagates as the
  409 every lock holder already answers.

### 13-C1c: Action proposals, narration and audit

- **Inputs**: an action fence, a player Action, or (13-C3) an NPC selection.
- **Outputs**: a `kind: "action"` proposal payload (8.1); the resolution (9);
  the transcript line and continuation block (13); the audit's transaction lines
  and reversal guard (15).
- **Guarantees**: one adjudication route resolves every source; records without
  `kind` are check proposals and behave exactly as before; the revert edge is
  refused while a transaction names the proposal; `heal` completes an open
  transaction before retiring a record; a pack without Actions produces
  byte-identical prompts.
- **Failure**: a malformed or illegal proposal opens in Modify with problems; it
  is never dropped and never resolved as written.

### 13-C2a: Conditions (II-B)

`conditions.json`, `<campaign>/mechanics/conditions.json`, ops
`condition_add`/`condition_remove` as a unit kind of 13-C1b, `manual`
transactions, modifier reporting from `resolve_check`, and availability gates
(20). Guarantee: a condition changes only through a transaction; no modifier is
applied twice through a retry; a dangling condition applies nothing, visibly.

### 13-C2b: Clocks (II-C)

`<campaign>/mechanics/clocks.json`, optional templates, ops
`clock_advance`/`clock_set`, completion events (21). Guarantee: a clock moves only
through a transaction; completion is reported, never acted on by the engine.

### 13-C2c: Contests (II-D)

`contest` Actions, both rolls recorded before any effect, two tagged roll
entries, one target (22). Guarantee: no retry re-rolls either side.

### 13-C3: The NPC action seam, sampled through 01c

- **Inputs**: a sheeted NPC about to take a turn in a round; the campaign's
  `npc_actions` setting; 13-C1a's legal set; 01c-C2/C3/C4 (`draws.draw`,
  `new_seed`); 01e-C3a for multi-target Actions; 01e-C3b optionally; 02-C2's
  intent and 02-C3's `extra` slot when present.
- **Outputs**: either nothing (the turn proceeds as prose) or an action proposal
  with `source: "npc"` and a replay record (24.5).
- **Guarantees**: the distribution is drawn from as reported, never reshaped;
  the selection is a member of the legal set re-checked under the lock; the
  replay record is persisted on the proposal payload and the round record, and
  copied into the transaction when one exists; abstention, refusal or failure never become an Action; no Action
  resolves without the player's accept.
- **Failure**: any decide failure or stale selection degrades to prose, logged,
  never a failed round.

---

## 26. Interaction with repo rules

- **Campaign lock.** `store.mechanics.txn` and `store.mechanics.resolve` join
  `locks.DOMAIN_MODULES` with their reasons (`test_lock_domain_guard.py`);
  `availability` and `effects` mutate nothing. Every Action touches one
  campaign, so no `hold_all`. The module-edit guards run under the locks the
  edit already holds.
- **Atomic writes and paths.** Every write goes through `atomic.write_text`;
  every path is built from `campaigns_paths.campaign_root(cid)`
  (`test_atomic_guard.py`, `test_paths_guard.py`). The `open/` to `done/`
  move is a write then an unlink, never a rename across a crash-unsafe step:
  either file present is a readable state (10.3).
- **Imports.** New package `store/mechanics/` (`availability`, `effects`,
  `txn`, `resolve`, `lines`), module-scope imports only, submodules bound as
  modules (`from ..sheets import writer as sheets_writer`), acyclic per 11.4
  (`test_import_guard.py`).
- **Revision.** Bumped in a `finally` from the first durable transaction write
  (11.1), by recovery wherever it wrote (11.3), and by undo and settle. The
  middleware still stamps the 2xx answers on top.
- **Scene freeze.** `POST .../action-proposal` joins `test_scene_freeze.py`.
  Adjudication already holds the scene through its turn reservation.
- **Detached runs.** No new detached handler (18); the CLAUDE.md count of
  thirty-two is unchanged. An NPC decision runs inside the character turn that
  is already detached.
- **Metering and the LLM.** II-A adds no LLM call: the continuation is the
  existing `continuation` task. 13-C3 adds one decide task on a decide route,
  through `operations.decide` (`test_operation_guard.py`, `test_routing_guard.py`).
- **Regex view.** The NPC item reads transcript text through `store.regex.view`
  (`test_regex_prompt_guard.py`).
- **Templates.** `action_result.j2`, `action_rejected.j2` and the Actions block
  are checked by `verify_templates.py`; the offline evals cover the roll
  protocol including Actions (14).
- **Frozen campaign.** No built-in pack changes in II-A (17), so the frozen
  campaign's sweep snapshot does not move. II-A's test that a pack without
  Actions renders byte-identical prompts is the stronger guarantee.
- **Privacy.** Transactions hold ids, labels already in the campaign, and
  integers; they live in the campaign tree and are never written to the log.
  `logs.record` lines from this subsystem (a stall, a recovery that wrote, an NPC
  decision dropped) carry the campaign id, the transaction id and a code, never
  values. Test fixtures use the placeholder names only.
- **Android and pydantic.** Pure Python, standard library; request models are
  plain `BaseModel` fields (`list[str] | None = None`, `bool = True`), dumped via
  `routes.common._dump`. No new dependency.
- **Frontend.** List/detail pattern and the History as a `ColumnSection`
  (19); bindings through `useHotkeys`; no `keydown` listener.
- **Codex gates.** The plan for II-A, and each of II-B to II-D and 13-C3, passes
  `/codex:adversarial-review` before implementation, per CLAUDE.md.

## 27. Tests and acceptance

### 27.1 Store tests (II-A)

- **Pack validation**, one case per error in 5.4, including reachable tiers
  with and without a ladder and with and without `vs`; a roll name the shape
  cannot produce; a value name colliding with a field or a `<key>_max`; a cost
  on a target; a cost naming `targets`, `difficulty`, `modifier` or a
  roll-dependent value; too many values, a chain too deep, an expression too
  long.
- **`reachable_tiers` and `roll_shape`** against both shipped packs' checks.
- **Availability**: each reason code; pool order; `self`; a target whose type
  lacks a touched field is ineligible; the legal set's order and digest are
  stable across runs and change when a sheet value that affects payability does.
- **Effects** (pure): each op on `resource` and `track` at, inside and past
  each bound; `add` clamp and reject; `spend` refusal; `restore` clamp; negative
  amounts for `spend`/`restore`; **a `before` already outside the bounds**
  (`add -2` on `-3/8` stays `-3`; `restore 1` on `11/8` stays `11`; `add -1`
  with `bounds: reject` on `11/8` is allowed to `10`); float-to-int;
  `ambiguous_name`; `amount_out_of_range` on a value chain that would grow past
  the magnitude cap; values in file order; two ops on one field fold into one unit; fan-out order; one failing
  expansion rejects all; a no-op unit is dropped.
- **Sheet unit writer**: `applied`, `already`, conflict on a changed value,
  conflict on a re-created sheet with a new `gen`, conflict on a type change;
  `gen` and the creation mark preserved.
- **Transactions and recovery**, each with a dice fake that fails if
  `resolve_check` is called during recovery:
  - crash (injected exception) after claim, before record: no transaction; the
    chip answers 409 until a send supersedes it; nothing was recorded;
  - after record, before the first unit: recovery applies all units once;
  - after the first of several units (multi-target): recovery finishes the rest
    once;
  - after commit, before hand-off; after hand-off, before close; after close,
    before project: each finished by recovery or the next heal with no second
    write to any sheet and no second roll. In particular, no ordering of
    `complete`, `transition`, `close` leaves an empty `open/` beside a
    `resolving` proposal (the revert guard would then hand back a re-rollable
    chip);
  - scene deleted and its sid reused before recovery: units land, the record
    closes `orphaned`, the new scene's transcript is unchanged; a renamed
    scene's record is handed off under its new id;
  - another device's open record inside the grace period is left alone;
  - `heal` with the module rebound, deleted or made invalid: the transaction
    stalls and the send proceeds (no 500);
  - a foreign sheet write before recovery: `stalled`, value untouched; settle
    moves it to `partial`;
  - the revert edge refused while an open transaction names the proposal;
  - `heal` (through `supersede` and `new`) completes an open transaction and
    projects its line before retiring the record;
  - rejected outcome: roll recorded, costs applied, record `resolved`, a second
    accept answers the existing stale/narrated paths and rolls nothing.
- **Undo**: succeeds on unchanged fields; 409 when any touched field moved,
  **with nothing written** (the dry check covers every unit before the record);
  409 `not_undoable` after a module rename; no 500 from a `SheetError`;
  redo of an undo; a crash between the undo's commit and marking the original
  is finished by recovery.
- **Fan-outs**: a scene rename repoints transactions; a reclassify repoints
  refs and a later undo writes the moved sheet.

### 27.2 Route and stream tests

- A model fence with `action` produces an action proposal; with problems, it
  opens in Modify; with both `check` and `action`, `check_and_action`.
- A player Action: create (400 with problems; 200 with a record; 409
  `round_open` while a character round is unfinished), accept with
  `narrate: false` (one frame, record `narrated`, line projected, works with no
  usable model), accept with
  `narrate: true` (continuation streams with `action_result.j2` appended).
- Double accept and a lost-response retry each produce one transaction and one
  line.
- A `resolving` record with an open transaction is finished by a retry, not
  refused with 409.
- `POST .../action-proposal` is refused while a turn holds the scene
  (`test_scene_freeze.py`).
- An Action rename is refused while an action proposal names it; any module edit
  is refused while a bound campaign has a stalled transaction.
- **Byte identity**: for each shipped pack and for a fixture pack whose Actions
  are all unavailable, the composed prompt equals the pre-II-A prompt.
- The audit stages, unselected and flagged, a delta that would reverse a
  listed transaction, and lists in its prompt every transaction since the
  scene began that touched an in-scope sheet, from any scene, undo included.
- A proposal superseded before accept has no effects; a module edit between
  proposal and accept that makes the proposal illegal refuses it before any
  roll (MII 32's two cases).
- An older build's view: an action payload's `check` is `null`; a no-roll
  resolution has no `result` key.

### 27.3 Frontend tests

The proposal card renders an action payload and edits targets; the palette
disables an unavailable Action with its reason and its key binding follows the
disabled state; the History lists transactions, opens one read-only, and shows
Undo / Settle in the sidebar (CLAUDE.md's list/detail test triple).

### 27.4 Acceptance: II-A is done when

1. A pack may define checked and no-roll Actions, validated at load (5).
2. Availability, the legal set and proposal checking are one implementation
   used by every surface (6).
3. `set`, `add`, `spend`, `restore` work on `resource` and `track` with the
   bounds of 7.2; costs are preconditions; fan-out is exact.
4. The play model proposes Actions through the roll fence; players create them
   from the palette; both resolve through the one adjudication route.
5. The roll is recorded before any effect; recovery is idempotent and never
   re-rolls; a foreign change stalls rather than being overwritten.
6. Undo reverses a transaction or refuses with the field that moved.
7. Narration receives concrete committed effects; the audit lists transactions
   and refuses to reverse one.
8. Packs without Actions, and every existing check proposal, behave exactly as
   before, prompts included.
9. The module editor creates, edits, renames and deletes Actions, with guards.
10. `make check` passes, and the four Codex gates have run.

II-B, II-C and II-D are done when their contract items (25) hold with the same
style of crash-injection tests over their unit kinds. 13-C3 is done when its
eval gate passes and its guarantees (25) are tested with a fake Decision client
returning: a distribution, an answer with no distribution, an abstention, a
refusal, an illegal option, and an error.

## 28. Non-goals

- A combat engine, initiative or action economy in II-A (II-E is a sketch).
- Executable module code, or any evaluator other than `store/expressions.py`.
- Effects on `number`, `dots`, `text` or `ref` fields; `ref_add`/`ref_remove`.
- Cross-sheet expressions beyond actor-scope `values`.
- Off-scene targets, area targeting, multi-target contests, reactions,
  triggered or nested Actions, durations in rounds, refundable costs,
  alternative costs, reroll currencies, damage-type taxonomies.
- Making every narrated deed an Action; the audit remains the exception path.
- Automatic NPC Action resolution (`auto`), and NPC actions outside a character
  round (factions, downtime).
- Writing formal mechanics into the scene tracker, or the tracker into
  mechanics.
- Reversing transactions on cut, retcon or branch.
- A journal entry per transaction.
- A Todo chore for gameplay state ("low hit points"); the only chore is a
  stalled transaction, which is app maintenance (MII 30, kept).

## 29. Open questions

1. **Ship Actions in the built-in packs?** Adding `actions.json` to the shipped
   packs changes the prompt for every campaign bound to them and moves the
   frozen campaign's snapshot. **Recommendation:** not in II-A; a separate
   reviewed change after II-A has been played with a fixture pack, regenerating
   the snapshot deliberately.
2. **`add` clamps by default.** MII preferred reject. **Recommendation:** clamp,
   recorded, for outcome effects (7.2), because a post-roll rejection erases the
   whole outcome; `bounds: "reject"` remains available. The clamp never moves a
   value against the sign of its op (7.2). Revisit if authors report surprise.
3. **Effects on `number`/`dots`.** Some systems drain attributes.
   **Recommendation:** no; II-B conditions express a temporary drain as a
   modifier, and a permanent one is advancement or a hand edit.
4. **Using an unsampled answer for an NPC.** *Closed.* Recorded as a
   cross-spec decision in `ROADMAP-CHECKLIST.md` ("NPC answer with no
   distribution"): 13 may act on a plain Choice answer recorded
   `sampled: false`; it is not a draw and nothing is replayed from it, which
   01c-C4 states (24.4).
5. **Should a cut that removes an Action's line offer to undo it?**
   **Recommendation:** not automatically; the cut dialog warns and links the
   History (16). Automatic reversal would make a transcript edit a mechanics
   edit.
6. **Undo appends no transcript line.** **Recommendation:** keep; the History
   and the audit carry it. A "note it in the scene" option can come later.
7. **Audit of a branched sibling.** Transactions are keyed by scene identity,
   and a sibling's pre-branch lines were played in its source.
   **Recommendation:** the sibling's audit reads its own transactions plus its
   `branch_of` source's transactions up to the branch point, so pre-branch
   effects read as explained.
8. **An `auto` NPC mode.** **Recommendation:** not until the 13-C3 eval gate has
   real results; then as a per-campaign opt-in with the same proposal record and
   an "auto-accepted" mark.
9. **Speaker eligibility from conditions.** 02's draft mentioned an
   incapacitated NPC being ineligible to speak. **Recommendation:** leave it to a
   02 revision after II-B lands; II-B can expose a `blocks_turn` flag on a
   condition without committing 02 to read it. This is a possible future edge,
   not a contract here.
10. **Per-transaction files vs one file.** **Recommendation:** per-file with
    `open/`/`done/` (10.1). If History listing over `done/` grows slow, 03-C1's
    cache can key a projection over the directory; nothing here depends on it.
    The audit and the other multi-record readers already use the derived
    `index.jsonl` (10.1).
11. **01c's persistence sentence for 13.** 01c 6.1 says 13 persists the replay
    record in "the transaction-ledger entry that commits the Action", but a
    declined NPC proposal has no transaction. **Recommendation:** 01c's
    sentence becomes "the proposal payload and the round record, copied into
    the transaction" (24.5). This is a request to the 01c owner, not a change
    13 can make.

## 30. Review record

**Substitute spec-gate review, 2026-10-10** (`reviews/13.md`: 5 blocking, 10
should-fix, 13 minor). Each finding was checked against the code at the
baseline. Disposition:

| Finding | Disposition |
|---|---|
| B1 clamp moves an out-of-bounds value the wrong way | Fixed: direction-preserving bounds, formulas and examples (7.2), tests (27.1) |
| B2 costs on a rejected outcome contradicted | Fixed: costs always land; a rejected record carries cost units only (7.4, 9, 10.3, 11.1, 11.6) |
| B3 recovery projects into a recycled or renamed scene | Fixed: identity and proposal fence, `orphaned` close, delete-route recovery, `proposals` joins the scene-rename fan-out (11.3, 16) |
| B4 unbounded chained values hang the lock | Fixed: value count, chain depth, expression length and magnitude caps (5.3, 5.4, 7.3) |
| B5 recovery layering vs import guard; close order | Fixed: `txn` (no proposals import) vs `resolve`; `complete` never closes; order complete -> fence -> hand off -> close -> project (11.1, 11.3, 11.4) |
| S1 `heal` can 500 every send | Fixed: `complete` never raises on store content; recovery at rebind, module delete and import (11.3, 11.4, 17) |
| S2 `narrate: false` leaves "narration owed" | Fixed: ends in `commit_narration(..., lambda: None)`; skips `require_inference` (8.4) |
| S3 player Action strands a paused round; line match breaks | Fixed: 409 `round_open`; `mechanics.lines.format_line` everywhere a line is rebuilt (8.3, 11.4, 13.2) |
| S4 undo half-applies; 1.2 wrong about `gen` | Fixed: dry check of every unit first; module-change 409; 1.2 corrected (1.2, 12) |
| S5 audit keyed by scene, sheets are campaign-wide | Fixed: by time and sheet; undo carries no scene; reversal is a flag, not a drop (12, 15) |
| S6 older builds break on action records | Fixed: no `result` on a no-roll resolution; `action_check` with `check: null` (8.1, 9) |
| S7 costs vs values timing | Fixed: costs may name only the actor's sheet and static values (5.4, 7.4) |
| S8 13-C3 contracts disagree with 01c and 01e | Fixed: two-step questions, `MultiSelect` (01e-C3a hard) for multi-target, `Joint` soft and keyed `head=>tail`, `draws.draw` unmodified, persistence point and turn-plan metering named, `incapable`/`missing_key` degrade; 01c's sentence raised as Open question 11 (Depends on, 24.3-24.7) |
| S9 recovery breaks the scene freeze; one-device assumption | Fixed: projection only where the scene is free; device stamp and grace period; 10.1 no longer reads as a concurrency promise (10.1, 10.2, 11.3) |
| S10 wrong citations | Fixed (`writer.py`, `binding.py`, `rolls.py`, `migrate.py`). They came from line numbers read off a multi-file listing |
| M1 `<key>_max` names | Fixed (5.3) |
| M2 ambiguous labels | Fixed: `target_ambiguous` / `actor_ambiguous` (8.2) |
| M3 regex path problem and `reaction` | Fixed (8.2) |
| M4 total prompt cap | Fixed: `MAX_PROMPT_ACTIONS_TOTAL` (14) |
| M5 `done/` scans | Fixed: derived `index.jsonl` (10.1) |
| M6 status panel dropped silently | Fixed: stated in 19 |
| M7 speaker-pick wording; `legacy` | Fixed: the difference is stated; `routing.NO_LEGACY` (24.4, 24.7) |
| M8 Modify cannot change the Action | Fixed: `action_id` override, re-checked (8.2, 8.4) |
| M9 resolution status at hand-off | Fixed: hand-off stamps `status` and `landed` (9, 10.2) |
| M10 "revert" of a crashed claim | Fixed: 409 until a send supersedes (11.1, 11.6, 27.1) |
| M11 `apply_unit_locked` and the lock-domain guard | Fixed: the `_locked` convention (11.2) |
| M12 recovery after a data-dir move | Fixed (11.3) |
| M13 MII 32's two missing cases | Fixed (11.6, 27.2) |

Cross-spec inputs folded in at the same time: 01e's settled `Joint` (one
target, `Pair.key` spelled `head=>tail`, soft for 13), and the checklist's
decision that 13 may act on a plain answer recorded `sampled: false`.
