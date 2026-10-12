# 02-S3: Sampling (speaker and stance) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** with `decide_speaker_sampling=on`, draw WHO speaks from the pick's
reported distribution after the issue mapping has read the answer, and store
the draw's record with the speaker. The turn intent's stance is drawn the
same way. Delivers 02-C2a (full) and 02-C2b (full).

**Spec:** §5, §6.5, §10, §13 ("Speaker sampling"), Slices (02-S3).

## Design

- **`group_play.named(text, entries, author_ref=None)`.** The planner's name
  matcher, made public, which now drops the author itself. Both planner
  callers pass `author_ref`.
- **`response_protocol`** (pure):
  - `addressed(trigger, eligible, author_ref)`: the refs the trigger names,
    less its author.
  - `pick_eligibility(offered, addressed)`: `exclude=(NONE_KEY,)`,
    `only=addressed or None`, `cutoff=1/(2n)`, no floor.
  - `sampled_pick(item, result, offered, addressed, seed)`: `draws.draw`
    with that eligibility. When it returns `none` with `ineligible` or
    `cutoff` over an answered speaker, it draws again with the same seed and
    purpose and the none excluded, nothing else.
  - `next_of(selection, draw)`: only a read speaker is drawn over.
- **`routes/character_turns.py`.**
  - `_selector_inputs` gathers the item and the trigger: the typed note, else
    the newest in-context, non-synthetic post as the model is shown it,
    whose author is mapped to a ref through `responses.actor_refs`. It is the
    regex-guarded reader now, with `_intent_item`.
  - `_select` returns `(next, issue, pick)`: `pick` is None when off. A draw
    `ValueError` leaves the answer standing, stores no pick, and logs at
    ERROR by class.
  - `_first_actor` stores `pick` in the `_round_state` write that stores the
    speaker. A round whose `pick` is present is decided and never re-picked,
    hand-backs included.
- **The stance.** `turn_plan.drawn_stance` uses `stance_eligibility()` (none
  excluded, `cutoff=1/(2*len(STANCES))`). An answer of None is never drawn.
  `sampled` and `answer` give the value; a cutoff that empties the set
  leaves the plain answer. The intent record gains `draw`.
- **Policy.** `TASK_POLICY` gains `response-selector` and `turn-intent`:
  `samples=True` with their question ids, escalation off.
  `test_task_policy.py`'s `_SAMPLES` and its landing test name both.
  `reads_declines` waits for the escalation it qualifies.
- `decide-speaker` already carries native recordings with a distribution
  (01c-S3), so this slice adds none.

## Tests

`tests/test_speaker_sampling.py`:
- the eligibility;
- many seeds: always offered, never the none, never below the cutoff, and
  the record replays;
- the addressed narrowing;
- an answer outside `addressed` with no report is kept by the re-draw;
- a cutoff that empties the addressed set keeps a speaker;
- the issue mapping for abstained, refused, error and off-option, each
  drawing `none`;
- a structured answer is `answer`/`no_report`;
- `addressed` and `named` drop the author;
- stance draws, an abstained stance and a structured stance;
- the route: off stores no pick; on stores a seeded, replayable pick with
  its speaker; a typed note narrows; a handed-back pick is never asked
  again; a draw defect leaves the answer.

`test_turn_intent.py` reads the stance's `answer` record on a structured
reply.
