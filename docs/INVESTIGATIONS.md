# Agentic investigations: bounded, read-only world audits

**Status:** first slice of [Agentic Investigations](roadmap/sprints/agentic-investigations.md) (Sprint 082, Wave 9): milestone 1, "run a read-only consistency audit with attributed findings". It is implemented as:
- `grimoire.investigations`: pure values, the job loop (`run_job`), evidence checking, review and a deterministic worker (`ConsistencyAuditor`);
- `grimoire.investigationstore`: the store-backed read-only toolbox (`WorldToolbox`) and the service that scopes, runs and reviews a job (`InvestigationService`).

There is no app route or UI (the Wave 9 UI exit, 116, owns that), no model worker, and no write path. The scenarios are synthetic. No store format changes and nothing is written to a source or campaign.

## What it means

- An **investigation** is one job over **one source** (normally a world): a worker chooses actions, the job runs them through a read-only toolbox, and the result is a report a person reviews.
- A worker can only **list** the source's entities and **read** an entity's head. There is no action that writes, and the job's authority grant names only `inspect`. A repair is a separate, human-approved [World Change Set](CHANGESETS.md) (milestone 2, not built).
- A finding is kept only when **every citation is evidence the job itself read**: the same entity, revision and file etag, a field that exists in that revision and, when quoted, words that appear verbatim in that field. A worker cannot cite a record it did not read, a revision it imagined, or text the record does not contain.
- Every job is **bounded** (steps, reads, findings, seconds) and **cancellable** between any two actions. Whatever stops it, the findings accepted so far are kept, and nothing is written.
- A **review** re-reads the cited entities and marks a finding **stale** when its evidence changed after the run.

## Trust model

`InvestigationService(store, authority, owner=...)` is trusted application code acting for the library's owner (a human principal). It is never handed to a worker. For each run it:

1. refuses a source the library does not admit (`InvestigationError` `missing_source` or `excluded_source`) before granting anything;
2. issues one grant, from the owner, of `inspect` only, over `sources/<segments>/_source` (the source's own files, not its sub-sources), to a principal of the job's own, `investigator:<job_id>`;
3. mints an **untrusted** handle for that principal. Only the `WorldToolbox` holds it; the worker sees observations, never the handle, the toolbox or the store;
4. runs the job;
5. revokes the grant in a `finally`, however the job ended, unless the owner already revoked it mid-run (the report is still returned).

Every tool call asks the handle for `inspect` on the address it is about to read, before any I/O: `list` on the source address, `read` on `.../_source/records/<kind>/<id>`. So a job reads only what its grant covers, and with a `DurableAuthority` both the grant's lifecycle and every read decision are in the library's `security` audit ([TOOL_AUTHORITY](TOOL_AUTHORITY.md)). `WorldToolbox` refuses a trusted handle.

Heads resolve as the rest of the app resolves them (`ChangeSetLibrary.head`), and an entity `load()` drops (an id reused across kinds, say) is listed with no head and refused on read (`entity_dropped`), so a job never sees a head `load()` would not. Each tool call is its own read under the store lock; the job holds no lock between steps, so a long audit does not block the library ("explore outside file locks").

The job checks citations against **its own copies** of what it read, taken before the worker sees a result, and keeps a re-validated copy of each finding it accepts. So re-running a frozen value's `__init__` on what the worker was shown, or on a finding after it was accepted, changes no evidence and nothing kept.

The worker is untrusted code **in the same process**. The boundary is the job's API (observations in, actions out), not an OS sandbox: Python code given to `run_job` could import the store itself. A worker from outside, such as an external script or agent, should drive a job over a process boundary. That adapter is not built.

## The job (`grimoire.investigations`)

`InvestigationSpec(job_id, source, goal, budget=Budget(), tools={"list", "read"})`. `job_id` is `[a-z0-9][a-z0-9._-]{0,47}`; `goal` is at most 2,000 characters of text for the worker; `tools` may narrow the two tools, never add one.

`Budget(max_steps=400, max_reads=200, max_findings=50, max_seconds=None)`. Every action, refused ones included, costs a step and is recorded, including the one a read or findings limit stops.

`run_job(spec, worker, tools, *, clock, cancel=None)` loops: check `cancel`, check the step and time limits, ask `worker.next_action(observation)`, check `cancel` again, then run the action. The clock and the cancel token are passed in; the module reads neither a clock nor the environment.

| Action | What the job does |
|---|---|
| `ListRecords()` | Lists the source's entities: `Listed(kind, entity_id, revisions, head)`. |
| `ReadRecord(kind, entity_id)` | Reads that entity's head: `RecordRead(kind, entity_id, revision, etag, name, payload)`. Costs a read. |
| `Report(Finding)` | Checks the finding's evidence; accepted as `f1`, `f2`, … or rejected with a reason. |
| `Finish(note)` | Ends the job `finished`. |

Each observation carries the step number, the action, its result and what is left of the budget. A refusal is an observation the worker can react to (`denied`, `missing`, `no_head`, `entity_dropped`, `tool_not_allowed`, `invalid_target`, `unknown_action`).

| Status | Reason |
|---|---|
| `finished` | The worker finished. |
| `cancelled` | `cancel.cancelled` was set before an action or while the worker chose one; the chosen action is not run. |
| `budget_exhausted` | The first limit hit: `steps`, `reads`, `findings` or `seconds`. Time is checked again after the worker chooses, so a slow choice runs nothing. |
| `failed` | `worker_error` (the worker raised, or returned a malformed action that raised when run) or `tool_error` (the toolbox raised, returned something other than a `ToolResult`, or returned another record). The note keeps the exception's type name only, never its message. |

`InvestigationReport` holds the spec, status and reason, the accepted and rejected findings, every step, the records read, the listed entities never read (`unread`), a trace (`grimoire.tracing`: root span `investigation`, one `investigation.step` per action, counts of steps, reads, findings and rejections) and the worker's note. `describe()` gives the lines a reviewer reads.

## Citations

`Finding(category, summary, citations)`: `category` is `[a-z][a-z0-9_]{0,47}`, `summary` at most 500 characters, 1 to 16 citations.

`Citation(kind, entity_id, revision, etag, field=None, quote=None)`. `field` is an RFC 6901 JSON pointer into `{"name": ..., "payload": ...}` (so `/name`, `/payload/description`); `quote` (at most 200 characters) needs a field. `RecordRead.cite(field, quote)` builds one from a read.

| Rejection | Meaning |
|---|---|
| `uncited` | No citation. |
| `unread_evidence` | The cited entity was not read in this job. |
| `revision_mismatch` | Read at another revision or etag (also after a re-read moved it). |
| `unknown_field` | No such field in that revision. |
| `quote_not_text` | The field is not a string. |
| `quote_not_found` | The words are not in the field. |
| `duplicate_finding` | The same category over the same cited fields was already accepted. |
| `not_a_finding` | `Report` did not carry a `Finding`. |

A citation's kind, id and revision must be plain `str` values (a subclass could compare equal to a revision never read); constructing one otherwise raises. The job only guarantees that the evidence is real and exactly attributable. Whether the claim follows from it is the reviewer's call.

## The built-in worker (`ConsistencyAuditor`)

A deterministic worker that needs no model: it lists the source, reads every entity with a head in sorted order, and reports:

- **`dangling_reference`**: a payload string that is a reference into this source (`world:harbor/location/old-hall`, optionally `@rN`) to an entity the source does not have, or to a revision that does not exist.
- **`possible_duplicate`**: two entities of one kind whose display names match after normalization (NFKC, case-folded, punctuation as spaces, a leading "the" dropped) and that do not refer to each other. It cites both `/name` fields.
- **`missing_link`**: one record's text names another entity's display name (the whole name, as words, any case, at least three characters) while neither refers to the other. It cites the mentioning field with the matched words, and the named entity's `/name`.

Definitions in V2 have no structured relations, so a "link" is a payload string holding the other entity's reference. A reference to another source is neither a link nor prose: a namesake here is not who `world:saltmarch/character/winifred` names.

## Review and stale evidence

`InvestigationService.review(report)` re-reads the head of every cited entity under the store lock and returns `AuditReview`: each finding with the changes in its evidence (`head_moved` when the head is another revision, `edited` when the same revision's bytes changed, or the read's refusal such as `missing`). `describe()` adds "f1 is stale: kind/id edited". A review reads only.

A review checks the records a finding **cites**, not the ones it says are absent. A `dangling_reference` finding stays current after someone creates the missing target, because only the referring record is cited and it did not change. Rerun the audit to drop it.

## Answers to the sprint's open questions

- **What job state must survive restart?** For this slice, none: the report is an in-memory value, and a job that dies is rerun. The durable trail is the authority's audit of the grant and each read. Resumable jobs (milestone 3) would need the spec, the steps and the reads (revision and etag) recorded; re-checking those reads is how a resumed job would know its evidence still holds.
- **How is stale evidence detected?** By revision and file etag: each citation pins both, and `review` compares them to the current head. Any write to a cited record, including a hand edit of the same revision's file, makes the finding stale. Creating an entity a finding says is missing does not (see above).
- **Which user permissions are revocable?** Every job's grant is revoked when the job ends, and the owner can revoke it earlier with the authority (a revoked grant refuses the next read, which the worker sees as `denied`). There are no standing user permissions for jobs yet; "safe low-risk permissions under explicit user policy" is milestone 3.

## Limits

- No model worker. A model-driven worker plugs into the same `Worker` protocol; it is not built because model calls cost money and the live inference stage is turn-bound.
- No persistence, resumption or queue: one job runs synchronously in the caller.
- No process boundary for outside workers (see Trust model).
- A crash between granting and revoking leaves an `inspect` grant active in the durable authority. Only trusted code can mint a handle for it, and the grant reads one source; nothing cleans it up yet.
- No UI or app route (116), no proposals or repairs (milestone 2).
- Review does not notice a missing entity being created, so a `dangling_reference` finding can outlive its cause until the audit is rerun.
- Two jobs given the same `job_id` share a principal, so the audit trail cannot tell their reads apart; each toolbox is still pinned to its own source.
- The auditor's checks are lexical: a name used as an ordinary word ("Harbor") can be reported as a missing link, and two distinct entities with one name as a possible duplicate. That is why they are findings for review, not edits.

## Tests

- [`test_investigation_store.py`](../backend/tests/test_investigation_store.py): the proving scenario (a missing guild link and a possible duplicate, both cited, nothing written, and a cancelled run that keeps its findings and writes nothing), the audit trail, scope denials, lying workers, staleness, refusals and budgets.
- [`test_investigations.py`](../backend/tests/test_investigations.py): the pure loop, budgets, cancellation, evidence checks and the auditor.
