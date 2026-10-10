# 01f. Structured generation: a provider's schema mode on `generate`

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01f in `ROADMAP-CHECKLIST.md`. Lane: Now (minor spec under 01;
feeds the retrieval lane through 10 and the tool-calling spec 01g).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** nothing in the 2026-10-06 bundle drafts directly (there is no
01f draft); it extends `2026-10-07-inference-backend-refactor-design.md` (01)
section 7.2, whose `generate(schema=)` signature landed in slice I but whose
structured mode was only ever wired for `decide` ("Initially only `decide`'s
structured backend uses it").

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `generate` / `decide` operations, `wire.Target.structured`, `llm.SchemaRefusalError`, the `structured_output` capability with provenance | 01 (landed, slices F and I) | Everything here is an extension of those four. | Hard |
| 01i-C1 (`wire.Limits`, max output) | 01i | The `max_tokens` cap is also held to the model's known maximum output (section 3.9). Without it the caller's cap alone applies. | Soft |
| 01a-C1 (eval cost, latency, tokens) | 01a | Optional: comparing an adopter's parse-failure rate with and without the mode. Not needed to land the mechanism. | Soft |

## Required by

Rebuilt from the edges in `ROADMAP-CHECKLIST.md` (01g ← 01f-C1/C2/C3 H;
10 ← 01f-C1 H, 01f-C2 S).

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01f-C1 | 01g (hard; 01g-C2a's finalize turn) | A tool loop asks for its final record with a schema, tools off, under the turn's output cap. |
| 01f-C2 | 01g (hard) | The finalize turn inherits the refusal re-send. |
| 01f-C3 | 01g (hard) | Every tool's parameter schema passes `schemas.check`; arguments and the final record are read with `schemas.find_value`. |
| 01f-C1 | 10 (hard; 10-C1, the `history_plan` route on Fast) | The query plan is generated with a schema and the per-call `max_tokens` cap, held to the schema where the provider can. |
| 01f-C2 | 10 (soft) | A provider that refuses the field still answers the plan from the prompt. |

## 1. Current state (reconciled against main)

- **The signature is there; the behaviour is not.** `inference.generate`
  already takes `schema=` (`inference.py:734-777`) and hands it to the facade
  only when given. The facade passes a schema to an adapter only for a target
  flagged `structured` (`llm.py:1281-1294`, `_generate`). A generate
  resolution never carries the flag: `resolve._flag_structured` returns the
  attempts untouched unless `operation == "decide"`
  (`store/inference/resolve.py:955-966`). So today a `generate(schema=...)`
  call sends exactly what it would without the schema. No call site passes one
  (`grep "operations.generate("` finds eleven call sites, none with `schema=`).
- **The flag is written in one place.** `resolve._stamp` is "the one place a
  resolution writes a target's account or structured flag"
  (`resolve.py:969-981`). `decide` never writes it. It stamps the decision mode
  per call onto **new** targets (`inference._with_mode`, `inference.py:223-227`),
  and it builds its re-send target with `dataclasses.replace(attempt,
  structured=False)` (`inference._without_mode`, `inference.py:230-235`).
- **Wire envelopes per adapter:**
  - OpenRouter: `response_format: {type: json_schema, json_schema: {name:
    "reply", strict: true, schema}}` (`openrouter.py:253-256`).
  - OpenAI-compatible: the same envelope (`openai_compatible.py:361-364`).
  - Anthropic: `output_config.format: {type: json_schema, schema}`, merged
    beside the adaptive-thinking effort (`anthropic.py:280-290`).
  - Claude Agent SDK: never; the adapter drops the keyword
    (`adapters.py:259-263`), and the `claude` preset lists
    `structured_output` in `never` (`store/inference/providers.py:74-80`).
- **Refusal handling exists, but only `decide` re-sends.** `_resilient`
  recognises a refused envelope (`llm._schema_refusal`, `llm.py:851-875`). It
  does not observe it as a health failure (CODE-M5, `llm.py:1049-1061`). It
  moves on to the next route, and when every route failed it raises
  `SchemaRefusalError` carrying the refusing attempts (`llm.py:1107-1114`).
  Only `inference._ask` acts on that: it re-sends each refusing attempt once,
  alone and without the mode, as its own metered call (`inference.py:289-318`).
  A `generate` caller would receive the `SchemaRefusalError` as an ordinary
  `LLMError`.
- **One reader assumes only `decide` flags.** `llm._structured_share`
  (`llm.py:704-725`) reads the envelope's spellings from the flag rather than
  from whether a schema was sent, "because only a decide resolution carries
  the flag and `decide` always sends one". `_preset_refusal` subtracts those
  spellings, and `_schema_refusal` matches on them. Once a generate target can
  be flagged, that premise has to be made true by construction (section 3.4).
- **The capability.** `structured_output` resolves as follows
  (`store/inference/capabilities.py:153-179`):
  - from the catalog: OpenRouter `supported_parameters` naming
    `structured_outputs`, or Anthropic's `features.structured_output`;
  - from the user's override;
  - from a preset's `never`.

  There is no probe for it (`store/inference/probes.py` probes `generate`,
  `vision`, `embed` and `decide_native`), so on a local or custom endpoint it
  stays `unknown` unless the user says otherwise.
- **The portable schema subset is written down only for decisions.**
  `decisions.py:32-38` restricts a batch schema to `type` (object, string,
  boolean, integer, null), `enum`, `anyOf`, `required`, `properties` and
  `additionalProperties: false`. That is the intersection of OpenAI strict
  mode and Anthropic's `output_config.format`. The strict-mode budgets are
  constants there too (`MAX_ENUM_VALUES`, `MAX_SCHEMA_STRING_CHARS`,
  `MAX_SCHEMA_PROPERTIES`, `decisions.py:101-133`). A generation's schema
  needs arrays and numbers, which decisions never did.
- **The schema rides the prompt** for decisions: `templates/decide/system.j2`
  renders `{{ schema | tojson(indent=2) }}` ("ruling 3": always, so a
  fallback without the mode answers from the same prompt).
- **JSON-in-prompt callers today.** Each one describes its shape in prose and
  parses tolerantly:
  - `suggestions` and `intent`, via `store/suggest._extract_json`,
    `store/suggest.py:1311-1325`. `templates/scene_intent/system.j2` spells
    the keys in a sentence.
  - `scenario` and `character-from-passage`, via `absorb.parse.extract_object`
    (`store/absorb/parse.py:135`, `store/scenario.py:216`).
  - `tracker-update`, `absorb`, `dossier` and `audit`.
  - **Taglines are not one.** `store/taglines.parse_output` takes the first
    non-blank line of plain text (`store/taglines.py:44-48`), so they have no
    schema to adopt.

## 2. Goal (and what is explicitly not the goal)

Let any generation ask for JSON matching a JSON Schema, with these properties:

1. **Held to the schema where the provider can.** Each attempt on the chain
   gets its provider's structured mode if that attempt's model is known
   (`yes`) to take it.
2. **Answered from the prompt where it cannot.** The schema is always in the
   prompt, so an attempt without the mode (a fallback, a local model, a
   provider that refused the field) is answering the same question.
3. **Never failed by the mode alone.** A refusal of the field is re-sent
   without it and is never a health failure. This is `decide`'s rule, shared
   rather than copied.

Not the goal:

- Making structured mode a *requirement* of any route (no `requires`
  entry). The schema in the prompt is the contract. The mode is a
  reliability improvement on top of it.
- Converting the existing JSON callers wholesale (section 3.8 pilots one).
- Tool or function calling, which is 01g.
- Using a forced tool call as a structured-output substitute. 01 section 7.2
  already rejected that for Anthropic ("not a forced tool, which current models
  answer with a 400").

## 3. Design

### 3.1 The portable schema subset (`grimoire/schemas.py`, new)

A new gateway leaf, **standard library only**. `test_schemas.py` holds that by
the AST, the way `test_wire.py` holds `wire.py`. The leaf exists so that `decisions`, `inference` and `store` code can
all import it. It holds one rule: what a schema may contain if it is to be
sent to both OpenAI strict mode and Anthropic's `output_config.format`.

```python
class SchemaError(ValueError): ...

def check(schema: dict) -> None
    """Raise SchemaError unless `schema` is inside the portable subset."""

def render(schema: dict) -> str
    """The one spelling of `schema` a prompt carries:
    json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False).
    No HTML escaping: the model should read "the character's", not
    "\u0027". Registered in the prompts environment as the Jinja filter
    `schema_json`, so a template and the check use the SAME function."""

def find_value(text: str) -> dict | list | None
    """The reply's top-level JSON value (object or array), tolerant of a
    fence or prose around it. `decisions.find_object` keeps its own
    dict-only contract: it calls this and maps a list to None, so a bare
    `[...]` still reads NO_OBJECT and `test_decide_chain_golden.py` does not
    move."""
```

The subset (the decisions subset, widened by exactly what generations need):

- `type`: one of `object`, `array`, `string`, `integer`, `number`,
  `boolean`, `null`. Never a type array: nullable is `anyOf` with a
  `{"type": "null"}` branch, as decisions already does.
- An object has `properties`, `required` naming **every** property, and
  `additionalProperties: false`. Strict mode requires both. An optional field
  is `anyOf: [T, {"type": "null"}]`.
- An array has `items`, which is one schema.
- Also allowed: `enum` (strings or integers), `anyOf`, and `description` (a
  string).
- **Refused:**
  - numeric bounds (`minimum`, `maximum`, `exclusive*`, `multipleOf`), which
    Anthropic refuses (`decisions.py:36`);
  - `minItems` and `maxItems`, `minLength` and `maxLength`, `pattern`,
    `format`;
  - `$ref` and `$defs`, `oneOf`, `allOf`, `not`, `const`;
  - any key not listed above.

  A bound the caller needs is enforced by the caller's parser, after the
  reply arrives. **The subset stays strict on purpose.** A consumer whose
  natural schema has bounds drops them from the schema it sends and
  enforces them in its tolerant parser. 10 does exactly that for its
  `history_plan` schema, dropping `maxItems`, `maxLength`, `minimum` and
  `maximum` (resolved on 10's side in the spec gate). There is no
  bound-stripping helper here: one would let a schema in the prompt promise
  a bound the wire never enforced.
- **An empty `enum` is refused.** Strict mode answers `enum: []` with a 400
  naming `response_format`, which `_schema_refusal` would misread as the mode
  refused, re-sending and pointing the user at a `structured_output: no`
  override that is the wrong remedy. A caller with nothing to offer sends
  that field as a plain string (or array of strings) instead (section 3.8).
- The strict-mode budgets are the decisions constants, imported rather than
  restated: `MAX_ENUM_VALUES`, `MAX_SCHEMA_STRING_CHARS` and
  `MAX_SCHEMA_PROPERTIES` (`decisions.py:109-133`). They move into
  `schemas.py` and `decisions` re-exports them, so there is one copy. Nesting
  depth is capped at `MAX_DEPTH`. Its value is OpenAI strict mode's documented
  nesting limit, read from the same "Supported schemas" page that
  `decisions.py:104-133` cites, when the plan is written. This spec does not
  restate the figure, because the page has changed it before. A decision
  batch nests four deep (batch, item, answers, a question's value;
  `decisions.py:128-133`), far inside any published limit.

`decisions.schema(...)`'s output must pass `check`, and a test holds that.
The widening adds nothing a decision batch uses, so a decision is unaffected.

### 3.2 Flagging per call, not per resolution

`generate(schema=...)` builds the chain it sends **per call**. A
resolution's targets are frozen and shared, and the flag is a property of
this call rather than of the route.

```python
# store/inference/resolve.py
def structured_capable(attempt: Attempt) -> bool:
    """`structured_output` is `yes`. A `no` or an `unknown` is not flagged."""

# _flag_structured keeps its decide behaviour and calls structured_capable.

# inference.py
def _structured_chain(resolved: ResolvedInference) -> wire.Chain:
    """`resolved.chain` with each target replaced by a NEW target,
    flagged `structured=True` when its attempt is `structured_capable`.
    The primary is attempts[0]; the fallback, when it rides, attempts[1]."""
```

The resolution is left as it is. `resolve`'s docstring promise that "a
generate resolution's targets are what they were before slice F" stays true.
The equivalence tests that compare resolutions against the frozen baseline
see no change, and the settings view reads the same targets.

Only `yes` flags an attempt. `unknown` does not, because a request carrying
the envelope to an endpoint that does not know the field is a 400 on a
strict server (the reason `openai_compatible` sends no `usage.include`). The
re-send in section 3.5 would recover from that, but it would pay a failed
round trip on every call to recover. A user who knows their local model
takes it says so with the facts override `structured_output: yes`, which is
already a capability source.

### 3.3 The schema must be in the prompt

`generate(schema=...)` refuses, with a `ValueError` before any client call,
a message list in which no `system` or `user` message contains
`schemas.render(schema)`. Ruling 3 is the reason, and this makes it a check
rather than a convention:

- A fallback without the mode, a re-send after a refusal, and an `unknown`
  model all answer only from the prompt.
- A schema sent on the wire but missing from the prompt would get one answer
  from the provider that enforces it and a different answer from every
  attempt that does not.

The check is a substring test over strings already in memory. It costs
nothing next to the call.

**One renderer on both sides.** The prompts environment (`prompts._env`,
`prompts.py:30-33`) uses Jinja's default `tojson` policy, which sorts keys
and HTML-escapes `<`, `>`, `&` and `'`, so its output is not a plain
`json.dumps`. 01f does not try to imitate it. `prompts._env` registers
`schemas.render` as the filter `schema_json`, and every template that carries
a `generate` schema renders it as `{{ schema | schema_json }}`. The check
calls the same function, so the two cannot drift. `decide/system.j2` keeps
`{{ schema | tojson(indent=2) }}` unchanged, because `decide` does not go
through this check and its golden bytes must not move. A template that
spells the schema any other way fails the check loudly at its first call in
tests, rather than silently in production.

`generate(schema=...)` also refuses (`ValueError`, before any call):

- a schema that fails `schemas.check`;
- a prompt that ends in an assistant turn, or a `PreparedMessages` carrying
  per-attempt tails (`model_guidance.PreparedMessages.with_tails`). 01
  section 7.2 states that structured output is incompatible with prefill, and
  a tail chooser could pick a prefill ending for one attempt and not another.

### 3.4 The facade invariant: a flagged target was sent a schema

`LLMClient._streamed` (`llm.py:1383-1394`) gains one rule. When `schema` is
None, every target on the chain is sent unflagged
(`dataclasses.replace(t, structured=False)` on a new chain). So "flagged"
means "this attempt was sent the envelope" on every path, and
`_structured_share`'s premise becomes an invariant rather than a remark about
which caller sets the flag. `_structured_share`'s docstring changes to say so.
`decide` always sends a schema, so its behaviour does not change.

### 3.5 A refused field is re-sent without it (01f-C2)

The re-send leaves `_ask` and becomes one helper, which both operations call:

```python
# inference.py
async def _without_refused_mode(error: llm.SchemaRefusalError,
                                send: Callable[[wire.Chain], Awaitable[T]]
                                ) -> T:
    """Each attempt `error` names (`error.attempts[i]`, the refusing ones)
    once more, alone and without the mode, in order; the first that answers
    wins. Should every re-send fail, raise `llm.routes_failed(words)` with
    each re-sent route's word replaced by its re-send's failure."""

def _stream_without_refused_mode(error: llm.SchemaRefusalError,
                                 open_stream: Callable[[wire.Chain], AsyncIterator[str]]
                                 ) -> AsyncIterator[str]:
    """The streamed twin: the same order and the same composed error, but a
    re-send's failure is discovered while iterating, so this is an async
    generator rather than an awaitable. Both share the attempt-selection and
    error-composition code."""
```

Both `send` / `open_stream` closures are **nested inside `generate`**, so
`test_usage_guard.FORWARDERS` (`test_usage_guard.py:123-138`), which clears
facade calls made inside a top-level `def generate` that forwards its `usage`
parameter, still covers them. `decide`'s re-sends stay inside `_ask`.

- **`decide`** keeps its behaviour exactly. `_ask` calls the helper with a
  `send` that opens its own meter per re-send, as today.
- **`generate(stream=False)`** catches `SchemaRefusalError` carrying
  attempts and calls the helper with a `send` that calls `client.complete`
  on the **caller's holder** (`usage`). The meter stays at the call site,
  which is what `test_usage_guard.py` requires of `generate`. The row filed
  describes the attempt that answered. Its `attempts` is the re-send's count
  plus the count the refused call had reached. The holder still holds the
  refused call's last stamp (`attempts` under `llm._stamp`, `llm.py:543-590`)
  when the helper starts, so it is read there and added after. Nothing else
  in the holder survives the re-send's `_stamp`. That is the facade's existing
  rule, "a failed attempt's provider-side charge is NOT in the ledger".
- **`generate(stream=True)`** wraps the facade iterator. A
  `SchemaRefusalError` can only be raised before any text was yielded:
  `_resilient` re-raises the raw provider error once `sent` is true
  (`llm.py:1062-1063`), never the subclass. So the wrapper can re-send
  without retracting anything, and it is the same helper with a
  stream-shaped `send`.
- **Never a health failure.** `_resilient` already does not `_observe` a
  schema refusal, and a re-send is an ordinary call whose outcome is
  observed as any other.
- **Logged once per refusal** at warning, as today (`llm.py:1058`),
  naming the connection label and the provider's sentence. No prompt text.
- **No cache of "this model refused".** A refusal is a sign that the
  catalog over-advertised the mode. The remedy is the user's facts override
  (`structured_output: no`), which the warning's sentence names. Remembering
  it silently would be a second source of capability truth outside
  `capabilities.resolve_caps`.

### 3.6 Reading the reply

Structured mode makes a conforming reply likely. It does not make one
certain, and the prompt-only attempts guarantee nothing. So `generate`
returns **text** exactly as before, and the caller parses it. `schemas`
gives callers one tolerant reader, `find_value` (section 3.1). Semantic
validation (ids that exist, dates that parse) stays with each caller, as
`suggest.parse_output` and `absorb.parse_output` do today. A reply that does
not parse is the caller's "no JSON" outcome, never an exception from the
operation.

### 3.7 The ledger and the capture

- **The chain a call sends is a pure function a capture can be handed.**
  `inference.call_chain(resolved, *, schema=None, max_tokens=None) ->
  wire.Chain` builds the per-call chain (structured flags of 3.2, the cap of
  3.9). `generate` sends exactly what it returns. A caller that records the
  prompt hands the same chain to `routes.common._record_prompt(conn=...)`, so
  the prompt log reports the cap and the structured flag the call was sent
  with, not the preset's. Today `_record_prompt` records only
  `llm_sampling.report(conn.primary)` for the chain it is handed, before the
  call (`routes/common.py:406-450`), and `generate` exposes nothing, so the
  earlier claim that "the capture already shows whether the mode was sent"
  was not true. This makes it true.
- No new ledger field. A re-send without the mode is visible in the
  incoming-response capture as its own `call_id`. Section 9 keeps the
  ledger question open for 01a.
- The incoming-response capture (`llm_capture`) records each attempt and
  each re-send as its own `call_id`, as the facade already does per call.

### 3.8 Adopters

01f lands the mechanism and **one pilot**. Every other adopter converts in
its own change, behind its own evidence.

| Task | Parser today | Schema shape | Notes |
|---|---|---|---|
| `intent` (**pilot**) | `suggest._extract_json` → `parse_intent` | `{title: string, date: string, location: enum of location ids or "", cast: array of enum tokens}` | Small and closed. The enums are what the mode buys: the model cannot invent an id where the provider enforces them. Enum sizes are bounded by `MAX_ENUM_VALUES`. A campaign past that bound, or one with **nothing to offer** (no locations, no available cast), sends that field as a plain string or string array, because an empty `enum` is refused (3.1). `parse_intent` already drops unknown ids. The pilot runs through `common.draft_completion`, which gains `schema=` and `max_tokens=` keywords passed to `generate` only when given, so the other fourteen draft routes send exactly what they send today. |
| `suggestions` | `suggest._extract_json` | array of suggestion objects | Variant prompts (`scene_suggestions/instruction/*`). Convert after the pilot. |
| `scenario`, `character-from-passage` | `absorb.parse.extract_object` | `{characters: [...], entries: [...]}` | Card import paths, with no campaign. |
| `tracker-update` | its own | per-character state | On every post, so its cassettes are the most numerous. |
| `absorb`, `dossier`, `audit` | `absorb.parse` | large | The absorb contract is guarded by the evals (`evals/run.py`). Convert only behind them. |
| `tagline` | first line of text | — | Not JSON. Not an adopter. |
| `history_plan` (10-C1) | new | 10's plan | Born structured. |
| 12's final record, 01g's finalize turn | new | consumer's | Born structured. |

The pilot changes `templates/scene_intent/system.j2` to render the schema
(section 3.3). That touches `scripts/verify_templates.py`, the
`campaign_flow` cassette matchers that `test_llm_fakes.py` renders, and
`evals/run.py`'s offline render if the template is in its corpus. All three
are part of `make check`, which is the point: a reworded prompt fails
loudly.

### 3.9 A per-call output cap: `generate(max_tokens=)`

10's query plan (10-C1) is a short structured reply on Fast, and must not
inherit a role-play preset's long cap. 01g's turns need the same thing (its
run budget sends a per-turn cap). Today the only output cap is a sampler
preset's `max_tokens`, chosen per route rather than per call.

```python
# wire.py
def with_output_cap(self, n: int) -> Target:
    """A NEW target whose sampling `max_tokens` is min(the preset's, n), or
    n when the preset sets none. The preset's id, name and scope are kept, so
    the ledger still names the preset the call was sent with. The new
    `Sampling.call_cap` field records n, so a refusal can tell a cap the CALL
    added from one the user's preset carried."""

# inference.py
generate(task, messages, *, client, resolved, usage=None, schema=None,
         max_tokens: int | None = None, stream=True)

def cap_sent(target: wire.Target) -> bool:
    """Whether this attempt's adapter will put `max_tokens` (or its
    translation) on the wire: `"max_tokens" in llm_sampling.sent_names(target)`.
    Pure. 01g's spend guard asks it."""
```

- With `max_tokens`, `generate` sends a per-call chain whose every target is
  `with_output_cap(max_tokens)`. This is the same per-call, new-target rule as
  section 3.2, so the resolution is never mutated. A positive int is
  required; anything else is a `ValueError` before any call.
- `llm_sampling.effective` translates the cap per adapter exactly as it does a
  preset's: `max_completion_tokens` on the OpenAI API, and the required
  `max_tokens` on the Anthropic API, where adaptive thinking's budget is
  already held to half of it.
- **The cap is sent only where the attempt's adapter sends `max_tokens`.**
  The Claude Agent SDK takes no sampling. An OpenRouter model whose cached
  catalog `supported_parameters` omits `max_tokens` has it dropped as
  `UNSUPPORTED` (`llm_sampling.py:298-316`), logged at debug. In both cases
  the cap is silently not a bound. `cap_sent(target)` says so before the
  call, and the capture's controls report (through `call_chain`, 3.7) shows
  it after. A caller that needs the cap to be a bound (01g under a spend
  ceiling) must ask `cap_sent`; 10's cap is a cost preference and can live
  without it.
- Where 01i-C1 knows the model's maximum output, the sent cap is also capped
  at it.
- **A refused call cap is worded as the call's, not the user's preset's.**
  The cap travels as a sampler field, so a 400 naming `max_tokens` is still
  read by `llm._preset_refusal`, and the fallback is still skipped. The
  fallback would be sent the same cap, and the call carries no preset of the
  user's that could be blamed or fixed. But when `sampling.call_cap` is set
  and the preset itself sent no `max_tokens`, the error is
  `CapRefusalError` (a `PresetRefusalError` subclass, same kind and status),
  reading "this call's output cap (max_tokens) was refused", not 'carried
  sampler preset "?"' (`llm.py:774`). It is never re-sent without the cap:
  01g under a ceiling cannot use an uncapped call, and 10 treats the failure
  as its skip. An endpoint that refuses `max_tokens` is the user's to fix
  with a provider setting (`sampler_support`).
- A cap that cuts a structured reply short leaves unparseable JSON. That is
  the caller's "no JSON" outcome (section 3.6), so a caller sizes the cap to
  its schema.

## Slices

Landing order within this spec: S1 → S2 → S3 → S4. S4 (the pilot) can
land in parallel with S3 if `draft_completion` gains only `schema=` there;
as written it takes both keywords, so it waits for S3.

### 01f-S1: The portable-schema leaf

- **Delivers:** 01f-C3 (full)
- **Needs (this spec):** none
- **Needs (other specs):** none
- **Needs (slices):** none
- **Scope:**
  - New stdlib-only `grimoire/schemas.py`: `check` (the strict subset,
    including the bound and empty-`enum` refusals), `render`, `find_value`,
    and the strict-mode budgets moved there from `decisions.py`, which
    re-exports them.
  - `decisions.find_object` becomes a dict-only wrapper over `find_value`.
  - `prompts._env` registers `schema_json`.
  - No call site changes behaviour; `decide/system.j2` is untouched.
- **Acceptance:**
  - `test_schemas.py`: subset refusals, the `schema_json` filter equal to
    `render`, `decide/system.j2` bytes unchanged, `find_value` cases, and
    the AST stdlib-only check;
  - every `decisions.schema(...)` passes `check`;
  - `test_decide_chain_golden.py` is unchanged.
- **Size:** S

### 01f-S2: Structured mode on `generate`, and the shared refusal re-send

- **Delivers:** 01f-C1 (part: per-attempt structured mode on
  `generate(schema=)`, with the in-prompt check and the prefill/tails
  refusals); 01f-C2 (full)
- **Needs (this spec):** 01f-S1 (H)
- **Needs (other specs):** none
- **Needs (slices):** 01f-S1 (H)
- **Scope:**
  - `resolve.structured_capable`, and `generate`'s per-call structured
    chain (3.2).
  - The in-prompt check and the other pre-send refusals (3.3).
  - The facade invariant that a target is unflagged when no schema is sent
    (3.4).
  - `_without_refused_mode` and its streamed twin, extracted from `_ask` and
    used by both `decide` and `generate`, with the closures nested in
    `generate` (3.5).
  - No call site passes a schema yet, so every request is byte-for-byte
    unchanged.
- **Acceptance:**
  - `test_inference_generate.py`: flagging per capability; the resolution
    unchanged; no envelope without a schema; each refusal raises with no
    request sent.
  - The re-send tests with `RefusingProvider`: primary, fallback, both, one
    row with summed `attempts`, no health observation, and the streamed
    path.
  - `decide`'s re-send tests pass unchanged.
  - The `_structured_share` invariant test.
- **Size:** M

### 01f-S3: The per-call output cap

- **Delivers:** 01f-C1 (full: adds `generate(max_tokens=)`,
  `wire.Target.with_output_cap`, `Sampling.call_cap`, `cap_sent`,
  `call_chain` and `CapRefusalError`)
- **Needs (this spec):** 01f-S2 (H)
- **Needs (other specs):** 01i-C1 (S: `wire.Limits.max_output` on the
  target. Until it lands, the cap is `min(preset's, max_tokens)`.)
- **Needs (slices):** 01f-S2 (H), 01i-S1 (S)
- **Scope:**
  - `wire.with_output_cap` and `Sampling.call_cap`.
  - `generate(max_tokens=)` built through `inference.call_chain`, together
    with the schema flags.
  - `inference.cap_sent`.
  - `llm._preset_refusal` raises `CapRefusalError`, worded as the call's,
    when only a call cap carried `max_tokens`.
  - Nothing passes `max_tokens` yet.
- **Acceptance:**
  - the cap is sent where `cap_sent` is True, and absent on the Claude SDK
    and on an OpenRouter model whose catalog omits `max_tokens`;
  - the resolution is unchanged;
  - a 400 naming `max_tokens` on a call cap raises `CapRefusalError`;
  - `call_chain` equals the chain the facade was sent.
- **Size:** S

### 01f-S4: The `intent` pilot

- **Delivers:** none (the pilot adopter of section 3.8; every contract item
  is already delivered)
- **Needs (this spec):** 01f-S2 (H); 01f-S3 (H, for `draft_completion`'s
  `max_tokens=` keyword)
- **Needs (other specs):** none
- **Needs (slices):** 01f-S2 (H), 01f-S3 (H)
- **Scope:**
  - `templates/scene_intent/system.j2` renders the intent schema with
    `schema_json`.
  - `routes/scenes.post_scene_intent` builds the schema: enums of location
    ids and cast tokens, or plain strings where there is nothing to offer or
    the enum budget is exceeded.
  - `common.draft_completion` gains `schema=` and `max_tokens=`, passed only
    when given, so the other fourteen draft routes are unchanged.
  - The `campaign_flow` cassette and `verify_templates.py` are updated in the
    same PR.
- **Acceptance:**
  - the prompt carries the rendered schema;
  - a conforming reply parses as before;
  - an out-of-enum id is dropped by `parse_intent`;
  - `verify_templates.py` and `test_llm_fakes.py` are green.
- **Size:** S

## 4. Contract

**01f-C1 — `generate(schema=...)` requests a provider's structured mode per
attempt, with the schema still in the prompt, and takes a per-call
`max_tokens` cap.**

- Input: `inference.generate(task, messages, *, client, resolved, usage,
  schema: dict, max_tokens: int | None, stream)`. The `resolved`, `usage`
  and `stream` rules are unchanged.
- Refused before any client call (`ValueError`, so no holder is stamped and
  no row is filed):
  - a schema that fails `schemas.check`;
  - a prompt that does not contain `schemas.render(schema)`;
  - a prompt ending in an assistant turn, or one carrying per-attempt tails;
  - the existing task, operation and empty-resolution refusals.
- Each attempt on the chain is sent its provider's structured mode exactly
  when its model's `structured_output` resolves `yes`. Every other attempt is
  sent the request it would have been sent without the schema. The
  resolution is never mutated.
- `max_tokens` (section 3.9), with or without a schema, caps each attempt's
  output at `min(preset's cap, max_tokens, 01i-C1's max output where known)`
  through `wire.Target.with_output_cap`, on new per-call targets, **where
  that attempt's adapter sends `max_tokens`** (`inference.cap_sent`). It is
  not sent on the Claude Agent SDK, nor to an OpenRouter model whose catalog
  omits the parameter. A refused call cap is `CapRefusalError`, worded as
  the call's. A value that is not a positive int is a `ValueError` before
  any call.
- `inference.call_chain(resolved, *, schema, max_tokens)` is the pure
  function that builds the chain a call sends, for a caller's prompt
  capture.
- Output: the reply text, as today. Nothing about the reply is guaranteed to
  conform.

**01f-C2 — a refused schema field is re-sent without it, and is never a
health failure.**

- When every route of a call has failed and at least one route's failure was
  its provider refusing the structured field, each refusing attempt is sent
  once more, alone, without the mode, in route order. The first answer wins.
  If all fail, the error is the routes' failures composed afresh
  (`llm.routes_failed`).
- `decide` and `generate` share the one helper. `decide` files a row per
  re-send, as today. `generate` files one row through the caller's meter, for
  the call that answered, with `attempts` counting every attempt the call
  made.
- No refusal marks a connection failing, and no capability is written.
- On the streamed path, a re-send happens only before any text has reached
  the caller, which `_resilient` guarantees.

**01f-C3 — one portable-schema rule and one tolerant reader.**

- `schemas.check(schema)` is the subset in section 3.1, raising
  `SchemaError(ValueError)`. `decisions.schema` output always passes it.
- `schemas.render(schema)` is the prompt spelling,
  `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)`.
- `schemas.render(schema)` is also the prompts filter `schema_json`.
  Templates for `generate` schemas use it, and `decide/system.j2` keeps
  `tojson`.
- `check` refuses bounds (`min*`, `max*`, `pattern`, `format`) and an empty
  `enum`. A consumer enforces bounds in its parser.
- `schemas.find_value(text)` never raises. It returns None when the reply
  holds no JSON value. `decisions.find_object` keeps its dict-only contract.

## 5. Interaction with repo rules

- **`test_routing_guard.py`.** No new door: `client.stream` and `complete`
  are still spelled only in `inference.py`. The re-send's `send` closures
  live there.
- **`test_usage_guard.py`.** `generate`'s facade calls still forward the
  caller's `usage`, so they stay `FORWARDERS`. The re-send passes the same
  holder. `decide`'s per-re-send meters are unchanged.
- **`test_operation_guard.py`.** Unchanged. A schema is not an operation.
- **`test_wire.py`, `test_import_guard.py`.** `schemas.py` is stdlib-only and
  imported by `decisions` and `inference`, so the module graph stays acyclic.
  `decisions` binds the module (`from . import schemas`), never names off it,
  per the import rule.
- **Android / pydantic v1.** No pydantic is involved. Schemas are plain dicts.
- **Templates.** `verify_templates.py`, `test_llm_fakes.py` and the offline
  evals cover any adopter's template change (section 3.8). Cassettes are
  hand-authored replies, so an adopter's cassette gains a conforming JSON
  body. It does not need a "structured" marker, because the fakes never
  enforce a schema.
- **Privacy.** The warning on a refusal names a connection label and the
  provider's error text, as today. Schemas are code, not store content.
  The adopter enums (location ids, cast tokens) go to the provider inside the
  schema, as they already go inside the prompt. The refusal warning quotes
  the provider's sentence, and a strict-mode 400 can echo enum values, which
  for an adopter are store ids (location slugs, cast tokens). That is within
  CLAUDE.md's logging allowance ("ids, occasionally a name"), and no prose
  reaches a log.
- **The cost rule.** Unchanged. A re-send is a call like any other, and its
  price lands in whichever column `cost_basis` decides.

## 6. Interaction with 01s (inference settings group)

None needed. `structured_output` is already a listed capability with
provenance (`ProvidersView.tsx:39`). Whether a generate route *would* use the
mode is not a setting. A Models-page note such as "this route asks for JSON,
and this model will be held to it" is optional polish and is not part of this
spec.

## 7. Tests and acceptance

- `test_schemas.py`:
  - `check` accepts the subset and refuses each excluded keyword, a type
    array, an object missing `additionalProperties: false`, a `required`
    that omits a property, depth past `MAX_DEPTH`, and each budget past its
    constant;
  - every `decisions.schema(...)` the decide tests build passes;
  - an empty `enum` and each bound keyword (`maxItems`, `maxLength`,
    `minimum`, ...) are refused;
  - the `schema_json` filter renders exactly `schemas.render`'s text, for
    keys out of order and a description holding `'`, `<` and `&` (the
    case `tojson` would have escaped);
  - `decide/system.j2`'s rendered bytes are unchanged;
  - `find_value` reads an object or an array; `decisions.find_object`
    still answers None for a bare array, and its existing cases pass;
  - the leaf imports only the standard library (an AST check).
- `test_inference_generate.py` (extended):
  - a call with a schema flags exactly the attempts whose
    `structured_output` is `yes`, and leaves an `unknown` and a `no`
    unflagged;
  - the resolution's targets are identical before and after the call;
  - a call without a schema sends no envelope even on a capable model;
  - each refusal in 01f-C1 raises before any client call, which a
    `RecordingProvider` that saw nothing proves;
  - `max_tokens` sends the cap on every attempt that `cap_sent` answers True
    for, leaves the resolution unchanged, and is absent on the Claude SDK and
    on an OpenRouter model whose catalog omits `max_tokens`;
  - a 400 naming `max_tokens` on a call cap (no preset cap) raises
    `CapRefusalError` worded as the call's;
  - `call_chain` returns exactly the chain the facade was sent.
- Re-send tests, using `RefusingProvider` from `backend/tests/llm_fakes.py`:
  - a primary that refuses the field is answered by its re-send;
  - a fallback that refuses after the primary failed is re-sent;
  - both refuse: two re-sends, and the composed error if both fail;
  - one row per `generate` call, its `attempts` counting all;
  - no health observation for the refusal;
  - the streamed path re-sends and yields only the re-send's text;
  - `decide`'s existing re-send tests pass unchanged.
- The facade invariant: `_structured_share` is empty for a target that was
  flagged when no schema was sent (section 3.4).
- The pilot (`intent`):
  - the route's prompt carries the rendered schema;
  - a conforming reply parses as before;
  - a reply naming an id outside the enum is dropped by `parse_intent` as
    before;
  - the cassette is updated, and `verify_templates.py` is green.

Acceptance: the existing decide suites pass unchanged, and a generate call
without a schema is byte-for-byte the request it was at the baseline.
`test_format2_play.py` is the turn path's witness for that.

## 8. Non-goals

- Making structured mode required on any route, or refusing a model at the
  seam for lacking it.
- A `structured_output` probe in the model test. It would need a schema that
  every provider accepts and that a refusal can be told apart from. Worth
  doing, but its own change, under "a settings surface never spends unasked".
- Validating a reply against the schema inside the operation.
- Tool calling (01g), and a forced-tool emulation of structured output.
- Converting adopters beyond the pilot.
- Structured output on the Claude Agent SDK. The installed SDK (0.2.159) has
  `ClaudeAgentOptions.output_format` (json_schema) and
  `ResultMessage.structured_output`, so the preset's `never` is a choice
  about this adapter today, not a fact about the SDK. Revisit with 01g-C2b,
  which raises the SDK floor anyway.
- A bound-stripping helper. A consumer drops bounds from the schema it
  sends (3.1).

## 9. Open questions

1. **Should `unknown` be flagged and rely on the re-send?** That would get
   enforcement from providers whose catalog says nothing, at the cost of one
   failed round trip per call where the field is refused, and a strict local
   server refuses every time. *Recommendation:* no, as written. `yes` only,
   with the facts override as the user's lever.
2. **Should the ledger row say whether the mode was sent?** For example a
   `structured` field: `sent`, `dropped` (re-sent without), or absent.
   *Recommendation:* defer to 01a. If its comparison tables need it, add it
   there. The capture shows it through `call_chain` (3.7).
3. **Which pilot?** *Recommendation:* `intent`. It is the smallest closed
   schema, its enums are where the mode adds the most, and nothing else
   reads its output. `suggestions` is the higher-value second, once the
   pilot has run for a while.
4. **OpenRouter provider routing.** OpenRouter may route a request to an
   upstream that ignores `response_format` unless the request asks it to
   honour every parameter. *Recommendation:* do not send that routing
   preference in 01f. The schema in the prompt covers an upstream that
   ignores the field, and requiring parameter support could turn an answer
   into a "no endpoint" 404. Revisit with 01a's numbers if parse failures
   show up on OpenRouter.

## 10. Review record

**Substitute spec gate, 2026-10-09** (adversarial review: 2 blocking, 6
should-fix, 4 minor). Each item was checked against the code. Each was then
fixed as noted, or rejected with a reason.

| Item | Verdict | Where |
|---|---|---|
| B1 `render` cannot equal Jinja `tojson` (which sorts keys and HTML-escapes) | Fixed. One renderer, `schemas.render` (`sort_keys=True`, no escaping), registered as the `schema_json` filter and used by both the templates and the check. `decide/system.j2` keeps `tojson`. Confirmed against `prompts.py:30-33`. | 3.1, 3.3, C3, 7 |
| B2 10's schema uses bounds the subset refuses | Fixed on 10's side, per the coordinator: 10 drops the bounds from the schema it sends and enforces them in its parser. 01f keeps the subset strict and says so, with no stripping helper. | 3.1, 8 |
| S1 the cap is not always sent | Fixed. `cap_sent(target)`, and C1 says "where the adapter sends `max_tokens`". Confirmed at `llm_sampling.py:298-316`. | 3.9, C1 |
| S2 a refused call cap reads as the user's preset | Fixed. `Sampling.call_cap` and `CapRefusalError` worded as the call's, never re-sent without the cap. | 3.9, C1 |
| S3 the capture does not show the mode or the cap | Fixed. `inference.call_chain`, handed to `_record_prompt`. The earlier claim is withdrawn. | 3.7, C1 |
| S4 empty `enum` | Fixed. `check` refuses it, and the pilot sends a plain string field instead. | 3.1, 3.8 |
| S5 `find_object` widening would change `decide` | Fixed. `find_value` is new, and `decisions.find_object` keeps its dict-only contract. | 3.1, C3 |
| S6 plumbing (`draft_completion`, `FORWARDERS`, the streamed re-send shape) | Fixed. `draft_completion` keywords, closures nested in `generate`, and a streamed twin helper. | 3.5, 3.8 |
| M1 nesting depth | Fixed (four deep). | 3.1 |
| M2 the stdlib-only check needs its own test | Fixed (`test_schemas.py`). | 3.1, 7 |
| M3 the SDK has `output_format` | Noted under non-goals. | 8 |
| M4 refusal warnings can echo enum ids | Stated in the privacy bullet. | 5 |

Slices added (4 slices).
