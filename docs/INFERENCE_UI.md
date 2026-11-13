# Models and costs in the app (112)

First slice of [UI Inference, Prompts and Costs](roadmap/sprints/ui-inference-prompts-costs.md). The app's
**Models and costs** page sets up the model the table plays with, says which model calls a turn may make, and
shows what each chat's calls used. It is the `providers` page of 108's [app shell](APP_SHELL.md) (`#/providers`,
in the settings slot), and its ops are the shell's `providers` adapter (`providers.models`...), served beside 044's
[text API](TEXT_API.md) on the same loopback address and token ([MOBILE](MOBILE.md)). They are
`grimoire.app.inference.InferenceApi`'s, one per open library: `serve` gives it the settings file
(`grimoire.app.adapters.providers.configure`) before it listens, and runs it under the text API's lock.

`InferenceApi` writes one thing: the app's model configuration file. It lives outside the library, because it is
per device and names where the provider's key is read from, never the key: it does not sync, so each device keeps
its own model and its own prices. Everything else is read from records other services own. Nothing in the library
is written, and one op alone sends anything to a provider: `test_call`, when the person asks for it.

## Ops

Each is called as `providers.<op>`.

| Op | Arguments | Result |
|---|---|---|
| `models` | none | `settings` (the configuration, with `key_set`: whether the key variable is set where the app runs, never its value; `null` when none), `etag` (the file's revision, `null`: no file yet), `file` (`configured`: the app was served with a settings file; `exists`), `generation` (`available`, `problem` with `code` and `message`, `routes`: the route labels in the order attempts use them, `local`), `stages`, `limits`, `prices` (`set`: both prices are saved; `route`: the route they price), `test_call` (`available`, `reason` when not, `seconds`: its time limit), `unavailable`, `bounds` |
| `check_models` | `settings` (a draft) | `ok`, `problems` (each `field`, `message`), `routes`, `local`, `key_set`, `sent: false`. Nothing is saved or sent |
| `save_models` | `settings`, `expected` (the `etag` `models` gave; `null` when there was no file) | `models`' answer, after the file is written and the runner given what it configures |
| `usage` | `chat`, `limit` (turns listed, 1 to 1000, default 200), `skip` (the newest turns left out, default 0: how earlier turns are listed); the totals always cover every turn | `turns` (each `turn`, `number`, `set_aside`, `calls`), `skip`, `listed`, `of`, `total`, `by_stage`, `complete`, `problems` |
| `context` | `chat`, optional `actor` (full InstanceRef) | Present director-controlled NPCs; for the selected NPC, current brief, selected items/provenance, budget omissions/estimate, effective routes and editable tactics. No model call or write |
| `save_agenda` | `chat`, `actor`, `expected`, `aim`, `actions` (1–8 lines), or `remove: true` | Saves only this NPC’s simple tactics in the settings file; returns `models`. Refuses player/foreign actors and advanced policies |
| `diagnostics` | none | `available`, `traces`: this process's last runner calls and test calls, newest first |
| `test_call` | `expected` (the `etag` `models` gave) | How one test request to the saved model ended: see [The test call](#the-test-call) |

### Settings

The file is 044's model configuration ([MOBILE](MOBILE.md#answers-from-a-model)) with these further keys:

- `fallbacks`: other models **on the same endpoint**, at most one fewer than `attempts` (so up to 2). 013's
  adapter gives attempt *i* of a call the *i*-th route, so a fallback is tried only by a retry, and a model no
  attempt could reach would stop every call before it is sent: such a file is refused.
- `attempts`: attempts per planned call, 1 to 3 (004's `max_attempts_per_call`, default 1). Every attempt may
  be billed, and retries happen only before any text reached the table (013).

- `decision`: optional decision calls per turn, 0–16 (default 0, off). A call is requested only for a speaking NPC with an authored agenda and no recorded decision for this scene turn.
- `agendas`: at most 64 actor-qualified, director-authored policies on this device. Each has `actor`, `goals` (`id`, `aim`, `priority`, `approaches` with `id`, `action`, `needs`, `tags`) and `limits` (`id`, `rule`, `forbids`, `when`). Conditions are `{"present": "<InstanceRef>"}` or `{"holds": "<proposition id>", "stances": [...]}`; existing 039 validation applies. The UI edits one simple goal with ordered actions and preserves unrelated policies. Policies with typed conditions/limits or custom priorities remain read-only in this simple editor, so no authored constraint is silently dropped.
- `price_prompt`, `price_reply` and `price_cached`: optional prices per million prompt tokens, reply tokens and
  cached prompt tokens ([Prices and costs](#prices-and-costs)).

`director` (0 or more; any count above 0 allows the one director reply a turn may make) is the optional
`director_generation` stage. `max_output_tokens` is 1 to 32,768.

The file carries no version number: every key beyond the endpoint and the model is optional with a default, and
a key the app does not know is refused. A file written before the price keys existed loads as it always did, and
saving a configuration with no prices writes no price key, so it is byte for byte the file it was.

**Saving** refuses a configuration over 64 KiB and paths inside V1 or the served V2 library, including aliases. It writes the whole file atomically, one writer at a time (an OS lock on `FILE.lock` beside it; a save that
waits more than 2 s for another is refused `busy`), only if it is still at `expected`: a file changed since it was read
(by another tab or by hand) is refused `changed`, and nothing is written. The revision is checked again just before the new file replaces the
old, so a hand edit made while it was written is refused too, not overwritten. A file edited or removed by hand while
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
in case it was a key pasted by mistake. A name that says whose key it is (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY` and the others in `OWNED_KEYS`) is sent from the app only to that provider's own hosts, a
subdomain of one, or an endpoint on this machine (a local proxy), so the token cannot point a well-known key at a
server of the holder's choosing; a hand-written file keeps such a name for the endpoint it names, as above. Any
other `_API_KEY` name can still be pointed at another https endpoint by whoever holds the token, and `check_models`
says whether such a variable is set: the token is the owner's, as for every other op, so name a key variable the
table owns when the token may be shared.

**Starting.** `serve --model FILE` reads FILE if it exists. A file that does not exist yet, or cannot be used,
starts the app without a model: generation is refused `no_runtime`, everything else works, and `models` says why
(`not_configured`, `settings_unreadable`, `settings_invalid`, `key_missing`). A file whose content cannot be used
is never echoed. Without `--model` the settings are read-only (`file.configured: false`, `save_models` refused
`read_only`), and a runtime the text API was built with (a test's, say) is left as it is.

### Stages

One entry per 004 stage: `stage`, `kind` (`baseline` or `optional`), `enabled`, `cap`, `available` and a `note`.
`npc_generation` is the baseline: one call per speaking NPC, never switched off. `director_generation` is the one
optional narrator stage. `decision` is independently opt-in and capped; its recorded choice is replayed on rerolls and after restart. Every remaining optional stage is listed `available: false`, with the reason, because
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
`lower_bound` (a sum is a lower bound while any of its counts is unreported, or a call was issued with no attempt
booked: a screen never shows it bare),
`measurements`, `unmetered_calls` (planned calls with no attempt booked, as 004 counts them) and `not_issued_calls`
(those of them never sent, so they used nothing; what the rest used is unknown, never free). `listed` and `of` say
how many turns are listed, of how many, after the `skip` newest.

Every attempt, `total` and each of `by_stage` also carry a cost at the prices set now (`cost`, `cost_state`,
`cost_lower_bound`, `unpriced_attempts`), and the answer's `prices` says whether both prices are set and for
which route: see [Prices and costs](#prices-and-costs).

**What is never shown.** A usage answer holds no prompt, reply, post or line text, and no brief: only what 013
booked. It names the NPC each call was for, which the owner, who directs the table, already sees in the cast.

### Diagnostics

`InferenceApi` gives the runner 048's tracer when it has none, and keeps the last 64 traces in memory: never
written, and gone when the app stops. Each is the runner call's `operation`, `status` and `seconds`, its span
`breakdown`, and its model calls per role, from 048's `call_costs`: `stage`, `actor` (`npc-1`, `npc-2`... in
plan order, never an id or a name), attempts, retries, failures, lost calls, `latency` (`measured` only over a
real provider on the real clock; `synthetic`, `partial` or `unreported` otherwise), `provider_seconds` and
`first_byte_seconds`, usage and its cost at the prices set now. A trace names no chat, so diagnostics are per
device, not per chat. A test call is traced here as the operation `test_call`.

### Prices and costs

**No prices ship (013).** A person may save three with the model, through `check_models` and `save_models` like
every other setting: `price_prompt`, `price_reply` and `price_cached`, each the price of a million tokens of that
kind on the configured `model`. No currency is recorded: the number is in whatever the provider bills in.

- A price is **decimal text** (`"3"`, `"0.25"`): up to six digits before the point and up to six after it, so 0
  to 999999.999999, and nothing else. A JSON number, a sign, an exponent, a space, `NaN` or an empty string is
  refused, naming the key, so no price ever passes through a float. An empty field in a draft leaves the key out.
- A cost exists only when **both** `price_prompt` and `price_reply` are set (`prices.set`). `price_cached` is
  optional: without it a cached prompt token is charged at the prompt price.
- The prices are the configured model's alone. An attempt booked under another route (a fallback model, or the
  model configured before) has no price: it is left out of the cost and counted in `unpriced_attempts`.

A cost is worked out when it is asked for, by 048's cost model (`grimoire.tracing.usage_cost`), from the token
counts 013 booked and the prices **as they are now**. No usage record stores a cost, so changing a price re-prices
every past call on that model, and removing one makes every cost unknown again. It is an **estimate, never a
bill**: `Decimal` arithmetic on whole token counts (tokens times price, divided by a million; uncached prompt
tokens at the prompt price, cached ones at the cached price, reasoning inside the reply), given as decimal text to
six places and never fewer than two. No record holds a cost a provider itself reported; were one to, it would be
shown instead of the estimate.

| `cost_state` | `cost` | Meaning |
|---|---|---|
| `unpriced` | `null` | Unknown, never zero: no prices are set, or every sent attempt went to a route with no price (`unpriced_attempts` above 0) |
| `priced` | text | Every count is the provider's own and nothing is missing |
| `estimated` | text | Some count was not the provider's own (a scripted or test transport, or an estimate) |
| `partial` | text | A lower bound |

`cost_lower_bound` is true whenever the number is a floor, whatever the state: a needed count went unreported (an
unknown cache share is charged at the cheaper rate), a call was issued with no attempt booked, or some attempts
had no price. A floor is rounded down, so it stays one; any other cost is rounded to the nearest millionth.

### The test call

`test_call` sends **one** small request to the saved model, and only when it is called: no other op, and nothing
the page does by itself, sends one. It goes the way a turn's reply goes, through the same 013 adapter the runner
plays with (so the same endpoint, key variable, authorization, redaction and booking), over the same routes in the
same order under the configuration's `attempts`: a retry is made, and a fallback model tried, exactly when a
turn's call would. It carries a fixed one-line prompt and asks for at most 32 reply tokens.

- It is planned on a turn of its own that belongs to **no chat**. Nothing in the library is read or written: no
  chat, no turn file, no story state. Its attempts are booked on that turn's meter and handed to 048's tracer, so
  it is among `diagnostics`' traces with its tokens, timing and cost. It is in no chat's `usage`, because it was
  no chat's call, and like every trace it is kept in memory only.
- `expected` must be the `etag` `models` gave (refused `changed` otherwise, nothing sent): what is tested is the
  saved configuration the screen showed, never a draft.
- Refused `no_model`, nothing sent, when no model answers (none configured, the key variable not set, or a model
  the app was started with and does not configure).
- Refused `key_destination`, nothing sent, when the key variable is one of `OWNED_KEYS` and the endpoint is
  neither that provider's own host nor this machine. A draft like that cannot be saved from the app at all; a
  file written by hand keeps its endpoint for turns, and a test call is still not sent there.
- The whole call, every attempt included, is cancelled after 30 seconds and answered as `status: "timeout"`.
  On Windows the cancel may not wake a read that is already waiting on the model (the transport's shutdown does
  not always interrupt a blocked `recv` there), so the answer can come later than the limit: when the read's own
  timeout or the server ends it. It is still answered as a timeout.
- Like every op it runs under the text API's lock, so no turn plays while it is under way.

The answer: `ok` (the model answered: `accepted`, or `incomplete` only because the test's own small reply bound
cut it short), `status` (`accepted`, `incomplete`, `failed`, `cancelled`, `refused`, `timeout`), `reason` (013's
error kind, the finish reason or the refusal), `http_status`, `code` and `detail` (the provider's own error, with
the key redacted by the adapter), `sent`, `local`, `model` (the model the provider says answered), `route`,
`seconds` and `limit_seconds`, `attempts` (each `attempt`, `outcome`, `route`, `measurement`, `reason`, `tokens`),
`tokens` (`sums`, `unreported`), `recorded` (a trace was kept) and the cost keys above. The reply's text is never
returned or kept, and the key is never shown, logged or returned.

## The page

`grimoire/app/static/pages/providers/` is the page: `page.json`, `page.css` and `page.js`, which exports the
shell's `mount(root, ctx)` and calls `providers.<op>` and the text API's `chats` through `ctx.api`. From the top:

- A **status card**: "Generating with *model*" or "No model is set up" with the app's reason, whether the endpoint
  is on this computer (no provider bills it) or hosted (every attempt may be billed, with the key's variable
  named), and the routes calls go to. **Send a test call** is offered while one can be sent, with a line under it
  saying it sends one small request to the saved model and that a hosted model may bill it. While the request is
  under way the button is off and reads "Calling…"; then one line gives the model that answered, the time and
  the tokens (and the estimate, when prices are set), or the failure with the provider's own reason. When a
  model answers but may not be tested from here, the card says why and offers no button.
- **Model**: the settings form with **Check** and **Save**. The presets (**OpenRouter**, **Local (Ollama)**,
  **Custom**) fill the provider name, the endpoint address and the key variable in the form and nothing else:
  the model is the person's to name, and a preset saves nothing until **Save**. The form holds no field for a
  key, only for the name of its environment variable. Three price fields (per million prompt, reply and cached
  prompt tokens) are sent as the text typed, and left out when empty.
- **Calls a turn may make**: each stage with its state and its reason.
- **Usage by chat**: the 100 most recent chats (**Show more chats** for up to 1,000;
  `#/providers?chat=<ChatRef>` opens one chat's usage directly), a tile for each total (a lower bound is always
  said to be one) and the chat's turns, 200 at a time, newest first, with **Earlier turns** and **Later turns**.
  Opening a turn lists its calls and attempts. The **Cost** tile reads "Set prices to see it" until both prices
  are saved; then the estimate, labelled "Estimate from your prices"; a lower bound reads "at least" with why;
  and "Unknown" when none of the chat's calls went to the priced model. With prices set, a line under the tiles
  says the costs are estimates at the prices set now, past calls included, and that the provider's bill is what
  counts.
- **Current context**: choose a chat and an NPC, then inspect its current brief, authorized source provenance and budget omissions. **Refresh context** rebuilds from saved state; it neither generates nor retains a prompt. Failed requests and changed selections clear the previous context. The **NPC tactics** form sets one goal and ordered candidate actions for the selected actor on this device.
- **Recent calls on this device** and, folded away, **Not available yet**.

Every library and provider text is set as text, never markup. `backend/tests/test_app_inference_page.py` runs the
page in Node over a stand-in DOM and fake ops (skipped where Node is not installed);
`backend/tests/test_app_inference_shell.py` serves it through the shell, from `serve --model FILE` as a process too.

## Not available yet

Each is listed on the page with its reason (`unavailable` in `models`; the page leaves out `presets`, since it
fills its form from its own):

- **A separate director model.** 027's runtime calls one list of models for every call.
- **Other endpoints.** The app configures one endpoint; fallbacks are other models on it.
- **Prices for fallback models.** The prices are the configured model's; a fallback's attempts are left out of
  the cost, which is then a lower bound.
- **A kept record of test calls.** A test call is traced in memory like every runner call; no file keeps it.
- **Session budgets.** 004 caps calls per turn only.
- **Historical prompt snapshots and draft retrieval preview.** The inspector rebuilds current saved context only. It cannot reconstruct what an earlier call saw or preview an unsaved draft. Each inspected NPC sees its own evidence; withheld evidence and its identifiers/counts are not exposed.
- **Provider presets** kept by the app. The page's own presets only fill its form.


## Runner integration and boundaries

`TextApi` constructs a `DecisionLibrary` on its trusted `app:decisions` handle and passes it to
`SceneRunner`. The additive `agendas=()` constructor input and `SceneRunner.set_agendas()` accept trusted
039 policies; production `providers.configure` loads them from the model settings. No campaign page is changed.
`SceneRunner.inspect_context(chat, actor)` shares generation's actor assembly, returning an explicit safe
view rather than serializing the snapshot or hidden/held secret markers. Source-wide diagnostics are omitted.
It refuses damaged knowledge and player/foreign actors, and works without a provider.

For generation, the runner plans a select phase only for undecided actors with agendas, sends bounded
`GenerationRuntime.decide` calls on the same meter as narration, records choices in the existing decision
ledger, and briefs each NPC with its own choice. Disabling the cap uses the deterministic policy on the next
new turn; a reroll retains its recorded choice. Removing or changing that choice's agenda refuses replay,
so finish/pass the pending turn or restore its policy. Invalid picks fall back to the deterministic choice;
no choice writes world facts or beliefs. Paid attempts remain in usage after a recoverable ledger failure when the store and authority remain writable.
A stale/paused/passed/replaced take cannot record a new decision. The store's existing exclusive process
lease and shared authority/store lock protect the current-turn check and append.

A process crash or unusable storage/authority can leave a running take with planned calls and unknown usage; this does not promise exactly-once
billing. Historical snapshots, general goal editing, probability policies and scene-wide policy comparisons
remain later work. These device-local settings are not synchronized campaign canon.

Executable proofs include `test_app_inference_context.py`, `test_app_inference_decisions.py`,
`test_play_decisions.py` and `test_app_inference_browser.py` (actual CLI, AppServer and Chromium,
with synthetic fixtures and a loopback model). Browser coverage includes keyboard submission, narrow
viewport, restart and failed-request recovery; it is not a physical-phone or screen-reader certification.
