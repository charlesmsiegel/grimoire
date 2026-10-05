# Mechanics II — actions, structured effects, conditions, clocks, and richer resolution

**Date:** 2026-10-04  
**Status:** Directional design for the next major Grimoire milestone after the Continuity Capstone. This document identifies the highest-value mechanics work and a recommended architecture. Each implementation slice still requires the repository's normal adversarial-review and planning process.  
**Builds on:** Mechanics & Dice Phases 1–8, all existing module/sheet/check/proposal/audit infrastructure, and the forthcoming continuity capstone.  
**Primary implementation target:** Claude Code or another coding agent working in this repository.

---

# 1. Why Mechanics II

Grimoire's first mechanics milestone is already much more than a dice roller.

The repository currently has:

- declarative mechanics modules rather than executable game-system plugins;
- multiple sheet types per entity kind;
- typed fields, field groups, derived statistics, and expression evaluation;
- generic sheet storage and pretty module-defined layouts/themes;
- creation budgets and character/content creation flows;
- advancement pools and formula-based advancement costs;
- a generic dice engine;
- module-defined checks and outcome ladders;
- LLM-proposed checks during play;
- a durable accept/decline/resolution proposal state machine;
- engine-resolved random rolls;
- continuation narration grounded in roll results and rules;
- an end-of-scene mechanics audit that catches narrated contradictions and proposes sheet corrections;
- a scene-state tracker;
- in-app module authoring, migration, validation, import, and export.

That means the next important problem is **not more sheet schema**.

The missing abstraction is that Grimoire knows how to represent mechanical state and how to resolve a check, but most consequences still travel through narration and later audit.

The current conceptual loop is:

    fiction
      -> model proposes check
      -> engine resolves roll/outcome
      -> model narrates consequence
      -> later absorb audit notices mechanical state changes

Mechanics II should make the loop:

    fiction
      -> model or player proposes a module-defined action
      -> engine validates actor/targets/costs
      -> engine resolves check/contest if required
      -> engine computes structured effects from the outcome
      -> effects are applied as one recoverable mechanical transaction
      -> continuation narration is grounded in the already-applied result
      -> end-scene audit verifies/catches exceptional prose, rather than serving as the ordinary state-transition engine

The central principle mirrors the continuity capstone:

> The LLM interprets what the fiction calls for. The engine asserts what the formal game state becomes.

---

# 2. Primary goal

Add a system-neutral **Action + Effect** layer on top of the existing module/check/sheet infrastructure.

An action is a module-defined thing an actor may attempt.

An effect is a constrained declarative state transition produced by an action's cost or resolved outcome.

With those two abstractions in place, Grimoire can model:

- attacks and damage;
- healing;
- spending or restoring resources;
- gaining and clearing conditions;
- advancing clocks;
- inventory/reference changes;
- status-track movement;
- social or investigative actions;
- magic/ability use;
- downtime mechanics;
- extended projects;
- resisted/opposed actions.

without hard-coding “combat,” “hit points,” “Armor Class,” “Willpower,” “Stress,” “Paradox,” or any specific RPG vocabulary into Grimoire itself.

---

# 3. Non-goals

Mechanics II should **not** begin by:

- adding a hard-coded combat engine;
- adding D&D-specific initiative/action economy;
- adding World of Darkness-specific health or dice-pool semantics;
- allowing arbitrary Python or JavaScript in modules;
- letting module authors write filesystem/network code;
- replacing the existing expression evaluator with eval;
- bypassing the current proposal state machine;
- letting the LLM directly mutate sheet files;
- turning every narrated action into a forced roll;
- making combat rounds mandatory for games that do not have them;
- replacing the end-of-scene mechanics audit;
- building a tactical map;
- implementing movement grids/ranges;
- implementing every possible status-duration model in the first slice;
- implementing multi-user/server-grade concurrency beyond the repository's stated threat model;
- redesigning module authoring from scratch.

The goal is a reusable transition engine from which those features can later be composed.

---

# 4. Hard invariants

## 4.1 Module data remains declarative

Actions, conditions, clocks, and effects are data files validated at module load/save time.

No user module code executes.

## 4.2 Existing checks remain the random-resolution primitive

Do not build a second dice/check engine for actions.

A simple action either:

- references an existing check;
- references a future contest definition built on checks; or
- has no roll and resolves deterministically when accepted.

## 4.3 A resolved action produces a structured result before narration

Narration receives:

- what action occurred;
- who acted;
- targets;
- roll/check result if any;
- outcome tier;
- costs;
- applied effects;
- resulting relevant state.

It does not infer those effects after writing prose.

## 4.4 Effects are validated and engine-applied

The LLM may propose the action/targets/parameters.

The module maps an outcome to effect templates.

The engine resolves those templates to concrete operations and validates them.

The LLM never outputs an arbitrary “set health to 3” mutation outside the allowed action/effect contract.

## 4.5 Existing play must remain valid

A module with no actions behaves as it does today.

The existing roll/check proposal path remains supported.

Existing proposal files deserialize.

Existing modules require no migration merely to load.

## 4.6 Effects must be auditable and recoverable

One action may change multiple pieces of state.

A crash may not leave half of an action applied with no way to explain/recover it.

That includes the roll. Once a check has selected an outcome, the roll is durable before any effect lands (§13.1), so recovery finishes the action it recorded and never rolls it again.

Mechanics II therefore needs a transaction record rather than a series of unrelated sheet PUTs.

## 4.7 Audit becomes verification, not ordinary mutation

The absorb mechanics audit remains important for:

- narration that bypassed action flow;
- manually edited transcripts;
- actions the model failed to formalize;
- contradictions between prose and state;
- exceptional or legacy scenes.

But routine resource spending/damage/conditions from resolved actions should already be in formal state before absorb.

---

# 5. Recommended milestone structure

The next mechanics investment should be sequenced:

### Mechanics II-A — action/effect transaction substrate

Highest priority. Required before everything else.

### Mechanics II-B — conditions

Named persistent mechanical statuses, including check modifiers.

### Mechanics II-C — clocks and extended actions

Generic progress/doom/project tracks plus repeated resolution.

### Mechanics II-D — opposed/contested actions

Two-sided mechanical resolution.

### Mechanics II-E — encounter structure

Optional initiative/turn/round state for systems that need it.

Do not begin with II-E. Combat UI without a reliable generic effect transaction layer would create a large system on the wrong foundation.

---

# 6. New module file: actions.json

Add an optional module pack file:

    actions.json

Absence means the module has no structured actions and preserves current behavior.

Conceptual shape:

    {
      "strike": {
        "label": "Strike",
        "description": "Attempt to harm a nearby opponent.",
        "sheet_types": ["fighter"],
        "check": "melee",
        "targets": {
          "min": 1,
          "max": 1,
          "kinds": ["characters", "creatures"]
        },
        "costs": [
          {"op": "spend", "target": "actor", "field": "stamina", "amount": "1"}
        ],
        "outcomes": {
          "success": [
            {"op": "add", "target": "target", "field": "health", "amount": "-1"}
          ],
          "critical": [
            {"op": "add", "target": "target", "field": "health", "amount": "-2"}
          ]
        },
        "rules": ["combat"]
      }
    }

Names are examples using generic placeholders, not a commitment to exact field names.

## 6.1 Action fields

Recommended v1:

- label: display label;
- description: short user-facing purpose;
- sheet_types: actor sheet types that can use it; empty/omitted may mean any valid actor with required check fields;
- check: optional existing check id;
- targets: target cardinality/kind constraints;
- costs: effect templates applied before or as part of resolution;
- outcomes: map from check outcome tier to effect templates;
- rules: rule-doc ids included in proposal/continuation context;
- tags: optional strings for UI grouping and future cross-cutting mechanics.

## 6.2 No-roll actions

check may be omitted.

A no-roll action has one outcome key:

    resolved

and applies its validated effects when accepted.

Use this for explicit resource spends, recovery actions, stance changes, activating a known ability with no check, etc.

## 6.3 Unknown outcome tiers

At module validation:

- if action names a check, every outcomes key must name a tier the referenced check can produce, plus an optional fallback key if the check contract supports no-tier;
- the exact relationship with check-level/default outcome ladders must be derived from current checks.py behavior rather than duplicated.

A pack edit that makes an action reference a nonexistent tier is invalid.

---

# 7. Effect DSL

Effects must be small, explicit, and composable.

Do not begin with a generic scripting language.

Recommended first operations:

## Sheet state

    set
    add
    spend
    restore

Targets:

    actor
    target

Fields must exist on the addressed sheet and have compatible field types.

### Multi-target fan-out

An action may let the actor select more than one target (targets.max > 1). The selector stays singular; it fans out deterministically:

- an effect template whose selector is `target` is expanded once per selected target, in selection order — the order of the accepted proposal's `targets` list;
- each expansion is resolved independently: its field is checked against that target's sheet, its amount is evaluated in that target's scope (§8), and it is validated against that target's current state;
- an `actor` effect is applied once, however many targets were selected;
- concrete operations are ordered by template order, then by selection order within a template, and each is computed against the running state the earlier operations of the same transaction produced, so two operations on one field chain rather than both reading the original value;
- if any expansion fails validation, the whole action is rejected; there is no partial fan-out;
- a selection naming the same target twice is rejected.

A non-contested check is the actor's check against static inputs, so it is rolled **once**: its single outcome tier selects one outcome branch, and that branch fans out across every selected target. Contested checks (II-D) roll per side, and in v1 a contest takes exactly one target (§18.5).

This was chosen over limiting II-A to one target because the rest of the substrate is already plural — the proposal, resolution object and transaction all carry a `targets` list and per-target operations — and a single roll fanned out is the whole of what a non-contested check can mean. The per-target question only becomes hard once each target rolls too, which is why it is confined to contests and deferred there.

### set

Assign a validated value.

Use sparingly in module actions because absolute assignment is easy to misuse.

### add

Numeric/dots/track delta.

Clamp/reject behavior must follow field schema rather than silently creating out-of-range values. Prefer reject if the action definition creates an impossible transition; module authors should deliberately express caps where desired.

### spend

Subtract from a resource-like field with an insufficient-resource guard.

This is semantically distinct from add -N because failure means “action cannot pay its cost.”

### restore

Increase a resource/track subject to declared maximum semantics.

## Reference/list state

Later in II-A if straightforward, otherwise II-B:

    ref_add
    ref_remove
    list_add
    list_remove

These allow inventories, spell lists, known techniques, etc., to be manipulated without special code.

## Condition state

Introduced in II-B:

    condition_add
    condition_remove

## Clock state

Introduced in II-C:

    clock_advance
    clock_set

Do not put operations into the DSL before their state store exists.

---

# 8. Effect amount expressions

Reuse store/expressions.py.

Do not invent another evaluator.

An effect amount may be:

- integer literal;
- permitted expression string.

Evaluation scope should be deliberately narrow.

For an effect applied to a sheet target, include (for a fanned-out `target` effect, the one target that expansion addresses — §7):

- that target's numeric sheet fields;
- that target's derived values;
- roll scope values such as total, successes, margin, natural, ones where applicable;
- difficulty/modifier where already meaningful;
- a bounded set of action parameters explicitly defined in the action schema later.

Do not support arbitrary cross-actor dotted expressions in v1.

If an action needs actor-vs-target arithmetic, that belongs in contest design rather than smuggling two sheets into one unnamespaced expression scope.

Expression result must be correct type for the operation.

Non-finite, float-where-int-required, bool, or out-of-range values reject resolution visibly.

---

# 9. Action availability

Grimoire must be able to answer deterministically:

    available_actions(cid, sid, actor_ref) -> list[ActionAvailability]

An action is unavailable when:

- campaign has no module;
- action's sheet-type gate excludes actor;
- required check cannot resolve for actor;
- required target kinds cannot exist in context if the action requires an immediate target;
- mandatory cost cannot possibly be paid from current sheet state;
- module/action record is invalid.

Return a reason, not simply false, so UI can decide whether to hide or disable.

The LLM prompt should receive only actions it can plausibly propose for the acting character, not every action in the pack.

---

# 10. Action proposals and the existing proposal state machine

Do **not** build a second parallel durable proposal machine.

Generalize the current proposal payload to distinguish:

    kind: "check" | "action"

Old records without kind are interpreted as check proposals.

An action proposal contains:

    action
    actor
    targets
    optional difficulty/modifier or other permitted check parameters
    source passage/watermark as current proposal machinery requires

The exact CAS lifecycle remains:

    pending
      -> resolving
      -> resolved / declined
      -> narrated
      -> superseded where current rules apply

The Phase 4 state-machine and commit/projection split are explicitly load-bearing. Preserve its concurrency/idempotency behavior.

## 10.1 Fence/protocol

The LLM play protocol may gain an action fence in addition to existing roll/check proposal syntax.

Conceptually:

    action:
      id: strike
      actor: characters:mara
      targets:
        - creatures:sentinel

Actual wire format must fit the existing stream watcher safely and be specified in the implementation plan after inspecting FenceWatcher.

Do not let both action and raw roll proposals describe the same interrupted generation.

---

# 11. Player-initiated actions

Structured actions should not be AI-only.

Once availability exists, the scene UI can offer an **Actions** control for a selected/present actor.

Flow:

1. choose actor;
2. choose available action;
3. choose target(s);
4. fill any check parameters the action exposes;
5. preview known costs and possible outcome labels;
6. Resolve.

This produces the same proposal/resolution transaction as an LLM-proposed action.

There must be one engine path, not “manual actions” and “AI actions” with duplicated logic.

---

# 12. Mechanical resolution object

Generalize the current check resolution into a richer action result without breaking current callers.

Conceptual:

    {
      "kind": "action",
      "action": "strike",
      "action_label": "Strike",
      "actor": "characters:mara",
      "targets": ["creatures:sentinel"],
      "check": {
        ...existing CheckResolution fields...
      },
      "outcome": "success",
      "costs": [
        ...concrete effect operations...
      ],
      "effects": [
        ...concrete effect operations...
      ],
      "transaction": "mt42"
    }

A no-roll action has check null and outcome resolved.

Concrete effect operation records the resolved numeric value and exact target. Narration never receives unresolved formulas as if they had already happened.

---

# 13. Mechanical transactions

This is the most important store addition in Mechanics II.

Add a campaign mechanical transaction ledger, for example:

    <campaign>/mechanics_transactions.json

It must record enough to:

- explain every structured mechanical change;
- detect/recover an interrupted multi-file application;
- support safe undo where feasible;
- let the Story Graph later show mechanics events;
- let the absorb audit distinguish an action-driven state change from an unexplained narration-driven change.

Conceptual transaction:

    {
      "seq": 42,
      "id": "mt42",
      "scene": "012--...",
      "proposal": "p17",
      "action": "strike",
      "actor": "characters:mara",
      "targets": ["creatures:sentinel"],
      "status": "committed",
      "resolution": {...},
      "ops": [
        {
          "target": {"store": "sheet", "kind": "characters", "id": "mara"},
          "field": "stamina",
          "before": 3,
          "after": 2,
          "expected_gen": "...",
          "result_gen": "..."
        },
        {
          "target": {"store": "sheet", "kind": "creatures", "id": "sentinel"},
          "field": "health",
          "before": 5,
          "after": 4,
          "expected_gen": "...",
          "result_gen": "..."
        }
      ],
      "created": "<iso>"
    }

Do not log private prose beyond labels/ids already represented in campaign data. This is campaign-private state, not generic logs.

status is prepared, committed or rejected (§13.1). resolution carries the roll result and the selected outcome tier from the moment the transaction is first written.

## 13.1 Roll/record/apply/commit

Because one action may touch multiple files, application needs recovery semantics — and because the roll is random, the roll has to be part of what is recovered rather than something recovery recomputes.

Today a check proposal's roll becomes durable at one CAS: `proposals.claim` moves the record `pending -> resolving`, `checks.resolve_check` rolls in memory, and the `resolving -> resolved` transition stores the resolution carrying the result (`RESOLUTION_EDGES`). A crash before that CAS loses a roll nobody saw, which is why the `resolving -> pending` revert is safe there. An action must keep that property while adding effects, so its roll is written down before anything is applied.

Required pattern, all under one campaign lock hold:

1. Claim the proposal (`pending -> resolving`) and validate actor, targets and every cost against current state (§26). A failure here has rolled nothing; the existing revert hands the proposal back.
2. Resolve the random check in memory (or skip it for a no-roll action) and select the outcome branch.
3. Resolve every cost and outcome template to concrete operations, with before/after values and expected source generations, validating each against current state.
4. **Record.** Persist the transaction as prepared, in one atomic write carrying the roll result, the selected outcome tier and every resolved operation. This is the transition that makes the roll exist, and it lands before any effect is applied.
5. Apply each concrete operation idempotently.
6. Mark the transaction committed.
7. Move the proposal `resolving -> resolved` with a resolution naming the transaction, then project the roll (`proposals.project`, idempotent by proposal tag through `rolls.find_or_append_by_proposal`) and narrate.

Nothing is written or shown between steps 2 and 4, so a crash before step 4 leaves a resolving proposal and no transaction: no one observed the roll, and the phase-4 path (revert or supersede, then a fresh accept rolls fresh) stays sound. From step 4 on, the recorded roll is the only roll:

- the `resolving -> pending` revert is refused for a proposal a transaction names, checked under the same lock as the CAS; taking it would hand back a chip whose re-accept rolls again;
- a retry or double-accept of a resolving proposal that a prepared transaction names finishes that transaction from its record and continues at step 7, instead of answering 409 "adjudication in progress";
- `proposals.heal`, which `supersede` and `new` already call before retiring or replacing a record (#242), also completes a prepared transaction naming that record, so retirement never outruns a recorded roll. The roll stands as history, as a superseded resolved roll does today, and no continuation is offered;
- a committed transaction whose proposal is still resolving needs only step 7.

If step 3 rejects the selected outcome after the roll — an amount expression out of range, a target field that no longer fits — the roll is still recorded. The transaction is persisted as rejected, with the roll, the tier and the reason and no operations; nothing is applied; and the proposal resolves carrying that rejection, so the roll is projected like any other. Reverting to pending instead would let a reader re-accept until some roll's outcome validated.

If a crash occurs with a prepared transaction:

- recovery takes the roll, tier and operations from the transaction and never calls resolve_check again;
- recovery determines which operations landed;
- completes idempotently if current state still matches either before or intended after;
- refuses/flags if external mutation makes safe recovery impossible.

Do not simply write several sheets and hope the process survives.

The implementation plan should inspect existing module-edit recovery/journalling patterns and reuse their lessons rather than inventing a weaker protocol.

## 13.2 One campaign lock

All action resolution and transaction application occur under campaign_lock(cid).

Audit existing sheet writers to ensure the action transaction cannot race a same-campaign sheet edit in a way that defeats its generation checks.

Do not introduce an independent per-mechanics lock that can deadlock with campaign_lock.

---

# 14. Undo

A structured action is a particularly strong candidate for undo because its before/after values are explicit.

Provide:

    POST /campaigns/{cid}/mechanics-transactions/{tid}/undo

Rules:

- transaction must be committed;
- current affected values/gens must still equal what transaction produced;
- otherwise 409, never overwrite later play;
- undo writes a new transaction that reverses the original rather than deleting history;
- undoing that reversal may function as redo if CAS conditions still hold.

If the existing generic journal can cleanly represent a multi-operation mechanics transaction, reuse it. If not, do not force-fit a multi-file action into unrelated one-row journal entries; keep mechanics transaction history authoritative and optionally add a journal summary row pointing to it.

---

# 15. Continuation narration

The current roll continuation grounds narration in:

- roll result;
- on-roll rules;
- check-specific rules.

Action continuation should additionally include:

- action description;
- actor/targets;
- outcome;
- costs applied;
- effects applied;
- relevant resulting sheet/condition/clock state;
- action rule docs.

Instruction should be explicit:

> Narrate the result that has already been mechanically resolved. Do not change, reverse, add, or ignore formal effects unless the player explicitly begins another mechanical action.

This closes the loop before prose is written.

---

# 16. Conditions — Mechanics II-B

Conditions are named persistent mechanical statuses attached to an actor or other sheet-bearing entity.

Add optional module file:

    conditions.json

Conceptual definition:

    {
      "wounded": {
        "label": "Wounded",
        "description": "...",
        "stacking": "none",
        "check_modifiers": [
          {"checks": ["athletics"], "modifier": -1}
        ],
        "rules": ["injury"]
      }
    }

## 16.1 Condition store

Campaign-owned state, not stored inside prose fields.

Possible shape:

    <campaign>/conditions.json

    {
      "characters:mara": {
        "wounded": {
          "stacks": 1,
          "source_transaction": "mt42",
          "applied_scene": "012--..."
        }
      }
    }

Do not put conditions into character_state.md. That file is narrative state/knowledge, not formal module state.

## 16.2 Stacking v1

Support only:

- none: one instance;
- count: integer stack count with optional max.

Do not implement arbitrary duration scripting initially.

## 16.3 Check modifiers

resolve_check must incorporate active condition modifiers deterministically.

The resolution should report them separately:

    base modifier
    condition modifiers
    final modifier

so the UI and continuation can explain the number.

A condition definition may target named checks and/or tags if check tagging is added. Do not make free-text rule matching decide modifiers.

## 16.4 Removing conditions

Conditions leave only through:

- structured effect;
- manual user action;
- later duration subsystem.

Narration alone does not clear one.

---

# 17. Clocks — Mechanics II-C

Grimoire should support generic mechanical clocks/progress tracks without assuming they belong on a character sheet.

Examples include:

- ritual completion;
- pursuit;
- suspicion;
- project progress;
- doom;
- infiltration alert.

These may represent formal game state and therefore should not be conflated with continuity plot threads.

## 17.1 Store

Campaign-owned:

    <campaign>/mechanics_clocks.json

Record:

    {
      "escape": {
        "label": "Escape",
        "current": 2,
        "max": 6,
        "status": "active",
        "source": "module-or-manual",
        "updated_scene": "..."
      }
    }

## 17.2 Templates vs instances

A module may define clock templates if useful, but campaigns must also be able to create ad-hoc formal clocks manually.

Do not require every clock to be predeclared in the module.

## 17.3 Effects

    clock_advance
    clock_set

Advancing beyond max follows an explicit module/store rule; do not silently wrap.

When max is reached, expose that as a mechanical event/result. Do not automatically decide the fictional consequence unless the action/module defines one.

## 17.4 Extended actions

An extended action is a repeated action whose success effects advance a clock.

Do not build a second “extended roll” state machine if ordinary actions + clocks express it.

What II-C may add is convenience schema/UI:

    extended:
      clock: project
      complete_at: max

but the underlying mechanics remain action transactions.

---

# 18. Opposed and contested actions — Mechanics II-D

The current check engine is actor-vs-static inputs.

Many games need actor-vs-target resolution.

Do not fake this by asking the LLM to turn the target's sheet into a difficulty.

Add an engine-level contest primitive built from two existing checks.

Conceptual action definition:

    contest: {
      "actor_check": "attack",
      "target_check": "defend",
      "compare": "successes"
    }

or a constrained comparison expression over result scope.

## 18.1 Contest result

Return both check resolutions plus:

- winner: actor / target / tie;
- margin if defined;
- outcome key consumed by action outcomes.

## 18.2 Randomness

Both sides' rolls are engine-generated and logged.

Do not allow the LLM to supply one side's random result.

## 18.3 Comparison modes

Start with result shapes the dice engine already exposes:

- total;
- successes;
- optionally margin.

If a module needs an exotic comparison, add a safe expression over named scalar result values rather than arbitrary code.

## 18.4 Conditions/modifiers

Each side's active conditions modify only its own check unless a condition explicitly defines a cross-target mechanic in a later extension.

Keep v1 contest scope understandable.

## 18.5 One target per contest in v1

A contest action must declare targets.max of 1; module validation rejects anything else. One contest is one actor roll against one target roll, and its one outcome branch is applied as in §7.

Multi-target contests are deliberate later work (§37). They have to answer questions II-A's fan-out does not: whether the actor rolls once against every defender or once per defender, and how actor effects compose when several targets each produce their own outcome tier.

---

# 19. Encounter/round structure — Mechanics II-E, deliberately later

Once actions/effects/conditions/clocks/contests work, some modules will want initiative and round/action economy.

Do this as an optional encounter layer, not a prerequisite for actions.

Potential store:

    <campaign>/encounters/<eid>.json

Potential concepts:

- participants;
- initiative/order;
- current turn;
- round;
- per-turn action/resource reset hooks;
- encounter conditions;
- end condition.

This requires a separate spec. Do not smuggle it into II-A because an “attack” example makes combat tempting.

A game without initiative must continue to use structured actions normally.

---

# 20. Scene state tracker interaction

The scene-state tracker and mechanics state answer different questions.

Tracker:

- visible/current scene facts;
- who is present;
- mood/injury descriptions;
- awareness.

Mechanics:

- formal rule state;
- health/resource values;
- conditions;
- clocks.

Do not merge the stores.

However, actions may produce a **follow-up tracker update** after formal effects are committed.

Example:

- mechanics health decreases;
- tracker inference later updates visible injury wording.

Formal effect lands first. Tracker narration follows.

A future effect op that directly sets a tracker field should require a dedicated design because tracker awareness semantics are richer than a plain key/value write.

---

# 21. End-scene mechanics audit after actions

Phase 5 remains valuable but its role changes.

Audit receives:

- transcript;
- sheet baselines/current sheets;
- roll log;
- committed mechanics transactions;
- conditions/clocks.

It should distinguish:

1. **Explained state change** — matches a committed transaction: no correction.
2. **Narrated but unformalized change** — transcript says a resource/condition changed with no action transaction: propose correction.
3. **Mechanical contradiction** — prose contradicts committed result/state: warn.
4. **Missing narration** — formal effect landed but prose never acknowledged it: optional warning, not necessarily a sheet edit.

This prevents the audit from proposing a second copy of an effect already applied during play.

---

# 22. Module context and prompt budgeting

Do not dump every action/condition into every prompt.

For the current acting character, context should include:

- available action ids/labels;
- brief descriptions;
- target requirements;
- check id;
- perhaps known costs.

Detailed rules/effect maps can be injected when an action is proposed/resolved, not on every ordinary turn.

This keeps the scene prompt from becoming a mechanics manual.

Rules already use a tiered context strategy; fit actions into it rather than creating an unbounded section.

---

# 23. UI priorities

## 23.1 Scene action palette

Add an Actions control near existing dice/check mechanics controls.

For selected/present actor:

- grouped available actions;
- short descriptions;
- disabled reason where useful;
- target chooser;
- cost preview;
- Resolve button.

A user should be able to play mechanics without convincing the LLM to notice a check.

## 23.2 LLM action proposal card

When generation pauses on a proposed action:

Show:

- action;
- actor;
- targets;
- check/difficulty/modifier;
- known costs;
- rules summary if current UI already surfaces rule context.

Controls:

- Accept;
- edit permitted proposal parameters;
- Decline.

Preserve current check-proposal ergonomics and idempotency.

## 23.3 Resolution card

After engine resolution:

- dice result;
- outcome;
- costs;
- effects;
- condition/clock changes;
- transaction id/history affordance.

Then continuation narration appears.

## 23.4 Mechanics sidebar/status

For present actors, compactly show:

- important resources;
- active conditions;
- relevant clocks;
- sheet link.

Do not reproduce the entire pretty sheet in scene chrome.

## 23.5 History

A Mechanics History surface lists committed transactions with:

- scene;
- action;
- actor/targets;
- outcome;
- effects;
- undo when safe.

This is more useful than forcing a user to infer mechanical history from raw roll entries.

---

# 24. Module authoring UI additions

Phase 8's authoring UI should be extended rather than replaced.

Add sections for:

- actions;
- conditions;
- optional clock templates;
- later contests.

Editor requirements:

- schema-aware forms for common fields;
- effect-operation builder;
- expression validation;
- dry-run/full pack validation using existing staging discipline;
- impact report for edits that invalidate actions/conditions;
- rename fan-out where action/check/condition ids are referenced.

Module export/import automatically carries these new files.

Built-in/example modules used by tests should exercise the new features with existing placeholder naming.

---

# 25. Validation

Module load/save must reject:

Action:

- unknown sheet type;
- unknown check;
- invalid target kind;
- min/max target nonsense;
- contest action with targets.max other than 1 (II-D, §18.5);
- unknown rule;
- unknown outcome tier;
- invalid effect op;
- effect field not available on declared target sheet type where statically knowable;
- invalid amount expression;
- cost op that is not safe as a precondition.

Condition:

- invalid stacking enum/max;
- unknown check modifier target;
- non-numeric modifier;
- unknown rule.

Clock template:

- max <= 0;
- default outside range.

Runtime validation handles things pack validation cannot know:

- actual target has appropriate sheet;
- selected target count within min/max, with no target named twice;
- resource balance;
- target still present/existing if presence is required;
- target field current value;
- condition current stacks;
- clock instance existence;
- source generation/CAS.

---

# 26. Action costs vs effects

Costs are mechanically special.

A cost must be payable **before** random resolution.

The engine should:

1. validate every cost;
2. resolve random check;
3. record the roll, the selected outcome and the resolved cost and outcome operations as a prepared transaction (§13.1), before any of them is applied;
4. apply cost and outcome effects under the same transaction protocol.

Nothing durable is written between cost validation and the roll. The transaction is first persisted with its roll already in it, so no prepared transaction ever exists whose roll recovery would have to make up.

If the check itself fails, costs normally still apply unless action schema explicitly defines refundable behavior later.

Do not silently treat costs as just success effects.

A failed precondition produces “action cannot be attempted,” not a failed roll.

---

# 27. Failed check with effects

Actions may define effects for failure/botch/etc.

Example:

    failure:
      - add stress
    botch:
      - add stress
      - condition_add exposed

The action's check outcome tier selects exactly one outcome branch unless schema explicitly supports inheritance/composition later.

Do not automatically apply “success” effects plus “critical” effects unless the module declares such behavior.

---

# 28. Manual GM override

The user remains GM.

They must be able to:

- edit action proposal before resolution within allowed fields;
- decline;
- manually edit sheets/conditions/clocks outside an action;
- undo a safe transaction;
- roll manually as today.

Manual sheet editing need not synthesize a fake action transaction, but it should keep existing history/conflict semantics.

A future “record manual mechanics transaction” affordance would be useful but is not required for II-A.

---

# 29. Interaction with continuity capstone

The two systems should connect through read-only projections, not share core stores.

After Mechanics II:

The Story Graph may add a Mechanics lens showing:

- check/action transaction nodes or scene annotations;
- resources spent;
- conditions gained/cleared;
- clock advances.

Scene-driver machinery may later treat formal mechanics pressure as optional drivers:

- critical condition;
- nearly completed clock;
- exhausted resource.

Those integrations are intentionally deferred from the Continuity Capstone and should be added only after Mechanics II state is trustworthy.

The extension seam should be typed mechanical events/transactions, not prose parsing.

---

# 30. Todo opportunities

Do not overload Todo initially, but the mechanics substrate enables honest chores:

- module bound but required actor has invalid/missing sheet;
- prepared mechanical transaction needs recovery;
- invalid/dangling condition after module edit;
- clock/template reference broken;
- action definition unavailable because module validation failed.

Todo must not become “you have low health,” which is gameplay state rather than app maintenance unless a module explicitly defines such a reminder system later.

---

# 31. API surface

Exact routes require a plan, but II-A needs equivalents of:

Read:

    GET /campaigns/{cid}/scenes/{sid}/actions
    GET /campaigns/{cid}/mechanics-transactions
    GET /campaigns/{cid}/mechanics-transactions/{tid}

Propose/resolve:

    POST /campaigns/{cid}/scenes/{sid}/action-proposal
    POST /campaigns/{cid}/scenes/{sid}/action-proposal/{pid}/resolve

or a generalization of the current roll-proposal route rather than parallel endpoints.

Undo:

    POST /campaigns/{cid}/mechanics-transactions/{tid}/undo

Conditions:

    GET/PUT endpoints in II-B, preferably routed through effect/manual-edit helpers rather than arbitrary JSON replacement.

Clocks:

    GET/POST/PUT in II-C.

Prefer evolving the current proposal endpoint if backward-compatible typing remains clear. Avoid two almost-identical state machines.

---

# 32. Concurrency and crash recovery tests

This subsystem changes formal game state during play and needs adversarial tests.

Required scenarios:

- double-accept same action proposal -> one roll, one transaction, one narration;
- lost response then retry -> replay same resolved transaction;
- action superseded before resolution -> no effects;
- crash after the check resolved but before the prepared record -> no transaction, no effects; the proposal reverts or is superseded, and a fresh accept rolls fresh;
- crash after prepared record before first effect -> recovery applies once, using the recorded roll; fake dice assert resolve_check is not called again;
- crash after first of several effects -> recovery finishes remaining operations exactly once;
- revert of a resolving proposal named by a prepared transaction is refused;
- retry or double-accept of a resolving proposal with a prepared transaction finishes it rather than answering 409;
- supersede of a resolving proposal with a prepared transaction completes the transaction and projects its roll first;
- outcome rejected after the roll -> rejected transaction records the roll; re-accepting cannot roll again;
- multi-target action -> one roll, the target branch applied to each target in selection order; a crash after the first target's operations is finished exactly once; one target failing validation rejects the whole action;
- sheet manually changed after prepared transaction -> recovery refuses rather than overwrites;
- undo after later sheet change -> 409;
- module edited between proposal and resolution -> current pack/check/action validation prevents stale application according to existing module-lock rules;
- condition modifier cannot be applied twice through retry;
- clock advance retry is idempotent.

Use deterministic fake dice where tests already provide them.

---

# 33. Mechanics II-A acceptance criteria

The first mechanics slice is complete when:

1. Modules may define actions that reference existing checks.
2. Modules may define no-roll actions.
3. Actions constrain actor sheet types and target kinds/counts, and an effect on `target` fans out deterministically across every selected target under one roll (§7).
4. Actions may declare structured pre-resolution costs.
5. Check outcome tiers select structured effects.
6. The effect engine can at least set/add/spend/restore compatible sheet values.
7. LLMs can propose actions through a durable proposal flow.
8. Players can initiate the same actions directly from UI.
9. Accepting an action produces one engine-generated resolution.
10. All costs/effects are validated before becoming formal state.
11. Multi-state changes are recorded as a recoverable mechanical transaction.
12. Retry/double-tap cannot duplicate rolls/effects.
13. Continuation narration sees and must honor the committed transaction.
14. End-scene audit recognizes those changes as explained rather than proposing duplicates.
15. Existing check-only modules and scenes still work unchanged.
16. Module authoring can create/edit/validate actions.
17. Relevant tests, typecheck, template verification, make check, and mandatory Codex reviews pass.

---

# 34. Mechanics II-B acceptance criteria

Conditions slice is complete when:

1. Modules define named conditions.
2. Campaign state attaches them to sheet-bearing refs.
3. Effects add/remove them.
4. Stacking rules are deterministic.
5. Active conditions can apply explicit numeric modifiers to named checks.
6. resolve_check reports condition contributions.
7. Scene mechanics UI displays active conditions.
8. Action retry cannot duplicate condition application.
9. Module edits/renames preserve or visibly invalidate condition references.

---

# 35. Mechanics II-C acceptance criteria

Clocks/extended slice is complete when:

1. Campaigns can hold formal clocks independent of character sheets.
2. Effects advance/set clocks.
3. Clock writes participate in mechanical transactions.
4. Max completion is reported as a formal event.
5. Repeated actions can naturally advance a clock.
6. UI displays active relevant clocks.
7. No separate bespoke “extended-roll” state machine is required for the common case.

---

# 36. Mechanics II-D acceptance criteria

Contests slice is complete when:

1. An action can resolve actor and target checks under engine control.
2. Both random rolls are logged.
3. Comparison is deterministic from declared result fields/expression.
4. Result identifies winner/tie/margin where defined.
5. Outcome branch maps to ordinary structured effects.
6. Conditions/modifiers apply independently to each side.
7. Retry cannot reroll one side separately.
8. A contest action takes exactly one target; module validation rejects a multi-target contest (§18.5).

---

# 37. Deliberate later work

Do not block II-A on:

- initiative/rounds;
- tactical positions/range;
- area-of-effect targeting;
- multi-target contests (per-defender rolls, §18.5);
- reactions/interrupts;
- nested actions;
- triggered passive effects;
- duration measured in rounds;
- equipment slots;
- encumbrance;
- spell preparation;
- damage-type/resistance taxonomies;
- condition immunity;
- multi-resource cost alternatives;
- roll keep/re-roll currencies;
- automatic NPC tactical choice;
- encounter AI;
- maps/minis.

These may eventually be built from the action/effect transaction substrate.

If an implementation of II-A starts growing special fields for several of these, stop and re-evaluate: the generic substrate is probably being contaminated by a specific system.

---

# 38. Recommended first implementation target

The single most valuable next mechanics feature is:

> **Module-defined actions whose accepted resolution atomically applies structured sheet effects before narration.**

A minimal demonstration module/test should prove three qualitatively different actions with existing placeholder fixtures:

1. a checked action that spends a resource and changes a target field on success;
2. a checked action with a failure effect;
3. a no-roll action that changes a resource.

Once those are reliable, conditions and clocks become natural new effect targets.

Do not begin Mechanics II by designing the perfect combat encounter UI.

---

# 39. Completion boundary for the next mechanics push

The mechanics work is “substantially advanced” when Grimoire can run a system-neutral scene where:

- the model recognizes an action;
- the reader approves/edits it;
- the engine rolls if needed;
- the module determines formal consequences;
- state changes occur recoverably;
- the model narrates what actually happened;
- conditions/clocks can carry consequences forward;
- opposed actions are engine-resolved when modules request them;
- the campaign can later inspect the mechanical history.

At that point Grimoire's two main goals finally meet:

1. persistent fiction/history that remains coherent over long campaigns;
2. formal game mechanics that remain synchronized with the fiction instead of being advisory metadata beside it.

That is the product direction Mechanics II should optimize for.
