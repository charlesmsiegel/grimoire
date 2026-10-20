# Decision capture and capability guards

Source: [decision_capture.py](../../../../../backend/src/grimoire/routes/decision_capture.py), [test_decision_capture.py](../../../../../backend/tests/test_decision_capture.py).

  **The prompt log's capture runs after each call settles** and outside its
  meter, guarded, so it can never fail an answered decision or file an error
  row. It is handed the request as sent -- a structured chunk's messages, or a
  native item's normalised body as one JSON message, built off the loop and only
  when a capture was asked for -- with the outcome (`decisions.outcome`) and the
  dict that was actually sent (`llm.ATTEMPTED`) when the call answered, so a
  fallback that answered is the one the log names. A call that never went out
  (`native_unrepresentable`, a refused clock) is captured with `messages=[]`
  and its error; an adapter's `missing_key` is raised after the attempt is
  stamped, so it is captured with the request it would have sent and files an
  `error` row; a cancelled call, and a chunk or item held back
  after a connection-wide failure, are not captured at all. Every decide site
  captures through one helper, `routes/decision_capture.py` (roadmap 01b): a
  scope -- one speaker pick, scene-break check, voice-drift phase, duplicate
  check or reconcile pass -- holds each settled call in memory and files ONE
  prompt-log entry when it exits, under `operation: "decide"` and in a
  retention pool of its own, so decisions never evict a turn. It is fenced on
  the scene's strict identity, or -- the reconcile sweep, filed at campaign
  level with no scene and read through `GET /campaigns/{cid}/prompts` -- on
  the run's own `stillborn`. A failed call is kept as its error kind, status
  and code, never the provider's text, and `test_decision_capture.py` holds
  every `decide` call in the package to the helper. The outcome envelope is
  filed as a zero-token section whose id is
  `routes/decision_capture.OUTCOME_SECTION_ID` (`"decision"`), which the
  prompt viewer draws as "outcome · not sent" rather than as a prompt
  section. The
  settings view carries `decision_mode` and `decides_natively` (`yes`, `no` or
  `unknown`, `no` only when known) on the Decision card and on every decide
  route row -- "No native decision API" is said only of a known `no` -- read off the same resolution the chain
  runs, so the Models page's decide note is the server's sentence and the
  frontend keeps no capability rule of its own. The eight decide routes (`speaker`,
  `scene_break`, `voice_drift`, `continuity`, `turn_plan`, `history_check`,
  `epistemic` and `investigation_decide`) use the
  Decision role, which
  inherits Fast, so on them a campaign's own Fast override no longer outranks
  a global pin (spec 5.1) -- the duplicate check and the sweep included, which
  a Fast override used to reach: a campaign that wants that sets its own
  Decision role. A call site converts only behind `evals/run.py --gate`, whose corpus
  starts from the legacy parser's own tests (`evals/README.md`). The speaker
  pick turns a roster `decide` refuses before sending -- two refs that read as
  one once normalised, or more than 254 refs (with `grimoire`, past a choice's
  255 options) -- into the invalid-handoff issue, so control returns to the
  player rather than the round failing.
  `test_routing_guard.py` fails a call that names no task, a task no route
  claims, an `operation=` that differs from its route's, a reference to
  `require_inference` that is not a call (a task handed through
  `run_in_threadpool` is a literal the guard cannot see), a direct
  `inference.resolve` call in `routes/` without a `# routing-ok:` marker (the
  markers are capped), and a route whose tasks nothing uses. A route names a
  whole connection or model rather than a bare model string, because since
  `llm_connections/` a model name no longer says which provider serves it --
  the same call #144's fallback made. A route that needs a capability declares
  it in `requires`, and the seam refuses a primary that is *known* not to have
  it (a `no`, never an `unknown`) with 409 `incapable`; an image route whose
  adapter says `no` keeps the old `UNSUPPORTED` body instead. A failed test
  call is `unknown` with its error, never `no`, and the name rule's `no` is a
  guess that hides a model in a picker but never refuses one. The Embedding
  role has no seam to refuse at, so the same known `no` for `embed` turns
  embedding off instead: `embed_space.resolve` names no space, no request is
  sent that could only fail, and the Embedding card says why.
