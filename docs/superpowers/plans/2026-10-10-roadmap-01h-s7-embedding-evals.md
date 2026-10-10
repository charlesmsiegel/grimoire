# 01h-S7: Embedding evals — Plan

**Goal:** `evals/run.py --embed` measures retrieval: recall@1/3/5/10 and MRR
per shape and overall, beside a lexical baseline, on an invented corpus.
Replay of recorded rankings and an offline end-to-end case run under
`pytest backend`; `--live --embed` ranks it on the Embedding role's model,
in throwaway homes, with `--embed-options` A/B sets and `--record`.
Delivers 01h-C6.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§8, §9 (C6), §10, §11 (Evals), Slices 01h-S7. Builds on 01a-S2 (live runs
metered in an isolate, `runner.harvest`, the tripwire) and 01a-S3 (the
report's `costs` buckets).

**Review gates:** not run (speed mode); the Codex gates are owed.

**Open questions (§13):** none is this slice's. **01a-C3 (soft):** 01a-S4's
run file and `--compare` are built around generate/decide cases; folding
embedding rankings into the eval-run v1 file is left open, so (as the spec
allows until then) each option set prints its own rows, and `--out` and
`--compare` are refused with `--embed`.

## Design (`evals/embed/`)

- `corpus.json` (100 documents, 30 queries, written by hand with placeholder
  names): three shapes (`recall`, `search`, `identity`), each query with a
  paraphrase positive (no shared content word) and a lexical decoy (shares
  at least one), plus fillers. Every query ranks the whole pool, which gives
  at least ten documents per unit of the largest k.
- `corpus.py`: loading, `SHAPES` (shape -> its real embed task, and whether
  its query goes as a query), `content_words` and `problems` (the rules).
- `metrics.py`: `recall_at`, `reciprocal_rank`, `grade` (per shape and
  "all", misses, unanswered queries and unknown ids) and `table`.
- `harness.py`: `lexical_rankings` (`store.search`'s scorer);
  `embedded_rankings` (one `embed_sync` per shape under its task: the
  shape's queries first with `queries=`, or 0 for identity, then its
  documents; fillers ride the search call; cosine in pure Python);
  `bow_handler`/`bow_client`/`bow_space` (the deterministic offline endpoint,
  which honours or ignores `dimensions`); `options_of` (validated as a facts
  write) and `with_options` (space recomputed by `space_of`); `resolve_live`;
  `estimate`; recordings named `<model-slug>-<digest or none>.json`.
- `evals/run.py`: `--embed` (replay, or with `--live` the live flow) and
  `--embed-options` (repeatable, live only). The live flow resolves the space
  before any isolate, prints bytes and tokens first, and per option set opens
  `temp_home()`, refuses a real home (the tripwire), embeds through a fresh
  client, harvests the rows (`runner.harvest`) and prints wall time and the
  cost bucket (`costs.fold`, `costs.bucket_line`).
- `evals/README.md` gains "Embedding evals" (both interpreter paths).

## Tests (`backend/tests/test_embed_evals.py`)

- The corpus keeps its rules; the rules catch a planted positive and decoy;
  the lexical baseline ranks no paraphrase first.
- Metrics on a known ranking; a perfect ranking; a planted miss is flagged;
  an unanswered query and an unknown id are not whole; every recording
  replays whole; the CLI replays and refuses what `--embed` does not take.
- Offline: the bag-of-words path reproduces its recording, one row per shape
  with its task and the run id; prefixes reach every request; the `param`
  split and `dimensions` end to end; a width-ignoring endpoint fails the
  check; option sets never share a space; recordings are named by model and
  option digest.
- Live with the endpoint swapped for the offline one: rows land in the
  isolates and the report, never in the real ledger; a run whose isolate is
  the real home is refused before anything is sent.
