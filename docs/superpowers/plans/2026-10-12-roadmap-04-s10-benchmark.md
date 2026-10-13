# Roadmap 04 S10 — Overview benchmark on synthetic libraries

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C3b in full (spec 04 sections 9, 12.3), and the measured
registration ruling S7 and S8 deferred to it.

## Tasks

1. `scripts/synth_library.py` (03-C9) gains `generate_library(target,
   profile, seed)` and `--out/--profile/--seed`: store writers only
   (worlds, characters, entities, greetings, campaigns, scenes and posts,
   both ledgers, review sidecars, a candidate cache, solid-colour PNGs
   through `assets.put_image`), placeholder names with an index, a seeded
   nonsense vocabulary, a seeded `now_iso` patched over every module that
   bound it, file and directory mtimes backdated deepest first, the
   `.synthetic-library` marker, a non-empty target refused. Profiles `small`,
   `medium`, `large`, each knob justified by the boundary it exercises
   (`large` passes `statcache.MAX_ENTRIES` scene files and has one campaign far
   larger than the rest). 03's `generate(target, Shape)` is unchanged.
2. `scripts/bench_overview.py`: the privacy guard (marker required; refuses
   the default store, the pointer's store, the inherited `GRIMOIRE_HOME`, and
   anything inside or containing them; reads only the pointer's path value);
   every scenario on a fresh temporary copy; the five endpoints; the eight
   scenarios of 9.2; per scenario and endpoint the median wall time, the last
   run's counters and the audit-hook open/list counts; output without names,
   ids or paths; `--register shipped|none|all|KIND,...` and `--clocks
   injected|wait`.
3. `statcache` counter names never carry a path (a few memos key on one);
   `compiled.store.<kind>` also counts a `derive`'s staged family.
4. **The ruling.** On the synthetic profiles, with the compiled layer's
   control snapshot settled: a lookup costs more than parsing a `world.md` or
   `campaign.md`, and more than computing the whole continuity summary, at
   every size the profiles reach — 03's own ruling for the shelf rows — so
   `world_row`, `campaign_row` and `continuity_summary` stay declared and
   unwired. A transcript parse grows with the transcript and passes a
   lookup's fixed cost for long scenes, so `scene_turns` is registered with an
   admission size (`overview.read.SCENE_TURNS_PERSIST_MIN_BYTES`, twice the
   observed crossover, the `tokens.PERSIST_MIN_CHARS` precedent); a shorter
   scene keeps the in-process memo and is never stored or warmed. No timing
   figure is committed (spec 04 section 9.2, Open question 10).
5. `tests/overview_support.register_overview_kinds` registers the candidates
   and drops the admission size to zero, so the persisted layer stays tested
   whatever ships.

## Tests

`tests/test_overview_bench.py` (inside `make check-py`, on `small`, clocks
injected, kinds registered): warm after restart (no `campaign.md` parse, one
head parse per scene, no transcript parse on the shell, no continuity
compute on Todo); warm same-process lists no `scenes/`; a relevant change
costs one head and one listing; visit after a real turn hits the warmed
count; an unrelated change misses nothing; a one-campaign change misses only
that campaign's summary; the shell and Todo share one continuity compute; no
closed scene's transcript is read in any state; `character.md` parsed at most
once across two Todo reads. The guard's refusals (unmarked, each real root,
inside each) and an output with no name, id or path. In
`tests/test_overview_persisted.py`: the shipped registration, and a short
scene never persisted while a long one is.
