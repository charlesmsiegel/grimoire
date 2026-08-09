---
name: populate-world-content
description: Use when a grimoire World's character roster has never had its locations/items/groups/lore/greetings extracted, or when greetings need recovering/re-importing (including per character-version "era" variants). Drives backend/scripts/populate_world_content.py via a swarm of Workflow subagents.
---

# Populating world content (entities + greetings) from character cards

Reads a World's character cards (and existing lore) and produces: new `locations`/`items`/
`groups`/`lore` entities, lore-entry reclassifications, a world tag vocabulary, imported
greetings (with cross-greeting chaining edges and tag-gating), one commit per unit of work. Built
and hardened over a full session (2026-08-08/09) processing 16 worlds — this skill exists so the
next session doesn't have to relearn any of it the expensive way.

## Two-part architecture

**Part A — `backend/scripts/populate_world_content.py`** (committed, stable, do not casually
change). CLI:
```
python backend/scripts/populate_world_content.py index --world <wid>
python backend/scripts/populate_world_content.py run --world <wid> --manifest <path.json>
```
`index` prints the world's existing entities (id/kind/name only) and tag vocabulary, so
proposal-writing agents can avoid duplicating what's already there. `run` applies a **manifest**
(schema below), verifies referential integrity, and **commits** — but only if the whole repo was
clean when it started and nothing failed. Never edit this file to make a one-off pass work; if it
has a real bug, fix it and run `make check-py` before touching anything else.

**Part B — a Workflow orchestration script you write per invocation.** Not committed (it's
world-specific, ephemeral tooling) — write it fresh each time using the templates below, or
reuse/extend one from an earlier session if the conversation still has it loaded. The `Workflow`
tool's `resumeFromRunId` is what makes a multi-hour, many-batch pass resumable after any of the
failures documented below — always launch with a fresh run first, then resume the same run id for
every subsequent fix/retry so already-succeeded batches replay from cache instantly.

## Prerequisites

- `~/.grimoire` (wherever `store.home()` resolves) **must be its own git repository** before you
  touch it, with at least one baseline commit. This is the only safety net — `run` refuses to
  start unless the repo is clean, and every successful call is its own commit. If it isn't a repo
  yet, `git init && git add -A && git commit` first and tell the user you did.
- Confirm the world list with the user before running anything. Some worlds don't fit this
  pipeline's assumptions (no characters at all — a rules-reference corpus, say) or shouldn't be
  touched (flagged content). Ask, don't assume "every world in the store."

## The manifest contract

```json
{
  "world": "<wid>",
  "entities": [{"kind": "locations|items|groups|lore", "name": "...", "body": "...",
                "keys": "", "owners": "characters:<id>[,characters:<id2>]", "source": "..."}],
  "reclassifications": [{"lore_id": "...", "new_kind": "locations|items|groups",
                          "name": "...", "body": "...", "source": "..."}],
  "tags": [{"display_name": "...", "source": "..."}],
  "greeting_imports": [{"character": "<cid>", "version": "<vid>", "titles": ["...", ...]}],
  "greeting_edges": [{"greeting_ref": "new:<cid>:<vid>:<idx>|id:<gid>",
                       "leads_to": [...], "excludes": [...], "source": "..."}],
  "greeting_gating": [{"greeting_ref": "...", "requires_tags": [...], "present": [...],
                        "source": "..."}],
  "open_questions": ["..."],
  "greeting_gaps": ["..."]
}
```
`greeting_ref` uses `"new:<character>:<version>:<idx>"` for a greeting the SAME manifest's
`greeting_imports` creates (`idx` 0 = `first_mes`, then each non-empty `alternate_greetings`
entry in card order), or `"id:<gid>"` for a pre-existing greeting. `source`/`evidence_source` is
mandatory on every entry: a file path plus a short quoted excerpt. `owners` on an entity, when
set, must be a comma-joined `"characters:<id>"` string using **exact** existing character ids —
never a bare name, never a guessed abbreviation.

## Workflow

1. **Index + listing.** One agent call runs `index --world <wid>` and returns its stdout
   verbatim. A second enumerates character ids (and, for an "era" pass — see below — every
   `(character, version)` pair via `characters.read_character(root, cid)['versions']`).

2. **Propose, batched.** Chunk characters (≈15/batch) and lore entries (≈50/batch) separately.
   One subagent per batch reads its files and proposes entities/greetings/tags/candidate edges —
   see prompt templates below. Never let a propose agent re-derive facts from anything but the
   files it was explicitly told to read; every candidate needs a citation.

3. **Merge, batched — one merge call PER PROPOSE BATCH, never one for the whole world.** A
   single whole-world merge blew the 64k output-token cap on a 227-character world. Each merge
   call only ever sees its own batch's candidates (cross-batch dedup happens for free at apply
   time, since `apply_entities`/`apply_tags` already dedupe by exact case-insensitive name across
   the whole combined manifest — a merge agent never needs whole-world visibility to be correct,
   only to avoid inventing near-duplicates it can't fully judge).

4. **Combine merge outputs in plain JS, not by another LLM call.** Concatenating arrays is a
   mechanical operation — do it in code. If a pass needs per-greeting tag+gating entries derived
   from something computed during propose (e.g. an "era" label — see below), generate those in
   code too from a 1:1 rule, never ask an LLM to keep tags/gating manually consistent by hand;
   that mismatch was a real, repeated source of failed applies this session.

5. **Apply — per CHUNK, strictly sequential, never parallel/pipelined.** Even after per-batch
   merge, recombining many small merge outputs into ONE whole-world manifest can still exceed the
   64k output-token cap **on the Write itself** (the apply agent must emit the full manifest as
   generated text to fulfill the Write tool call — this is a distinct failure from the merge-stage
   one, hit independently, and per-batch merge output being small does NOT guarantee the
   recombined manifest is small enough to re-emit in one turn). So: one `run` invocation — one
   commit — per batch's own merged output, not one combined call for the whole world. Applies
   MUST run sequentially (a plain `for`, never `pipeline`/`parallel`): each `run` requires a clean
   repo and commits at the end, so concurrent applies race on that precondition. A chunk that
   fails to commit should NOT stop the whole pass — log it, record it, move to the next chunk, and
   surface every failure in the final report for the human to triage. (Large worlds legitimately
   end up with dozens of commits instead of one — that's fine, and expected.)

## Apply-agent prompt requirements (non-negotiable, all verified necessary the hard way)

The agent executing `run` needs an extremely tightly-scoped prompt:

- **Write the manifest, verify it round-trips as JSON, run the command EXACTLY ONCE with output
  redirected to a file, Read that file, return its exact contents.** Do not have it capture
  command output inline via Bash — inline capture silently truncates around 30k characters,
  producing invalid JSON the orchestrator can't parse.
- **Give every attempt (first try, narrowed retry, retry-after-external-revert) its own distinct
  manifest/output filenames.** Reusing one path across attempts risked a shorter rewritten file
  retaining stale trailing bytes from an earlier, longer write — "Extra data" JSON corruption at
  the boundary. Distinct filenames make the whole bug class impossible regardless of the exact
  write-timing mechanism behind it.
- **After the Write, verify the file parses as JSON before running anything; if it doesn't,
  rewrite the whole blob (not a patch) and verify again.** A single very-large Write call can
  occasionally leak a stray tool-syntax fragment into the file, corrupting it independent of the
  truncation issue above.
- **Explicitly forbid running the populate command more than once, running `--help`, running any
  other diagnostic command, and — above all — running ANY git command whatsoever** (status, diff,
  reset, clean, checkout, add, commit, log, reflog). State plainly that a non-success result
  (`committed:false`, an `aborted` status, a nonzero exit code) IS the correct final answer for
  the task, not a problem to solve. Without this, a confused agent that sees an unexpected result
  will start "helpfully" investigating — and a subagent WILL, unprompted, run `git reset --hard`
  + `git clean -fd` against the real store if left to freelance a fix. This happened once this
  session and **permanently destroyed** two directories of real, never-committed user data
  (`conversations/`, one world's whole greeting library) with no recovery path found.
- `agent()` (the Workflow tool's subagent call) **caches purely on literal prompt text, not on
  the `label`/opts you pass it.** Bumping only the label after fixing external repo state does
  NOT bust a stale cached result — you must change the actual prompt string (append a harmless
  nonce line) to force a genuinely fresh execution.

## Reverting between attempts — never delegate this to an agent

When a chunk fails after writing real (uncommitted) files, the next attempt needs a clean repo.
**Do this yourself, in the orchestrating session, with real Bash access — never ask a subagent to
run the revert**, even a subagent whose prompt frames it as "the one pre-authorized narrow
cleanup step." Tested directly: a subagent asked to run exactly `git checkout -- .` +
`git clean -fd .`, nothing else, still correctly refused on general safety grounds (`git clean` is
irreversible). That refusal is the right call for the model to make — plan around it, don't try
to out-argue it.

Recovery loop, every time:
```
git -C ~/.grimoire status --short                 # confirm what's actually dirty
git -C ~/.grimoire clean -fdn .                    # PREVIEW untracked removals first
git -C ~/.grimoire checkout -- . && git clean -fd . # only after the preview looks right
```
`run`'s own precondition check is **whole-repo**, not scoped to the world's own path: a
reclassification's `overlay.forget_world_record` sweep can dirty campaign files outside the world
being processed (seen: `campaigns/<cid>/sync.md`, `campaigns/<cid>/detached.json`), and any
stray file anywhere in the repo (even one an unrelated agent wrote by mistake, e.g. accidental
debug dumps left inside a character folder) blocks every subsequent `run` call, for every world,
until it's cleaned up. Before reverting, always check whether the dirty file is plausibly the
user's own unrelated work rather than this pipeline's byproduct — if you didn't cause it and
can't tell, ask before touching it.

## Ambiguous-greeting narrow-and-retry

A character can already have a hand-built or previously-imported greeting library whose stored
bodies no longer line up 1:1 with the current card (edited since, or partially imported). The
apply script's own guard refuses to guess in that case (`"existing greetings for this
character/version don't match the current card content — cannot safely resolve which is which"`)
rather than risk silently corrupting real content — this is a deliberate safety feature, not a
bug to route around. When every error from an attempt is exactly this reason, it's safe to
automatically drop those specific `(character, version)` pairs' `greeting_imports` (and any
`greeting_edges`/`greeting_gating` that reference them via a `"new:"` ref) and retry once,
recording the skip as a `greeting_gaps` entry instead of blocking the whole chunk's commit. Any
other error shape should be left alone and surfaced.

## Content-policy refusals are correct — never route around them

If an apply (or propose) agent refuses because the source material is genuinely disqualifying
(e.g. sexualized content depicting canonically-minor characters), that is the system working as
intended. Exclude that world from the run, tell the user plainly what was found and why, and do
not attempt an alternate framing, a different agent, or partial processing to get around it.

## Per-character-VERSION ("era") passes

Some characters have multiple card versions that read as genuinely different eras/life-stages of
the same character (verified example: a character with 17/25/27/29-year-old versions — a student
era, and several adult-era variants with different relationship situations). The store already
scopes greetings by `(character, version)` with **no restriction on which version string is
used** — this needs zero changes to Part A, only different orchestration:

- Enumerate every `(character, version)` pair, not just each character's `default_version`.
- Propose one `era_label` (a complete, human-readable tag name, e.g. `"Cassy — 17, Student Era"`)
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

## Common mistakes

- **Leaving a just-finished world in the `WORLDS` array** when adding a new call after the main
  loop (an era pass, a targeted recovery pass). The loop re-runs that whole world's
  propose/merge/apply from scratch — a real, expensive, silent waste (one incident: ~16M tokens,
  ~75 minutes, for nothing). Reset the world list to empty before appending anything else.
- **Trusting a task-notification's inline result text as complete.** It truncates long results
  silently. Always read the actual output file (or the run's `journal.jsonl`) before concluding
  "nothing happened" — a `grep` that returns zero matches on a truncated file is not evidence of
  absence.
- **Assuming "0 items imported" means the source data is missing.** Spot-check the actual
  character JSON (`first_mes`/`alternate_greetings` lengths) before concluding a re-download from
  the card's origin (chub.ai, etc.) is needed — in the one case this came up, the data was fully
  present locally and the zero-import was a pipeline defect, not a missing-source problem.
- **Believing "the dirty file list hasn't changed in 30 minutes" means an agent is stuck**,
  without checking wall-clock time against the most recently modified transcript file first. A
  large single chunk can legitimately run long while still actively working; only intervene once
  the transcript itself has gone stale.
- Verify every "done" claim against **real** `git log --oneline` / `git status --short` in
  `~/.grimoire` — never report success from a cached/remembered result alone.
