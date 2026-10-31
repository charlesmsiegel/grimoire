# Character Cards, Lorebooks and Scenario Import

> **V2 scope correction:** Treat CC V2/V3 as an interchange adapter, not native storage. Reuse the V0.2.1 mapping skill for original provenance and source image references, and import into composable V2 entities/portrayals. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** Directly import standalone character, scenario and lorebook cards into V2 sources **without creating a world**, and later attach them to an existing chat or authored setting while retaining their original provenance.

## Scope
- Parse cards, internal lorebooks, alternate portrayals, scenario prompts and image/voice references separately.
- Preview matches, ambiguous aliases, licenses, facts versus suggestions and target scopes.
- Preserve originals, provenance and user edits; reconcile selected links rather than merging by names.

## Milestones
1. Build an inspectable **approved import/apply** of (a) a standalone character card + lorebook and (b) a standalone scenario-only card, with optional portrait and greeting; prove an immediate world-less chat can reference both without creating fictional actor facts.
2. Allow selective adoption of lore, portrayal and world references.
3. Exercise duplicate candidates, source removal and repeat import idempotency.

## Proving scenario
An imported character mentions a guild and city; Grimoire suggests existing group and location links without inventing them or canonizing a greeting.

## Independence and interfaces
Import/adaptation module over Wave 2 generic records and Sprint 107's world-optional associations; no V1-world importer dependency. World Change Sets remain optional for later authoring.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- The importer runs on an untrusted `WorkerHandle` whose grants cover only staging and its target world; it applies only with a human approval bound to the staged bytes. A card's text never gains authority beyond those grants (007, [tool authority](../../TOOL_AUTHORITY.md)).

## Moved earlier: Wave 4 standalone import

**This sprint moves from Wave 5 to Wave 4** so Wave 5's first playable chat can begin from a card, scenario and lorebook, rather than only from a prebuilt V1 world. It is **independent** of 026's V0.2.1 world migration: both consumers use the source-qualified identity, 010's guarded canonical writer, 018's art placements, 022's effective source views, 107's chat/source links and Wave 1 untrusted-worker authorizations.

**First-slice acceptance scenarios:**

- Import a valid standalone **Character Card V3 JSON** with personality, example messages/voice anchors, alternate greetings, optional portrait and embedded or separately imported lorebook. Preserve original bytes, origin/revision, unknown extensions and the difference between authored style, scenario instructions and objective facts. The character becomes a source definition; it is not forced into a world.
- Import a **scenario-only** card or authored scenario document with a title, premise, opening and optional lorebook/initial cast references. It does **not** invent an `Agent` character or silently declare all opening prose canon. Launch provenance and attached sources can be recorded by 107; 027 supplies explicitly metered director narration for no-NPC scenes and 035 owns actual play.
- Preview a proposed destination, source/identity collisions, images and security scope, obtain a human approval bound to exact staged bytes, then apply through 010. Reruns are idempotent, unsupported formats and dangling links are reported, and a source update never rewrites an existing chat.
- Card additions after a chat begins use explicit source bindings and new actor-instance identities. No added card can acquire all earlier private conversation evidence by default, and no model calls are needed just to attach it.
- **Later import coverage:** Character Card V2, PNG/CHARX, SillyTavern embedded lorebook variations and advanced formatting/trigger semantics. This first slice must not claim complete compatibility merely because JSON import succeeds. The historical **SillyTavern chat log and swipes** adapter belongs to 036.

The import preview must distinguish an opening greeting, scenario premise, lorebook content, private instruction, authored voice and accepted event. The same card can be reused across multiple distinct chats without copying source definitions or combining the actors' memories.

[Chat architecture](../CHAT_MODEL.md) / [Sprint 107](world-optional-chat-scopes.md).

## Open questions
- Which card fields are ever canonical?
- How are portrayals and instructions disambiguated?
- What privacy/license evidence must survive import?
