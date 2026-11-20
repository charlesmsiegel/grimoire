# Draft-Aware Prompt and Context Preview

**Status:** milestone 1 implemented ([INFERENCE_UI](../../INFERENCE_UI.md#draft-preview-085)): on the owner's request, the Models and costs page's context inspector packs an NPC's context with an unsent draft read exactly as a post would be, and shows what the draft likely recalls (the lore it triggers, the memories whose words it shares) and what the budget would leave out, labelled as an estimate. Nothing is posted, written or sent. Retained snapshots of a submitted turn's prompt and milestones 2 and 3 remain open.

**Outcome:** Show how an unsent draft may affect context selection without secretly executing a turn or spending inference continuously.

## Scope
- Compute approximate retrieved context and token packing for a draft on demand or with debounce.
- Explain relevance, omitted lore and privacy/knowledge boundaries.
- Clearly distinguish estimated preview from the exact immutable prompt snapshot of a submitted run.

## Milestones
1. Offer a read-only preview on explicit request.
2. Show useful deltas between two draft phrasings.
3. Experiment with cheap incremental relevance previews and warnings for missing context.

## Proving scenario
A player can see which archived promise their draft likely recalls and can inspect the final prompt later; preview changes no canonical file.

## Independence and interfaces
Works over existing context composer; no continuous embedding calls per keystroke or live voice stream required.

## Open questions
- What latency budget makes preview worth using?
- How should estimated versus exact differences be described?
- Which sources may preview for private scenes?
