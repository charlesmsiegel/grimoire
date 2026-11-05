# Entity facets and campaign instances

**Status:** first slice of [Entity Facets And Instance Model](roadmap/sprints/entity-facets-and-instance-model.md), implemented as pure in-memory values in `grimoire.core` (`facets.py`, `entity.py`, `roster.py`, `json_value.py`, and the provisional `entity_codec.py`). Nothing is persisted; there is no store, importer, runner or UI. The scenarios are synthetic.

The entity model has no exclusive Character, PC, Creature, Item or Location record type (the Wave 1 character types remain only as [legacy types](#legacy-first-slice-types)). A person, a species, a particular monster and a sentient sword are all `EntityDefinition`s with different **facets**. Three things stay separate:

| Concept | Value | Identity |
|---|---|---|
| Reusable authored definition | `EntityDefinition` | pinned `SourceRef` ([identity](IDENTITY.md)) |
| One individual in one campaign | `EntityInstance` | `InstanceRef` |
| Who speaks and decides for it | `ControlAssignment` | ([player sovereignty](PLAYER_SOVEREIGNTY.md)) |

`kind` is an identity namespace (`character`, `species`, `item`, …). It is not a runtime class and is not derived from facets, so a facet change never changes kind or ID.

## Facets

A facet is a typed, immutable capability contract with invariants. Integers are whole numbers, never `bool`.

| Facet | Fields | Requires | Meaning |
|---|---|---|---|
| `physical` | `material` (text or none), `size` (`tiny`, `small`, `medium`, `large`, `huge`, or none) | | occupies space, can be touched |
| `portable` | `bulk` (`light`, `normal`, `heavy`) | `physical` | can be carried; *who* carries it is a `carries` relation ([GRAPH](GRAPH.md)) |
| `durable` | `max_integrity` ≥ 1 | `physical` | can be damaged; current integrity is campaign state |
| `biological` | | `physical` | a living body; species membership is a relation (024) |
| `agent` | | | can hold intentions and act; **the only facet that makes control possible** |
| `communicative` | `speaks`, `understands`: sets of `speech`, `telepathy`, `sign`, `writing` (not both empty) | `agent` | can be heard (expresses itself on any channel) and/or addressed |
| `spatial` | | | a place other entities can be in; what is in it is a `located_in` relation ([GRAPH](GRAPH.md)) |
| `abilities` | `abilities`: `Ability` values (effects, limits, costs, prerequisites), `extras` | | what the entity may and may not do with fictional powers; an empty facet means none. Only an agent can attempt one. See [ABILITIES.md](ABILITIES.md) (025) |

- `FacetBundle`: canonical (sorted by name), hashable, one facet per name, no requirement check. Used for an instance's additions and replacements.
- `FacetSet`: a bundle in which every known facet's requirements are present as **known** facets. Used for definition traits and effective facets.
- `UnknownFacet(name, data)`: a facet from another version, kept verbatim. Its data and every `extras` mapping are `JsonObject`s, whose equality is JSON-type strict (`true`, `1` and `1.0` differ). It grants no capability and satisfies no requirement; it cannot use a known facet name or put a `facet` key in its data.
- Reserved names `control`, `controller`, `pc`, `npc`, `player` and `director` are refused. This is extra defence: the real guarantee is that no authority or intent module imports facet code (pinned by `test_entity_isolation.py`).
- `FacetError(ValueError).reason`: `invalid_field`, `duplicate_facet`, `reserved_name`, `known_name_as_unknown`, `missing_requirement`; ability values add `duplicate_ability`, `duplicate_ability_part`, `unknown_applies_to`, `empty_ability` and `contradictory_ability` ([abilities](ABILITIES.md#ability-values)). `FacetError` lives in the leaf module `facet_errors` and is re-exported from `facets`.
- `abilities` is one facet, so evolving one ability on a sourced instance means `replaced` with a full copy, which no longer follows head changes to the other abilities. Per-ability inheritance belongs to 022/024.

## Definitions and instances

`EntityDefinition(ref, name, nature, traits, description="", extras={})` is one pinned revision. Facet and state classes are exact: subclasses are refused, so none can redefine a name or requirement. `nature` is `individual` (one particular someone or something) or `template` (a generic kind, such as a species). A definition is never an actor. `extras` holds unknown authored top-level fields verbatim; it takes part in equality and may not use a field or address key.

`EntityInstance(ref, source=None, local_name=None, added=(), replaced=(), state=(), extras={})`:

- `source` follows Wave 1 semantics: pinned stays on that revision, unpinned follows the declared head. `None` means a campaign-original entity, which needs a `local_name`.
- `added` capabilities live on this instance only, so awakening one sword never changes its definition or another campaign's copy. `replaced` swaps a same-named trait explicitly. A name may not appear in both.
- `state` is typed campaign state, at most one value per type. The first slice has `Integrity(current)`, which needs an effective `durable` and means full integrity when absent.
- Several instances of one definition may exist in a campaign under distinct ids (clones, three goblins). Template instances may be unnamed and then show the template's name. Promotion of one of them to a persistent individual is described in [Individualizing a template instance](#individualizing-a-template-instance).

`effective_entity(instance, index)` resolves the exact source revision and checks it:

1. The source must be an `EntityDefinition`.
2. An instance of an *individual* must share its kind; a template's instance chooses its own.
3. It composes traits, then replacements, then additions, and checks requirements.
4. It checks state against the effective facets.

It returns a disposable `EffectiveEntity`, which records each facet's origin (`trait`, `replaced` or `added`). A missing source raises Wave 1 `MissingDefinition`; a namesake is never substituted.

`compose_instance(instance, base)` is the same composition over an explicit base: an `EntityDefinition`, or an `EffectiveDefinition` from [Effective World Snapshots](EFFECTIVE_VIEWS.md) (022), which composes a derived portrayal over its pinned base first. A definition whose payload carries `derivation` is a derived **delta**, not a complete card. Both `effective_entity` and `compose_instance` refuse it with `derived_definition`, so roster control, the index-only `actor_view` and `propose_damage` of a derived-portrayal actor fail visibly. A derived-portrayal actor's perspective comes from `snapshot_actor_view(instance, snapshot)`, and a derived template instance is promoted through its `EffectiveDefinition` (see [EFFECTIVE_VIEWS](EFFECTIVE_VIEWS.md#instances)); snapshot-aware control and damage are 035's. A base that is not an authored entity is `not_entity_definition`; a base that is not the instance's own source entity (or a base for a campaign original, or none for a sourced instance) is `source_mismatch`.

`EntityError(ValueError).reason`: `invalid_field`, `campaign_original_needs_name`, `not_entity_definition`, `kind_mismatch`, `replaced_without_trait`, `added_shadows_trait`, `missing_requirement`, `state_without_facet`, `invalid_state`, `duplicate_state`, `not_agent`, `derived_definition`, `source_mismatch`.

Capability queries return `False` or `None` when the facet is absent: `is_agent`, `can_be_carried`, `can_be_damaged`, `can_be_heard`, `can_be_addressed` and `integrity`. `propose_damage(instance, amount, index)` returns a new instance with reduced integrity, floored at 0. It is a **proposal**, not a committed event; committing state belongs to the event and recovery sprints (020, 030).

### Source drift

Validation runs at resolution time, so a head move can make an unpinned instance invalid. These are **hard, named errors**, never silent repairs:

- the definition gains a facet the instance had added (`added_shadows_trait`);
- it lowers `max_integrity` below current integrity (`invalid_state`);
- it drops a facet an addition requires (`missing_requirement`).

In this slice the remedy is to pin the instance to the earlier revision. Reviewed rebase and merge belong to [Source Rebase](roadmap/sprints/source-rebase-and-semantic-merge.md) and [Live Inheritance](roadmap/sprints/live-inheritance-and-overrides.md).

## Control: the campaign roster

Authority and intent keep naming an actor by its campaign-local segment (`ControlAssignment.actor_id`, `RoutingContext.active_pc`). **An instance id is unique within a campaign across kinds, and is never reused for a different individual**, so that segment is unambiguous. Enforcing no-reuse across history (tombstones) belongs to the file store and sync sprints (010, 019).

`CampaignRoster(campaign_id, instances)` is the one explicit adapter. Any repeated instance id, even the same value twice, is an `IdentityConflict`.

| Call | Accepts | Refuses |
|---|---|---|
| `ref_for(segment)` | a bare segment | reference text (`ValueError`); an unknown segment (`UnknownInstance`) |
| `segment_for(ref)` | an `InstanceRef` or strict reference text | another campaign, an unknown id, or the same id under another kind (`UnknownInstance`); malformed text (`ValueError`); any other type, such as a `SourceRef` (`TypeError`) |
| `assign_control(ref, controller, index)` | an agent instance | a `SourceRef` or text (`TypeError`); an unknown instance; a non-agent (`AuthorityError`); resolution errors propagate |
| `check_controls(controls, index)` | `ControlAssignment`s (anything else: `TypeError`) | reports `other_campaign`, `unknown_instance`, `unresolved` (with reason) or `not_agent` |

Facets never grant control. `assign_control` validates a *new* assignment only. An existing assignment keeps working through the authority policy even if a source change removes `agent`. That case is reported by `check_controls` and never revoked or rewritten, so PC rights do not silently disappear. A control switch (player → director → player) changes no entity value and restores the player's protected domains.

The authority and intent string fields still accept any nonempty string, and an unknown id fails closed. Tightening them is left to the [first text runner](roadmap/sprints/minimal-text-scene-runner.md).

## Legacy first-slice types

`CharacterDefinition` and `CampaignActor` remain for Wave 1 callers and stay character-only; an actor of any other kind (a sentient sword, a ship) is an `EntityInstance`, which chooses its kind explicitly. Two explicit functions convert them:

- `entity_from_character(d)`: an individual with only `agent`. Physicality is not invented.
- `instance_from_actor(a)`: `campaign:<c>/character/<actor_id>`, keeping the segment and the `InstanceRef`, so old control assignments and the actor's [knowledge](KNOWLEDGE.md) (evidence and beliefs held by that ref) still apply. A campaign-original actor (no source) gets an added `agent`; a sourced actor's agency comes from its converted definition.

A legacy definition and its conversion cannot share an index, because they have the same ref. `actor_view` accepts either actor type; `snapshot_actor_view` accepts only an `EntityInstance`. Every `EntityInstance`, and every legacy actor whose source is an `EntityDefinition`, must be an effective agent, so the legacy type cannot bypass that rule.

## Individualizing a template instance

**Status:** first slice of [Creature Individualization](roadmap/sprints/creature-individualization.md) (024), in `grimoire.core.individual` plus `Individuality` in `entity.py`. Pure and in memory; there is no runner, UI or accepted-event record.

Three goblins are three instances of the goblin **template**. An instance is **generic** while its source resolves to a template and it has no `individuality`; `is_generic(instance, index)` answers this (on the index path a derived template answers `True` too; see below). It only classifies: it does not run `effective_entity`, so an instance it calls generic can still fail resolution (for example after a head move) and be refused by `individualize`. A `local_name` on a generic instance is only a label ("the tall goblin"). An instance of an *individual* definition, or a campaign original, is already an individual.

`individualize(roster, decision, index, controls)` promotes one generic instance **in place** and returns `Individualization(roster, instance, applied)`:

- The instance keeps its `InstanceRef`. Everything keyed by that reference stays with it: the [evidence](KNOWLEDGE.md) it holds (the conversation it witnessed while generic), control assignments, and later relations (017) and image placements (018). Nothing is copied or re-keyed. Its siblings and the species definition are untouched.
- It records `scene_id`, the scene where the decision was made, which is not necessarily where the creature was first met (see the [sprint doc](roadmap/sprints/creature-individualization.md)). It sets `local_name` to the decision's name, replacing any label a generic instance had (the label is not kept), and records `Individuality(decision_id, given_name, decided_by, scene_id, template, event_ids=())`. `template` is the exact pinned template revision the source resolved to at promotion. It is **provenance only**: the live source stays `instance.source`, with the usual pin semantics, so an unpinned individual follows template head moves (and can meet the [drift errors](#source-drift); pinning is the remedy). Nothing checks `template` against the index later: it is asserted by the promotion and is unverified after that, so a hand-edited record can claim a revision that does not exist. `given_name` is history; a later rename changes only `local_name`.
- Live from the source: traits and description. Personal: name, state (wounds), added and replaced facets, extras, and everything keyed by the reference.
- A template source is not a species-membership *relation*. It is only where inherited traits come from. [The graph](GRAPH.md) assigns species membership to 024, and an explicit membership relation is a later 024 milestone.
- Individualization is facet-agnostic. No `agent` or `biological` facet is required.

**A derived species template** (a 022 portrayal of a template, such as a Vampire-world goblin) is a delta, so the index path refuses it with `derived_definition` at validation (step 8). Pass its `EffectiveDefinition` instead of the index, to both `is_generic(instance, effective)` and `individualize(roster, decision, effective, controls)`. Take it from the snapshot that captured the instance (`snapshot.effective(source)`); a snapshot itself is a `TypeError`. The effective definition must be the instance's own source entity and, for a pinned source, exactly that revision; anything else is `source_mismatch`. `template` records the leaf pinned revision (never the base), and the individual then composes through the snapshot (`snapshot.entity`). Everything else (check order, authority, validation) is the same, with `compose_instance` over the effective definition as the validation. An unpinned or missing base has no effective definition (`invalid_derivation`, `missing_base`), so it cannot be promoted either way.

**Who decides.** `IndividualizationDecision(campaign_id, decision_id, instance, name, decided_by, scene_id, event_ids=())` is the only input that individualizes. The permitted decider is the actor's current controller if it has one, otherwise `director`. An instance with no control assignment, which includes an NPC and any non-agent that was never assigned, is director-only, and a player's actor is that player's call. `controls` is a required argument, as in every authority API, so an omitted argument can never read as "no controller"; pass `()` only when there are no assignments. An existing assignment keeps its authority even when `check_controls` reports it (for example `not_agent` after a source drops `agent`), because control is reported, never silently revoked. A mismatch is an `AuthorityError`, and ambiguous control fails closed. A decision comes only from a human, out-of-character channel: the table owner's command or a confirmed intent (in solo play, the user's explicit confirmation is the director decision), or the controlling player. It **never** comes from narrator or model output. `decided_by` is checked as authority against current control, like `ControlAssignment.controller_id`: for an NPC it is `director`, the table authority over non-player actors. This differs from the [session-zero](SESSION_ZERO.md#trust-boundary) player-input records, which must name a player and never `DIRECTOR`. The record therefore does not say which human confirmed a director decision; authenticating that human, and recording who it was, is the runner's job (035). `decided_by` is asserted by that trusted caller and checked against current control here. `event_ids` are optional recorded grounds, not cross-checked against `Knowledge` in this slice.

**Check order.**
1. Argument types (`TypeError`). `controls` is read once.
2. Another campaign (`ValueError`).
3. A decision id recorded on a *different* instance (`decision_conflict`).
4. Exact target lookup (`UnknownInstance`).
5. An instance that already has `individuality`:
   - an exact replay (event ids compared sorted, and even after a rename) is a no-op with `applied=False` and no authority check. Names are compared as exact text, so a retry whose name differs only in Unicode normalization or whitespace is a `decision_conflict`, not a replay. It does not re-validate the instance against the current index, so call `effective_entity` to confirm it still resolves;
   - the same decision id with different content is `decision_conflict`;
   - any other decision is `already_individualized`.
6. Template:
   - a campaign original or an individual source is `not_template`;
   - a missing source raises `MissingDefinition`;
   - a non-entity source is `not_entity_definition`;
   - an effective definition of another entity or revision is `source_mismatch`.
7. Authority.
8. `effective_entity` validation of the result (`compose_instance` over an effective definition). For example, existing drift propagates.

A roster does not enforce unique decision ids when it is built, so a sync-merged campaign still loads. On such a roster even an exact replay fails closed with `decision_conflict`. Idempotency is per roster. A durable decision record belongs to 020/021 and history tombstones to 010/019.

**Instance invariants** (`EntityError` reasons):
- an `individuality` value must be exactly an `Individuality` (`invalid_field`);
- it needs a source (`individuality_without_template`);
- it needs a `local_name` (`individual_needs_name`);
- its template must be the same entity as the source (`template_mismatch`).

All `Individuality` and decision field errors are `invalid_field`. The `template_mismatch` rule holds only for this slice; it will be relaxed when re-sourcing to a changed species lands.

At resolution, a template revision that changes nature to `individual` makes an individualized instance `not_template`. The existing `kind_mismatch` check runs first, so this is reachable only for instances of the definition's own kind. Instances of another kind (`creature` instances of `species/goblin`) all hit `kind_mismatch`. A generic instance of the definition's own kind then simply resolves as an instance of an individual.

`is_generic` answers `False` for a source that is not an `EntityDefinition`, such as a legacy `CharacterDefinition`. `individualize` refuses such a source as `not_entity_definition`.

## Provisional payload codec

`grimoire.core.entity_codec` (not exported from `grimoire.core`) converts definitions and instances to and from plain JSON-compatible payloads that the [Native File Store](roadmap/sprints/native-file-store-and-revisions.md) envelope carries opaquely.

- **The envelope owns the address and the schema id** (`grimoire.entity/0`). Both are passed in. In the native store's `grimoire.record/1`, the schema id is a preserved top-level `"schema"` key, which is how [chat actors (107)](CHATS.md) are written. A reader checks it before decoding. A payload that repeats an address part (`ref`, `address`, `source_id`, `campaign_id`, `kind`, `entity_id`, `instance_id`, `revision`) or `schema` is refused (`address_in_payload`), so a hand edit can never appear to rebind identity. `extras` may not use these keys either.
- Facets are encoded as a list of `{"facet": name, …}` objects, so duplicates are detected.
- An instance's `individuality` is an object with `decision_id`, `given_name`, `decided_by`, `scene_id`, `template` (pinned reference text) and `event_ids` (omitted when empty). Unknown fields inside it are refused as `unknown_individuality_field`; a missing field, malformed value or unpinned template is `invalid_field`. A payload without the key decodes with `individuality=None`.
  - A pre-024 build keeps the key as an unknown extra, so nothing is lost. But such a build could clear `local_name` while keeping it, and a 024 decode then refuses the payload as `individual_needs_name`; or it could change `source` to another entity, which a 024 decode refuses as `template_mismatch`.
  - An old foreign `individuality` extra is now decoded strictly.
  - Both cases are refusals, not loss.
- **Extension points keep unknowns.** Unknown top-level fields become `extras`, and unknown facet names become `UnknownFacet`.
- **Inside a known structure, unknowns are refused with a reason**, except in the `abilities` facet: there unknown keys on the facet and on each ability value are kept as that value's `extras`, and the [ability assessment](ABILITIES.md#assessment) fails closed on them (`needs_upgrade`). `CodecError` reasons are `unknown_facet_field`, `unknown_state`, `unknown_state_field`, `unknown_individuality_field`, `invalid_field` (including missing required fields), `address_in_payload` and `unsupported_schema`. `FacetError` and `EntityError` reasons (`duplicate_facet`, `reserved_name`, …) propagate unchanged; every refusal is a `ValueError` with `.reason`. Refusing is not losing: the envelope still holds the original payload.
- Field spellings are provisional; 010 owns serialization and files. Re-encoding is canonical: explicit nulls and defaults (`"material": null`, `"description": ""`, `"added": []`) are omitted, and the decoded values stay equal. A known field of the *other* record type (such as `traits` in an instance payload) is just an unknown field there and is kept in `extras`.

Interoperability is proven against a test-only neutral envelope that imports no grimoire module.

### Registered payload keys

`sections` and `derivation` are top-level `grimoire.entity/0` payload keys owned by [Effective World Snapshots](EFFECTIVE_VIEWS.md) (022). The 009 codec keeps them in `extras` like any unknown field; only 022 composition interprets them.

## Not in this slice

Persistence and files (a codec↔store round trip of an individual is proven by a test-local join only); relations (carrying, location and kinship now exist in [the graph](GRAPH.md), without facet constraints; a species-membership relation is a later 024 milestone, see [Individualizing a template instance](#individualizing-a-template-instance)); definition-to-definition derivation is 022's, in [EFFECTIVE_VIEWS](EFFECTIVE_VIEWS.md); creating a named instance directly from a template; re-sourcing an individual to a changed species; a durable accepted-event record of an individualization decision (020/021); model proposals of individualization; cross-checking a decision's event grounds against knowledge; facet suppression; collective, container and mechanics facets (fictional abilities are the `abilities` facet, 025); time-bound state; UI; changes to authority or intent signatures.
