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
   and the scene inspector says which ones and why.
4. **SillyTavern import** of a preset file, mapping what maps and listing what
   does not.

## Parameters

Nine, and only nine — the ones the request names, which are also the ones every
mainstream chat-completions backend speaks some subset of:

| param | type | bounds | wire name |
|---|---|---|---|
| `temperature` | float | 0 – 5 | `temperature` |
| `top_p` | float | 0 – 1 | `top_p` |
| `top_k` | int | 0 – 1000 | `top_k` |
| `min_p` | float | 0 – 1 | `min_p` |
| `repetition_penalty` | float | 0 – 3 | `repetition_penalty` |
| `frequency_penalty` | float | -2 – 2 | `frequency_penalty` |
| `presence_penalty` | float | -2 – 2 | `presence_penalty` |
| `max_tokens` | int | 1 – 200000 | `max_tokens` |
| `stop` | list[str] | ≤ 16 entries, each 1–200 chars | `stop` |

A preset **supplies exactly the parameters it sets**. An unset parameter is
absent — not zero, not a default — and the backend's own default applies. That
is `response_presets`' governing rule, for the same reason: defaulting an
unspecified field silently overrides a provider default nobody chose to change.

The bounds are sanity bounds (reject a typo like temperature 70), not a claim
about any one provider's accepted range; a provider that rejects a value inside
them returns its own 400, which surfaces as the ordinary `bad_response`.

## Storage

`<home>/sampler_presets/<id>.json`, one file per preset, written through
`store.atomic`:

```json
{"name": "Cold extraction", "params": {"temperature": 0.2, "max_tokens": 4000},
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
campaign lock. Writes are whole-file atomic replaces.

## Attachment and resolution

Three places can name a preset, and resolution is a cascade per **route** (the
`store/routing.py` slots — scene turns, absorb, dossier …):

1. the campaign's `preset_<route>` key in `campaign.md` (campaign-scoped routes
   only — the same `campaign_scoped` flag routing uses);
2. the global `preset_<route>` key in `config.md`;
3. the **serving connection's** `sampler_preset` field;
4. otherwise none: provider defaults.

The cascade is independent of the connection cascade, mirroring how model
routing is overridable per campaign: a campaign can retune a route without
repointing it, and vice versa.

Each scope may also say **"no preset"** with the sentinel `⁣none`
(`samplers.PRESET_CLEAR`, the `response_presets.STYLE_CLEAR` trick, so a preset
literally named "None" cannot collide with it). It stops the walk: this is how
a reader keeps a connection-level role-play preset (with a 300-token
`max_tokens`) off the absorb route, whose JSON reply would be truncated by it.

A scope value naming a preset that no longer exists is *no opinion* and the
walk continues, as `routing._opinion` does for a deleted connection — a delete
cannot reach into every campaign's frontmatter. Deleting a preset clears the
references it *can* reach: the global `preset_*` keys and every connection's
`sampler_preset`.

**The fallback connection** (#144) uses its own `sampler_preset` only. It is
resolved without a task, and a route preset is tuned for the route's own model
— the SillyTavern convention is one preset per model — so carrying it onto a
different model on a different provider is a guess the user did not make.

**A per-call override** (a reroll on another connection, #77) keeps the route
cascade: the route's preset still applies, and when no scope sets one, the
*override* connection's own preset is the base.

## Delivery: the capability table

`llm_sampling.py` (a gateway-side leaf beside `llm.py`, store-free like it)
owns the parameter table and one function:

```
split(kind, params, extended) -> (applied: dict, dropped: list[str])
```

| connection kind | applied | dropped |
|---|---|---|
| `openrouter` | all nine | none |
| `openai_compatible` (default) | temperature, top_p, frequency_penalty, presence_penalty, max_tokens, stop | top_k, min_p, repetition_penalty |
| `openai_compatible`, `sampler_support: extended` | all nine | none |
| `claude` (Agent SDK) | none | all set params |

- **OpenRouter** documents all nine and itself ignores one a given model does
  not support.
- **OpenAI-compatible** endpoints are whatever the user pointed `base_url` at.
  The OpenAI spec has six of the nine; a strict endpoint answers an unknown
  field with a 400 (the reason `openai_compatible` already declines to send
  `stream_options`). Local servers — llama.cpp, vLLM, LM Studio, koboldcpp,
  TabbyAPI — accept the other three, and they are where SillyTavern presets
  matter most, so a connection opts in with a new `sampler_support` field
  (`"standard"` / `"extended"`, default standard).
- **Claude (Agent SDK)** exposes no sampling options at all
  (`ClaudeAgentOptions` has none), so everything a preset sets is dropped.

`LLMClient._dispatch` calls `split` per attempt with the attempt's own
connection, so a fallback on a different kind is split for that kind. The
adapters receive only `applied` and merge it into their request body; they do
not know presets exist.

### Saying so in the inspector

The connection dict a route resolves carries a `sampling` block —
`{preset_id, preset_name, scope, params}` — and the report shown to the reader
is `split` over it for that connection:

```
{"preset_id", "preset_name", "scope", "applied": {...}, "dropped": [...], "kind"}
```

- `GET /campaigns/{cid}/scenes/{sid}/context` (the inspector's live view) adds
  `sampling` for the next ordinary scene turn.
- Every frozen prompt snapshot (`store.prompt_log`) records the same block for
  the attempt it captured, so a past turn says what it was sent with. A
  distinct-fallback snapshot records the fallback's own report.
- The inspector renders it as a "Sampler" line under the model: preset name and
  source scope, the applied values as chips, and — when any — "Not supported by
  this connection, not sent: top_k, min_p" with the reason per kind.

A debug log line names dropped parameters per attempt as well; the inspector is
the surface the reader is expected to look at.

## SillyTavern import

`POST /api/sampler-presets/import` takes the JSON file and an optional name
(default: the file name). Both of SillyTavern's preset families are read:
Chat Completion presets (`temperature`, `openai_max_tokens`, …) and Text
Completion / KoboldAI presets (`temp`, `rep_pen`, `genamt`, …).

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

The response is the saved preset plus a report:

- `mapped` — the grimoire params that were set, and from which ST key;
- `neutral` — mapped keys whose value is the sampler's off position
  (`top_k: 0`, `min_p: 0`, `repetition_penalty: 1`, the two penalties at 0,
  `top_p: 1`, an empty stop list, `max_tokens: 0`). These are left **unset**
  rather than stored: ST writes every field into every file, and storing its
  off-positions would make every imported preset report `top_k` "dropped" on a
  standard endpoint for a value that does nothing;
- `invalid` — mapped keys whose value is out of bounds or the wrong type, left
  unset;
- `unmapped` — every other top-level key, sorted (`top_a`, `typical_p`,
  `mirostat_mode`, `prompts`, …).

The import UI shows all four lists after saving. A file that is not a JSON
object is a 400; a file that maps nothing still saves (an empty preset is
valid) and says so.

## API

| method | path | |
|---|---|---|
| GET | `/api/sampler-presets` | list `{id, name, params, source, notes}` + the param table (labels, bounds, per-kind support) |
| POST | `/api/sampler-presets` | create `{name, params, notes}` |
| GET/PUT/DELETE | `/api/sampler-presets/{id}` | read / replace / delete |
| POST | `/api/sampler-presets/import` | SillyTavern JSON → preset + report |

Route attachment rides the existing routing endpoints: `GET /routing` and
`GET /campaigns/{cid}/routing` bundles gain `presets` / `preset_effective` /
`preset_inherited` maps beside the connection ones, and the PUTs accept a
`presets: {route: id}` map next to `routes`. `ConnectionCreate`/`Update` gain
`sampler_preset` and `sampler_support`.

Validation: params outside the table or bounds are a 400 naming the param; a
route the scope cannot set, or an id naming no preset, is a 400 (the routing
PUT's rules).

## UI

- **Sampler presets** — a section in Settings (Configuration page) built on the
  list/detail pattern: a rail of presets with `+ New preset` and `Import from
  SillyTavern…`, a read-only view (params as a table, source, notes) with an
  **Edit** button, and a form with one field per parameter (blank = unset).
- **Connection editor** — a "Sampler preset" select and, for
  `openai_compatible`, an "Endpoint accepts top-k, min-p and repetition
  penalty" checkbox.
- **Model routing picker** (global and per-campaign) — a second select per
  route row: inherit / no preset / each preset, with the inherited label naming
  what inheriting currently gets, as the connection select does.
- **Scene inspector** — the Sampler line described above, in the live
  composition and in a frozen turn.

## Out of scope

- Parameters beyond the nine (top-a, typical-p, mirostat, DRY, XTC, logit bias,
  seed). The import lists them as unmapped; adding one later is a row in the
  table plus a per-kind support decision.
- A per-turn sampler override in the composer.
- Per-model (rather than per-connection) attachment: a connection already names
  one model, so a connection preset *is* a model preset.
- Exporting a preset back to SillyTavern format.

## Testing

- `llm_sampling.split` per kind, including extended and an empty preset.
- Each adapter merges `applied` into its request body (openrouter,
  openai_compatible) and the Claude path sends nothing.
- `_dispatch` splits per attempt: a fallback of a different kind gets its own
  split.
- Store: create/read/update/delete, malformed file reads as missing, delete
  sweeps config and connection references.
- Resolution cascade: campaign → global → connection → none, the clear
  sentinel, dangling ids walking past, non-campaign-scoped routes ignoring a
  campaign key, fallback using its own connection preset.
- Import: chat-completion and text-completion fixtures, neutral values left
  unset, JSON-string stop list, invalid values reported, non-object rejected.
- Routes: CRUD, routing bundle/PUT with presets, context `sampling` block,
  snapshot `sampling` block.
- Frontend: preset editor list/detail tests per CLAUDE.md, import report,
  routing picker preset select, inspector sampler line with dropped params.
