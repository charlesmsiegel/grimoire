# Visual Novel & Expressions — delivered foundation and remaining opportunities

**Status (2026-10-26): substantial core DELIVERED on `main`; future Visual Storytelling work is NOT automatically committed.**
**This is a roadmap reconciliation document**, not a code spec and not a claim that all possible VN features are complete.

## Why this belongs on the roadmap

In the October 2026 design discussion, VN mode was explicitly intended as **an optional presentation overlay on Grimoire's ordinary roleplay**, not a separate engine, script format, game rule system, or alternative story authority. Character expressions were to be **first-class character assets**, not undifferentiated gallery images. The logical author-facing asset organization was **`expressions/<outfit>/<emotion>.png`**, with outfit and emotion meaningful independently of file layout.

The engine should continue to own characters, campaign/world content, scene posts, authoring, histories, state and mechanics. Players can view the same underlying play as chat/transcript or as a visual-novel-style stage; neither visualization is the source of truth. A presentation renderer must not imply that a character was present, speaking, or aware of a post if the stored history says otherwise.

## Delivered on main — don't schedule as an unfinished sprint

[PR #554](https://github.com/charlesmsiegel/grimoire/pull/554) was merged October 23, 2026, delivering:

- **Actor expression libraries** for characters and PCs, version-scoped; outfits, emotions, transparent sprite artwork, scale/alignment, inherited art behavior, editing, validated pack import/export and native archive integration.
- **Historical VN scene reading** with persistent transcript/post identities, historical cast and location context, audience distinctions, speaker highlighting, tracker mood, backgrounds, fallbacks, navigation, autoplay and a saved reading position.
- **Presentation controls** for each post's outfit, expression, visibility and background, with revision/scene guards, reset and branch isolation.
- **Responsive desktop/mobile rendering** and evidence via [PR screenshots](https://github.com/charlesmsiegel/grimoire/tree/main/docs/screenshots/pr-554).

Subsequent `main` commits, October 24–26, refined sentence-paced reading, inset dialogue layout, optional PC sprites, post replies, controls, expression/outfit counts, missing/legacy art fallbacks, decision-selected expressions and quote highlighting. Do **not** open a second sprint merely to rebuild those shipped behaviors.

**Implementation references as of the audit:** `backend/src/grimoire/store/expressions.py`, `backend/src/grimoire/store/vn/`, `backend/src/grimoire/routes/expressions/`, `backend/src/grimoire/routes/vn.py`, `frontend/src/components/vn/`, `frontend/src/components/expressions/`, `frontend/src/routes/VisualNovelView.tsx`.

The physical storage may use content-addressed blobs and metadata manifests. The logical `outfit/emotion` concept is essential; do **not** rewrite the image store simply to force the original proposed folder layout onto its physical implementation. Keep all authored metadata/manifest facts in Grimoire's file-backed authority, with derived indices disposable.

## Product contract to preserve as the feature evolves

- **One underlying scene, two presentations.** Play content, post identity, versions, cast visibility, recorded choices and scene state are not forked just because a reader switches presentation.
- **Expression ≠ generic gallery art.** Emotions/outfits are named, addressable, version-associated assets; the sprite renderer uses the logical expression set. Normal portrait/gallery fallback is allowed when no expression exists.
- **Inheritance until overridden.** Respect existing world/campaign and actor/version art inheritance, replacements and provenance. An override should never silently mutate the source image or another character.
- **Multi-actor composition.** More than one sprite can appear, with a legible speaker emphasis, stage arrangement, backgrounds and correct presence/absence; no inference that a non-audience character witnessed secret dialogue.
- **Historical determinism.** Replaying an older scene renders what the stored evidence actually supports; unavailable information is unknown, not filled in with the present roster.
- **Optional and cost-conscious.** Keep routine mechanics-free chat viable, avoid mandatory extra LLM passes for every dialogue beat, and make any expression-selection decision/presentation enrichment separately budgeted and inspectable.
- **Responsive, accessible and undoable.** Keyboard/mobile navigation, view preferences, safe persistence of presentation choices and a route back to plain chat. User overrides should have explicit reset and conflict behavior.

## Possible later work — design candidates, not approved sprint promises

These are **gaps/questions to review against the current app**, not assertions of missing features or an authorization to implement them:

1. **Stage state and presentation cues:** clarify whether outfits/emotions/positions are selected solely from saved per-post settings, deterministic decisions, or optional bounded inference. Keep an interpretable choice record, no silent continuity mutations.
2. **Reusable asset tooling:** richer import validation, expression preview/filtering, asset-set conversion from third-party character-expression packs, version lineage, and better image/VRAM/memory budgeting where a real user need appears.
3. **Presentation richness:** optional poses/animations/transitions or location illustrations *only* if meaningful to roleplay and platform performance. Do not automatically add a real-time animation framework.
4. **Usability and accessibility:** inspect actual desktop/mobile usage for readability, keyboard navigation, focus/contrast, long scene browsing and large multi-character stages before adding another UI-control layer.
5. **Authoring provenance and portability:** preserve expression libraries and per-scene overrides through campaign/world export, fork, backup, restore and future World Composition/qualified-source identity migrations.

The two historical presentation models discussed were **Ren'Py-like one-beat staging** and **an illustrated transcript/stage reader**. The shipped reader is the reference implementation; no other mode is silently required. Future UX variants should be grounded in usage rather than the old discussion alone.

## Acceptance rehearsal for any future VN enhancement

An existing multi-character scene, some posts with limited audiences, plays in both ordinary transcript and VN presentation. It uses a versioned actor expression library with two outfits; another actor has only a portrait fallback. Scene navigation and historical replay preserve the old cast and post identities, voice and mechanics-free content. Changing presentation overrides doesn't alter transcript or character facts; new asset imports don't overwrite unrelated inherited art; loading the same scene on desktop and mobile yields a usable stage.

## Roadmap relation

- **Presentation & Participation → Visual Novel & Expressions:** delivered core; continue only with validated additional player outcomes.
- **Visual Storytelling / Image Generation / Audio Ambience / Voice Roleplay:** independent broader candidates, not all delivered by the VN reader.
- **Content Identity / Content Composition:** future changes must maintain provenance and inheritance of expressions and presentation references.
- **Engineering Health / Test Hygiene:** regression and performance coverage for the now-shipped VN subsystem is part of ongoing engineering health, not a feature redo.

**Do not interpret this document as a new GitHub implementation issue.** The purpose is to prevent the entire October design discussion from disappearing while correctly crediting what has already landed.
