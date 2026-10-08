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
2. **One resolver.** Exactly one entry point per operation family answers
   "what will serve this inference?" (§5.4): `resolve` for `generate` and
   `decide`, `resolve.embedding` for the Embedding role, which `resolve`
   refuses to stand in for. The wire request and every screen that describes
   it are built from that output. No call site and no frontend component reassembles
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
builds (§11) and ignored by this version once the store is at format 2 — with
one exception until slice I: a GLM connection's `reasoning_effort` still
applies where the effective preset sets none (§11.2 step 4). At format 2 they
are refused on write (§11.3).

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
    "overrides": {"generate": "", "vision": "", "embed": "", "decide_native": "",
                  "structured_output": ""}
  }
}
```

- `vision`: `""` (auto) / `on` / `off` — today's per-connection post-image
  setting, now per model. `on` is the user's word that the model reads images
  (a `user` yes, §6.2). `off` **stops post images and nothing else**: it is a
  preference about what this library sends, not a statement about the model,
  so it never makes the vision capability `no` and never refuses an image
  description that serves today (reading it as `no` would break rule 3). A
  user who knows the model reads no images says so with
  `overrides.vision = no`.
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

**Facts are what a call sends.** At format 2 the lowering (§5.4) overlays the
selected model's facts on the connection dict it builds: `vision`, `prefill`
and `post_process` come from `facts.json[<model>]` (a missing entry reads as
`""`, `false`, `none`), never from the legacy connection fields. Otherwise the
facts panel would write settings nothing sends, and a provider created at
format 2, which has no legacy fields at all, could never prefill. The legacy
`sampler_preset` is ignored at format 2 for the same reason: the preset
cascade (§5.2) supplies the preset, ending at the selection's own.

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
this version writes `2` as the last step of migration (§11), or with the
defaults it first writes into a fresh store (§11.1). Each migrated
`campaign.md` carries its own `inference_format: 2`, and so does each campaign
created at format 2 (§11.1).

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

**On the decide routes that is no longer so for Fast.** Step 3 reads the
campaign's slot of the role the route *uses*, and since slices F and G the
four decide routes (`speaker`, `scene_break`, `voice_drift` and `continuity`)
use Decision, which inherits Fast. A campaign that overrides Fast and not Decision reaches them
only at step 5, by that inheritance, after a global pin at step 4. So with
scene-break checks pinned globally and a campaign Fast override, the checks
in that campaign ran on the campaign's Fast model before F and run on the pin
after it; the campaign gets the old behaviour back by setting its own
Decision role. This is deliberate (the route uses Decision, and step 3 is
about that role, inheriting or not), and the migration-equivalence check
cannot see it, because a migrated store has no campaign role slots: the case
exists only once someone sets one in the new layout. `continuity` (slice G) is
the fourth, and the same case applies to it: the duplicate check and the sweep
ran on a campaign's Fast model before G and run on the pin after it.

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
  An embed task is the exception: a known `no` for `embed` turns embedding off
  instead of refusing (§7.3).
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
- **decide** (slice H): there is no skip. A primary that is known unable to
  `generate` and not known unable to `decide_native` is served natively
  (§5.5); an `unknown` `decide_native` is allowed, as everywhere. A model that
  can generate is served by structured generation whatever its `decide_native`
  says (§5.5, §16). `OPERATION_CAPABILITY` (the table of which capability an
  operation needs of its primary) widens decide's entry to "decide_native or
  generate", so a decide primary is refused only when both are known `no`, and
  its sentence then reads "cannot generate text or make native decisions",
  composed in `incapable_text` from the two capabilities' own phrases rather
  than from a new key in `CANNOT`.
  The Decision role card is resolved as a `decide`, so the card and the decide
  routes' rows read one refusal: `skip_text`, `decide_skip` and the skip notice
  are deleted, and a role card and its row can no longer disagree.
- **known incompatible** on a fallback attempt → that attempt is dropped from
  the chain (generalising today's "text-only fallback is dropped for
  image-bearing messages"). From slice C; slice B reports it in
  `fallback_missing` and the facade still sends it.
- **unknown** → allowed; surfaces show "unverified".

### 5.4 `ResolvedInference`

The one answer, built by one function per operation family:
`inference.resolve(task, cid, *, operation, override=None)` for `generate` and
`decide`, and `resolve.embedding(cfg)` for the Embedding role. The Embedding
role has its own entry point because it is global only (§4.4) and no task
chooses it: `resolve.resolve` refuses an embed task, `operation="embed"` and
`role="embedding"`, so embed work can never land on Primary as an unknown task.
`resolve.embedding` reads the role through
`cascade.role_selection("embedding", campaign={})`, and it is the one reader:
the settings view, the migration and the operation all ask it.

```python
ResolvedInference(
    task, operation, route,
    provenance,            # which scope/role supplied the selection and the preset
    attempts,              # ordered: primary, then fallback — each an Attempt
    space_id=None,         # embed only; set only when the role embeds
    decision_mode=None,    # decide only: "native" | "structured"; a property over
                           # the attempt that is sent (Attempt.decision_mode)
                           # (there is no `skipped`: slice H deletes the skip)
)

Attempt(
    provider_id, provider_kind, base_url, rev, billing,
    model, facts,           # vision/prefill/post_process/rates from model facts
    capabilities,           # {name: (value, source)}
    preset_id, controls,    # effective controls (§8)
    retries,                # primary: llm_retries; every fallback stage: 0,
                            # structured included (slice H, ruling 12)
    decision_mode="",       # decide only: the capability answer (see below)
)
```

Until slice I, each `Attempt` is lowered to today's connection-dict shape so
`LLMClient` runs unchanged (approach 1, §13). The lowering is a single
function with its own tests; nothing else builds a connection dict.

`Attempt.decision_mode` is the resolver's *capability* answer: which backend
will serve that attempt first. How a call *was* served is not read back from
it; the backend stamps the mode per call on a copy of the **account block**
(a small record the resolver lays on each lowered connection dict, carrying
`operation`, `role` and the like, which the facade copies into the usage
holder so the ledger row learns what the resolver knew; §9.3), so no block of
the resolution is mutated. `ResolvedInference.decision_mode` reads the same
answer off the attempt that is sent. `ResolvedInference.skipped` is removed
with the skip (§5.3). `Attempt.decision_mode` gains `native` in slice H, and it
is `native` only when `generate` is a known non-guess `no` and `decide_native`
is not a known `no`; it is `structured` whenever the attempt can generate,
whatever `decide_native` says (§5.5). A fallback stage takes `retries: 0`,
native or structured, and `LLMClient.complete` takes an optional `retries=`
that overrides the primary route's count only when given.

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
facade consuming the attempts; D: `space_id`; F: `Attempt.decision_mode` and `skipped`; H: the
`native` value, and `skipped` removed. The
fallback attempt already carries what the facade sends: the primary's
route-scoped preset when the primary's came from a route (§5.2), and no
fallback at all when it would be the primary's own provider (except for a
decide primary that cannot generate, §5.5).

`space_id` is set only when the Embedding role embeds: it names a model and an
endpoint, and the model is not a known `no` for `embed` (§7.3). A role that is
unset, half-set or known not to embed resolves with no `space_id`.

### 5.5 Fallback

- Fallback comes from the role the route resolves through; a route pinned to an
  explicit selection uses the fallback of the role named by its
  `default_role`. On a decide route that role is Decision, so its fallback is
  Decision's at either scope before the Fast fallback it inherits: a
  campaign's own Fast fallback, like its Fast selection (§5.1), reaches a
  decide route only when nothing names a Decision fallback.
- One attempt, as today, dropped when it resolves to the same **provider** as
  the primary (the facade's rule since #144: a second attempt on the same
  provider is a retry, which the retry budget already covers) or when it is
  known incapable (§5.3). The one exception is a decide primary that cannot
  generate (slice H, ruling 6): its next step is a different model on another
  backend, not a retry, so a same-provider fallback is kept (one OpenRouter
  account with a native-only Decision model and one of its generating models
  behind it). A same-provider fallback behind a primary that generates is
  dropped as before. `fallback_problem` reads off the same condition:
  `SAME_PROVIDER` is lifted exactly where the drop is, so a row that keeps the
  fallback names no problem with it, and one that drops it says `SAME_PROVIDER`,
  or the credential problem it shares with the primary (no key on that
  provider), as it always did.
- `decide` chain: the selection's one backend — native when it is known unable
  to `generate` and not known unable to `decide_native`, otherwise structured
  generation when it can `generate` — then the role fallback, chosen by the
  same rule, in one attempt. Native is not tried first for a model that also
  generates (§16). Whether it should be is a later user decision, made only
  after `evals/run.py --live --decide-backend` has compared the two backends on
  one model.
  - Each stage runs the items still pending down its own backend. **What moves
    an item to the next stage is a failed call**: an `LLMError`, including a 2xx
    body that is not the documented envelope or that answers none of the item's
    questions (`bad_response`), and an item refused unsent as one a decisions
    endpoint cannot represent (code `native_unrepresentable`, §7.4). An answer
    never moves an item, a native `refused` and a `None` from a well-formed body
    included.
  - The fallback gets **one stage**, native or structured by the same rule
    applied to its own capabilities. It rides the facade (`FALLBACK_KEY`) only
    when it is structured and the primary generates; otherwise it is its own
    stage.
  - A `PresetRefusalError` stops the chain, as it stops the facade, including a
    native fallback stage that would take no sampling: the user should fix the
    preset.
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
preset field from a request. From slice C the preset is honoured on a
format-1 store too (a store whose migration keeps failing stays there): the
legacy layout lowers a named preset exactly as the new one does. The
provider-only meaning moves only with the format, since a format-1
connection still has a model of its own.

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
| Anthropic API (new) | `anthropic` | fixed `https://api.anthropic.com` | metered | no | generate; vision and structured_output (`output_config.format`) per the model's catalog entry; no embed, no decide_native; prefill per model: a hard `no` on Claude 4.6 and later and on an id naming no version (the API answers a trailing assistant turn with a 400), possible on earlier models such as Claude Haiku 4.5; sampling parameters per model (current models reject them) |
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
   the current `rev`, then `overrides` (`overrides.vision = no` is the user's
   vision `no`), and the per-model `vision: on` and `prefill` facts, then a
   failed test. A `vision: off` fact is **not** read here: it stops post
   images and nothing else (§4.2). A failed test is `unknown` carrying its
   error, never `no`: it marks the row unverified without hiding it, and the
   user's own override can still answer it. An `unknown` never overrides a
   known `no` from a lower source that is not a guess: a failed test outranks
   the catalog's `yes` but not its `no`, so it can neither lift a refusal
   (§5.3) nor turn an Embedding role that is off back on (§7.3).
3. **Provider catalog** (`source: catalog`) — OpenRouter is fetched with
   `output_modalities=all` (its default is text-only, which is why no embedding
   or decision model reaches the catalog today): `text` → generate,
   `embeddings` → embed, `decisions` → decide_native; `input_modalities`
   containing `image` → vision; `supported_parameters` containing
   `structured_outputs` → structured_output (`response_format` alone says
   nothing: on OpenRouter it usually means `json_object`, not a schema), and the
   param list feeds §8 as today. Anthropic's `/v1/models` is a catalog too: it
   publishes a capability tree (`image_input`, `structured_outputs`,
   `thinking.types`, `effort`, `max_tokens`).
4. **Preset table** (`source: preset`) — only its always-true facts
   (`generate`, `stream`); its hard `no`s (e.g. z.ai: embed) are source 1.
   The legacy connection `vision` field is a **post-image** setting and is not
   read as a vision assertion (the seam honours it as a bridge, §5.3); §4.2's
   `vision: on` in model facts is the new-layout assertion, and its `off` is
   no assertion at all.
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
   will be sent, and the estimated cost. The estimate is the catalog's reported
   price when it states one. Otherwise it depends on whether the provider
   reports its own price (its preset's `reports_price`, e.g. OpenRouter):
   - a provider that **reports** its price is "cost unknown". It never falls
     through to the model's rates or `pricing.json`, because the ledger never
     prices its calls from them (what it reports always wins, §9.1), and a
     zero default there would read "≈ $0.00 at your rates" for a call that
     will be billed;
   - a provider that does **not** report a price is priced from the model's
     rates, then `pricing.json`.

   `estimate_basis` says which source priced it. Only when none of them does
   the dialog say "cost unknown — one tiny request". The vision probe also
   adds one image at the catalog row's per-image price (OpenRouter's
   `pricing.image`, kept on the normalised row as `image`); a catalog row that
   states token prices but no parseable non-negative image price leaves the
   *catalog* estimate unknown — never priced as free — and the rule above
   then applies. With user rates the vision probe is priced from its token
   guess, since rates price image input as prompt tokens.
2. On confirm, one probe per capability asked about:
   - generate: a fixed instruction, the reply capped at 64 tokens (the vision
     probe takes the same cap)
   - embed: one fixed short string; records the dimension
   - vision: a small solid-colour PNG (64×64, one tile; some vision stacks
     refuse a 1×1 image for reasons that have nothing to do with vision) and a
     one-word question
   - decide_native: one fixed predicate. It is **never priced**: the probe is
     `priceable=False`, read by every estimator, because a catalog row does not
     say how a decision is billed and a probe with no reported tokens must not
     read as "$0.00". The Decision picker offers it only for a model known
     unable to generate (a model that can generate is served structured, §5.5,
     so the answer would change nothing); the provider page's explicit Test…
     may list it for any model
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
4. The call is metered under task `model-test` with no campaign. An `embed`
   probe's row carries `operation: "embed"` and whatever counts the endpoint
   reported, and a probe that fails is recorded with its kind and HTTP status
   only (the verdict shown to the user still carries the provider's text).
   The probe stays on the provider under test: it is not an embed task and does
   not resolve the Embedding role. `model-test`
   is registered as a non-route task so the routing guard knows it (the
   catalog refresh and the health probe meter nothing, so they need no
   registration).

The probe runs as a `draft` run (`runs.run_draft`) so a dropped connection does
not lose the result; it writes its facts in the run's terminal step.

### 6.5 Health checks

Free checks (OpenRouter `/key`, a `/models` listing) run as today. The
Claude-subscription probe generates, so it now asks first (rule 1) — the
Provider page offers "Check (sends one short message)" instead of checking
automatically, and the server refuses that check without `confirm: true`
(§12), so no client can spend on it unasked.

---

## 7. Operations

### 7.1 Module layout

- `store/inference/` — pure and store-side: `providers.py` (preset table,
  provider records), `facts.py`, `roles.py` (role/route keys and the pure
  cascade), `capabilities.py`, `controls.py` (§8), `translate.py` (legacy →
  new, §11), `migrate.py`. It must respect `test_import_guard.py` (module-scope
  imports, submodule bindings across packages, acyclic).
- `store/inference/embed.py` — the `embed` / `embed_sync` operation (§7.3).
  Every caller is a store module, and the store never imports `llm.py` (#239),
  so the operation cannot sit beside `LLMClient`.
- `inference.py` (top level, beside `llm.py`) — the async `generate` and
  `decide` API below, built on `inference.resolve` and the existing
  `LLMClient`. `decide` stays here. The two modules are disjoint, and neither
  merges into the other.
- `decisions.py` (top level, a gateway leaf that imports nothing from the
  package) — `decide`'s request and result types, validation, the JSON Schema
  of a batch and the parser (§7.4). Slice H's native adapters are gateway
  modules too (#239) and normalise into it without touching the store.
- `store/locks.py` classifies the new modules: global config modules are
  `OUTSIDE_DOMAIN` (they take no campaign lock), except `migrate.py`'s campaign
  step, which takes `campaign_lock_nowait(cid)` per campaign.

### 7.2 `generate`

```python
await inference.generate(task, messages, cid="", *, stream=True, usage=..., schema=None, override=None)
# slice F has: LLMClient.complete(messages, conn, usage=None, *, schema=None)
#               LLMClient.stream(messages, conn, usage=None, *, schema=None)
```

`inference.generate` arrives with slice I's adapter registry. Until then the
facade's own `complete` and `stream` take `schema=` (shown above) and are
what `decide` calls. Behaviour is today's `LLMClient.stream/complete` behind `require_inference`.
New: `schema=` asks for JSON matching a JSON Schema. When the attempt's
`structured_output` is `yes`, the adapter sends the provider's structured mode
(`response_format: {type: json_schema}` on OpenRouter/OpenAI-compatible;
`output_config.format` with `json_schema` on `anthropic`, not a forced tool,
which current models answer with a 400). Initially only `decide`'s structured
backend uses it.

- **The schema is always in the prompt**, and structured mode is added on top
  for each attempt that can carry it. There is then one prompt per decision:
  one capture, one prompt for the evals to score, and no per-attempt prompt
  variant for a fallback that lacks the mode.
- **Decided per attempt.** The resolver flags an attempt on a decide
  resolution whose `structured_output` is `yes` (`STRUCTURED_KEY` on its
  lowered dict); the facade reads the flag from the dict it already receives,
  so a fallback is covered without the facade importing the store. A generate
  resolution never carries the key, so its dicts are unchanged.
- **Schema features** are restricted to the intersection of OpenAI strict
  mode's and Anthropic's documented subsets: `type`, `enum`, `anyOf`,
  `required`, `additionalProperties: false`, with no numeric bounds (a score is
  an integer `enum`) and a nullable choice as `anyOf` with a `{"type": "null"}`
  branch, never a type array. Structured output is incompatible with prefill,
  which decide prompts never use. Each adapter re-checks Appendix B before it
  is coded.
- **A 400 that names the structured field is not a preset refusal.** The
  facade's sampler-preset refusal subtracts the structured envelope's
  spellings, so a provider refusing the schema leaves the fallback to be tried.
  A schema refusal is not a health failure either: the connection answered,
  and still serves every generate call. Once every route has failed,
  `decide` sends each attempt that refused the field once more without the
  mode -- a refusing primary after its fallback failed too, a refusing
  fallback after the primary failed for any reason -- each as its own metered
  call (the schema is in the prompt, so the reply still parses, and a call
  that worked prompt-only before slice F still works). One retry per attempt,
  never more; any other 400 is the attempt's failure as before. A refused
  field is a mode the call can drop, not the connection's word, so a
  both-failed error whose primary refused it reports the fallback's kind and
  window (`llm.routes_failed`).
- Anthropic compiles each new schema once and caches it, so the speaker's
  roster-dependent schema pays the compile latency once per roster
  composition. To be tuned against real prompts later, never against a
  measured library.
- `decide` is the only caller of `schema=` before slice I adds
  `inference.generate`.

### 7.3 `embed`

```python
vectors = embed_sync(task, texts, *, space, client, deadline=None, budgeted=False,
                     campaign="", scene="", cached=None, uncached=None)
vectors = await embed(task, texts, ...)   # the same, in a worker thread
```

Both live in `store/inference/embed.py` (§7.1).

- Tasks: `semantic-recall`, `semantic-search`, `art-catalog`,
  `continuity-similarity`. They are registered embed tasks (not routes) so the
  guard and metering see them; all resolve through the **Embedding role**. The
  registry is `routing.EMBED_TASKS`, not a route: every embed task resolves
  through the one global role, so a route would carry no choice, and the routes
  table is what the frozen baselines enumerate.
- **The space is handed in.** Every caller reads its vector cache under a space
  before it embeds. Resolving again inside the call could embed with one model
  and save under another space's key, so the caller passes the `space` it read
  with. **The client is the caller's own** (`client`); each module keeps its
  client, which is also its test seam. Deadlines, the vector cache and
  degradation stay with the caller.
- `embed_sync` wraps today's synchronous client; the async form runs it in a
  worker thread until a native async client replaces it. Callers that are
  already in the threadpool keep calling `embed_sync`.
- **A known `no` for `embed` means off.** Known is §5.3's sense: an adapter
  `no`, a preset's hard `no`, a user assertion or the catalog, and never the
  name rule's guess. There is no 409 for this operation: no request is sent,
  and every caller degrades exactly as it does when the role is unset. A
  chat-only catalog model chosen as the Embedding model stops sending a request
  that could only fail, and the Embedding card says so through its `problem`
  (§10, §12).
- Every embed call is metered (§9) with `operation: embed`. The row carries
  the caller's `campaign` where it has one (and `scene` where it has one: a
  turn's lore recall and art ranking, and absorb's identity check, so a
  scene's own totals carry its embeds); resolution never reads either, because the space is
  global (§4.4). A campaign's budget is measured by `cost_usd` per campaign,
  and an OpenRouter embed is real spend.
- **User-visible cost change.** From D onward every recall, art, search and
  continuity embed files a ledger row. An endpoint that reports no price (a
  local or `openai_compatible` server) files an **unpriced** row, so Costs
  totals read "incomplete" until the user enters rates for that model. An
  embed row's absent completion count is a structural zero, not a count
  nobody took, so those rates model it (`modelled_usd`, never spend) from D
  onward rather than waiting for slice E; a row with no prompt count stays
  unmetered.
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
  (rule 1), and the server refuses an unconfirmed change (`confirm_embedding`,
  §10). Old vectors remain as unreachable cache entries. Turning the role on
  is the same move, so a model-facts write that does (the user's `embed: yes`
  over a known `no`) is confirmed the same way (§12).

### 7.4 `decide`

```python
results = await inference.decide(task, items, *, client, resolved, explain="",
                                 campaign="", scene="", post=None, round_id="",
                                 capture=None, around=None)
```

A call site resolves with `require_inference(task, cid, operation="decide")`
and hands `decide` the resolution, so its 409 comes before the call's own due
check, absorb's soft resolution still lets a phase report itself failed, and
the speaker keeps its threadpool hop. The client is passed because the
app-scoped client is the seam the test fakes replace. The task stays a literal
for the guard (§14.1), and `decide` refuses a resolution for another task or
another operation.

**Request.** `items` is a list; each item has its own `context` (text) and an
ordered list of questions:

| Type | Fields | Answer |
|---|---|---|
| `predicate` | `id`, `instructions` | `bool`, optional `probability` |
| `choice` | `id`, `instructions`, `options: [{id, description, aliases}]` (2–255), `allow_none` | an option id or `None`, optional `distribution` |
| `score` | `id`, `instructions`, `levels: [description, …]` (2–10, ordered) | a level index, optional `distribution` |

`rank` is not provided — nothing needs it.

An option may carry `aliases`, which the structured parser accepts after one
generic spelling normalisation (case-folded, stripped, runs of spaces or
hyphens as `_`); voice drift's two legacy synonyms survive that way. `validate`
rejects any id or alias that collides with another once normalised, and native
backends ignore aliases.

**Result.** Per item, per question: `answer`, optional `probability` /
`distribution`, and when `answer` is `None` a `reason` (`unreadable`,
`refused`, `abstained`, `error`). Plus `backend` (`native` | `structured`), the
selection that answered, and usage. Missing distributions stay missing.
What answered is per chunk, since each chunk runs down the attempt chain on
its own: `served` names every `(provider, model)` that answered one, in the
order it first answered, and `provider`/`model` name the selection only when
it is the one `served` holds (both empty when chunks were answered by
different routes, the primary on one and its fallback on another).
`errors` holds each failed chunk's final error, in chunk order: what `decide`
would raise for that chunk alone, after its re-sends (each refusing route's
prompt-only re-send, its failure composed with the other route's as the
facade composes any both-failed call, the routes' own failures kept as
`words`). It is empty when every chunk answered. A backend fills it for every
chunk whose items it marks `error`: call sites report a batch's failure from
it (the first entry, which is the error `decide` raises when no chunk
answers), so a backend that left it empty would turn a rate limit into a
bare `error` and lose its kind and `retry_after`. Under slice H
`Decision.backend` follows the same rule as `provider`/`model`: it names the
one backend that answered, and is empty when stages with different backends
answered items of one batch. Each `ItemResult` carries its own `backend`,
which is the per-item truth when a batch is split across stages.
An explicit `null` on a choice with `allow_none` is `answer: None, reason:
abstained`; a `null` on a choice without it is `unreadable`. `unreadable` may
carry a `detail` sub-reason (`not_an_option`: an answer present and not null
that matches no option; `no_object`: the reply held no object; `no_item`: it
held nothing readable as this item), which is not a new reason and keeps the
closed vocabulary closed. `no_object` and `no_item` carry "undecodable is not
an empty answer" per item.
A `choice` with `allow_none` may offer a single option: null is its second
answer.
`was_read(answer)`: an answer was read when it is not `None`, or is
`unreadable` with detail `""` or `not_an_option`. `no_object`, `no_item`,
`error`, `refused` and `abstained` are not read. A mapping that must not store
a guess for an item the model never answered asks this.
`offerable(spelling)`: the one test `validate` applies to an option id or
alias, so a builder can drop what would be refused. A new reserved option id is
refused inside `offerable`, so every builder drops it and `validate` never
sees it (slice H's `NONE_KEY` is the first).
**There is no synthetic confidence value**, and callers must not compute one
across backends; task-specific escalation uses what a backend actually
reports. `explain` is the rationale *instruction*, a string: scene-break's is one
sentence of reason, voice drift's a corrective addressed to the writer, and
empty means no rationale is requested. With one the structured backend returns
a short `rationale` per item; native backends return none, and callers must
work without it. For reconcile, a status word stands on an answered evidence
scene alone, on either backend, and stores `reason: ""` when no rationale came
back; nothing is invented in its place. Voice drift on a native backend with no rationale is slice H's
branch, settled: a native `drift` with no note is usable, stages nothing, keeps
any standing flag and is listed in `noteless`, and a native `in_voice` still
proposes the clear; the over-cap and unknown checks still apply. F keeps "drift
needs a corrective" for the structured backend, which always has a rationale
field.

**Structured backend.** One `generate(schema=…)` call per batch, items keyed
by index, chunked above a size cap (a constant justified structurally and
tuned against real prompts later — never against measured store contents).
Templates live under `templates/decide/` and go through
`verify_templates.py` and `evals/`.

- **The cap is 8 items per call** (`MAX_ITEMS_PER_CALL`). Each item carries its
  own transcript context, so one call's prompt grows linearly with the batch,
  and one unreadable reply loses every item in its chunk; eight bounds both.
  No slice-F caller sends more than one item.
- **A chunk also holds at most 1,000 enum values** (`MAX_ENUM_VALUES`,
  summed over every choice's options and every score's levels in its schema),
  OpenAI strict mode's documented budget; an item that alone exceeds it is
  refused by `validate`. That is the only strict-mode schema limit F enforces.
  Three more are documented and reachable (OpenAI's Structured Outputs,
  "Supported schemas", checked 2026-10-08): 15,000 characters of enum strings
  in a single enum of more than 250 values (`MAX_ENUM_STRING_CHARS`,
  `ENUM_STRING_CHARS_ABOVE`; refused per choice), 120,000 characters of
  property names, definition names, enum values and const values in total
  (`MAX_SCHEMA_STRING_CHARS`, counted by `schema_chars` over the schema with
  `rationale`, the worst case), and 5,000 object properties in total
  (`MAX_SCHEMA_PROPERTIES`, counted by `schema_properties`; nothing bounds an
  item's question count, so an item of about 5,000 predicates reaches it).
  Slice H enforces all three in `decisions.validate`, and the last two in
  `chunks` too, whatever the backend, because the chain can fall to a
  structured fallback stage. The page's fourth limit, 10 levels of nesting,
  cannot be reached: a batch schema is four objects deep whatever it asks.
- **One meter per chunk, opened inside `decide`.** A caller's time budget runs
  inside it through the `around` hook, which is handed the facade call and the
  meter's live holder, so an overrun is filed as an `error/timeout` row as
  today. Operation and decision mode reach the ledger through the account block
  (§9.3); `decide` adds no meter parameter and writes nothing into the holder.
- **Provider errors propagate.** An `LLMError` from a chunk is re-raised as
  such when no chunk answered; `reason: error` marks only a failed chunk's
  items, and only when another chunk answered. `Decision.errors` holds each
  failed chunk's final error, which the call sites read through
  `_decide_error`.
- **`capture`** is a hook called after each call settles (§9.4).
- **Reading the reply.** The parser never raises and answers every item. It
  reads a fenced or surrounded object, and for a single item two shapes a
  model answering in today's style produces: the *unwrapped* (`{"answers":
  …}` with no index key) and the *flattened* (the question ids and
  `rationale` at the top level). The flattened shape also takes the legacy
  prompts' names for the rationale, `note` (voice drift) and then `reason`
  (scene-break), when there is no `rationale` and no question of that id, so a
  reply in today's format keeps its corrective. With two items either is
  unreadable. An item keyed by its index whose question ids sit directly under
  the key (`{"0": {"over": true}}`, the `answers` level dropped) is read as the
  flattened shape under that key, for any number of items. A reply keyed by
  index but not by ours is read as no item's: an all-digit key that is not one
  of the indices sent (`"2"` beside two items) or is not canonical (`"01"`)
  leaves every item `unreadable` with detail `no_item`, the in-range keys
  beside it included, because a key past the batch is most likely the model
  numbering from 1, and renumbering it is a guess (an answer left unread,
  never misattributed). The cost lands differently per call site: a
  continuity-reconcile chunk's candidates get no proposal and the next sweep
  asks them again, while a continuity-identity chunk's rows stage with hints
  only for that absorb, since the phase has no retry. A reply numbered from 1
  that skipped an item can stay in range, and no test on the keys alone tells
  it from a reply keyed from 0 that skipped item 0, so it is read as keyed;
  `decisions._foreign_index` records that limit. A
  predicate is a JSON boolean only (the string `"true"` is unreadable), a
  score an integer inside its levels (a boolean is not), and a choice an option
  id or alias. Valid JSON in any other shape is `None` with `unreadable`, never
  a default. A repeated key in a reply keeps its first value, at every level,
  the rule both continuity parsers have today; the scene-break, voice-drift and
  speaker call sites inherit it, and none of F's gate corpora repeats a key.

**Native backends** (slice H), one request per item, bounded concurrency. A
native stage serves a selection that cannot generate (§5.5); the wire shapes
below are from the two providers' pages as read on 2026-10-08 (Appendix B), and
the slice re-reads them before coding. A contradiction of a fact stated here
stops the task rather than being guessed around.

- **Requests.**
  - OpenRouter: `POST https://openrouter.ai/api/alpha/decisions`, body
    `{model, state: context, questions}`, with `questions` an object keyed by
    question id; answers are an object keyed by question id, each tagged with
    its `type`.
  - OpenAI: `POST https://api.openai.com/v1/decisions`, body
    `{model, input: context, questions}`, with `questions` an array, each
    carrying a unique `name`; answers are an array matched by `name`.
  - Grimoire ids are sent verbatim unless a documented character set forbids
    one, and then positionally and reversibly, inside the adapter.
- **Question kinds map one to one.**
  - `predicate`: OpenRouter `noul` (no criteria) / OpenAI `predicate`; the
    answer is read from P(true).
  - `choice`: `choice`, with OpenRouter criteria `{key: description}` / OpenAI
    `choices [{value, description}]`; the answer is the explicit `choice`, else
    the argmax of `probabilities`.
  - `score`: `score`, with OpenRouter criteria `[descriptions]` / OpenAI
    `levels [{label: str(i), description}]`. **Both providers return a
    fractional, probability-weighted `score`, and neither has an explicit
    "none".** The answer is the argmax of the per-level probabilities, a tie is
    `abstained`, and with no usable probabilities it is `unreadable`; the
    weighted `score` is never rounded into an answer.
- **`allow_none` adds one reserved option.** Neither provider documents an
  explicit none (OpenRouter's tutorial recommends adding a `none` option,
  OpenAI's guide a fallback option such as `"other"`), so the adapter adds one.
  Its wire key is `none`, or `none_2`, `none_3`, … when an option already uses
  it; its description is a fixed sentence (`NATIVE_NONE_TEXT`); `NONE_KEY`
  (`"<none>"`) is its reserved key in a distribution. Choosing it, or a
  distribution whose argmax is on it, is `answer: None, reason: abstained`.
  A choice with `allow_none` that offers a single option (above) is therefore a
  two-option native choice.
- **Normalisation.** The provider's explicit answer wins (a choice's `choice`; a
  predicate's bool, which neither provider sends today); otherwise the argmax
  of its distribution, and a tie is `abstained`. A predicate's probability is
  P(true), and exactly 0.5 is `abstained`. Distributions are keyed by option id
  or level index, with `NONE_KEY` for the reserved none, and an invalid report
  (values that are not probabilities) is dropped,
  never repaired. A well-formed body whose answer is not an offered option, or
  is missing, is `None` with `unreadable` (`not_an_option` where it applies); an
  envelope that answers *none* of the item's questions is not an answer but a
  `bad_response` (§5.5), and one that answers some leaves the rest `unreadable`.
  OpenAI's per-answer `"type": "refusal"` is `refused` for that question;
  OpenRouter documents none, so none is mapped until a reference does; an
  answer of the wrong `type` is `unreadable`.
- **`confidence` is not carried, from either provider**: the contract has no
  field for it, and a synthetic confidence is forbidden above.
- **An item a decisions endpoint cannot represent cannot go native.** Today
  that is a choice with `allow_none` whose options plus the reserved none pass
  255 (a speaker roster of 254 refs plus `grimoire`); the slice adds any
  further documented limit. `decisions.native_gap` names it and the native call
  refuses it unsent with `LLMError("bad_response", <the sentence>,
  code="native_unrepresentable")`. It moves to a structured fallback stage when
  there is one; on a model that cannot generate with no such stage it fails
  with that reason, which reaches the caller and the error store.
- **Usage and price.** OpenRouter's `{input_tokens, output_tokens, cost}` is
  mapped explicitly, never through the chat parser; OpenAI documents no usage,
  so its rows carry no counts. A native row is priced only from what the
  provider reports (§9.3).
- **Concurrency.** `NATIVE_CONCURRENCY = 4`, argued structurally (a provider
  call per item, the continuity sweep sending one item per row) and to be
  tuned later against real prompts. The native items of one stage run in one
  `asyncio.TaskGroup`, so an unexpected exception or a cancel leaves no request
  running, and every meter opened files `aborted`. Results keep input order.
- **Health.** A native 4xx in `NATIVE_REJECTED_STATUSES` (`REJECTED_STATUSES`
  plus 403) does not mark the connection failing: the connection answered, and
  the decisions endpoint refused that model, key or request.

**The five conversions** (slices F and G):

| Call | Question(s) | What happens to the prose half |
|---|---|---|
| scene-break | `predicate` "is this scene over?" | the verdict commits first; only when a YES landed, a second guarded write follows a separate `generate` (`scene-break-title`, route `summary`, so the `summary` route's model, not the Decision role's) that drafts the title, read from the reply's first non-empty line (an explanation below it is not part of the title), cleaned (a quotation mark that opens and closes it stripped as a pair, so an apostrophe of the title's own survives, and trailing punctuation stripped) and capped, since a title is one frontmatter line shown in a chip. A YES therefore costs two calls and two copies of the transcript, and a title that fails leaves the verdict with an empty title. Verdict first means a read between the two writes sees a YES with no title: the inspector may show the proposal untitled until its next refresh. The reason becomes the optional rationale |
| voice-drift | `choice` drift / in_voice / not_enough | the note becomes the optional rationale (`explain` carries the corrective instruction); a native backend gives none and the verdict is shown without a note; today's `unknown` becomes `answer: None` and still counts as a failed check |
| speaker | `choice` over eligible refs, `allow_none` | none; the speaker keeps today's two issue strings, and control still returns to the player when nothing is readable. A roster `decide` refuses before sending is today's invalid handoff too: two refs that collide once normalised, or more than 254 refs (with `grimoire`, past the 255 options a choice may offer) |
| continuity-identity | one item per row, two questions: `decision` (a `choice` existing / new / uncertain) and `id` (a `choice` over that row's offered candidate ids, `allow_none`, a single candidate allowed) | the candidates are shown in the item's context and the one meant is answered as `id`; keeping today's two fields keeps today's two outcomes apart (an unknown word is `uncertain` and accepted, an unoffered id is `uncertain` and downgraded). Each item's context is self-contained (native backends send one request per item), and rows are chunked at 8 in one `decide()`, one metered call per chunk, each drawing on the absorb budget. A failed or garbled chunk beside answered ones leaves its rows `unchecked` (phase `degraded`, never `ok`), never `uncertain` (that covers an item the reply never reached; one answered badly is still read and stored `uncertain`); a row not `was_read` is unanswered, and a chunk error with nothing read is reported as that error. The trust rules are today's: answers become today's decision dicts for the unchanged `Examination.decide`, and the reason is display-only, stored `""` when none came back. `None` for every item is today's "undecodable"; an object holding no item (`{}`, today's format) is an empty answer. An offered id is read through the generic normalisation (a normalised-id widening toward a merge, a printed gate entry). Neither resolution is refused at request time: the identity phase reports `failed` and persist 1's findings stand, and the review shows the refusal's own sentence for `incapable` |
| continuity-reconcile | one item per candidate: `choice` over that candidate kind's vocabulary, plus `from` / `to` (`choice` A / B, `allow_none`) on the pair vocabularies only, and up to three nullable evidence choices (`evidence_scene`, `evidence_scene_2`, `evidence_scene_3`, `EVIDENCE_SCENES = 3`) over the scenes that item shows, each asked only when it shows at least that many | per-kind vocabularies become per-item option lists, labelled by their own word with the criteria carried whole in the question; temporal keeps today's fixed commitment-to-event direction. Evidence maps to a deduped, ordered list as today; only a reply citing four or more scenes is narrowed (the first three are stored). Each item's context is self-contained (the reconcile date, the item's own chronicle lines, the recent window and the question instructions repeat per item, so repeated input grows linearly with the candidates, which chunking bounds per call and not in total); option descriptions do not repeat the context. One `decide()` chunked at 8: a sweep of up to 24 candidates is up to 3 metered calls, plus one prompt-only re-send per route that refused the schema (so up to 3 per chunk with a fallback), each under the full reconcile ceiling, so a sweep can hold the campaign's background run up to 3x (9x with both routes of every chunk re-sent) as long as today, during which `PUT /config/data-dir` is refused and End Scene adopts rather than starts. A failed or garbled chunk beside answered ones leaves its candidates without a proposal (the sweep lands with `llm: "ok"` and `unanswered`, and the review shows a one-line partial-sweep note); nothing is stored `uncertain` for a candidate the reply never reached, and a cached candidate is asked again by the next sweep, while a model-only nomination in a failed chunk is not stored (as with today's whole-call failure). A chunk error with nothing read is reported as that error. The trust rules are today's: answers become today's reply elements for `_decide`, unchanged but for I4's rationale rule (a status word stands on its evidence scene alone). Neither resolution is refused at request time: the sweep lands with `llm: "off"`, and a model that cannot serve continuity shows the `incapable` sentence rather than "No model connection" |


**Eval gate.** Each conversion lands with recorded cases under `evals/` and
switches its call site only when the structured backend equals or beats
today's parse on them, offline. Native backends are measured with
`evals/run.py --live`, which spends money and is never automatic.

- The corpus starts from today's parser tests. Each entry pairs a legacy reply
  with a decide reply of the same shape, scored by the full call-site outcome
  (the value the caller acts on, not a parse in isolation).
- "Equals or beats" means *never loses an entry*. Today's parsers are frozen
  verbatim in `evals/legacy.py` (the selector's `validate_handoff` included) so
  the gate stays runnable after the switch, and the legacy parse tests are
  deleted only once a test holds that every one of their inputs is a gate
  entry.
- The permanent `decide-*` eval cases hold the prompt contract, and
  `verify_templates.py` holds that no criterion of the old prompt was dropped.
- **The gate's `judge` scores a whole batch that fits one call:**
  `Conversion.decide` takes every `ItemResult`, and a corpus entry's per-entry
  items are rejected. Slice G's reconcile legacy cases are adapted
  mechanically (candidate keys and runtime scene ids mapped to the fixture's),
  because today's tests build them at runtime; identity's are verbatim. Where
  an adapted case's intended outcome differs from legacy, the entry carries a
  `ruling`, which the report prints.
- `--live` resolves the Decision role and sends the schema, so it measures what
  production sends; replay measures the parser. Under slice H it runs each
  decide case through the chain on the whole resolution by default and grades
  `decisions.render` of the result. `--decide-backend native|structured`
  forces one backend on the resolved primary, which is how "native beats
  structured" is measured on one model (§16), and `--provider/--model` selects
  through `override_inference`, writing no settings. Every `--live` run spends
  money and needs the user's explicit approval.

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
| `n/a` | the operation takes no sampling (embed, native decide) — produced from slices D/H, when operations reach the controls API: the controls API takes `operation`, and a native attempt reports every control `n/a` | not sent | the panel says so |

`reasoning_effort` translations:

| Adapter | Wire |
|---|---|
| `openrouter` | `reasoning: {effort}`: `translated` when the cached catalog lists `reasoning`, `unsupported` when a cached catalog omits it, `unknown` (sent) when no catalog is cached |
| OpenAI preset | `reasoning_effort`, decided by the model id (source `name`; lowercased, after any `vendor/` prefix): a reasoning family (`o` and a digit — `o1`, `o3-mini`, `o4-mini` — `gpt-5*`, `gpt-oss*`) is `supported` and sent, with `off` `unknown` (nothing sent) as before; a non-reasoning family (`gpt-4*`, `gpt-3*`, `chatgpt-*`, and the `gpt-5*-chat*` chat snapshots, checked first) is `unsupported` (nothing sent, reported dropped — it would answer a 400 that reads as a preset refusal and skips the fallback), where `off` is honoured by sending nothing (`supported`); any other id is `unknown` and sent, unverified |
| `anthropic` | `low`/`medium`/`high` → `thinking: {type: "adaptive"}` + `output_config: {effort: <same>}` where the catalog says adaptive thinking is supported (current models reject `budget_tokens`); where the catalog lists `enabled` thinking instead, `budget_tokens` 1024 / 4096 / 16000, each held to at most half the effective `max_tokens` and at least 1024 (thinking is `unsupported` when that leaves no room); when the catalog states neither, nothing is sent and the control is `unknown`; `off` depends on the catalog's thinking types: on a model with adaptive thinking (whose unset default is to think, whatever else it lists) it is `translated` → `thinking: {type: "disabled"}` where the catalog says `disabled` is supported, `unsupported` (nothing sent) where it says `disabled` is not — that model's thinking cannot be turned off — and `unknown` (nothing sent) where it does not say; Claude Sonnet 5.5 is the one exception, which refuses `disabled` and takes `thinking: {type: "between_tools"}` as its off (no thinking before the reply; the API refuses it on every other model, so it is chosen by id), sent whatever the catalog's `disabled` says or omits (`translated`, source `adapter`: a row cached before `disabled` was read would otherwise leave it thinking); on a budget-only model, or one that takes no thinking at all, omitting `thinking` is off, so nothing is sent and it is `supported`; with no thinking types known it is `unknown`. `disabled` and `between_tools` are not thinking: they do not hold back sampling parameters. Sampling parameters are decided by the model id's Claude version, not by the thinking the catalog lists (Claude Opus 5 lists `enabled` thinking and still refuses them): they are sent only to an id naming a version below 4.7 (`claude-opus-4-6`, `claude-3-7-sonnet-20250219`) while no thinking is being sent, and `top_p` is not sent beside `temperature`; otherwise they are `unsupported` (source `adapter` for 4.7 and later, `unknown` for an id that names no version, such as `claude-mythos-preview`); `max_tokens` defaults to 16000 capped at the catalog's limit; `stop` is `translated` → `stop_sequences` |
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

1. **The provider reported a price** → the column its `cost_basis` names:
   `billed` → `cost_usd`, `equivalent` → `estimated_usd` (today's
   `claude_agent` reports the second). `billing` is a label and never a
   switch: `cost_basis` alone decides the column. A subscription-tagged row
   counts against a budget only when its provider reported a *billed* price,
   because then the provider said it charged; the tag changes what a surface
   says, never which column a figure lands in.
2. **Otherwise** rates × tokens → `modelled_usd`. Rates: the model facts'
   `rates` for (provider, model) first, then a `pricing.json` match (exact,
   then wildcard, then `""`).
   - Facts rates price only a row that names a provider (`provider_id`, §9.3).
     A row filed before that field has only `connection`, a display name that
     is neither unique nor stable, so it prices from `pricing.json` alone; no
     name is matched.
   - Facts rates are stated under the selection's model, so they are looked up
     by the row's `requested_model` when it has one. `pricing.json` matches
     `model` first, so an existing table prices exactly what it did (no older
     row carries `requested_model`), and then `requested_model`, tier by tier
     (every name's exact entry, then every name's wildcard, then `""`). The
     second name is what keeps the two chores agreeing (§9.2): configuration
     knows only the name asked for, so an entry under it must price the calls
     that answered as a dated snapshot of it, or the Housekeeping chore would
     clear while every call stayed unpriced. Both chores and every rollup ask
     the one function, `pricing.rate_for_call`.
   - `provider_id` is unique at any instant but not stable across a delete:
     a provider id is its name's slug, and a slug is reusable. Rollups price
     history at current rates, so deleting a provider (its facts go with it)
     sends its rows to `pricing.json` or to unpriced, and a provider created
     later under the deleted one's id prices that id's old rows with its own
     rates wherever the model strings coincide. The ledger has nothing else to
     tell the two apart, and this is accepted: only `modelled_usd` moves,
     never spend (`cost_usd`) and never a budget, because no rate touches a
     row its provider priced.
   - Writing rates is strict and reading them is fail-soft: `PUT …/facts`
     refuses a partial, invalid or unknown-field entry with 400 (`{}` clears),
     and a mangled file reads as no rates, falling back to `pricing.json`. A
     write never replaces a facts file it could not parse: a mangled file is
     refused with 409 `facts_unreadable` (a held one with 503), and the GET
     flags it `unreadable` so the panel offers no save.
   - A provider with `billing: subscription` tags the row `subscription`, and
     cost surfaces label those rows "subscription — not billed". Their modelled
     figure is arithmetic this side did and stays in `modelled_usd`. Such rows
     are a breakdown count rather than a figure: `modelled_subscription_calls`
     sits inside `modelled_calls` and `unpriced_subscription_calls` inside
     `unpriced_calls`. There is no subscription dollar figure, since a fourth
     one is one more thing to be added to the other three.
3. **Tokens** are the provider-reported counts when present. When a provider
   reports no count, Grimoire counts locally with `store/tokens.py` (tiktoken
   on desktop, the characters/4 heuristic on Android) and flags the row
   `tokens_estimated`. A row with estimated tokens is labelled as such wherever
   its figure appears.
   - Local estimation runs only for an attempt whose stream ended on its own,
     which the facade decides and the row's status does not: a route can file
     `ok` after breaking out early, and nobody knows what such a call billed.
     An aborted or failed call is the same, since an estimate would invent
     cost. Only the count the provider omitted is filled; image parts are not
     estimated, and reasoning text is counted as completion.
   - Estimated counts go in `prompt_tokens` / `completion_tokens`, flagged
     `tokens_estimated: true`, so every existing reader sums them and every
     rate can price them; the flag drives the labels, and buckets count
     `estimated_token_calls`. An older build reading a synced ledger sees them
     as counts.
   - An `embed` row with no completion count is priced with completion 0: an
     embedding generates nothing, so this is a structural fact rather than a
     guess, and no estimate ever fills an embed row's completion. Every other
     operation still needs both counts.
   - The facade counts, off the event loop: the counter is injected into
     `LLMClient` (the gateway imports no store), runs on a worker thread and is
     bounded by `COUNT_TIMEOUT_S`. `Meter.done` only reads numbers.
   - An embed call (slice D's door, `inference.embed`, and the model test's
     embed probe) that returned without a prompt count gets one estimated from
     its input texts, flagged the same way. It never starts an encoder load:
     an encoder already loaded counts, else the characters/4 heuristic
     (`tokens.count_if_loaded`). A failed or aborted embed is not estimated.
4. **No rates anywhere** → `unpriced_calls`, as today.

The three money columns and the "Estimated total" projection keep their
CLAUDE.md definitions; `modelled_usd`'s definition ("arithmetic this side
did") is unchanged and now also covers subscription calls priced from user
rates.

### 9.2 The Housekeeping chore

`routes/todo.py` gains "Models in use with no price" in the Housekeeping
group (`store/chores.py` only holds the ignore set): the count of distinct
`(provider, model)` pairs in use whose provider does not report prices and
which have no rates in model facts or `pricing.json`. It counts across the
global roles, their fallbacks and the chosen route pins, the Embedding role,
and each campaign's overrides of those, so it is library-scoped. It skips
dangling references, blank models and unchosen pins. "Reports a price" is the
provider preset's `reports_price`, so OpenRouter and the Claude subscription
never count; zero rates are a price. It is computed from configuration, never
from the ledger (the chores contract: a live, cheap count), and sits beside the
ledger-based `unpriced` chore: that one asks which recorded strings no rate
matches, this one asks which configured models nothing would price. Its action
opens that model's rates field.

### 9.3 Ledger rows

Each usage row keeps `task` and gains `operation`, `provider_id`,
`requested_model`, `model`, `preset`, `role` (when a role supplied the
selection), `decision_mode` (`native` | `structured`), `billing`,
`tokens_estimated`. Embed and decide calls are metered from their first slice
onward. `usage.Meter.done` remains the one place LLM failures are logged
(CLAUDE.md, Observability).

`operation` and `decision_mode` reach a row through the account block (§5.4):
the decide backend stamps the mode per call, on a copy of the block, so a
resolution is never mutated and a fallback attempt carries its own.

- A native row is priced only from what the provider reports (slice H).
  `cost_usd` is filed when the provider reports a cost (OpenRouter's
  `usage.cost`), and reported tokens are filed as reported. `modelled_usd` is
  never computed for a native row: neither provider is documented as billing
  decisions per token on both sides as chat is (OpenAI bills input only), so
  chat rates would mis-model it. Without a reported cost the row is unpriced,
  it never carries a local token estimate, and it is never read as zero.
- `provider` stays the adapter kind, as every existing row already writes it
  and an append-only ledger cannot change a field's meaning under rows older
  builds still read. The provider goes in `provider_id`, and `connection`
  stays the display name.
- `model` is what answered. `requested_model` is what was asked for, recorded
  only when it differs (a provider may name a dated snapshot of the model
  requested); it keys the model's rates, which facts state under the
  selection's model.
- `role` is the slot that supplied the resolution, on both attempts, after
  inheritance: a route whose role (e.g. Fast) has no selection inherits
  Primary's and files `role: "primary"`, since Primary is the slot that
  supplied it. It is absent for a pin, and absent when a per-call override
  changed the provider or the model, because then the user supplied the
  selection. A pinned route's
  fallback does come from `default_role`'s fallback (§5.5) but files no role:
  the row names the resolution's slot, not a separate guess at which slot the
  fallback came from.
- `preset` is the sampler preset actually sent: `conn["sampling"]["preset_id"]`
  on the attempted dict, after `fallback_sampling`. It is never the §6.1
  provider preset, which §5.4 names apart as `provider_preset`.

An embed call files **one row per `embed_sync` call**, covering all of its
batches. `operation` lands with slice D, which is first to write it; no row is
filed when nothing is sent (an empty input, or a deadline that lapses before
the first request, whether at the call's own check or inside the meter), and
no error either. When the caller's own budget (`budgeted=True`: absorb's
identity check, the continuity sweep) cuts a request that went out, the row is
`aborted` and nothing reaches the error store: the caller's clock is not a
provider failure. A deadline that only bounds the provider (lore recall's and
search's share one client timeout across a retry) cutting it is an error. A call whose later batch fails
after its request went out but before its body was read files its counts as
absent, never as the earlier batches' partial sum.

### 9.4 Capture

- `generate`: unchanged prompt capture.
- `decide`: the full request (contexts and questions), mode, normalised
  answers and distributions, through the same prompt-capture path and under
  the same Settings privacy disclosure. No chain-of-thought is requested or
  stored; `rationale` is a requested output, not reasoning text. The capture
  is split by slice. F owns the speaker's request capture (the decide prompt,
  with its contexts and questions). Scene-break and voice drift never had a
  capture and gain none, and neither do slice G's two continuity decisions
  (identity and reconcile; neither captured before): per-check entries would
  compete in the prompt log's Turn-history rail with the turns the reader
  inspects, and `taskLabel` has no label for them. H owns mode, normalised answers and distributions (in F the
  mode is always `structured`, which the ledger already records, and
  distributions exist only natively), under these rules (slice H):
  - **The capture runs after each call settles**, as `(messages, outcome,
    conn)`. The messages are the request as sent: a structured chunk's
    messages, or a native item's normalised body as one JSON message. They are
    built only when a capture is given, and off the loop.
  - The outcome is `decisions.outcome`, recorded as a zero-token `decision`
    section. A native stage's conn is captured without `sampling`.
  - The capture runs outside the meter and guarded, so it can never fail an
    answered decision or file an error row.
  - One structured call is one capture, its schema-refusal retry included. A
    failed or timed-out call is captured with its error. A cancelled call is
    not: a cancel is the player's own act, and a write during cancellation would
    itself be cancelled or delay it.
- `embed`: one Debug-level line per call that sent a request, through the one
  writer (a call that sends nothing writes none). It carries the counts
  (`inputs`), `bytes`, `dimension`, `space_id`, and cache hits (`cached`) and
  misses (`uncached`) named apart from `inputs`, plus the campaign and scene
  when given and, on failure, `error`: the error kind, or `aborted` when the
  caller's own budget cut the request. Hits and misses count a caller's run,
  so they ride on the run's first call only; a retry or a later chunk of the
  same run carries neither. **Embedded
  text is never captured or logged**, nor a provider's message or a URL. An
  embed failure's recorded detail is its kind and HTTP status only.

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
  generating checks behind a button with confirmation, which the server
  enforces: the Claude-subscription check is refused without `confirm: true`,
  §6.5), catalog age +
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
- **The Embedding card asks before it changes.** Changing the Embedding role
  re-embeds the library through the chosen provider, which may cost money —
  and slice C is what makes paid OpenRouter embeddings selectable — so the
  card states that and waits for confirmation, and the write carries
  `confirm_embedding: true`. The server enforces it (rule 1): a change of the
  Embedding provider or model to a non-empty value without it is refused
  (§12). Clearing the role, or rewriting the value it already has, needs no
  confirmation. The provider's own Edit form asks the same question when the
  edited provider is the one the Embedding role embeds through and the edit
  would restamp its `rev` (a key or an address): `PUT /llm-connections/{id}`
  takes `confirm_embedding` too and refuses such an edit without it (§7.3). An
  edit after which the role embeds nothing asks nothing.
- **Routing** (collapsed, "Advanced"): 15 rows — "Role ▾ (default: Fast)" or
  "Specific model…", plus the preset override, each labelled "Inherit
  (resolves to …)". The label comes from the settings view's `inherits`
  answer, which the backend resolves with the scope's own choice for that row
  silenced — what the row would run on if it were cleared, never a frontend
  re-derivation.
- Each role card and route row carries the backend's `problem` for it (no key,
  a known capability `no`), which is the seam's own refusal decision (§12),
  not a copy that could drift from what a turn would be told. The Embedding
  card's `problem` also names a known `no` for `embed`, which switches
  embedding off (§7.3).
- Capability warnings inline on the card or row concerned:
  - "This model can't generate text, so it can't be Primary."
  - "This route sends images; the chosen model is unverified for vision."
  - "No native decision API; structured generation will be used."
  - "This model can't create embeddings."

  The decide warning is keyed on the resolution's `decision_mode` (slice H):
  native; structured on a model that could also decide natively; structured
  with no native API; or the refusal's own sentence. The frontend keeps no
  capability rule of its own.

**Presets editor**: today's `SamplerPresetEditor`, renamed, with
`reasoning_effort` and a **Preview on…** model picker rendering §8's states.

**Settings (`ConfigView`)**: the Connection, Model routing and Embeddings
sections are replaced by a summary card (the four roles at a glance, links to
`/models` and `/providers`). Retries, timeouts, semantic recall depth and
threshold, and image sending stay. `embeddingsOn.ts` is deleted; the
"Embeddings" chip reads the backend's resolved answer.

**Status bar and `GET /config`**: at format 2, `GET /config`'s
`active_connection`, `ready` and `health` describe what chat would run on —
taken from the resolve that `_public_config` already makes — rather than the
legacy active connection. The status bar and the frontend's model cache read
those fields today, so they follow the resolver with no frontend change. The
roles summary the response carries for the Settings card is pure: it reports
the stored roles and makes no resolve calls of its own.

**Campaign Inspector**: "Model routing" becomes **Models** — campaign role
overrides (Primary, Fast, Decision) and route overrides, with inherit labels.

**Reroll override** (`RerollRoute`): provider ▸ model (Fits list for
`generate`) ▸ preset.

**Setup wizard**: Storage → **Provider** (preset, key, free check) →
**Models** (Primary required; Fast and Decision default to "Same as …";
Embedding optional with one line on what it enables) → Look → World.

**Newer-format banner** (§11) wherever model settings are shown, and an
**upgrade-pending banner** while the migration is pending or failed (§11.2):
it gives the status and, when the safety backup failed, the reason; the
new-layout editors cannot save until the migration completes (409
`not_migrated`), and play continues through the translation meanwhile.

---

## 11. Migration and compatibility

### 11.1 Translation, then persistence

`store/inference/translate.py` is a pure function from the legacy layout to
the new one. While `inference_format` is absent, the resolver reads the store
**through it**, so the app is correct before, during and without migration.
Migration persists that same translation **verbatim** — padded ids, dangling
references and `PRESET_CLEAR` stay exactly as `translate.global_view` /
`campaign_view` give them — **plus the enrichments in §11.2** that a read-time
translation deliberately does not make (provider presets and billing, model
facts, an unset Claude model written as `opus`): those change what a later
edit starts from, not what resolves today. Every derived value is
deterministic.

**The layout is decided once, globally.** A campaign's own `inference_format`
is honoured only when `config.md`'s is current; a campaign marker alone never
switches that campaign to the new keys, so a store migrated by a newer build
and opened by an older one resolves every campaign from the same frozen
legacy state.

**Campaign keys need the campaign marker.** At format 2 a campaign without
its own marker still resolves through the translation: it was skipped as busy
(§11.2 step 8), forked from one that was, or created by an older build on a
synced device. So the marker is never written over legacy overrides that were
never translated:

- `set_campaign_inference` stamps the marker when the global layout is
  current, and a campaign created at format 2 is stamped at creation.
- A write to **any unmarked** campaign migrates that campaign first (step 8's
  work, for that one campaign), in the same `campaign_lock` hold as the write.

**A fresh store is born at format 2.** A store with no `config.md` is never
migrated: there is no backup of an empty install, and nothing is written
before the wizard's Storage step has said where the store lives. This build's
first materialisation of defaults writes `inference_format: 2` with them, and
the wizard's Provider and Models steps write the new layout directly.

### 11.2 Steps (global; once; idempotent; resumable)

**When it runs.** The migration runs in the background, never on the play
path and never inside a request. Startup schedules it, and `PUT
/config/data-dir` reschedules it for the new root. It holds a migration lock,
so two starts never interleave, and `main.start()` holds
`maintenance_excluded` around it — the run exclusion lives in `main.py`, never
in a store module (CLAUDE.md), so a backup, fork or image-store run cannot
walk the tree while it is rewritten. A data-dir switch mid-run writes no
marker: the run belongs to the root it started on. While the migration is
`pending` or `failed`, play resolves through the translation and every
new-layout settings write answers 409 `not_migrated` with the status — a write
into a format-1 store would be ignored. Tests turn the automatic start off
(`GRIMOIRE_INFERENCE_AUTOMIGRATE=0`) and call `ensure()` themselves.

`llm_connections.ensure_migrated()` — today's format-1 seeding of the named
connections — runs first. Every resolve already runs it, so it is not a
migration write. Then:

1. **Backup** — `backups.create_backup()`, named `pre-inference-…`.
   Retention never prunes it: rule 3's "the user can go back" depends on it
   existing. On failure (an I/O error, or the store busy) nothing is written;
   Settings shows "Upgrade pending: the safety backup failed (reason)"; play
   continues through the translation; the next start retries. A store already
   at format 2 with only campaigns left to finish takes no new backup.
2. **Providers** — each connection file gains `preset` and `billing`,
   **keeping its `rev`** (a `keep_rev` write), so catalogs, health verdicts
   and vector caches survive. Preset inference: `openrouter` → OpenRouter;
   `claude` → Claude subscription (`billing: subscription`);
   `openai_compatible` by `base_url` → OpenAI (`api.openai.com`), z.ai
   (`api.z.ai/api/paas`), z.ai Coding Plan (`api.z.ai/api/coding`, billing
   subscription), Ollama (port 11434), LM Studio (port 1234), else Custom.
3. **Model facts** — each connection's `vision`, `prefill`, `post_process` →
   `facts.json[<its model>]`, which is what the format-2 lowering reads
   (§4.2).
4. **Presets — none derived; the legacy GLM effort keeps applying.** A
   connection's legacy `reasoning_effort` is not turned into a preset in this
   slice. It keeps applying, at both formats, whenever the effective preset
   sets no `reasoning_effort` — slice B's rule — until slice I. Deriving
   presets here would change every observed preset (id, name, params) and buy
   no change on the wire; and `glm_effort` matches by model name regardless of
   kind, so a derived preset on an OpenRouter GLM connection would start
   sending reasoning that connection never sent. Slice I derives them when it
   stops reading the legacy field (§14): for `openai_compatible` GLM
   connections only, and only for values a preset can represent — its own
   sampler preset's params plus that effort, named "<preset name> ·
   reasoning <effort>" (or "Reasoning <effort>" when it had no preset), with a
   deterministic id (`slugify` of that name); identical derivations collapse
   to one.
5. **Roles** — Primary = the active connection (its model, or `opus` for an
   unset Claude model) with its own sampler preset. Fast and Decision unset
   (inherit). Embedding = `embeddings_connection_id` + `embeddings_model`
   **only when the legacy configuration actually embeds**
   (`embed_space.resolve` answers non-None); a legacy choice that does not —
   an OpenRouter connection, which had no embeddings route, or a known `no`
   for `embed` such as a z.ai connection (its preset's hard `no`) — leaves the
   role unset, so it stays off as §6.1 promises. User-visible: from slice D a
   legacy z.ai choice is already off at format 1 (it sends no request that
   could only fail), and after the switch the Embedding card shows no
   selection rather than the old one. `fallback_connection_id` → the
   fallback of Primary, Fast and Decision.
6. **Routes** — global `route_<k>=<conn>` → `use_<k>=model` +
   `use_<k>_{provider,model,preset}` from that connection. `preset_<k>`
   untouched. Unset routes stay unset; they resolve through their default role
   to Primary, which is the old active connection — today's behaviour.
7. **Split routes** — `speaker`, `scene_break`, `voice_drift` copy the
   `use_*` and `preset_*` of `scene`, `summary`, `voice` respectively.
8. **Campaigns** — steps 6–7 inside each `campaign.md`, one campaign at a
   time under that campaign's own `campaign_lock_nowait(cid)` — never two held
   (`test_lock_order_guard` applies) — stamping the campaign's revision token
   (`revision.bump(cid)`) in the same hold and writing the campaign's own
   `inference_format: 2`. A busy campaign is skipped and retried on the next
   `ensure()`; until then the translation answers for it, and a write to it
   migrates it first (§11.1).
9. **Marker last** — `inference_format: 2` in `config.md`.

Two devices migrating one synced store concurrently write identical content
(every derived value is deterministic); the second write rewrites the same
bytes.

### 11.3 Older and newer builds

- **Older builds** keep running on the state as of migration: legacy keys and
  fields are left in place, frozen. Changes made in the new UI do not reach
  them.
- **Legacy keys are refused at format 2.** A write of a legacy inference key
  (`active_connection_id`, `fallback_connection_id`, `route_*`,
  `embeddings_connection_id`, `embeddings_model`) or a legacy connection field
  (`model`, `post_process`, `reasoning_effort`, `sampler_preset`, `vision`,
  `prefill`) is a 400 once the store is at format 2: it would reach older
  builds only and change nothing here. At format 1 they stay writable — that
  is the layout the app still reads.
- **Deletes sweep global references.** Deleting a provider clears the global
  roles, fallbacks and pins that name it; deleting a preset clears the global
  role, fallback and pin presets that name it, the same way. Campaign
  references dangle and are walked past (§5.1).
- **Newer formats**: from this release on, a build that reads an
  `inference_format` higher than it knows shows "This library was upgraded by
  a newer Grimoire" and refuses model-settings writes with 409
  `newer_format`, so it cannot overwrite settings it does not understand. Play
  continues on what it can read, and it never migrates that store.

### 11.4 Retirement (slice I)

For stores at format 2: derive the reasoning presets §11.2 step 4 describes
(the legacy GLM `reasoning_effort` stops being read here, so it must become a
preset first), delete the legacy config keys (`active_connection_id`,
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
| New-layout settings write while the migration is pending or failed | 409 `not_migrated`, carrying the migration status |
| Legacy inference key or legacy connection field written at format 2 | 400 (§11.3) |
| Embedding provider or model changed to a non-empty value without `confirm_embedding: true` | 400; nothing written (rule 1) |
| A provider edit that moves the Embedding role's vector space (a `rev` restamp on the provider it embeds through, after which it embeds, judged as the provider will read once saved: a catalog row or probe verdict the restamp leaves stale says nothing) without `confirm_embedding: true` | 400 `confirm_embedding`; nothing written (rule 1, §7.3) |
| A model-facts write that turns the Embedding role on (the user's `embed: yes` over a known `no` for the model it embeds with) without `confirm_embedding: true` | 400 `confirm_embedding`; nothing written (rule 1, §7.3). A passed test call and a catalog refresh are not the user's settings writes to the role and can lift a `no` unasked; a failed test cannot (§6.2) |
| Claude-subscription health check without `confirm: true` | 400; nothing sent (rule 1) |
| Migration backup failed | no write; Settings banner ("Upgrade pending: the safety backup failed (reason)"); translation serves; the next start retries |
| Decide question unanswerable | `answer: None` + `reason`; never a guessed default |
| Embedding provider fails | caller degrades as today; no fallback; one metered error row per `embed_sync` call that sent a request, its detail the kind and HTTP status only |
| Test call fails | a refusal of the probe itself is recorded in `verified` with the provider's error text and resolves as `unknown`, so the row stays "unverified" with that error shown and the call is never refused for it; transient failures (rate limit, outage, credits or a spend limit, auth, transport) are reported and not recorded |

Reads fail soft (a mangled `facts.json`, catalog or preset reads as empty);
writes fail visibly.

**One refusal decision.** Whether a resolution can serve — a missing key, a
known capability `no` — is decided once, by `inference.refusal`. The seam
raises it as the 409s above, and the settings view reports it as a role's or
route's `problem` (§10): the seam's answer, not a copy that could drift from
what a turn would be told. Likewise, an Embedding model that is a known `no`
for `embed` (§5.3) switches embedding off rather than refusing a turn, and the
Embedding card's `problem` says why. A decide route row's `problem` is
that same refusal (§5.3): slice H deletes the skip notice, because a model that
cannot generate is served natively, so a row has a refusal or nothing.

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
| **C — The switch** | New storage writes, migration (§11), the facade taking each call's per-role fallback (re-resolved per generation, as the global one is today), retirement of the second cascade the legacy routing UI reads (`routing.resolve`/`bundle`, `sampler_presets.resolve`/`inherited`) in favour of the resolver, the newer-format guard, `/providers`, `/models`, Presets editor with reasoning and Preview on…, Settings summary card, Inspector Models, reroll override, wizard, capability warnings, test-call UI, dropping an incapable fallback attempt (§5.3; B reports it); legacy settings UI removed. Also: the format-2 lowering overlaying model facts (§4.2); the migration run in the background with 409 `not_migrated` until it completes, the never-pruned `pre-inference-` safety backup, campaign markers, and fresh stores born at format 2 (§11); legacy keys refused at format 2 (§11.3); one refusal decision shared by the seam and the settings view (§12); the server-enforced confirmations for the Claude health check and for an **Embedding role change** (`confirm_embedding`, §10) — C makes paid OpenRouter embeddings selectable, so rule 1 cannot wait for D | **Yes** |
| **D — Embedding operation** (settled) | `embed` / `embed_sync` in `store/inference/embed.py`, embed tasks (`routing.EMBED_TASKS`), metering (the confirmation on an Embedding-role change already landed in C and stays), and one reader of the Embedding role, `resolve.embedding` (today `translate.embedding_role` serves `embed_space` while `cascade.role_selection("embedding")` is unused) | Small |
| **E — Pricing** | Ledger fields, rates in model facts, subscription tagging, local token estimation + flag (D's embed rows included: E stamps their account fields and estimates an unreported prompt, §9.1), the Housekeeping chore | Yes |
| **F — `decide()`** (settled) | The contract (`decisions.py`), `generate(schema=)`, the structured backend, the decide skip of §5.3, scene-break / voice-drift / speaker converted behind the eval gate; those routes' `default_role` flips to `decision` | Decision role in use |
| **G — Continuity decisions** (settled) | continuity-identity and continuity-reconcile converted behind the eval gate; `continuity.default_role` flips to `decision`. Both conversions switch in one change, because they share the `continuity` route and the safety rule flips a route only where its call sites decide; neither is refused at request time (both resolutions stay soft) | the Decision role serves the duplicate check and the continuity sweep; a partial sweep says so |
| **H — Native decisions** (settled; lands after G; its native chain serves G's continuity items) | OpenRouter and OpenAI decision adapters; the chain of §5.5 (the selection's one backend, native only for a model that cannot generate, then the role fallback in one attempt; the native backend stamps `"native"`, replaces F's skip and widens `OPERATION_CAPABILITY["decide"]`); §9.4's capture of mode, normalised answers and distributions; voice drift's native "verdict without a note" branch; `--live` evals with `--decide-backend`; the Decision role card and its routes read one refusal (the skip and `skip_text` are deleted, §5.3); and strict mode's schema limits beyond the 1,000-value enum budget, enforced for every backend (§7.4). OpenRouter's `provider.require_parameters` stays out (the schema is always in the prompt, so routing to a provider that ignores `response_format` is harmless) and is left to I | Opt-in |
| **I — Retirement** | §11.4, including the derived reasoning presets of §11.2 step 4 (`openai_compatible` GLM connections only, representable values only) as the legacy GLM `reasoning_effort` stops being read; the adapter registry, with `inference.generate`; deletion of the connection-dict lowering, with `FALLBACK_KEY` and `STRUCTURED_KEY` | No |

**Safety rule across slices**: a route's `default_role` stays `fast` until
its tasks call `decide()` (F/G). Pointing the Decision role at a decide-only
model during C–E therefore cannot break any task; nothing uses that role yet.
After F, a decide-only Decision model is skipped for a generating fallback
(§5.3), and refused only when no fallback can generate; after H it is served
natively, and refused only when it is known unable both to generate and to
decide natively. The flip has one
consequence for campaign overrides, noted in §5.1: on a decide route a
campaign's Fast override no longer outranks a global pin.

### 14.1 Guards and docs

- `test_routing_guard.py`: follows `require_inference`; fails an operation
  mismatch between call site and route; knows the registered non-route tasks
  (`model-test`) and embed tasks.
- New guard, `test_operation_guard.py`: every `embed`/`embed_sync` names a
  registered embed task, its `space=` traces back to `embed_space.endpoint`
  (the Embedding role's one reader, §7.3), and only the operation and the
  model test reach the embeddings client (slice D); every `inference.decide` names a task on a
  `decide` route (slice F adds that half to the same file, and appends to the
  same `CONTRIBUTING.md` row). The decide half resolves import bindings
  (D's alias resolution, generalised to a target module) and never matches a
  method by its name alone, because `decide` is a common method name.
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
reasoning + sampler preset (no preset derived; the legacy GLM effort still
sent, §11.2 step 4); a legacy OpenRouter embedding choice (role left off);
local Ollama; z.ai and Coding Plan URLs; dangling route; dangling fallback; a
busy campaign (skipped, translated, migrated next run, or migrated by its
first write); a failed backup (nothing written); a fresh store (born at
format 2, never migrated); `rev` preserved; **behaviour equivalence for every
task**, before and after — against both frozen fixtures, the second of which
(`inference_baseline_c.json`) records the GLM, post-image, prefill and
legacy-embedding states the first never built; idempotence (running twice
changes nothing); newer-format refusal.

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
`llm_fakes`/cassettes, never live); a native failure → the role fallback in one attempt, a model that can also generate staying structured;
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
  before they win on evals. Slice H serves native only a model that cannot
  generate; "native first for a model that also generates" is recorded as a
  later user decision, gated on `evals/run.py --live --decide-backend`
  measuring both backends on one model.
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
- **OpenRouter Decisions (checked 2026-10-08)**, re-read by slice H before
  its adapter was coded. Two sources: the **API reference**,
  https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
  (`DecisionsRequest` / `DecisionsResponse`), and the Jev **tutorial**,
  https://openrouter.ai/blog/tutorials/how-to-use-jev/, which is a tutorial
  and not a reference. Every ruled fact held:
  - `POST https://openrouter.ai/api/alpha/decisions` (the operation overrides
    the `/api/v1` server), `Authorization: Bearer <key>`, any OpenRouter key;
    body `{model, state, questions}` (all required), `questions` an object
    keyed by question id, each question discriminated by `type`, with its
    instructions under `instructions` (required).
  - `noul`: `criteria` optional (when sent it must hold both `"true"` and
    `"false"`, so Grimoire sends none); answer `{type, noul}`, `noul` a number
    (the tutorial: the probability of true).
  - `choice`: `criteria` an object, option name to description; answer
    `{type, choice (required string), confidence, probabilities}`, the
    probabilities keyed by option name.
  - `score`: `criteria` an ordered array; answer `{type, score (required,
    probability-weighted), confidence, legend, probabilities}`, the
    probabilities keyed by zero-based level index as strings.
  - Response `{id, model (a dated build), provider, answers, usage}`, answers
    keyed by question id; `usage {input_tokens, output_tokens (required
    integers), cost (optional number)}`.
  - **No refusal answer type** in either source, so none is mapped.
  - **Errors**: 400, 401, 402, 403 ("authenticated but insufficient
    permissions"), 404, 413, 429, 500, 502, 503, 524 and 529, each with the
    body `{error: {code, message, metadata}, user_id}` -- the chat API's error
    shape, so `_extract_error` reads it unchanged.
  - **Ids**: no character set or length is documented for a question id or a
    criteria key, so Grimoire ids are sent verbatim.
  - Model id at the time `typesafe/jev-1.13` (`~typesafe/jev-latest` tracks
    the newest release).

  Still open: the 255-option limit per choice and the 2–10 levels per score
  are the tutorial's only (the reference sets a score's `criteria` at
  `minItems: 1` and bounds neither); no limit on questions per request or on
  the `state`'s length is documented in either source, so `native_gap` names
  no further limit (a 413 is the only sign one exists); the documented
  optional `provider`, `session_id`, `trace` and `user` fields are not sent;
  and the meaning of 403 for a key without alpha access is inferred from its
  description, not stated.
- OpenAI Decisions: `POST /v1/decisions`, `{model, input, questions}`, with
  `questions` an array, each with a unique `name`; question types `predicate`
  (`probability`), `choice` (`choices`; `choice`, `probabilities`,
  `confidence`) and `score` (`levels`; a fractional weighted `score`,
  `probabilities`, `confidence`); answers an array matched by `name`, with a
  per-answer `"type": "refusal"` for a refusal; the guide recommends a fallback
  option such as `"other"` and documents no explicit none; no usage shape is
  documented; public beta, `gpt-6-luna` at the time.
  https://developers.openai.com/api/docs/guides/decisions
- z.ai: pay-as-you-go `https://api.z.ai/api/paas/v4`; GLM Coding Plan keys work
  only at `https://api.z.ai/api/coding/paas/v4`.
  https://docs.z.ai/api-reference/introduction

These are planning-time facts (the two decisions pages re-read 2026-10-08 for
slice H). The slice that implements each adapter re-checks its reference before
coding, and records an open point here.
