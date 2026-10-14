# Roadmap 11-S5: live leakage evals — plan

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-11-epistemic-history-retrieval-design.md`
§8.2 (the live arm of 11-C4) and the 11-S5 slice. **Needs:** 11-S3 (in this
branch), 01a-S2 and 01a-S3 (on main: live evals metered in a throwaway home,
and per-case cost, latency and tokens).

**Goal:** `evals/run.py --epistemic --live` generates each checked NPC's
reply to a question about the planted history and reports a **leakage line**
apart from a **recall line**: a configuration that recalls more but leaks more
is a regression on the leakage line whatever its recall says. Offline,
`--epistemic` runs the deterministic suite through the same reporter, and the
live graders run in `pytest backend` against canned replies.

## 1. Where the slice moves the spec

Each is a spec amendment ("Amended by 11-S5").

1. **The judge waits for 11-S6 (the user's decision, 2026-10-14).** §8.2 asks
   for "a needle check first, a decide-based judge second". A decide call
   needs a route and task the routing guard accepts, and the one this spec
   plans for it, 02-C5a's `epistemic` route, lands with 11-S6's call site
   (`test_routing_guard` fails a route whose task nothing in the package
   calls). So 11-S5 ships the needle check, and 11-S6 adds the judge on that
   route. Needles are invented tokens (`REVEALNEEDLE1`), so a reply that
   carries one used the planted text; a paraphrase is what the judge will
   catch.
2. **The question rides `appended`.** Each live check asks its NPC one user
   question about the planted scene (`compose_turn(appended=)`), so the
   transcript is untouched and every check composes from the same store.
3. **Case 3's live expectation.** The whisper without an override is visible
   offline by design (R3); live, Mara must not repeat it. The case carries
   that as `live_forbidden`, graded only on a reply.

## 2. Pieces

- `evals/epistemic/cases.py`: `Check` gains `ask` (the question; a default
  built from the case) and `live_forbidden`. Cases 1, 2, 3, 4, 8, 9, 13 get
  questions that invite the planted facts.
- `evals/epistemic/graders.py`: `reply_leaks(reply, forbidden)` and
  `reply_recall(reply, allowed)` (the share of allowed needles a reply uses;
  the structural phrases like `You know this:` are prompt-only and excluded).
- `evals/epistemic/live.py`: per case, per NPC check with a question:
  compose (as offline), then `inference.generate("chat", ...)` under a
  `usage.meter` in the case's throwaway home (`runner.isolated` refuses the
  real home), harvest the ledger rows for the case (`runner.harvest`) and fold
  them into the three money columns (`costs.fold`), as 09's live arm does.
  Each record carries the reply, `leaks`, `recall`, wall time, tokens and
  money.
- `evals/epistemic/report.py`: `summary(records)` prints the leakage line
  (`leaked replies / replies`, with each leak's case and needle) and the recall
  line apart; `gate(records)` passes only with zero leaks offline (prompt
  needles) and, live, zero reply leaks.
- `evals/run.py --epistemic`: offline by default (the deterministic suite,
  reported through `summary`); `--live` adds the replies, resolving `chat`
  from the real store first (`runner.resolve_connections`), then running
  every case in a throwaway home. `--out` writes a run file
  (`runfile.build`). Refuses the flags that belong to other modes.
- Tests (`backend/tests/test_epistemic_live.py`): the graders on canned
  replies (a leak, a paraphrase-free pass, recall); the live runner end to
  end with a fake client (one generation per NPC check, the question in the
  last message, metering rows harvested, money columns filled or `None`, a
  planted leaking reply reported on the leakage line and failing the gate);
  `run.py --epistemic` dispatch, its refusals, and that `--live` resolves
  before any home is repointed.

## 3. Repo rules

- Placeholder names only; every store is a throwaway home.
- No new route or task: the arm generates under `chat`, as play does.
- `evals/README.md` documents the arm and the judge's deferral; the spec's
  11-S5 slice text and §8.2 carry the amendment.

## 4. Review dispositions

Plan gate: an independent adversarial reviewer (substitute for the Codex CLI
gate). Verdict "not ready"; every finding is taken, and where these differ
from §§1–3, these win.

| # | Finding | Disposition |
|---|---|---|
| 1 | 11-S3 already keeps every forbidden needle out of an NPC's prompt, so a reply can only leak a needle its prompt held: only case 3 exposes one | **Taken.** The leakage line is over *exposed* replies (the prompt held a `live_forbidden` token). A reply carrying a forbidden token its prompt never held is a harness fault (contamination), reported apart. `--repeat` (≤ `MAX_REPEAT`) gives case 3 more than one sample; the amendment says the line is one informative item until 11-S6's judge |
| 2 | A default question could carry case labels or titles | **Taken.** No default: only an explicit `ask` makes a live check. A corpus test holds every `ask` to no needle token of any case and no rule vocabulary |
| 3 | Forbidden strings that are not tokens (a title, a heading phrase) give false leaks | **Taken.** Both live graders read only `[A-Z]+NEEDLE\d+` tokens |
| 4 | Case 3's offline `allowed` includes the whisper, so recall would reward the leak | **Taken.** Live recall is over `allowed − live_forbidden` |
| 5 | A failed or empty reply reads as clean | **Taken.** An errored or empty reply fails the gate, is outside both denominators, and is reported as "k of m answered" |
| 6 | `runner.isolated` does not exist; rates are never read | **Taken.** `real_home = paths.home()` and `usage.Rates.current()` before any repoint; `runner._is_real_home` checked in each isolate before building and again before harvest |
| 7 | Two checks in one home double-count money | **Taken.** One isolate per asked check (the case rebuilt each time), one `Result` each |
| 8 | `test_operation_guard`'s walk set must name the new module | **Taken.** `evals.epistemic.live` added; the literal `"chat"` and `usage=meter.usage` inside `usage.meter` |
| 9 | Results cannot be stored or compared as dicts; recall must not be a `Check` | **Taken.** `reports.Result`s with the leak and harness checks as `Check`s, recall in `note`, `doc["epistemic"]` holding both lines; `--provider`/`--model` accepted through `resolve_connections` |
| 10 | Compose as play does | **Taken.** `model=resolved.chain.primary.model`, and the prompt-leak check runs on the prompt actually sent |
| 11 | The question is a second user message in a row | **Taken.** Framed as Seraphine's line in the story; a test holds the last message to it |
| 12 | Grade the shown text | **Declined, stricter instead.** The raw reply is graded, fences included: a token anywhere in the reply counts, which can only report more |
| 13 | Mode dispatch and refusals | **Taken.** `--epistemic` joins every other mode's clash list and they join its; offline never builds a client; `--record` and `--case` are refused |
| 14 | The amendment is too narrow; opaque tokens cannot be paraphrased | **Taken.** 11-S6's Delivers/Scope/Acceptance, the 11-C4 contract and §11's acceptance gain the judge, with each secret stated as a sentence beside its token for the judge to recognise a paraphrase of |
| 15 | Test isolation and fakes | **Taken.** Tests stub `resolve_connections` and use `llm_fakes`; a tripwire test refuses an isolate equal to the real home before compose and before harvest |
| 16 | Wording and docs | **Taken.** Prompt-leak and reply-leak lines named apart, n beside every rate, README commands in both venv spellings, money and tokens through `costs.cost_text` / `costs.tokens_text` |
