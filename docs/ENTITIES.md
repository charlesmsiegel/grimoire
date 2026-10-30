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
| `portable` | `bulk` (`light`, `normal`, `heavy`) | `physical` | can be carried; *who* carries it is a relation (017) |
| `durable` | `max_integrity` ≥ 1 | `physical` | can be damaged; current integrity is campaign state |
| `biological` | | `physical` | a living body; species membership is a relation (017, 024) |
| `agent` | | | can hold intentions and act; **the only facet that makes control possible** |
| `communicative` | `speaks`, `understands`: sets of `speech`, `telepathy`, `sign`, `writing` (not both empty) | `agent` | can be heard (expresses itself on any channel) and/or addressed |
| `spatial` | | | a place other entities can be in; what is in it is a relation (017) |

- `FacetBundle`: canonical (sorted by name), hashable, one facet per name, no requirement check. Used for an instance's additions and replacements.
- `FacetSet`: a bundle in which every known facet's requirements are present as **known** facets. Used for definition traits and effective facets.
- `UnknownFacet(name, data)`: a facet from another version, kept verbatim. Its data and every `extras` mapping are `JsonObject`s, whose equality is JSON-type strict (`true`, `1` and `1.0` differ). It grants no capability and satisfies no requirement; it cannot use a known facet name or put a `facet` key in its data.
- Reserved names `control`, `controller`, `pc`, `npc`, `player` and `director` are refused. This is extra defence: the real guarantee is that no authority or intent module imports facet code (pinned by `test_entity_isolation.py`).
- `FacetError(ValueError).reason`: `invalid_field`, `duplicate_facet`, `reserved_name`, `known_name_as_unknown`, `missing_requirement`.

## Definitions and instances

`EntityDefinition(ref, name, nature, traits, description="", extras={})` is one pinned revision. Facet and state classes are exact: subclasses are refused, so none can redefine a name or requirement. `nature` is `individual` (one particular someone or something) or `template` (a generic kind, such as a species). A definition is never an actor. `extras` holds unknown authored top-level fields verbatim; it takes part in equality and may not use a field or address key.

`EntityInstance(ref, source=None, local_name=None, added=(), replaced=(), state=(), extras={})`:

- `source` follows Wave 1 semantics: pinned stays on that revision, unpinned follows the declared head. `None` means a campaign-original entity, which needs a `local_name`.
- `added` capabilities live on this instance only, so awakening one sword never changes its definition or another campaign's copy. `replaced` swaps a same-named trait explicitly. A name may not appear in both.
- `state` is typed campaign state, at most one value per type. The first slice has `Integrity(current)`, which needs an effective `durable` and means full integrity when absent.
- Several instances of one definition may exist in a campaign under distinct ids (clones, three goblins). Template instances may be unnamed and then show the template's name. Naming and promotion rules belong to [Creature Individualization](roadmap/sprints/creature-individualization.md).

`effective_entity(instance, index)` resolves the exact source revision and checks it:

1. The source must be an `EntityDefinition`.
2. An instance of an *individual* must share its kind; a template's instance chooses its own.
3. It composes traits, then replacements, then additions, and checks requirements.
4. It checks state against the effective facets.

It returns a disposable `EffectiveEntity`, which records each facet's origin (`trait`, `replaced` or `added`). A missing source raises Wave 1 `MissingDefinition`; a namesake is never substituted. [Effective World Snapshots](roadmap/sprints/effective-world-snapshots.md) may supersede this view.

`EntityError(ValueError).reason`: `invalid_field`, `campaign_original_needs_name`, `not_entity_definition`, `kind_mismatch`, `replaced_without_trait`, `added_shadows_trait`, `missing_requirement`, `state_without_facet`, `invalid_state`, `duplicate_state`, `not_agent`.

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
- `instance_from_actor(a)`: `campaign:<c>/character/<actor_id>`, keeping the segment so old assignments, observations and beliefs still apply. A campaign-original actor (no source) gets an added `agent`; a sourced actor's agency comes from its converted definition.

A legacy definition and its conversion cannot share an index, because they have the same ref. `actor_view` accepts either actor type. Every `EntityInstance`, and every legacy actor whose source is an `EntityDefinition`, must be an effective agent, so the legacy type cannot bypass that rule.

## Provisional payload codec

`grimoire.core.entity_codec` (not exported from `grimoire.core`) converts definitions and instances to and from plain JSON-compatible payloads that the [Native File Store](roadmap/sprints/native-file-store-and-revisions.md) envelope carries opaquely.

- **The envelope owns the address and the schema id** (`grimoire.entity/0`). Both are passed in. A payload that repeats an address part (`ref`, `address`, `source_id`, `campaign_id`, `kind`, `entity_id`, `instance_id`, `revision`) or `schema` is refused (`address_in_payload`), so a hand edit can never appear to rebind identity. `extras` may not use these keys either.
- Facets are encoded as a list of `{"facet": name, …}` objects, so duplicates are detected.
- **Extension points keep unknowns.** Unknown top-level fields become `extras`, and unknown facet names become `UnknownFacet`.
- **Inside a known structure, unknowns are refused with a reason.** `CodecError` reasons are `unknown_facet_field`, `unknown_state`, `unknown_state_field`, `invalid_field` (including missing required fields), `address_in_payload` and `unsupported_schema`. `FacetError` and `EntityError` reasons (`duplicate_facet`, `reserved_name`, …) propagate unchanged; every refusal is a `ValueError` with `.reason`. Refusing is not losing: the envelope still holds the original payload.
- Field spellings are provisional; 010 owns serialization and files. Re-encoding is canonical: explicit nulls and defaults (`"material": null`, `"description": ""`, `"added": []`) are omitted, and the decoded values stay equal. A known field of the *other* record type (such as `traits` in an instance payload) is just an unknown field there and is kept in `extras`.

Interoperability is proven against a test-only neutral envelope that imports no grimoire module.

## Not in this slice

Persistence and files; relations (carrying, location, kinship, species membership); species-trait inheritance; facet suppression; collective, container and mechanics facets; time-bound state; UI; changes to authority or intent signatures.
