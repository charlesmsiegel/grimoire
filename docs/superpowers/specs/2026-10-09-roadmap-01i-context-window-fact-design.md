# 01i. Context window as a resolved model fact

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01i in `ROADMAP-CHECKLIST.md`. Lane: retrieval (feeds 01g, 09, 12).
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
| 01i-C1 `wire.Limits` on every target and resolved attempt | 01g (S) | Sizing a loop turn's context against the window where it is known, through 01i-C2 with the run's per-turn output cap (01g-C4) passed as `max_tokens` |
| 01i-C1 (`max_output`) | 01f (S) | `generate(max_tokens=)` never asks for more than the model's known max output (01f section 3.9) |
| 01i-C1 + 01i-C2 | 09 (S) | The history section's budget (09-C3): a share of `prompt_ceiling(resolved).tokens`. Without it, 09 uses its own cap |
| 01i-C1 + 01i-C2 | 12 (H) | Bounding each investigation turn's accumulated context (12-C2): `prompt_ceiling(resolved, max_tokens=<per-turn cap>)`, never `window - max_output` |
| 01i-C3 user-stated limits, the Models readout, `model_window` | 09, 12 (indirectly) | A local server whose catalog says nothing still gets a ceiling |

**01i-C2 is the stated way a consumer derives a ceiling** (section 5). The
checklist's edges for 09 and 12 cite `01i-C1/C2`.

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

It is read with `_context`'s rule (`catalog.py:162-170`): a positive whole
number, an integral float such as `16000.0` included, since JSON does not tell
the two apart. The window and the cap therefore treat the same JSON the same
way. (`_positive_int`, `catalog.py:93-94`, which refuses the float, stays the
rule for `features.max_tokens`, unchanged.) It is not
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
    binding: tuple[str, str] | None   # (provider_id, model) of the attempt that bound it
    reason: str               # "" or a sentence, set when tokens == 0 (below)

def reply_reserve(attempt: Attempt, *, max_tokens: int | None = None) -> int: ...
def prompt_ceiling(resolved: ResolvedInference, *, reserve: int | None = None,
                   max_tokens: int | None = None) -> Ceiling: ...
```

`binding` is a tuple, not a joined string, because most OpenRouter model ids
contain `/` and a joined string could not be split back.

**`reply_reserve(attempt, max_tokens=None)`** reserves the tokens a reply on
that attempt may take, reading the attempt's own `controls` and
`target.limits`:

1. A **per-call cap** the caller will send (`max_tokens`): 01f-C1's
   `generate(max_tokens=)`, or 01g-C4's per-turn output cap. It is not in
   `Attempt.controls`, which `llm_sampling.effective` built from the preset at
   resolution (`resolve.py:498-503`), so the caller must pass it. A caller
   that sends a per-call cap and does not pass it here gets a ceiling that
   reserves the preset's figure, and its prompt can overflow by the
   difference. Section 5 makes passing it a rule.
2. Otherwise, the `max_tokens` the attempt is sent from its preset: the
   `max_tokens` control's value in `controls["effective"]`, under its wire
   name (`max_tokens`, or `max_completion_tokens` at OpenAI,
   `llm_sampling.py:308-309`). The Anthropic API is always sent one
   (`llm_sampling.py:588-590`), so its reserve is known exactly.
3. Otherwise, `min(DEFAULT_REPLY_RESERVE, window // 4)`.

In every case the reserve is capped at a known `max_output`: a reply cannot be
longer than the model allows, whatever was asked. `Limits.max_output` is a cap
on what may be *requested*, and is **never itself used as the reserve**.
Reserving it whole would refuse every prompt on a model whose listing puts
`max_completion_tokens` close to the window.

`DEFAULT_REPLY_RESERVE = 4096`. It is argued structurally: an unset
`max_tokens` says nothing about how long the reply will be, a shared window
counts the reply, and so something must be held back. The quarter-window cap
keeps a small local window usable, rather than reserving all of it. Both
numbers are placeholders, to be tuned against real prompts later, never
against a measured library. Reasoning tokens count against `max_tokens` on
the providers that have them (`llm_sampling.py:86-91`), so a sent
`max_tokens` already covers them. An unset one is covered only by the
default, which is one more reason the default is not small.

**`prompt_ceiling(resolved, reserve=None, max_tokens=None)`** walks the
attempts `resolved.chain` is built from: `resolved.attempts[0]`, and
`resolved.attempts[1]` only when `resolved.rides` (the rule `chain` applies,
`resolved.py:145-154`). It walks attempts, not targets, because each
attempt's own `controls` are what `reply_reserve` reads. A fallback that does
not ride is never sent, and a decide resolution's separate fallback stage is
not on the chain (its prompt is the decide template's, not a packed one), so
neither lowers the ceiling. For each attempt with a known window it computes
`window - (reserve if given else reply_reserve(attempt, max_tokens=max_tokens))`,
floored at 0. It returns the **smallest**
result, since the facade may send the same messages to either target, and a
prompt sized for the primary's window overflows a fallback with a smaller one.

- `complete` is False when any target on the chain has an unknown window. The
  result then binds only the known ones, and the consumer decides how much to
  trust it.
- `tokens is None` when no target's window is known. **It is never `0` for
  unknown**: `0` means a known window with no room left after the reserve,
  which a consumer must treat as "nothing more fits".
- When `tokens == 0`, `reason` names why, for example "The preset asks for
  32,000 reply tokens; this model's window is 8,192." The settings view's
  `limits` carries the ceiling and its reason (section 6.3), so the Models
  readout shows the misconfiguration rather than a section that silently
  receives nothing.
- An override resolution (`override_inference`, a reroll) is just another
  `ResolvedInference`, so the same call answers for it.

The helper is pure and reads nothing. It is cheap enough to call per turn.
`store/inference/limits.py` imports `wire` and `resolved` (the latter as a
submodule, `from . import resolved`). That is acyclic: `resolved` imports
only `wire`, `capabilities` and `cascade` (`resolved.py:26-28`).

## 5. How consumers derive a ceiling (normative)

A consumer that bounds a prompt, or a share of one, against the model's
window **must** derive it through `prompt_ceiling` (01i-C2), or through
`reply_reserve` for a check of one attempt. It must not compute
`window - max_output`, must not take a minimum over `resolved.attempts`
itself, and must not read `catalog` or `facts`. Each of those disagrees with
the facade about which targets a prompt reaches and how much reply to hold
back. A caller that sends a per-call output cap passes it as `max_tokens`.
`tokens is None` means "use your own fixed cap", never zero and never
unbounded.

- **09-C3 (history section budget).** The section's budget is
  `min(09's own cap, floor(share × ceiling.tokens))`, where the share and the
  cap are 09's constants, plus 09's packer-budget term when `context_budget`
  is set. With `tokens is None`, 09 uses its cap alone, so a model with an
  unknown window behaves exactly as it would without 01i. **09 must change:**
  section 9.2 takes `WINDOW_SHARE * min known context_window over the chain's
  attempts` from C1 directly. It should take `WINDOW_SHARE *
  prompt_ceiling(resolved).tokens` instead. That drops a non-riding fallback
  from the minimum and reserves the reply.
- **12 (bounded investigation).** Before each model turn, the loop refuses as
  `budget_exhausted` (`limit: "context"`) when `estimate + CONTEXT_MARGIN >
  prompt_ceiling(resolved, max_tokens=<the run's per-turn output cap>).tokens`.
  With `tokens is None`, it uses its own fallback cap. **12 must change:**
  section 7.3 checks `estimate + max_output > context_window -
  CONTEXT_MARGIN`. Reserving `max_output` refuses every turn at once on a
  model whose listed output cap is close to its window. Its
  `FALLBACK_CONTEXT_TOKENS` stays, as its "own cap".
- **01g (tool loop).** Its per-turn output cap (01g-C4) is what it passes as
  `max_tokens`.
- **10** is not a consumer: it does not cite 01i, and the checklist has no
  edge from it. If 10 later bounds its planner input against the window, it
  follows this section and adds the edge.

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
wire could carry.

A write that would leave a stated `max_output` above a stated
`context_window` for the same model is refused with a sentence. The check
needs the merged entry: a request may state only one of the two while the
other is already on file. So it runs **inside `change`**, after the merge,
under the hold `_write_existing` takes (`facts.py:251-285`). It raises
`ValueError` there, so nothing is stored (`change` runs before `_store`), and
the route answers 400. A
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
`limits.of`, so the panel shows exactly what a call would use. `_facts_body`
already spreads `facts.of`'s view into the body (`**known`,
`routes/config.py:1644`), so the two *stated* values also appear at the top
level. The panel reads **stated** values from the top-level `context_window` /
`max_output` (what the form edits) and **resolved** values from `limits`
(what the view shows). The frontend `ModelFacts` type
(`frontend/src/api/types.ts:428`) gains all three fields, and
`ModelFactsUpdate` the two writable ones.

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
  does today: once per arrival, and cleared on leaving the form. The panel's
  `?edit=rates` effect (`ProvidersView.tsx:884-906`) is generalised to both
  values. The address is built only in `frontend/src/providerPaths.ts`, which
  gains `EDIT_LIMITS = "limits"` and `modelLimitsPath(id, model)` beside
  `EDIT_RATES` and `modelRatesPath` (`providerPaths.ts:17-22`). It encodes the
  model **segment by segment**, so a model's own `/` stays a path separator
  for the `models/*` splat (`App.tsx:375`).

Tests follow the list/detail rule in CLAUDE.md: the row shows read-only Size,
**Edit** reveals the two fields, and a save re-reads the facts.

### 6.3 The Models page (01s)

The server's `_role_card`, `_route_row` and `_embedding_card`
(`store/inference/settings.py:164-238`; the frontend types are `RoleCard`,
`RouteRow` and `EmbeddingCard`) each gain `limits`:
`{"window", "max_output", "fallback_window", "ceiling": {"tokens", "binding",
"reason"}}`. `window` and `max_output` are the resolved primary's (section
6.1's shape), `fallback_window` is the riding fallback's window (or absent),
and `ceiling` is `prompt_ceiling(resolved)`. It is computed from the same
resolution as `resolves` and 01s's `rate`. **`limits` is `null` when nothing
resolves, and also on a card or route whose `decision_mode` is `native`**: a
native decision sends no prompt to pack. The server decides this, so the
frontend holds no rule of its own (the decide-note precedent in CLAUDE.md).
The summary row gains a short readout after the rate:

```
Primary    Saltmarch AI ●  · model-large   · Story   · $3 / $15 per M (provider) · 200k window
Fast       Saltmarch AI ●  · model-small   · —       · $0.25 / $1 per M (provider) · window unknown [Set]
```

**Set** links to `modelLimitsPath(id, model)`, for example
`/providers/saltmarch/models/vendor/m?edit=limits`. This is *not* 01s's
**Set rate** grammar, which carries the model in a query parameter on
`/models`. The fallback line 01s already draws ("Fallback: provider · model ·
preset") gains `· 8k window` when the fallback rides. A ceiling bound by the
fallback rather than the primary therefore shows, and a `ceiling.reason`
(a reply reserve larger than the window) shows under the row in the warning
style. A native Decision row, whose `limits` is `null`, shows no window. That
row already carries 01s's "native decisions" sentence instead of a rate. The readout is
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
not default to it (Open question 1). So is the same claim in the comment
beside `DEFAULT_CONTEXT_BUDGET` (`store/config.py:32-34`, "the backend cannot
see the model's window size, only the frontend can").

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

**01i-C2 `limits.prompt_ceiling(resolved, reserve=None, max_tokens=None) -> Ceiling`.**

- It walks `resolved.attempts[0]`, plus `attempts[1]` when `resolved.rides`.
  `tokens` is the smallest `window - reserve` over those whose window is
  known, floored at 0, or `None` when none is known.
- The reserve is the caller's per-call cap (`max_tokens`), else the preset's
  sent `max_tokens`, else `min(DEFAULT_REPLY_RESERVE, window // 4)`. It is
  always capped at a known `max_output`, unless the caller passes `reserve`
  outright. `max_output` is never itself the reserve.
- `complete` says whether every walked attempt was known, `binding` is the
  `(provider_id, model)` that bound it, and `reason` explains a ceiling of 0.

It is pure. **It is the stated way every consumer derives a ceiling**
(section 5). A consumer must treat `None` as "use your own cap" and `0` as
"nothing more fits", and must pass any per-call output cap it sends.

**01i-C3 stated limits and their surfaces.**

- `facts.state(..., context_window=, max_output=)` and the facts route accept
  `None` (leave), `0` (remove) or a positive int below `2**31`. A stated
  output above a stated window, judged on the merged entry inside the write's
  hold, is refused.
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
  is read from an integral float (`16000.0`) as the window is, and is absent
  for a zero, negative, boolean or fractional value, and absent for a
  vLLM or Ollama row. `features.max_tokens` is unchanged.
- **New `test_inference_limits.py`**:
  - `limits.of` covers the source order (user over catalog over unknown) for
    each value independently, and a malformed facts value contributes
    nothing.
  - `reply_reserve` uses a per-call `max_tokens` over the preset's; a sent
    `max_tokens`, `max_completion_tokens` at OpenAI, and the Anthropic
    default. Unset, it uses the default and the quarter-window cap. Every case
    is capped at a known `max_output`, and `max_output` is never the reserve
    itself (a model listing a 128k cap in a 131k window still gets a usable
    ceiling).
  - `prompt_ceiling`:
    - takes the smaller window when a fallback rides;
    - ignores a fallback that does not ride, and a decide resolution's
      separate stage;
    - returns `complete=False` with one unknown;
    - returns `tokens=None` with none known;
    - floors at 0, with a `reason`, when a preset's `max_tokens` exceeds the
      window;
    - passes a per-call `max_tokens` (an 8192 cap with no preset value
      reserves 8192, not the default);
    - honours an explicit `reserve`;
    - returns `binding` as a `(provider_id, model)` tuple for a model id
      holding `/`.
- **`test_inference_resolve*.py`**: an attempt's `limits` equals `limits.of`
  over the same row and facts. A reroll override resolves its own. An
  embedding attempt judged with `catalog=False` reads only stated values. A
  catalog cached under an old `rev` gives `unknown`.
- **`test_inference_facts.py`, the facts route tests**:
  - set, remove and leave each value;
  - 400 for `"8192"`, `true`, `-1`, `2**31` and an output above the window;
  - **two requests**: a stated window of 8192, then a request stating only
    `max_output: 16000`, gives 400 and leaves the file unchanged; the reverse
    order likewise;
  - the stated value survives a `rev` change;
  - `not_migrated` before format 2;
  - no `confirm_embedding` is asked;
  - GET returns the resolved `limits` with their sources.
- **Settings view**: each card's `limits` follows its `resolves`; it is
  `null` when nothing resolves and on a native decide card or route; it
  carries the riding fallback's window and a ceiling `reason` when the preset
  reserve exceeds the window.
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
- **Models summary**: a known window; `window unknown` with its **Set** link,
  which for `vendor/m` on `saltmarch` is
  `/providers/saltmarch/models/vendor/m?edit=limits`; the fallback line's
  window; a ceiling `reason`; and no window where `limits` is `null`.
- **`providerPaths`**: `modelLimitsPath` encodes per segment.
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

## 12. Review record

Substitute adversarial review, 2026-10-10 (Codex gate still pending). No
blocking findings. Each finding was checked against the code at `35c1fb7`.

- **S1, fixed.** Section 5 is now normative: a consumer derives a ceiling
  through `prompt_ceiling` (or `reply_reserve` for one attempt), never
  `window - max_output`, never its own minimum over `attempts`. 10 is
  removed as a consumer. **Routed:**
  - the checklist edges for 09 and 12 become `01i-C1/C2`;
  - 09 section 9.2 replaces "min known context_window over the chain's
    attempts" with `WINDOW_SHARE * prompt_ceiling(resolved).tokens`;
  - 12 section 7.3 replaces `estimate + max_output > context_window -
    CONTEXT_MARGIN` with `estimate + CONTEXT_MARGIN >
    prompt_ceiling(resolved, max_tokens=<per-turn cap>).tokens`, keeping
    `FALLBACK_CONTEXT_TOKENS` for `None`;
  - 01g passes its per-turn output cap (01g-C4) as `max_tokens`.
- **S2, fixed.** `reply_reserve` and `prompt_ceiling` take `max_tokens` for
  a per-call cap (01f-C1, 01g-C4). Every reserve is capped at a known
  `max_output`. A ceiling of 0 carries a `reason`, which the Models readout
  shows.
- **S3, fixed.** The **Set** link uses `providerPaths.modelLimitsPath`,
  which encodes per segment, with `EDIT_LIMITS` beside `EDIT_RATES`. The test
  expects `/providers/saltmarch/models/vendor/m?edit=limits`.
- **S4, fixed.** The output-above-window check runs inside `change`, on the
  merged entry, under the write's hold. A two-request test is added.
- **S5, fixed.** `prompt_ceiling` walks attempts (`attempts[0]`, plus
  `attempts[1]` when `rides`) and reads each one's `controls`. `limits`
  imports `resolved` as a submodule, which is acyclic.
- **M1, fixed.** The `store/config.py:32-34` comment is corrected alongside
  `pack.py`'s docstring.
- **M2, fixed.** `max_output` uses `_context`'s rule, which accepts an
  integral float, so the window and the cap agree.
- **M3, fixed.** The settings card carries `fallback_window` and the ceiling,
  and the 01s fallback line shows the fallback's window.
- **M4, fixed.** `binding` is a `(provider_id, model)` tuple.
- **M5, fixed.** The server sends `limits: null` on a native decide card or
  route.
- **M6, fixed.** The panel reads stated values from the top-level fields and
  resolved ones from `limits`. The frontend `ModelFacts` type gains them.
- **M7, fixed.** The server function names (`_role_card`, `_route_row`,
  `_embedding_card`) and the frontend type names are both given.
