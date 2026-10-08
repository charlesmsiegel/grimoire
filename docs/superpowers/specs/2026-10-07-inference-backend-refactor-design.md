# Inference backend refactor: providers, roles, presets, capabilities, and `generate` / `decide` / `embed`

**Status:** approved design, ready for per-slice planning
**Date:** 2026-10-07
**Baseline:** `7f80c42` (no open PRs touched the inference layer when this was written)
**Input:** the planning draft "01. Inference backend refactor" (2026-10-06). This
document supersedes it; where they disagree, this one is the decision.
**Gates:** the spec → planning Codex adversarial review was **waived by the
user** for this spec. Every per-slice gate in CLAUDE.md still applies (§14).

---

## 0. Read this before changing code

Every slice begins by re-reading the then-current code and tests for the
subsystem it touches. The inventory in §2 is a snapshot at the baseline; a
slice plan that finds it stale updates the plan, not this spec, unless a
decision below is actually invalidated.

Five rules recur through every section. If a change would break one, stop and
raise it rather than working around it:

1. **Nothing spends money without asking first.** Any paid call started from a
   *settings* surface — a capability test, a generating health probe, a change
   that will cause re-embedding — states what it will send and what it may cost
   and waits for confirmation. Play is not gated: sending a turn *is* the
   request.
2. **One resolver.** Exactly one function answers "what will serve this
   inference?" The wire request and every screen that describes it are built
   from its output. No call site and no frontend component reassembles
   provider/model/preset/capability configuration on its own.
3. **Migration is behaviour-neutral.** After the upgrade every task resolves to
   the same provider, model, sampling and fallback it did before, until the
   user changes something.
4. **Embeddings never mix spaces.** No fallback on any axis for `embed`.
5. **A price nobody reported is never rendered as zero** (CLAUDE.md, Costs).

---

## 1. What the user sees, and why

The user-facing goal is a setup flow in three moves:

1. **Add providers** — an API key (or a local endpoint, or the Claude
   subscription) per service.
2. **Pick a model for each role** — Primary, Fast, Decision, Embedding — by
   choosing a provider, then a model *from the list of that provider's models
   capable of filling that role*, then a preset.
3. **Optionally re-point individual tasks** (the existing per-task routing, now
   pointing at roles by default).

Concretely: OpenRouter's Embedding list contains embedding models and not GLM;
a z.ai provider's Embedding list is empty (with a note saying z.ai serves no
embeddings) while its Primary and Fast lists are full. The same GLM 5.3 model
can be Primary with high reasoning and a large token cap and Fast with low
reasoning and a small one, because a role is **provider + model + preset**.

Underneath, the refactor separates concepts that one "connection" record
currently conflates — transport/credentials, model identity, per-model facts,
sampling/reasoning choices, task routing and operation type — and gives
Grimoire three explicit operations: `generate`, `decide`, `embed`.

---

## 2. Where the code is today (baseline inventory)

Recorded so each slice plan starts from facts, and so what "behaviour-neutral"
means is checkable.

**Connections** (`store/llm_connections.py`): `<home>/llm_connections/<id>.md`
with fields `kind` (`openrouter` | `claude` | `openai_compatible`), `name`,
`base_url`, `api_key`, `model`, `post_process` (`none` | `strict`),
`reasoning_effort`, `sampler_preset`, `sampler_support`, `vision`
(`""`/`on`/`off`), `prefill`, and an opaque `rev`. `rev` is restamped on every
write except edits confined to `REV_NEUTRAL_FIELDS` (sampler pair, `vision`,
`prefill`); it gates the catalog sidecar `<id>.models.json` and the health
verdict, and is part of the embedding space key. `<id>.regex.json` holds the
connection-level output rules. Repointing `base_url` drops the key unless a new
one is supplied in the same call.

**Routing** (`store/routing.py`, pure leaf): 12 routes, each a named set of
tasks with a `campaign_scoped` flag. Keys `route_<route>` (a connection id) and
`preset_<route>` (a sampler preset id or `PRESET_CLEAR`) at global
(`config.md`) and campaign (`campaign.md`) scope. Resolution: campaign → global
→ the active connection (`active_connection_id`). A reference to a connection
that no longer exists reads as "no opinion"; a routed connection that exists
but cannot send is reported (409), never walked past.

**The seam** (`routes/common.py`): `_require_connection(task, cid)` →
`_standing_connection` → `_routed_connection` + `_attach_sampling`, refusals in
`_usable_or_409`. `_override_connection(body, task, cid)` serves the per-call
reroll override (#77: `connection_id` and/or `model`). `_soft_connection` turns
a 409 into `(None, reason)` for absorb's secondary phases. The returned
*connection dict with `sampling` attached* is, in practice, today's resolved
object: `LLMClient.stream/complete` (`llm.py`) consume it.

**The facade** (`llm.py`): retries (`llm_retries`, Retry-After honoured,
capped), idle timeout and heartbeats, one global fallback connection
(`fallback_connection_id`, one attempt, route-scoped sampling follows it via
`fallback_sampling`), text-only fallback filtering for image-bearing messages,
a degrade-to-text sibling route for post images, per-attempt prompt selection
(`model_guidance.PreparedMessages.for_connection`), provider health
(`health.py`), usage stamping. Adapters: `openrouter.py`,
`openai_compatible.py`, `claude_agent.py`.

**Sampling** (`llm_sampling.py`, `store/sampler_presets.py`): presets in
`<home>/sampler_presets/<id>.json` with nine params (`temperature`, `top_p`,
`top_k`, `min_p`, `repetition_penalty`, `frequency_penalty`,
`presence_penalty`, `max_tokens`, `stop`). Cascade: campaign route → global
route → the serving connection's own preset → none. `llm_sampling.split`
already classifies each param as sent / dropped / unverified with reasons.
Reasoning effort is only sent to `openai_compatible` GLM models
(`llm_reasoning.glm_effort`).

**Vision**: post images (`store/post_images.capability`: the connection's
override, else the cached catalog; `claude` always no) and image-description
drafts (`store/image_drafts.SUPPORTED_KINDS`, refused with 409 otherwise).

**Structured output**: none. No call sends `response_format` or a JSON schema;
every JSON answer is extracted from free text.

**Embeddings** (`embeddings.py`, `store/embed_space.py`): synchronous
`EmbeddingsClient` (batch 64, 30 s deadline), configured by
`embeddings_connection_id` + `embeddings_model` in `config.md`, restricted to
`openai_compatible` connections. Space id: `f"{conn_id}\0{rev}\0{model}"`.
Vector cache in `<home>/.cache/embeddings/`. Callers: lore recall
(`context/semantic.py`), art catalogue (`context/art.py`), semantic search
(`semsearch.py`), continuity similarity (`continuity/similarity.py`). Embed
calls are **not metered and not routed**. Every caller degrades to
keyword/basic matching when embeddings are unavailable.

**Decide-shaped calls today** (all generate + free-text JSON parse):
continuity-identity (batched rows, `existing|new|uncertain`),
continuity-reconcile (per-candidate vocabularies), scene-break
(`{break, reason, title}`), voice-drift (`drift|in_voice|not_enough|unknown` +
note), response-selector (`{"next": ref|null}`).

**Pricing** (`store/pricing.py`): `<home>/pricing.json`, keyed by *model id*
with wildcards and a `""` default; drives `modelled_usd`. `cost_usd` when the
provider reports a price; `estimated_usd` when it reports a subscription
equivalent (`claude_agent`, `cost_basis: "equivalent"`).

**Frontend**: `routes/ConfigView.tsx` (Connection, Model routing, Embeddings
sections), `/connections` (`ConnectionEditor`, `ConnectionForm`),
`ModelRoutingPicker` (global and campaign scope, the latter in
`SceneInspector`), `SamplerPresetEditor`, `SamplingSummary`, `RerollRoute`,
`ModelCombobox`, `SetupWizard` (Storage → Model → Look → World),
`embeddingsOn.ts` (a frontend mirror of `embed_space.resolve` — to be deleted,
see rule 2).

**Guards that pin this**: `test_routing_guard.py`, `test_usage_guard.py`,
`test_import_guard.py`, `test_regex_prompt_guard.py`; plus the behaviour suites
`test_routing*.py`, `test_llm*.py`, `test_llm_connections_store.py`,
`test_sampler_presets_*.py`, `test_llm_sampling.py`, `test_model_catalog.py`,
`test_provider_health.py`, `test_post_images_*.py`,
`test_image_description_*.py`, `test_draft_runs.py`, `test_embeddings.py`,
`test_vectors.py`, `test_context_semantic.py`, `test_semsearch_store.py`,
`test_continuity_similarity.py`, and their frontend counterparts.

---

## 3. Concepts

```text
Provider ─┐
          ├─ Selection = provider + model + preset ──► Role (Primary/Fast/Decision/Embedding)
Model ────┤                                         └► explicit route pin
Preset ───┘
Model facts = what is true of (provider, model)

Task ─► Route (15) ─► role (default) or explicit selection ─► ResolvedInference ─► adapter
                                     operation: generate | decide | embed
```

| Concept | Answers | Lives in |
|---|---|---|
| **Provider** | How do I reach this service, with what credential, billed how? | `llm_connections/<id>.md` |
| **Model facts** | What is true of *this model on this provider*? | `llm_connections/<id>.facts.json` |
| **Preset** | How should an inference run? (sampling, reasoning, caps) | `sampler_presets/<id>.json` |
| **Selection** | provider + model + preset | flat keys (§4.5) |
| **Role** | The default selection for a kind of work | `config.md`, `campaign.md` |
| **Route** | Which tasks share a routing choice; their operation; their default role | `store/routing.py` (code) + `use_*` keys |
| **Operation** | `generate`, `decide`, `embed` | `inference` API |
| **Capability** | Can this selection perform this operation / take this input? | resolved, with provenance |

The planning draft's standalone **Model Profile** does not exist in this
design. What it carried is split by nature: model-intrinsic facts go to
**model facts**; how-to-run choices go to the **preset**; the binding of a
model to a use is the **selection** on a role or route. Two roles on the same
provider and model with different presets are therefore the normal case, not a
duplication.

**Vision is a capability, not a role.** A route that needs images declares
`requires: vision`.

---

## 4. Data model and storage

Everything stays file-backed and human-readable. Directory names do not change
(no folder renames under a sync client). All writes go through `store.atomic`.

```text
<home>/
  config.md                          roles, role fallbacks, route choices, inference_format
  llm_connections/<id>.md            Provider
  llm_connections/<id>.models.json   catalog cache — rev-gated, disposable
  llm_connections/<id>.facts.json    Model facts — durable (NEW)
  llm_connections/<id>.regex.json    provider-level output rules (unchanged)
  sampler_presets/<id>.json          Preset
  pricing.json                       global wildcard rates (unchanged; fallback layer)
<campaign>/campaign.md               campaign role and route overrides
```

### 4.1 Provider

Today's connection record minus model behaviour. Fields written by this
version:

| Field | Meaning |
|---|---|
| `kind` | adapter: `openrouter` \| `claude` \| `openai_compatible` \| `anthropic` (new) |
| `preset` | which provider preset (§6.1) it was created from |
| `name` | display name |
| `base_url` | endpoint (fixed for some presets) |
| `api_key` | credential; masked in every response as `key_set` |
| `billing` | `metered` \| `subscription` |
| `sampler_support` | the "extended samplers" switch — a property of the server, not of one model |
| `rev` | opaque; restamped when `kind`, `base_url` or `api_key` change |

The legacy fields `model`, `post_process`, `reasoning_effort`,
`sampler_preset`, `vision`, `prefill` are **left in place, frozen** for older
builds (§11) and ignored by this version once the store is at format 2.

`rev` rules are unchanged in spirit: edits to `name`, `preset`, `billing`,
`sampler_support` keep it; edits that could change the deployment behind the
provider (kind, URL, key) restamp it. The "repointing `base_url` drops the key"
protection is kept.

### 4.2 Model facts

`facts.json` is keyed by model id and holds only what is true of that model on
that provider:

```json
{
  "glm-5.3": {
    "vision": "",
    "prefill": false,
    "post_process": "none",
    "rates": {"prompt_usd_per_1k": 0.0006, "completion_usd_per_1k": 0.0022},
    "verified": {
      "rev": "a1b2c3d4e5f60718",
      "caps": {
        "generate": {"ok": true, "at": "2026-10-07T12:00:00Z"},
        "embed":    {"ok": false, "at": "2026-10-07T12:00:00Z",
                     "error": "404 model does not support embeddings"}
      }
    },
    "overrides": {"generate": "", "embed": "", "decide_native": "", "structured_output": ""}
  }
}
```

- `vision`: `""` (auto) / `on` / `off` — today's per-connection override, now
  per model.
- `prefill`, `post_process`: as today, per model.
- `rates`: per-token rates, same field names and units as `pricing.json`
  (§9).
- `verified`: test-call results (§6.4). The results carry one stamp, the
  provider `rev` they were made under; results whose `rev` differs from the
  provider's current one read as absent ("unverified"). A test made under a
  new `rev` replaces the old results rather than merging with them.
- `overrides`: an explicit user assertion per capability (`""` / `yes` /
  `no`), for endpoints whose discovery says nothing and where the user does
  not want to spend on a test call. Bounded by adapter facts (§6.2).

A missing entry means "nothing known beyond discovery". The file is read
defensively (a hand-mangled file reads as empty, never raises), like the
catalog sidecar.

### 4.3 Preset

Today's sampler preset, plus one parameter:

| Param | Values |
|---|---|
| the nine existing params | unchanged (`llm_sampling.PARAMS`) |
| `reasoning_effort` | `off` \| `low` \| `medium` \| `high` (provider-neutral) |

A preset is reusable across models. A value a given model cannot take stays
stored and is reported as unsupported for that model (§8), never deleted. The
UI calls these **Presets**; the directory and module keep their names.

### 4.4 Roles

Four global roles, each a selection:

- `role_primary_{provider,model,preset}`
- `role_fast_{provider,model,preset}`
- `role_decision_{provider,model,preset}`
- `role_embedding_{provider,model}` (embedding takes no preset)

Primary, Fast and Decision each also carry an optional fallback selection:
`role_<r>_fallback_{provider,model,preset}`. Embedding has none (§7.3).

Unset roles inherit: Fast → Primary; Decision → Fast → Primary; Embedding →
**off** (semantic layers degrade as they do today). An unset Primary is today's
409 `missing_key`.

Campaigns may set `role_primary_*`, `role_fast_*`, `role_decision_*` and their
fallbacks in `campaign.md`. **The Embedding role is global only**: a
per-campaign vector space would split the shared vector cache and the
cross-campaign search surfaces.

### 4.5 Routes and route choices

`store/routing.py` stays a pure leaf. Each route gains:

- `operation`: `generate` | `decide`
- `default_role`: `primary` | `fast` | `decision`
- `requires`: a tuple of input capabilities (`("vision",)` on `image`)

The 12 routes become **15** — three routes that mixed operations are split so
every route has exactly one operation:

| Route | Tasks | Operation | Default role (final) |
|---|---|---|---|
| `scene` | chat, retry, regenerate, extend, director, replay, continuation | generate | primary |
| `speaker` (new) | response-selector | decide | decision |
| `opener` | opener | generate | primary |
| `absorb` | absorb, audit | generate | fast |
| `dossier` | dossier | generate | fast |
| `continuity` | continuity-identity, continuity-reconcile | decide | decision |
| `summary` | rolling-summary, scene-break-title | generate | fast |
| `scene_break` (new) | scene-break | decide | decision |
| `tracker` | tracker-update | generate | fast |
| `suggestions` | suggestions, intent, character-from-passage | generate | primary |
| `voice` | voice-anchor | generate | primary |
| `voice_drift` (new) | voice-drift | decide | decision |
| `image` | image-description | generate (requires vision) | fast |
| `tagline` | tagline | generate | fast |
| `scenario` | scenario | generate | fast |

`tagline` and `scenario` stay `campaign_scoped = False`. Route keys are
spelled with underscores (`speaker`, `scene_break`, `voice_drift`) because
they become frontmatter keys; task names keep their hyphens. The table shows
each route's **final** values: until its tasks are actually converted to
`decide()`, a decide route carries `operation = generate` and `default_role =
fast`, and slices F and G flip both together (§14's safety rule).

`scene-break-title` is the prose half split out of today's scene-break call
(§7.4) and is registered in slice F, when that split happens. The new decide
routes carry only the bounded question.

Route choices, at both scopes (new key names — the legacy `route_*` keys stay
frozen for older builds):

- `use_<route>`: `primary` | `fast` | `decision` | `model` | `""` (no opinion)
- when `model`: `use_<route>_provider`, `use_<route>_model`,
  `use_<route>_preset` — an explicit pin is a full selection
- `preset_<route>`: unchanged meaning and key (an older build reads it
  correctly)

Whether a role's current selection can serve a route is checked at
resolution (§5.3), not when the route is written, because roles change after
routes are set. The one write-time refusal is `use_<route>=embedding` (400):
Embedding is not a generate/decide role.

### 4.6 Format marker

`config.md` gains `inference_format`. Absent means 1 (the baseline layout);
this version writes `2` as the last step of migration (§11). Each migrated
`campaign.md` carries its own `inference_format: 2`.

---

## 5. Resolution

### 5.1 Selection cascade

For a task:

The agreed order is **campaign route → campaign role → global route → global
role**. For a task:

1. `route = routing.route(task)`. An unknown task resolves as today, to the
   Primary role; the guard test keeps new call sites from getting here.
2. **Campaign route.** If the route is campaign-scoped and the campaign's
   `use_<route>` has an opinion: a pin is the answer; a role `R` is resolved
   campaign `role_R_*` → global `role_R_*` → inheritance (§4.4). Done.
3. **Campaign role.** Let `R` be the role the route uses *at global scope*:
   the role the global `use_<route>` names, or the route's `default_role` when
   the global choice is a pin or absent. If the campaign sets `role_R_*`, that
   is the answer — **including over a global pin**. Done.
4. **Global route.** If the global `use_<route>` is a pin, it is the answer.
5. **Global role.** Global `role_R_*` → inheritance (§4.4).

| Campaign route | Campaign role R | Global route | Result |
|---|---|---|---|
| pin or role | any | any | the campaign route's choice (step 2) |
| — | set | any (pin included) | campaign role R (step 3) |
| — | unset | pin | the global pin (step 4) |
| — | unset | role or unset | global role R (step 5) |

Example: summaries are pinned globally to a specific model; a campaign sets
its own Fast role. In that campaign, summaries run on the campaign's Fast
model. In every other campaign they run on the pin.

A reference to a provider that no longer exists, or a preset id that names no
preset, is "no opinion" and the walk continues (today's rule, for today's
reason: a delete cannot rewrite every `campaign.md`). A provider that exists
but cannot serve — no key, or a known capability mismatch — is **reported**
with a 409, never walked past.

### 5.2 Preset cascade

campaign `preset_<route>` → global `preset_<route>` → the selection's own
preset → none. `PRESET_CLEAR` keeps its meaning. Route-level presets follow the
task onto the fallback (the sampler-preset design's rule); a selection's own
preset does not (the fallback brings its own).

### 5.3 Capability check

Before any network call, the operation and the route's `requires` are checked
against the resolved capabilities (§6.2) of each attempt in the chain:

- **known incompatible** on the primary attempt → 409 `incapable`, naming the
  route, the role (if any), the provider and the model, and what is missing.
  Only a `no` from the adapter, a user assertion or the catalog refuses: the
  name rule (§6.2 source 5) sorts pickers but never refuses a call, and a
  failed test call is `unknown`, not `no` (§6.4). Until slice C migrates it
  into model facts, a connection whose legacy `vision` is `on` is not refused
  for a vision `no` from the catalog or the preset; an adapter `no` or the
  model's own facts still refuse.
  Example: "The Scene-break route runs on the Decision role (Jev 1.13 on
  OpenRouter), which cannot answer decide() for this question type — choose
  another Decision model or pin this route." A pinned route says "is pinned
  to <model> on <provider>" and suggests another model for the route.
- **known incompatible** on a fallback attempt → that attempt is dropped from
  the chain (generalising today's "text-only fallback is dropped for
  image-bearing messages"). From slice C; slice B reports it in
  `fallback_missing` and the facade still sends it.
- **unknown** → allowed; surfaces show "unverified".

### 5.4 `ResolvedInference`

The one answer, built by one function (`inference.resolve(task, cid, *,
operation, override=None)`):

```python
ResolvedInference(
    task, operation, route,
    provenance,            # which scope/role supplied the selection and the preset
    attempts,              # ordered: primary, then fallback — each an Attempt
    space_id=None,         # embed only
    decision_mode=None,    # decide only: "native" | "structured" (per attempt)
)

Attempt(
    provider_id, provider_kind, base_url, rev, billing,
    model, facts,           # vision/prefill/post_process/rates from model facts
    capabilities,           # {name: (value, source)}
    preset_id, controls,    # effective controls (§8)
    retries,                # primary: llm_retries; fallback: 0
)
```

Until slice I, each `Attempt` is lowered to today's connection-dict shape so
`LLMClient` runs unchanged (approach 1, §13). The lowering is a single
function with its own tests; nothing else builds a connection dict.

Slice B adds `provider_kind`, `base_url`, `rev`, `billing`, `provider_preset`
(the §6.1 preset id — named apart from the sampler `preset_id`), `facts`,
`capabilities` and `controls`, and reports a known-incapable fallback in
`ResolvedInference.fallback_missing`; the facade starts dropping it in slice C.

**Slice A ships a reduced form** of these two types: `Attempt` carries the
provider id, model, preset id and the lowered connection dict, and
`ResolvedInference` carries task, operation, route (and its legacy key), the
role/via/scope provenance, the standing selection and the attempts. Each
later slice adds the fields it introduces — B: `provider_kind`, `base_url`,
`rev`, `billing`, `facts`, `capabilities`, `controls`; C: `retries` and the
facade consuming the attempts; D: `space_id`; F/H: `decision_mode`. The
fallback attempt already carries what the facade sends: the primary's
route-scoped preset when the primary's came from a route (§5.2), and no
fallback at all when it would be the primary's own provider.

### 5.5 Fallback

- Fallback comes from the role the route resolves through; a route pinned to an
  explicit selection uses the fallback of the role named by its
  `default_role`.
- One attempt, as today, dropped when it resolves to the same **provider** as
  the primary (the facade's rule since #144: a second attempt on the same
  provider is a retry, which the retry budget already covers) or when it is
  known incapable (§5.3).
- `decide` chain: native (if the selection is `decide_native`) → structured
  generation on the same selection (if it can `generate`) → the role fallback
  (native or structured, by its own capabilities).
- `embed`: no fallback (§7.3).

### 5.6 Per-call override (reroll, #77)

Request bodies that take an override accept optional `provider`, `model`,
`preset`; each one replaces that part of the resolved selection for this call
only. The legacy `connection_id` field is read as `provider`. Today's refusals
are kept verbatim in kind: an overlong model id is 400; an id naming no
provider is 400 (not 404 — scene routes reserve 404 for "scene gone"); a
provider that cannot send is the same 409. `routed` (whether the override
differs from the standing route) compares the effective provider + model +
preset.

Two details are settled in slice C, when the override UI is built, and not
before: an override's **preset** replaces the route-level preset too (it is
the most specific choice there is, so it ranks above `preset_<route>`), and a
**provider-only** override keeps the standing model on the new provider —
the legacy meaning ("that connection's own model") ends with the legacy
connection model field. Slice A keeps the legacy meaning and accepts no
preset field from a request.

### 5.7 The seam

`_require_connection(task, cid)` becomes `require_inference(task, cid, *,
operation)` returning `ResolvedInference`; `_override_connection` and
`_soft_connection` follow. `test_routing_guard.py` follows the rename and
additionally fails a call site whose operation differs from its route's.

---

## 6. Providers, capabilities and catalogs

### 6.1 Provider presets and adapters

Four adapters; eight presets. A preset is a friendly front on an adapter: it
pre-fills the URL and billing, and contributes capability facts.

| Preset | Adapter (`kind`) | Base URL | Billing | Reports price | Can do at all |
|---|---|---|---|---|---|
| OpenRouter | `openrouter` | fixed | metered | yes | generate, vision, embed, decide_native, structured_output — per catalog |
| Anthropic API (new) | `anthropic` | fixed `https://api.anthropic.com` | metered | no | generate; vision and structured_output (`output_config.format`) per the model's catalog entry; no embed, no decide_native; prefill never (current models reject it); sampling parameters per model (current models reject them) |
| Claude subscription | `claude` | — (Agent SDK) | subscription | equivalent | generate only |
| OpenAI | `openai_compatible` | fixed `https://api.openai.com/v1` | metered | no | generate, vision, embed, structured_output, decide_native (`/v1/decisions`) |
| z.ai | `openai_compatible` | fixed `https://api.z.ai/api/paas/v4` | metered | no | generate; vision, structured_output and prefill per model (unverified); no embed, no decide_native |
| z.ai Coding Plan | `openai_compatible` | fixed `https://api.z.ai/api/coding/paas/v4` | subscription | no | as z.ai |
| Ollama | `openai_compatible` | editable, default `http://localhost:11434/v1` | metered | no | everything unknown except `decide_native` (no) |
| LM Studio | `openai_compatible` | editable, default `http://localhost:1234/v1` | metered | no | everything unknown except `decide_native` (no) |
| Custom | `openai_compatible` | editable | user's choice | no | everything unknown except `decide_native` (no) |

A local endpoint costs nothing only once the user enters zero rates for it;
Grimoire never assumes a price (rule 5), so until then its models count toward
the Housekeeping chore (§9.2) like any other unpriced model.

The last column means **not ruled out**: it only decides whether a model
whose catalog says nothing lands in §6.3's Unverified group. A preset asserts
`yes` only for what is true of every model behind it (`generate` and
`stream` on the hosted presets; the local and custom presets assert nothing,
since an embedding-only server sits behind them as easily as a chat one) —
never for a per-model capability such as vision or embeddings. A known gap:
because the preset outranks the name rule, an embedding id whose catalog row
states no `outputs` reads `generate: yes` on a hosted preset; slice C's
Embedding picker must not rely on that row's generate answer.

The preset table is shipped code, deliberately small, and is where the
"quirks" the planning draft mentioned live. It is not a model database.

The new `anthropic` adapter speaks the Messages API with an API key: streaming,
system prompt, images, `max_tokens`, `temperature`/`top_p`/`top_k`/`stop`,
extended thinking for `reasoning_effort` (§8), usage capture (no price — rates
or `pricing.json` price it), the retry/error taxonomy of the other adapters,
and `/v1/models` for the catalog. It joins `catalog.py`'s normalisation and
the `TEXT_ONLY_KINDS` / `SUPPORTED_KINDS` partition test.

OpenRouter gains embeddings for the new-layout Embedding role (slice C makes
it selectable; a legacy `embeddings_connection_id` stays
`openai_compatible`-only, so no existing store starts embedding) — `POST /api/v1/embeddings`, through the same
response validation `embeddings.py` applies today (order, dimension, size
bounds).

### 6.2 Capability vocabulary and sources

Capabilities: `generate`, `stream`, `vision`, `embed`, `decide_native`,
`structured_output`, `prefill`. (`tools` is out: no call site uses it.) Each
resolves to `yes` / `no` / `unknown` with a `source`.

Sources, highest authority first:

1. **Adapter facts** (`source: adapter`) — what the wire protocol can
   physically do (e.g. `claude` and `anthropic` cannot `embed`; only
   `openrouter` and the OpenAI preset have a native decision endpoint). Nothing
   below may claim past a hard `no` here.
2. **User assertions** (`source: test` or `source: user`) — a passed test for
   the current `rev`, then `overrides` (and the per-model `vision`/`prefill`
   facts), then a failed test. A failed test is `unknown` carrying its error,
   never `no`: it marks the row unverified without hiding it, and the user's
   own override can still answer it.
3. **Provider catalog** (`source: catalog`) — OpenRouter is fetched with
   `output_modalities=all` (its default is text-only, which is why no embedding
   or decision model reaches the catalog today): `text` → generate,
   `embeddings` → embed, `decisions` → decide_native; `input_modalities`
   containing `image` → vision; `supported_parameters` containing
   `structured_outputs` or `response_format` → structured_output, and the param
   list feeds §8 as today. Anthropic's `/v1/models` is a catalog too: it
   publishes a capability tree (`image_input`, `structured_outputs`,
   `thinking.types`, `effort`, `max_tokens`).
4. **Preset table** (`source: preset`) — only its always-true facts
   (`generate`, `stream`); its hard `no`s (e.g. z.ai: embed) are source 1.
   The legacy connection `vision` field is a **post-image** setting and is not
   read as a vision assertion (the seam honours it as a bridge, §5.3); §4.2's
   `vision` in model facts is the new-layout assertion.
5. **Name rule** (`source: name`) — an id containing `embed` → embed `yes`,
   generate `no`.
6. Otherwise `unknown`.

`catalog.entry` keeps `vision` and `params` as today and gains `outputs` (the
output-modality list, or absent when the provider said nothing — absent is not
empty).

### 6.3 What a role picker lists

For a chosen provider, the model list is that provider's catalog split three
ways for the role's operation:

| Group | Meaning |
|---|---|
| **Fits** | capability `yes` for what the role needs |
| **Unverified** | `unknown`, and the preset does not rule the operation out; each row has **Test…** |
| (hidden) | known `no` |

What a role needs: Primary and Fast — `generate`; Decision — `decide_native`
**or** `generate` (structured generation can answer any decision; §7.4);
Embedding — `embed`. A typed model id is always accepted; one the catalog
does not list is judged on its name, the preset and its facts alone, so it can
land in any group. A provider whose preset rules an operation out shows an empty list
with the reason ("z.ai serves no embeddings").

Fallback pickers use the same filtering. The route pin picker uses the route's
operation and `requires`; the capabilities API takes one need per call, so a
route needing two (the image route: generate and vision) combines two calls.
Hidden rows are returned with their reasons for the screens that explain them.

### 6.4 The test call

Optional; offered on unverified rows and on the model-facts panel.

1. The user clicks **Test…**. A confirmation states the provider, model, what
   will be sent, and the estimated cost when the catalog states a price
   (otherwise "cost unknown — one tiny request"; the user's rates and
   `pricing.json` join the estimate with slice E).
2. On confirm, one probe per capability asked about:
   - generate: a fixed instruction, the reply capped at 64 tokens (the vision
     probe takes the same cap)
   - embed: one fixed short string; records the dimension
   - vision: a small solid-colour PNG (64×64, one tile; some vision stacks
     refuse a 1×1 image for reasons that have nothing to do with vision) and a
     one-word question
   - decide_native: one predicate (lands with the native adapters, slice H)
3. Results go to `facts.json[model].verified`, stamped with the provider's
   `rev` when the test started (nothing is recorded if it moved meanwhile). A
   failure is recorded only when the provider refused the probe itself
   (400/404/413/415/422, and not a refusal of the probe's own reply cap), with
   the error text it gave and no credentials; it resolves as `unknown` with
   that error (§6.2). Rate limits, outages, 402/408, auth and transport
   failures are reported to the caller and not recorded, and a failure that
   answers for every probe stops the probes after it (reported "not sent").
   An account limit (`llm_errors.account_limit`) is both: never recorded and
   always halting -- a 402, a 429 carrying `enforced_spend_limit_reached`, and
   a 400 whose message opens "You have reached your specified" (a spend limit
   the user set, which answers with a refusal's status). The same test keeps
   such a 400 from ever reading as a sampler preset refused.
   Each probe runs **once — no retries and no fallback** — and succeeds when
   the request is accepted and the response completes (text is not required:
   a thinking model may spend a small cap thinking).
4. The call is metered under task `model-test` with no campaign (an `embed`
   probe's row carries no token counts until slice D meters embeddings). `model-test`
   is registered as a non-route task so the routing guard knows it (the
   catalog refresh and the health probe meter nothing, so they need no
   registration).

The probe runs as a `draft` run (`runs.run_draft`) so a dropped connection does
not lose the result; it writes its facts in the run's terminal step.

### 6.5 Health checks

Free checks (OpenRouter `/key`, a `/models` listing) run as today. The
Claude-subscription probe generates, so it now asks first (rule 1) — the
Provider page offers "Check (sends one short message)" instead of checking
automatically.

---

## 7. Operations

### 7.1 Module layout

- `store/inference/` — pure and store-side: `providers.py` (preset table,
  provider records), `facts.py`, `roles.py` (role/route keys and the pure
  cascade), `capabilities.py`, `controls.py` (§8), `translate.py` (legacy →
  new, §11), `migrate.py`. It must respect `test_import_guard.py` (module-scope
  imports, submodule bindings across packages, acyclic).
- `inference.py` (top level, beside `llm.py`) — the async API below, built on
  `inference.resolve` and the existing `LLMClient`.
- `store/locks.py` classifies the new modules: global config modules are
  `OUTSIDE_DOMAIN` (they take no campaign lock), except `migrate.py`'s campaign
  step, which takes `campaign_lock(cid)` per campaign.

### 7.2 `generate`

```python
await inference.generate(task, messages, cid="", *, stream=True, usage=..., schema=None, override=None)
```

Behaviour is today's `LLMClient.stream/complete` behind `require_inference`.
New: `schema=` asks for JSON matching a JSON Schema. When the attempt's
`structured_output` is `yes`, the adapter sends the provider's structured mode
(`response_format: {type: json_schema}` on OpenRouter/OpenAI-compatible; a
forced tool schema on `anthropic`). Otherwise the schema is appended to the
prompt and the reply is parsed. Initially only `decide`'s structured backend
uses it.

### 7.3 `embed`

```python
vectors = await inference.embed(task, texts)
vectors = inference.embed_sync(task, texts)   # for today's synchronous callers
```

- Tasks: `semantic-recall`, `semantic-search`, `art-catalog`,
  `continuity-similarity`. They are registered embed tasks (not routes) so the
  guard and metering see them; all resolve through the **Embedding role**.
- `embed_sync` wraps today's synchronous client; the async form runs it in a
  worker thread until a native async client replaces it. Callers that are
  already in the threadpool keep calling `embed_sync`.
- Every embed call is metered (§9) with `operation: embed`.
- **`space_id` keeps today's exact string** `f"{provider_id}\0{rev}\0{model}"`.
  Migration preserves every provider's `rev` (§11), so existing vector caches
  stay valid through the upgrade.
- **No fallback, on any axis** (provider, model or preset). If the Embedding
  provider fails, the operation fails and the caller degrades exactly as it
  does today (keyword recall, keyword search with a note, basic continuity
  matching).
- Changing the Embedding role — provider, model, or a change that bumps the
  provider's `rev` — starts a new space. The settings surface confirms first,
  stating that the library will be re-embedded gradually as it is used
  (rule 1). Old vectors remain as unreachable cache entries.

### 7.4 `decide`

```python
results = await inference.decide(task, items, cid="", *, explain=False)
```

**Request.** `items` is a list; each item has its own `context` (text) and an
ordered list of questions:

| Type | Fields | Answer |
|---|---|---|
| `predicate` | `id`, `instructions` | `bool`, optional `probability` |
| `choice` | `id`, `instructions`, `options: [{id, description}]` (2–255), `allow_none` | an option id or `None`, optional `distribution` |
| `score` | `id`, `instructions`, `levels: [description, …]` (2–10, ordered) | a level index, optional `distribution` |

`rank` is not provided — nothing needs it.

**Result.** Per item, per question: `answer`, optional `probability` /
`distribution`, and when `answer` is `None` a `reason` (`unreadable`,
`refused`, `abstained`, `error`). Plus `backend` (`native` | `structured`), the
selection that answered, and usage. Missing distributions stay missing.
**There is no synthetic confidence value**, and callers must not compute one
across backends; task-specific escalation uses what a backend actually
reports. With `explain=True` the structured backend may return a short
`rationale` per item; native backends return none, and callers must work
without it.

**Structured backend.** One `generate(schema=…)` call per batch, items keyed
by index, chunked above a size cap (a constant justified structurally and
tuned against real prompts later — never against measured store contents).
Templates live under `templates/decide/` and go through
`verify_templates.py` and `evals/`.

**Native backends** (slice H), one request per item, bounded concurrency:

- OpenRouter: `POST https://openrouter.ai/api/alpha/decisions`, body
  `{model, state: context, questions}`; Grimoire `predicate` → `noul`,
  `choice` → `choice` (criteria map from option descriptions; `allow_none`
  maps to its explicit `none`), `score` → `score` (ordered criteria).
- OpenAI: `POST https://api.openai.com/v1/decisions`, body
  `{model, input: context, questions}`; questions as `predicate` / `choice`
  (`choices`) / `score` (`levels`), each with `name` and `instructions`.
- Both normalise to the result above. A native refusal is `reason: refused`.

**The five conversions** (slices F and G):

| Call | Question(s) | What happens to the prose half |
|---|---|---|
| scene-break | `predicate` "is this scene over?" | when true, a separate Fast `generate` (`scene-break-title`, route `summary`) drafts the title; the reason becomes the optional rationale |
| voice-drift | `choice` drift / in_voice / not_enough | the note becomes the optional rationale (`explain=True`); a native backend gives none and the verdict is shown without a note; today's `unknown` becomes `answer: None` and still counts as a failed check |
| speaker | `choice` over eligible refs, `allow_none` | none |
| continuity-identity | one item per row: `choice` existing / new / uncertain | the candidate id travels in the item's context; `None` is today's "undecodable" |
| continuity-reconcile | one item per candidate: `choice` over that candidate kind's vocabulary | per-kind vocabularies become per-item option lists |

**Eval gate.** Each conversion lands with recorded cases under `evals/` and
switches its call site only when the structured backend equals or beats
today's parse on them, offline. Native backends are measured with
`evals/run.py --live`, which spends money and is never automatic.

---

## 8. Presets and effective controls

`llm_sampling.effective(conn)` (wrapped for the screens by
`store/inference/controls.preview`) is the generalisation of
`llm_sampling.split`, and it is the only thing that both builds the wire
parameters and describes them. For each control:

| State | Meaning | Wire | UI |
|---|---|---|---|
| `supported` | sent as written | sent | normal |
| `translated` | sent under the provider's spelling | sent | editable, shows the mapping |
| `unsupported` | known not to work | not sent | greyed out; stored value kept |
| `unknown` | cannot be proven | sent, per today's rule (marked unverified; standard-only on strict endpoints unless `sampler_support` is on) | distinguishable from unsupported |
| `n/a` | the operation takes no sampling (embed, native decide) — produced from slices D/H, when operations reach the controls API | not sent | the panel says so |

`reasoning_effort` translations:

| Adapter | Wire |
|---|---|
| `openrouter` | `reasoning: {effort}`: `translated` when the cached catalog lists `reasoning`, `unsupported` when a cached catalog omits it, `unknown` (sent) when no catalog is cached |
| OpenAI preset | `reasoning_effort` |
| `anthropic` | `low`/`medium`/`high` → `thinking: {type: "adaptive"}` + `output_config: {effort: <same>}` where the catalog says adaptive thinking is supported (current models reject `budget_tokens`); where the catalog lists `enabled` thinking instead, `budget_tokens` 1024 / 4096 / 16000, each held to at most half the effective `max_tokens` and at least 1024 (thinking is `unsupported` when that leaves no room); when the catalog states neither, nothing is sent and the control is `unknown`; `off` depends on the catalog's thinking types: on a model with adaptive thinking (whose unset default is to think, whatever else it lists) it is `translated` → `thinking: {type: "disabled"}` where the catalog says `disabled` is supported, `unsupported` (nothing sent) where it says `disabled` is not — that model's thinking cannot be turned off; Claude Sonnet 5.5 is the one exception, which refuses `disabled` and takes `thinking: {type: "between_tools"}` as its off (no thinking before the reply; the API refuses it on every other model, so it is chosen by id) — and `unknown` (nothing sent) where it does not say; on a budget-only model, or one that takes no thinking at all, omitting `thinking` is off, so nothing is sent and it is `supported`; with no thinking types known it is `unknown`. `disabled` and `between_tools` are not thinking: they do not hold back sampling parameters. Sampling parameters are decided by the model id's Claude version, not by the thinking the catalog lists (Claude Opus 5 lists `enabled` thinking and still refuses them): they are sent only to an id naming a version below 4.7 (`claude-opus-4-6`, `claude-3-7-sonnet-20250219`) while no thinking is being sent, and `top_p` is not sent beside `temperature`; otherwise they are `unsupported` (source `adapter` for 4.7 and later, `unknown` for an id that names no version, such as `claude-mythos-preview`); `max_tokens` defaults to 16000 capped at the catalog's limit; `stop` is `translated` → `stop_sequences` |
| GLM on `openai_compatible` | today's `llm_reasoning.glm_effort`; `off` is `unsupported` (nothing sent, reported dropped): GLM takes low, high or max |
| `claude` (Agent SDK) | unsupported |
| other `openai_compatible` | `unsupported` (not sent) on a strict endpoint, like the other extensions; `unknown` (sent) where `sampler_support` allows non-standard parameters |

`max_tokens` is `translated` → `max_completion_tokens` on the OpenAI preset
(and wherever else an endpoint requires it). This is a deliberate exception to
rule 3: an existing connection at `api.openai.com` with a `max_tokens` preset
sends the new spelling from slice B, because the endpoint refuses the old one
on current models. The per-kind decisions live in
one gateway function (`llm_sampling.effective`, which the facade calls per
attempt from the connection dict, so the fallback is covered too); the store
wraps it to add each control's capability `source` for the screens. The API returns `{requested, effective, controls: {name: {state,
source, wire}}}` for any (preset, selection) pair; the Presets editor's
"Preview on…" and every role card render it. The frontend keeps no capability
table of its own.

---

## 9. Prices, metering and observability

### 9.1 Price precedence

1. **The provider reported a price** → `cost_usd`; or `estimated_usd` when the
   provider reports a subscription-equivalent price (today's `claude_agent`).
2. **Otherwise** rates × tokens → `modelled_usd`. Rates: the model facts'
   `rates` for (provider, model) first, then a `pricing.json` match (exact,
   then wildcard, then `""`). A provider with `billing: subscription` tags the
   row `subscription`, and cost surfaces label those rows "subscription — not
   billed"; they never count against a budget (they never did: only
   `cost_usd` does).
3. **Tokens** are the provider-reported counts when present. When a provider
   reports no counts, Grimoire counts locally with `store/tokens.py` (tiktoken
   on desktop, the characters/4 heuristic on Android) and flags the row
   `tokens_estimated`. A row with estimated tokens is labelled as such wherever
   its figure appears.
4. **No rates anywhere** → `unpriced_calls`, as today.

The three money columns and the "Estimated total" projection keep their
CLAUDE.md definitions; `modelled_usd`'s definition ("arithmetic this side
did") is unchanged and now also covers subscription calls priced from user
rates.

### 9.2 The Housekeeping chore

`chores.py` gains "Models in use with no price" in the Housekeeping group: the
count of distinct selections in use — roles, role fallbacks, route pins, and
campaign overrides of those — whose provider does not report prices and which
have no rates in model facts or `pricing.json`. It is computed from
configuration, never from the ledger (the chores contract: a live, cheap
count). Its action opens that model's rates field.

### 9.3 Ledger rows

Each usage row keeps `task` and gains `operation`, `provider`, `model`,
`preset`, `role` (when a role supplied the selection), `decision_mode`
(`native` | `structured`), `billing`, `tokens_estimated`. Embed and decide
calls are metered from their first slice onward. `usage.Meter.done` remains
the one place LLM failures are logged (CLAUDE.md, Observability).

### 9.4 Capture

- `generate`: unchanged prompt capture.
- `decide`: the full request (contexts and questions), mode, normalised
  answers and distributions, through the same prompt-capture path and under
  the same Settings privacy disclosure. No chain-of-thought is requested or
  stored; `rationale` is a requested output, not reasoning text.
- `embed`: counts, bytes, dimension, `space_id`, cache hits/misses. **Embedded
  text is never captured or logged.**

Keys never reach logs, captures, diagnostics or API responses.

---

## 10. Screens

All record editors follow CLAUDE.md's list/detail pattern (read-only by
default, explicit Edit). `/providers` and `/models` are pages that own the
screen, so their records sit in `ColumnSection`s in the page's context column.
Both are reached from Settings; the app rail is unchanged. Shortcuts, if any,
go through `useHotkeys`.

**`/providers`** (`/connections` redirects here)
- "+ New provider" asks which preset first; URL prefilled (locked for
  OpenRouter, Anthropic API, OpenAI, both z.ai presets).
- Detail sidebar: billing, extended samplers, health (free checks automatic;
  generating checks behind a button with confirmation), catalog age +
  Refresh, **Used by** chips (each role and route using this provider) that
  navigate there.
- **Models on this provider**: the catalog with capability badges (generate /
  vision / embed / decide — yes, no, unverified). Opening a model shows its
  facts: vision override, prefill, post-processing, rates, capability
  overrides, **Test…**.

**`/models`**
- Four **role cards** — provider ▸ model (Fits / Unverified / type an id) ▸
  preset — each with a **fallback** disclosure (not on Embedding) and an
  effective-controls readout ("Reasoning effort: translated → thinking budget
  8k · Min-p: unsupported, not sent"). Unset Fast/Decision show "Same as
  Primary" / "Same as Fast". The Decision card lists which routes currently
  use it (none until slice F).
- **Routing** (collapsed, "Advanced"): 15 rows — "Role ▾ (default: Fast)" or
  "Specific model…", plus the preset override, with today's "inherit (resolves
  to …)" labelling.
- Capability warnings inline on the card or row concerned:
  - "This model can't generate text, so it can't be Primary."
  - "This route sends images; the chosen model is unverified for vision."
  - "No native decision API; structured generation will be used."
  - "This model can't create embeddings."

**Presets editor**: today's `SamplerPresetEditor`, renamed, with
`reasoning_effort` and a **Preview on…** model picker rendering §8's states.

**Settings (`ConfigView`)**: the Connection, Model routing and Embeddings
sections are replaced by a summary card (the four roles at a glance, links to
`/models` and `/providers`). Retries, timeouts, semantic recall depth and
threshold, and image sending stay. `embeddingsOn.ts` is deleted; the
"Embeddings" chip reads the backend's resolved answer.

**Campaign Inspector**: "Model routing" becomes **Models** — campaign role
overrides (Primary, Fast, Decision) and route overrides, with inherit labels.

**Reroll override** (`RerollRoute`): provider ▸ model (Fits list for
`generate`) ▸ preset.

**Setup wizard**: Storage → **Provider** (preset, key, free check) →
**Models** (Primary required; Fast and Decision default to "Same as …";
Embedding optional with one line on what it enables) → Look → World.

**Newer-format banner** (§11) wherever model settings are shown.

---

## 11. Migration and compatibility

### 11.1 Translation, then persistence

`store/inference/translate.py` is a pure function from the legacy layout to
the new one. While `inference_format` is absent, the resolver reads the store
**through it**, so the app is correct before, during and without migration.
Migration persists that same translation **plus the enrichments in §11.2**
that a read-time translation deliberately does not make (an unset Claude model
written as `opus`, derived reasoning presets): those change what a later edit
starts from, not what resolves today, so the translation keeps the stored
values verbatim.

**The layout is decided once, globally.** A campaign's own `inference_format`
is honoured only when `config.md`'s is current; a campaign marker alone never
switches that campaign to the new keys, so a store migrated by a newer build
and opened by an older one resolves every campaign from the same frozen
legacy state.

### 11.2 Steps (global; once; idempotent; resumable)

1. **Backup** — `backups.create_backup()`. On failure nothing is written;
   Settings shows "Upgrade pending: the safety backup failed (reason)"; play
   continues through the translation; the next start retries.
2. **Providers** — each connection file gains `preset` and `billing`,
   **keeping its `rev`** (a `keep_rev` write), so catalogs, health verdicts
   and vector caches survive. Preset inference: `openrouter` → OpenRouter;
   `claude` → Claude subscription (`billing: subscription`);
   `openai_compatible` by `base_url` → OpenAI (`api.openai.com`), z.ai
   (`api.z.ai/api/paas`), z.ai Coding Plan (`api.z.ai/api/coding`, billing
   subscription), Ollama (port 11434), LM Studio (port 1234), else Custom.
3. **Model facts** — each connection's `vision`, `prefill`, `post_process` →
   `facts.json[<its model>]`.
4. **Presets** — a connection with `reasoning_effort` set gets a derived
   preset: its own sampler preset's params plus that effort, named "<preset
   name> · reasoning <effort>" (or "Reasoning <effort>" when it had no preset),
   with a deterministic id (`slugify` of that name); identical derivations
   collapse to one.
5. **Roles** — Primary = the active connection (its model, or `opus` for an
   unset Claude model) with its own or derived preset. Fast and Decision unset
   (inherit). Embedding = `embeddings_connection_id` + `embeddings_model`.
   `fallback_connection_id` → the fallback of Primary, Fast and Decision.
6. **Routes** — global `route_<k>=<conn>` → `use_<k>=model` +
   `use_<k>_{provider,model,preset}` from that connection. `preset_<k>`
   untouched. Unset routes stay unset; they resolve through their default role
   to Primary, which is the old active connection — today's behaviour.
7. **Split routes** — `speaker`, `scene_break`, `voice_drift` copy the
   `use_*` and `preset_*` of `scene`, `summary`, `voice` respectively.
8. **Campaigns** — steps 6–7 inside each `campaign.md`, under that campaign's
   own `campaign_lock` (one at a time — never two held; `test_lock_order_guard`
   applies), stamping the campaign's revision token, writing the campaign's own
   `inference_format: 2`. A busy campaign is skipped and retried; until then
   the translation answers for it.
9. **Marker last** — `inference_format: 2` in `config.md`.

Two devices migrating one synced store concurrently write identical content
(every derived value is deterministic); the second write rewrites the same
bytes.

### 11.3 Older and newer builds

- **Older builds** keep running on the state as of migration: legacy keys and
  fields are left in place, frozen. Changes made in the new UI do not reach
  them.
- **Newer formats**: from this release on, a build that reads an
  `inference_format` higher than it knows shows "This library was upgraded by
  a newer Grimoire" and refuses model-settings writes with 409
  `newer_format`, so it cannot overwrite settings it does not understand. Play
  continues on what it can read.

### 11.4 Retirement (slice I)

For stores at format 2: delete the legacy config keys (`active_connection_id`,
`fallback_connection_id`, `route_*`, `embeddings_connection_id`,
`embeddings_model`) and the legacy connection fields, then delete the
translation layer. A store still at format 1 at that point is migrated first,
backup included.

### 11.5 What does not migrate

Campaign content needs no migration. The frozen campaign fixture's `home/` is
never migrated in place; tests migrate a copy.

---

## 12. Errors

| Situation | Response |
|---|---|
| No Primary (nothing set, nothing to translate) | 409 `missing_key` (today) |
| A chosen provider has no key | 409 `missing_key`, naming provider and route/role (today's wording, generalised) |
| Known capability mismatch on the primary attempt | 409 `incapable`, naming route, role, provider, model and the missing capability |
| Override names no provider / overlong model | 400 (today) |
| `use_<route>=embedding` written | 400 |
| Campaign writes a global-only key (`role_embedding_*`, a non-campaign route) | 400 (today's `refused` rule, extended) |
| Model-settings write on a newer-format store | 409 `newer_format` |
| Migration backup failed | no write; Settings banner; translation serves |
| Decide question unanswerable | `answer: None` + `reason`; never a guessed default |
| Embedding provider fails | caller degrades as today; no fallback |
| Test call fails | a refusal of the probe itself is recorded in `verified` with the provider's error text and resolves as `unknown`, so the row stays "unverified" with that error shown and the call is never refused for it; transient failures (rate limit, outage, credits or a spend limit, auth, transport) are reported and not recorded |

Reads fail soft (a mangled `facts.json`, catalog or preset reads as empty);
writes fail visibly.

---

## 13. Implementation approach

**Resolver first, adapters reorganised last.** `ResolvedInference` is lowered
to today's connection dict so `LLMClient` — retries, fallback, health, image
lowering, prefill tails, post-processing — and its tests run unchanged while
the substrate moves underneath. The per-operation adapter registry
(`generate` / `decide` / `embed` / `models` per adapter) and the deletion of
the lowering come in slice I, when nothing depends on the dict shape.
Rejected alternatives: rewriting adapters first (largest risk before anything
is visible), and growing the connection dict forever (fails rule 2 and does
not fit `decide`/`embed`).

---

## 14. Slices

Each slice gets its own plan and goes through CLAUDE.md's gates (adversarial
review of the plan, `/codex:review` of the diff, a final adversarial review
against this spec) and lands green under `make check`. Order is chosen so that
**main never ships a UI that writes settings the backend ignores**.

| Slice | Lands | User-visible |
|---|---|---|
| **A — Resolver substrate** | `store/inference/` (keys, cascade, translation, resolver), the 15-route registry with `operation`/`default_role`/`requires` (legacy surfaces keep the original 12), `ResolvedInference` + lowering, `require_inference`, per-role fallback chain, `embed_space` resolved through the Embedding role, guard updates. Reads legacy state through the translation; writes nothing new | No. A behaviour-equivalence test pins every task's resolved provider, model, preset and fallback against the baseline resolver |
| **B — Providers, capabilities, controls** | The preset table, capability resolution (all sources) and the §5.3 capability check, OpenRouter `output_modalities=all` + `outputs` in catalog entries, the `anthropic` adapter, OpenRouter embeddings, `effective_controls` with `reasoning_effort` translations, the test-call endpoint and its confirm-first contract | API only |
| **C — The switch** | New storage writes, migration (§11), the facade taking each call's per-role fallback (re-resolved per generation, as the global one is today), retirement of the second cascade the legacy routing UI reads (`routing.resolve`/`bundle`, `sampler_presets.resolve`/`inherited`) in favour of the resolver, the newer-format guard, `/providers`, `/models`, Presets editor with reasoning and Preview on…, Settings summary card, Inspector Models, reroll override, wizard, capability warnings, test-call UI, dropping an incapable fallback attempt (§5.3; B reports it); legacy settings UI removed | **Yes** |
| **D — Embedding operation** | `inference.embed` / `embed_sync`, embed tasks, metering, the confirm on Embedding-role change, and one reader of the Embedding role (today `translate.embedding_role` serves `embed_space` while `cascade.role_selection("embedding")` is unused) | Small |
| **E — Pricing** | Ledger fields, rates in model facts, subscription tagging, local token estimation + flag, the Housekeeping chore | Yes |
| **F — `decide()`** | The contract, `generate(schema=)`, the structured backend, scene-break / voice-drift / speaker converted behind the eval gate; those routes' `default_role` flips to `decision` | Decision role in use |
| **G — Continuity decisions** | continuity-identity and continuity-reconcile converted behind the eval gate; `continuity.default_role` flips to `decision` | — |
| **H — Native decisions** | OpenRouter and OpenAI decision adapters, the native → structured → fallback chain, `--live` evals | Opt-in |
| **I — Retirement** | §11.4, the adapter registry, deletion of the connection-dict lowering | No |

**Safety rule across slices**: a route's `default_role` stays `fast` until
its tasks call `decide()` (F/G). Pointing the Decision role at a decide-only
model during C–E therefore cannot break any task; nothing uses that role yet.

### 14.1 Guards and docs

- `test_routing_guard.py`: follows `require_inference`; fails an operation
  mismatch between call site and route; knows the registered non-route tasks
  (`model-test`) and embed tasks.
- New guard: every `inference.embed`/`embed_sync` names a registered embed
  task; every `inference.decide` names a task on a `decide` route.
- `test_usage_guard.py`: decide and embed are metered.
- `test_import_guard.py`, `test_lock_domain_guard.py`: classify the new
  modules.
- CLAUDE.md: "Adding an LLM call site?" (`require_inference`, operation,
  route), Costs (billing basis, `tokens_estimated`, subscription rows in
  `modelled_usd`), the spending rule, and the "Detached runs" inventory (the
  test call is a new `draft`, so its handler count and the list of computing
  previews change); `docs/store-guarantees.md`: the
  migration/backup/marker promise. `test_docs_guard.py` holds both to the
  code.

---

## 15. Testing

**Resolution**: global roles; inheritance (Fast → Primary, Decision → Fast);
route → role; explicit pin; the four-way cascade table in §5.1 row by row;
campaign role override vs global pin; dangling provider/preset walked past;
existing-but-keyless provider reported; capability mismatch → 409; fallback
from the route's role and from a pinned route's default role; fallback dropped
when identical or incapable; reroll override field by field.

**Migration** (store fixtures, copies only): single active connection;
several routes on different connections; separate embeddings connection;
reasoning + sampler preset; derived-preset de-duplication; local Ollama;
z.ai and Coding Plan URLs; dangling route; dangling fallback; a busy campaign
(skipped, translated, migrated next run); a failed backup (nothing written);
`rev` preserved; **behaviour equivalence for every task**, before and after;
idempotence (running twice changes nothing); newer-format refusal.

**Capabilities**: each source and its precedence; adapter `no` beats every
other source; `rev` change invalidates `verified`; OpenRouter
`output_modalities` mapping; name rule; picker grouping per role — including
OpenRouter's Embedding list excluding GLM and a z.ai Embedding list empty with
its reason.

**Generate**: the existing streaming, retry, fallback, timeout, sampling,
image and capture suites stay green unmodified through slice H; `schema=` on
structured-capable and incapable attempts.

**Embed**: migrated role resolves to the same endpoint, model and `space_id`;
`space_id` changes on provider `rev` or model change; order/dimension
validation; no fallback on any axis; degradation paths unchanged; metering.

**Decide**: request validation (option and level bounds); structured parsing
of predicate/choice/score; malformed and partial output → `None` with reason;
batching and chunking; native normalisation for both APIs (canned bodies in
`llm_fakes`/cassettes, never live); native failure → structured → fallback;
no specialised decision model required; the five conversions' eval cases.

**Pricing**: precedence; facts rates before `pricing.json`; subscription tag;
local token estimation and its flag; never a zero for an unknown; the
Housekeeping chore's count from configuration only.

**UI**: list/detail tests per CLAUDE.md for `/providers`, model facts, Presets;
role cards (provider → filtered model → preset); fallback disclosure; routing
role/pin; capability warnings; test-call confirmation (nothing sent without
it); Embedding-change confirmation; wizard steps; Inspector overrides; reroll
picker; newer-format banner.

---

## 16. Non-goals

- A Vision role, a role per modality, or a model database.
- Requiring a native decision model, or making native decisions the default
  before they win on evals.
- Embedding fallback of any kind.
- Historical retrieval, agent orchestration, tool use.
- Replacing file-backed configuration with a database.
- Auto-picking "the best model".
- Per-campaign embedding spaces.
- Dual-writing legacy fields for older builds.

---

## 17. Completion criteria

1. Providers carry only transport, credentials and billing; several selections
   share one provider cleanly.
2. A role is provider + model + preset; routes resolve campaign route →
   campaign role → global route → global role.
3. Every LLM call is `generate`, `decide` or `embed` through one resolver;
   nothing reassembles configuration on its own, frontend included.
4. Role pickers filter by capability, with an unverified tier and an optional,
   confirmed test call.
5. The five decide-shaped calls run on `decide()` and pass their evals; native
   decision adapters plug in without call-site changes.
6. Embeddings resolve through the Embedding role, are metered, keep their
   space ids through migration, and never fall back.
7. Prices: reported → rates × tokens (locally estimated when unreported, and
   flagged), subscription-tagged, with the Housekeeping chore for missing
   prices.
8. Existing stores migrate automatically — backup first, marker last — and
   play identically.
9. Retry, fallback, health, sampling, image and capture behaviour is unchanged
   or has the explicit replacement stated here.
10. No paid call is started from a settings surface without confirmation.

---

## Appendix A — Decision log (brainstorming, 2026-10-07)

| # | Question | Decision |
|---|---|---|
| 1 | How to carve the work | One spec, lettered slices (continuity-capstone precedent) |
| — | Substrate or feature? | User-facing: providers → models per role is the deliverable |
| 2 | Setup flow | Providers first; each role picks provider + model from that provider's capability-filtered list |
| 3 | Unknown capabilities | Preset table + unverified tier + optional test call, **confirmed before spending** |
| 4 | Where per-model settings live | Split: model-intrinsic facts per (provider, model); sampling/reasoning/caps in the preset on the role/route |
| 5 | Campaign overrides | Campaign route → campaign role → global route → global role |
| 6 | Subscription pricing | Reported price first; else rates × tokens into `modelled_usd`, tagged subscription; no rates → Housekeeping Todo |
| — | No token counts reported | Count locally, flag `tokens_estimated` |
| 7 | Fallback | Per role; none for Embedding (provider-axis fallback considered and rejected: vectors from different providers are not provably one space) |
| 9 | Mixed-operation routes | Split into one-operation routes (15 total) |
| 10 | Which calls convert to `decide()` | All five, each behind an eval gate |
| 11 | Provider presets | Eight, including a new direct Anthropic API adapter |
| 12 | Older builds on a migrated store | One-way migration with backup and format marker; legacy fields frozen |
| — | Approach | Resolver over the existing client; adapter registry last |
| — | Spec gate | Codex adversarial review of this spec waived by the user |

## Appendix B — External API references (checked 2026-10-07)

- OpenRouter models: `GET /api/v1/models`, `architecture.input_modalities`,
  `architecture.output_modalities` (`text`, `image`, `embeddings`, `audio`,
  `video`, `rerank`, `decisions`, …; the endpoint defaults to `text`, filter
  with `output_modalities=…|all`), `supported_parameters`.
  https://openrouter.ai/docs/api-reference/models/get-models
- OpenRouter Decisions (Jev): `POST /api/alpha/decisions`, `{model, state,
  questions}`, question types `choice` (criteria map, explicit `none`), `noul`
  (true/false probability), `score` (2–10 ordered levels); answers keyed by
  question id with distributions; model id at the time `typesafe/jev-1.13`.
  https://openrouter.ai/blog/tutorials/how-to-use-jev/
- OpenAI Decisions: `POST /v1/decisions`, `{model, input, questions}`, question
  types `predicate` (`probability`), `choice` (`choices`; `choice`,
  `probabilities`, `confidence`), `score` (`levels`; `score`, `probabilities`,
  `confidence`); public beta, `gpt-6-luna` at the time.
  https://developers.openai.com/api/docs/guides/decisions
- z.ai: pay-as-you-go `https://api.z.ai/api/paas/v4`; GLM Coding Plan keys work
  only at `https://api.z.ai/api/coding/paas/v4`.
  https://docs.z.ai/api-reference/introduction

These are planning-time facts. The slice that implements each adapter
re-checks its reference before coding.
