# Context Inspector

Pre-flight, live counterpart to the post-hoc "what did the model see?" view
in `16-observability.md`. Lets the user see what the next turn's prompt will
look like given current state and the draft input, while typing.

## Scope

- `ContextInspector` protocol on the Context Builder: `preview`, `explain`,
  `pin`, `exclude`, `clear_pin`, `diff`.
- `ContextPreview` payload: per-tier message lists, sources (scope + owner
  + version), per-tier token budgets, proposed rolls, warnings.
- Inclusion-reason annotations on every assembled chunk. Canonical
  vocabulary: `present_in_scene`, `mentioned_in_recent_posts`,
  `commitment_open_to_pc`, `keyword_triggered`, `relationship_to_present`,
  `pinned_by_user`, `scene_anchor`, `mechanics_relevant`,
  `style_guide_active`, `pc_card`, `composition_default`. Reasons compose.
- User overrides — `pin` and `exclude` with a TTL in turns. Stored as
  campaign-local SQLite rows (`context_pins`), audited via `context_pin_*`
  deltas. No prompt reordering, no inline editing, no template overrides.
- Diff between a preview and a prior turn or another preview: entities
  added/removed, tier budget shifts, source-version changes, rolls deltas.
- UI affordances in the play view: token bars per tier, click-through to
  inclusion reason, source attribution per chunk, debounced live update on
  draft input, one-click pin/exclude with TTL selector, diff toggle against
  the previous turn.

## Constraints

- Inspector is a read interface over deterministic assembly + a small set
  of audited overrides. Out of scope: editing prompt fragments inline,
  reordering tiers, runtime prompt templates.
- Previews are not persisted across sessions; `PreviewHandle` is
  session-scoped. Persistent comparisons happen against turn ids.
- Configuration: debounce ms, auto-preview-on-input, default pin / exclude
  TTL, warning toggles.
