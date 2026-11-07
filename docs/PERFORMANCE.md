# Performance and tracing (048)

What a played turn costs, where its time goes, and how that is measured. First slice of [Performance And Tracing](roadmap/sprints/performance-and-tracing.md).

## Traces

`grimoire.tracing` is pure: it reads no clock of its own and does no I/O. A `TraceBuilder(clock)` builds one `Trace` for one call:

- **Spans** nest. Each has an operation name from a fixed vocabulary (`read.turns`, `brief.npc`, `write.turn`...), its start and wall time, a status, and integer counts (files read, lines heard, items packed). A span's *self* time is its wall time less its direct children's, and `Trace.breakdown()` gives each operation's count, wall and self time with the self times adding up to the root's wall time exactly: no second is counted twice and none is lost.
- **Status** is `ok`, a refusal's machine reason (`turns_unreadable`, `not_player`...), or the type name of an exception that left the span. A status, like an operation or a count name, is a short ASCII name with no spaces; a refusal whose reason is not one is recorded by its type name, so its message never enters a trace.
- **Call samples** (`CallSample`) are one per attempt at a planned model call: the `CallAttempt` its turn ledger booked (stage, attempt number, outcome, route label, tokens with cache reads and writes) and the provider's first-byte and total time. A call that was sent but whose booking failed altogether (the meter's *poisoned* call, with no attempt on the ledger) is still one sample, marked `lost`, with every count unreported: a trace never leaves out a call that may have been billed.

A trace never holds prompt, reply or post text, character or player names, ids or paths. The runner replaces each model call's campaign, turn and actor ids: the actor is its place in the turn's plan (`npc-1`, `npc-2`... in the order the speakers were planned), because an imported actor's instance id can be its character's name. It does hold route labels (the provider and model, never a key). Traces are handed to the caller and **never persisted**: nothing in this slice writes one to disk.

### Missing data is said, never filled in

`call_costs(trace, prices=None)` sums a trace's model calls per role (one stage for one actor, such as `npc_generation` for Mara) and in total:

| What | States |
|---|---|
| Tokens | `UsageTotals` (004): a count the provider did not report is counted as unreported, never as zero, and a sum is a lower bound while any of its field's counts is unreported. |
| Provider time | `measured` (every sent attempt timed on the real clock against a provider-reported attempt), `synthetic` (a test or scripted clock: never evidence of real latency), `partial` (some timed, some not, or a mix), `unreported` (none timed: a lost report, or nothing sent). The seconds are `None` when nothing was timed, and first-byte seconds are `None` unless every timed attempt reported its first byte. |
| Money | `unpriced` (`None`) unless the caller passes a `PriceTable` with a price for every route the role used; `estimated` when any count was not provider-reported (a test's or scripted transport's, or an estimate), so a made-up bill never reads as a real one; `partial` (a lower bound) when a needed count is unreported, including an unknown cache share (charged at the cheaper rate) and a lost call; otherwise `priced`. An attempt never sent costs nothing. |
| Retries | `attempts` beyond each call's first; `failed` counts failed or cancelled attempts, never unsent or lost ones; `lost` counts sent calls whose booking failed. |

No prices ship (013). Without a table every cost is `unpriced`.

## The runner's hook

`SceneRunner(..., tracer=None, clock=time.perf_counter)`. With a `tracer` (any callable taking a `Trace`, such as a `TraceLog(keep=64).record`), every public call (`open`, `seat`, `begin`, `post`, `generate`, `label`, `confirm`, `decide`, `framed`, `outcome`, `propose_effect`, `approve_effect`, `endorse_effect`, `recover_effects`, `keep`, `strike`, `pass_turn`, `request`, `resume`, `choose`) is one trace whose root span is the call. Inside it:

| Span | What it times | Counts |
|---|---|---|
| `read.library` | `store.load()` | |
| `read.snapshot` | 022's chat snapshot | |
| `read.turns` | the chat's turn files | `files` (found: each readable one read and hashed), `decoded`, `reused`, `turns` |
| `read.transcript` | the imported history's transcript pages (035 M2) | `posts` |
| `read.control`, `read.party` | 010b's control, 033's party | |
| `stage.cast`, `voices` | staging and voice resolution | |
| `read.stage` | the app's 041 stage source, and checking its stage against the cast (only with `stages`) | |
| `read.knowledge` | what each speaker knows | |
| `brief.npc` / `pack` | one speaker's brief and its packed context (with an agenda, 039's check, choice and the brief rebuilt with it) | `heard`; `items`, `omitted` |
| `brief.director` | the director's brief | |
| `plan`, `provider`, `review` | planning the turn's calls, running them, reviewing the takes | `calls` |
| `write.obstacle` | `pass_turn`'s withdrawal of an obstacle attempt 029 cannot decide (040), before the turn write | |
| `write.turn` | each turn write (a file, or 028's choice or 016's resume rewriting several in one commit), its checks under the lock included | |

`generate` and `label` (035 M2's labelling call) add one call sample per booked attempt from their meter. Without a tracer every span is a no-op and nothing is timed.

Tracing never changes what a call does: a tracer that raises loses that trace and never the call's result; a clock that raises or gives no number loses the trace being built and the call goes on untraced; and a refusal is recorded as the root's status before it reaches the caller unchanged. The trace being built is held in a context variable, so concurrent calls on one runner each build their own.

## The bottleneck, and what changed

Before this slice every runner call decoded every turn file of the campaign (read, strict JSON parse, envelope, classify, then the turn itself), once in reading the chat and once more under the lock in every write. Profiled at 500 synthetic turns, that was about 80% of a call.

Now:

- **Only this chat's files are read.** A file whose name spells another chat's turn (`<other chat>.<6 digits>[-<device>].json`) is skipped before it is read: whatever it holds, the chat's turns would have dropped it. Any other name, including a stray file, is still read and still counts against every chat (fail closed).
- **An unchanged file is not decoded again.** Every call still reads and hashes every one of the chat's turn files; a file whose bytes hash to the etag of what this runner last read is reused as decoded, and any other is decoded afresh. A damaged or rewritten file, written by this runner, another runner or another program, is therefore always seen. A file that could not be decoded stays a problem on every read, and a record is reused only at the address it was read from. What was read is held per chat and replaced whole on each read, so a file gone from the folder is forgotten; a library keeps the 16 chats it read most recently.
- **A write's checks under the lock use the same read.** They only ever looked at this chat's turns.

The records, problems, order, fork and gap detection are unchanged. Tests prove a warm runner and a fresh one send the same prompts over an edited history (a struck line), that a change made by another runner is seen, that a file damaged after it was read is reported and play stops, and that another chat's damaged file is not read.

## Benchmarks

`backend/tests/scene_bench.py` defines them and `backend/tools/bench_scene.py` prints them. Everything is synthetic: the fixture libraries, scripted provider replies and a scripted clock for provider time, so every provider latency is `synthetic` and every cost `unpriced`.

- **Turn.** A history of *n* kept turns (a post and a reply each) is written into a chat. A *cold* table (a new store, authority and runner, as after an app start) opens the chat and plays one turn (`post`, `generate`, `keep`); the same table then plays a second, *warm* turn. Two scenarios: Mara Vell's card chat (one speaker, with her lorebook) and 026's migrated V1 campaign (Mara and Seraphine both answer).
- **Import.** 026's V1 import of its two-NPC library (scan, plan, apply) and 037's import of one card.

```
python backend/tools/bench_scene.py --history 10 100 500
python backend/tools/bench_scene.py --untraced   # outside wall times only, for a build with no hook
```

### Baseline

Wall milliseconds on one development container, synthetic data, before (`v2` at 2ad28e4) and after this slice, timed from outside with no tracer. The before numbers were taken in a checkout of 2ad28e4 with this slice's `grimoire.tracing` package, `scene_bench.py` and `bench_scene.py` copied in (the old runner has no hook; the benchmark itself imports the pure tracing package), run with `--untraced`. The numbers vary by about 20% between runs; below 100 turns the difference is within that noise.

| Card chat | 100 turns, before | after | 500 turns, before | after |
|---|---|---|---|---|
| cold `open` | 70 | 54 | 206 | 228 |
| cold `post` | 129 | 66 | 421 | 152 |
| warm `open` | 69 | 33 | 265 | 89 |
| warm `post` | 153 | 55 | 494 | 174 |
| warm `generate` | 124 | 64 | 299 | 155 |
| warm `keep` | 95 | 42 | 285 | 105 |

| V1 campaign (two speakers) | 100 turns, before | after | 500 turns, before | after |
|---|---|---|---|---|
| warm `open` | 55 | 28 | 293 | 87 |
| warm `post` | 108 | 63 | 525 | 176 |
| warm `generate` | 94 | 84 | 394 | 145 |

At 500 turns a warm call is about two to three times faster. A cold `open` is not faster: the first read of a chat must decode every file. The imports are unchanged (V1 about 0.2 s, one card about 0.05 s).

With the tracer on, the warm turn at 500 turns spends its time in `read.turns` (still reading and hashing every file, about 60 to 75 ms), `write.turn` (the durable commit, 20 to 90 ms) and `read.library` (about 12 ms).

## Known limits

- **A call still grows linearly with the chat's history.** Every file is still read and hashed, and the library is still loaded afresh on every call. A per-chat index that avoids reading every file, and caching the library view, are later work.
- **Only the scene runner is traced.** Imports are timed by the benchmark from outside; there is no hook in the importers, and no UI latency is measured.
- **No retrieval-quality check.** No context is reduced in this slice, so recall and continuity cannot regress by it; a check is due with the first change that does reduce context.
- **Synthetic numbers only.** No real provider latency or price is recorded; a `measured` latency needs a real provider call.
