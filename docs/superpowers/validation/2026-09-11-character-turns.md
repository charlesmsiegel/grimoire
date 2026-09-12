# Character responses: branch validation

Branch: `feature/character-turns`, based on `main`. Local trial only; no push or merge.
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
