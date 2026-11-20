# A campaign's story continuity (115)

The **Story** page (`#/story/<campaign id>?chat=<ChatRef>&audience=director`) is the director's screen for two
Wave 8 slices: the scenes two devices changed apart offline (049) and the review of a retcon or fork (054). The
campaign hub (113) and the Chronicle's feature list ([CHRONICLE](CHRONICLE.md)) link it, for the same chat and
view.

It is served by the `story` adapter (`grimoire.app.adapters.story`, owner 115), over the existing domain services
only: 019/028's keep-both conflict records and the last sync reports (as Devices and sync reads them), 049's
`semantic` review (`resolution_risk`, `validate_transition`), and 054's `ContinuityLibrary` (`snapshot`,
`preview`, `rehearse`, `corrections`, `declare_dependency`, `inspect_fork`). It keeps no store of its own and
answers from the library each time.

Continuity is the director's: every op refuses `director_only` unless `audience` is `director`, and in the table's
view the page says so, shows no scene text and offers nothing. A campaign that is not in the library refuses
`no_campaign`; a bad argument refuses `invalid_field`. Domain refusals keep a reason (`changed`,
`not_authorized`, `unreadable`, `continuity_refused`) and never answer HTTP 500.

## Divergent offline scenes (049)

| Op | Args | Answer |
|---|---|---|
| `story.divergent` | `campaign`, `audience` | `{campaign, device, peers[], scenes[], ancestry[], held[], unknown, retained[], problems[], fork_choices[], unavailable[]}` |

Each entry of `scenes` is a file the sync kept in two versions here: `mine` and every `theirs` (both stay until a
person resolves the file), `keep_one` (`refused` where 049's semantic review holds the file, so the ordinary
keep-one resolver would take one version over what rests on the other; `needs_you` otherwise), `detected` (the
peers whose last sync held it), `taking_theirs` (049's reason codes for what taking each other version here would
leave unmet), `uncertain` (its ancestry is not recorded: a legacy or unreadable accepted event, or a review that
cannot be made) and `continues` (an earlier scene of the same chat differs too: a divergent continuation). The page
shows uncertain ancestry and divergent continuations as **refused**, with the reason. `held` lists the accepted
events and holdings the last sync held with them, so no effect is applied twice or lost.

`retained` lists the retained forks the campaign holds: each scene's exact original from every device, which one
was restored, and `reconciled_here` (this device holds the restored scenes and the ledger the fork predicts).
Ordinary sync brings the accepted effects across and applies each once.

The page decides nothing here: keeping one side, where it is allowed, is in Needs you, and syncing is on Devices
and sync (`pages/sync`), which the page links rather than repeats.

## Reviewing a retcon or fork (054)

| Op | Args | Answer |
|---|---|---|
| `story.scenes` | `campaign`, `audience` | `{campaign, etag, coverage, records[], dependencies[], problems[], declarable[], actions[], unavailable[]}` |
| `story.preview` | `action` (edit, cut, replace, replay, fork), `roots[]` (`{path, revision}` as shown, at most 64), optional `alternative` | `{complete, problems[], impacts[], corrections, corrections_refused, rehearsal, written: false}` |
| `story.declare_plan` | `source`, `dependent` (`{path, revision}`), `reason` (dialogue, causal_action, asset), `operation_id`, `expected` (the `etag` shown) | `{plan: {operation, already_applied, source, dependent, reason, files[], reaches[]}, scenes}`; nothing is written |
| `story.declare_commit` | the same args as the plan | `{done, scenes}` |

A preview names each record that rests on the roots, why (`reason`) and whether that is `known` or only
`potential`, with the references a memory correction (053) would cite. An incomplete snapshot previews with its
problems and exports no correction. An authored alternative is rehearsed (no model, not saved). A root whose
revision moved refuses `changed`; one no longer there refuses `missing_record`.

A declaration records what 054's native records cannot see, such as a later line of dialogue that rests on an
accepted event. The page shows the plan, then writes it only on **Make this change**, with the same operation id
and the snapshot revision as reviewed:

- a snapshot changed since the review refuses `changed`, and the page reads it again;
- the same declaration made again with its operation id is `already_applied` and writes nothing; the id reused
  for another one refuses `operation_conflict`;
- an edge already recorded refuses `already_declared`; one that would make a circle refuses `invalid_dependency`;
  an incomplete snapshot refuses `incomplete_snapshot`; a review not made refuses `review_required`.

## Not available

Named on the page with the reason, never offered as a control:

- **Restore one device's scene as a retained fork.** `continuitystore.resolve_fork` needs both devices'
  libraries open on one machine, each with its own authority; this app serves one library and reaches the other
  only through sync. Its result (a retained fork) is shown once it exists.
- **Merge both versions of a scene.** 049 keeps both versions; it never merges their text.
- **Repair what depends on a changed scene**, **write a continuation** and **promote an alternative into the
  story.** 054 flags what a change touches, calls no model and keeps alternatives historical.

## Not proved here

Tests: `test_app_story.py` (ops, refusals, privacy, stale review, idempotent retry, a retained fork reaching the
second device once), `js/story.test.mjs` (page logic, late answers, drafts) and `js/story.e2e.mjs` (fakedom
against a real app server on both devices). The restoration between the two runs is made by 054's owner operation
outside the app. The page has not been used in a real browser, with a screen reader or on a phone.
