# 02-S5: Play gate harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** `evals/run.py --live --play FEATURE` measures what a play decision
does to play, at what cost, on seeded synthetic fixtures. The offline half is
in `make check`. Delivers 02-C6 (part: the play gate of §4).

**Spec:** §4, §5.7, §6.6, §7.5, Slices (02-S5).

## Design

- **`evals/play.py`:**
  - `FIXTURES`: the four of §5.7, placeholder names only.
  - `Selection` (`provider/model[#preset]`), `PlayConfig` with `axes()`
    (`feature`, `scene_selection`, `decision_selection`, plus `sampling` and
    `cutoff` for the speaker), and `matrix()`.
  - `read_selected` copies the selected providers' files (and presets) out
    of the real store before any isolate.
  - `seed` writes them, the format-2 Primary and Decision selections, the
    switch, and turns off the tracker, rolling summary and scene-break.
  - `build` and `play` send a real `POST /chat` through `create_app()` and
    return the drain context.
  - `collect` reads the rounds' picks and speakers and each contribution's
    text, intent and issue.
  - Graders:
    - per run: turn checks (non-empty, authority, handoff), legality, no
      sampled abstention, hand-back preserved, the leak check, and mass on
      intended over the drawn weights;
    - across runs: `gate()`, the five hard criteria;
    - `summary()`: latency, stance diversity, spread, departures, addressed
      overrides, mass.
  - `run_all` puts each (config, fixture, repeat) in its own isolate. It
    checks the tripwire before building and before harvesting, and drains
    before it harvests (a stuck drain stops the run). Each run yields a
    `runner.Result` (rows, wall, per-task buckets) for the report and the
    run file.
  - The `cutoff: 0` arm swaps `response_protocol.pick_eligibility` for the
    run.
- **`evals/run.py`.** `--play FEATURE` with `--scene` and `--decision`
  (repeatable) is refused without `--live`, refuses case-run flags, defaults
  `--repeat` to 5, prints the case report, the gate and the summary, writes
  `--out`, and exits 1 when a hard criterion fails.
- **Per configuration.** `gate.no_turn_regression` compares each "on"
  configuration with the fixture's "off" runs on its own, so one that always
  regresses is never masked by another. `gate.off_is_free` fails an "off"
  run that ran its feature: a row under its task, a stored speaker draw, or
  a tool offered.
- **Drain.** A run whose follow-ups outlive the drain raises
  `runner.FollowUpsRunningError`, which `temp_home` keeps the home and the
  environment for. A send that raises is a failed run that is still
  drained.
- **Owed before 02-S6.** Two things are not done here:
  - §6.6's model-judged adherence. `adherence_item` is the item, but the
    harness does not spend on it yet. It must be asked, and reported, by
    the live run whose report ratifies the intent and plan switches.
  - §4.2's ensemble turn graders (`turn-taking`, `roll-fence`,
    `scene-length`, `owned-lore`). They score a multi-speaker reply against
    their own fixtures (a nominated lead, a roll prompt, an owned secret),
    which the play fixtures' single contributions do not carry. The per-run
    `turn.*` checks (non-empty, authority, handoff) stand in for them until
    the fixtures carry what those graders need.

## Tests

`tests/test_play_gate.py` runs the harness end to end against a scripted fake:
- refusals without `--live` and without selections;
- the matrices;
- nothing written to the real store, and each isolate keyed with exactly
  the selected provider;
- rows stamped `scope: "eval"` with the fixture campaign;
- a well-behaved intent run passes the gate (off files no feature row, a
  typed note is never asked, the pending fixture hands back);
- a skipping configuration fails `gate.on_is_on`;
- the speaker gate stores picks only when on;
- a consistent turn regression fails, and a mixed one does not;
- mass on intended reads the drawn weights;
- the leak check on recorded replies.
