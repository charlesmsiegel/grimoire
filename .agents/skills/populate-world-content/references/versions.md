# Version-specific greeting passes

## Per-character-VERSION ("era") passes

Some characters have multiple card versions that read as genuinely different eras/life-stages of
the same character (for example, variants with different roles or relationship situations). The store already
scopes greetings by `(character, version)` with **no restriction on which version string is
used** — this needs zero changes to Part A, only different orchestration:

- Enumerate every `(character, version)` pair, not just each character's `default_version`.
- Propose one `era_label` (a complete, human-readable tag name, e.g. `"Mara — Early Career"`)
  + `era_source` citation per pair, based strictly on what that version's own text says.
- Generate one tag + one `greeting_gating` entry (`requires_tags: [era_label]`) per greeting, in
  plain code from the propose output — never ask an LLM to hand-keep that many tag/gating pairs
  consistent (see the merge-stage lesson above).
- **Chaining is scoped strictly to within one `(character, version)`** — never across two
  versions of the same character (divergent eras, not a timeline) and never across two different
  characters (matching eras between different characters is a judgment call for a human; file it
  as an `open_question`, don't guess).
- New entities discovered only in a variant's text still get extracted, with era/character
  context folded into the name/body and `owners` set to that character — entities have no formal
  tag field (only `owners`), so era-scoping an entity is prose, not a structured filter.
