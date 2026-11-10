# V2 UI coverage and wave-exit contract

**Status: planning and acceptance contract, not implemented UI.** [Ordered roadmap](README.md) owns the sequence and priorities. This document defines what the new **Wave 7** must put in front of a human for all usable Waves 1–6 functionality, and what the **sequential UI exit slice** must deliver after each later wave's domain first slices. New UI sprints are **108–119**; IDs 001–107 are unchanged. A first slice that is *headless*, synthetic or unfinished is **not** a functioning screen. Every action must be backed by a real domain service and an authorized typed adapter.

## Product goals and source of truth

- **One product on desktop/web and phone.** Preserve useful interaction patterns on V1 `main` (responsive shell, navigation rail, command palette, accessible list/detail forms, rich scene composer and inspector, model setup, art browser, review panels, timeline, costs and error recovery) by selective port or redesign; never merge V1's entire frontend/API or reproduce its ontology.
- **One canonical V2 store.** Source-qualified reusable definitions, composable facets, pinned portrayals, campaign-local instances and control roles are distinct. PC versus NPC is control/authority, not different fictional species. An accepted event is distinct from an NPC's observation or belief.
- **World-less first play.** Fresh install → library → optional import or create character/scenario → optional persona → Start Chat → post/generate/keep → save/continue. A world, mechanics module and campaign wizard are optional. One chat keeps one history and can be discovered from multiple indexes without duplication.
- **No invisible writes or inference.** Approval binds exactly the revision/content previewed; generated text is a proposal until accepted. Show effective provider/model, optional model calls and meaningful cost labels. A model cannot author player interiority or secretly change campaign state.
- **Unimplemented means unavailable.** The roadmap includes future milestones, some only headless. For all Wave 7 and later gates, render a truthful disabled state or omit a feature with explanatory navigation; do not invent data, fake backend operations, or pretend every `main` feature has V2 support.
- **Source and migration isolation.** Never implicitly import from or mutate a V1 library. Source/target paths and lossy mappings are explicit; failed or stale edits preserve originals. The public repo and UI fixtures use synthetic data, never private library content.

## Wave 7 coverage: everything human-facing through Wave 6

The following groups are the **required inventory** for the Wave 7 P0 convergence gate, matched to distinct new sprint owners. The list covers existing V2 first slices from 001–048, 052 and 107 where a user-visible interaction makes sense; policy-only/evaluation-only capabilities require honest status and diagnostics, not a decorative editor.

| User-facing surface | Owner | Waves 1–6 contracts | Required experience at Wave 7 exit |
|---|---:|---|---|
| Application shell, setup and navigation | **108** | 001, 002, 010, 044, 052, 107 | Fresh V2 root, library setup, responsive rail/mobile navigation, home/recent, search, deep links, command palette, drafts, focus, keyboard and accessible errors |
| Sources, entities, world and character authoring | **109** | 009, 010, 014, 017, 022, 024, 025, 037, 042, 046 | Create/edit character, persona, scenario, lore, world and supported entity facets; source-qualified search, revision/pin/clear/override provenance, relations and exact record preview |
| Card import, V1 migration and guarded review | **110** | 007, 011, 026, 036, 037, 042, 043 | Import card, scenario, lorebook and supported history; inspect original bytes/provenance/loss; preview V0.2.1 identity and art; authorize apply, review changes, rebase and missing refs |
| Start Chat, cast, play and transcript | **111** | 003, 008, 016, 027, 031, 032, 033, 035, 037, 044, 052, 107 | Start from card/scenario/premise without world; add card and cast; select NPC/director, compose, generate, view/keep/pass takes, pause/skip/redirect, close/reopen, recover forked/interrupted turns |
| Provider settings, evidence and metering | **112** | 004, 005, 013, 014, 015, 027, 031, 045, 048 | Configure/test providers, primary/director/optional roles, costs, feature flags and turn/session budgets; show live call status, usage provenance, current authorized context; no secret evidence in a public transcript |
| Campaign situation, authority and canon | **113** | 003, 012, 017, 020, 021, 023, 024, 025, 029, 030, 032, 033, 034, 035, 039, 040, 041, 042, 043, 047 | Campaign/chat hub, actor control, party/presence, stage, ability/holding/equipment and obstacle readouts, event vs belief, adjudication and reviewed effect acceptance or recovery with correct human authorization |
| Art, sync, conflicts and resilient presentation | **114** | 006, 018, 019, 028, 038, 044, 046 | Shared image browser, placements/crops, missing art and source-layer visibility; pair/sync phone/desktop, manual sync, keep-both conflicts/forks and recovery; responsive/error/accessibility checks |

**Core UI actions not in the first phone text API:** `TextApi` currently has open/list/post/generate/keep/pass/sync operations but not general source CRUD, V1 migration preview, provider configuration or Start Chat. The appropriate UI sprint must add an authenticated, revision-aware **adapter** to the existing domain service. No UI team may silently invent a second persistence model.

**Wave 6 status discipline:** 044 already has a minimal loopback page and text API; 052 has a headless quick-start contract; 035 has a headless scene runner. They are foundations, *not* proof of a complete product. If 046/047/048 or later milestones are not integrated when UI work runs, the UX must say which is blocked and by which backend work, and the wave gate remains incomplete for capabilities that were actually delivered. Do not hold the entire wave for uncommitted future milestone-2 ambitions that were never scheduled as its first slice.

## Wave 7 independent ownership and integration

The Wave 7 sprints may work concurrently only against frozen, explicit UI contracts, with noncompeting areas:

| Sprint | Primary ownership | Must not rewrite |
|---:|---|---|
| 108 | App shell, global routes/menu, design primitives and onboarding | Domain persistence or another sprint's feature page |
| 109 | Library-source views, facet/portrayal editors and source-specific read/write adapters | Shell/router or scene runner |
| 110 | Import, preview, approval/rebase/change-set UI and import adapters | Source editor's form internals or canonical import writes |
| 111 | Chat list, launch, transcript, cast/composer and scene-specific adapters | Shell/router, source definition editor |
| 112 | Provider/model settings, prompt/cost inspection and inference adapters | Generation runtime or another team's scene controls |
| 113 | Campaign state, stage/knowledge/effect panels and authority-specific adapters | Raw store files, play engine, chat transcript |
| 114 | Gallery, image editing, sync/conflict views and device adapters | Shared source IDs or backend sync algorithms |

**Handoff contract:** 108 defines stable navigation slots, typed route registration and a small design-system contract from **existing** V2 HTTP/JSON conventions; peers export self-contained page entrypoints rather than editing global routing concurrently. Schema for `SourceRef`, `ChatRef`, `InstanceRef`, revision/etag, effective view, `ApiRefused` and approval token comes **from the owning backend**, not from frontend guesses. If a needed shared API does not exist, freeze that contract first on a short serial integration commit; do not hide an architectural dependency inside two supposedly parallel PRs. Every action uses the existing library security principal and confirms destructive effects at the appropriate revision. Component tests include missing refs, stale writes, wrong authority, disconnected peer, failed model and pending unsaved draft.

### End-to-end release proof

With synthetic fixtures and **no direct CLI/editing after launching the app**:

1. First run chooses a V2-isolated library, sets a hosted or local model endpoint, and can skip world creation.
2. Import a V3 character card with its image and lorebook *or* create a character/scenario manually; inspect the pinned source and import provenance.
3. Start a world-less chat, choose greeting/persona, add another NPC after play has begun; the newcomer does not receive earlier private history.
4. Post, produce separate NPC calls with distinguishable voices and metered costs, keep a take, change a relevant user-authorized setting and reopen the same chat after process restart.
5. Inspect source context and accepted/candidate events with per-actor knowledge boundaries, then approve/reject a supported effect at its precise revision.
6. Preview/review an explicit V0.2.1 import from a separate read-only source, open a migrated-world scene, play, close and resume it; loss and art-placement warnings remain visible.
7. Put art in a chosen record/instance slot, pair two reachable devices, manually sync that image and the chat, and resolve a conflicting offline branch without silently losing either candidate.
8. Traverse the same canonical chat via Chats, Character and Scenario; navigate back without draft loss. Finish a keyboard-only/screen-reader smoke pass on desktop and touch-size pass on phone.
9. No hidden paid model call, unintended canonical write, fake card creation or V1 mutation is accepted. Failures show an actionable message and preserve drafts/provenance.

A feature not yet backed by a shipped Wave 1–6 domain service may remain **disabled with a named blocker**, but must not be presented as completed parity. See the [V2 architecture](../ARCHITECTURE.md), [chat contract](CHAT_MODEL.md), [text API](../TEXT_API.md), [phone client](../MOBILE.md) and V1 [main route table](https://github.com/charlesmsiegel/grimoire/blob/main/frontend/src/AppRoutes.tsx).

## Later-wave UI exit matrix

**Waves 8–12 run their listed domain sprints in dependency-safe parallel first.** Each numbered UI tail (115–119) is a **sequential wave exit**, consumes the *merged user-facing* first slices from its own wave and the completed 108–114 interface, and does not block unrelated domain PRs. An unfinished optional domain milestone does not become a phantom UI requirement. The exit slice must either demonstrate the shipped capability end to end or record a concrete API/UI blocker before the wave is called user-visible complete.

| New wave | Old wave | UI exit sprint | Domain sprint coverage | Sample end-to-end UI gate |
|---:|---:|---:|---|---|
| **8** | 7 | **115** | 049–072 (including 065) as applicable | Fork/retcon and conflict review; durable memory/chronicle; calendar/party/location/goal controls; full workspace search, source catalog and keyboard UX; VN staging if delivered |
| **9** | 8 | **116** | 073–087 | Auxiliary writing; time/long activities; rules/mechanics module/sheets/rolls; quests and relationship panels; story graph/context preview; voice draft and guided worldbuilding where supported |
| **10** | 9 | **117** | 088–099 | Campaign tuning, growth, encounters, conversation flow, investigation and provenance; maps, generated images, EPUB publishing and ambient cues where supported |
| **11** | 10 | **118** | 100–104 | Arc planning, content publishing/sharing, multi-PC collaboration and multi-speaker attribution; 101 is a build gate, not a user screen |
| **12** | 11 | **119** | 105–106 | Live voice RP with visible consent, transcription and keyboard fallback; immersive room navigation is optional and never suppresses text/manual control |

All later UI work must keep source identity, separate NPC inference, PC sovereignty, scope/knowledge filtering, cost and safe revision/sync semantics. The new UI slice may introduce narrow API adapters and frontend routes but may not reinterpret domain schemas or claim unfinished moonshots. A wave is marked complete separately as **domain-complete** and **UI-complete**.

## Reference inventory

This gate derives from the UI feature inventory prepared against `main`'s actual `frontend/src/AppRoutes.tsx`, character/world editors, campaign/scene pages, provider/model pages, VN reader, search, logs and model-cost views, plus V2's [thematic catalog](CATALOG.md). The `main` UI is the interaction baseline, not the data authority: reuse patterns after checking their dependencies and update them for V2's world-optional, facet-based, source-qualified ontology.
