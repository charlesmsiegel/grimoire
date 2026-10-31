# Wave 2 interface and acceptance contract

**Status:** normative handoff for the **first usable slices** of sprints 009–016 on the V2 branch. Several have already landed as headless contracts; consult each sprint and current PR state for implementation status. The handoff describes intended interfaces and does not imply a complete playable application. Read with the [ordered roadmap](README.md), [V2 architecture](../ARCHITECTURE.md), and [Wave 1 contracts](../IDENTITY.md). This is not a new sprint, not a mandate to freeze the complete ontology, and not a license to skip independent review.

## Wave 2 entry gate

- Integrated `v2` must have green **required** domain and compatibility tests for the current head. CI passed for the Wave 1 repair commit, but GitHub branch protection / required up-to-date checks are a **repository-owner configuration**; no documentation or test can claim to have enabled them.
- Until branch protection is configured, every PR author must verify it targets current `v2`, test the merge result against the latest `v2` head in an ephemeral branch/worktree and check the completed result before landing. Do not merge additional PRs atop a red integration branch.
- Each Wave 2 PR must include an acceptance scenario, a failed/unsafe scenario, tests, contract documentation, and a short list of downstream consumers. Keep a single owning module for each shared interface.

## Stable shared inputs inherited from Wave 1

- `grimoire.core.identity.SourceRef` (definition, with optional pinned revision), `InstanceRef` (campaign individual), and `LegacyId` (V1 source record), plus `DefinitionIndex` semantics, are already established. Source/version/instance IDs are **not display names**.
- `grimoire.library` supplies read-only V2 root selection, V1 path refusal, and guarded write-destination checks. It does **not** provide a persistent V2 store, and V1 source access is allowed only for explicitly approved, read-only inspection.
- `grimoire.core.authority`, `grimoire.intent`, `grimoire.core.inference`, `grimoire.security`, `grimoire.testing`, and the offline evals supply policies and test seams. None constitutes a running provider, state writer, scene narrator, or mobile application.

## Parallel ownership and handoffs

| Sprint | Owns now | Consumes | Must **not** depend on another Wave 2 implementation |
|---|---|---|---|
| **009 Entity facets** | Definition/instance semantics, typed facets, capability/state distinction, control-role bridging | Wave 1 identity, authority, intent | Does not import 010's storage engine; proves values and adapters against an in-memory generic envelope |
| **010 Native file store** | Generic record envelopes, revision/read/write/compare-and-swap, source owners/heads/links, provenance, guarded atomic publication and crash recovery | Wave 1 identity, paths, fault harness and security/authority persistence obligations | Does not import 009's facet classes or embed a Character/PC/Creature case split in the writer |
| **011 V1 inventory** | Executable, offline, read-only `0.2.1` scanner, identity/art placement manifest, unresolved/loss report and skill workflow | Wave 1 source IDs, V1 path checks, legacy source-format references | Does not require 009, 010 or an operational V2 importer; output is a *plan*, never canonical destination state |
| **012 Epistemic policy** | Immutable claims/evidence/perception contract and actor-specific visibility projections | Wave 1 source/instance identity and PC sovereignty | Does not persist via 010 or require 017's graph; tests use in-memory evidence, later persisted by 020 |
| **013 Provider adapter** | Minimum real provider seam, planned-call authorization, stream/response accounting and scripted failure/cancellation fake | Wave 1 call planning/usage, tool authority and fault-testing contracts | Does not wait for 014's voice prompts or 015's baseline capture; fake prompts suffice |
| **014 Voice evidence** | Independently prompted NPC style anchors, authored portrayal voice contract, recognizable synthetic comparison | Wave 1 identity, player authority and offline quality evaluation | Does not require 009's entity implementation, 010's storage or 013's provider; tests use supplied synthetic portrayed actors |
| **015 V1 measurement** | Measured `0.2.1` synthetic-scenario capture, provenance-safe accounting and versioned `story-quality/2` interchange | Wave 1 eval/usage contracts, unmodified V1 tag | Does not require V2's new provider adapter, store or 027; no user-private content in public fixtures |
| **016 Session zero** | Immutable typed content/participation boundaries and skip/redirect *intent* contract | Wave 1 player-sovereignty policy | Does not persist via 010 or require the Wave 5 runner to execute an action |

### Generic record-envelope boundary (009 / 010)

The storage API operates on **opaque payloads** accompanied by an envelope. The envelope contains, at minimum: validated source-qualified definition or campaign-instance address; source and scope/owner attribution; explicit file-format/schema version; content revision and expected revision for writes; and a lossless representation of unknown supported authored fields. A record representing source metadata can also preserve its declared heads and links. A missing/ambiguous reference remains visible as an attributed unresolved reference rather than being rebound by name.

**010 owns the serialization and guarded persistence**, including retaining base revisions for future source merges, checking every resolved descendant against V1 path isolation and symlink traversal, audit and delegation/correction persistence adapters, and exercising crash recovery with the 006 harness.

**009 owns the meaning and validation of entity facets and campaign instances** and may provide codecs/adapters against the generic record contract *after both first-slice interfaces exist*. Neither sprint must import the other's unfinished module. Do not put species, agent capabilities, NPC control, world/campaign inheritance or character card fields inside the low-level storage writer.

The exact Markdown/JSON filenames, field spellings and on-disk form are an **010 design decision** subject to its adversarial specification review; other Wave 2 sprints consume a recorded envelope interface contract, not an invented competing serialization.

### Observation/evidence boundary (012 / 017 / 020)

**Implemented first slice:** [Knowledge and perspective contract](../KNOWLEDGE.md) (`grimoire.core.knowledge`, `grimoire.core.perspective`); it maps the status list below onto proposition *standing* and belief *stance*. The canonical *meaning* of evidence belongs to 012: event/claim/evidence IDs and sources; observer/recipient; perception or communication channel and whether delivery was confirmed; time/scene sequence; access/audience; status (**established**, **reported**, **suspected**, **rejected**, or **unknown**); and the distinction between the actual event and an actor's potentially false proposition.

- An actor's **access to evidence** is not equivalent to its **belief**, and neither is equivalent to world truth. Receiving a message does not make its assertion canonically true. A transcript line does not establish that an off-stage actor heard it.
- The actor-only projection is deterministic and must fail closed for private/unknown evidence; no model call on ordinary filtering. The director projection is separate and explicitly authorized.
- 017 builds typed graph traversal **on** 012's evidence access policy; a graph path to a hidden node never grants knowledge.
- 020 persists immutable/correctable event and evidence records using 010's envelope contract; 012 itself has no file writer. 032 later owns actual delivery semantics.

### Provider/voice/quality boundaries (013 / 014 / 015 / 027)

- V0.2.1's baseline is **one independent generation per speaking NPC contribution**. A cast of four with two speakers must not trigger four background-thinking calls, nor a single actor-voice-merging call.
- 013 must refuse unplanned provider calls, require a granted `execute` operation, and book *every attempt* (including retries, timeout/cancel/fallback) into the originating `TurnUsageLedger`. Unreported counts are missing, never free. Its scripted fake must prove no accepted state on truncated/failed output.
- 014 supplies per-portrayal voice anchors and compact dialogue context without generating one shared voice for all characters. It can exercise a fake-independent speaker rendering contract; provider integration happens in 027.
- 015 captures **actual provider-reported** V0.2.1 costs/voice for synthetic inputs, plus a v2 story-quality interchange format that reconciles narration, cached tokens, optional-call stages and measured provenance with the Wave 1 inference contract. A hand-authored synthetic baseline must stay labelled synthetic. Live provider tests are optional and billed only with explicit authorization; raw prompt material and credentials stay outside the public repo.
- 027 orchestrates actor-scoped prompts and generation using 012 + 013 + 014 and the graph. 031 adds broader context retrieval **in parallel** during Wave 4; neither may require the other's unfinished code.

### Session-zero boundary (016 / 026 / 035)

016 defines a typed, **optional** content and participation preference contract distinct from PC authorship authority, game outcomes and model-provider policy. Test fade/skip/redirect *decisions* without a runner and without silently changing an accepted world event. A "skip" in a future scene may change presentation/transition, never silently rewrite canon.

- 010 can persist opaque configuration records without interpreting their consent semantics.
- 026 may carry known V1 configuration forward if present, reporting unsupported fields as unknown; no fabricated V1 preferences.
- 035 is the **first execution owner**: accept a player's actual pause/skip/redirect through the same authorized text-input/intent route, apply 016's boundaries while preserving accepted outcomes and record its effect in V2 state. 016's headless policy alone must not be called playable skip support.

## Wave 2 exit gate: two independent proofs

**Data contract:** create four synthetic definitions/instances (person, species, particular creature, sentient weapon), persist and reload an authored root plus two independent campaign instances with revision guards, validate their IDs/facets/authority, preserve unknown fields and source ownership, and prove that the generic storage writer has no dependency on facet classes. Recovery and stale-write behavior must pass fault injection, not merely a happy-path round trip.

**Real V1 inventory:** invoke the runnable V0.2.1 scanner against a frozen **synthetic `0.2.1`-format library** in a temporary location, producing an inspectable read-only mapping for worlds, Character Card V3 versions, PC versions, campaign scenes, lore, group relations, and image object/blob/placement/gallery/outfit/expression references. The report must distinguish one reused image object from two placement slots, list missing/ambiguous paths and unsupported fields, refuse external symlinks, be deterministic/idempotent, make zero provider calls, and prove the source was not modified.

**Other exit proofs:** actor evidence filters exclude a private betrayal; two independently portrayed NPCs remain distinguishable; planned-call usage actually accounts for provider failures; the `0.2.1` cost baseline is labelled measured only when data really is provider-reported; session-zero controls produce safe *intent* output, not a fictional state write.

**No V2 canonical migration apply, actual graph, scene runtime or phone sync in Wave 2.** These remain Waves 3–6. Don't claim them when all Wave 2 tests are green.

## Downstream ownership after Wave 2: quick chats, portrayals and navigation

The new [World-optional chat contract](CHAT_MODEL.md) adds **Sprint 107 in Wave 3** after the accepted 010 storage contract; it does **not** reopen 010 or change the eight Wave 2 exit requirements. A one-off chat has an automatically allocated internal campaign namespace and no mandatory world. 107 owns its persisted chat associations and rebuildable reverse indexes. **022 independently owns effective definition inheritance** (shared character → world portrayal with inherit/replace/clear), using the same Wave 2 source/record contracts; neither can require the other's unfinished Wave 3 implementation. 035 joins both to play.

**037 is promoted to Wave 4** for direct standalone character/scenario/lorebook cards, independent of 026's V1 import. **035 in Wave 5** must play a migrated V1 world *and* a new world-less card/scenario chat; **036 in Wave 5** imports prior SillyTavern histories/swipes as an independent first proof; **052 is promoted to Wave 6** for fast Start Chat onboarding, while 065 provides later richer navigation. Historical chat records live once, in the campaign-local chat, and are indexed by all associated characters/scenarios/worlds without copying.

## Downstream ownership and unchanged milestones

- Wave 3: 107 world-optional chat scopes + 010 storage, 022 shared-character/derived-portrayal effective views (both independent), 017 graph + 012 perspective, 018 assets, 019 device conflicts, and 020 accepted events/evidence.
- Wave 4: 026 approved, idempotent V1 world/campaign import; **037 standalone card/scenario/lorebook import** independently; 027 generation using 013/014 and bounded actor views; 031 richer optional retrieval must not gate 027.
- Wave 5: 035 plays both imported V1 world scenes and new world-less 037 card/scenario chats with 107 IDs and 016 boundaries; 036 imports SillyTavern chat histories/swipes independently.
- Wave 6: 044 phone play and authenticated, conflict-preserving transfer, plus 052's one-click character/scenario Start Chat UX.
