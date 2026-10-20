# Propose, merge and sequentially apply

## Workflow

1. **Index + listing.** One agent call runs `index --world <wid>` and returns its stdout
   verbatim. A second enumerates character ids (and, for an "era" pass — see below — every
   `(character, version)` pair via `characters.read_character(root, cid)['versions']`).

2. **Propose, batched.** Chunk characters (≈15/batch) and lore entries (≈50/batch) separately.
   One subagent per batch reads its files and proposes entities/greetings/tags/candidate edges —
   see prompt templates below. Never let a propose agent re-derive facts from anything but the
   files it was explicitly told to read; every candidate needs a citation.

3. **Merge, batched — one merge call PER PROPOSE BATCH, never one for the whole world.** A
   whole-world merge can exceed a tool or model output limit. Each merge
   call only ever sees its own batch's candidates (cross-batch dedup happens for free at apply
   time, since `apply_entities`/`apply_tags` already dedupe by exact case-insensitive name across
   the whole combined manifest — a merge agent never needs whole-world visibility to be correct,
   only to avoid inventing near-duplicates it can't fully judge).

4. **Combine merge outputs in plain JS, not by another LLM call.** Concatenating arrays is a
   mechanical operation — do it in code. If a pass needs per-greeting tag+gating entries derived
   from something computed during propose (e.g. an "era" label — see below), generate those in
   code too from a 1:1 rule, never ask an LLM to keep tags/gating manually consistent by hand;
   that mismatch was a real, repeated source of failed applies this session.

**Processing multiple worlds in one run? Don't serialize them.** A whole world's
propose+merge+apply has zero data dependency on any other world's — running world A's *entire*
pass (including its apply, which is slow only because it's sequential, not because it's
expensive) before even starting world B's propose+merge wastes real wall-clock. Split each
world's work into a propose+merge-only function (returns partials, no apply) and a separate apply
step; kick off every world's propose+merge concurrently (`Promise.all`), then run each world's
apply sequentially afterward (apply still can't run concurrently *across* worlds either — they
share one repo, and `run` needs a clean repo + commits at the end regardless of which world it's
for). Also: before re-processing a world you've partially done, compute the actual remaining
`(character, version)` pairs from real store state — read each existing greeting file's own
`character`/`version` frontmatter field, never infer from the greeting filename (filenames are
slugified from the card's *display name*, not the folder id — a character folder named
`mara-variant` can have greetings named `mara.md`, `mara-2.md`, ...). Pass only the remaining
pairs into the batch listing so you're not re-paying propose/merge cost for characters already
done.

5. **Apply — per CHUNK, strictly sequential, never parallel/pipelined.** Even after per-batch
   merge, recombining many small merge outputs into ONE whole-world manifest can still exceed the
   64k output-token cap **on the Write itself** (the apply agent must emit the full manifest as
   generated text to fulfill the Write tool call — this is a distinct failure from the merge-stage
   one, hit independently, and per-batch merge output being small does NOT guarantee the
   recombined manifest is small enough to re-emit in one turn). So: one `run` invocation — one
   commit — per batch's own merged output, not one combined call for the whole world. Applies
   MUST run sequentially (a plain `for`, never `pipeline`/`parallel`): each `run` requires a clean
   repo and commits at the end, so concurrent applies race on that precondition. If a chunk fails after writing, pause subsequent applies and read
   [recovery](recovery/index.md). Preserve completed proposal work, inspect partial
   writes, and report the failure. The next apply still requires a clean library.
