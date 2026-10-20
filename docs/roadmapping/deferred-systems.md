# Architecture-dependent backlog — transferred product goals

**Status: ACTIVE/OPTIONAL REQUIREMENTS, NOT SHIPPED.** Seven architecture-dependent trackers were previously retired; **#93 and #151 have now also been moved out of the immediate backlog**. #151 is a discretionary feature whose value is not yet established. See [backlog-discretionary.md](backlog-discretionary.md).
**Assessed 2026-10-15** from nine existing issues. All nine are now tracked on the long-term roadmap rather than the current GitHub implementation backlog. Although #93 and #151 have technically viable initial slices, the author prefers to defer #93 pending campaign storage choices and reassess whether #151 deserves implementation at all.
**Original full issue bodies:** [archived-issue-text.md](archived-issue-text.md). Old issue assumptions and suggested code paths may be obsolete; no historical implementation option is automatically adopted.

The intent is to **keep the pre-cleanup GitHub backlog focused on work the author actually intends to land now**. Feasible does not mean high-priority: the user explicitly opted to defer #93 and reconsider #151. No implementation is claimed.

## 1. Cross-world character identities and variants — #50

Source: [#50 — Character variants across worlds, selected per campaign](https://github.com/charlesmsiegel/grimoire/issues/50)

**Player goal:** Reuse a character across multiple worlds/settings while permitting distinct world-specific or campaign-specific portrayals, character versions, art and continuity without re-importing every card by hand.

**Requirements**

- A character has a stable originating identity/provenance distinguishable from display name; *similar names do not automatically imply identical characters*.
- Explicitly associate variant portrayals with a source character when the author intends that relationship. Cross-world variants need not share a universal fictional ontology.
- Worlds can specialize a character definition; campaigns can select the appropriate portrayal/version, maintaining their own locked versions, scenes, relationships, images and mutable state.
- Preserve card metadata, all relevant versioned assets, localized image references, sidecars and provenance. Source removal or updates must not silently replace one portrayal with another.
- Give a discoverable author/player UI for finding related variants, selecting a version, and understanding which world or collection owns a record.
- Distinguish **copy with lineage**, **referenced reusable definition + explicit variant**, and **fork of a setting**. All three were explored in #50; the forthcoming source-aware resolver should decide when each is appropriate.
- Never require cross-world character reuse just to represent different versions within the same world; existing per-character/per-campaign version selection should be reused.

**Must be informed by:** [Content Architecture Study](content-architecture-study.md) (World View, qualified identities, source provenance, override vs variant vs distinct identity), Content Composition, asset resolution and campaign COW/synchronization. Avoid implementing #50's older copy-only suggestion as a temporary substitute for a decision we're currently making.

## 2. Spatial world graph and map — #42 and #43

Sources: [#42 — Spatial queries over locations](https://github.com/charlesmsiegel/grimoire/issues/42), [#43 — Map view of locations](https://github.com/charlesmsiegel/grimoire/issues/43)

**Player/author goal:** Locations have meaningful connections and optional visual positions, supporting believable travel/navigation, nearby-place retrieval and authoring maps.

**Graph/data requirements (#42)**

- Represent location-to-location connections using stable source-aware references; do not assume every location came from the local world or that identical short IDs/names mean the same place.
- Support deterministic **adjacency/neighbor**, **path-between** (e.g. shortest unweighted hop route), and **within-depth** queries, with explicit semantics for symmetric versus directed links.
- Local/world/campaign location differences must be respected; graph queries see the **effective world** plus campaign overrides rather than raw single-root files.
- Missing/deleted/uninstalled dependency locations must not cause an accidental connection to another same-named record; report or safely omit dangling edges.
- Keep authoring data in editable Markdown/frontmatter or the project's existing editable JSON conventions. Coordinates/distances are optional additions to topology, not substitutes for it.
- Potential consumers include travel opportunities, scene suggestions, location-aware context and later weather/movement; retrieval must remain bounded.

**Visual/authoring requirements (#43)**

- Show location nodes with names and optional portraits/avatars, plus relationships/connections and reasonable layout when no coordinates are available.
- Permit author-arranged coordinates (and eventually optionally image-backed fantasy/world maps), clickable navigation, selection and normal editor operations.
- UI scope follows the world/campaign effective map, with edits to world-owned or campaign-local records routed to the correct source rather than an imported dependency by accident.
- A simple adjacency/detail view is an acceptable first slice; interactive SVG or image pins are later alternatives, not preselected.
- The original issue suggested SVG and optional `x/y` fields; retain the idea but revisit the data contract after World Resolution/Content Identity decisions.

**Dependencies:** World Resolution + Content Identity + Content Composition; the location graph precedes map visualizations. If a minimal world-local map is independently useful, it could land earlier *only* with a migration path to qualified refs.

## 3. Library activity and affected campaigns — #55

Source: [#55 — Library activity feed](https://github.com/charlesmsiegel/grimoire/issues/55)

**Player/author goal:** Know what changed across authored worlds and reusable content, who/what changed it, and which dependent campaigns may be affected.

**Requirements**

- Recent record creation/edits/deletions, significant content refreshes, world/library imports and relevant campaign-facing changes must be discoverable in an activity view.
- Show links to affected records and, where determinable, campaigns dependent on them (including divergence/sync state: unaffected, live-inherited, pending update, conflict).
- Distinguish changes observed through application writes from **external editor / Claude / Codex / synced-file changes**. A write-path-only log misses the latter; detection/reconciliation semantics must be explicit.
- Never equate a filesystem mtime with a canonical content revision when false positives could mislead users; respect source hashes, provenance, and known cache/restore behavior.
- Design with the future **source-qualified World View** and **Change Sets** so the feed doesn't assume one authored world is the sole parent of every record.
- Records, deletion history and provenance should remain file-backed authority; a derived SQLite/FTS view may accelerate display but must not become the sole source of irreversible user history.
- Avoid bulk touching old entity Markdown files merely to add timestamps: doing so can trigger false sync changes.

**Earlier options in issue:** append-only per-world activity log; derived mtimes; or hybrid. Evaluate alongside provenance/change-set operation logs, not as a detached new truth source. This feature supports **World Maintenance**, **Content Ecosystem**, **Context & Reliability**, and **Engineering Observability**.

## 4. Copy/import a scene across campaigns — #93 (deferred)

Source: [#93 — Copy a scene from another campaign](https://github.com/charlesmsiegel/grimoire/issues/93)

**Player goal:** Reuse a scene/transcript from another campaign while making it coherent with the destination's characters, versions, history and world.

**Requirements**

- Import into a **new destination scene ID**, respecting the destination campaign's chronology and number allocation, without overwriting or aliasing the source.
- Reconcile cast members by explicit actor identity and their source/world relations—not display name alone. Missing actors, world differences and conflicting character-version locks require visible resolution or refusal.
- Preserve or appropriately rewrite local asset and image references; do not leave URLs pointing into a vanished source campaign.
- Decide what copies with a scene: just transcript, transcript + cast, or selected continuity facts/plot/relationships with review. **Do not silently transplant source campaign continuity as destination truth.**
- Optionally rerun the existing extraction/absorb process in the destination to produce *new* reviewed state rather than copying incompatible source facts.
- Account for transcript-provenance references from journals, chronicle, memory, threads, commitments, relationships, mechanics outcomes, and other state; preservation/repoint semantics may differ per kind.
- Support dry-run/preflight, detected collisions, safe cancellation and recovery from a partially attempted import. Preserve source campaign integrity.
- Avoid a second inconsistent scene-copy algorithm beside existing branch/fork/scene-ref APIs.

**Roadmap posture:** #93 is **deferred** despite the existence of #92 scene import and #72 campaign fork. The author wants to revisit campaign-storage types and identity/provenance first. The same-world-only transcript/cast copy remains an optional narrow slice later; cross-world copy requires Content Identity/World Resolution, Change Sets and migration/transaction guarantees.

## 5. NPC/group time-skip simulation — #102

Source: [#102 — Tick NPC and group state forward over a time skip](https://github.com/charlesmsiegel/grimoire/issues/102)

**Player goal:** Advancing significant time makes NPCs, organizations and open situations progress believably without pretending nothing happened offscreen.

**Requirements**

- Use a real elapsed-time/time-skip signal, campaign calendar and current NPC/group/world state; deterministic clocks, agendas, queued tasks and elapsed-time changes should be computed explicitly.
- Propose or evaluate offscreen NPC/group developments with optional inference **only where judgment is needed**; avoid compulsory full-agent simulation for every NPC and every trivial skip.
- Respect knowledge/perspective, agency, campaign tuning and mechanics when selected, but remain usable in fiction-only roleplay.
- Produce coherent consequences: relationships, group commitments, resources, open plots, characters' current state, timeline/events; don't silently change canonical world definitions.
- Provide audit trail, idempotency, review and conflict handling. Updates should not be committed as arbitrary LLM text or a silent background sweep.
- An optional PC-absent/director scene or reviewable "meanwhile" result is a valid mode. An entirely deterministic stale-state report is a valid fallback.
- Ensure large-world performance: relevance/pacing determines which entities are simulated; updates never multiply uncontrollably with roster size.

**Dependencies:** Time Management/Calendar, World Simulation, NPC Autonomy/Faction Dynamics, State Transactions and Agentic Workers/Change Sets. Reuse the existing continuity/absorb/editor surface but do not adopt the issue's outdated "OpenRouter-only" assumption.

## 6. Long-activity advancement and outcome resolution — #108

Source: [#108 — Advance to the end of a long activity and resolve it](https://github.com/charlesmsiegel/grimoire/issues/108)

**Player goal:** Say "spend weeks traveling, researching, training, crafting, recovering" and have the campaign arrive at the appropriate time with results and consequences.

**Requirements**

- Accept open-ended activity intent and optionally estimate duration; present the duration/assumptions for review or allow a user-supplied time target.
- Advance the campaign clock through deterministic calendar/time mechanics. Identify holidays, intervening events, resource consumption, completion conditions, or clocks that the activity crosses.
- Resolve the activity through the selected mechanics when relevant (rules, rolls, required actions, progress clocks); in mechanics-free play derive an explicitly reviewed fiction-first outcome instead.
- Apply the consequences coherently to campaign-owned state through a recoverable, idempotent transaction/change-set path, with enough provenance for history and later retcon.
- Coordinate, rather than double-count, offscreen NPC/group developments from #102 for the same elapsed period.
- The user may choose an explicit played-out scene instead of a compressed resolution. No unapproved model result may overwrite campaign or world truth.
- Cost and latency should be proportional to activity complexity, avoiding unnecessary speculative inference.

**Original alternative designs:** three steps (estimate → advance → resolve), one combined model call, or manual duration + resolve. Leave open pending architecture. **Dependencies:** Time Management, Mechanics II Action/Effect and clocks, World Simulation, Consequences, Campaign Tuning, State Transactions/Change Sets.

## 7. Historical turn replay, comparison and fork — #151 (optional / reassess)

Source: [#151 — Replay turn through gateway, with fork and seed](https://github.com/charlesmsiegel/grimoire/issues/151)

**Player/developer goal:** Reproduce or compare how an LLM handled a prior turn, and optionally explore an alternate continuation safely.

**Requirements**

- Replay using the **captured historical input** and effective routing/parameters available at the time, not silently reconstructing from a world that has since changed; mark missing old evidence honestly.
- Replay should be **ephemeral by default**: no mutation of the original scene/transcript, world or continuity. Compare stored response with replayed output and capture model/routing/cost differences.
- Support an explicit alternative model or settings for A/B comparison; persist replay details only with consent/clear audit semantics.
- Forward/store an optional supported sampling seed on a best-effort basis; upstream model/provider determinism is *not guaranteed* merely because a seed is supplied.
- Adopting an alternate should go through a campaign/scene **fork** or explicit reviewed change, never overwrite established history.
- Historical transcripts, prompt content, knowledge privacy and usage traces must respect existing security/access scopes.
- Reuse inference routing, prompt capture, generation budgets and model-evaluation infrastructure; do not build a second model gateway.

**Roadmap posture:** #151 is a **discretionary candidate, possibly not needed at all**. #150 prompt auditing/capture, #72 campaign forking and newer inference routing already provide some of the intended diagnostic ingredients. Revisit only if a concrete A/B replay use case remains unmet. Distinguish best-effort text regeneration from reproducible state transitions; do not implement an elaborate historical replay service simply because this old tracker existed.

## 8. Structured export filters and export history — #140

Source: [#140 — Export filters: strip OOC, anonymize, consolidate POV, history](https://github.com/charlesmsiegel/grimoire/issues/140)

**Player goal:** Export a polished, shareable campaign artifact with configurable omission/privacy/POV behavior and a record of what was exported.

**Requirements**

- Support a shared export-content representation with per-format rendering rather than duplicating parsing/transforms for EPUB, Markdown, HTML, text or other supported formats.
- **OOC/mechanics filtering:** remove clearly labeled out-of-character and mechanical content **only when authoring/transcript metadata identifies it**, not via brittle text guessing. Its full behavior depends on future mechanics/state representations and any OOC-markup convention.
- **Anonymization:** stable per-export aliases for characters/players, covering speaker labels and handling prose mentions with deliberate false-positive/privacy policies. It should not mutate authoritative names.
- **POV consolidation:** define how scene-level narrator viewpoint metadata governs chapter grouping/organization; do not invent POV information where absent.
- **Export history:** record successful export format, chosen filters, time, and artifact metadata in human-editable campaign authority where useful; distinguish past export history from generated files.
- Filters should be pure transforms of snapshots; never rewrite the campaign and never silently publish secrets.
- Provide UI/CLI settings and previews as appropriate; record exactly what was included or stripped.
- A standalone anonymization feature is a viable earlier slice, but **the complete issue is dependent on future mechanics conventions, POV metadata and shared export architecture**. Closing its old tracker does not drop that near-term option.

**Dependencies:** Presentation, Campaign Management, POV/context, Mechanics II and the export pipeline. Revisit old #139/#86 relationships against current main rather than assuming historical issue numbers reflect outstanding code.

---

## Planning and implementation constraints

- This document preserves **product requirements**, not the dated "current state" or preferred options from the original issues.
- **Core first:** World Resolution, Content Identity, clean campaign/world editing and mechanics extensibility are architectural prerequisites for some—but not all—of these goals.
- **File authority:** Markdown and existing editable JSON stay the source of truth; derived databases/caches are optional.
- **Mechanics independence:** Fictional world content and characters must remain usable with no selected mechanics module.
- **Reviewability:** User-approved or policy-authorized changes land through deterministic, audited store mutators; LLM prose alone does not commit state.
- **No scope collision:** A campaign-local decision cannot modify the imported library/global world unintentionally.
- **Compatibility:** Existing worlds, campaigns, actor assets, synched folders, scene history, and Android must retain their current guarantees or have a reviewed migration path.

## Traceability and disposition

| GitHub issue | New roadmap owner | Reason it should not be implemented in the pre-cleanup backlog wave |
|---|---|---|
| [#50](https://github.com/charlesmsiegel/grimoire/issues/50) | World Resolution / Content Identity | Cross-world variants depend on identity and source provenance |
| [#42](https://github.com/charlesmsiegel/grimoire/issues/42), [#43](https://github.com/charlesmsiegel/grimoire/issues/43) | World Resolution / Spatial World | Location refs and map views should consume effective worlds |
| [#55](https://github.com/charlesmsiegel/grimoire/issues/55) | World Maintenance / Change Sets / Observability | Activity and impact propagation need reliable source provenance |
| [#93](https://github.com/charlesmsiegel/grimoire/issues/93) **DEFERRED** | Campaign Management / Identity / Transactions | Campaign data/storage decisions should settle before scene transfer |
| [#102](https://github.com/charlesmsiegel/grimoire/issues/102) | World Simulation / Time | Needs coherent offscreen transitions, agendas and reviewable state |
| [#108](https://github.com/charlesmsiegel/grimoire/issues/108) | Time / Rules & Adjudication / Consequences | Activity resolution depends on future time/mechanics actions |
| [#151](https://github.com/charlesmsiegel/grimoire/issues/151) **OPTIONAL** | Evaluation / State / Campaign Forks | Reassess value against existing capture/fork/retry; may be declined |
| [#140](https://github.com/charlesmsiegel/grimoire/issues/140) | Campaign Export / Presentation | POV/mechanics-sensitive filtering requires future semantics |

**All nine original stand-alone issues have been transferred from GitHub's implementation backlog**, including #93 and the optional #151. Full historical text remains in [archived-issue-text.md](archived-issue-text.md). Their issue closure means the old tracker is superseded; it does not imply an implementation or, except if later explicitly decided, a feature rejection. **Do not close issues with linked open PRs; those are being implemented now.**
