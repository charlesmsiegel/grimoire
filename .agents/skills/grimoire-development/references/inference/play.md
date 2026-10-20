# Sampling and play decisions

Source: [draws.py](../../../../../backend/src/grimoire/draws.py), [play_decisions.py](../../../../../backend/src/grimoire/store/play_decisions.py), [README.md](../../../../../evals/README.md).

- **Sampling a decision goes through `draws.py`, and only there** (roadmap
  01c). A caller that wants a probabilistic outcome calls `draws.draw(q,
  result, seed=draws.new_seed(), purpose=…, eligibility=draws.Eligibility(…))`,
  and the only reshaping is that recorded `Eligibility` (`only`, `exclude`,
  `cutoff`, `floor`). Its `floor` is sized to the eligible set (at most
  `1 / len(eligible)`) and refused up front otherwise, so no report can make
  a draw raise mid-turn. `draws.draw_from` is not for callers: it takes raw
  weights and so skips the rules below. The draw is SHA-256 and integer
  arithmetic (`quanta`), never `random` and never a float sum, so a record
  replays to the same key on 3.11 to 3.14, on Android and in a browser, and a
  change of rule is a new `ALGORITHM`. Nothing that reported no distribution
  is sampled. An answer of None is `basis: none`. A structured answer, a
  report under `1 - MASS_SLACK` in mass, a report that contradicts its own
  answer, and a rank's or selection's `marginals` are each `basis: answer`,
  `sampled: false`: acted on, and never replayed. The helper stores nothing.
  The caller files `Draw.record` in the same write, under the same lock, as
  the outcome it chose (for the speaker pick, the round record through
  `responses.update_round`). A present record means decided, a decided
  outcome is never re-drawn, and a reroll mints a new seed. Four tasks may
  sample (`routing.TASK_POLICY`): the speaker pick, the turn intent and plan's
  stance, and the play decide tool's selection -- each only behind its play
  decision switch, and only the speaker draw is on by default (below).
- **Play decisions (roadmap 02) are soft, and only the speaker draw is on
  by default.** Three switches (`decide_speaker_sampling`, `decide_turn_plan`
  = `off` | `intent` | `plan`, `decide_play_tool`), each a `config.md` key
  with a `campaign.md` override (`PUT /campaigns/{cid}/play-decisions`), are
  read through `store/play_decisions.py` alone and shown in Settings, Play
  decisions. The user ratified all three for the UI (02-S6) with the speaker
  draw `on` (it adds no call: the pick was already asked) and the two that
  each add a call `off`. A global value a switch does not know reads as off.
  The live play gate (`evals/run.py --live --play`, `evals/play.py`) measures
  them; the current evaluation procedure is in `evals/README.md`. A turn
  decision off is byte-identical: nothing is resolved, called or written.
  On, a turn decision is asked before the contribution is composed
  (`character_turns._turn_plan`), resolved through `_soft_resolved`, bounded
  by one `common.plan_deadline()` for the whole decision (a spent one is
  `DeadlineRefused`, unsent, never noted against the connection), and caught
  whole: any failure is recorded `skipped` on the response's `intent` and the
  turn plays on. Its answer is one system message after the history
  (`compose_turn(plan=)`, `store/turn_plan.block`), never a catalog section,
  frozen into the snapshot and carried into a roll resume and Keep writing; a
  retry and a reroll never ask again. The speaker draw is stored as the
  round's `pick` with its speaker, and a round holding one is never re-picked.
  The decide tool (`decide_choice`) is offered only to a fresh NPC
  contribution or a reroll, on the `scene` route (`routing.TOOLS_OPTIONAL`),
  once, before any text the watcher would show (`stream_tools(visibility=)`),
  and its record rides the variant as `tool`. Routes born at format 2 carry
  `routing.NO_LEGACY` and their tasks sit in `NO_LEGACY_TASKS`, which the
  frozen inference baselines leave out. `store/history_rerank.py` is the
  relevance kit used by 09-S5's captured `routes/history_recall.py` caller
  on the `history_check` route, with no sampling or escalation and its
  registered replay case. The rerank switch defaults off, and an ungraded
  candidate keeps its RRF slot. Roadmap 10's planner shares that phase:
  `history_plan` (`off` by default) lets a narrator step's `thin` or `empty`
  first retrieval (never an NPC's: 10 plans for the narrator only)
  ask the `history_plan` route (Fast, `NO_LEGACY`) for one structured plan
  (`store/history/plan.py`: records offered by handle, every bound in the
  reader), merge one planned round under the same depth and ceiling, and --
  gated on a `history-sufficiency` decide on `history_check` that asks no
  rationale and reads a non-answer as `unknown` -- take at most one repair
  plan, never another (`routes/history_recall.plan_and_repair`). Its
  `PlanTrace` rides the evidence and the inspector row, never the prompt, and
  its stable fields are what 12 reads. `store/epistemic_access.py` is the kit 11-S6's
  `routes/epistemic.py` decides with, on the `epistemic` route. Its task landed
  with escalation off. The kit's `policy()` is the candidate row, to be
  switched on only with its "Decision escalation" evidence. Its eval case is
  registered, and `evals/cases.KIT_CASES` is empty.
