# Reviewed V0.2.1 import/apply and recovery

**Outcome:** Import enough real V1 content to play early, without altering original files or losing image and scene references.

## Scope and first proof
Preview and approve import of one world, two characters, one PC, a scene and associated art; repeat import idempotently into an isolated V2 home.

## Subsequent milestones and moonshot
Extend to long campaigns, alternative versions/responses, epistemic history, inherited outfits, image collections and verified resumable batches.

## Independence and interfaces
Imports reviewed canonical files, images and relations; requires migration skill, native store and image placement contracts.

## Proving scenario
Existing V2 edits are protected on repeated import; V1 home remains byte-for-byte untouched.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Workers that parse V0.2.1 content get untrusted handles; the V1 library is an explicit, read-only source with no address in the V2 namespace, and the destination passes `check_migration_paths` (001, 007).
- Imported links keep their V0.2.1 origin through `LegacyIdMap`, and a fork/import round trip preserves source-qualified references in the identity text forms (002 milestone 3).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
