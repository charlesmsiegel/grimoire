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

## Art attached to standalone cards and world-specific portrayals

Art belongs to the *picture object and scoped placement* model, never automatically to a world directory. A standalone imported card can have an avatar/gallery/expression placement with no world; a derived Modern/Vampire Elderfen portrayal may inherit core art **only where that placement's provenance/override policy allows it**. A `clear` or hidden inherited placement must not fall back to a different depiction via same logical name.

Offer a source-qualified API usable by both 026's V1 importer and 037's standalone card/scenario importer. Two chats using one character image may share blob bytes but own distinct chat/actor state. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
