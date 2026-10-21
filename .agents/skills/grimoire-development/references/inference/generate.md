# Generation and resolution

Source: [inference/](../../../../../backend/src/grimoire/inference/), [common/](../../../../../backend/src/grimoire/routes/common/), [routing.py](../../../../../backend/src/grimoire/store/routing.py).

- **Adding an LLM call site?** Resolve it with
  `require_inference(<task>, cid)` and name the task the call meters under.
  **A generation is `operations.generate(<task>, messages, client=…,
  resolved=…, usage=m.usage)`** inside that task's `store.usage.meter`
  (`stream=False` for the joined reply; the operation module bound as
  `from .. import inference as operations`). It refuses a resolution for
  another task or operation before any client call, so a call that generates
  under a sibling task of the route it resolved -- a director turn on the
  send's `chat` -- says so with `operations.for_task(resolved, <task>)`.
  A generation may also pass `schema=` (01f: each attempt whose model takes
  it is sent its provider's structured mode, on a per-call target; the schema
  must ride the prompt too, a refusal of the field is re-sent once without
  the mode, and the reply is read with `schemas.find_value`) and
  `max_tokens=` (a per-call output cap, held to the model's known maximum
  output, `wire.Target.limits`; a refused cap the preset did not carry is
  `llm.CapRefusalError`, never re-sent uncapped). A caller that records its
  prompt passes `operations.call_chain(...)` as `conn`, so the capture says
  what the call was sent with.
  The recognized direct `client.stream` calls live in
  `inference/_generation.py`; direct `client.complete` calls also live in
  `inference/_decision_calls.py`; confirmed model probes live in
  `routes/config/_probes.py`. The tool loop calls its owned `self.client` in
  `inference/_tool_runtime.py`. What the seam returns
  carries the `wire.Chain` the facade is sent as `.chain` (`generate` hands it
  on), and the facade dispatches each of its typed `wire.Target`s through the
  adapter registry (`adapters.py`); read the display facts (`.kind`, `.model`,
  `.provider_id`) off `resolved.chain.primary`. There is no connection dict:
  the lowering is deleted.
  `store/routing.py` maps the task to a route (each one declares its
  `operation`, its `default_role` and what it `requires`), and
  `store/inference/` resolves the route to a role (Primary, Fast, Decision,
  Embedding) or a pinned model. Every store is resolved as format 2: the
  roles and their fallbacks, each route's choice, pin and preset, a campaign's
  own overrides of both, and the facts of the chosen model -- `vision`,
  `prefill` and `post_process`, which each attempt's target is built with in
  place of the connection's legacy fields. A layout the migration has
  not reached is read through the planner, in memory (`resolve._overlay`, the
  one call of `legacy_plan.overlay`): the legacy keys seen as that same
  layout, a legacy GLM effort as a derived reasoning preset, and nothing
  written, so a call site never asks which one it is on. Two answers stay
  format 1's on a format-1 store, both read off the overlay rather than a
  legacy field: a reroll naming a provider alone runs that provider's own
  model and preset, and the `missing_key` sentence keeps its old wording
  (`ResolvedInference.legacy`). The derived presets stay in memory until
  retirement (`store/inference/retire.py`) writes them and marks each scope
  retired; a retired scope reads nothing of the planner. The fallback rides on
  the resolved chain: `chain.fallback` is the fallback attempt's target
  (wearing the route's preset when the route has one), and the facade sends
  that one. A fallback *known* unable to do what the route needs is reported
  (`fallback_missing`) and never rides, so the facade never sends it -- nor
  one that names the primary's own connection (a retry, which the retry budget
  covers -- except behind a Decision model that cannot generate, where it is a
  decide stage of its own), nor one that cannot carry the call's images. A
  task whose `routing.TaskPolicy` says `fallback="none"` is sent no fallback
  at all, and its route row says so (`resolve.NO_FALLBACK_POLICY`) when its
  role had one; a route's tasks agree on it, because `for_task` hands one
  sibling's resolution to another. A reroll's connection override goes through
  `override_inference`, and
  absorb's secondary phases hand `_soft_inference` a thunk (voice drift and the
  duplicate check hand one to `_soft_resolved`, which keeps the whole
  resolution `decide` takes), so a phase that cannot resolve reports itself
  failed with a reason instead of losing the review.
