# User Interface, Visual Storytelling and Accessibility

**Priority:** incremental improvements when validated by player experience. A visual-novel reader and outfit/emotion expression libraries already exist; do not rebuild them.

## Goal

Make the same Grimoire world, characters and scenes readable and controllable through accessible transcript, VN and editing views, without creating a separate simulation or canonical state for each presentation.

## Work to plan

- **Scene UI:** reduce navigation and writing friction; help players find cast, current time/location, relationship clues, optional resources and continuity controls while keeping ordinary mechanics-free play simple.
- **Visual Storytelling / VN extensions:** build on the existing historical visual-novel reader, versioned outfit/emotion expression libraries, sprite placement, dialogue highlights, background choices and presentation overrides. Only add missing improvements supported by testing: richer stage transitions, cue choice, pose/portrait fallbacks, legibility and navigation.
- **Expression fidelity:** emotion and outfit are structured, addressable assets rather than unrelated gallery images. Maintain origin, versions, inherited defaults and individual overrides through imported cards, forks and source changes.
- **HUD customization:** optionally let a campaign choose/reorder/show/hide cast, location, time, commitments, events and possible inventory/mechanics widgets, with sensible reset/default settings and graceful unsupported widget behavior.
- **Live context preview (optional):** display how an unsent draft might alter retrieval and token packing without writing the scene, paying for embeddings on every keystroke, or confusing an approximation with the actual effective prompt.
- **Presentation consistency:** character identity, speaking role, scene history, limited audiences, mood and visibility must be grounded in actual scene/campaign evidence. Never infer a historical audience member from today's cast.
- **Accessibility:** keyboard, screen-reader labeling, color/contrast, focus, small screens, reduced motion, text scale and sensible behavior for large casts/long histories.
- **Editable asset workflows:** expression imports, reviews, atlas/gallery browsing, picture scale/alignment and error/limit feedback can be enhanced without converting canonical art into UI-only state.

## Proving scenario

One scene with multiple NPCs and a hidden-information beat can be read in transcript and VN mode. An actor has two outfits and several expressions, another only a portrait fallback. The historical speaker/cast/audience remains correct; an edited outfit override never changes the written scene; keyboard and mobile access work. The user can find current holdings without having to build a customizable HUD.

## Dependencies and boundaries

[Content Composition](content-composition.md) and [World Resolution](world-resolution.md) influence inherited assets; [Creature → Character](creature-to-character.md) introduces new individual portrayals; [Inventory](inventory.md) supplies optional status; [Media](media.md) owns image/audio generation. The VN reader is an existing capability, **not** an unfinished foundational sprint.

## Open questions

- Which presentation gaps actually justify another VN iteration, versus keeping the current working reader?
- Are outfit/expression choices per-post overrides, deterministic cues, optional bounded inference, or some combination with clear auditability?
- What HUD layout and visibility settings should be campaign-syncable versus device-local?
- Can live draft-preview use existing context packing cheaply and truthfully, without costly speculative calls?
- What is the minimum tested accessibility standard for desktop, mobile and Android?
- When are custom backgrounds, transitions, animation or complex multi-sprite staging worth their performance and content burden?
