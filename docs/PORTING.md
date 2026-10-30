# V0.2.1 → Grimoire V2 selective porting and migration

**Do not merge V1 `main` wholesale into `v2`.** V1 remains working and its tested code is reference material. V2's implementation branch is `v2`; the approved [V2 ordered roadmap](roadmap/README.md) now supersedes the loose early PR sequence below.

## Paths and safety

- **Source:** an explicitly selected frozen V1 **`0.2.1`** library or backup. V1 ordinarily defaults to `~/.grimoire/`. Never read that path implicitly, and never write to its source.
- **Destination:** V2 desktop default `~/.grimoire-v2/` (V2 override `GRIMOIRE_V2_HOME`) or distinct user-selected location. Android will default to its app-private area; until then it requires an explicit `GRIMOIRE_V2_HOME`. No automatic `GRIMOIRE_HOME` fallback and no source/destination path aliases.
- **Authority:** portable native Markdown/JSON, stored scene text, events, typed relations, image placement associations and reviewed mappings. Never import cache, credentials, logs or inferred private details as facts.
- **Cost:** source scanning, image mapping, graph building and ordinary migration verification do not call paid models. Optional content extraction is explicitly consented and billed.

The repository includes an *early migration skill specification*, [`migrate-v1-to-v2`](../.agents/skills/migrate-v1-to-v2/SKILL.md), with [V1 file mapping](../.agents/skills/migrate-v1-to-v2/references/v1-layout.md), [art/expressions](../.agents/skills/migrate-v1-to-v2/references/images.md) and [verification](../.agents/skills/migrate-v1-to-v2/references/verification.md). **Neither executable scanning nor import/apply is implemented at this baseline.** Wave 2 must deliver the executable read-only scanner, while the canonical writer and guarded import/apply are separately owned by 010 and 026. See [Wave 2 handoffs](roadmap/WAVE2_HANDOFFS.md).

## Migration milestones in delivery order

| Gate | Deliverable |
|---|---|
| Wave 1 | V2 library-path resolver, source-qualified IDs and cost/authority contracts |
| Wave 2 | Composable entity/instance model, generic revisioned files and an **executable V1 read-only inventory scanner** with source-ID, scene, image-object/placement and loss reports |
| Wave 3 | Typed graph/event evidence, shared images/placements and device conflict-safe revisions |
| Wave 4 | Reviewed **import/apply** of a V1 world plus character/PC versions, images, expressions and an accepted scene; separate per-NPC model generation; app-managed transport |
| Wave 5 | **Playable desktop text loop** using the imported V1 scenario, with stable persistence and actual token accounting |
| Wave 6 | **Playable phone client with actual sync/handoff** of scene, picture and actor knowledge to desktop |

## Why early Wave 2 scans matter

The migration agent should be able to inspect a **frozen synthetic V0.2.1 library and output an actual preview** before the V2 relation graph and image writer are finished. The scanner must preserve a source-fidelity manifest (including exact legacy path spellings, ambiguous aliases, card extensions, scene chronology and separate pixel-image objects versus logical placements) without touching V1 or writing destination state. Once the V2 file and media contracts land, sprint 026 reviews/applies this manifest rather than performing a second hidden scan.

The first **interactive** migration test still belongs to Wave 5: import the reviewed actors/art, start a scene, produce **separately generated NPC dialogue**, save and resume. The session-zero content-boundary contract from 016 supplies **optional pause/skip/redirect intents** to 035; it must never rewrite already accepted world events.

## Port selectively, only behind V2 contracts

| V1 subsystem | V2 treatment |
|---|---|
| Character V2/V3/PNG/CHARX and PC Markdown | Import authored definitions/portrayals; share native entity model and keep original source for a loss-aware export |
| World entities and relation/knowledge ledgers | Resolve to typed entity facets, relations, explicit campaign instances, events and attributed evidence |
| Content-addressed image store, placement refs and expressions | Reuse validated blob decoding/identity logic; map picture IDs and **per-version/per-scope placement links** separately; preserve crop, captions, outfit fallback and hidden inheritance |
| LLM providers, routing, retries and cost accounting | Port thin provider adapters; **separate model generation for each speaking NPC** remains required, optional thinking calls are feature-flagged |
| Async scene streaming and lifecycle | Preserve cancellation and stale-run fencing when the new text runner exists |
| Atomic file writes, locks and crash tests | Reuse proven semantics with V2 revisions and device conflict protocol |
| Mechanics, voice, React/VN UI and Android | Port useful components independently after first working text scenario; **minimal phone + sync** is promoted early |
| V1 world/campaign data | Preview/approve import into isolated V2; no silent migration on boot and no V1 source writes |

## Operational acceptance

The first imported scenario must retain stable record IDs, selected portrayal, actual image bytes and crops, expression/outfit visibility, scene order and authorship, and unknown source fields with provenance. Import rerun is idempotent, and a test forced halfway through an asset transfer must be recoverable without modifying V1 or an existing V2 record.

Device synchronization is a built-in product goal, not a particular sync client requirement. Peer-to-peer on LAN works when both apps can reach each other; roaming/off-LAN requires an optional endpoint or relay. Transferred revisions, image assets and conflicts are verified; nothing depends on silent timestamp-based overwrite.

The initial V2 branch remains headless. None of the production migration, mobile play or sync features are implemented merely because this plan or its skill exists.
