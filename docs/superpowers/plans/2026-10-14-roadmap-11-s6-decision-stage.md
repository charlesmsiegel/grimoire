# Roadmap 11-S6: the Decision stage — plan

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-11-epistemic-history-retrieval-design.md`
§5 (the Decision stage), §8.2 (the Decision gate, and the live arm's judge
moved here by 11-S5), §9 (11-C1, 11-C4), §11 ("Decision stage" tests) and the
11-S6 slice. **Needs:** 11-S3, 11-S4 and 11-S5 (this branch); 01b-S2, 01c-S1,
01d-S4, 02-S5 and 02-S7 (on main: the capture helper, decide's no-sampling
contract, `escalation=` and `escalation_inference`, `_soft_resolved` and
`plan_deadline`, `store/play_decisions.py`, and the `epistemic_access` kit).

**Goal:** for an NPC's compose step, ask a capped, deadline-bound, fail-soft
`decide` call about the parts of its recalled history that the deterministic
classifier left `unknown` but that the actor had a structural channel to, and
fold the answers back into the view that step composes from. Off by default,
behind a switch hidden from Settings; off is byte-identical. Plus the evals:
the Decision gate (false-`known` rate apart from accuracy) and the live arm's
decide judge.

## 1. Where the slice moves the spec

Each is a spec amendment ("Amended by 11-S6").

1. **Answers re-enter the classifier; they are not patched onto slices.**
   `classify(..., decided=)` takes the answers keyed by part (scene key, part,
   re-matched posts) and applies them to the actor's own classes before the
   runs are cut, so a part that becomes `known` gets its text through the
   same `_runs` path every visible slice does. A part whose key no longer
   matches when the second pass re-reads the scene (a cut in between) stays
   `unknown`: answers fail closed. Re-classifying is one more read; it is the
   price of not having a second renderer.
2. **Invisible slices keep their `source`.** An eligible part's excerpt is
   the text of its posts as 09 handed them over (already the prompt-phase
   view), so the item builder needs the posts of a withheld slice. `Slice.source`
   is now set on every re-matched posts slice; `text` stays empty on an
   invisible one, and every renderer still reads `visible()` alone (11-C2's
   guarantee and its tests are unchanged). Moved posts still carry none.
3. **The (b) channel is read off one classifier pass over the roster.** The
   refine pass classifies the actor's perspective with `roster=present`, so
   `by_ref` holds every present ref's class of every post. "A later scene in
   play order" compares `scene_ids.parse_sid` numbers; a scene whose id is
   outside that grammar (a legacy id) is never "later", so it opens no (b)
   channel (it narrows, never widens). A teller has access to the part when
   every post of the part is visible to them; "recorded present" in the later
   scene is a post there both the actor and the teller `witnessed`.
4. **The regex-guard pin is replaced by a source test.** The spec asks for
   `epistemic_access.build_items` to be pinned in `test_regex_prompt_guard`.
   The item builder reads no transcript: every excerpt is a slice's `source`
   posts, which are 09's evidence and already went through 08's prompt-phase
   expansion (`searchdocs.expand(phase="prompt")`, itself pinned), and the
   actor's own `state.md`, which is not transcript text. A pin would need a
   `view(` call the builder has no posts for. Instead a test holds that every
   question's excerpt is built from slice sources and the state alone, and
   that a display-phase rule never reaches an item.
5. **The history view rides the frozen snapshot.** §5.5 stores the view on
   the response record "as `history_view`" so a roll resume recomposes with
   it. Recomposing needs the excerpt text, which `responses.json` (one file,
   written whole) should not carry per response. So the renderable view is
   stored inside the response's primary snapshot (`snapshot["history_view"]`,
   `epistemic.to_record`), which is per response, content-addressed, cloned on
   a branch and removed with its scene; the record carries the compact
   Decision record (`epistemic`: asked, skipped, the per-part answers) beside
   02's `intent`. The snapshot holds the view whenever a view was composed,
   switch on or off: it closes 11-S3's gap where a roll resume in a later
   request recomposed with no history at all. The prompt is unchanged by it.
6. **The play gate's `--play epistemic` feature lands with the PR that
   exposes the switch.** 11-S6 keeps the switch hidden and off, so the three-
   part gate (§5.4) is measured by the change that asks for ratification; this
   slice ships the two parts it can: the Decision gate and the leakage line.
7. **The Decision gate runs in `--epistemic --live`, not `--gate`.** `--gate`
   compares a frozen legacy parser with the decide parse; epistemic access has
   no legacy parser. The gate cases are ordinary decide cases (`schema` set,
   task `epistemic-access`) built in throwaway homes from the synthetic
   corpus through the production eligibility and item builder, run through
   `runner.live_all`'s decide chain, graded against their gold and reported
   as the **false-`known` line** (a permissive answer where the gold is not)
   apart from accuracy. The offline half (each gate case yields exactly its
   expected eligible part, with its channel) runs in `pytest backend`.
8. **The judge meters under `epistemic-access`.** A decide call needs a task
   on a route, and the routing guard fails a task nothing under `routes/`
   names, so an eval-only judge task cannot exist. The judge is a decide case
   on the `epistemic` route's one task, asking a predicate per stated fact;
   its rows land in its own throwaway home, priced like every live case.

## 2. Pieces

### 2.1 The classifier (`store/context/epistemic.py`)

- `ELIGIBLE_BASES = ("not_recorded_present", "scene_cast", "no_record",
  "summary_needs_grant")`.
- `Classified.stood: bool` — for an actor perspective, whether the actor
  stood in the item's scene: in its appearance record's `scenes` list, the
  chronicle `cast`, or with any interval there (§5.1 (a)). Read from the same
  `_Source` and `_Scene` the rules read; False for the narrator and for an
  unclassified item. In `detail()`.
- `Slice.source` on every re-matched posts slice (§1.2).
- `classify(perspective, items, *, roster=(), decided=None)`. `decided` maps
  `(scene key, part, posts)` to `(access, basis)`; applied to the actor's own
  classes only, only where the deterministic part is `unknown` with bases
  inside `ELIGIBLE_BASES`, and never producing `witnessed` or
  `narrator_only` (asserted). A summary answer of `known`/`suspected` shows the
  header as a whole-scene grant would.
- `EpistemicView.decided` counts parts with basis `decision` that became
  visible; `pending` lists the eligible parts (`(key, part, posts)`).
- `to_record(view)` / `from_record(record)`: the JSON form compose needs
  (perspective, items with their redacted item, slices with sources and keys,
  header, state, notes, `by_ref`, `sid`, `stood`; the evidence's coverage,
  ceiling, depth and digest, its items and candidates left out). A record
  that does not parse is `None` (the resume composes with no history, as
  today).

### 2.2 The items (`store/context/epistemic_items.py`, new, pure)

- `Part`: key, part, posts, text, scene sid, channel (`"stood"` or
  `"told"`), and up to two channel posts' texts for a (b) part.
- `eligible(view) -> tuple[Part, ...]`, over a view classified with
  `roster=present`, in 09's order (items, then slices): §5.1's three tests.
  The narrator's view yields nothing.
- `questions(parts, *, actor_ref, actor_name, state) -> tuple[AccessQuestion,
  ...]`: each excerpt is the part's text, then the actor's own `knows` and
  `suspects` (capped, `STATE_CAP` characters each), then the channel posts,
  inside `EXCERPT_CAP`; `basis=()` (§3.5). `evidence_ref` is
  `ref_of(part)`, a stable spelling of `(key, part, posts)`.
- `own_state(cid, actor_ref)`: the actor's `state.md` knows/suspects through
  `playstate.read_state` on the campaign's actor root; `("", "")` when it
  cannot be read.

### 2.3 The call (`routes/epistemic.py`, new; helpers, no router)

- Constants `EPISTEMIC_CEILING_S = 10.0`, `EPISTEMIC_DECIDE_ITEMS = 8`,
  `EPISTEMIC_ROUND_ITEMS = 16`, with §5.4's comments; `ceiling()` is
  `min(EPISTEMIC_CEILING_S, llm_call_budget)` when the budget is positive
  (as `plan_ceiling`).
- `RoundBudget(asked: int)` read off the round record's `epistemic_asked`
  (absent is 0), `left()` against `EPISTEMIC_ROUND_ITEMS`.
- `async def refine(view, *, cid, sid, client, post, round_id, budget,
  response_id) -> tuple[EpistemicView, dict]`: never raises an `Exception`.
  - the narrator, a `None` view, or the switch off: the view unchanged, no
    record, nothing read past the switch;
  - classify for the roster (threadpool), `eligible`; none: unchanged;
  - take at most `min(EPISTEMIC_DECIDE_ITEMS, budget.left())` in order; the
    rest `decision_skipped`;
  - `_soft_resolved(require_inference("epistemic-access", cid,
    operation="decide"))`; `None`: every eligible part `decision_skipped`,
    `skipped: <kind>`;
  - `operations.decide("epistemic-access", items, client=, resolved=,
    campaign=cid, scene=sid, post=post, round_id=round_id,
    response_id=response_id, capture=scope.hook(),
    around=plan_deadline(ceiling), escalation=lambda: run_in_threadpool(lambda:
    escalation_inference("epistemic-access", cid)))` inside
    `decision_capture.capturing(cid, sid, "epistemic-access")`; no
    `explain`;
  - `access_of` → `decided` per §5.3; `LLMError`, `DecideRequestError` or any
    other `Exception`: every asked part `decision_failed`, `skipped:
    <kind>` / `error:<Class>` logged at ERROR with the class name only;
  - the final `classify(..., decided=)` (threadpool), carrying the
    evidence and the round cost; the scope notes the parts asked, the
    answers, the mapping and any skip;
  - the record: `{"asked": n, "skipped": "...", "parts": [{"ref", "access",
    "basis"}]}`.
- The switch: `play_decisions.epistemic_decide(cid)` (`off` | `on`, default
  `off`), the campaign's own `campaign.md` value first, read through
  `_setting`; deliberately NOT in `VOCABULARIES`, so `effective`, `own`, the
  campaign PUT and Settings never show it. `config`: the key in
  `_CONFIG_KEYS` and its default, `ConfigUpdate`'s field and `_ON_OFF_KEYS`
  (a hand edit or an eval sets it); `GET /config` does not report it.

### 2.4 The turn path (`routes/character_turns.py`)

- `_refine_step(cid, sid, client, turn, actor, view, response_id)` after
  `_round_history`: skipped for the narrator, a `None` view, an appended or
  continuation step, and a key already refined in this request (the cached
  refined view is reused, never re-asked). Mints a response id when the turn
  plan did not. Persists `epistemic_asked` on the round
  (`responses.update_round`) after a call that sent items, and keeps
  `turn.round_record` in step. The refined view replaces the cached one.
- Replays never reach it: `_round_history` returns `None` when the round's
  pending response is incomplete (11-S3), and a reroll replays its snapshot
  without composing.
- `_prepare(..., epistemic=record)` stores the record on the response
  (`responses.prepare(epistemic=)`, stored only when given), and puts
  `epistemic.to_record(view)` into the snapshot as `history_view`. Its resume
  branch (`pending and appended`) composes with `from_record` of the pending
  response's snapshot when the request holds no view of its own.

### 2.5 Routing (`store/routing.py`, `store/epistemic_access.py`)

- The `epistemic` route joins `ROUTES` (after `history_check`), and
  `TASK_POLICY["epistemic-access"]` is the kit's row, spelled in routing with
  literals (the kit imports routing, so routing cannot import the kit); the
  kit's `ROUTE` and `policy()` become reads of routing's, and a test holds the
  literals to the kit's constants. `NO_LEGACY`, so the frozen baselines are
  unaffected.
- `evals/cases.py`: `decide-epistemic-access` moves from `KIT_CASES` into
  `CASES` (as 09-S5 moved the rerank case).

### 2.6 The capture's accept (`frontend/src/components/EpistemicAccept.tsx`)

- A slice whose basis holds `decision` shows the answer ("Decision:
  known"), and its first button accepts that answer as the user's word:
  `knows` or `suspects` for a visible answer, `unaware` for a withheld
  `decision` answer. Every other button stays as 11-S3 made it. Abstained,
  failed and skipped parts offer only what an `unknown` part always did.

### 2.7 Evals

- **Cases.** `evals/epistemic/cases.py` gains case 7 (Mara told, in a later
  scene, by Winifred, who was present at the revelation: deterministic
  `unknown`, gold `known`), case 15 (Mara met Winifred later, and the later
  scene shows Winifred keeping it from her: gold not known) and case 15b (a
  legacy scene with Mara in its cast, whose post says Mara had already
  gone: gold not known). Offline they are ordinary leakage checks (the needle
  absent from Mara's prompt). Each carries a `gate` (the needle whose part is
  asked, and its gold class set). Case 10 gets a gate too (gold: permissive).
- **The gate** (`evals/epistemic/decision.py`): per gate case, a decide
  `Case` (task `epistemic-access`, `schema` set) whose build plants the case
  in its throwaway home, classifies Mara over the roster, takes `eligible`,
  builds `questions` and `build_items`, and whose grade maps the reply
  through `access_of` and checks gold. `false_known(records)` and
  `accuracy(records)`; `gate` passes with **zero false-`known` answers over
  every repeat** (the bar, set here before any run) and every case answered.
  Offline: `eligible` yields exactly the expected part and channel for each
  gate case, and canned replies exercise the grader.
- **The judge** (`evals/epistemic/live.py`): a check's `facts` state each
  `live_forbidden` token's secret as a sentence (case 3's whisper gains a
  plain-language secret beside its token). After the replies, each answered
  exposed reply is judged by a decide case (task `epistemic-access`, one
  `decisions.Predicate` per fact: "Does the reply state or clearly imply
  this fact?"). A yes joins the reply-leak line; a judge that failed or did
  not answer leaves that reply unclean. Recall stays apart.
- **The mode**: `--epistemic --live` resolves `chat` and `epistemic-access
  (decide)` before any home moves, prints both, and runs the replies, the
  judges and the gate; the summary carries the gate's lines.
- `test_operation_guard`'s eval-task check holds the gate and judge task to a
  decide route.

## 3. Tests

`backend/tests/test_epistemic_refine.py` (with `llm_fakes`), each of §11's
"Decision stage" bullets:

- a refine call reaches a meter (the `escalation=` regression);
- the gate: no call when every unknown lacks a channel, none for the
  narrator, none for `moved`, `unverifiable`, `unkeyed` or override-group
  parts; (a) and (b) channels each open one; a legacy-id later scene opens
  no (b) channel;
- both caps and `decision_skipped`, with the round cap surviving a resumed
  round (the count read back from the round record);
- each §5.3 row, `experienced` → `known`, and a failed escalation hop;
- a failed call, a deadline overrun (patched clock: a second chunk refused
  unsent as `DeadlineRefused`), no `_noting` on the overrun, a soft
  resolution failure, and an exception in item building each leave the
  parts withheld, and the turn completes;
- a round with a pending incomplete response runs no retrieval, classify or
  refine; a reroll replays the snapshot and makes no call;
- off is byte-identical and reads nothing past the switch; the switch is in
  neither `effective()` nor `GET /config`;
- a roll resume in a later request composes with the stored view, and asks
  nothing;
- a decided part re-matched after a cut stays `unknown`;
- the capture is filed with the parts and answers, and no basis code
  reaches an item.

Plus: `test_epistemic_prompt.py` (a decided `known` part renders under "You
know this:", its basis never in a message); `test_epistemic_evals.py` (cases
7, 15 and 15b offline; the gate's eligibility; the gate and judge graders on
canned replies); `test_epistemic_live.py` (the judge's verdict on the
reply-leak line, a failed judge never clean, the mode resolving both tasks
first); the frontend's accept of a decided part.

## 4. Repo rules

- Placeholder names only (Seraphine, Mara, Winifred, Ilse; Realm,
  Saltmarch). No store content in any committed file.
- Lock domain: `routes/epistemic.py` takes no campaign lock; its round write
  goes through `responses.update_round`, which takes one. The classifier and
  item builder stay read-only.
- Imports at module scope; `store/context/epistemic_items.py` binds
  submodules across packages.
- The spec gains "Amended by 11-S6" notes for §1's eight points; CLAUDE.md's
  play-decisions paragraph gains the switch; `evals/README.md` the gate and
  the judge; `ROADMAP-CHECKLIST.md` ticks 11-S6.

## 5. Plan gate dispositions

Plan gate: an independent adversarial reviewer (substitute for the Codex CLI
gate). Verdict "not ready"; every finding is taken, and where these differ
from §§1–4, these win.

| # | Finding | Disposition |
|---|---|---|
| 1 | A summary withheld because a post-listed `unaware` reaches the actor read as `summary_needs_grant`, so a `known` answer could reopen what the user withheld | **Fixed.** That case's basis is now `override` (an override's doing), outside `ELIGIBLE_BASES`; a test plants it with a whole-scene grant and a canned `known` and the summary stays out of the prompt |
| 2 | The policy row escalated without the evidence 01d requires | **Taken (amendment 9).** The task lands with escalation off (`TaskPolicy()`), as every task does; `refine` passes no `escalation=`; 02-C5a's row stays the kit's `policy()`, the candidate to switch on with its "Decision escalation" evidence. §5.2's "escalation= is required" is amended |
| 3 | A resume after a rename would hand compose a view for the old sid | **Fixed.** `_stored_view` keeps a stored view only when its identity is the run's, rebinding the sid; anything else composes with none. Tested |
| 4 | A writable key `GET /config` never reports fails #410's test | **Fixed.** `GET /config` reports it; no Settings control draws it (§5.4's "hidden") |
| 5 | The gate ran a different chain than production, and had no summary negative | **Fixed.** With escalation off the gate's chain (`run_stages`) is the one production sends; case 8's secret summary is a gate negative |
| 6 | The snapshot stored withheld text | **Fixed.** `to_record` keeps text and source for visible slices only; a test holds a withheld needle out of the stored record |
| 7 | Stop during refine could still send a paid generation | **Fixed.** `_refine_step` checks `cancel_requested` after the call |
| 8 | Off is not byte-identical for a cross-request roll resume | **Taken (amendment 10).** With the switch off nothing is resolved, called or minted, and a fresh compose is unchanged; a roll resume in a later request now composes with the view its response was composed with instead of none, which is 11-S3's gap closed, not the switch |
| 9 | The registered kit case carried a basis production never sends | **Fixed.** `basis=()` |
| 10 | `_soft_resolved` must take a thunk | Already so in code |
| 11 | "A second chunk refused unsent" cannot happen at 8 items | **Taken.** The deadline test spends the clock before the first call, which is refused unsent |
| 12 | `EXCERPT_CAP` reads as the kit's | **Fixed.** `EXCERPT_CAP` and `STATE_CAP` are `epistemic_items`' own, argued structurally |
| 13 | Pinned tables and docs | **Fixed.** `test_routing`, `test_inference_settings`, `test_task_policy`, `test_evals`; CLAUDE.md's decide-route list; the route's text says it is off unless switched on |
| 14 | A replay peek that fails reads as "not a replay" | **Recorded, not changed.** `_history_replays` is a hint by design (11-S3): an unreadable responses file fails `_prepare` under the lock anyway, so the most a misread costs is one refine whose answers the replay discards. Said in the spec amendment |

Two more amendments from the implementation:

9. **Escalation off at landing** (finding 2): 02-C5a's escalating row is not
   registered; the operation guard's rule (`escalation=` exactly when the
   policy escalates) then means `refine` passes none.
10. **Off is free, not frozen** (finding 8): what the switch guarantees is
    that nothing past it is read, resolved, called or minted, and that a
    fresh compose sends what it sent before. The roll-resume fix rides
    alongside, because the view is stored whenever one was composed.
