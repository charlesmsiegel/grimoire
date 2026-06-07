# Quality bar

The target is the shipped seed world `sakura-high`
(`backend/src/grimoire/seed/library/worlds/sakura-high/`). Read a few of its
files before generating. Concretely:

## Voice anchors (characters)

Every speaking character gets a `voice` block that is specific, not generic:
- `summary`: a sentence that captures the *contradiction* in the character
  ("Direct, dryly funny, surprisingly tender.").
- `voice_register`: how they actually talk ("casual; honorifics used correctly
  but without fuss").
- `samples`: 2–4 real lines in their voice that you could drop into a scene.
- `speech_patterns`, `address_terms`, `dos`, `donts`: tells that keep them
  consistent across turns.

Bad: "She is nice and likes her friends." Good: a line she'd actually say.

## Prose

- Specific sensory detail over abstraction: "the metal of the door handle is too
  hot to touch in the afternoon", not "it is warm in summer".
- Bodies are short, structured (e.g. `## Appearance`, `## What they want`), and
  give the model something to *play*, not an encyclopedia entry.
- A location body conveys mood and what *happens* there, not just geography.

## Wiring (the part that's easy to skip)

- Locations have a real `parent_id` and `connections`; the place a scene starts
  in must exist and be reachable.
- Greetings reference characters that exist and a `starting_location` that
  exists; `defaults.starting_location` points at a real room.
- Factions list real members/leaders; characters' `structural_relationships`
  point at real ids.
- The world ships at least one greeting so a campaign can start immediately.

## Calendar & atmosphere

- Give the world a calendar with named months, seasons (with palettes and
  weather bias), and a handful of holidays that create story hooks.
- `atmosphere.default_register` and `default_palette` set the narrative tone.

## Scope

There is no fixed size. Propose counts in the plan step and let the user adjust.
A lean world (≈4–6 characters, ≈5 locations, 1–2 factions, a few lore, 1–2
greetings) is playable; `sakura-high` (≈12 characters, ≈11 locations) is rich.
Quality per file matters more than count.
