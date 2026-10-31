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

## Wave 2 handoff: consume the executable inventory

Input to 026 is the **reviewed, versioned, deterministic manifest produced by 011's executable read-only V0.2.1 scanner**, not ad-hoc traversal or a second set of character/scene/image parsers. Explicit approval maps each `LegacyId` to a source-qualified authored definition/portrayal and an independent campaign instance; ambiguous or unsupported assets remain unresolved. 010 provides the only canonical record writer, 017 typed relations, 018 image objects/placements, 020 accepted events/evidence, 022 effective resolution and 016 optional content-boundary preservation. 026 must not invent a campaign's private beliefs from transcript presence.

The first **apply** proof uses one synthetic V1 world with two NPCs, one PC, a short scene, alternate portrayal and an image object reused by an avatar and expression slot. It stages bytes and metadata, verifies object hashes and per-placement crop/outfit/hidden semantics (or explicitly flags unsupported parts), writes only to an isolated V2 destination, and records a recoverable source→destination identity/revision receipt. Running it twice makes no duplicate actors, events, images or graph links; existing V2 edits and the immutable V1 source remain unchanged. No automatic paid model interpretation or remote image fetch.

No Wave 2 sprint is allowed to write V2 canonical import state: 011 produces a **report only**. This sprint is its first authorized apply consumer. See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#wave-2-exit-gate-two-independent-proofs).

## World-optional migration and reusable-portrayal handoff

Consume **107's** durable play scope for each imported campaign/chat and **022's** explicit effective-source view. Existing V1 worlds remain independently importable, but a reused V1 character card or world-local portrayal must **not** require the target to instantiate a fully built V2 world first. Preserve original exact source-qualified identity, selected version, original text, image placements and provenance.

For Elderfen variants, **do not** coalesce multiple Mara Vale cards by display name. An importer may *propose* that common stable characterization belongs to a standalone shared definition and that Regency/Modern/Mecha/Vampire settings have explicitly derived versions, but only verified/reviewed authored links become canon. A world-local source can remain standalone until such a refactoring is accepted.

Do not confuse V0.2.1 **world/campaign import (026)** with **direct SillyTavern-style character/scenario/lorebook import (037, moved to Wave 4)**. The latter is a separate adapter over the same source/store identities and does not wait for a complete V1 world migration; 036 later imports historical ST chat swipes. The source V1 library is never modified. [Chat lifecycle](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
