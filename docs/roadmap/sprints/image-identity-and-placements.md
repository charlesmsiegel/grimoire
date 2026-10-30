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

## Wave 2 handoff: image identity versus occurrence

Consume the generic 010 record interface and 009 source/instance identity, but do not assume that a V1 `px1` picture ID is a file path, a blob SHA or an actor identity. 011's **read-only image placement inventory** supplies synthetic acceptance cases; no direct dependency on a partially implemented importer is required.

The first proof must preserve **one shared blob/image object with two distinct version- and scope-qualified placement links**, each with its own avatar/portrait role, focus/crop and provenance, plus a missing-reference refusal. Source/campaign/inherited/hidden placement semantics are not flattened; features not supported by the first V2 asset writer remain explicitly unmapped for the Wave 4 importer. No network fetch or paid inference during ordinary image resolution.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#parallel-ownership-and-handoffs).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
