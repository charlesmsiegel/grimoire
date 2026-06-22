# Greetings & Context Builder — Captured Decisions

> **Status: decisions captured — NOT yet a full design.** This records choices already made so a
> later brainstorm starts from settled ground rather than re-litigating them. It is not yet
> spec'd to the level of the implementation-ready designs; the "Open questions" section lists what
> still needs deciding before writing a plan.

**Date:** 2026-06-21
**Depends on:** [`2026-06-21-pcs-tags-actors-design.md`](2026-06-21-pcs-tags-actors-design.md)
(tags, PCs, and the recorded `player` role) and
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (cards, appearances).

This covers two intertwined pieces deferred from earlier specs: the **context builder** (which
finally injects state into the LLM, starting with `{{user}}`) and **greetings & plot maps**.

## A. Context builder & prompt injection

The worlds/campaigns and character-cards specs deferred *all* prompt injection; the PCs spec
records a `player` role but deliberately injects nothing. This work builds the context builder.

- **`{{user}}` first.** Gather a scene's **player-role** actors (from `appearances.json`,
  `role == player`; the seam is `appearances.players_in_scene`). Build **one system message** —
  a persona block listing each player's persona (PC: name/pronouns/summary/description;
  character-cast-as-player: name + card description/personality). Substitute the **`{{user}}`**
  token in outgoing user content (and within injected text): one player → that player's name;
  multiple → the comma-joined names. Prepend the system message to the OpenRouter messages.
- **Isolated + testable.** Assembly lives in its own module (e.g. `store/prompt.py`,
  `build_messages(cid, sid, scene_messages) -> list[dict]`), pure beyond reading the store, so
  the chat/retry routes just call it.
- **`{{char}}` and lore come after `{{user}}`** in the same builder: NPC character cards and
  campaign lore injection are the next layers once `{{user}}` lands. (Greetings generation, below,
  is the first consumer that needs world-informed context.)

## B. Greetings

- **First-class world objects** — `worlds/<wid>/greetings/<id>.md` (own files), seeded **from a
  card's `first_mes` / `alternate_greetings` via an explicit per-character/version import**, then
  edited independently of the card. Each greeting references a character + version and carries its
  plot edges.
- **Sourcing is explicit**, not auto-derived: an "import greetings from this card" action creates
  N editable greeting objects.

## C. Plot maps

- **Directed `leads_to` edges.** An edge both **branches** (suggests successors once a greeting is
  played) and **gates** (locks the successor until its predecessors are played).
- **Per-greeting AND/OR join** over predecessors: each greeting declares whether *all* or *any* of
  its predecessors must be played to unlock it.
- **Mutual exclusion.** Greetings can be marked mutually exclusive; playing one **locks** the
  greetings it excludes — for multi-entry plotlines that should collapse to the single entry that
  fits a given campaign.

## D. Tag gating (consumes Spec-1 tags)

- A greeting may **require world tags**. It is permitted in a campaign if a **player actor in that
  campaign carries** the required tag(s). (This is why the tag vocabulary + PC tags ship first.)

## E. Playing & availability

- **Played** = a **campaign-level set of greeting ids**; a greeting becomes "played" when a scene
  is **started from it**.
- A greeting's **availability** in a campaign = passes the plot-edge gate (AND/OR over played
  predecessors) **AND** is not excluded by a played mutually-exclusive greeting **AND** its tag
  requirements are satisfied.

## F. Starting a scene

- **From a greeting** → seeds the scene's **first (assistant) post** with the greeting text, marks
  the greeting **played**, and calls `appear()` for the greeting's character (locks its version;
  `npc` role).
- **From a prompt** → a **world-informed, character-less** LLM-generated opener (uses the context
  builder's world context, no single character chosen up front). The generated opener is
  **ephemeral by default, with an offer to save** it as a world greeting (optionally wiring plot
  edges).

## Open questions (resolve before a plan)

- Greeting file schema: exact frontmatter (character, version, `leads_to`, `excludes`,
  `predecessor_join: all|any`, `requires_tags`) and where edge data lives (on the source node, the
  target, or both) given the string-scalar frontmatter writer (likely a JSON sidecar like
  `appearances.json`, or a dedicated `plotmap.json`).
- Where "played" is stored (campaign sidecar vs derived from scene frontmatter — leaning toward a
  campaign-level set, consistent with the earlier choice).
- The exact world-context payload the generator sees, and the generation endpoint/streaming shape.
- Whether tag gating should also consider character-cast-as-player tags (Spec 1 keeps tags on PCs
  only; may need extending).
- Frontend: the plot-map graph editor (deferred with the rest of the frontend).
