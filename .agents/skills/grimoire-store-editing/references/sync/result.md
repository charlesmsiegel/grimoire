# Interpret and verify the result

## Read the result

Exit 0 means the sync completed; 1 means failed, refused, missing, unverified or
unfinished work, an early stop, or failed token updates. Exit 2 is invalid syntax;
3 means the library root could not be used. Read the entire report: successful
listed paths alone do not establish that the batch finished.

| Vocabulary | Names | Meaning |
|---|---|---|
| Path status | `refreshed`, `current` | The derived work is current. |
| Path status | `cold` | Nothing was hot for this record; this is expected. |
| Path status | `deleted` | An intended removal was handled. |
| Path status | `missing` | Check the path or declare the intended deletion. |
| Path status | `refused` | Resolve the refusal reason before retrying. |
| Path status | `failed` | A reader or rebuild failed; inspect the exception class. |
| Path status | `verify_failed` | Cached and fresh derivations disagreed; report it. |
| Path status | `unsynced` | Work is unfinished; inspect its reason. |
| Refusal reason | `outside_store`, `link`, `not_syncable`, `temp_file`, `not_a_file` | The target is not a permitted record path. |
| Refusal reason | `not_found`, `not_deleted`, `bad_id`, `too_many`, `unreadable` | Correct the input or access problem. |
| Kind outcome | `built`, `hit`, `planned` | Rebuilt, already current, or planned by a dry run. |
| Kind outcome | `lazy`, `deferred`, `skipped`, `failed` | Inspect work left for later, deliberately omitted, or failed. |
| Batch field | `tokens_bumped`, `tokens_failed` | Failed updates leave the app uninformed about some writes. |
| Batch field | `truncated`, `stopped` | A bound or stop curtailed the batch; report it. |
| Embedding field | `would_send`, `sent`, `requests`, `held` | Preview, request and cached-text counts. |
| Embedding field | `stale_space`, `embedding_off`, `deferred` | Vectors were not refreshed in this space, or spending was disabled. |
| Embedding field | `not_sent`, `failure` | Report incomplete embedding warm-up. |
| Stop reason | `cancelled`, `root_moved`, `lane_busy` | Explain why the batch stopped before claiming completion. |

A nonzero `tokens_failed` or nonempty `stopped` is incomplete, even beside
`refreshed` paths. `cache: off` is a supported configuration. Never discard
embedding `failure` or `not_sent` when reporting the result. The report has
private paths and root names, although it includes no record prose: keep it
in conversation, and put only scratch-run counts/statuses in repository evidence.

## Check one reader

Read an edited record through its store reader, or inspect its app page.
For a substantial batch, rerun the same sync with `--verify`, preserving
`--no-embed` when required. Verification and a failed writer need separate
reporting; a multi-file batch is not a transaction with automatic rollback.

## A library without a Python shell

On a phone, ordinary reads rebuild lazily. `POST /api/cache/sync` is available
through loopback only, for a PC with an existing `adb forward` setup.
An open page can paint remembered data briefly before revalidation; see the
[overview consistency bound](../../../../../docs/store-guarantees.md#what-an-overview-page-may-show-from-memory).

## Report back

Describe the verified edits, sync counts and any failed, deferred or stopped
work. Library commits follow that library's own history. This public checkout
gets no library names or contents; its evidence concerns isolated placeholders.
