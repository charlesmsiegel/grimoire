# Models and costs in the app (112)

First slice of [UI Inference, Prompts and Costs](roadmap/sprints/ui-inference-prompts-costs.md). The app's
**Models and costs** page sets up the model the table plays with, says which model calls a turn may make, and
shows what each chat's calls used. Its ops are `grimoire.app.inference.InferenceApi`, served beside 044's
[text API](TEXT_API.md) on the same loopback address and token ([MOBILE](MOBILE.md)).

`InferenceApi` writes one thing: the app's model configuration file. It lives outside the library, because it is
per device and names where the provider's key is read from, never the key. Everything else is read from records
other services own. Nothing in the library is written, and nothing is sent to a provider by these ops.

## Ops

| Op | Arguments | Result |
|---|---|---|
| `models` | none | `settings` (the configuration, with `key_set`: whether the key variable is set where the app runs, never its value; `null` when none), `etag` (the file's revision, `null`: no file yet), `file` (`configured`: the app was served with a settings file; `exists`), `generation` (`available`, `problem` with `code` and `message`, `routes`: the route labels in the order attempts use them, `local`), `stages`, `limits`, `unavailable`, `bounds` |
| `check_models` | `settings` (a draft) | `ok`, `problems` (each `field`, `message`), `routes`, `local`, `key_set`, `sent: false`. Nothing is saved or sent |
| `save_models` | `settings`, `expected` (the `etag` `models` gave; `null` when there was no file) | `models`' answer, after the file is written and the runner given what it configures |
| `usage` | `chat`, `limit` (turns listed, 1 to 1000, default 200; the totals cover every turn) | `turns` (each `turn`, `number`, `set_aside`, `calls`), `total`, `by_stage`, `complete`, `problems` |
| `diagnostics` | none | `available`, `traces`: this process's last runner calls, newest first |

### Settings

The file is 044's model configuration ([MOBILE](MOBILE.md#answers-from-a-model)) with two more keys:

- `fallbacks`: up to 4 other models **on the same endpoint**. 013's adapter gives attempt *i* of a call the
  *i*-th route, so a fallback is tried only by a retry.
- `attempts`: attempts per planned call, 1 to 3 (004's `max_attempts_per_call`, default 1). Every attempt may
  be billed, and retries happen only before any text reached the table (013).

`director` (0 or more; any count above 0 allows the one director reply a turn may make) is the optional
`director_generation` stage. `max_output_tokens` is 1 to 32,768.

**Saving** writes the whole file atomically, only if it is still at `expected`: a file changed since it was read
(by another tab or by hand) is refused `changed`, and nothing is written. A file edited or removed by hand while
the app runs is read again the next time `models` is asked, and played by from then on, so the page always shows
the file as it is. The runner then plays its **next** call
by the new runtime and policy; a call already running finishes as it began. A configuration whose key variable is
not set is saved, and generation stays off with `problem.code: key_missing`: the app reads its environment at
start, so set the variable and restart it.

**Key variables.** A configuration saved from the app may name only a key variable that looks like a provider
key: capitals, digits and `_`, ending `_API_KEY` (`OPENROUTER_API_KEY`). Otherwise whoever holds the serve's
token could have the app send any of its environment, a cloud credential say, to an endpoint of their choice. A
file the owner wrote by hand keeps whatever name it gives, and saving it again from the app with that name is
allowed for the endpoint it was written for, never with another `base_url`. A refused name is never echoed back,
in case it was a key pasted by mistake. The token holder can still point a provider key variable at another https
endpoint: the token is the owner's, as for every other op.

**Starting.** `serve --model FILE` reads FILE if it exists. A file that does not exist yet, or cannot be used,
starts the app without a model: generation is refused `no_runtime`, everything else works, and `models` says why
(`not_configured`, `settings_unreadable`, `settings_invalid`, `key_missing`). A file whose content cannot be used
is never echoed. Without `--model` the settings are read-only (`file.configured: false`, `save_models` refused
`read_only`).

### Stages

One entry per 004 stage: `stage`, `kind` (`baseline` or `optional`), `enabled`, `cap`, `available` and a `note`.
`npc_generation` is the baseline: one call per speaking NPC, never switched off. `director_generation` is the one
optional stage the app plays. Every other optional stage is listed `available: false`, with the reason, because
the app wires no service that would make the call: a switch that changes nothing is never offered.

### Usage

`usage` reads the chat through 035's `SceneRunner.open`, and lists every planned call of every take and
labelling of its turns, the turns a fork choice set aside included (they were billed, though nobody heard them):

- `take` or `label`, its `state`, `phase`, `stage`, `kind`, `actor` (the instance id; `null` for the scene-level
  director reply) and `name` (the cast's display name), `status` and `reason`;
- `attempts`: each `attempt`, `outcome`, `measurement` (`provider_reported`, `estimated` or `synthetic`, as 013
  chose it), `route`, and `tokens`, each count `null` when the provider did not report it;
- a take's `declined` optional requests, as 035 recorded them.

`total` and each of `by_stage` are 004's sums: `sums`, `unreported` (attempts that left each count unreported) and
`lower_bound` (a sum is a lower bound while any of its counts is unreported: a screen never shows it bare),
`measurements`, `unmetered_calls` (planned calls with no attempt booked, as 004 counts them) and `not_issued_calls`
(those of them never sent, so they used nothing; what the rest used is unknown, never free). `listed` and `of` say
how many turns are listed, of how many.

**No prices ship (013)**, so every `cost` is `null` with `cost_state: "unpriced"`: unknown, never zero.

**What is never shown.** A usage answer holds no prompt, reply, post or line text, and no brief: only what 013
booked. It names the NPC each call was for, which the owner, who directs the table, already sees in the cast.

### Diagnostics

`InferenceApi` gives the runner 048's tracer when it has none, and keeps the last 64 traces in memory: never
written, and gone when the app stops. Each is the runner call's `operation`, `status` and `seconds`, its span
`breakdown`, and its model calls per role, from 048's `call_costs`: `stage`, `actor` (`npc-1`, `npc-2`... in
plan order, never an id or a name), attempts, retries, failures, lost calls, `latency` (`measured` only over a
real provider on the real clock; `synthetic`, `partial` or `unreported` otherwise), `provider_seconds` and
`first_byte_seconds`, and usage. A trace names no chat, so diagnostics are per device, not per chat.

## The page

`grimoire/app/static/pages/inference.js` exports `render(root, ctx)`, where `ctx.api(op, args)` posts one op. It
has five sections: **Model** (the settings form, **Check** and **Save**), **Model calls a turn may make**,
**Usage by chat**, **Recent calls on this device** and **Not available yet**. Every library and provider text is
set as text, never markup. `backend/tests/test_app_inference_page.py` runs the page in Node over a fake DOM and
fake ops (skipped where Node is not installed).

## Not available yet

Each is listed on the page with its reason (`unavailable` in `models`):

- **A connection test.** 013's adapter sends only a turn's planned calls, and 004 has no job-scoped ledger to bill
  a call outside a turn, so a test request would be an unbilled, unplanned call. **Check** validates a draft and
  sends nothing.
- **A separate director model.** 027's runtime calls one list of models for every call.
- **Other endpoints.** The app configures one endpoint; fallbacks are other models on it.
- **Prices and money.** None ship (013).
- **Session budgets.** 004 caps calls per turn only.
- **Prompt and context preview, prompt history.** No V2 service previews a brief or keeps a prompt: briefs are
  built for a call and never stored (013's privacy rule). When one exists it must show only what the table's
  player may read: never another NPC's private evidence.
- **Provider presets.**
