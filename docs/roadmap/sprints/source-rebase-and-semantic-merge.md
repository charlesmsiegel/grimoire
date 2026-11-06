# Selective root/instance rebase

**Status:** first slice implemented ([REBASE](../../REBASE.md)): a reviewed, field-level rebase of one campaign actor onto a newer revision of the same entity, and a chat's picked actors proposed as a new reusable world with provenance, neither rewriting any history. Campaign A keeps its pinned revision while campaign B takes a biography correction; the new world is attached with 107's `attach_world`, keeping every id.

**Outcome:** World definitions evolve without forcing campaign identities, state, memories or established history to change.

## Scope and first proof
Compare actual common ancestor, new world source and independent campaign overrides; accept one changed authored field while keeping individual relationships and history.

## Subsequent milestones and moonshot
Field/facet-level merge, pin/detach/follow modes, provenance previews and explicit upstream promotion.

## Independence and interfaces
Source upgrade and merge engine; requires native revisions, effective-world view and typed relationships.

## Proving scenario
Campaign A retains her original portrayal; campaign B accepts a world biography correction without sharing memories.

## Promote a quick chat into a world without copying the history

A world-less chat can later acquire an authored world source, or propose a new reusable one from **selected and reviewed** chat-authored lore, organizations, places, relationships and character adaptations. The new world receives its own source-qualified definitions with provenance; it does **not** take ownership of the existing chat, copy every line as global lore, or merge the lived state of two distinct campaign instances.

Prove a small Mara chat becomes associated with a Modern Elderfen world after previewing a conflict between newly imported source biography and established campaign events. Keep the original campaign/actor/chat IDs and accepted history, pin or explicitly review source-revision changes, and refuse a same-named substitute. The first slice can operate on synthetic records and prepare selected-source changes; richer authoring UI belongs to 052/056. [Chat lifecycle](../CHAT_MODEL.md).

## Planned later work (not in the first slice)

- Rebasing a derived portrayal onto its base's new revision (source-level rebase).
- Campaign-local overrides of description and sections, so one of two changed sections can be kept (a 009 payload change). Until then `keep` on authored text is refused and the campaign stays pinned.
- Keeping a campaign's own voice, greeting or system prompt when a card's uncomposed payload changes (046). The first slice shows each changed payload key as an `extra:` row that needs an explicit `take`.
- Adopting a different entity (a world portrayal of the actor's card) for an existing actor, which needs a 107 roster change. The first slice proposes the chat's actors as a new world instead.
- A conflict preview that compares new authored text with established events. The first slice reports `history_review` with the history ids.
- Promoting relations, lore, places and organizations, not only character adaptations.
- A reviewed way to drop lived state when upstream removes the facet it needs; checking every layer of a derived ancestor for hand edits.
- Review before a following chat sees a new head, and pin/follow/detach modes beyond what bindings store.
- Redacting `promoted_from` on release or sharing (056), provenance previews in a UI (052/056), and undo.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
