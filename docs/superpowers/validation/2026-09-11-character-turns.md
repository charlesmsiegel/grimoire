# Character responses: branch validation

Branch: `feature/character-turns`, based on `main`. Initial trial and subsequent
local integration verification are recorded below.
Usage: [Character responses](../../character-responses.md).

## Implemented behavior

- Actor-specific context and complete bounded voice examples, shared situational
  voice rules, and editable profiles selected by the actual dispatched model.
- Sequential prose with a hidden handoff in the same call. Each NPC and Grimoire
  can contribute once automatically per player round; Continue and Respond as
  authorize one additional contribution.
- Stable response IDs, individual deletion/reroll/variants, explicit replay,
  frozen historical prompts, retained later text with changed-context indicators.
- Durable same-actor dice continuation, applied-mechanics edit boundaries,
  cancellation/retry recovery, and per-round/per-response usage attribution.
- Optional reviewed character creation or attachment from narrator prose.
  Attributed dialogue review needs no model call; richer description drafting is
  optional. No mandatory additional scene-closeout generation.
- Legacy cast retain newly witnessed dialogue across player rounds, without
  treating uncertain earlier history as witnessed. Silent present NPCs count too.

## Passing checks

- Frontend: **2,992 tests in 141 files**, typecheck and production build.
  Coverage: statements 85.06%, branches 80.42%, functions 78.81%, lines 88.00%.
- Final backend coverage: **93.09%** with branch measurement, meeting the existing
  `--cov-fail-under=93` gate. No threshold or exclusion was weakened.
- Final continuity/response coverage refresh: **188 passed**.
- Response, actor, presence, lock and import checks after the legacy observation
  fix: **216 passed**. Earlier broader response/recovery checks: **189 passed**.
- Final Pydantic 1.10 / bounded FastAPI dependency checks over changed behavior,
  detached runs, follow-ups, presence, overlay and facade guards: **170 passed**.
  This used a separate Python 3.12 environment; desktop dependencies were not downgraded.
- Legacy combined prompt-capture and complete routing suite: **72 passed**.
- Template harness: **126 checks**, byte-for-byte agreement.
- Ruff: 1,180 findings, all at the existing baseline. ESLint: 844, all at baseline.
  Mypy against the CI Linux platform: 180, with one earned baseline reduction.
  Native Windows mypy has existing platform-specific findings.

The complete backend suite was run in four non-overlapping process batches with
separate temporary stores and coverage files. After the last continuity fix,
obsolete line mappings for `store/responses.py` were removed and that module was
remeasured with its covering tests before appending to the aggregate. The final
coverage report therefore does not reuse pre-edit line positions for that file.
The normal `backend/coverage.xml` artifact was regenerated.

The voice/model checkpoint deliberately updated the frozen prompt snapshot.
The frozen campaign `home/` fixture was not modified.

## Full-suite limitations

The full Windows backend run finished with **8,886 passed, 7 failed, 10 setup
errors, and 20 skipped** before the final bounded continuity rerun. Six failing
tests and the ten setup errors reproduce on unchanged `main`: concurrent append
and log recording, backup CRLF expectations, Windows-invalid image/world/sidecar
names, and oversized parameter-derived fixture paths.

The seventh failure was an intermittent `PermissionError` reading a scene during
the scene-break follow-up. It passed in subsequent focused runs. Five isolated
attempts on `main` did not reproduce that race, so its baseline status is not
asserted. No assertion was skipped or weakened to hide it.

The earlier full Pydantic 1 run also exposed five absorb-budget assertions;
all five reproduce on unchanged `main`. Legacy assertions tied to the combined
writer now explicitly select that retained mode. Individual-response behavior has
separate API tests. The full Windows suites are **not** claimed green.

The in-app browser could not start because its Windows sandbox helper was absent.
UI behavior is covered by frontend tests and the successful build; no visual
browser verification is claimed. No paid live model calls were made. Naturalness,
latency and cost should be compared during play, with Combined mode available in
Configuration. Automated results do not establish a dialogue-quality improvement.

## Review coverage

Independent review passed actor-context isolation and frozen prompt behavior.
Independent UI/core reviews found attribution, optional-evidence, synthetic-note,
legacy replay, mechanics-boundary, multipart recovery and terminal-delta Stop
issues. These were corrected and their failure cases verified. Controller review
also found and pinned the legacy-observation continuity bug and empty-response
follow-up behavior.

Worker provider quota prevented a fresh independent review after the final fixes.
The controller completed source review, red-to-green reproductions, and final
verification; no fresh independent post-fix approval is claimed.


## Follow-up: dialogue format and speaker progress

Individual writers now receive prose formatting without ensemble script labels,
and the shared reply format explicitly requires double-quoted speech. Combined
mode retains the labels its parser requires. The stream preview shows each named
response before the first delta, retires its progress on response_end, and clears
speaker boundaries with the preview. Continue remains visible and disabled through
the existing busy latch; Stop occupies the same action column above it.

Verification: 352 CampaignView tests passed, including initial selection, a second
speaker with no prose yet, completion, cancellation and a held reattached response.
22 actor-context tests and 354 context/frozen-campaign/cassette/offline-eval tests
passed; the template harness passed all 126 checks. Frontend typecheck and production
build passed; ESLint remained at its 844-finding baseline. Only prompt output changed
in the deliberately refreshed snapshot; the original frozen home was untouched.

Root diff/spec review checked that speech punctuation is requested in prompts,
not imposed on narration by a text rewrite; raw streamed content stays separate
from display boundaries; response_end does not release Continue between speakers;
and reconnect/Stop retain the existing parent-run latch. No fresh independent
review or paid model-quality evaluation was performed for this follow-up. Browser
visual verification remains unavailable as recorded above.


## Follow-up: eligible handoff choices

The handoff prompt previously listed the entire round roster, including the
current speaker and actors who had already used their slot, while omitting the
narrator slot accepted by the engine. Explicit single-response requests also
offered successors that the engine would ignore. Candidate construction now
matches those constraints. The assigned budget no longer requests a multi-actor
script, and GLM guidance expressly includes required control blocks. Handoff
instructions distinguish an actor's prose from the routing decision and show
both an eligible successor and a deliberate stop.

Four regressions failed before the repair. Afterward, 103 actor/engine/protocol
tests and 79 model/frozen-history/offline-eval/mechanics/control tests passed.
The same 103 actor/engine/protocol tests passed under Pydantic 1. The template
harness passed 126 checks; Ruff remained at 1180 and CI-platform mypy at 180.
Frozen snapshots did not change. No paid model call was made: these checks prove
candidate delivery and bounded execution, not improved model compliance.

Root review checked automatic versus explicit requests, the narrator's one slot,
used-slot exclusion, roll continuation, historical snapshot preservation and
missing/invalid control stopping the sequence. This follow-up does not add a
fallback generation or force every present NPC to speak. Existing review-tool
availability limits still apply.


## Follow-up: incoming provider capture

Debug logging now captures decoded incoming SSE lines before JSON/content/usage
filtering, HTTP error bodies, and all fields exposed by Claude SDK messages.
Call IDs group attempts; per-attempt sequence and arrival offsets preserve
ordering. The existing log writer splits long payloads into numbered parts.
Scene output and usage accounting continue consuming their existing projections.

Eight capture regressions failed before implementation. After implementation,
the provider/capture/logging/lifecycle selection passed 251 tests. A final
capture/accounting/metrics/errors/lock-order selection passed 228 tests,
including ten capture cases. Architecture guards passed 186 tests. Configuration
tests and the frontend build passed. Ruff stayed at 1180 and ESLint at 844;
CI-platform mypy stayed at 180. Native Windows mypy additionally reports five
findings in unchanged atomic.py/proclock.py platform branches.

The log stress test exposed an existing Windows append race: every append
reported success but rows were missing. Loading the committed log module into
an isolated test process reproduced it; serializing its appends passed. The
diagnostic writer now serializes appends within this process, and a capture
test reconstructs every part while ordinary logs are written concurrently.
This does not claim cross-process locking or durable archival capture.

Root diff and requirement review checked unknown nested fields, malformed SSE,
SDK projection limits, fallback separation, generator closure, sink failure,
large payload reconstruction, opt-in activation, monthly caps, and unchanged
metered usage. The independent review tools remain unavailable as recorded
above; this is a root review, not an independent-review claim. No paid model
call, process restart, or live campaign mutation was used for verification.


## Follow-up: collapsible thinking and GLM reasoning effort

Character responses carry a separate reasoning event stream. The optional
per-call buffer survives gateway accounting resets, clears on each attempt,
and wakes the display while prose is still unavailable. Closing the response
stream closes the provider. Reasoning is never fed to roll/handoff watchers.
Each variant stores its reasoning; transcript metadata holds only a variant
pointer. The default-collapsed panel renders literal thinking tags and escaped
text, fetches saved reasoning on expansion, and follows variant selection.

Custom GLM 5.3/5.3-Flash connections expose provider-default/low/high/max effort.
The field round-trips through connection storage and is omitted from requests
unless explicitly selected for a supported model. Model switches do not leak
the setting into another model's request. The provider's documented Max default
and qualitative, non-token-target semantics are linked from character-responses.

Initial tests failed for missing reasoning delivery/storage and effort fields.
The gateway/character/capture/connection selection passed 149 tests. Persistence,
mechanics and architecture checks passed 228 tests; the final frozen-history,
connection and docs selection passed 95. All 353 CampaignView tests passed;
Thinking/ConnectionEditor tests passed 31 and ResponseControls passed 4.
Checks cover delivery before provider completion, retry reset, literal rendering,
no reasoning in subsequent prompts, and stored reasoning following variants.

Root requirement and diff review checked the optional side-channel, content
choice matching, cancellation, per-speaker UI boundaries, lazy saved-history
reads, roll continuation reasoning, setting defaults and provider compatibility.
Independent review tooling remains unavailable as noted above. No paid LLM calls,
live campaign mutations, or Grimoire restart were used for this change.


## Follow-up: narrator ownership of scene events

The assigned narrator template prohibited NPC dialogue while permitting observable
reactions. That left established actors' physical reactions within the narrator's
apparent scope. The revised contract assigns established NPCs their own actions,
decisions and reactions, preserves environmental consequences and new-character
introductions, and applies the same distinction to selection and handoffs. The
length instruction now defers to this scope rather than contradicting permission
to introduce a character. No routing, transcript or frozen snapshot data changes.

Two regression checks failed before the template change. The final selection
passed 371 tests covering actor context, character turns, response controls,
snapshots, offline evals, frozen campaign compatibility and documentation. The
template harness passed all 126 comparisons; Ruff remained at its 1180 baseline
and git diff --check passed. The route regression follows a selector, an NPC and
Grimoire, checking delivery of scope instructions and retention of the used NPC
in the present roster while excluding them from successor candidates.

Root review checked involuntary reactions, absent established actors, independent
scene events, new-character speech, brevity, manual responses and frozen rerolls.
The independent review tools remain unavailable as recorded above. These checks
verify prompt delivery and orchestration; they do not establish model compliance
with semantic ownership rules. No paid model calls, campaign writes, or app
restart were used for verification. Templates reload for newly composed responses;
historical rerolls continue to use their saved prompt snapshots.


## Local integration verification, 2026-09-12

The user requested committing outstanding work and integrating the feature locally.
After fetching origin, main remained the feature's ancestor with no divergent
commits. A merge commit preserves the feature history and provides a single
revert point. The feature branch is retained. No remote push is part of this work.

Fresh verification of the integration tree:

- Full frontend: 2,999 tests in 142 files passed; coverage gates passed
  (85.09% statements, 80.39% branches, 78.84% functions, 88.02% lines).
- Production build and TypeScript compilation passed.
- Full backend: 8,917 passed, 20 skipped, five failed, ten setup errors.
  Branch coverage reached 93.13%, passing the 93% gate.
- All five backend failures reproduced in a fresh archive of unchanged main:
  concurrent append completeness, backup CRLF expectations, and three
  Windows-invalid filename cases. The same Python environment also reproduced
  all ten parameter-derived fixture-path setup errors on unchanged main.
  None of these test files differs between main and the feature. The full
  Windows backend suite is not claimed green; no new failing test was found.
- Pydantic 1 compatibility: 148 response, actor-context, reasoning, capture,
  mechanics and persistence tests passed in the existing isolated environment.
- Ruff 1,180, ESLint 844 and CI-platform mypy 180 remained at their baselines.
- All 126 template comparisons passed; git diff --check passed.

Validation used isolated stores and fake model calls. Grimoire was not restarted.
The final integration documentation is the only addition after these code checks;
the documentation guard is checked separately before committing it.
