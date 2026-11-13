# Semantic continuity and retained scene forks

Headless first-useful slices of roadmap 049 and 054. Broad application controls belong to sequential 115. These services do not replace accepted-effect, relationship, holdings, transcript or actor-knowledge ownership.

## Provenance and correction interface

A `ContinuityRef(campaign_id, record_id)` names an immutable record at `campaigns/<campaign>/continuity/<record_id>.json`. Native storage, ordinary sync and authenticated transfer understand this family. A conflicting same ID is preserved for review; deletion is not propagated. These files are historical provenance and review archives, never active scene posts or actor memories.

New accepted effects and recovered adoptions record `extras.continuity_sources`: version 1, coverage `same_chat_turns` or `empty_limited`, and source entries containing canonical turn path, exact historical envelope etag, and semantic scene revision. Commit rechecks the same-chat turn listing and scene projections under the existing transaction lock. Effect completion retains the original provenance. Source revisions cover authored text, speakers, audiences, source versions and branch disposition; independent effect/label/call accounting is excluded.

Historical events without pins remain readable but cannot establish exact scene ancestry. Empty coverage is explicit, and previews do not invent a scene for an event created through a headless API. The present capture refuses beyond 1024 same-chat turns rather than truncate, and a malformed campaign turn scan blocks new acceptance even when another chat contains the malformed turn. Native record size limits also apply.

The pure impact API uses revision-pinned `Pin` and `Dependency` values and deterministic direct/transitive explanations. Recorded causal dependencies and potential chronology are distinguished. Missing/stale records, malformed declarations, cycles and bounds prevent a clean report. The graph is bounded to 2048 nodes and 8192 edges by default. Graph connectivity does not confer knowledge.

The `grimoire.continuity` package contains the pure graph and strict provenance/declaration values. Native scene serialization and historical archive envelope validation live in the low-level `grimoire.continuitywire` adapter: `continuitywire.projection.scene_revision` and `continuitywire.archive`. This boundary uses the existing native value codecs without invoking storage services or sync orchestration. The projection algorithm and persisted archive bytes are unchanged. The higher-level `continuitystore` archive exports remain available.

`ContinuityLibrary(store, handle, *, issued=None, owners=None)` requires a trusted owner handle issued by that library's bound authority. For an unbound store, the trusted application must supply the verified issuer and owner set; the handle cannot vouch for itself. Bound authority takes precedence over those fallback arguments. Every read rechecks the owner and requires `requested_by='director'`.

- `snapshot(campaign)` returns native pins, dependencies, record metadata and explicit problems, with `coverage='recorded_dependencies_only'`.
- `preview(campaign, roots, action)` accepts revision-pinned roots and one of `edit`, `cut`, `replace`, `replay`, or `fork`.
- `rehearse(campaign, roots, action, alternatives)` adds one to eight authored alternatives, at most 16,384 characters each. It reports zero model cost and remains speculative.
- `declare_dependency(record_id, source, dependent, reason)` validates current same-campaign endpoints under the store lock and appends a create-only `grimoire.continuity-dependency/1` record. Stale declarations do not silently rebind.
- `corrections(campaign, roots, action='edit')` exports pinned source/dependent, reason, certainty and explanatory chain for 053. Incomplete previews refuse export. This interface performs no consolidation or repair.

Turns use semantic scene revisions; other native records use exact envelope etags. Native edges include recorded scene inputs to accepted events, accepted receipts to relationships and possession, counted holdings, event citations to summaries, and explicit knowledge evidence/proposition/ruling/belief references. Acceptance ordering is only potential dependence. Explicit turn `follows` links and conservative legacy turn-number ordering also expose potential downstream scene impacts; they do not prove that later dialogue used an earlier fact. Explicit declarations cover dialogue, causal actions and assets that the native records do not establish. No prose inference or exhaustive dependency discovery is claimed.

## Offline review and retained forks

Ordinary and authenticated peer sync run the same semantic guard. `grimoire.sync.semantic.reviews(plan)` exposes immutable campaign review records containing scene paths, affected or conservatively held endpoint paths, reason codes and a token; it does not expose scene text. The token and locked apply checks bind the complete canonical campaign listing on both devices, including declared dependency endpoints outside the event/scene families. A stale endpoint, malformed provenance, or unsupported continuity schema cannot produce a clean review. Unreadable or uninspectable declaration input conservatively holds possible native endpoints; reason codes identify that uncertainty rather than claiming a recorded causal link.

A changed source scene holds the scene and whole accepted-event ledger together. Stale or missing declared endpoints are held explicitly, including campaign images, instance placements and campaign artwork. Campaign asset copies and deletions recheck the complete campaign premises locally and on authenticated serving peers, so a declaration added after planning blocks a stale asset mutation. Direct editorial relationship changes without event receipts are conservatively classified as potential impact and are held with changed agreed scenes. Generic turn, relation, campaign asset and ledger conflict resolvers cannot bypass these checks. Unrelated supported changes continue through their existing owners. Peer serving independently checks the proposed transition and refuses missing semantic preconditions.

`resolve_fork(left, right, campaign, operation_id, token, source_device, handles, owners)` operates on two locally open libraries with their actual durable authorities. Handles and configured owner sets are keyed by device identity. It accepts a current semantic review token and a source device whose scene variants satisfy every recorded dependency. Uncertain history, contradictory pins, absent variants, incompatible effect/label/call bookkeeping, unrelated divergent campaign records and divergent offline continuations refuse restoration.

The operation stores the same immutable `grimoire.continuity-fork/1` receipt on both devices. It contains exact UTF-8 original scene envelopes, before/after etags, source selection, device identities, original campaign premises, a fingerprint, and the expected campaign listing after ordinary ledger reconciliation and already-planned unrelated deletions. Each device publishes its receipt and scene restoration atomically; both transactions are permission-checked before either commits. The two-device operation can be interrupted between those transactions. Retrying the same operation identity resumes only from recorded states, and changed causal premises refuse.

The result is `ForkResult(receipt, status)`, with `pending_sync` until ordinary sync reconciles accepted effects. An unchanged retry after that exact reconciliation returns `already_applied`. The resolver never writes accepted events, relationship receipts or holdings. Complex histories requiring a separate ledger decision remain refused.

`ContinuityLibrary.inspect_fork(campaign, operation_id)` returns the validated archive to the library owner, with the same director-only boundary as previews. Archives remain historical: their text does not become active graph nodes, actor knowledge or a continuation. The strict decoder checks the whole record; recognizing the schema name alone never bypasses review. Current limits are 256 retained scenes, 8192 premise entries per listing, and a 6 MiB archive. The limits refuse rather than truncate.

An old scene edit and a dependent relationship/event history are reviewed together. Accepted events continue to be reconciled by the existing ledger owner and receipt verifier. The continuity layer never reapplies an effect. An inactive retained fork is a historical alternative; selecting it does not promote its text or effects into current canon.

## Read-only retcon previews

Edits, cuts, replacements, replays and forks can be previewed against native provenance. A bounded authored rehearsal is speculative, model-free, and write-free. It does not simulate a guaranteed alternate future. Neither previews nor archives enter actor context or private memory retrieval.

## Boundaries

Explicit dependency declarations supplement recorded provenance; no automatic prose claim extraction or exhaustive semantic understanding is claimed. Automatic downstream state repair, rollback of arbitrary accepted-effect histories, model-generated continuations, remote fork-resolution UI, long-period disconnected retcon convergence and 053 memory consolidation remain separate work. Current APIs expose pinned correction references so consolidation can preserve obsolete and corrected sources without treating a preview as canon.

## Executable evidence

Synthetic tests live in `test_continuity.py`, `test_effect_continuity.py`, `test_continuitystore.py`, `test_continuity_storage.py`, `test_sync_semantic.py`, `test_peersync_semantic.py` and `test_continuity_forks.py`. They exercise the relationship-after-scene/offline-edit conflict, transferred-key downstream preview, director authority, malformed and stale inputs, generic resolution bypass attempts, local and serving-peer races, endpoint deletion, exact retained originals, interrupted/reopened resolution and no duplicate accepted effects. These are headless domain proofs; they do not establish broad campaign UI or physical-phone acceptance.
