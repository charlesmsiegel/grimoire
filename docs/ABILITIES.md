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

She tries to fly using radiation pressure. The labelled attempt needs `light`, `force` and `flight`. The assessment is `beyond_limits`: it cites `no_force` and `force`, charges no cost, and refuses any approval. If the author had written no such limit, the same attempt would be an `extrapolation` waiting for a ruling. If she also has another ability the flight draws on (one sharing `light` or providing `flight`), it is a `combination` under whichever ability it is filed: `no_force` and any unmet prerequisite are *terms* that bind until a director's ruling names them and sets them aside. In no case is it effortless flight. Two more constrained abilities are proven the same way: speaking with the recently dead, and a fuelled thruster pack (`backend/tests/test_ability_scenarios.py`).

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

**`extras`** exist on the facet and on every value. They keep unknown authored fields verbatim and take part in equality. They may not use one of that value's field names; the facet's extras may also not use `facet`. They **fail closed** where they could narrow what is attempted: extras on the facet, or anywhere in the named ability (the ability itself or any of its effects, limits, costs and prerequisites), make the assessment `needs_upgrade` instead of a grant. An extrapolation stretches effects it does not name, so every effect counts. Extras on the actor's *other* abilities are not consulted, except for a `combination`, where every drawn-on ability's extras fail closed too; so they do not block the named ability's own uses or an approved new ability. Write free prose in `description` or `text`, not in new keys.

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

Unlike every other known facet, **unknown keys here are kept as extras** rather than refused. Malformed known fields are still refused: a wrong container or a name listed twice is a `CodecError` (`invalid_field`), and a value the ability types reject keeps its `FacetError` reason (`invalid_field`, `duplicate_ability`, `duplicate_ability_part`, …). An explicit `null` on optional prose (`description`, `range`), or a blank `range`, decodes as absent, matching how encoding omits it. A `null` list or `certainty` is refused, because reading it as empty or `reliable` would loosen the power. Decoding accepts the read-only mappings and tuples of a stored payload, and re-encoding is canonical.

Abilities therefore persist in any record that carries an entity payload, such as a 010 definition or instance record. **The store needs no change only because it treats payloads opaquely.** The 107 chat actors are plain source bindings: they reach abilities through their definition.

## Attempts

`AbilityAttempt(attempt_id, actor, ability_id, needs, provenance, effect_id=None, conditions_met=frozenset())` is a **labelled** attempted use.

| Field | Rule |
|---|---|
| `attempt_id` | any nonempty text, with no length cap, like `Resolution.attempt_id` and `PlayerContribution.contribution_id`. For a player-controlled actor it is the submitting action's `contribution_id`, as `Resolution.attempt_id` is; one action carries at most one ability attempt. |
| `needs` | 1–32 tags: what the attempted feat requires. |
| `actor` | an exact `InstanceRef` (subclasses are refused). Ids and text in ability values, attempts and rulings must be plain `str`. |
| `effect_id` | the declared effect the labeller says it uses, or none. |
| `provenance` | attribution text up to 512 characters, never authority. |
| `conditions_met` | up to 64 prerequisite ids that the labeller says hold: a plain id names a prerequisite of the **named ability**; `<ability>/<id>` names one of an ability the attempt draws on (needed for a `combination`). Anything else is stale (`relabel`, also when the named ability is missing). These are asserted, not checked against scene facts; checking them is 029's job. |

**Labelling is the trust boundary.** These fields come from a labeller that is **not the narrator**, the same rule as for `NarrationClaim`. The assessment is only as good as the labels and the shared vocabulary.
- A labeller that writes `push` for `force` escapes `no_force`. The unused tag then shows up in `unknown_tags`.
- Filing an attempt under an invented id, or a sibling ability, does **not** escape another ability. Unless the named effect fully covers the attempt, an attempt that shares a needed tag with another of the actor's abilities (`other_drawn`) *draws on* it:
  - it is a `combination`: approvable only case by case by an explicit director ruling, and fail-closed until then;
  - every participating ability, the named one included, is treated alike, so the result does not depend on which one the attempt is filed under;
  - their limits on a needed tag (`binding_limits`) and unmet prerequisites are **terms**: they bind until an approving ruling that names exactly those terms sets them aside (a prerequisite can instead be met: `<ability>/<id>` in `conditions_met`);
  - their unknown fields fail closed and their costs are charged (`charges`);
  - an attempt another ability covers, or one that is not a use of the named ability at all, reads `relabel` (file it there).

  Only a genuinely new vocabulary is a `new_ability`; a limit of an ability it does not draw on stays advisory (`other_conflicts`), since a new power may legitimately produce that tag.
- A labeller that leaves the outcome out of `needs` (`{light}` with `veil`, for the radiation-pressure flight) gets a plain `known_capability`, and nothing shows. **Every intended outcome, and the mechanism it uses (`light` for a light-based flight), must be a needed tag:** this is a labeller obligation for 029/035.

All three escapes are pinned by tests that state what is and is not guaranteed: the classification follows the labels.

## Assessment

`assess_attempt(attempt, entity, rulings=())` takes the `EffectiveEntity` from `effective_entity`. It never resolves sources itself.

Errors (`AbilityError`, a `ValueError` with `.reason`):
- `actor_mismatch`, `not_agent`;
- `invalid_field`;
- `duplicate_ruling` (two rulings on **this** attempt id share an id; a duplicate elsewhere in the campaign never blocks this attempt);
- `conflicting_rulings`.

A non-attempt or non-entity argument is a `TypeError`. **References never raise.** An `effect_id` or `conditions_met` id that the ability does not have, including when the ability itself is missing, is reported in `stale_references`. Deleting an ability, an effect or a prerequisite therefore never makes an assessment crash.

Let `A` be the named ability and `E` the named effect, if they exist. Then:

- `conflicts`: limits of `A` that exclude a needed tag.
- **Scope** is the set of effects whose effect-scoped (`applies_to`) prerequisites and costs apply:
  - when the named `E` covers `needs`: just `E`;
  - otherwise (a stretch, or no effect named): `E` if named, **plus every effect the attempt touches**, meaning each effect sharing at least one needed tag.

  Adding a needed tag can only add effects, so no label and no extra tag ever drops the constraints of an effect the attempt draws on (a property test checks this). It is deliberately conservative: an effect touched only through a generic tag (`light`) counts too, so flight by radiation pressure carries the mirage's migraine. Authors who want effects kept apart give them distinct tags.
- **Applicable prerequisites:** the ability-wide ones plus those scoped to an effect in scope. A wrong label can therefore read `unavailable` before `relabel`. This is conservative: nothing is used.
- `candidates`: declared effects whose own tags cover `needs`, including `E`. **A union of tags across effects never covers.**
- Computed for every attempt, advisory only (without a facet, every needed tag is unknown):
  - `unknown_tags`: needed tags that none of the actor's abilities use;
  - `other_conflicts`: limits of the actor's *other* abilities that exclude a needed tag;
  - `other_unrecognized`: paths on the actor's *other* abilities that carry extras, so a director deciding a `new_ability` sees a constraint this build cannot read.
  - `other_candidates`: `<ability>/<effect>` of declared effects in the actor's *other* abilities that cover `needs`;
  - `other_drawn`: `<ability>/<effect>` of other abilities' effects that share a needed tag;
  - for a `combination` only: `binding_limits` (`<ability>/<limit>` of participating abilities' limits that exclude a needed tag) and `terms` (`other_drawn`, `binding_limits` and every unmet prerequisite, qualified as `<ability>/<id>`): exactly what a ruling on it must name.

  These change the classification only when the named effect does **not** cover the attempt. A declared use is never blocked by a sibling, so overlapping powers never deadlock.
- `unrecognized`: paths of values carrying extras. These are `#facet`, `<ability>` and `<ability>/<part>`, for every part of the named ability.

**Classification**, first match:

| # | Condition | Classification | Approval |
|---|---|---|---|
| 1 | no `A` | `needs_upgrade` if the facet has extras; `relabel` if there are stale references or `other_candidates`, or it draws on exactly one ability (file it there); if it draws on several, `needs_upgrade` (3a) or `combination` (5a); else `new_ability` | `not_approvable`, or `pending` |
| 1a | no effect of `A` shares a needed tag, but another ability's does | `relabel`: not a use of `A`; file it there (before `A`'s limits) | `not_approvable` |
| 2 | `conflicts`, outside a combination: a limit of the named ability excludes a needed tag | `beyond_limits` | `not_approvable` |
| 2a | `stale_references` | `relabel` | `not_approvable` |
| 2b | `other_candidates` while `A` has no covering effect | `relabel`: file it under the ability that covers it | `not_approvable` |
| 2d | no effect of any ability shares a needed tag | `relabel`: file it under its own ability id | `not_approvable` |
| 3 | unmet prerequisites, outside a combination | `unavailable` | `not_approvable` |
| 3a | `unrecognized` (for a combination, also the drawn abilities') | `needs_upgrade` | `not_approvable` |
| 4 | `E` covers `needs` | `known_capability` if reliable, else `attempted_feat` | `not_needed` |
| 5 | `candidates` | `relabel`: name a covering effect | `not_approvable` |
| 5a | `other_drawn` (a stretch of `A` drawing on other abilities) | `combination`: limits and unmet prerequisites become `terms`; approvable by a ruling on exactly those terms | `pending` |
| 6 | otherwise | `extrapolation` | `pending` |

`approval` is `not_needed` only when the power works as declared. A refusal is `not_approvable`, never `not_needed`. Once a ruling exists the value becomes `approved` or `refused`. **`permitted`** is the one flag a consumer should gate on: it is true exactly for a known capability, an attempted feat, or an approved extrapolation, new ability or combination.

`relabel` exists so that a labeller cannot turn a known power into an approvable extrapolation just by leaving out the effect.

Other outputs:

- **`costs`** are what *would* apply: the ability-wide costs plus those scoped to an effect in scope. They are reported only for a known capability, an attempted feat, or an **approved** extrapolation or combination. An approved combination also reports `other_costs`: `(ability, cost)` for each drawn ability's ability-wide costs and those scoped to the effects it touches. **`charges`** lists every cost to charge as `(ability, cost)`, the named ability's and the others'; consumers charge `charges`. Nothing is committed. Applying costs through accepted state is milestone 2, with [Turn Effects](roadmap/sprints/turn-effects-and-wrap-up.md) (021) and 029.
- **`uncertain`** is true for an attempted feat and for an approved extrapolation, new ability or combination. It concerns whether the power works this time. The larger action's outcome is still 029's.
- **`reasons`** are `Reason(code, text)` values. The `code` is stable; the `text` is deterministic prose naming the ids. Codes:
  - `no_ability`
  - `limit_excludes`
  - `stale_effect`, `stale_prerequisite`
  - `prerequisite_unmet`
  - `unrecognized_constraint`
  - `effect_covers`, `effect_uncertain`
  - `not_covered`, `covered_by_other_effect`, `covered_by_other_ability`, `draws_on_other_ability`, `outside_ability`
  - `unknown_tag`, `other_limit_excludes`, `other_unrecognized_constraint`
  - `approved`, `refused`, `pending_approval`, `combination_term`
  - `inapplicable_ruling`, `mismatched_ruling`
- **Order independence:** the same inputs in any order give an equal assessment.

## Provisional rulings

`AbilityRuling(ruling_id, attempt, decides, decision, provenance, note="", terms=frozenset())` decides one **exact attempt value** for one classification. `decides` is `extrapolation`, `new_ability` or `combination`; `decision` is `approved` or `refused`. `terms` is required for, and only for, a ruling on a combination: it must equal the assessment's `terms` when issued. An approval sets aside exactly those limits and prerequisites. A combination ruling applies only while the terms are unchanged: an authored change (a new limit, a prerequisite, another ability joining) makes it inapplicable, never a crash, and only rulings on identical terms can conflict. Outside a combination a limit is never liftable (`limit_not_approvable`). `provenance` and `note` are text up to 512 characters. It is **025-provisional**: 029 may replace this surface. It is unrelated to [`knowledge.Ruling`](KNOWLEDGE.md).

How rulings are matched:
- A ruling's campaign is that of its attempt's actor. Other-campaign rulings are **ignored**. Ruling ids are unique within the campaign; `check_ruling` enforces that when a ruling is issued. Assessment checks only the rulings on its own attempt id **and actor**, so a stray duplicate elsewhere, or another actor's attempt under the same id, never touches it. The same ruling record passed twice (two merged logs) counts once; two different rulings under one id raise `duplicate_ruling`.
- A ruling decides the attempt's **labels**: `attempt_id`, actor, `ability_id`, `needs`, `effect_id` and `conditions_met`. `provenance` is attribution and is not compared, so re-running a labeller with a new attribution keeps its approvals.
- Same `attempt_id`, different labels: the ruling is reported in `mismatched_rulings` and approves nothing. A relabelled attempt needs a fresh ruling.
- Same labels: the ruling **matches**. Matching rulings are grouped by `decides`.
- Only the group that equals the current classification is **live**. Approve and refuse within the live group raise `conflicting_rulings`; agreeing rulings are fine, and the lowest id is reported.
- Every other matching ruling is `inapplicable` and never raises. An approval of an extrapolation therefore stops applying when the author deletes the ability (the attempt becomes `new_ability`, `pending`), and nothing crashes.

At most 256 rulings may name one attempt id **for one actor**; rulings on other attempts, by other actors or in other campaigns are not counted.

`check_ruling(ruling, entity, existing=())` is the issue-time check. It assesses the attempt with `existing` (so an already inconsistent `existing` raises `duplicate_ruling` or `conflicting_rulings` first), then refuses, in this order:
1. `limit_not_approvable`: the attempt is beyond limits. Only an authored change to the ability can alter that.
2. `ruling_not_needed`: a known capability or attempted feat, whether the ruling approves or refuses.
3. `not_approvable`: `relabel`, `unavailable` or `needs_upgrade`. These cannot be ruled on as labelled: relabel, meet the prerequisite, or read with a newer build.
4. `wrong_classification`.
4a. `terms_mismatch`: a ruling on a combination does not name exactly its current `terms`.
5. `too_many_rulings`: the attempt id already has 256 rulings, so a further one would make it impossible to assess.
6. `duplicate_ruling`.
7. `conflicting_rulings`: a live existing ruling decided the other way.

**An approval is attempt-scoped.** It lets *this* attempt count and adds nothing to the entity. A standing new power is an authored facet change. Presenting the same attempt again returns the same assessment; that is not a new grant, but it is also not consumption tracking.

## Authority and obligations

- **Rulings** come only from the rule/adjudication channel, as a `Resolution` does, and never from narration or model output.
  - Who may issue one is not checked here. Authenticating the issuer is a **029/035 obligation**.
  - "Player/director approval" maps to two things: the player's attempt, which must come from their own contribution (whose id is the `attempt_id`, checked by 029/035), and the director's ruling.
- **Instance ability changes** (`added`/`replaced`) come only from authored edits or, later, accepted state changes (021), never from narration or model output.
  - For a player-controlled actor, consent to a changed power is a **035 obligation**.
- **Assess and charge each `attempt_id` once:** an obligation on 020, 021 and 029.
- **Superseding a ruling** is not possible in this slice: an approve/refuse pair on the same labels is a named `conflicting_rulings`, never a silent overwrite. A supersede/revoke record, and whether a ruling should also pin the entity's `resolved_source`, belong to 029.
- **Head gains the facet.** By 009's source-drift rule, an unpinned instance that *added* an `abilities` facet stops resolving (`added_shadows_trait`) once its definition's head gains one (tested): pin it, or move its powers into a `replaced` facet.
- **Whole-facet limitation.** `abilities` is one facet. Evolving a single ability on a sourced instance means `replaced` with a copy of all of them, and that copy no longer follows head changes to the others (tested). Per-ability inherit, replace and clear is a follow-up for [022](roadmap/sprints/effective-world-snapshots.md) and [024](roadmap/sprints/creature-individualization.md).

## Not in this slice

- observed consequences as evidence (020);
- committed costs and cooldowns (milestone 2);
- rules for combining abilities in one attempt (milestone 3): this slice reads them as `combination`, fail-closed until a director approves one case by case;
- limits that evolve by rule, and novel high-concept power systems (milestone 3);
- adapters to real rulesets;
- wielded or item-granted abilities (034);
- any runner, prompt or UI.

## Migration

The change is additive, and the schema id stays `grimoire.entity/0`.
- Payloads without an `abilities` facet decode identically.
- A pre-025 build keeps an `abilities` facet verbatim as an `UnknownFacet`.
- A 025 build keeps unknown inner **keys** as extras and fails closed on them. Only additive keys are tolerated: a later build that writes a new *value* for a known field (a new `certainty`, or a limit with no `excludes`) makes the definition fail to decode, with a reason. Later milestones must add keys, not widen value vocabularies. A 025 build fails closed only on keys of the facet, of the *named* ability and, for a combination, of the abilities it draws on; cross-ability or composition constraints (milestone 3) must therefore be written as **facet-level keys**, never only on one ability or in a new facet.
- One break is possible: a hand-written `abilities` facet of another shape is now refused with a reason, though the envelope still holds it. No such writer exists on `v2`.
- `FacetError` moved to the leaf module `facet_errors` and is re-exported unchanged from `facets`.
