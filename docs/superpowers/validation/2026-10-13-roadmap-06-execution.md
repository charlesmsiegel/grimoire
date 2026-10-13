# Roadmap 06 execution record

Spec: [Store-editing skill](../specs/2026-10-09-roadmap-06-store-editing-skill-design.md).
Base: main at `c647d06c4`; branch `roadmap-06-store-editing`.

## Spec gate

The installed Codex adversarial-review runtime inspected supplied, numbered
repository snapshots. Its sandbox could not launch the Windows reader helper;
the snapshots avoided changing the plugin or accessing any real store.
Thread `01a1344a-486e-78c2-bc40-7333ca16519e` found six material defects:

- Store locks did not stand in for the app's live-run exclusion.
- Collector exit could send embeddings before applying a no-spend policy.
- Deleting the compiled directory would erase purge and deletion controls.
- Reporting a path rename did not make a referenced record rename valid.
- Result guidance omitted current incomplete-work and token-update failures.
- Checking option names and module imports did not validate command verbs.

Each finding was checked against the current code and corrected in the spec.
Recheck turn `01a1345a-e7ee-7f52-a192-55d02a71fdb9` returned `approve`, with no
findings: all six were resolved at the design level, and planning could proceed.

Codex's current [skill discovery documentation](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
confirms repository `.agents/skills` discovery. The adapter therefore remains.

## Baseline

A focused baseline over the docs guard, installer checks and cache CLI passed:
124 passed, 7 platform skips. This is not the whole repository gate.

An independent read-only application scenario used the existing operational
skills, without the new skill or roadmap specs. The agent found the store
writers but could not find a verified no-spend sync procedure, concluding:
"I would leave explicit warming deferred." The new reference procedure must
make the collector and CLI options discoverable while preserving no-spend
intent. This baseline made no library writes or model calls.

## Remaining stages

Plan gates, implementation, application scenarios, whole-gate output, diff
review and the final spec-to-diff review are recorded here as they complete.

## Plan gate

Codex reviewed both slice plans in turn `01a13463-319c-7fb2-82ad-271f65b106a3`.
S2 was complete; S1 missed report names used only in result prose. The plan now
checks those names and includes a prose-only `tokens_falied` negative fixture
with every table row still valid. Recheck turn
`01a1346d-7ae1-73b1-a0e9-f9e624a0c958` approved both plans with no remaining
material blockers. Implementation proceeds inline in the requested worktree.

## Implementation progress

The canonical and adapter validate with skill-creator's validator. The focused
skill, docs and installer suite passed 129 tests with 7 platform skips. The
new diagnostics passed 39 planted/reference checks after the observed RED run;
the required-artifact checks then passed once the canonical and adapter existed.

The reference is 157 lines. It uses local-only collection before a bulk preview,
requires campaign quiescence, preserves lifecycle references and cache deletion
controls, and explains current incomplete-work outcomes. Script-backed handoffs
avoid duplicate syncs and repeated ingestion.

Theory: runtime interfaces define executable truth, while the canonical owns
operational guidance. The guard checks static references and routing boundaries;
it does not prove arbitrary shell/Python programs or an agent's decisions.
Reused: the CLI parser, store signatures, frontmatter parser and docs helpers.
One compatibility choice uses the public OpenAPI path map because this FastAPI
version wraps included routers. A future hidden-but-documented route would need
an explicit traversal. Application evidence is recorded below; the full gate remains pending.

## Application and fresh review

An independent fresh-context application used a disposable placeholder library,
with its bootstrap pointer and machine-lock resolver also isolated. Store
readers confirmed an anchor rewrite and a lore deletion, and a prior item edit
remained byte-identical. The collector and real CLI (`--verify --no-embed`)
each reported one cold and one deleted path, no failed token update or stop,
and zero embedding sends/requests. Network auditing recorded zero attempts.
An interrupted collector re-raised after the first write, kept that write,
left the pending deletion intact, and reported one refusal (`not_deleted`).
The tokenizer used the supported offline heuristic. These cold records gave
no rebuilt-kind comparison; existing cache regressions supply that coverage.

The active-detached-run, stable-ID display rename, cache-control preservation
and incomplete-report variations were reasoning scenarios, not executed state
changes. The agent applied the canonical decisions correctly in each case.
Scratch reports and harnesses remain outside this public checkout; only their
placeholder-run counts and outcomes appear here.

A fresh senior reviewer found no Critical or Important defect. The guard-table
marker was corrected to an em dash. Its privacy-fragment suggestion was
considered but declined: the owning document is required reading, its privacy
section begins at line 19, and the shared heading validator does not model
GitHub's adjacent spaces around removed em dashes exactly. The canonical links
to the full authority rather than claim a fragment it cannot validate. Cost if
wrong: readers navigate to the nearby privacy heading themselves.

Reviewer scope exclusions were accepted: dynamic Python/shell and full call
validity exceed static references; methods/hidden routes exceed documented
paths; only path statuses require exhaustive vocabulary coverage; discovery
and agent decisions need separate evidence; cache repair, format redesign and
retrofitting existing skills remain explicit non-goals. The independent
application and current discovery documentation cover the operational parts.

The public status (`isPrivate=false`) was verified before retrying a Codex
review snapshot call that automatic approval review initially rejected as a
possible private-code export. The retry was approved; snapshots contain only
repository files and placeholder documentation, never a real library.

Two additional planted fixtures reproduced unused missing store imports and
missing writers behind a root `import grimoire.store as store` alias. Both
failed before the binding/import validation fix, then passed. The refined
guard has 44 tests; all pass, and Ruff reports no finding. Static import
checking resolves symbols without executing snippets. The import validation
and call validation remain separate responsibilities.

## Codex implementation and final spec gates

Built-in diff review thread `01a13490-68ed-7450-b512-1d28cec12d50`, turn
`01a13490-69fd-7e23-b8ac-61551e6c6008`, found no concrete defect. Built-in
recheck turn `01a13498-8850-72f0-b21a-c124e66e3132` reviewed the final import
and facade refinements and found no additional actionable finding.

The final adversarial diff-and-full-spec review, turn
`01a1349a-bfb8-72a0-bac8-fcd8cf77d499`, approved S1 and S2 implementation
completeness with no findings. It accepted the documented route-map and
whole-authority privacy-link choices and the explicitly stated limits of the
application evidence. Both Codex reviews explicitly left full-gate success
unclaimed and required diagnosis of its backend failures before reporting
verification complete.

The fresh reviewer also rechecked the facade fix after its reproduced RED
acceptance fixture, confirmed it correct, and reported no remaining findings.
All 44 guard tests pass with clean Ruff output. The lint/template/frontend
stages passed, including 5,175 frontend tests; both dependency-set backend
runs remain in progress.

## Initial whole-gate result and unchanged-main reproduction

`make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`
ran from this worktree. Ruff (1,136 existing findings), mypy (161 existing
findings), template verification (527 checks), ESLint, frontend type checking
and all 5,175 frontend tests passed. The backend coverage run reached 93.68%,
above the 93% floor: 21,070 passed, 42 skipped, 7 failed and 11 setup errors.
The initial command exited nonzero; that run was not green.

Every failed case was rerun on unchanged main at `c647d06c4`, using that
checkout's source and the same interpreter, without changing its HEAD or
tracked files. All seven failures reproduced; the common benchmark setup
error also reproduced. At that point, the product sources, synthetic generator
and affected tests had no diff on this branch. The failures are therefore an observed
baseline, not inferred from their filenames:

- The missing-`ctypes` import case reaches the Windows recycle driver.
- The refusal fixture calls `os.mkfifo`, absent on Windows.
- Five image-GC fault-injection cases do not intercept the Windows file calls.
- The synthetic benchmark calls `os.utime(..., follow_symlinks=False)`, which
  raises `NotImplementedError` on this platform; its one module fixture
  accounts for the eleven setup errors.

The selected main reproduction reported 7 failures and 1 setup error. Its log
is retained outside the checkout; full-gate logs are in the ignored execution
workspace. Main remained clean. No baseline was weakened, test skipped, frozen
home regenerated or product behavior changed to make this feature appear green.
The separate initial Android-dependency run is recorded below. Verification
was limited by this baseline even though scoped checks, actual application
and implementation/spec review gates passed.

`make check-pydantic1` also exited nonzero: 21,066 passed, 48 skipped, the
same 7 failed cases and the same 11 benchmark setup errors. It used its
throwaway environment under this worktree. This comparison does not make the
whole gate pass; the Windows failures occur with both dependency sets.

Final scoped run on the completed skill/routing tree: 169 passed, 7 platform
skips across skill, docs, interpreter and cache-CLI checks. Both skill manifests
validate. No lint baseline changed. Both slices' Code, Review and Final spec
stages were complete at the initial handoff; Landed stayed clear. The branch
and native worktree were preserved without publication. The user then
authorized the repairs below, followed by a push and PR once implemented.


## Authorized Windows gate repair

After the baseline reproduction, the user requested fixing the errors and
confirmed proceeding. This authorizes the bounded runtime/test repair below;
the initial spec-06 non-goal remains the boundary of the skill feature itself.
The earlier failures above describe the pre-repair tree.

The native recycle driver is optional infrastructure. The store facade must
remain importable without it, and an unavailable driver must refuse a send
before its callback or sender runs. One complete unavailable namespace is
published first; the live namespace replaces it only after all native imports,
DLLs, declarations and helpers initialize. The native ABI and successful-send
logic are retained. Missing ctypes, DLLs and exports are exercised in isolated
subprocesses, including unchanged bytes and absent callback/sender calls.

Synthetic timestamps belong to an exclusively owned, disposable generated
tree. The generator now validates the entire tree before any timestamp writes:
links, reparse points and hard-linked regular files are rejected, and traversal
errors propagate. It then applies exact nanosecond timestamps deepest first using the supported
API. This preflight does not claim protection
against concurrent path replacement; exclusive ownership is required.

Fault injection now selects each intended POSIX or Windows image-move branch
explicitly. Windows rename permission and cross-device failures also preserve
the original blob and sidecar and stop the run. FIFO refusal still uses a real
FIFO where available; the unavailable-API case supplies only its classification
metadata and retains the existing literal refusal assertions. No failure was
hidden through a new skip, altered baseline or deletion fallback.

The new regressions were observed failing before the production fixes:
10 failed, 1 passed and 1 existing benchmark setup error. Afterward the original
failures and new cases passed together (18 passed), and all six affected
modules passed (626 passed, 4 existing platform skips). Ruff and mypy ratchets
remained at 1,136 and 161. No frozen home or snapshot was regenerated.

Built-in Codex diff review thread `01a1356e-da5f-7323-be36-dbc75b0f39b8`, turn
`01a1356e-db29-7c21-82b8-54c62b7ab2ed`, found two test portability defects:
a cwd-based subprocess source path and an indirect metadata mock. Both were
reproduced before correction. The source path now derives from the test file,
and the child asserts it imported this worktree. The existing compiled-cache
subprocess had the same defect and was corrected too: 4 failures became
4 passes when run from `backend/`. The reparse mock now patches `Path.lstat`
directly: a failing emulation of the older pathlib implementation became a
pass. A fresh reviewer rechecked these fixes and found no remaining blocker.

Built-in Codex recheck turn `01a13579-f217-7623-b2e0-e3521c281b4b`
confirmed both portability corrections with no further finding. The final
adversarial diff-and-repair-requirements review, turn
`01a1357b-9815-7841-88bf-bc9283137e25`, approved with no findings. Both
reviews explicitly required the complete gate result before claiming
verification complete. The fresh primary backend run passed: 21,104 passed,
42 skipped, zero failures, and 93.71% coverage above the 93% floor. It took
1,308.74 seconds with eight workers. Template checks and all 5,175 frontend
tests passed too. The Android/Pydantic-1 dependency run also passed:
21,098 passed, 48 skipped, zero failures, in 1,158.94 seconds. The complete
`make check` command exited 0 with the explicit shared `PY` and `WORKERS=8`.
Both runs used this worktree's source; no failure was suppressed or baseline
inflated. The final test-only review corrections were additionally verified
from `backend/` and under the older-pathlib emulation described above.

Completion metadata now marks 06-C1 delivered and both slices implemented,
reviewed and verified. Landed remains clear until merge. The user authorized
pushing this branch and opening a PR, then stopping; the worktree is preserved
for later review.

## CI repair and Linux application run

The PR's first CI run failed both backend jobs on two
`test_serving_compression.py` bundle tests, identically on `main`: the runner
had resolved Starlette 1.8, whose `FileResponse` listens for a disconnect while
it streams under ASGI < 2.4, and the raw-ASGI harness reported a disconnect the
moment the request was read. The harness now disconnects once the response has
ended, as a client does (bounded by a timeout). The tests pass on Starlette 1.8
and on the Android set's 0.46. No product code changed.

The section 9 dry run was repeated on Linux against a scratch `GRIMOIRE_HOME`
holding only the placeholder World Realm. The skill's Python example, copied
verbatim, wrote Seraphine's anchor and deleted the tidewatch lore entry inside
`collecting(embed=False, deleted=...)`: exit 0, one `cold` and one `deleted`
path, zero tokens failed, zero embeddings sent. The skill's macOS/Linux CLI
sync of the same two paths with `--no-embed`, and a `--verify --quiet` rerun,
each exited 0 with the same statuses. No model request was made.

A Linux `make check-py` (four workers) passed every test but
`test_atomic.py::test_a_read_only_record_is_not_silently_replaced`, which
cannot fail closed when the suite runs as root (a `0o444` file is writable to
root); it is unaffected by this branch and passes on CI. Coverage was 93.63%.
