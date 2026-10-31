# Composable entities, facets and campaign instances

**Status:** first slice implemented as pure in-memory values; see the [entity facets contract](../../ENTITIES.md). A person, a species, a particular creature and a sentient sword share one `EntityDefinition` type with typed facets; campaign `EntityInstance`s carry added/replaced capabilities and state; `CampaignRoster` reconciles `InstanceRef` with the segment-keyed authority and intent APIs. The sentient-sword scenario (carried, damaged, heard, addressed, played by a human) is proven on **synthetic fixtures** only. Not persisted (010 owns the store; a provisional payload codec is proven against a neutral in-memory envelope), no relations (017), no runner.

**Outcome:** Replace exclusive Character, PC, Creature, Item and Location backend types with stable entities plus validated, composable capabilities.

## Scope and first proof
Define and exercise in-memory, typed **definitions, individuals/instances, composable facets and control roles** for one person, one generic species, one particular monster and one sentient sword. Reconcile Wave 1's segment-only actor identifiers with the canonical `InstanceRef`; preserve existing PC authorship and intent-routing behavior. The resulting entity model does not need a V2 file store or a universal ECS.

## Subsequent milestones and moonshot
Typed facets for physical, agent, container, collective and biological behavior; explicit state versus reusable traits.

## Independence and interfaces
Core model/identity package; consumes source-qualified identity and does not create a universal ECS database.

## Proving scenario
A sentient sword can be carried, damaged, heard and addressed without inheriting a special character/item subclass.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Reconcile actor-id grammars: authority and intent take a campaign-local segment, not a reference text, and `CampaignActor` still hard-codes `kind="character"` (002, 003, 008).

## Wave 2 handoff and acceptance

- **Identity:** every authored definition retains the source-qualified `SourceRef`, a distinct campaign individual uses `InstanceRef`; the immutable source identity namespace `kind` is **not** a hard-coded runtime class nor a mutable capability list. A species template is not automatically an individual actor.
- **Composition:** allow one entity to be physical, portable, agent-like or spatial without forbidden `Character + Item` inheritance. Distinguish authored traits, campaign state, attached capabilities and relationships (relationships are owned later by 017).
- **Control:** PC and NPC are assignments, not separate record kinds. Reconcile `CampaignActor` and authority/intent callers that currently take a raw actor-ID segment; provide an explicit migration/adapter contract rather than silently accepting ambiguous strings. PC rights must not disappear on a control switch.
- **Parallelism:** define in-memory serializable domain values and explicit adapter expectations, **not** a bespoke persistent writer. 010 owns the generic record-envelope serializer; 009's facet validation does not import 010. Test interoperability using a neutral in-memory envelope fixture derived from Wave 1 identity.
- **Negative proof:** a namesake across worlds is not merged; a new capability never mutates the source definition; an actor cannot claim PC control by changing a facet.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#generic-record-envelope-boundary-009--010).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
