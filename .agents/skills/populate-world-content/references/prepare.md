# Tools and preconditions

## Two-part architecture

**Part A — `.agents/skills/populate-world-content/scripts/populate_world_content.py`** (committed, stable, do not casually
change). CLI:
```
python .agents/skills/populate-world-content/scripts/populate_world_content.py index --world <wid>
python .agents/skills/populate-world-content/scripts/populate_world_content.py run --world <wid> --manifest <path.json> [--allow-creatures]
```
`index` prints the world's existing entities (id/kind/name only) and tag vocabulary, so
proposal-writing agents can avoid duplicating what's already there. `run` applies a **manifest**
(schema below), verifies referential integrity, and **commits** — but only if the whole repo was
clean when it started and nothing failed. Never edit this file to make a one-off pass work; if it
has a real bug, fix it and run `make check-py` before touching anything else.

`--allow-creatures` permits the `creatures` entity kind for this world; omit it and any
`creatures` entity/reclassification in the manifest errors out instead of writing. The script
itself carries no notion of which of your worlds are fantasy/genre-appropriate for creatures —
that judgment belongs in the Part B orchestration script (per-world, per-invocation, never
committed), not hardcoded here as a set of world ids.

**Part B — orchestration using the host tools available in this session.** Not committed (it's
world-specific, ephemeral tooling) — write it fresh each time using the templates below, or
reuse/extend one from an earlier session if the conversation still has it loaded. Persist each batch's proposal, manifest and result in private scratch space. If
the host has resumable orchestration, reuse its run id for the same work. Otherwise
use the recorded successful batches as checkpoints; do not repeat completed applies.
The repository does not supply a `Workflow` tool.

## Prerequisites

- `~/.grimoire` (wherever `store.home()` resolves) **must be its own git repository** before you
  touch it, with at least one baseline commit. This is the only safety net — `run` refuses to
  start unless the repo is clean, and every successful call is its own commit. If it isn't a repo
  yet, `git init && git add -A && git commit` first and tell the user you did.
- Use the World scope the user authorized; clarify it only if it is missing. Some worlds don't fit this
  pipeline's assumptions (no characters at all — a rules-reference corpus, say) or shouldn't be
  touched (flagged content). Ask, don't assume "every world in the store."
