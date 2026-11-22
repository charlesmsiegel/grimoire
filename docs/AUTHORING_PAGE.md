# A source's authoring tools (116)

The **Authoring** page (`#/authoring/<source id>`) is part of Wave 9's UI exit. It gathers the library owner's
authoring tools for one source: world authoring proposals (087), a read-only consistency audit (082) and source
upgrade previews with optional dependencies (079). Every record's Library page (109) links to it for that
record's source, under **Authoring**. The page does not split into a table view and a director view: these tools
belong to whoever owns the library, and the owner is the only person the app serves.

The page is served by the `authoring` adapter (`grimoire.app.adapters.authoring`, owner 116). Like every adapter,
it works only over the merged domain services:
- 087's `AuthoringLibrary` and `AuthoringGenerator`;
- 082's `InvestigationService`;
- 079's `grimoire.sourceupgrades`.

It keeps no store of its own. Two things live only in the app's memory, under ids the server chose: 087's
proposals and 082's reports. Neither domain persists them. **A restart loses every proposal that was not
accepted, and every audit report.** The page says so.

`authoring.source` (`source`) is the page's first read. It answers:
- the source: its title, whether it is a world, and its manifest etag;
- the existing records 087's `context` offers as link targets (worlds only), each with whether it is a place;
- whether a suggestion can be sent now, and why not;
- the proposals and audits held for the source;
- the bounds the forms are checked against;
- the list of what is not built, each with its reason.

It refuses `missing_source` or `excluded_source`. A source that is not a world (a card, a bestiary) still has its
upgrade previews. Its proposals and audit sections each name 087 or 082 as working on world sources only, and
offer no control.

## World suggestions (087)

| Op | Args | Answer |
|---|---|---|
| `authoring.build` | `source`, `candidates[]`, `links[]`, `rationale`, `brief` (optional) | `{proposal}`, already reviewed |
| `authoring.suggest_plan` | `source`, `request_id`, `brief`, `constraints[]`, `max_candidates` (1–16, default 8) | `{plan: {request, expected, replay, blocked, call, context}, note}`; nothing is sent |
| `authoring.suggest` | the same, and `expected` (the plan's token) | `{done: {request, replay}, proposal}` |
| `authoring.review` | `source`, `proposal` | `{proposal}` |
| `authoring.draft` | `source`, `proposal`, `accept[]`, `distinct[]`, `topology` (optional) | `{proposal}` with its `draft` |
| `authoring.apply` | `source`, `proposal`, `draft` (the draft's digest) | `{done: {already_applied}, proposal}` with what was `applied` |
| `authoring.reject` | `source`, `proposal` | `{proposal}`, discarded |

A proposal view has these fields:
- `proposal`, `origin` and `kind` (`hand` or `model`), and `state` (`open`, `applied` or `rejected`);
- `candidates[]`: each with key, kind, name, role and description, whether it can be stored, and its `duplicates[]`
  (an existing record, or another candidate, that shares its name);
- `links[]`: each with its relation, its subject, its `object` (a candidate, or an existing record by its pinned
  ref) and its review `problem`;
- `draft`, `applied` and `usage`.

The page never sends a proposal's content back. It names a proposal only by the id the server gave it, so a client
cannot present its own candidates as the model's.

**By hand.** The builder holds rows of places (`location`) and groups (`organization`), each with a role:
- an `individual` record to keep;
- a `template` kind;
- a `brainstorm`, which is never kept.

Each row gets a stable key (`c1`, `c2` and so on). Links are a road between two places, or one place lying within
another. A link's far end is another candidate or one of the world's places, named by the exact pinned ref the
context offered. The builder survives leaving the page as the page's draft, until it is reviewed. `build` checks
what it is sent exactly as 087 checks a model's reply (`parse_proposal`), with the library owner as its origin
(whatever the page sends). It keeps 087's refusal reasons, among them:
- `unknown_record`: a ref the context did not offer, or one at another revision;
- `template_endpoint`;
- `invalid_shape`: an unknown field, an `origin` included.

A source that is not a world refuses `not_a_world`.

**From the model.** This is off unless the model settings' `authoring` is 1 (Models and costs, *World
suggestions*; [INFERENCE_UI](INFERENCE_UI.md)). When it is off, the plan answers `blocked`, and `suggest` refuses,
with one of these reasons:
- `no_model`: no model is set up;
- `not_enabled`: suggestions are switched off;
- `key_missing`: the key variable is not set.

The page shows the reason and offers no call. `suggest_plan` builds the exact messages 087 would send and answers
the app's estimate (as Prep's scene preparation does: the prompt counted as its bytes, the reply cap, the configured
prices). With it comes a review token over:
- the request and the context it offers;
- the model settings' etag and the estimate;
- the calls already spent under that request.

Nothing is sent. `suggest` builds everything again, refuses `changed` if anything moved (and `review_required` with
no token), and only then runs 087's generator. That is one optional `planning` call under the settings' authoring
policy, through 013's adapter, on an untrusted handle of its own (`app:world-author`), approved for its exact
payload while that one call is made. It is one 048 trace, `world_authoring`.

The same request asked again answers the proposal it already gave and sends nothing. Asked with another brief, it
refuses `request_conflict`. A reply 087 refuses keeps nothing and keeps its reason (`unknown_record`, for one). The
spent call is part of the next estimate, so the old token cannot approve a second send.

**Review, decide, write.** Every proposal is reviewed when it is made, and again on **Review again**. 087's review
shows each namesake of a record already in the world, and each link problem. The person ticks **Accept** on the
candidates to keep, and **It is a different place: keep both** on a namesake to keep. A brainstorm has no Accept.
**Draft the change** answers 087's acceptance draft:
- every file the change set would write, byte for byte (with its sha256 and size);
- its preconditions, each linked to the record;
- the links kept and the links dropped, with why;
- the candidates not stored.

A draft writes nothing. It refuses as 087 does:
- `duplicate_unacknowledged` (a namesake not marked distinct);
- `brainstorm_not_storable`;
- `stale_review`, when the world moved since the review. The page then reviews the proposal again by itself and
  says so.

**Approve and write** sends the draft's digest. `apply` writes exactly that draft through 043, as the owner's
press:
- a digest that is not the draft held now refuses `changed`;
- a world that moved since the draft refuses `conflict` and writes nothing, and the page reviews again;
- retried after it was written, it answers `already_applied` and writes nothing again;
- a proposal already written refuses `already_applied` to `review` and `draft`.

After a write the page links each created record and names the world's new map revision. **Discard** (`reject`)
ends the proposal and writes nothing. A brainstorm, and any candidate not accepted, is never stored anywhere.

## Consistency audit (082)

| Op | Args | Answer |
|---|---|---|
| `authoring.audit` | `source`, `max_steps` (1–2000, default 400), `max_reads` (0–1000, 200), `max_findings` (0–200, 50), `max_seconds` (1–60, 20) | `{audit}` |
| `authoring.audit_review` | `source`, `audit` | `{audit, review: {stale[], findings[{finding, current, changes[]}]}}` |

`audit` runs 082's deterministic `ConsistencyAuditor` over one world through `InvestigationService`, as the library
owner, within the budget the page chose. It looks for dangling references, possible duplicates and missing links.
A value outside its bounds refuses `invalid_field`, and a source that is not a world refuses `not_a_world`.

The report answers:
- its `status` and `reason`, and `written: false`, which the page shows as *Nothing written*;
- every accepted finding with each citation (kind, id, revision, field, quote), linked to the cited record;
- the rejected findings, the records read and the records not read.

082 grants the job `inspect` alone and revokes it however the job ends. No source or campaign file is written. The
library's security log records the grant and each read, as 082 says.

**Check for stale evidence** is 082's `review`. It reads the head of every cited record now, and marks a finding
stale with what changed. A report is named only by the id the app gave it. One the app never made, or one for
another source, refuses `unknown_audit`.

## Source upgrades (079)

| Op | Args | Answer |
|---|---|---|
| `authoring.upgrades` | `source` | `{removal, dependencies, update: {available: false, reason}, apply: {available: false, reason}, written: false}` |
| `authoring.pin_plan` | `source`, `dependency`, `expected` (the manifest etag shown) | `{plan: {dependency, version, expected, before[], after[], declared, files[]}}`; nothing is written |
| `authoring.pin` | `source`, `dependency`, `version`, `expected` (as reviewed) | `{done: {already_applied, version}, dependencies}` |

**Removal preview.** This is 079's `preview_source_removal`. Every reference elsewhere in the library that removing
the source would leave missing is grouped by the file that holds it, with its record kind and each reference's
status. The preview also lists the source's own entities, the problems that make it incomplete, and `complete`.

**Dependencies.** This is 079's `dependency_report`. Each declared dependency is pinned, drifted, following or
missing, against its current version. Any other source can be pinned at its current version. **Review the pin**
shows the declarations before and after, and the exact bytes of the manifest it would write. They are staged in a
store transaction that is never committed. **Pin it** is 079's `pin_dependency`, guarded by that etag:
- a manifest that moved since the review refuses `changed`, and the page reads it again;
- a dependency whose version moved since refuses `changed`;
- retried after it was made, it answers `already_applied` and writes nothing again.

079 also refuses `self_dependency`, `missing_dependency` and `limit_exceeded`. A proposal written from the same page
moves the manifest, so the upgrades section reads it again then.

## Not on a screen

The page names each of these as not available, with its reason:
- Campaign places and groups. 087 writes world records only.
- Other kinds of record and link. 087 formalizes places and groups, joined by roads and containment.
- Larger linked regions (087 milestone 3).
- Keeping a proposal across a restart. 087 keeps proposals in memory only.
- A model auditor, repairs from an audit (082 milestone 2), and resumable or queued audits (082 milestone 3).
- Previewing an update. 079 needs a candidate library (an unpacked update opened read-only), and the app has no way
  to open one: installing a package is Sprint 103's.
- Applying an update, or removing or disabling a source. 079 builds previews only.

## Coverage

| Slice | Feature | Route | Integration test | When it is not there |
|---|---|---|---|---|
| 087 | World authoring proposals | `authoring` | `test_app_authoring.py`, `js/authoring.e2e.mjs`, `js/authoring.test.mjs` | off unless World suggestions is on in Models and costs, or no model set up: the page says so and offers no call, and building by hand still works; a stale estimate refuses `changed` and sends nothing; a stale review refuses `stale_review` and the page reviews again; a world that moved since the draft refuses `conflict` and writes nothing; a source that is not a world refuses `not_a_world` |
| 082 | Read-only consistency audit | `authoring` | `test_app_authoring.py`, `js/authoring.e2e.mjs`, `js/authoring.test.mjs` | a source that is not a world refuses `not_a_world`; a budget out of bounds refuses `invalid_field`; an unknown report refuses `unknown_audit`; nothing is written |
| 079 | Source upgrades and optional dependencies | `authoring` | `test_app_authoring.py`, `js/authoring.e2e.mjs`, `js/authoring.test.mjs` | an update preview is named as not available; a stale manifest refuses `changed`; a retried pin writes nothing; applying an update or removal is named as not built |

## Not proved here

The page runs end to end in fakedom against a real app server, with the one model call answered by a scripted
transport. It has not been used in a real browser, with a screen reader, on a phone, or against a live model.
