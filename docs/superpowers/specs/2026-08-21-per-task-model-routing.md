# Per-task model routing, with a per-campaign override (#142)

*Closes #142. Closes #143 (two-tier Heavy/Light routing) as the coarser special
case of the same map — the issues say to build one shape, not both.*

## What the issue asked for, and what has moved since

> Per-task model routing with global + per-campaign overrides

#142 was written when `config.md` held a single `model:` field and every call
site read `cfg["model"]`. That field no longer exists. `store/llm_connections.py`
replaced it: a connection is a named `(kind, base_url, api_key, model,
post_process)` profile, `active_connection_id` in `config.md` picks the one
generation uses, and every call site now reads `_require_connection()` and hands
the resulting **connection dict** to `client.complete(...)` / `client.stream(...)`.

So the issue's Option A — `model_scene`, `model_absorb`, … string keys — cannot
be implemented as written: a bare model name says nothing about which provider
serves it, which key pays for it, or which base URL to send it to. A `model_scene`
of `anthropic/claude-opus-4.1` means one thing under an OpenRouter connection and
nothing at all under a Claude-SDK one.

**A route therefore names a connection, not a model.** That is not a new posture:
#144's fallback route made exactly this call, for exactly this reason, and said so
in `config.DEFAULT_FALLBACK_CONNECTION_ID` — *"a connection, not a second model
name, so the fallback may be a different provider entirely; the pre-connections
design could only have meant 'another model on the same account'."* Routing a task
to a connection delivers the model the issue asked for and the provider freedom
#141 is about, in one setting, and it makes the picker a `<select>` over a list
the store already owns instead of a second free-text model field per task.

The cost, stated plainly: running two models on one account means two
connections, and creating the second one means re-entering the key. That is a
real ergonomic tax and the obvious follow-up (a "duplicate connection" action) is
deliberately out of scope here.

## Routes, and the tasks behind them

Every generation in the app already carries a **task** string — it is what
`store.usage.meter(task, …)` files the cost under and what `prompt_log` labels a
frozen prompt with. There are twenty of them today, which is twenty pickers, and
six of those twenty are the same job (a scene turn, a retried scene turn, a
regenerated one, a director turn, a replayed one, a mechanics continuation).

#142's own list is the authority on the granularity it wanted: it named *"Scene
turn — `post_chat` (incl. director turns), `post_retry`, `post_regenerate`"* as
**one** task. So the routing unit is a **route**: a named slot covering one or
more usage tasks. The six routes the issue named are here under its own names;
the four others are call sites that did not exist when it was filed.

| route | label | tasks | campaign override |
|---|---|---|---|
| `scene` | Scene turns | `chat`, `retry`, `regenerate`, `director`, `replay`, `continuation` | yes |
| `opener` | Scene openers | `opener` | yes |
| `absorb` | Absorb & mechanics audit | `absorb`, `audit` | yes |
| `dossier` | Dossier refresh | `dossier` | yes |
| `summary` | Summaries & scene-break checks | `rolling-summary`, `scene-break` | yes |
| `suggestions` | Scene suggestions | `suggestions`, `intent` | yes |
| `voice` | Voice anchors & drift | `voice-anchor`, `voice-drift` | yes |
| `image` | Image descriptions | `image-description` | yes |
| `tagline` | Character taglines | `tagline` | no |
| `scenario` | Scenario drafts | `scenario` | no |

The task→route map is code, because tasks are code. What keeps it honest is a
guard, in the tree's own idiom (`test_lock_domain_guard.py`'s "classify your
module or the test names it"): **`test_routing_guard.py` parses `routes/` for
every `_require_connection(...)` call and fails on one whose task literal no
route claims** — including a call that passes no task at all. A new call site is
one line in `ROUTES`, and forgetting it is a failing test rather than a
silently-unroutable generation.

`tagline` and `scenario` carry no campaign override because their call sites have
no campaign: a tagline is generated against a world character and a scenario
against an uploaded card. `voice` and `image` are mixed — the campaign-scoped
call sites (`POST /campaigns/{cid}/voice-anchor`, a campaign image) honour a
campaign override, the world-scoped ones resolve globally, because there is no
campaign to ask.

## Resolution

`routing.resolve(task, campaign_meta, cfg, exists)` walks, for the task's route:

1. **campaign** — `route_<route>` in `campaign.md` frontmatter (only for a
   campaign-scoped route, and only when the call site knows a `cid`),
2. **global** — `route_<route>` in `config.md`,
3. **active** — `active_connection_id`, i.e. today's behaviour.

`""` at a scope means *no opinion*; the walk continues. There is no "clear"
sentinel — the base of this cascade is the active connection, and "no
connection at all" is not a state a generation can run in, so the tri-state
`response_presets.STYLE_CLEAR` exists for has nothing to express here.

**An id naming a connection that no longer exists is no opinion either, and the
walk continues.** Same rule, same reason as `response_presets.resolve`'s
`styles.exists(value)` check. `delete_connection` already clears every *config*
key that names the deleted connection and gains the ten route keys, but it
cannot reach into every campaign's frontmatter — so a campaign override left
dangling by a delete has to degrade to the next scope rather than fail a turn.

The same rule is what makes a campaign survive travelling: a campaign exported
from one library and imported into another carries its `route_*` keys in
`campaign.md`, and the connection ids they name are that library's, not this
one's. Walking past them lands the campaign on the new library's active
connection, which is the only answer that exists.

A routed connection that exists but **cannot send** (an OpenRouter connection
with no key, a custom endpoint with no base URL) is the opposite case and is
*not* walked past: `_require_connection` answers 409 as it already does for the
active connection, naming the route. `_connection_problem` calls that a setup
mistake rather than a runtime failure, and silently generating on a different
connection than the one the Configuration page says is the failure that posture
exists to prevent. A deleted connection is not a decision; a broken one is.

## Storage

- **Global**: ten `route_*` keys in `config.md` frontmatter, defaulting to `""`.
  Flat string scalars, which is all `store/frontmatter.py` stores.
- **Campaign**: the same key spelling in `campaign.md` frontmatter, written by
  `campaigns.lifecycle.set_campaign_routing` — `set_campaign_response`'s twin,
  including its `updated` stamp.
- **Never in world files.** #142's invariant, unchanged: a world is shared
  between campaigns and a routing choice is not a property of the setting.

`read_config()` narrows to `_CONFIG_KEYS`, so the keys are added there and to the
defaults dict; every existing `config.md` keeps working because a missing key
reads as `""`, which is "inherit".

`store/routing.py` is a **pure leaf**: the registry, the task→route map, the key
spellings, and a `resolve()` over dicts that are handed to it. It imports nothing
from the store, which is what lets `config.py` import it for the key list without
a cycle (`config` → `routing` only). Reading the campaign's frontmatter and
looking a connection up stays in `routes/common.py`, exactly where
`response_presets.resolve`'s callers put the same work.

## Surfaces

- `GET/PUT /api/routing` — global scope.
- `GET/PUT /api/campaigns/{cid}/routing` — campaign scope; a `PUT` naming a
  route that has no campaign override is a 400, not a silently-stored key that
  never fires.

Both return the shape `/response` returns and for the same reason: the picker
has to show what a scope *says* and what actually *resolves*, or "inherit" is an
unanswerable option.

```json
{"routes": {"scene": "openrouter-cheap", "absorb": ""},
 "effective": {"scene": "openrouter-cheap", "absorb": "openrouter"},
 "provenance": {"scene": {"scope": "campaign"}, "absorb": {"scope": "active"}},
 "connections": [{"id": "openrouter", "name": "OpenRouter", "model": "…"}]}
```

Frontend: one `ModelRoutingPicker` component, `scope="global"` in `ConfigView`
beside the connection list, `scope="campaign"` in a `SideSection` of the scene
inspector — which is already where a campaign-scoped knob lives (`CostPanel`
edits the campaign's budget from inside a scene). #73's settings tab is still the
natural long-term home and this moves there unchanged when it lands.

## What this does not do

- **No per-task temperature, provider or base URL.** A route names a connection
  and the connection carries all three.
- **No embedding tier.** #145 owns embeddings, and `embeddings_connection_id`
  already routes them; adding an eleventh route for a call these routes do not
  make would be a key that means nothing.
- **No per-scene scope.** The response cascade has turn/scene/campaign/global;
  this one has campaign/global, which is what #142 asked for. A scene is not
  where a cost decision gets made, and a fourth scope is four more keys in every
  scene's frontmatter for a knob nobody asked for.
- **No fallback per route.** #144's fallback connection stays global: it is the
  answer to "this connection is down", which is a property of the connection
  rather than of the job being sent to it.
- **No one-shot "regenerate with a different model"** (#77). That is a per-turn
  override of the `scene` route and wants a request body, not a stored setting.
- **The scene's stamped `model`.** `scenes.lifecycle.create_scene` records the
  then-active connection's model in the scene's frontmatter. It is informational
  (#142 says so) and stays the *active* connection's model, not the `scene`
  route's: changing it would make the field mean something different in old
  scenes than in new ones, with nothing recording which. Left as a known,
  documented gap rather than a silent one.
