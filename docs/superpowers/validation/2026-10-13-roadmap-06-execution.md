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
