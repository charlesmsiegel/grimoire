# Accounting

Source: [usage.py](../../../../../backend/src/grimoire/store/usage.py), [cost.tsx](../../../../../frontend/src/components/cost.tsx).

## Costs: three money columns, and no two of them may be added

The monthly Costs trend has one narrowly scoped projection named **Estimated
total**. It sums the three columns before display rounding to show the estimated
cost of all activity, including modelled-only months. It is a graph reading,
never spend, and never enters budgets or the accounting columns. An unpriced
call makes this projection incomplete and must be labelled as such. All other
money surfaces keep the three sources separate under the rule below.

`store/usage/` is an append-only ledger of what each LLM call reported, and
every rollup over it carries **three** separate money figures. Adding any two
of them together produces a number that is wrong in a direction nobody can
recover, so the split is the design rather than an artefact of it:

- `cost_usd` — what a provider said it charged. The only figure that is spend,
  and the only one a campaign budget is measured against.
- `estimated_usd` — a price the provider reported as a subscription equivalent
  (`cost_basis: equivalent`, the `claude_agent` path): what the call *would*
  have cost per token. Real usage; not money anybody paid. A billed price on a
  subscription-tagged provider stays `cost_usd`, since the tag is a label and
  `cost_basis` alone decides the column.
- `modelled_usd` — a call whose provider named no price at all, costed against
  the user's rates: the model's own (in its provider's model facts), then the
  per-token table in `pricing.json` (`store/pricing.py`). Arithmetic this side
  did, and the weakest of the three.

`unpriced_calls` counts what none of them covers, which is what makes an
incomplete total say so. The rule that follows from all of it, and the one the
UI exists to keep: **a price nobody reported is never rendered as zero.** Both
the ledger (an absent field, never a `0`) and the three cost surfaces
(`components/cost.tsx`, which every one of them formats through) are built
around that single sentence.

**A row says what served it, and which numbers this side supplied.** Each
row carries `provider_id` (the provider's store id; `provider` stays the
adapter kind), `requested_model` (only when the answer named a dated snapshot),
`operation`, `role`, `preset` and `billing` (and `hop`, on a call an
escalation hop sent). Nobody passes them at a call site:
`llm._stamp` copies them off the target (`wire.Target`) of the attempt that
served, a fallback included, and `usage.Meter.done` files what the holder carries.
`billing` (`metered` or `subscription`) is a label, and only `cost_basis` moves
a figure between columns — **a billed price beats the subscription tag**, stays
`cost_usd` and counts against a budget. A row with no reported price is priced
by `pricing.rate_for_call`, the one precedence function: the model's own rates
(stated on its provider's page and kept in that provider's model facts), then
`pricing.json`. That covers a subscription row with no price too, so it lands
in `modelled_usd`, counted again in `modelled_subscription_calls` rather than
in a fourth figure. A zero rate is a real `$0`, so a model stated free models
to `≈ $0.00` rather than reading unpriced — but a bucket whose only estimate is
that zero, beside calls nobody priced, still headlines "not reported", since a
zero there would pass for a complete total. Rollups price the whole history at
current rates: deleting a provider deletes its facts, so its rows fall back to
the table or read unpriced, and a provider later created under the same slug
prices them at its own rates. Only `modelled_usd` ever moves; no rate touches a
row a provider priced. A true `tokens_estimated` marks counts this side made:
the facade (`llm._resilient`) counts on a worker thread, never the event loop,
only for an attempt whose stream ended on its own, and only the half the
provider left out. An embed call that returned without a prompt count is the
other counter: `inference.embed.estimate_prompt` (`tokens.count_if_loaded`,
which never starts an encoder load) counts it on the caller's thread, which is
a threadpool worker for every caller today -- the model test's probe hands it
to one. `Meter.done` never counts, and a bucket says how many of its
calls rest on such counts in `estimated_token_calls`.

**`modelled_usd` is never computed for a native decision row.** A row whose
`decision_mode` is `decisions.NATIVE_BACKEND` is not modellable
(`store/usage.Rates.estimate` returns nothing for it, whatever rate exists and
whatever counts the row carries). A provider's decisions endpoint is not billed
like a chat call -- OpenAI bills decisions on input tokens only, and neither
provider documents a price for both sides -- so a chat rate times a native row's
counts would be a figure for a call nobody sold that way. Such a row is
`cost_usd` when the provider reported a cost (OpenRouter's `usage.cost`) and
unpriced otherwise; its reported token counts are filed and still sum into the
totals, and it never carries a count this side made. The guard sits at read
time, so a rate the user sets later cannot model one either, and every figure
that passes through the estimator inherits it: the rollups, the per-turn rows,
and the rail's aggregate (`usage_rollup.VERSION = 6`, so an older build's
rollup file is never read with a native row folded in at chat rates). An
unpriced native row is counted in `unpriced_native_calls`, apart from
`unmetered_calls`, so the cost card does not tell a reader to set per-token
rates for calls no rate could price. Housekeeping's `in_use.unpriced` skips
every generative use of a model that is native-only for the same reason: its
decisions are native and its generations are refused unsent, so no rate prices
either.

Attribution is per *player post*: a turn's ledger row carries the transcript
index it was answering, so a post and every reroll of it bucket together and
the transcript can say what getting one reply actually cost. The index is only
as stable as indices are — a cut renumbers what follows it and the ledger
cannot follow — so it is a breakdown, and the scene's own totals are the number
that is always right.

**A director note is in the transcript for exactly that reason.** It is what
the player typed to steer a turn rather than to say in it, and it used to be
ephemeral — which made a director turn the one generation in the app whose cost
had no index to sit against. It is stored as a `DIRECTOR_SPEAKER` line now
(`store/scenes/serialize.py`), which is a *synthetic* speaker: assistant-role,
because the transcript format derives the role from the label and `You` is the
only label that means user, and in `SYNTHETIC_SPEAKERS`, so nothing counts it
as a reply, rerolls it, or measures it for drift. It is an instruction rather
than story, so `context.story._project_history` drops it (the prompt is
byte-identical to what it was before notes were stored — the note still reaches
the model exactly once, as the final user message), no export and no absorb
prompt contains it, and the play view hides it behind a per-scene toggle. The
two director turns that store no note — an offscreen scene, and an empty send
meaning "next NPC round" — are still charged to nothing rather than to the last
post the player can see.
