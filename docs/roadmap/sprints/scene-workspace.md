# Scene Workspace and Accessible Controls

> **V2 Wave 8 scope correction:** The new **Wave 7** sprints [108–114](../UI_COVERAGE.md) first deliver a fully usable shell, library, quick-start/play, reviews, media/sync and basic navigation. This sprint **extends** that shipped UI into a richer long-term workspace; it must not duplicate the new P0 foundation or reintroduce V1 APIs. See the [ordered roadmap](../README.md).


**Outcome:** Make chats discoverable and resumable from **Chats, Characters, Scenarios, Campaigns and Worlds**, while keeping reading, writing, reviewing and steering scenes fluid on desktop and mobile.

## Scope
- Reduce friction in cast navigation, scene switching, composer actions, editing and current status.
- Offer useful accessible inventory/time/location indicators without mandatory configurable HUD infrastructure.
- Support keyboard, screen reader, contrast, focus, font scaling and reduced motion.

## Milestones
1. Enhance the Wave 7 unified chat library with advanced saved filters, source/portrayal comparisons and efficient large-library browsing; preserve the stable chat IDs, drafts and scroll/focus already delivered by 108/109/111.
2. Offer optional compact status panels and reliable keyboard/touch workflows.
3. Add user-configurable layouts and workspaces with graceful defaults.

## Moonshot
An adaptive, accessible RPG cockpit rearranges itself to suit a live scene, long writing session or GM review, with no hidden gameplay state.

## Proving scenario
A keyboard-only player finds a missing item, drafts a correction and returns to narration without losing composer content.

## Independence and interfaces
Can improve current screens incrementally; voice control is a parallel input mode, not a dependency.

## Navigation invariants inherited from the quick-chat path

107 owns stable world-less play scope/chat associations and a rebuildable reverse index. 035 provides the **minimal** chat list, and 052 the one-click start surface. This sprint (Wave 8) owns the **mature browsing/workspace enhancements beyond Wave 7 parity**, not a second data model:

- Recent / All Chats lists the canonical chat IDs, even if no world or named campaign exists.
- Character and Scenario pages show linked chats by **exact pinned/qualified source reference**. Include a deliberate equivalent/derived-portrayal filter but never merge two same-named authors' records.
- Campaigns list grouped chat threads; Worlds show attached campaigns/chats. A chat with multiple characters/scenarios is listed in each appropriate view, **without copied transcripts or duplicate history**.
- Chat detail retains original launch provenance plus currently attached cards, personas and source revisions. Broken source references remain visible for repair; no disappearing chats when a card is removed.
- Searching and switching chats does not change controller authority, commit canonical state or discard a draft. Mobile and desktop use the same association query semantics and sync revisions.

[Chat and navigation design](../CHAT_MODEL.md).

## Open questions
- What controls do people need during live play?
- Which preferences sync across devices?
- How does an optional HUD avoid overwhelming fiction-first players?
