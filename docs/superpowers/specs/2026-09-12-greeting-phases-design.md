# Greeting phases and bounded recommendations

Status: approved for implementation, 2026-09-12.

## Objective

Let authors organize shared world greetings into story phases while keeping every greeting manually startable. Campaign suggestions should avoid jumping ahead in a story arc.

## Storage

Greeting frontmatter gains three optional fields:

- `phase`: an author-defined string identifying a story phase.
- `sequence`: a positive integer used for stable author ordering.
- `optional`: a boolean identifying a greeting that may be suggested alongside the current phase's main path.

The existing `present` list remains the participant index. A shared greeting may leave `character` and `version` empty; no primary participant is required. Existing greetings read with an empty phase, no sequence, and `optional: false`.

## Recommendation contract

Availability continues to mean that a greeting may be started. Recommendations are a narrower presentation hint and never prevent manual selection.

Given the chooser's `after` scene and the played set, recommend:

1. unplayed direct `leads_to` successors of played greetings that pass existing availability checks; and
2. every unplayed, available, optional greeting in the current phase.

The current phase is the phase of the greeting that opened the `after` scene. If that scene has no greeting, or its greeting has no phase, no optional siblings are inferred. Optional siblings may fall before or after that greeting's sequence. Do not recommend grandchildren or greetings from later phases merely because their ordinary prerequisite rules make them available.

Direct successors sort first. Optional siblings follow in sequence order, then natural name order. A greeting qualifying through both rules appears once as a direct successor.

With no `after` greeting, ordinary root greetings retain their existing availability but nothing is marked recommended. Choosing any greeting establishes its phase for the next scene.

## Authoring and display

The greeting editor exposes Phase, Sequence and Optional fields and persists them through world and campaign routes. Greeting lists may show and group phase metadata without duplicating the underlying greeting.

## Compatibility

No migration is required. Sync, overlay and bundle paths preserve the frontmatter fields as part of the greeting file. Existing clients may ignore the added response properties.

## Validation

Backend tests cover metadata create/read/update compatibility and recommendation boundaries, ordering, deduplication, skipped/played/tag-gated greetings, and absent phase metadata. Route tests cover world and campaign round trips. Frontend tests cover editing the three fields and rendering recommendation state.

Private library names and content do not appear in source, tests, documentation or commit messages.

