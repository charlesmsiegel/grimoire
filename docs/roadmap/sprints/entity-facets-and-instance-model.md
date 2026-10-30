# Composable entities, facets and campaign instances

**Outcome:** Replace exclusive Character, PC, Creature, Item and Location backend types with stable entities plus validated, composable capabilities.

## Scope and first proof
Model one person, one generic species, one particular monster and one sentient sword; source definitions, campaign instances and controller roles are distinct.

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

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
