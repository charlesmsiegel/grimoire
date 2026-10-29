# Grimoire V2 — architecture charter

**Status:** exploratory, first-slice contracts only. This document owns architectural intent, not a final persistent schema or blanket migration approval.

## The conceptual model

1. **Content sources and worlds.** A world can own its characters and setting directly. It may optionally import catalogs from other sources. The **source-qualified identity** of a record is not a display name or an import-order slot. Alternative portrayals and adaptations are explicit relations, not string-matched duplicates.
2. **Character definitions and authored portrayals.** Definitions carry reusable authored identity and characterization; portrayals carry alternate interpretations or eras. Character Card V2/V3/CHARX are future *interchange adapters*, never mandatory runtime ontologies.
3. **Campaign actors.** A campaign owns its particular individuals, each with a stable **instance identity** and optional link to a source definition and portrayal. Multiple actors may intentionally reference the same definition. No world definition owns all its campaign histories.
4. **Controller assignments and permissions.** A PC is an actor assigned human decision authority in a campaign; an NPC is not a distinct ontological type. Controller, narrator, reader and director permissions must not be conflated.
5. **Events, observations and beliefs.** Objective events are not automatically known. Observations describe what a particular actor could perceive through a channel; beliefs are tentative interpretations and can be false. The canonical event and each subjective record have separate provenance.
6. **Ephemeral actor perspective.** At a given point in a scene, derive the actor's relevant identity, goals, observations, remembered evidence and beliefs; do **not** copy the entire transcript or objective director knowledge into every NPC. The current `actor_view` is a tiny proof of this boundary, not a production context builder.
7. **Actions and authoritative outcomes.** Human/AI actors propose actions under explicit authority. An adjudicator determines legal/fiction-first consequences; guarded writers commit effects once; narrators express that result without changing it. Scene wrap-up consolidates evidence rather than reapplying consumed items.

## Provisional human-editable storage layout

The exact paths, revision spelling and manifests are **not implemented or settled**:

```text
library/
  catalogs/             # optional reusable content
  worlds/<world>/       # independently usable setting, authored records
  campaigns/<campaign>/
    actors/             # instances and local overrides, NOT world cards
    scenes/             # accepted dialogue / authorial contributions
    events/             # accepted causal events, provenance and outcomes
    knowledge/          # audience-scoped observation and belief records
```

World-authored root characters remain world-owned unless explicitly published to a shared catalog. Campaign histories and beliefs are never nested in a giant root character file. Derived prompt caches, embeddings or SQLite indexes may accelerate reads but are not secret canonical state.

## Source updates and selective merging

Source, instance, common-ancestor revision and explicit overrides are different facts. A campaign may **follow compatible changes, review/merge, pin, or detach**; none may silently reinterpret previously accepted scenes. A three-way merge needs the actual common-ancestor data, not only a hash. Promote selected campaign-origin changes upstream only by explicit review, never by incidental scene generation. Absent source files must remain unresolved rather than rebind to similarly named replacements.

## Scenario the architecture must eventually prove

Two campaign instances reference the same reusable character. Only the instance in Campaign A witnesses a secret and develops a different relationship. The world character changes later. Campaign A pins its prior portrayal, Campaign B accepts a compatible update, and an explicit merge can bring one authored biographical correction to A without importing B's memories or overwriting A's history. Separately, one of the actors switches from NPC to human control without changing their identity. A private telepathic message reaches only its recipients; narration follows one accepted action outcome. Every step is traceable and recoverable.

## Constraints and intentionally unsettled choices

- Respect source and actor IDs rather than display names.
- Respect player control, known evidence, private channels and rules outcomes; no invisible LLM state writes.
- Human-editable files remain canonical; locks and failure recovery become explicit before stateful gameplay.
- Preserve source- and campaign-specific historical snapshots sufficiently for forks, retcons and reproducible prompts.
- **Still open:** exact on-disk format, source DAG limits, event-record granularity, belief persistence, version pin semantics, merge UX, default inheritance, multi-device conflict resolution and ruleset execution interface.
- Prefer one playable vertical slice over a universal content graph or all-encompassing event framework.
