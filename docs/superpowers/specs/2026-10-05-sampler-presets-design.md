# Sampler presets — design

**Status:** accepted for implementation · **Date:** 2026-10-05

## Problem

SillyTavern users tune and share *sampler presets* per model — a JSON file of
temperature, top-p, repetition penalty and the rest. Grimoire sends none of
these today: every request leaves the sampler at the provider's defaults, and
there is nowhere to say "this model wants min-p 0.05 and a 1.08 repetition
penalty" or "absorb should run cold".

## What ships

1. **Named presets** stored as JSON under the data directory.
2. **Attachment** of a preset to a connection or a route, overridable per
   campaign through the same cascade model routing uses.
3. **Honest delivery**: a parameter the serving backend cannot take is dropped,
   and the reader is told which ones and why — in the scene inspector for a
   played turn, and ahead of time on the routing picker and the connection
   editor, which between them cover every route (most routes are never
   captured to the prompt log, so the inspector alone would leave nine of
   eleven of them silent).
4. **SillyTavern import** of a preset file, mapping what maps and listing what
   does not.

## Parameters

Nine, and only nine — the ones the request names:

| param | type | bounds |
|---|---|---|
| `temperature` | float | 0 – 5 |
| `top_p` | float | 0 – 1 |
| `top_k` | int | 0 – 1000 |
| `min_p` | float | 0 – 1 |
| `repetition_penalty` | float | 0 – 3 |
| `frequency_penalty` | float | -2 – 2 |
| `presence_penalty` | float | -2 – 2 |
| `max_tokens` | int | 1 – 200000 |
| `stop` | list[str] | ≤ 16 entries, each 1–200 chars |

A preset **supplies exactly the parameters it sets**. An unset parameter is
absent — not zero, not a default — and the backend's own default applies. That
is `response_presets`' governing rule, for the same reason: defaulting an
unspecified field silently overrides a provider default nobody chose to change.

The bounds are sanity bounds (reject a typo like temperature 70), not a claim
about any one provider's range. Validation is by hand over a bare `dict`, not a
typed pydantic model: pydantic 1.10 (the Android set) coerces `40.7` to an
`int` field where v2 refuses it, and the two builds must agree.

`max_tokens` is the dangerous one, and it is labelled so wherever it is edited:
it caps *every* call that uses the preset, and nothing in the backend reads a
`finish_reason`, so a reply cut off by it lands as though it were complete — a
truncated post, or a JSON extraction that fails to parse. Grimoire's own
response-length controls (`response_targets`, `lengths`) are the tool for
prose length; this one exists for the reader who knows they want a hard cap.

## Storage

`<home>/sampler_presets/<id>.json`, one file per preset, written through
`store.atomic`:

```json
{"name": "Cold extraction", "params": {"temperature": 0.2},
 "notes": "", "source": ""}
```

- `id` is the slug of the name, uniquified (`paths.slugify` / `uniquify`), the
  way connections are.
- `source` records where an import came from (`"sillytavern"`), empty for a
  hand-made preset.
- An unreadable or malformed file reads as *no such preset* — never an
  exception on the generation path.
- No built-ins ship. A shipped preset would be a claim about a model's best
  settings, which is exactly what the community-shared files exist for.

The store is global, not campaign-scoped, like `llm_connections/`; it takes no
campaign lock.

## Attachment and resolution

Four places can name a preset, resolved per **route** (the `store/routing.py`
slots — scene turns, absorb, dossier …):

1. the campaign's `preset_<route>` key in `campaign.md` (campaign-scoped routes
   only — the same `campaign_scoped` flag routing uses);
2. the global `preset_<route>` key in `config.md`;
3. the **serving connection's** `sampler_preset` field;
4. otherwise none: provider defaults.

**A route-level answer (1 or 2) follows the route, whichever connection serves
it** — the routed connection, a per-call override (#77), and the fallback
(#144) alike. Only when no route scope has an opinion does each connection
fall back to its own preset. One rule, and it is the one that keeps the clear
sentinel meaningful: a reader who sets absorb to "no preset" to keep a
role-play preset's token cap off it must not have that undone the moment the
primary rate-limits and the fallback — carrying the same role-play preset —
takes the call.

Each scope may say **"no preset"** with the sentinel `⁣none`
(`sampler_presets.PRESET_CLEAR`, the `response_presets.STYLE_CLEAR` trick, so a
preset literally named "None" cannot collide). It stops the walk.

A scope value naming a preset that no longer exists is *no opinion* and the
walk continues, as `routing._opinion` does for a deleted connection. Deleting a
preset clears the global `preset_*` keys that named it (config.md is one file
and one write) and **nothing else**: rewriting a connection file mints a new
`rev`, which invalidates its cached model catalog and its health verdict, so a
dangling `sampler_preset` on a connection is walked past exactly like a
dangling campaign key.

The cost, stated: ids are slugs, so creating a new preset under a deleted one's
name reuses its id, and the references left behind resolve to the new preset.
That is the same trade `llm_connections` makes for a recreated connection's
campaign routes, and it is visible — every surface that shows a resolved preset
names it.

For the same reason, a connection's `sampler_preset`/`sampler_support` edit is
written **without a new `rev`** and without forgetting its health: neither
field changes what the catalog or the health check describe.

The resolution happens in `routes/common.py` (the impure side, as for routing)
and is attached to the connection dict as `conn["sampling"]` =
`{preset_id, preset_name, scope, params}`. It is attached at
`_standing_connection` (which `get_scene_context` reads), inside
`_override_connection` on the final resolved connection, and on the fallback
connection — the last carries the connection-level answer, and the facade
replaces it with the primary's when the primary's came from a route scope
(`LLMClient._routes`).

## Delivery: the capability table

`llm_sampling.py` (a gateway-side leaf beside `llm.py`, store-free like it)
owns the parameter table and one function:

```
split(conn) -> (applied: dict[wire_name, value], dropped: list[{param, reason}])
```

It reads `conn["kind"]`, `conn["sampler_support"]`, `conn["sampling"]["params"]`
and `conn["model_params"]` (below).

| connection kind | sent | dropped, with reason |
|---|---|---|
| `openrouter` | every set param the model's catalog entry lists in `supported_parameters`; all of them when the catalog is unknown | the rest: "this model does not take it (OpenRouter catalog)" |
| `openai_compatible`, standard (default) | temperature, top_p, frequency_penalty, presence_penalty, max_tokens, stop (≤ 4 strings) | top_k, min_p, repetition_penalty: "not in the OpenAI API — enable extended samplers for a server that takes them"; a stop list over 4: "the OpenAI API takes at most 4 stop strings" |
| `openai_compatible`, extended | all nine; `repetition_penalty` is sent under **both** `repetition_penalty` (vLLM, TabbyAPI, koboldcpp) and `repeat_penalty` (llama.cpp, LM Studio) | none |
| `claude` (Agent SDK) | nothing | every set param: "the Claude Agent SDK takes no sampling options" |

- **OpenRouter** forwards a parameter a model does not support and the model
  ignores it — which is the silent case the request rules out. OpenRouter's
  catalog says which parameters each model takes (`supported_parameters`), so
  `catalog.entry` keeps it (as `params`, only when the provider sent a list),
  and `routes/common` attaches the cached list for the connection's effective
  model as `conn["model_params"]`. With no cached catalog the split cannot
  know, sends everything, and the report says "unverified — refresh this
  connection's model list to check".
- **OpenAI-compatible** endpoints are whatever `base_url` points at. The
  OpenAI spec has six of the nine; a strict endpoint answers an unknown field
  with a 400 (the reason `openai_compatible` already declines to send
  `stream_options`). Local servers take the other three and are where
  SillyTavern presets matter most, so a connection opts in with
  `sampler_support: extended`. Extended mode is for lenient servers by
  definition, which is what makes sending both repetition-penalty spellings
  safe. A known limit, stated: OpenAI's own reasoning models refuse
  `max_tokens` (they want `max_completion_tokens`) and non-default
  temperature; such a refusal is the 400 described below, not a silent drop.
- **Claude (Agent SDK)**: `ClaudeAgentOptions` has no sampling fields. Its
  `env` could carry `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, but that caps a Claude Code
  run's output including thinking — not the same knob — so it is dropped by
  choice rather than mapped approximately.

`LLMClient._dispatch` calls `split` per attempt with that attempt's own
connection, so a fallback of a different kind is split for its kind. The
adapters receive only `applied` and merge it into their request body.

### A refusal caused by the preset does not fall back

`_resilient` hands any non-retryable failure to the fallback. For a preset that
is wrong: a provider answering **400/422** to a request that carried sampler
parameters is most likely refusing one of them, and serving the turn from the
fallback would hide that — every turn silently on the fallback, the primary's
health dot red over a setting rather than the connection. So adapters record
the HTTP status on `LLMError.status`, and an attempt that sent parameters and
failed 400/422 is raised immediately, its detail naming the preset and the
parameters sent, without falling back and without a health verdict against the
connection.

### Saying so

The report shape, everywhere it appears:

```
{"preset_id", "preset_name", "scope", "kind",
 "applied": {param: value}, "dropped": [{"param", "reason"}], "verified": bool}
```

- **Scene inspector, live**: `GET .../context` adds `sampling` for the next
  ordinary scene turn.
- **Scene inspector, frozen**: every prompt snapshot records the report for the
  attempt it captured. A distinct-model fallback snapshot (`on_variant`)
  records the fallback connection's report, resolved under the rule above.
  A same-model fallback produces no variant snapshot today
  (`PreparedMessages.for_model`), so its report is not captured — a stated
  limit of the existing capture, not something this adds.
- **Routing picker** (global and campaign): each route row shows, beside the
  preset select, what that route's effective connection and preset will send
  and drop. This is the surface that covers absorb, dossiers, the tracker and
  every other route the prompt log never sees.
- **Connection editor**: the view sidebar shows the connection's own preset
  split for this connection.
- A debug log line names dropped parameters per attempt.

## SillyTavern import

`POST /api/sampler-presets/import` takes `{name, data, include_max_tokens}` —
the file is read in the browser and sent as JSON, which keeps the route off
multipart. Both of SillyTavern's preset families are read: Chat Completion
presets (`temperature`, `openai_max_tokens`, …) and Text Completion / KoboldAI
presets (`temp`, `rep_pen`, `genamt`, …).

| grimoire | SillyTavern keys, first present wins |
|---|---|
| temperature | `temperature`, `temp` |
| top_p | `top_p` |
| top_k | `top_k` |
| min_p | `min_p` |
| repetition_penalty | `repetition_penalty`, `rep_pen` |
| frequency_penalty | `frequency_penalty`, `freq_pen` |
| presence_penalty | `presence_penalty`, `presence_pen` |
| max_tokens | `openai_max_tokens`, `max_tokens`, `genamt`, `amount_gen` |
| stop | `stop`, `stopping_strings`, `custom_stopping_strings` (a list, or a JSON-encoded list as ST stores it) |

The response is the saved preset plus a report of five lists:

- `mapped` — `{param, from, value}` for each param set;
- `neutral` — mapped keys at the sampler's off position (`top_k: 0`,
  `min_p: 0`, `repetition_penalty: 1`, the two penalties at 0, `top_p: 1`, an
  empty stop list), left **unset**: ST writes every field into every file, and
  storing its off-positions would make every imported preset report `top_k`
  dropped on a standard endpoint for a value that does nothing;
- `skipped` — `max_tokens` unless `include_max_tokens` was asked for. ST writes
  its response length (300 by default) into every chat preset, and imported
  silently it would cap absorb, dossiers and every other call the preset
  reaches (see Parameters). The import form offers the checkbox, unticked;
- `invalid` — `{key, why}`: out of bounds, wrong type, or a stop string
  containing a `{{…}}` macro (ST expands those; stored as text they would never
  match);
- `unmapped` — every other top-level key, sorted. `temperature_last`,
  `sampler_order` and `sampler_priority` are listed first with a note that the
  order samplers run in is not carried over, since it changes what a min-p
  value means.

A file that is not a JSON object is a 400. A file that maps nothing still saves
(an empty preset is valid) and the report says so.

## API

| method | path | |
|---|---|---|
| GET | `/api/sampler-presets` | `{presets: [{id, name, params, source, notes}], params: <table>}` |
| POST | `/api/sampler-presets` | create `{name, params, notes}` |
| GET/PUT/DELETE | `/api/sampler-presets/{id}` | read / replace / delete |
| POST | `/api/sampler-presets/import` | SillyTavern JSON → preset + report |

Route attachment rides the existing routing endpoints: the `GET /routing` and
`GET /campaigns/{cid}/routing` bundles gain `presets`, `preset_inherited`,
`preset_inherited_from`, `preset_catalog` (id, name) and `sampling` (the
per-route report), and the PUTs accept a `presets: {route: id}` map next to
`routes`. Both `config._CONFIG_KEYS` and `set_campaign_routing`'s allowed set
gain the `preset_*` keys — without them the keys are silently dropped on read
and write. `ConnectionCreate`/`Update` gain `sampler_preset` and
`sampler_support`; the connection detail read gains `sampling`.

A preset id is checked to exist **in the routes**, never in `llm_connections`
(which `sampler_presets` imports — the other direction would be a cycle).

## UI

- **Sampler presets** — a Settings section ("What the model sees") built on the
  list/detail pattern: a rail with `+ New preset` and `Import from
  SillyTavern…`, a read-only view with an **Edit** button, and a form with one
  field per parameter (blank = unset). Import shows the report's lists.
- **Connection editor** — "Sampler preset" select and, for `openai_compatible`,
  an "Extended samplers" checkbox; the view sidebar shows the split.
- **Model routing picker** — a preset select per route row (inherit / no preset
  / each preset), the inherited label naming what inheriting gets, and the
  sent/dropped line.
- **Scene inspector** — a Sampler block in the context breakdown, live and
  frozen.

## Out of scope

- Parameters beyond the nine (top-a, typical-p, mirostat, DRY, XTC, logit bias,
  seed) and sampler order. The import lists them.
- Reading `finish_reason` to flag a reply cut off by `max_tokens`.
- A per-turn sampler override in the composer.
- Splitting the response-selector (a JSON call inside the `scene` route) into a
  route of its own; it shares the scene route's preset.
- Exporting to SillyTavern format.

## Testing

- `llm_sampling.split` per kind, extended (both repetition spellings), stop
  over 4, OpenRouter with and without a catalog list, Claude.
- Adapters merge `applied`; `_dispatch` splits per attempt; fallback inherits a
  route-scope preset and keeps its own otherwise; a 400 with sampling does not
  fall back and is not observed as a connection failure; a 400 without sampling
  still falls back.
- Store: CRUD, malformed file reads as missing, delete clears config keys only.
- Connections: a sampler-only update keeps `rev` and the catalog sidecar.
- Resolution cascade, clear sentinel, dangling ids, non-campaign-scoped routes.
- Import: both families, neutral values, `max_tokens` skipped and included,
  JSON-string stop list, macro stop strings, non-object rejected.
- Routes: CRUD, import, routing bundle + PUT, context block, snapshot block,
  and an end-to-end chat turn whose provider receives the applied params.
- Frontend: editor list/detail tests per CLAUDE.md, import report, routing
  picker select + report, connection editor, inspector block.
