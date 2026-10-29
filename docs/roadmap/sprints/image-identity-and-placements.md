# Image identity, placements and expression lineage

**Outcome:** Keep image bytes deduplicated and image/asset placements linked to source-qualified V2 entities and portrayals.

## Scope and first proof
Round-trip one blob reused by two subjects with different placement crops and avatar flags; refuse unknown or wrong-source images.

## Subsequent milestones and moonshot
Expression/outfit mapping, galleries, source inheritance, captions/subjects, scene art and legacy representations.

## Independence and interfaces
Image object and placement storage; consumes native entity/store identities.

## Proving scenario
Two imported actor versions can reuse one picture without losing their different crops and expression assignments.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
