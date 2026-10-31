# One-Premise Campaign Launch and Organic World Growth

**Outcome:** Launch a character/scenario chat in one action without worldbuilding, and let the player grow it into a multi-card campaign or authored setting only if desired.

## Scope and ownership
- Offer a low-friction prompt-to-first-scene path with optional genre, PC, boundaries and mechanics/no-mechanics choices.
- Propose a minimally sufficient starting cast/location, then lazily expand world records with reviewable provenance as discoveries matter.
- Preserve the distinction between improvised draft suggestions and accepted persistent world canon.
- Allow manual editing and later migration to richer authored settings without locking players into a particular template.

## Milestones
1. Offer a **Start Chat** action for a chosen standalone character, scenario card, character + lorebook bundle, or one-line premise, opening the existing Wave 5 world-less runner with no mandatory world or campaign wizard.
2. Review and persist a named NPC/place only when it becomes relevant, reusing existing world/campaign record types.
3. Grow a coherent multi-region setting through long-term play with controlled source identity and creative surprise.

## Moonshot
From one character card or one line of scenario text to a vivid hundred-hour campaign, the world grows organically and remains editable, coherent and attributable; no data copies or identity resets.

## Proving scenario
A user supplies a one-line premise and begins play immediately; later saving a named NPC does not overwrite the original prompt or introduce unrelated lore as canon.

## Interfaces and independence
Builds on 035's text play loop and 107's lightweight play scope; imported 037 cards/scenarios and optional 016 boundaries are available without any world. Richer source/world authoring is a separate reviewed feature, not a launch prerequisite.

## Moved earlier: Wave 6 quick-start onboarding

This sprint moves from Wave 7 to **Wave 6**. A user should not need to locate a "create world" button to begin a SillyTavern-style roleplay. The first UI action accepts **exactly one launch selection** (character, scenario, premise or bundle), with optional persona, greeting and lorebook choices. It then opens a world-less 035 chat. Card import/preview is 037's responsibility, and no model should run until the user actually starts play.

**Navigation UX:** Start Chat appears from global Chats, Character detail, Scenario detail and a selected source collection. Each surface opens the **same** canonical 107 scope; continuing a chat never creates a second one. Show related chats and **Continue**, allow "Add Character/Card", and offer **Name as Campaign / Attach World** as an optional next step rather than a demand for new setup.

**Failure-proof:** a missing card or unresolved source displays a repair choice, preserving the chat; a scenario-only chat has no fabricated NPC; adding a new NPC doesn't grant old memories; an existing chat is never copied when displayed under two navigation filters. A world created later must be based on reviewed selected facts and retain the chat's actor IDs/events.

[World-less chat lifecycle](../CHAT_MODEL.md) / [Sprint 107](world-optional-chat-scopes.md).

## Open questions
- What is the irreducible setup for safe, coherent play?
- Who confirms newly established canon?
- How should early generated records evolve without retroactive retcons?
