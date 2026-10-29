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

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
