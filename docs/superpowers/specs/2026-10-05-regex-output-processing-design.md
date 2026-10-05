# Output processing — the regex pipeline

**Date:** 2026-10-05
**Status:** Design approved in conversation; awaiting the spec adversarial-review gate.

An ordered list of find-and-replace rules applied to transcript text: the
feature nearly every SillyTavern setup leans on to strip reasoning tags, fix
quotes, rename, and trim unfinished sentences. Grimoire has none of it today —
no `<think>` stripping, no quote fixing, no sentence trimming anywhere in the
backend. Reasoning a provider returns in its own field is already split off
(`llm_reasoning.from_chunk`); reasoning a model writes *inline* lands in the
transcript verbatim and is then fed back into every later prompt.

## 1. Decisions

| Question | Decision |
|---|---|
| What a rule's "scope" means | **Two axes.** `targets` (model output, user input) × `applies` (display, prompt). The four named scopes are editor presets over them. |
| Where display rules run | **On the server, one engine.** Python `re` everywhere — display, prompt and store — so the test pane, the screen and the prompt cannot disagree. |
| Streaming | Display rules apply while a reply streams, via `keep`/`tail` frames (§5.3). |
| Stored text | Raw by default. A rule may opt into `rewrite_stored`; that rewrite runs **only as new text lands**, never in bulk over an existing transcript, and is recorded with the original. |
| Levels | Global, world, campaign, connection. Run order: connection → global → world → campaign. Lower levels may switch an inherited rule **off**, never edit it. |
| Exports | Markdown / HTML / text / EPUB use display text; the JSON export stays raw. |
| SillyTavern import | Patterns are translated by a lexer with three verdicts (exact / approximate / won't translate). Untranslatable scripts are reported, never imported as a guess. |

## 2. The rule

```json
{
  "id": "r-7f3a9c",
  "name": "Strip reasoning tags",
  "enabled": true,
  "pattern": "<think>[\\s\\S]*?</think>\\s*",
  "flags": "gi",
  "replacement": "",
  "trim": [],
  "targets": ["model"],
  "applies": ["display", "prompt"],
  "rewrite_stored": false,
  "min_depth": null,
  "max_depth": null,
  "imported": null
}
```

- **`id`** — minted on create (`r-` + random hex), unique across every level,
  because an `off` list in another level names it. Never reused.
- **`pattern`** — Python `re` syntax. Validated by compiling on every save.
  A rule whose pattern does not compile may be saved only with
  `enabled: false`; enabling it is refused with the compile error.
- **`flags`** — a subset of `g i m s a`:
  `i` → `re.IGNORECASE`, `m` → `re.MULTILINE`, `s` → `re.DOTALL`,
  `a` → `re.ASCII` (makes `\w \d \b` match JavaScript's ASCII meaning; the
  importer sets it), `g` → replace every match (otherwise the first only, as
  in JavaScript).
- **`replacement`** — SillyTavern's syntax, kept so imported replacements need
  no translation: `$1`…`$99`, `$<name>`, `$&` and `{{match}}` (the whole
  match), `$$` (a literal `$`). Expanded by Grimoire's own replace function,
  never by `re.sub`'s template parser. A reference to a group the pattern does
  not have is a validation error on save. No other macros are expanded in
  replacements in this version.
- **`trim`** — strings removed from the matched text before it is substituted
  for `{{match}}` / `$&` (SillyTavern's `trimStrings`).
- **`targets`** — non-empty subset of `model`, `user`. `model` is an
  assistant-role post that is not a synthetic speaker; `user` is a player post.
  **Synthetic lines are never touched**: director notes, cast transitions,
  roll lines and anything in `SYNTHETIC_SPEAKERS`.
- **`applies`** — subset of `display`, `prompt`. May be empty only when
  `rewrite_stored` is true (a pure write-time rule).
- **`rewrite_stored`** — §5.1.
- **`min_depth` / `max_depth`** — optional, inclusive. Depth 0 is the newest
  message of the stored transcript; depth is counted over *real posts and
  synthetic lines alike*, on the transcript as stored (before any merging or
  projection), so it means the same thing in display and prompt. Ignored by
  the `store` phase (new text is always depth 0).
- **`imported`** — `null`, or `{"from": "sillytavern", "pattern": "/…/gi",
  "notes": ["…"]}`: the original and the translator's notes, shown in the
  detail view.

The four presets the editor offers, as settings of the two axes:

| Preset | targets | applies |
|---|---|---|
| Model output | model | display, prompt |
| User input | user | display, prompt |
| Display only | model, user | display |
| Prompt only | model, user | prompt |

## 3. Storage and layering

One JSON file per level, shaped `{"rules": [...], "off": ["r-…", …]}`:

| Level | File | Writer |
|---|---|---|
| Global | `<home>/regex.json` | `atomic.write_text`, no campaign lock |
| World | `<world_root>/regex.json` | `atomic.write_text`, no campaign lock (as the tracker's world layer) |
| Campaign | `<campaign_root>/regex.json` | under `locks.campaign_lock(cid)` |
| Connection | `<home>/llm_connections/<id>.regex.json` | `atomic.write_text`; deleted with its connection |

All paths resolve through `store.paths` / the world, campaign and connection
resolvers (`test_paths_guard.py`). The connection record itself (`<id>.md`)
is not touched — its frontmatter is flat strings, and a rule list does not fit
it. The global file has no `off` list (nothing above it).

**Reading never raises.** A file that does not parse reads as
`{"rules": [], "off": []}` and logs one error through `store/logs.py`; a single
rule that fails validation on read is dropped and logged, the rest of the file
still loads. A hand-edited or half-synced file must not take the play view down.

**The effective list for one message**, given (world, campaign, producing
connection):

1. that connection's rules, in list order;
2. global rules;
3. world rules;
4. campaign rules;

then drop any rule whose id is in the world's or the campaign's `off` list, and
any with `enabled: false`. Connection rules run first because they correct one
model's habits (its reasoning tags, its quote style), and every later, more
general rule should see text that is already clean.

The world may switch off global and connection rules; the campaign may switch
off global, world and connection rules. Switching off is the only way to
change an inherited rule — to change one, switch it off and add your own,
which is the tracker layer's rule (`store/tracker/fields.py`).

**Compiled-pattern cache.** Each level file's content hash keys a compiled
list, so a read does not recompile; the cache is per process and bounded.

### 3.1 Which connection produced a message

Not recorded today: the usage ledger knows (`llm._stamp`, `usage[ATTEMPTED]`),
but neither the transcript line nor the response variant does. From now on:

- `character_turns._save` and `streaming._persist_reply` record the
  **connection id** that actually served the attempt (after any fallback), read
  from `meter.usage[llm.ATTEMPTED]`;
- it is stored as `connection` in the per-message metadata comment — added to
  `serialize.RESPONSE_METADATA`, so every whole-transcript rewrite round-trips
  it — and on the response variant in `responses.json`.

A message with no `connection` (every post written before this ships, every
player post, greetings) gets **no** connection-level rules. A connection that
has since been deleted contributes none either. There is no backfill: the
ledger's attribution is per post index, which is not stable enough to stamp
history from (CLAUDE.md, "Costs").

## 4. The engine — `store/regex/`

A leaf package: it imports nothing else from the store except through the
resolvers it needs to read its files, and nothing in it writes a transcript.

- `rules.py` — the rule schema, validation, id minting, the replacement
  expander.
- `layers.py` — read/write per level, `effective(ctx)` for a context
  `{wid, cid, connection}`.
- `apply.py` —
  - `run(text, rules, *, role, phase, depth) -> str`;
  - `trace(text, rules, *, role, phase, depth) -> list[Step]`, one step per
    rule in run order: `{rule_id, level, name, applied: bool, reason, matches,
    text_after}`, where `reason` names why a rule did not apply (disabled,
    switched off, wrong target, wrong phase, outside depth, runtime error).
  - `view(messages, ctx, phase) -> list[dict]` — copies of the messages with
    `content` transformed, depth computed over the list given. The only
    function callers outside the package use.
- `translate.py` — the SillyTavern pattern translator (§7).

**Runtime failures.** A rule that raises at apply time is skipped for that
message and logged once per (rule, revision); the text passes through it
unchanged. An apply whose result would exceed a fixed multiple of its input
(a replacement that expands every match) is treated as that kind of failure.

**Backtracking.** Python `re` has no timeout; a catastrophically backtracking
pattern can hang the worker that applies it — the same exposure SillyTavern has
in the browser tab. Accepted and documented: rules are user-authored, the test
pane is where a bad one shows itself first, and a subprocess sandbox costs more
than it buys. The editor's help text says so.

## 5. Phases

### 5.1 `store` — opt-in rewrites as text lands

Rules with `rewrite_stored: true` run at exactly these seams, right beside the
existing `expand_macros` call:

- `routes/character_turns._normalise` — a model reply (role `model`);
- `routes/streaming._persist_reply` — the legacy reply path (greeting first
  post, mechanics continuation, `_chat_stream` fallbacks);
- `routes/scenes._chat_run` — the player's post (role `user`);
- the message-edit route (`PUT .../messages/{index}`) — applied to the text the
  player typed, with that message's role.

When the store phase changes the text, the original and the ids of the rules
that fired are recorded in `<campaign_root>/rewrites/<scene identity>.json`,
keyed by the message's `response_id` (model) or `post_id` (player), and the
message's metadata comment gets `rewritten: 1`. Keyed by the scene's minted
**identity** rather than its `sid`, so the record survives a rename or a
re-pad without being moved; `lifecycle.delete_scene` unlinks it with the
scene's other sidecars. Written under the campaign lock that the transcript
write already holds. Only the *latest* rewrite of a message is kept: an edit
that is rewritten again replaces the record, holding the text the player
submitted on that edit.

A reply that lands without a `response_id` (a legacy path with no variant) or a
post without a `post_id` is still rewritten, but there is nothing to key the
record by — so the store phase mints a `post_id` for it before the append.

**Restore original** (turn history) is an ordinary message edit writing the
recorded original back, through the same route — which means it is refused
with `scene_busy` while a turn or review holds the scene, like every other
edit. The restore clears the record and the flag, and does **not** re-run the
store phase on the restored text (it would rewrite it straight back).

### 5.2 `prompt`

`regex.view(messages, ctx, "prompt")` runs before **every** LLM reader of
transcript text:

- `store/context/assemble.py` before `story._project_history` (every turn and
  director prompt);
- `chronicle.transcript_text` callers that build a prompt — absorb, the
  mechanics audit, the rolling summary, the scene-break check, dossiers;
- the response selector (`character_turns`' last-12-posts read), the tracker
  update prompt, and the voice-drift judge.

`transcript_text` itself does not apply rules — exports call it too and need
the display phase — so each caller passes it an already-viewed list. A guard
test (`test_regex_prompt_guard.py`) parses the routes and store for
`read_scene(...)` results that reach a prompt builder without passing through
`regex.view`, in the style of the existing AST guards, so a new prompt reader
cannot silently skip the pipeline.

Recent-text world-info scanning (`assemble.recent_text`) reads the prompt view
too: a keyword that only appears inside a stripped `<think>` block should not
fire a lore entry the prompt will never show context for.

**Absorb citations.** Absorb verifies the quotes it cites against the
transcript (`store/absorb/routing.py`). It now verifies against the same prompt
view it was shown; otherwise a quote taken from cleaned text is flagged
unattributed. The view is computed once per absorb run and reused for both.

### 5.3 `display`

- **Scene read.** `GET` scene adds `shown` to a message *only when* the display
  phase changed it; the client renders `m.shown ?? m.content`. Editing a
  message still starts from `content` — the raw text is what is stored.
- **Exports.** `export._chapter` (Markdown, HTML, text, EPUB) uses the display
  view. `build_json` stays raw: it is a backup, and a backup of processed text
  would lose what the rules hid.
- **Streaming.** Today the client appends deltas to `streaming`. With display
  rules active, the turn stream also emits
  `{"display": {"keep": n, "tail": "…"}}`: the client truncates its display
  buffer to `n` characters and appends `tail`. That is what lets a closing
  `</think>` retract text already on screen. The server runs the display phase
  over the accumulated narration (after `ResponseWatcher`'s own redaction) at
  most every ~100 ms and once at the end, computes the longest common prefix
  with what it last sent, and sends the difference. Multi-part turns send part
  offsets in display coordinates. With no active display rules for the
  message's context, no display frames are sent and the stream is
  byte-identical to today's.
- `hideArtHandles` and the other existing client-side display transforms are
  unchanged and run after the server's display text.

### 5.4 What stays raw

The transcript file, response variants (except an opted-in rewrite, which is
recorded), `responses.json`'s reasoning, the JSON export, and the prompt log's
snapshots of what was sent (those record the *prompt* view, as sent). Turning
a display or prompt rule off restores every screen and every later prompt at
once.

## 6. UI

### 6.1 `RegexRulesEditor`

One component, `scope: {kind: "global"} | {kind: "world", wid} |
{kind: "campaign", cid} | {kind: "connection", id}`, built on the list/detail
pattern in CLAUDE.md (`.editor`, `.editor-list`, `.editor-body`,
`mode: "view" | "edit"`). Mounted:

- **Global** — a new **Output processing** section in `ConfigView`
  (`fields: []`, saves as it goes, like Routing and Pricing);
- **World** — a `WorldView` section under "Writing";
- **Campaign** — a `CampaignHub` settings panel beside Tracker;
- **Connection** — a block in `ConnectionEditor` under "Prompt
  post-processing".

**Rail.** Two groups. *Inherited*: read-only rows with a level tag
(`global`, `world`, `connection: <name>`) and a switch that writes this level's
`off` list; a campaign lists connection rules from every connection, since any
of them may have produced a message. *This level*: the level's own rules in run
order, each with an enabled checkbox and ↑/↓ buttons (no drag, as in
`PromptLayoutEditor`), then `+ New rule` and `Import…`.

**Detail view** (read-only by default): name as `<h3>`; pattern and replacement
in monospace; sidebar with **Edit**, flag chips, target and phase chips, depth,
`rewrite_stored`, and the translator's notes for an imported rule.

**Form.** The four presets as buttons that set the two checkbox groups, which
remain editable. Save validates server-side; a compile error is shown on the
pattern field.

### 6.2 Test pane

A collapsible panel under the rail. Paste text; choose role (model / user),
phase (display / prompt / store) and depth. `POST /api/regex/test`
`{scope, text, role, phase, depth, draft?}` — `draft` is the rule in the open
form, tested in place of its saved version (or appended, for a new rule) —
returns the trace (§4). Each step shows the rule, its level, its match count
and the text after it, with the change highlighted; steps that did not apply
are greyed with their reason. Opened from the play view, the pane offers "use a
message from this scene", which fills the text, role, depth and the message's
connection.

### 6.3 Turn history

`SceneInspector`'s Turn history marks turns whose message carries
`rewritten`. Opening one shows a before/after diff, the rules that fired, and
**Restore original** (§5.1).

### 6.4 Keyboard

No new bindings beyond what the list/detail pattern already registers.

## 7. SillyTavern import

**Accepted input:** a single regex script JSON, an array of them, or a
SillyTavern settings / preset export carrying `regex_scripts` or
`extensions.regex_scripts`. The client reads the file and posts its parsed JSON
(no multipart upload). `POST /api/regex/import/preview` returns
one row per script — the proposed rule, its verdict and notes;
`POST /api/regex/import` commits the chosen rows to the chosen level, appended
in file order. Nothing is written by preview.

**Field mapping:**

| SillyTavern | Grimoire |
|---|---|
| `scriptName`, `disabled` | `name`, `enabled = !disabled` |
| `findRegex` (`/body/flags`, or a bare body) | `pattern` (translated), `flags` (`g i m s` kept, `a` always added) |
| `replaceString`, `trimStrings` | verbatim |
| `placement` 1 / 2 | `targets` user / model |
| `placement` 3 (slash commands), 5 (world info), 6 (reasoning) | flagged and dropped from targets; a script left with no target is *won't translate* |
| `markdownOnly` / `promptOnly` | `applies` display / prompt (both set → both) |
| neither set (SillyTavern rewrites the stored chat) | `applies` display + prompt, `rewrite_stored` **false**, note says so — opting in is the user's call |
| `minDepth` / `maxDepth` | verbatim; `-1` / `null` / absent → `null` |
| `runOnEdit` | ignored, with a note: display and prompt rules apply on every read, so edits are always covered |
| `substituteRegex` ≠ 0 | flagged: macros inside patterns are not supported → *won't translate* |
| flag `u` | dropped, with a note (Python `str` patterns are already Unicode) |
| flags `y`, `d`, `v` | *won't translate* |
| replacement `` $` `` / `$'` | *won't translate* |

**The pattern translator** is a lexer over the JavaScript pattern (escapes,
classes, groups, quantifiers), emitting Python and a verdict:

- **Exact** — rewritten, meaning preserved:
  - `(?<n>…)` → `(?P<n>…)`, `\k<n>` → `(?P=n)`;
  - `\/` → `/`; `[^]` → `[\s\S]`;
  - `\u{XXXXX}` → `\UXXXXXXXX`; `\uXXXX` kept;
  - `.` without `s` → `[^\n\r  ]` (JavaScript's `.` also stops at
    `\r` and the Unicode line separators);
  - `$` without `m` → `\Z` (Python's `$` also matches before a final newline);
  - `\s` / `\S` → JavaScript's explicit whitespace class / its negation;
  - `\w \d \b` and their negations kept, with the `a` flag giving them
    JavaScript's ASCII meaning;
  - a `[` inside a class escaped (Python warns on nested-set syntax).
- **Approximate** — imported, with a note on the rule:
  - `^` / `$` under `m` (JavaScript also breaks lines at `\r`, U+2028, U+2029);
  - `i` on a pattern containing non-ASCII letters (with `a`, Python folds
    ASCII only; JavaScript folds more).
- **Won't translate** — not imported; listed in the preview with the original
  pattern and the reason:
  - `\p{…}` / `\P{…}` property escapes;
  - an empty class `[]` (JavaScript: never matches; Python: error);
  - lookbehind that Python rejects (variable width);
  - anything the lexer does not recognise, or Python fails to compile after
    translation for a reason the lexer did not predict.

The translator never guesses: a construct it has no rule for is *won't
translate*, not passed through.

## 8. Routes

- Global: `GET/PUT /api/regex`.
- World: `GET/PUT /api/worlds/{wid}/regex`.
- Campaign: `GET/PUT /api/campaigns/{cid}/regex`.
- Connection: `GET/PUT /api/llm-connections/{id}/regex`.
- Each GET returns `{layer, inherited}` — the level's own file and the read-only
  inherited rules with their level tags, the shape of `TrackerLayerBundle`.
  Each PUT replaces the level's whole file (rules in order + `off`), validated
  as a unit; one invalid rule fails the PUT with its index and error.
- `POST /api/regex/test`, `POST /api/regex/import/preview`,
  `POST /api/regex/import`.
- `PUT /api/campaigns/{cid}/regex` stamps the campaign's write token through the
  activity middleware like any campaign route. A world, global or connection
  edit writes no campaign file and so stamps none; the plan confirms no reader
  keys a cache on the token in a way that would serve stale display text.
- Pydantic models in `routes/models.py`, plain `BaseModel` fields only.
- No route here is a detached run; nothing here reserves.

## 9. Guards and docs

- `store/locks.py`: the campaign-level writer goes in `DOMAIN_MODULES`; the
  world/global/connection writers in `OUTSIDE_DOMAIN` with the reason
  (not campaign-scoped).
- Every write through `store.atomic` (`test_atomic_guard.py`); paths through
  the resolvers (`test_paths_guard.py`); imports at module scope, submodule
  bindings (`test_import_guard.py`).
- `test_regex_prompt_guard.py` (§5.2).
- The frozen campaign has no rule files, so its snapshot does not move — which
  is itself the check that rule-less stores read exactly as before.
- A CLAUDE.md section, short: stored text is raw; the four levels and run
  order; every prompt reader goes through `regex.view`; display is server-side
  and why.

## 10. Testing

- **Engine** — run order across levels; `off` at world and campaign; enabled;
  target, phase and depth filtering; synthetic lines untouched; replacement
  syntax (`$1`, `$<n>`, `$&`, `{{match}}`, `$$`, `trim`); `g` vs first-only;
  runtime-error skip; expansion cap; unreadable file reads empty.
- **Translator** — a table of SillyTavern patterns with expected Python and
  verdict, including each construct in §7, and a differential check for the
  *exact* cases on sample strings.
- **Routes** — GET/PUT at all four levels, validation errors, inherited
  bundle; test-pane trace including `draft`; import preview and commit.
- **Integration** —
  - a connection rule stripping `<think>` reaches the turn prompt and the absorb
    prompt, and does not apply to a post produced by another connection;
  - a cited quote from cleaned text is attributed;
  - display frames appear in a stream, retract text, and are absent when no
    display rule is active;
  - a store-phase rewrite is recorded and Restore original undoes it, and is
    refused while the scene is busy;
  - Markdown export uses display text; JSON export is raw;
  - `connection` round-trips through an edit and a cut.
- **Frontend** — the editor (row → read-only view with sidebar; Edit → form;
  `+ New` → form; inherited switch-off), presets, test pane, import preview,
  `shown` rendering, and display-frame application in the stream buffer.

## 11. Out of scope

- Retroactive "apply to scene" for stored rewrites.
- Rules over reasoning text, world info, or slash commands (SillyTavern
  placements 3, 5, 6).
- Macros inside patterns, and macros other than `{{match}}` in replacements.
- Per-scene rule levels.
- A regex timeout or sandbox.
