# 01i. Context window as a resolved model fact

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 01i in `ROADMAP-CHECKLIST.md`. Lane: retrieval (feeds 09, 10, 12).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `01-inference-backend-refactor.md` ("Provider
model catalogs should populate discoverable metadata such as context size",
line 195), which 01 landed only as far as the catalog row. It also extends the
landed 01 spec (`2026-10-07-inference-backend-refactor-design.md`, section 4.2
"Model facts" and section 5.4 `ResolvedInference`), and fits the planned
Models page of 01s (`2026-10-09-inference-settings-group-design.md`,
section 3.1).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, check whether 01s has
> landed. Section 6.3 adds one field beside its `rate`, and it must follow
> whatever shape `rate` landed in.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| Model facts (`store/inference/facts.py`), the cached catalog row (`llm_connections.cached_row`), `Attempt` / `_target` in the resolver | 01 (landed) | The two sources the fact resolves from, and the place it rides | Hard, and met |
| The Models page summary, with a per-row `rate` (01s, section 3.1 and 3.5) | 01s (planned) | The row the window readout sits on | Soft: without 01s the readout lands on today's `ModelsView` role cards |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01i-C1 `limits` on every resolved attempt | 09, 10, 12 | Knowing the window and output cap of the model a call will run on |
| 01i-C2 `prompt_ceiling(resolved)` | 09 (09-C3), 10, 12 | Sizing a prompt section or a planner or agent input against the real window, with the reply reserved |
| 01i-C3 the user's stated window, and where it shows | 09, 12 (indirectly) | A local server whose catalog says nothing still gets a ceiling |

## 1. Current state (reconciled against main)

**The catalog knows a window, and nothing downstream reads it.**
`catalog.entry` stores `context` for every row (`catalog.py:31`):

- OpenRouter's `context_length`, or vLLM's `max_model_len`, through
  `_context` (`catalog.py:152-171`). llama.cpp's training length is
  deliberately not read, and Ollama names none.
- Anthropic's `max_input_tokens`, through `_anthropic` (`catalog.py:110`).

A missing value is `None`, never `0` (`catalog.py:9-11`). The Anthropic row's
`max_tokens` (the most `max_tokens` a request may ask for) is kept only as
`features.max_tokens` (`catalog.py:130-132`). Its one reader is
`llm_sampling._anthropic_max_tokens`, which caps what the Anthropic API is
sent (`llm_sampling.py:380-387`). OpenRouter's
`top_provider.max_completion_tokens` is not read at all. The OpenRouter
models reference describes it as "Maximum completion tokens from the top
provider. Input and output tokens share the context window, so the effective
maximum output for a request is further limited by the context remaining
after input tokens" (checked 2026-10-09). The `claude` kind lists no catalog
(`adapters.py:244-268`), so its models have no window from any source.

**The resolver already reads that row for every attempt and drops the
window.** `_attempt` reads the catalog row once (`resolve.py:492`) and the
model's facts once, and builds the `wire.Target` from both (`_target`,
`resolve.py:422-461`). The target keeps `model_params` and `model_features`
from the row, but not `context`. `Attempt` (`resolved.py:36-80`) has
`facts`, `capabilities` and `controls`, and no window.

**Model facts hold nothing about size.** A facts entry is `vision`,
`prefill`, `post_process`, `rates`, `verified` and `overrides`
(`facts.py:7-10`, `_view` at `facts.py:165-185`). The panel's write is
`facts.state` (`facts.py:347`), reached through `PUT
/llm-connections/{id}/facts` (`routes/config.py:1677`, body `FactsUpdate` at
`routes/models.py:131-147`). A window is not a capability: `capabilities.NAMES`
(`capabilities.py:61-62`) is a yes/no/unknown vocabulary, and a window is a
quantity. So it cannot ride the `overrides` map.

**The packer's budget is a hand-set number.** `pack.budget_tokens()` reads
`context_budget` from `config.md`, where 0 (the default,
`store/config.py:35`) means unbounded (`pack.py:124-131`). The packer's
docstring explains why: "The backend cannot infer the number: only the
frontend sees the model list that carries each model's window size"
(`pack.py:69-73`). Since 01, that is no longer true. The backend caches every
provider's catalog and reads it on every resolution. The budget is read at
`assemble.py:1445` and `:1529`. It is reported as `budget_tokens` in every
breakdown (`assemble.py:1932`, `routes/character_turns.py:673`).

**The only consumer of the window is the inspector, client-side.**
`ContextBreakdown.contextLimit` (`frontend/src/components/ContextBreakdown.tsx:147-163`)
looks the breakdown's `model` up in the cached catalog it fetched, and draws
the bar against the smaller of that window and the packer budget. This misses
three cases:

- a model whose provider lists no catalog (`claude`, Ollama);
- a catalog that was not cached;
- any correction the user would want to make.

## 2. Goal (and what is explicitly not the goal)

Make the window, and the output cap where known, a **resolved fact with
provenance** on every attempt, so a consumer reads it the way it reads a
capability: off the resolution it is about to send, never by reading the
catalog itself.

- The window and output cap resolve from the user's stated word, then the
  provider's own catalog row, then unknown (01i-C1).
- One helper turns a resolution into the token count a prompt may hold, with
  the reply reserved, across every target the call may be sent to (01i-C2).
- The user can state either value per model on its provider, and the window
  shows where models are chosen and where prompts are measured (01i-C3).

Not the goal:

- **No change to what any prompt contains today.** `context_budget: 0` still
  means unbounded, and the packed prompt stays byte-identical. Whether the
  packer should default to the resolved window is Open question 1.
- No per-model table shipped in the repo, no name rule ("a model called `*-32k`
  has 32k"), and no parsing of a provider's error prose for a limit.

## 3. The fact

### 3.1 Shape

Both types live in `wire.py` beside `Target`. That module is the gateway's own
leaf, so an adapter or a capture that holds a target can read its limits
without importing the store.

```python
LIMIT_SOURCES = ("user", "catalog", "unknown")

class Limit(NamedTuple):
    value: int | None          # positive tokens, or None when unknown
    source: str                # one of LIMIT_SOURCES; "unknown" iff value is None

@dataclass(frozen=True)
class Limits:
    window: Limit = Limit(None, "unknown")       # prompt + reply share it
    max_output: Limit = Limit(None, "unknown")   # most a reply may be asked for
```

`wire.Target` gains `limits: Limits = Limits()`. `Attempt` gains a read-only
property `limits` that returns `self.target.limits`, so the fact is "on the
resolved attempt" (the checklist's wording) without a second copy that could
disagree. A target built by hand (`resolved.UNBUILT`) reads unknown.

**The window is always read as shared by prompt and reply.** OpenRouter says
so of `context_length`. vLLM's `max_model_len` is the server's whole sequence
length. Anthropic names its field `max_input_tokens`, but its docs count a
reply against the context window too. Treating all three the same way can
only err toward a smaller prompt ceiling, which is the safe direction for a
ceiling. `pack.MESSAGE_OVERHEAD` (`pack.py:105-115`) uses the same reasoning:
round high when bounding. A per-source "input-only" flag would buy a few
thousand tokens on one provider at the price of a second semantics nobody
could see. It is not added.

### 3.2 Resolution: `store/inference/limits.py`

This is a new pure module, `limits.of(row, model_facts) -> wire.Limits`. Each
of the two values takes the first source that states it:

1. **`user`**: the model's facts, `context_window` / `max_output` (section 6.1).
   This is the user's word about the model on this provider: for example, "this
   llama.cpp server was started with `-c 8192`", a value no catalog field can
   tell.
2. **`catalog`**: the provider's own listing for the model, as cached for the
   connection's current `rev`:
   - `row["context"]` for the window;
   - `row["max_output"]` for the cap (section 3.3).
3. **`unknown`**: `Limit(None, "unknown")`.

The user's word outranks the catalog for the same reason it outranks it for
capabilities (`capabilities.py:8-23`): it is a statement about this model on
this provider, which the user is placed to know and the listing is not. It is
a statement about the model, not a preference. A smaller prompt *by choice*
remains `context_budget`'s job, and the panel says so (section 6.2).

`_attempt` calls `limits.of(row, model_facts)` with the row and facts it
already read (`resolve.py:492-498`). `_target` gains a `limits` parameter, so
`target_for` and the resolved attempt still share one builder
(`resolve.py:508-534`). **No new read happens on the turn path.**
`embed_attempt` with `catalog=False` (`resolve.py:1043-1063`) passes
`row=None`, so an embedding attempt being judged for a pending edit reads
only the user's stated values. That is correct: the cached row belongs to the
old rev.

**Rev.** A catalog value is already rev-gated, because the sidecar is
(`llm_connections.cached_row`, `llm_connections.py:724-740`). A stated value
survives a rev change, as every stated fact does (`facts.py:22-23`). This is
deliberate. A new key does not change what the server was launched with, and
a stated value is shown beside the catalog's so a disagreement is visible
(section 6.2).

### 3.3 The catalog's output cap

`catalog.entry` gains `max_output` (a positive int, or the key left absent)
from two sources:

- OpenRouter: `top_provider.max_completion_tokens`;
- Anthropic: `max_tokens`, which is read as today and is also left in
  `features.max_tokens`. `llm_sampling` keeps its reader
  (`llm_sampling.py:384`), so nothing about what the Anthropic API is sent
  changes.

It is read with the same `_positive_int` rule (`catalog.py:93-94`). It is not
read from `per_request_limits`: that is a limit on the key, not a fact about
the model. A sidecar cached before this change has no `max_output`, so the
model reads `unknown` until the next catalog refresh. **No migration**: the
sidecar is a cache, and it already refreshes on `rev`.

## 4. 01i-C2: the prompt ceiling

```python
@dataclass(frozen=True)
class Ceiling:
    tokens: int | None        # what a prompt may hold; None when no target's window is known
    window: int | None        # the window that bound it
    reserve: int              # the reply reservation subtracted from it
    complete: bool            # every target on the chain had a known window
    binding: str              # "<provider_id>/<model>" of the target that bound it ("" when none)

def reply_reserve(target: wire.Target, controls: dict) -> int: ...
def prompt_ceiling(resolved: ResolvedInference, *, reserve: int | None = None) -> Ceiling: ...
```

**`reply_reserve(target, controls)`** reserves the tokens a reply on that
target may take:

1. The `max_tokens` the attempt is actually sent: the `max_tokens` control's
   value in `controls["effective"]`, under its wire name (`max_tokens`, or
   `max_completion_tokens` at OpenAI, `llm_sampling.py:308-309`). The Anthropic
   API is always sent one (`llm_sampling.py:588-590`), so its reserve is known
   exactly.
2. Otherwise `min(DEFAULT_REPLY_RESERVE, window // 4)`, and never more than a
   known `max_output`.

`DEFAULT_REPLY_RESERVE = 4096`. It is argued structurally: an unset
`max_tokens` says nothing about how long the reply will be, a shared window
counts the reply, and so something must be held back. The quarter-window cap
keeps a small local window usable, rather than reserving all of it. Both
numbers are placeholders, to be tuned against real prompts later, never
against a measured library. Reasoning tokens count against `max_tokens` on
the providers that have them (`llm_sampling.py:86-91`), so a sent
`max_tokens` already covers them. An unset one is covered only by the
default, which is one more reason the default is not small.

**`prompt_ceiling(resolved, reserve=None)`** walks the targets on
`resolved.chain`: the primary, and the fallback **only when it rides**
(`resolved.py:145-154`). A decide resolution's separate fallback stage is not
on the chain, and its prompt is the decide template's, not a packed one. For
each target with a known window it computes `window - (reserve if given else
reply_reserve(target, controls))`, floored at 0. It returns the **smallest**
result, since the facade may send the same messages to either target, and a
prompt sized for the primary's window overflows a fallback with a smaller one.

- `complete` is False when any target on the chain has an unknown window. The
  result then binds only the known ones, and the consumer decides how much to
  trust it.
- `tokens is None` when no target's window is known. **It is never `0` for
  unknown**: `0` means a known window with no room left after the reserve,
  which a consumer must treat as "nothing more fits".
- An override resolution (`override_inference`, a reroll) is just another
  `ResolvedInference`, so the same call answers for it.

The helper is pure and reads nothing. It is cheap enough to call per turn.

## 5. How 09, 10 and 12 read it

- **09-C3 (history section budget).** The history section's budget is
  `min(09's own cap, share × ceiling.tokens)`, where the share and the cap
  are 09's constants. With `tokens is None`, 09 uses its cap alone. So a
  model with an unknown window behaves exactly as it would without 01i.
  `context_budget`, when set, still bounds the whole prompt. 09 takes the
  smaller of the two, as `ContextBreakdown.contextLimit` already does on the
  client.
- **10 (query planning).** The planner input bound reads
  `prompt_ceiling(require_inference("history-query-plan", cid))`. The Fast
  role is often a small local model, which is where an unknown or small
  window matters most.
- **12 (bounded investigation).** Each loop turn's accumulated context is
  checked against the ceiling of the resolution the loop runs on. Approaching
  it is a terminal state (`budget_exhausted`, 12-C2) beside 01g-C4's limits,
  not a truncation.
- **What none of them do.** None reads `catalog` or `facts` directly, and none
  treats an unknown window as zero or as unbounded. Unknown means: use your
  own fixed cap.

## 6. 01i-C3: stating it, and seeing it

### 6.1 The stated facts

`facts.state` gains `context_window` and `max_output`:

- `None` leaves the value as it is;
- `0` removes it;
- a positive `int` below `2**31` sets it;
- anything else (a bool, a float, a string, a negative number) is a
  `ValueError` before the file is touched.

`0` removes for the reason `catalog._context` reads `0` as "does not say"
(`catalog.py:162-164`): no model has a zero window, so zero cannot be a
statement. The `2**31` bound only refuses typos that no int32 field on any
wire could carry. A write that would leave a stated `max_output` above a
stated `context_window` for the same model is refused with a sentence. A
stated value that only disagrees with the *catalog* is allowed, because the
user's word may be the correction.

`_view` (`facts.py:165-185`) adds both values, as `int | None`. They are
stated facts, so they survive `rev` (`facts.py:22-23`). The format-2
migration copies no legacy field into them, since no legacy field ever held
one. `LEGACY_FIELDS` (`facts.py:398`) is unchanged.

`FactsUpdate` (`routes/models.py:131-147`) gains `context_window: Any = None`
and `max_output: Any = None`, typed `Any` as the rest are, so the store
decides and a `"8192"` is a 400 under either pydantic. `put_connection_facts`
(`routes/config.py:1677-1740`) passes them to `facts.state`. Its existing
refusals apply unchanged: `not_migrated`, `newer_format`, 404, 503 and 409
`facts_unreadable`. Stating a window spends nothing and moves no vector
space, so it asks no `confirm_embedding`. It also changes no health verdict,
so `registry.forget_model` is not called for it.

`_facts_body` (`routes/config.py:1628-1650`) adds `limits`:
`{"window": {"value", "source"}, "max_output": {"value", "source"}}`. These
are resolved from the same row and facts the route already reads, through
`limits.of`, so the panel shows exactly what a call would use.

### 6.2 The model facts panel

`ModelFactsPanel` (`frontend/src/routes/ProvidersView.tsx:869`), the
list/detail record view for one model on one provider:

- **View:** a `.side-section` **Size**, showing `Window 128,000 tokens
  (catalog)` and `Max output 16,000 tokens (you)`, or `unknown`. When the
  user stated a value and the catalog states another, the catalog's figure
  shows beside it as a `.field-hint`, so a stale statement after a server
  relaunch is visible.
- **Edit:** two number fields, where empty means "not stated" and is sent as
  `0` when it was stated before. The hint text reads:

  > The model's own limits on this provider, when its listing does not say or
  > says wrong. To pack prompts smaller than the model allows, set the context
  > budget instead.

  That second sentence keeps the fact from becoming a second budget setting.
- **Deep link:** `?edit=limits` on `/providers/:id/models/<model>` opens the
  form with the caret in the window field. It behaves exactly as `?edit=rates`
  does today (`ProvidersView.tsx:882-893`): once per arrival, and cleared on
  leaving the form.

Tests follow the list/detail rule in CLAUDE.md: the row shows read-only Size,
**Edit** reveals the two fields, and a save re-reads the facts.

### 6.3 The Models page (01s)

`RoleCard`, `RouteRow` and `EmbeddingCard` (`store/inference/settings.py:164-238`)
each gain `limits` (the shape in section 6.1, or `null` when nothing resolves).
It is computed from the same resolved primary as `resolves` and 01s's `rate`.
The summary row gains a short readout after the rate:

```
Primary    Saltmarch AI ●  · model-large   · Story   · $3 / $15 per M (provider) · 200k window
Fast       Saltmarch AI ●  · model-small   · —       · $0.25 / $1 per M (provider) · window unknown [Set]
```

**Set** links to `/providers/<id>/models/<model>?edit=limits`, encoded as
01s's **Set rate** link is (01s, section 3.5). A native Decision row shows no
window: a native decision sends no prompt to pack. That row already carries
01s's "native decisions" sentence instead of a rate. The readout is
display-only. Nothing on the Models page writes a limit, because the facts
panel owns that write.

### 6.4 The context breakdown

Every breakdown that names a `model` also names `model_window`
(`{value, source}`), taken from the target that `model` was read from:

- the live context read;
- `routes/character_turns.py:673`;
- the prompt-log capture written through `routes/common._record_prompt`, which
  already receives the sent `wire.Target`.

`ContextBreakdown.contextLimit` prefers `ctx.model_window.value`. It falls
back to today's catalog lookup only for a snapshot recorded before this field
existed. A frozen snapshot is then measured against the window in force when
it was captured, as it already is against the budget then in force
(`ContextBreakdown.tsx:155-157`).

`pack.py`'s docstring sentence "The backend cannot infer the number" is
corrected to say the backend now knows the window (01i) and deliberately does
not default to it (Open question 1).

## 7. Contract

**01i-C1 `limits` on the resolved attempt.** Every `wire.Target` the resolver
builds carries `limits: wire.Limits`, and `Attempt.limits` returns it. Each of
`window` and `max_output` is `Limit(value, source)`:

- the source is `user` (stated in the model's facts), then `catalog` (the
  provider's cached listing at the current `rev`), then `unknown`;
- `value` is a positive int, or `None` exactly when the source is `unknown`.

Guarantees:

- It is resolved from reads `_attempt` already makes, so the turn path gains
  no I/O.
- It never raises: a malformed row or facts entry contributes nothing.
- It is never `0` and never guessed from a name.
- `window` is always read as shared by prompt and reply.

**01i-C2 `limits.prompt_ceiling(resolved, reserve=None) -> Ceiling`.**

- `tokens` is the smallest `window - reserve` over the targets on
  `resolved.chain` whose window is known, floored at 0, or `None` when none is
  known.
- The reserve is the sent `max_tokens`, else `min(DEFAULT_REPLY_RESERVE,
  window // 4)`, capped at a known `max_output`, unless the caller passes
  one.
- `complete` says whether every chain target was known, and `binding` names
  the target that bound it.

It is pure. A consumer must treat `None` as "use your own cap" and `0` as
"nothing more fits".

**01i-C3 stated limits and their surfaces.**

- `facts.state(..., context_window=, max_output=)` and the facts route accept
  `None` (leave), `0` (remove) or a positive int below `2**31`. A stated
  output above a stated window is refused.
- `GET /llm-connections/{id}/facts` returns the resolved `limits`.
- The facts panel shows and edits them.
- The settings view's role, route and Embedding cards carry `limits` for the
  Models summary.
- Every context breakdown carries `model_window`.

## 8. Interaction with repo rules

- **Paths, atomic writes, locks.** The facts write goes through the existing
  `facts._write_existing` read-merge-write under the facts lock and the
  connection lock (`facts.py:251-285`). There is no new file and no campaign
  lock: facts are global to the provider (`facts.py:39`).
- **Imports.** `store/inference/limits.py` imports `wire` and nothing in the
  store. `resolve` imports it as a submodule, following CLAUDE.md's
  import-binding rule. `wire.py` gains two plain types and stays a leaf.
- **Model-settings format.** Writes are refused before format 2 and on a
  newer format, exactly as every facts write is today (`refuse_unmigrated`).
  A format-1 store resolves through the planner's in-memory overlay, whose
  facts carry no stated limits, so it reads catalog-or-unknown. That is
  correct, and nothing is migrated.
- **Pydantic v1/v2.** `FactsUpdate` fields stay plain `Any` and are dumped
  through `routes.common._dump`.
- **Privacy.** No value is measured from a store. The constants in section 4
  are argued structurally, and the examples use placeholder names.
- **Guards.** `test_paths_guard`, `test_atomic_guard` and `test_import_guard`
  see no new pattern. `test_lowering_retired_guard` is unaffected, because the
  limits ride the target and not a connection dict.

## 9. Tests and acceptance

Backend (pytest):

- **`test_catalog.py`**: `max_output` is read from OpenRouter's
  `top_provider.max_completion_tokens` and from Anthropic's `max_tokens`. It
  is absent for a zero, negative, boolean or non-int value, and absent for a
  vLLM or Ollama row. `features.max_tokens` is unchanged.
- **New `test_inference_limits.py`**:
  - `limits.of` covers the source order (user over catalog over unknown) for
    each value independently, and a malformed facts value contributes
    nothing.
  - `reply_reserve` uses a sent `max_tokens`, `max_completion_tokens` at
    OpenAI, and the Anthropic default. Unset, it uses the default, the
    quarter-window cap and the `max_output` cap.
  - `prompt_ceiling`:
    - takes the smaller window when a fallback rides;
    - ignores a fallback that does not ride, and a decide resolution's
      separate stage;
    - returns `complete=False` with one unknown;
    - returns `tokens=None` with none known;
    - floors at 0;
    - honours an explicit `reserve`.
- **`test_inference_resolve*.py`**: an attempt's `limits` equals `limits.of`
  over the same row and facts. A reroll override resolves its own. An
  embedding attempt judged with `catalog=False` reads only stated values. A
  catalog cached under an old `rev` gives `unknown`.
- **`test_inference_facts.py`, the facts route tests**:
  - set, remove and leave each value;
  - 400 for `"8192"`, `true`, `-1`, `2**31` and an output above the window;
  - the stated value survives a `rev` change;
  - `not_migrated` before format 2;
  - no `confirm_embedding` is asked;
  - GET returns the resolved `limits` with their sources.
- **Settings view**: each card's `limits` follows its `resolves`; it is `null`
  when nothing resolves; a native Decision card carries no window.
- **Breakdown**: a chat turn's breakdown and its prompt-log capture carry
  `model_window` from the target sent.
- **Byte-identity**: with `context_budget` at 0, a composed prompt is
  unchanged with or without a known window. Assert this against the existing
  golden harnesses (`test_lore_golden.py` and the frozen-campaign sweep, whose
  `snapshot.json` must not move).

Frontend (vitest):

- **`ModelFactsPanel`**: the view shows Size with sources and the catalog's
  figure beside a stated one; **Edit** shows the two fields; clearing a
  stated value sends `0`; `?edit=limits` opens the form once.
- **Models summary**: a known window, `window unknown` with its **Set** link
  (an encoded model id with `/`), and no window on a native Decision row.
- **`ContextBreakdown`**: `model_window` is preferred over the catalog lookup.
  An older snapshot falls back to the lookup. The smaller of budget and window
  still bounds the bar.

**Acceptance.** All of the above is green under `make check`, and no
existing prompt's bytes change.

## 10. Non-goals

- Defaulting the packer's budget to the window (Open question 1).
- Counting a reply's actual length, or asking a provider for a token count
  before sending.
- A shipped table of model windows, or any name rule for them.
- Reading a limit out of a provider's error message after an overflow.
- Changing what the Anthropic API is sent as `max_tokens`. That stays the
  catalog feature's cap in `llm_sampling` (Open question 3).
- Embedding input limits as a consumer feature. The fact is resolved for an
  embedding attempt too, but truncating what 08 embeds is 08's call.

## 11. Open questions

1. **Should `context_budget: 0` mean "the resolved window's ceiling" instead
   of "unbounded"?** Recommendation: **not in 01i**. It would change the
   prompt on every install whose model has a known window, the byte-identity
   promise in `pack.py:69-73` would end, and drops would start appearing
   where none did. Decide it separately, with a visible setting such as
   "Fit to model" beside the number. 01i makes that a small change later.
2. **Should OpenRouter's window be the smaller of `context_length` and
   `top_provider.context_length`?** OpenRouter can route to a provider other
   than the top one. Recommendation: keep `context_length`, as the catalog
   does today. Revisit only if a consumer is seen overflowing a routed
   provider. A user who pins a provider can state the window.
3. **Should a stated `max_output` also cap what the Anthropic API is sent?**
   Recommendation: **no, for now**. `llm_sampling` reads only the catalog
   feature, and that is the one place an output limit reaches the wire.
   Making a stated fact change request bodies is a sampling change, and it
   belongs to that subsystem's own spec.
4. **Should `DEFAULT_REPLY_RESERVE` follow the reply-length knobs the prompt
   already states?** Recommendation: **later**. Those knobs are words in a
   template, not a token count. Tune the constant with 01a's reports once 09
   is drawing on it.
5. **Is a stated window per (provider, model) enough, or must a campaign be
   able to state a different one?** Recommendation: per provider and model
   only. A campaign wanting a smaller prompt has `context_budget`, and a
   per-campaign window would be a second budget wearing a fact's name.
