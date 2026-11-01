# Fictional abilities: constraints, attempts and assessment

**Status:** first slice of [Fictional Abilities](roadmap/sprints/fictional-abilities.md) (Sprint 025, Wave 3). It is pure, in-memory, provider-free code in `grimoire.core`:
- `abilities.py`: the ability values and the `abilities` [facet](ENTITIES.md#facets);
- `ability_use.py`: labelled attempts, provisional rulings and the assessment;
- `entity_codec.py`: the facet's payload form.

There is no runner, no prompt and no model call. Nothing is adjudicated: [Fiction-First Adjudication](roadmap/sprints/fiction-first-adjudication.md) (029) consumes the assessment. All scenarios are synthetic.

## What it is for

An author writes, as ordinary entity data, what a powers-capable entity **may and may not** do. This works the same for a spell, a technology or an extraordinary trait. A labelled attempt is then classified deterministically, with no dice, no ruleset and no model call. No generic numeric schema is assumed. Where a mechanics module is selected, it stays authoritative ([Mechanical Resolution](roadmap/sprints/mechanical-resolution.md)).

**Proving scenario.** Iris bends light. Her ability has these parts:
- effects: `veil` (concealment, reliable), `dazzle` (glare, uncertain) and `mirage` (illusion, uncertain, costs a migraine);
- a limit, `no_force`: light carries no meaningful push, so the ability excludes the tag `force`;
- a prerequisite, `ambient_light`;
- an ability-wide cost, `eye_strain`.

She tries to fly using radiation pressure. The labelled attempt needs `light`, `force` and `flight`. The assessment is `beyond_limits`: it cites `no_force` and `force`, charges no cost, and refuses any approval. If the author had written no such limit, the same attempt would be an `extrapolation` waiting for a ruling. In neither case is it effortless flight. Two more constrained abilities are proven the same way: speaking with the recently dead, and a fuelled thruster pack (`backend/tests/test_ability_scenarios.py`).

## Ability values

`Abilities(abilities=(), extras={})` is the facet named `abilities`.
- It **requires nothing**, so an object may *describe* powers. Only an agent can *attempt* one (`not_agent`). A wielder using an item's power belongs to [Unique Equipment](roadmap/sprints/unique-equipment.md) (034).
- An empty facet is valid and means "no powers". A `replaced` empty facet clears an instance's powers.
- It never confers control. `abilities` is not a reserved name, and control still needs `agent`.

| Value | Fields | Meaning |
|---|---|---|
| `Ability` | `id`, `name`, `effects` (≥ 1), `limits`, `costs`, `prerequisites`, `description` | one authored power |
| `Effect` | `id`, `text`, `tags` (≥ 1), `range` (prose), `certainty` (`reliable` or `uncertain`) | something the ability permits |
| `Limit` | `id`, `text`, `excludes` (≥ 1 tag) | tags the ability never produces |
| `Cost` | `id`, `text`, `applies_to` (effect ids; empty means every use) | a price a use would pay |
| `Prerequisite` | `id`, `text`, `applies_to` (effect ids; empty means every use) | a condition a use needs |

Rules:
- **Ids and tags** use the identity segment grammar. Ability ids are unique within the facet. Within one ability, effect, limit, cost and prerequisite ids share **one namespace**. `applies_to` names only that ability's effects.
- **Tags are an open vocabulary** that the author and the labeller share. Tags are what makes the classification deterministic. Prose (`text`, `range`, `description`) is for people and is never read.
- **`range` is prose only and is never checked.** If a range must bind, author it as a limit tag or a prerequisite.
- **An authored contradiction is refused.** A limit may not exclude a tag that one of the same ability's effects declares (`contradictory_ability`).
- **Bounds:** prose ≤ 4000 characters; ≤ 64 abilities; ≤ 64 parts of each kind per ability; ≤ 32 tags per effect or limit.
- Values are frozen and exact types (subclasses are refused). They are canonical: sorted by id, with sets frozen and deduplicated.

**`extras`** exist on the facet and on every value. They keep unknown authored fields verbatim and take part in equality. They may not use one of that value's field names; the facet's extras may also not use `facet`. They **fail closed** where they could narrow what is attempted: extras on the facet, on the named ability, on its limits, costs and prerequisites, or on the named effect make the assessment `needs_upgrade` instead of a grant. Extras on the ability's *other* effects and on the actor's *other* abilities are not consulted, so they do not block an approved extrapolation or new ability. Write free prose in `description` or `text`, not in new keys.

`FacetError` reasons:
- `invalid_field`;
- `duplicate_ability`;
- `duplicate_ability_part`;
- `unknown_applies_to`;
- `empty_ability`;
- `contradictory_ability`.

## Payload form

The codec writes `{"facet": "abilities", "abilities": [ … ], …extras}`. Each value is an object:
- Sets are written as sorted lists.
- Defaults and empty values are omitted: `certainty: reliable`, a null `range`, `[]` and `""`. An empty facet is still written, as `{"facet": "abilities"}`, because "no powers" differs from "no facet".
- Extras are written as that object's own keys.

Unlike every other known facet, **unknown keys here are kept as extras** rather than refused. Malformed known fields are still refused: a wrong container or a name listed twice is a `CodecError` (`invalid_field`), and a value the ability types reject keeps its `FacetError` reason (`invalid_field`, `duplicate_ability`, `duplicate_ability_part`, …). Decoding accepts the read-only mappings and tuples of a stored payload, and re-encoding is canonical.

Abilities therefore persist in any record that carries an entity payload, such as a 010 definition or instance record. **The store needs no change only because it treats payloads opaquely.** The 107 chat actors are plain source bindings: they reach abilities through their definition.

## Attempts

`AbilityAttempt(attempt_id, actor, ability_id, needs, provenance, effect_id=None, conditions_met=frozenset())` is a **labelled** attempted use.

| Field | Rule |
|---|---|
| `attempt_id` | any nonempty text up to 512 characters. For a player-controlled actor it is the submitting action's `contribution_id`, as `Resolution.attempt_id` is; one action carries at most one ability attempt. |
| `needs` | 1–32 tags: what the attempted feat requires. |
| `actor` | an exact `InstanceRef`. Ids and text everywhere must be plain `str`; subclasses are refused. |
| `effect_id` | the declared effect the labeller says it uses, or none. |
| `conditions_met` | the prerequisites the labeller says hold. These are asserted, not checked against scene facts; checking them is 029's job. |

**Labelling is the trust boundary.** These fields come from a labeller that is **not the narrator**, the same rule as for `NarrationClaim`. The assessment is only as good as the labels and the shared vocabulary.
- A labeller that writes `push` for `force` escapes `no_force`. The unused tag then shows up in `unknown_tags`.
- A labeller that files the flight under an invented ability id gets `new_ability`. The real limit then shows up in `other_conflicts`.

Both escapes are pinned by tests that state what is and is not guaranteed.

## Assessment

`assess_attempt(attempt, entity, rulings=())` takes the `EffectiveEntity` from `effective_entity`. It never resolves sources itself.

Errors (`AbilityError`, a `ValueError` with `.reason`):
- `actor_mismatch`, `not_agent`;
- `invalid_field`;
- `duplicate_ruling`;
- `conflicting_rulings`.

A non-attempt or non-entity argument is a `TypeError`. **References never raise.** An `effect_id` or `conditions_met` id that the ability does not have, including when the ability itself is missing, is reported in `stale_references`. Deleting an ability, an effect or a prerequisite therefore never makes an assessment crash.

Let `A` be the named ability and `E` the named effect, if they exist. Then:

- `conflicts`: limits of `A` that exclude a needed tag.
- **Applicable prerequisites:** the ability-wide ones plus `E`'s. A non-covering `E` still pulls in its own prerequisites, so a wrong label can read `unavailable` before `relabel`. This is conservative: nothing is used.
- `candidates`: declared effects whose own tags cover `needs`, including `E`. **A union of tags across effects never covers.**
- Computed whenever the entity has the facet: `unknown_tags` (needed tags that none of the actor's abilities use) and `other_conflicts` (limits of the actor's *other* abilities that exclude a needed tag). These are advisory only.
- `unrecognized`: paths of values carrying extras. These are `#facet`, `<ability>` and `<ability>/<part>`, for the named ability's parts and the named effect only.

**Classification**, first match:

| # | Condition | Classification | Approval |
|---|---|---|---|
| 1 | no `A` | `new_ability`, or `needs_upgrade` if the facet has extras | `pending` for `new_ability`; none for `needs_upgrade` |
| 2 | `conflicts` | `beyond_limits` | not approvable |
| 2a | `stale_references` | `relabel` | no |
| 3 | unmet prerequisites | `unavailable` | no |
| 3a | `unrecognized` | `needs_upgrade` | no |
| 4 | `E` covers `needs` | `known_capability` if reliable, else `attempted_feat` | no |
| 5 | `candidates` | `relabel`: name a covering effect | no |
| 6 | otherwise | `extrapolation` | `pending` |

`relabel` exists so that a labeller cannot turn a known power into an approvable extrapolation just by leaving out the effect.

Other outputs:

- **`costs`** are what *would* apply: the ability-wide costs plus `E`'s. They are reported only for a known capability, an attempted feat or an **approved** extrapolation. Nothing is committed. Applying costs through accepted state is milestone 2, with [Turn Effects](roadmap/sprints/turn-effects-and-wrap-up.md) (021) and 029.
- **`uncertain`** is true for an attempted feat and for an approved extrapolation or new ability. It concerns whether the power works this time. The larger action's outcome is still 029's.
- **`reasons`** are `Reason(code, text)` values. The `code` is stable; the `text` is deterministic prose naming the ids. Codes:
  - `no_ability`
  - `limit_excludes`
  - `stale_effect`, `stale_prerequisite`
  - `prerequisite_unmet`
  - `unrecognized_constraint`
  - `effect_covers`, `effect_uncertain`
  - `not_covered`, `covered_by_other_effect`
  - `unknown_tag`, `other_limit_excludes`
  - `approved`, `refused`, `pending_approval`
  - `inapplicable_ruling`, `mismatched_ruling`
- **Order independence:** the same inputs in any order give an equal assessment.

## Provisional rulings

`AbilityRuling(ruling_id, attempt, decides, decision, provenance, note="")` decides one **exact attempt value** for one classification. `decides` is `extrapolation` or `new_ability`; `decision` is `approved` or `refused`. It is **025-provisional**: 029 may replace this surface. It is unrelated to [`knowledge.Ruling`](KNOWLEDGE.md).

How rulings are matched:
- A ruling's campaign is that of its attempt's actor. Other-campaign rulings are **ignored**. Within the campaign, ruling ids are unique.
- Same `attempt_id`, different attempt value: the ruling is reported in `mismatched_rulings` and approves nothing. A relabelled attempt needs a fresh ruling.
- Equal attempt: the ruling **matches**. Matching rulings are grouped by `decides`.
- Only the group that equals the current classification is **live**. Approve and refuse within the live group raise `conflicting_rulings`; agreeing rulings are fine, and the lowest id is reported.
- Every other matching ruling is `inapplicable` and never raises. An approval of an extrapolation therefore stops applying when the author deletes the ability (the attempt becomes `new_ability`, `pending`), and nothing crashes.

At most 256 same-campaign rulings may name one `attempt_id`; rulings on other attempts or in other campaigns are not counted.

`check_ruling(ruling, entity, existing=())` is the issue-time check. It assesses the attempt with `existing` (so an already inconsistent `existing` raises `duplicate_ruling` or `conflicting_rulings` first), then refuses, in this order:
1. `limit_not_approvable`: the attempt is beyond limits. Only an authored change to the ability can alter that.
2. `ruling_not_needed`: any other classification that needs no approval, whether the ruling approves or refuses.
3. `wrong_classification`.
4. `duplicate_ruling`.
5. `conflicting_rulings`: a live existing ruling decided the other way.

**An approval is attempt-scoped.** It lets *this* attempt count and adds nothing to the entity. A standing new power is an authored facet change. Presenting the same attempt again returns the same assessment; that is not a new grant, but it is also not consumption tracking.

## Authority and obligations

- **Rulings** come only from the rule/adjudication channel, as a `Resolution` does, and never from narration or model output.
  - Who may issue one is not checked here. Authenticating the issuer is a **029/035 obligation**.
  - "Player/director approval" maps to two things: the player's attempt, which must come from their own contribution (whose id is the `attempt_id`, checked by 029/035), and the director's ruling.
- **Instance ability changes** (`added`/`replaced`) come only from authored edits or, later, accepted state changes (021), never from narration or model output.
  - For a player-controlled actor, consent to a changed power is a **035 obligation**.
- **Assess and charge each `attempt_id` once:** an obligation on 020, 021 and 029.
- **Whole-facet limitation.** `abilities` is one facet. Evolving a single ability on a sourced instance means `replaced` with a copy of all of them, and that copy no longer follows head changes to the others (tested). Per-ability inherit, replace and clear is a follow-up for [022](roadmap/sprints/effective-world-snapshots.md) and [024](roadmap/sprints/creature-individualization.md).

## Not in this slice

- observed consequences as evidence (020);
- committed costs and cooldowns (milestone 2);
- combinations of, and conflicts between, abilities in one attempt;
- limits that evolve by rule, and novel high-concept power systems (milestone 3);
- adapters to real rulesets;
- wielded or item-granted abilities (034);
- any runner, prompt or UI.

## Migration

The change is additive, and the schema id stays `grimoire.entity/0`.
- Payloads without an `abilities` facet decode identically.
- A pre-025 build keeps an `abilities` facet verbatim as an `UnknownFacet`.
- A 025 build keeps unknown inner fields as extras and fails closed on them.
- One break is possible: a hand-written `abilities` facet of another shape is now refused with a reason, though the envelope still holds it. No such writer exists on `v2`.
- `FacetError` moved to the leaf module `facet_errors` and is re-exported unchanged from `facets`.
