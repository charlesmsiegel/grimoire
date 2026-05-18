# Expression Sprites

Per-character expression sprite display fed by an Extractor strategy that
classifies the emotion of each character's spoken paragraphs.

## Scope

- Directory-form character cards recognised alongside the existing flat
  form. Layout:

  ```
  data/library/settings/<setting>/characters/
  ├── alistair-hyde-smythe.md            # flat form, no sprites
  └── beatrice/
      ├── card.md
      ├── avatar.png
      └── sprites/{neutral,happy,angry,...}.png
  ```

  The Library indexer treats `characters/<id>.md` and
  `characters/<id>/card.md` equivalently.

- Grimoire Expression Vocabulary (GEV) — fourteen labels: `neutral`,
  `happy`, `sad`, `angry`, `surprised`, `fearful`, `disgusted`, `smug`,
  `thoughtful`, `embarrassed`, `determined`, `hurt`, `tired`, `suspicious`.
- Mechanics-module extensions via manifest:
  `expression_vocabulary_extensions: [seductive, terrified, awakened]`,
  namespaced under the module id (e.g. `wod.seductive`).
- Fallback chain: requested emotion → `neutral.png` → `avatar.png` → no
  sprite (UI shows character name only).
- New Extractor strategy emitting `expression_changed` deltas
  `{character_id, emotion, scene_id, post_id, confidence}`. Two
  implementations behind the existing confidence + review-queue flow:
  rule-based heuristic and LLM classifier.
- SQLite `expression_state (turn_id, scene_id, character_id, emotion,
  set_at)`. "Current expression" is a query, not a stored value.
- Frontend display rules: the speaker of the latest paragraph shows their
  current expression, recent speakers retain their last-known, PC
  expression set by the player (not extractor), characters not in scene
  show no sprite.

## Constraints

- Sprites are a presentation-layer feature. They touch nothing in the
  deterministic context assembly, the state model, or mechanics.
- v1 is static PNGs only. Animated formats (GIF/APNG) and multi-emotion
  paragraphs (terminal emotion wins for now) are explicit non-goals.
- Recommended sprite profile (transparent PNG, ~512×768, centered)
  documented but not enforced.
