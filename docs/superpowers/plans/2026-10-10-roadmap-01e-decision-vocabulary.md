# 01e Decision Vocabulary: Implementation Plan (slices S1–S5)

**Goal:** Add `Rank`, a finer `Score` (`expected`, `tiers`), `MultiSelect`
and `Joint` to the decide vocabulary, on the structured path and through a
native lowering. No backend changes which answers, and no call site converts.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01e-decision-vocabulary-design.md`.
Section numbers below are the spec's. Each slice has its own section and
can be read on its own. The slices landed as one commit each, in order, on
one branch.

## Global constraints

- `decisions.py` stays a gateway leaf: it imports nothing from the package
  (§10). Value objects, the lowering and `tiers` all live there.
- The prompt of every existing decide call site stays byte-identical (§11,
  "Acceptance"). Proved two ways: a one-off render against the pre-01e
  templates from git, kept out of the repo, and `verify_templates.py`
  requiring that no new kind's bullet or question line appears in a batch
  that asks no such question.
- Every new answer is `None` with a reason when nobody gave it. A structured
  answer never carries `distribution`, `marginals` or `expected`.
- Fixtures use placeholder names only (Saltmarch, Mara, Winifred,
  Seraphine). No constant is justified by a measured library.
- Gates: `make check-lint`, `check-mypy`, `check-templates` and the decide
  test files run green before each commit, and the full `make check` runs
  before the PR.

## 01e-S1: Answer fields, `expected` and `tiers`

Files: `decisions.py`, `tests/test_decisions.py`.

1. `Answer` gains `marginals: dict[str, float] | None` and
   `expected: float | None`, placed before `stated` so positional
   construction is unchanged. `__post_init__` checks that marginals are
   probabilities keyed by string, and that `expected` is finite.
2. `native_answer` sets `expected` for a `Score` from the validated
   distribution: `sum(i * p) / sum(p)`, None for no distribution or no
   mass. It rides an abstained tie, and is never read from a provider's
   `score`.
3. `tiers(values, tolerance=MASS_TIE)` is greedy from the top, anchored on
   each tier's highest value. Within a tier, keys keep the mapping's order.
   A non-finite value or a bool raises `ValueError`.
4. `outcome` writes `marginals` and `expected` under `_present`'s rule.
5. Tests: `expected` over the reported mass, on a tie, and absent when it
   should be; the `Answer` checks; `tiers` with exact ties, ties within
   `MASS_TIE`, the anchor rule and integer levels; `outcome` with the new
   fields.

**Deviation, recorded:** S1's acceptance says `test_native_decisions.py` is
unchanged. Three of its asserts compare a whole native `Score` answer,
which now carries `expected`. They compare the answer with `expected`
cleared, then check `expected` separately. The answer and distribution
under test are unchanged.

## 01e-S2: `Rank` on the structured path, and the template switch to `KIND`

Files: `decisions.py`, `inference.py`, `templates/decide/{system,user}.j2`,
`scripts/verify_templates.py`, `evals/cases.py`, `evals/recordings/`,
`evals/README.md`, tests.

1. Add a `KIND` `ClassVar` to every question class, and the `Rank` and
   `Ranking` value objects. `Ranking.flat(tiebreak, *, rest=False)` raises
   on a tie when no tie-break is given. `rest` is left out unless asked
   for, and then flattens as one bottom group under the same rule, so the
   unranked set is never ordered silently.
2. `validate`: share the choice's option rules through `_check_options`.
   The choice's messages stay worded as they were. Candidate count is
   `2..MAX_RANK_CANDIDATES` (32), and `top` is in `1..n`.
3. Schema: an array of an enum, nullable as `anyOf`, with no array
   constraints. `enum_values` gains a branch, and `kinds(items)` is added.
4. Parse: `_match` (shared with `_read_choice`), `_read_ids` and
   `_read_rank`, under §4.2's rules.
5. `outcome` writes a ranking as `{"tiers", "rest"}`. `render` writes
   singleton tiers as an id list, and a tie as null.
6. `native_gap` stub: refuse every rank.
7. Templates: `user.j2` branches on `q.KIND`. `system.j2` takes `kinds`
   and renders the ranking bullet only when a rank is asked.
   `structured_messages` passes `decisions.kinds(items)`.
8. `verify_templates.py` passes `kinds` to its direct renders, checks that
   no new kind leaks into today's `_DECIDE_ITEMS` renders, and adds a
   second set per new kind.
9. Eval case `decide-rank`: five Saltmarch scenes, `top=2`, with
   `pointwise` set. Its baseline recording is a valid reply. Its
   counterexamples are a duplicate, an unknown id, a short reply, a null
   and a wrong order. The grader `grade_decide_vocabulary` covers the
   question block and bullet (both rendered from the templates), the
   schema, and `decide.json`, `known_ids`, `answered` and `answer`.

## 01e-S3: `MultiSelect` on the structured path

Same files as S2.

1. `MultiSelect`, with a `most` property: `max`, or n when `max` is None,
   so `max=0` means zero. Options are `1..MAX_SELECT_OPTIONS` (32), and
   `0 <= min <= most <= n`.
2. Schema as for a rank. `_read_select` returns the ids in option order,
   reads `[]` as a real answer, and makes any count outside the bounds
   unreadable.
3. `outcome` and `render` write a selection as a list. A `native_gap` stub
   is added, and the bullet and question line render only beside a
   selection.
4. Eval case `decide-select`: four onlookers, `min=1`. Its baseline is a
   valid reply. Its counterexamples are a duplicate, an unknown id, an
   empty selection (below `min`), a null and a wrong selection.

## 01e-S4: `Joint` on both paths, and the native lowering framework

Files: `decisions.py`, `openrouter.py`, `openai_compatible.py`, templates,
`verify_templates.py`, evals, tests.

1. Add `JOINT_SEP = "=>"`, `joint_key`, `Pair` with `.key`, and
   `split_joint`. `split_joint` refuses an empty head, an empty tail and a
   second separator.
2. `Joint.choice` is the flattened `Choice`. `_check_joint` covers the
   separator, aliases, unknown or repeated `tails` keys and non-`Option`
   entries, then the option rules on the flattened pairs.
3. Schema and parse go through the flattened choice. `_joint_answer` splits
   the chosen key into a `Pair`. `outcome` and `render` write the key.
4. `head_marginal` and `head_first` (via `regrouped` on the key) exclude
   `NONE_KEY`.
5. `native_form(item) -> (Item, Lift)` and `native_lift(item, result,
   lift)`. An item of plain kinds lowers to itself, so its bodies are
   byte-identical. Both adapters' `decision_body` send the lowered item,
   and `decision_result` reads against it and then lifts.
6. `native_gap` checks a joint's flattened choice against the native
   option limit, and refuses any lowered-id collision.
7. Eval case `decide-joint`: three actions with legal targets. Its
   baseline is a valid reply. Its counterexamples are an illegal pair, a
   bare head that takes a target, a null and a wrong pair. A single-string
   answer has no duplicate or short form.

## 01e-S5: Native `Rank` and `MultiSelect` through pointwise predicates

Files: `decisions.py`, tests, evals, 01's Appendix B.

1. `_lowered`: a rank with `pointwise` becomes `Predicate(f"{id}#{n}",
   f"{pointwise}\n\nCandidate {opt.id}: {opt.description}")`. A selection
   becomes the same per option, worded by `NATIVE_SELECT_TEXT`.
2. `_lift_rank` and `_lift_select`, under §4.3 and §6.1, reading only
   each lowered `Answer` (its answer, reason and probability).
3. `native_gap` drops the stubs. It refuses only a rank with no
   `pointwise`, and a lowered-id collision, naming both caller questions.
4. Tests: the lowering's shape, and the lift cases (missing, all refused,
   equal P(true), 0.5 with and without `allow_none`, out of bounds),
   through both adapters with canned bodies. Also a refused-unsent rank
   answered by a structured fallback, and `native_unrepresentable` in
   `Decision.errors` with no fallback.
5. Native recordings for the three eval cases (OpenAI bodies), with one
   counterexample each: a tie, a 0.5 option, and an unchosen argmax. The
   README describes running the vocabulary cases under both
   `--decide-backend` values.
6. Re-check both decisions references and both structured-output
   references, and record the result in 01's Appendix B.

## Gate record

The Codex CLI is not installed in the environment this was built in. Each
Codex gate (plan, `/codex:review` on the diff, final spec check) was
replaced by a substitute review: a hostile correctness review of the diff,
and a spec-conformance review of the diff against the spec. Their findings
and how each was resolved are in the PR description. The Codex gates are
still owed, as the checklist's `[~]` spec-gate entries already record for
every roadmap spec.
