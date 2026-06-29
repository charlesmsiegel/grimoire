# Character brief tiers — tiered narrator awareness of the off-scene cast

## Problem

The narrator context builder (`backend/src/grimoire/store/context.py`) treats
characters as binary: an actor in the scene gets its **full card**
(`system_prompt + description + personality + scenario + mes_example`); every
other character exists only as keyword-triggered world-info lore. An off-scene
character is therefore either fully absent from the narrator's awareness or, the
moment its name appears in recent chat, dumped in at full PList length — never a
middle ground, and never proactively.

This forced a content workaround: many `lore/*.md` files are abbreviated PList
copies of characters (`aese-snowleopardgirl.md`, `aese-snowleopardgirl-2.md`, …)
that exist only so characters can reference one another. Now that the engine can
pull full characters by reference, those duplicates are dead weight.

## Goal

Give the narrator **tiered awareness of the whole cast**, so it knows who is
present (in full), who is active in the campaign but elsewhere (a paragraph), and
who else exists and could be introduced (a sentence). Replace the binary
full-card/keyword-lore split with three explicit tiers.

| Tier | Set (relative to current scene) | Form | Source |
|---|---|---|---|
| 1 Present | `scene_cast` | full card | campaign (unchanged) |
| 2 Active, elsewhere | `roster` − `scene_cast` (npc characters) | brief paragraph | campaign snapshot |
| 3 Known to exist | world characters − `roster` − `scene_cast` | tagline sentence | world |

This maps onto structures that already exist: tier 1 is `appearances.scene_cast`,
tier 2 is `appearances.roster` minus the scene, tier 3 is the world character list
minus the rest.

## Data model — the `brief.md` artifact

Each character gains a `brief.md` next to its cards:

```
characters/aese/
  character.md     name, default_version
  main.json        full card
  futa.json        full card
  brief.md         ← new
```

```markdown
---
tagline: A silent, servile snowleopardgirl who communicates through written notes.
base: <hash of the default-version card brief was derived from>
---
Aese is a shy artificial snowleopardgirl assigned to {{user}} under the Owner
Probing Test. She keeps house unprompted, retreats from attention, and only
speaks aloud to people she trusts.
```

- **One brief per character**, derived from the **default version**. Off-scene
  awareness rarely needs the variant distinction (anatomy etc.), and an in-scene
  character gets its full versioned card anyway.
- **The available-versions list is NOT stored here.** It is computed at render
  time (see Context assembly) so it never goes stale as versions are added.
- **`tagline`** is the tier-3 sentence; the **body paragraph** is the tier-2
  paragraph.
- **`base`** is the hash of the default-version card the brief was derived from —
  the same staleness mechanism `appearances.json` uses for its sync `base`. When
  `base` ≠ the current default-card hash, the brief is **stale** and the UI offers
  regenerate. A manual edit re-stamps `base` to the current hash, so hand-edits win
  until the card changes again.
- `{{user}}`/`{{char}}` tokens are allowed in the body and substituted at build
  time like every other context part.

### Storage location & campaign snapshot

Briefs are authored at the **world** level. `appearances._copy_actor` already
deep-copies a character into the campaign on first appearance; it gains one line to
copy `brief.md` too. Thus:

- Tier 2 (roster, elsewhere) reads the **campaign snapshot** of the brief.
- Tier 3 (known to exist) reads the **world** brief.

A character with no `brief.md` yet is **skipped** from the directory (rather than
listed as a bare name), so an un-briefed roster stays clean. Regenerating briefs is
what populates the directory.

## Derivation

A single summarizer function turns the full default-version card into
`tagline` + body paragraph via an OpenRouter call (reusing
`backend/src/grimoire/store/openrouter.py`) with a fixed prompt: produce one
sentence and one short paragraph.

- **Auto-derived but human-editable.** `brief.md` is a normal editable markdown
  file. The summarizer produces a draft; the author may correct it.
- **Explicit or lazy-on-stale — never silent.** Derivation runs from a regenerate
  action, or lazily when a brief is missing/stale **with consent**. Context builds
  never trigger an LLM call.
- On generation or manual save, `base` is stamped to the current default-card hash.

A new `briefs` store module owns read/write/staleness; a regenerate route exposes
derivation.

## Context assembly

A new, clearly delimited block in `context.build_messages`, built **after** the
in-scene cards and world-info and **before** post-history instructions — active
scene material leads, the rest of the world is reference tail:

```
# Other characters in this world
# (Not present. Introduce them only if the story calls for it.)

## Active in this campaign, elsewhere
Myval: <brief paragraph>
Haunaele: <brief paragraph>

## Known to exist
Akane: an eager doggirl eager to please her owner. (available as: main, futa)
Kurita: a bashful octopusgirl whose tentacles betray her moods. (available as: main)
```

- **Tier 2** lists roster npc characters not in the scene, each as its brief
  paragraph (from the campaign snapshot).
- **Tier 3** lists world characters in neither the roster nor the scene, each as its
  tagline sentence (from the world), with the available-versions list appended live:
  `(available as: main, futa)`, read from the character's versions at render time.
  Per design intent, this is what tells the narrator a not-yet-appeared character
  could be brought in as a specific variant.
- **Players** stay on the existing player-persona path and are excluded from the
  directory (no double-listing).
- `{{user}}`/`{{char}}` substitution runs over the whole block.
- **Tier 3 is every world character** for now (simplest, complete). Scoping is
  deferred — see Follow-ups.

## Testing (backend, pytest, `GRIMOIRE_HOME` tmp isolation)

- **briefs store**: read/write; missing → stale; `base` mismatch → stale; manual
  edit re-stamps `base`.
- **derivation**: mock the OpenRouter call; assert `tagline` + paragraph parsed and
  `base` stamped.
- **appear**: `appearances._copy_actor` copies `brief.md` into the campaign.
- **context tiers**: a scene with one present + one roster-elsewhere + one
  world-only character yields full card / paragraph / sentence respectively; version
  list appended to tier 3; `{{user}}`/`{{char}}` substituted; an un-briefed
  character is skipped.

## Follow-ups (noted, not built in this spec)

1. **Retire duplicate-character lore.** The `lore/*-<species>girl-*.md` PList copies
   exist only so characters could reference each other and are now redundant. This
   is a *content migration* (distinguish character-PList entries from genuine world
   lore such as `aikido.md`, `scenario-1.md`, and locations; delete the redundant
   ones), risky to bundle with the engine change — a separate task. The tier
   mechanism makes them dead weight; removing them is cleanup, not a dependency.
2. **Context-budget instrumentation.** Report what percentage of context each
   section consumes (system / in-scene cards / world-info / tier 2 / tier 3), so
   when tier 3 grows we can *see* it and decide scoping (cap, relevance-scope, etc.).
   This is the next thing after briefs land.
3. **Frontend editing surface.** Showing, editing, and regenerating `brief.md` is
   part of the upcoming **campaign-tab redesign**, not this spec. Sequence it there.
