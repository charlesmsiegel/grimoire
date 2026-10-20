# Backend package responsibilities

These notes describe the behavior-preserving modularization on
`refactor/modularize-large-python`. They are a reading guide, not a new storage
or execution contract. [Store guarantees](../store-guarantees.md) remains the
authority for persistence and locking; [CLAUDE.md](../../CLAUDE.md) remains the
authority for project conventions.

Markdown/frontmatter and human-editable JSON remain authoritative. SQLite,
projections, search documents and vectors are disposable derivations. A smaller
source file does not justify changing who owns a write, a captured view, a
lock, a cancellation event, or the decision to degrade after a read failure.

## How to read a converted package

The original import names still resolve through explicit `__init__.py`
re-exports, including intentionally exposed private helpers and imported module
objects. Leaves name actual responsibilities and import other owners as
modules. Original algorithms, explanatory comments, defaults and resolved
annotations remain attached to their implementations.
Source references follow their moved owners; two such pointers also appear in
published endpoint descriptions. The exact OpenAPI metadata allowance and
original contract checks are documented in
[the validation record](backend-package-validation.md).

Mutable dictionaries, pools, registries and clients have one owner. Consumers
share those objects. The few owner bindings that can be replaced, including
fork state and the probe client binding, are read through narrow facade
`__getattr__` branches;
there is no generic assignment-forwarding proxy. Internal fault injection
patches the defining or consuming owner. A patch used by an external production
caller remains at the facade that caller actually reads.

Endpoint leaves eagerly share one `_router.router` per route package. Where responsibilities
interleave in the original source, a checked finalizer restores the captured
route sequence in place. It preserves route objects and list identity, rejects
missing/duplicate entries before mutation, and preserves overlapping matches.
`common` and `streaming` are shared support packages rather than endpoint routers.

## Inference and transport

| Package | Theory and responsibilities | Extension and watch point |
| --- | --- | --- |
| `inference/` | Requests become resolved generation or decision attempts. Contracts, pricing, request shaping, decision calls, tool policy and tool runtime have separate owners. | Extend one protocol at its request/call owner; retain the captured resolution, deadline and canonical LLM/tool timing reads throughout an attempt. |
| `decisions/` | Structured and native protocols produce the same decision vocabulary. Models, schema construction, reporting and each protocol adapter are separate. | Another protocol must preserve parse/refusal/report semantics; public report/parser patches remain visible to consumers. |
| `llm/` | One client coordinates transport, retry/refusal policy, accounting and lifecycle cleanup. Providers are dependency objects, not competing ledgers. | Extend transport through `_client`; retain the timeout source, shared call accounting, completion callbacks and bounded close/retry ownership. |
| `tool_calls/` | Tool declarations, message conversion and bounded tool runs describe different parts of one exchange. | Another tool changes its declaration and policy explicitly; preserve message order, run cancellation and minimum-turn policy. |

## HTTP routes and detached work

| Package | Theory and responsibilities | Extension and watch point |
| --- | --- | --- |
| `routes/models/` | Plain Pydantic contracts grouped by settings, worlds, mechanics, actors, scenes, continuity and extras. | Add a field within its domain while retaining Pydantic 1/2 and Android conventions; avoid framework-specific validators/configuration. |
| `routes/campaigns/` | Campaign requests expose settings, calendar, lifecycle, images, membership, sync and ledger behavior through one ordered router. | Add an endpoint to its domain owner and reviewed route roster; inherited records still go through overlay policy. |
| `routes/scenes/` | A scene combines transcript lifecycle, play, review, replay and follow-up orchestration. Review snapshot, identity, budget, phases and state remain distinct responsibilities. | A new review phase must preserve the captured transcript/identity and publication fences; it must not turn a proposal into an unreviewed authoritative write. |
| `routes/character_turns/` | Planning, prompt preparation, generation, persistence and group-round state are stages of the same reserved turn. | Extend the stage that owns the change; retain captured roster/state, active-round ownership, cancellation and persisted response identity. |
| `routes/runs/` | A per-app registry owns reservations, event replay, cancellation and exclusion until the producer actually unwinds. Record, execution, maintenance and HTTP adapters sit around it. | Another run class extends the existing registry/subject model. Keep the whole registry together; cancellation does not release exclusion before the worker returns. |
| `routes/common/` | Shared route dependencies separate clients, inference resolution, deadlines, responses/errors, prompts, drafts, uploads, images and lookups. | Reuse the same dependency function objects. Another deadline belongs to its policy owner, not a copied constant or a second app client. |
| `routes/streaming/` | One stream driver claims, fences, renders, persists and finalizes a turn using shared in-process per-scene token state. Display, persistence and follow-ups are supporting responsibilities. | Preserve partial-versus-complete persistence and the entire abort/finalize closure. A post-turn action must retain the existing lock and revision boundaries. |
| `routes/config/` | Settings, storage changes, providers, probes, response shaping and world options are separate request responsibilities. | A new paid settings action needs the existing server-side confirmation model. Probe closure and facade reads share the one embedding client. |
| `routes/characters/` | Record CRUD, drafts, exchange and gallery/image operations share framing and source selection. | Another gallery surface reuses captured source identity and existing draft cleanup; do not combine upload and record authority. |
| `routes/ledger/` | User-authored ledger movement, ordinary records and knowledge overrides have different policies and source readers. | A new knowledge edit remains a manual, journalled route action. Only `_knowledge` receives the moved knowledge-writer permission. |
| `routes/todo/` | One per-request context feeds a fixed catalog of campaign/library chores; expansion remains a live, on-demand read. | Add a builder and item expansion to the existing tables. Share the same context/memo within a request; overview projections must never decide an item expansion or ignore write. |
| `routes/tracker/` | Settings, transcript capture, fences, persistence, scheduling and run execution surround one per-app/campaign identity lock. | Another tracker trigger reuses scheduling and detached-run fences. Keep stale transcript/root checks and publication holds around the same effects. |
| `routes/worlds/` | World CRUD, bundles/imports, mechanics, tags, PCs/images and calendar requests share one router. | A new lifecycle operation must preserve sorted campaign holds, maintenance exclusion, temporary-file cleanup and existing post-commit failure handling. |

## Store authority, copy-on-write and presentation

| Package | Theory and responsibilities | Extension and watch point |
| --- | --- | --- |
| `store/overlay/` | A campaign sees its own records plus inherited world records, minus hidden/detached identities. Paths, ledgers, views, copying, identity transitions and lifecycle operations own separate rules. | Another inheritable kind extends the existing resolution/copy policy. Preserve strict whole-scope preflight, base-before-record publication, provenance and copy-down-before-world-delete ordering. |
| `store/context/assemble/` | One captured input snapshot becomes rendered sections, then model-specific packed messages and an inspection of that same result. | Add a section through `_catalog` and rendering. Do not reread the store during packing or create separate prompt/breakdown decisions. |
| `store/characters/` | A meta file claims a character container; addressable version JSON defines what can be opened. Memoized rows are projections vouched for by captured stamps. | Another catalog field extends its stamp proof. Imports retain one archive/decompression budget and one owned-write rollback; avatar/gallery and concurrent edits must survive partial failure. |
| `store/suggest/` | Generated suggestions use one captured calendar, cast and driver view. Durable ideas are separately reclassified against current campaign facts. | Another driver extends contracts, date judgment and provenance explicitly. Retain one batch snapshot and one lazy `_IdeaContext` per record list; unknown evidence stays live. |
| `store/sync/` | Explicit three-way identity movements distinguish incoming display, pull, push, promote and demote from their publication policy. | Another flat sync kind extends existing policy. Preserve whole identity scopes, strict preflight before effects, world-first push, owned-marker restoration and revision bumps on partial failure. |
| `store/assets/` | A logical image name resolves a placement first, then legacy bytes while an object is unavailable. Metadata, low-level placement changes, recovery, old-temp rescue, writes and version views form layers. | Another art operation reuses complete name/sidecar stripe sets. Preserve publish-before-cleanup, exact file identity checks, same-read cache tokens and idempotent journal roll-forward. |
| `store/world_bundle/` | Exchange packs a world without merging identities. Export snapshots, archive validation, image containment and staged import are separate responsibilities. | Another bundle format extends shared validation rules. Keep preflight before staging effects, local image rehashing, containment before publish, and metadata merge after successful publication. |

## Derived data and maintenance

| Package | Theory and responsibilities | Extension and watch point |
| --- | --- | --- |
| `store/compiled/` | Optional SQLite sessions cache validated derivations. One runtime owns sessions/fork state; one `Batch` stages a request; markers and intents fence durable maintenance. | Another derived kind extends existing contracts and recipes. SQLite is imported only by `_sqlite_support`; keep opening/recovery together, bounded SQL, ownership proofs, deletion intents and descriptor cleanup. |
| `store/cache_sync/` | One warm-up lane expands bounded live paths and executes a fixed file/overview/search-document/vector hook order against one endpoint context. | Another hook requires an explicit contract and provenance review. Keep shared registries/client, root/cancel fences, and one context factory; a new package leaf does not inherit network authority. |
| `store/usage/` | Append-only call rows feed independent billed, modelled and estimated money columns through one rate snapshot and fold. | Another report reuses `_folding`, not a new accounting rule. Preserve absent counts, unpriced versus unmetered distinctions, billed-only budgets and the one session clock/memo. |
| `store/image_migration/` | Dry-run planning and execution share one inventory/hash/group plan. Execution revalidates, publishes and verifies before exact legacy cleanup and metadata folding. | Another legacy surface extends planning and execution deliberately. Pending work remains a GC root before ingest and until verified completion; preserve root fences, compare/delete/restore and finally bumps. |
| `store/image_gc/` | Collection needs a complete fresh reachability view and a single-use device/root/age-bound token. Outbox publication commits disposal before cleanup; OS bin calls run outside store locks. | Another root source extends `_roots`. Unreadable or unarrived data holds relevant blobs; retain refreshed batch/object checks, no-clobber moves, recoverable outbox and cancellation fences. |
| `store/logs/` | Structured monthly logs support bounded historical queries and a byte-cursor tail. Row vocabulary, recording and reading have separate owners. | Another log query uses the same window/tail limits. Recording owns all guards, byte accounting, threshold, reentrancy and fork recovery; only Grimoire loggers cross the privacy boundary. |
| `store/locks/` | Policy declares campaign writer classifications; runtime owns lock selection, registries and sorted holds. `proclock` implements OS locks. | Classify a new writer in `_domain`. Extend lock behavior in `_runtime`, preserving one registry, process replacement, sorted whole-set holds and nonblocking nested repair. |

## Captured inference and review subsystems

| Package | Theory and responsibilities | Extension and watch point |
| --- | --- | --- |
| `store/continuity/reconcile/` | Discovery proposes findings; generation-fenced persistence writes only the candidate cache. Readouts, answers and decision payloads sit beside those responsibilities. | Another sweep signal extends discovery without granting reviewed-state writes. Keep the per-run embedding budget and the entire campaign hold that filters and commits a generation. |
| `store/inference/resolve/` | A captured settings/target snapshot is judged by capability policy to resolve an executable selection or a specific refusal. | Another capability belongs in policy/resolution, not an LLM call. Preserve refusal precedence, exact snapshot reuse and the one legacy overlay reader. |
| `store/inference/migrate/` | Migration execution persists the legacy planner's answer under the existing migration/archive protocol. Tracking owns activity, queue and progress leases. | Another migration step stays inside the existing run and campaign boundaries. Keep root-keyed activity, archive ownership, caller-held campaign semantics and format-marker-last publication. |
| `store/inference/legacy_plan/` | Pure planning interprets legacy settings into mapped fields and virtual reasoning presets, while readers retain their existing seeding semantics. | Another legacy field extends mapping/planning once. Keep the same virtual-preset dictionary and reader closures; settled scopes read nothing and strict failures cannot become absent settings. |
| `store/context/epistemic/` | One source sweep classifies visibility; failures narrow actor evidence. Records encode and restore that frozen view without reading current state. | Another visibility rule extends classification with explicit precedence. Keep the whole best-effort snapshot, the `_READ` default identity and the independent codec. |
| `store/absorb/materializer/` | Parsed extraction becomes a reviewable diff using one shared ID assignment. Continuity staging is distinct from section orchestration and record identity. | Another section extends existing staging vocabulary. Preserve unreadable versus empty continuity and both identity-failure barriers; importing continuity identity here would create a cycle. |

## Limits of this change

The restructuring does not strengthen optimistic filesystem guarantees,
cross-device synchronization, cancellation semantics or plugin behavior. Those
are existing contracts, including their documented gaps. POSIX fork and Android
APK execution need their own platforms; local Windows tests cannot establish
those outcomes.

Large test modules and TypeScript components are separate work. Individual
functions/classes such as `RunRegistry` and `Batch` remain cohesive units even
when substantial. Future changes should follow the responsibility and state
boundaries above, not an arbitrary line quota. Mechanics II and the future B+
quality gate remain deferred.
