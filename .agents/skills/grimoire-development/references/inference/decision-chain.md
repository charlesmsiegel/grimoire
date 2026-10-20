# Decision backends and fallback

Source: [inference.py](../../../../../backend/src/grimoire/inference.py), [resolve.py](../../../../../backend/src/grimoire/store/inference/resolve.py), [decisions.py](../../../../../backend/src/grimoire/decisions.py).

  **A decide call is answered down a chain** (`inference.stages`, run by
  `inference.run_stages`): the selection's one backend, then the role
  fallback's, which gets a single attempt with no retry budget of its own,
  native or structured alike. The backend is native only for a model that
  cannot generate (`resolve.native_only`: `generate` a known `no` from
  something other than the name rule, `decide_native` not a known `no` --
  `unknown` is allowed), served by its provider's decisions endpoint
  (`decision_mode == "native"`), one request per item. A user marks a
  decisions-only model the catalog cannot place (an OpenAI-preset one, whose
  preset says every model generates) with the model-facts override
  `generate: no`, which outranks the preset. A model that can
  generate stays on structured generation whatever its `decide_native` says,
  unless its task's `routing.TaskPolicy.native_first` lists its adapter kind
  and it is `resolve.native_capable` -- `decide_native` a known `yes`, never
  `unknown` (a kind with no decisions endpoint never reads `yes`: its preset
  rules it out, and a test holds every preset to that). Then the chain asks
  its decisions endpoint first (`resolve.native_first`), in an *isolated*
  stage on the same model with no retries, and the structured stage after it,
  unchanged, takes the items that stage failed. An isolated stage never marks
  its connection dead -- a key a decisions endpoint refuses may serve every
  chat call -- and its failure is not composed into the error of an item a
  later stage took; any stage is skipped as dead only when every route it
  sends is. A kind is listed only on the same-model comparison `evals/run.py
  --live --decide-backend` exists to give (it grades a native item's
  rationale n/a, and refuses `native` before sending for a primary with no
  native endpoint, a preset that never decides natively, or a known `no`),
  recorded in `evals/README.md`; no task lists one today.
  `inference.reports_distribution` says whether a resolution's first stage is
  native. A model that can do neither is
  refused with 409 `incapable`, and for the speaker pick `post_chat` raises
  it before it writes anything (`refuse_an_unanswerable_pick`). **What moves
  an item on to the next stage is a failed call, never an answer**: an
  `LLMError` (a 2xx body that is not the documented envelope, or an envelope
  that answers none of the item's questions, is `bad_response`), or an item
  refused unsent -- on a native-first stage too. A native `refused`, an abstention and a `None` from a
  well-formed body are answers; re-asking them elsewhere would be asking until
  something agreed. A policy-declared escalation is not a stage
  (`routing.TaskPolicy.escalate_to`, `inference.decide(escalation=)`): after
  the unchanged chain it hands an ANSWERED item, once, to a stronger resolver
  only on what the backend itself reported (`decisions.triggers`: a refusal,
  an abstention, a low margin), never to the model that answered it, and a
  hop that fails, garbles or declines leaves the original standing
  (`Decision.escalations`; `Decision.errors` stays the chain's). Its calls
  file `hop: escalation`. A call site resolves the hop through one seam,
  `routes.common.escalation_inference(<task>, cid)` (in the threadpool, as a
  thunk): the policy's role with its own preset, held to the base route's
  `requires`, and soft -- a refusal is the sentence every candidate is skipped
  with, never a 409. It passes `escalation=` exactly when its task's policy
  escalates (`test_operation_guard.py`), notes the outcomes in its capture
  scope (`Scope.note_escalations`, a failed detail cut to its kind), and
  `test_routing_guard.py` holds the seam's task to a literal whose policy
  names a role. No task enables it yet. A `llm.PresetRefusalError` ends the chain where it is met,
  as it ends the facade's: the preset is the user's to fix, and a native stage
  that takes no sampling would only hide it. So does the caller's clock
  refusing a call unsent: the refusal comes out as the `BudgetRefused` it is,
  never composed with a later stage's, so a phase still reports it skipped.
  A fallback on the primary's own connection AND model is dropped with
  `SAME_PROVIDER` even behind a native primary: that is a second send of
  the call that failed, whatever stage it sits in (#144). The same-model
  structured stage behind a native-first stage is not one: it is the chat
  endpoint, a different endpoint from the decisions one that failed, so it is
  no resend.
  `decide_native` is metered per item (`store.usage.meter`, opened in
  `inference._native` with `decision_mode` stamped on a new copy of the
  stage target's account), at most `NATIVE_CONCURRENCY` in flight inside one
  `asyncio.TaskGroup`, so an unexpected exception or a cancel leaves no
  request the group owns running (an `around` that detaches its call, as
  `_bounded_call` does, abandons it to unwind on its own). It sends no
  `sampling`, so its ledger row files no `preset`. An item `decisions.native_gap` says a decisions endpoint
  cannot carry (today a choice -- or a joint's flattened choice -- whose
  options plus the reserved none pass 255, a rank that names no `pointwise`
  question, or a lowered predicate id that collides with another question's)
  is refused unsent with `bad_response` and the code
  `native_unrepresentable`, and its reason is a sentence: it moves on like any
  failed call, and with no stage after it the reason reaches the caller and
  the error store. A native 4xx in `llm.NATIVE_REJECTED_STATUSES` does not mark
  the connection failing: the connection answered, and it was that model, key
  or request the decisions endpoint refused. It still files an error row
  (`Meter.done` records every failed call), so under native first a key
  refused by the decisions endpoint logs errors under its task on every
  decide call -- roughly one per wave of `NATIVE_CONCURRENCY` items, since
  an `auth` stops the native stage -- even when the structured stage
  answers. And a 5xx or a timeout from the decisions endpoint is not in that
  set, so under native first it counts against the connection's health: a
  working chat endpoint can show "failing" (spec 01c §4.2.3). Both are costs
  the change that first lists a kind accepts. A native stage starts **no
  further item** after a connection-wide failure -- auth, `missing_key`, a
  `rate_limit` the facade has already retried as far as it will, a spend
  refusal, or absorb's `BudgetRefused` -- and a structured stage sends no
  further chunk after one (`inference._connection_wide`); items in flight
  finish, and those never sent carry the failure that stopped them. A call
  with a fallback behind it is the exception: a bare error from one stops
  nothing, because the facade never tried the fallback, unless the clock refused
  it unsent. A non-isolated stage that stopped that way skips a later stage
  every route of which is on that connection. A native stage also stops after `NATIVE_TIMEOUT_STOP` items in a
  row time out -- a hung decisions endpoint -- but that stop is its own: a
  generating stage on the same connection still runs.
  **Who answered is only named when one did.** `Decision.backend` is empty
  when answers came from more than one backend, and `provider` and `model`
  when they came from more than one route (the fallback took the items the
  primary failed) -- so a native-first stage and the structured stage behind
  it, one model, name it with `backend` empty: `ItemResult.backend` is the
  per-item truth, `ItemResult.served` names the `(kind, provider id, model)`
  that answered each item (stamped by its backend, never compared),
  `Decision.served` lists every `(provider, model)` that
  answered across the stages, and `Decision.errors` holds each failed unit's
  final error -- a chunk on a structured stage, an item on a native one -- with
  an item that answered on a later stage contributing none.
  **A native answer carries no rationale to any caller**, because neither
  provider's endpoint has a field for one and the contract forbids a synthetic
  one. A native item that answered reaches G's continuity mapping as *read*:
  `backend == "native"`, an empty `rationale`, no `NO_ITEM` detail and no
  error, so a status verdict stands on its evidence scene with an empty reason
  rather than being mistaken for an item the reply never reached. A native
  `refused` or `abstained` on a `decision` question is *not* read, which leaves
  its row `unchecked` or its candidate unproposed. Voice drift's native branch:
  a drift verdict with no note is usable, stages nothing, keeps any standing
  flag, and is listed in `noteless` (the note *is* the corrective, so there is
  nothing to store); the over-cap and unknown checks apply as they do to a
  structured verdict (`store/voice_drift.py`, `routes/scenes._stage_voice_drift`).
