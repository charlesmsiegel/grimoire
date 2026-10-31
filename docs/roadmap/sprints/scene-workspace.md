# Scene Workspace and Accessible Controls

> **V2 scope correction:** V2 initially has no frontend. The first scene workspace enhancement must build on the minimal V2 text runner/mobile client, not reintroduce V1 APIs. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** Make chats discoverable and resumable from **Chats, Characters, Scenarios, Campaigns and Worlds**, while keeping reading, writing, reviewing and steering scenes fluid on desktop and mobile.

## Scope
- Reduce friction in cast navigation, scene switching, composer actions, editing and current status.
- Offer useful accessible inventory/time/location indicators without mandatory configurable HUD infrastructure.
- Support keyboard, screen reader, contrast, focus, font scaling and reduced motion.

## Milestones
1. Implement a navigable **unified chat library** (recent/all, by exact character portrayal, scenario, named campaign or attached world) over the same stable chat IDs, retaining drafts and scroll/focus when switching.
2. Offer optional compact status panels and reliable keyboard/touch workflows.
3. Add user-configurable layouts and workspaces with graceful defaults.

## Moonshot
An adaptive, accessible RPG cockpit rearranges itself to suit a live scene, long writing session or GM review, with no hidden gameplay state.

## Proving scenario
A keyboard-only player finds a missing item, drafts a correction and returns to narration without losing composer content.

## Independence and interfaces
Can improve current screens incrementally; voice control is a parallel input mode, not a dependency.

## Navigation invariants inherited from the quick-chat path

107 owns stable world-less play scope/chat associations and a rebuildable reverse index. 035 provides the **minimal** chat list, and 052 the one-click start surface. This sprint (Wave 7) owns the **full browsing/workspace UX**, not a second data model:

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
