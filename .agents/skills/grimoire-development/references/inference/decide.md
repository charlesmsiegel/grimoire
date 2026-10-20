# Decision call sites

Source: [inference.py](../../../../../backend/src/grimoire/inference.py), [test_operation_guard.py](../../../../../backend/tests/test_operation_guard.py).

  **A yes/no, pick-one or graded question is a `decide` call**, never prose
  parsed afterwards. Resolve it with `require_inference(<task>, cid,
  operation="decide")` on a route whose `operation` is `decide`, bind the
  operation module as `from .. import inference as operations`, and pass the
  resolution and the app's client to `operations.decide(<task>, items,
  client=…, resolved=…)`, which refuses a resolution for another task or
  operation. It opens one meter per chunk itself, so a caller with a time
  budget hands it over as `around` and the budget runs inside that meter.
  The continuity pair are decide calls too -- absorb's duplicate check
  (`continuity-identity`) asks one item per examined row, and the
  reconciliation sweep (`continuity-reconcile`) one per candidate -- each one
  `decide()` over the whole batch, chunked at 8 items to a metered call, so the
  per-item wording repeats once per item and the chunking bounds each call. An
  item the reply never reached is not an item it answered badly: a row stays
  `unchecked` and a candidate gets no proposal (so the sweep lands with it
  counted `unanswered`, and asks it again next time -- except a model-only
  nomination, which is not stored), while an item that was read and answered
  badly is `uncertain`. A status word stands on its known evidence scene
  alone; the rationale is display text and nothing stands on it. Each chunk
  runs under the full `llm_call_budget` ceiling (each prompt-only re-send of
  a route that refused the schema too), so a full sweep on a structured
  Decision model can hold the campaign's background run for up to three times
  as long as the one call it replaced, and up to nine with both routes of
  every chunk re-sent. On a native one each candidate is its own call under
  that ceiling, four in flight, so n candidates take up to ceil(n/4)
  ceilings, and a fallback stage of its own adds up to two per chunk: a full
  sweep with a fallback is up to twelve (`routes/continuity.py` argues it).
  `test_operation_guard.py` finds the call by import binding (a continuity
  `Examination.decide` is not one), fails a task literal that is not on a
  decide route or a call with no `resolved=`, and holds the safety rule both
  ways: a route flips to `operation="decide"` and the Decision role only in the
  change whose call site decides. Prose never rides a decision -- the
  scene-break title is its own `generate`, `scene-break-title` on the
  `summary` route, drafted only once a YES verdict is written, so a read
  between the two writes sees a YES with no title and the inspector shows the
  proposal untitled until its next refresh. Whether an attempt is also sent
  its provider's structured mode is decided per attempt
  (`wire.Target.structured`, set on a decide resolution's targets, and on a
  `generate(schema=)` call's own per-call targets); the
  schema is in the prompt either way, and the mode is filed per call on a new
  target's account (`with_account`), never by mutating the resolution's. An attempt whose provider refuses the structured
  field is sent once more without the mode once every route has failed -- a
  refusing primary after its fallback failed too, a refusing fallback after
  the primary failed -- once per attempt, as its own metered call
  (`llm.SchemaRefusalError` carries the refusing attempts). The refusal is
  never a health failure, nor the kind a both-failed error reports
  (`llm.routes_failed`).
