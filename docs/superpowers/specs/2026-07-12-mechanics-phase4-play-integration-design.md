# Mechanics Phase 4 — play integration

Full design for Phase 4 of the Mechanics & Dice milestone (issue #162),
superseding `2026-07-12-mechanics-phase4-play-integration-draft.md`. Depends
on Phases 1-3 (all landed). This is the payoff phase: after it, a
module-bound campaign plays with LLM-refereed, engine-resolved, truly random
checks — the LLM decides *when* a check is warranted and proposes it; the
player adjudicates; the engine does all math; the LLM narrates the result.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Outcome tiers | Named tiers with expression conditions, `[{"label", "when"}]`, first match top-down; check-level → module `_defaults` → engine fallback | System-agnostic: D&D crits (`natural == 20`) and WoD botches (`successes == 0 and ones > 0`) both express naturally; reuses the expression engine; gives Phase 5 a crisp label to validate narration against. |
| Difficulty | LLM proposes in the tag → check `difficulty` default → module `_defaults.difficulty`; always editable on the chip | "LLM referees, engine computes": situational DCs are refereeing, but the player has the last word before dice hit the table. |
| Roll tag | Fenced ` ```roll ` block, JSON `{check, actor, difficulty?, modifier?, reason?}`; model instructed to stop after it | Works with any streaming text model (no tool-calling assumed); JSON is parseable and cheap; ids are copied from context, not invented. |
| Modify scope | Difficulty + modifier inline, check swappable; actor fixed (select appears only when actor resolution failed) | Wrong-difficulty and wrong-check are the real table corrections; wrong-actor means the narration went wrong — that's what decline is for. |
| Decline semantics | Continuation told "check declined; continue without rolling"; no other trace | The scene simply proceeds; nothing mechanical happened. |
| Proposal persistence | **Durable**: one pending-proposal record per scene in `<campaign>/proposals.json` (rolls.json-style), written before the pre-fence narration persists; recoverable on scene reload; adjudication is idempotent by proposal id | Codex adversarial review (2026-07-12): the ephemeral design let a dropped SSE stream strand persisted narration at an unadjudicated decision point, and a retried accept could roll twice. Durability + idempotency close both. |
| Sheet summaries scope | Present cast + the current location's sheet (if sheeted) | Location stats (wards, hazards) are check-relevant; other sheeted entities wait for a need. |
| Rules budget | Keyword-activated rules docs capped at 6/turn; `always`/`sheet_types` docs uncapped | Bounded context growth; core digests and splat rules are the load-bearing text. |
| Manual checks | In scope: 🎲 popover gains a Check mode using the same resolution path | Exercises the engine without LLM involvement; same code path as accept. |
| Multi-roll turns | One fence per turn; a second complete fence in one buffer is ignored (logged) | Stop-after-fence is the instructed contract; tolerate violations quietly. |

## Context sections (`store/context.py` + templates)

Three new `_SECTIONS` entries with templates under
`templates/scene/sections/`, all rendering empty when
`modules.resolve(cid)` is `None`. `_assemble` gathers their data.

- **`mechanics_rules.j2` — rules digest.** Bodies of: every `always: true`
  doc; every `sheet_types:` doc whose type matches a present sheeted
  actor's `sheet_type`; `keys:` docs matched against the same
  `recent_text` the lorebook scan uses (word-boundary, case-insensitive,
  last `context_scan_depth` messages). Keyword-activated docs are capped
  at 6 per turn (activation order: file order; the cap does not apply to
  `always`/`sheet_types` docs). Bodies come from a new
  `modules.read_rule(mid, rid) -> {"meta", "body"}` (raises
  `ModuleNotFound` for unknown mid; returns `None` for unknown rid) —
  `load_pack` keeps frontmatter only.
- **`mechanics_sheets.j2` — sheet summaries.** One compact block per
  present sheeted cast member plus the current location if sheeted. Block
  header is the exact roll-tag actor reference: `characters:mara — Medium
  (Mara)`. Body lines: resources as `essence 6/10`, derived values as
  `sight_pool 6`, dots/numbers of note (all fields, single line each;
  sheets are small). Invalid sheets (non-empty `errors`) are skipped with
  a one-line `(sheet invalid)` marker.
- **`mechanics_response_format.j2` — the roll protocol.** Teaches the
  fence syntax with one inline example, instructs: propose a check only
  when an outcome is uncertain and consequential; emit the fence
  mid-narration at the moment of the attempt; stop immediately after the
  closing fence; never invent check ids or roll results. Lists available
  checks per present actor — `characters:mara: athletics (Athletics),
  stealth (Stealth)` — computed by `requires`-group membership against
  each actor's sheet type.

## The roll fence

### Grammar

````
```roll
{"check": "athletics", "actor": "characters:mara", "difficulty": 15,
 "modifier": 0, "reason": "leaping the collapsing gap"}
```
````

`check` (required): a check id from the module. `actor` (required):
`kind:id` as printed in the sheet-summaries section. `difficulty`,
`modifier` (optional ints). `reason` (optional string, shown on the chip).

Opener grammar: three backticks, then optional spaces/tabs (not
newlines), then `roll` at a word boundary, case-insensitive. The stream
watcher's holdback must be **prefix-state based** (hold the longest
buffer suffix that could still extend into an opener), not a fixed-length
tail — a fixed tail leaks backticks when the optional whitespace makes
the opener longer than the tail.

### Stream detection (`routes.py::_chat_stream`)

The accumulation loop gains a fence watcher over the growing buffer:

- When a complete ` ```roll …``` ` block closes: stop consuming the LLM
  stream; split the buffer into pre-fence narration and fence body;
  **first** write the pending-proposal record (see Proposal store below),
  **then** persist the narration via the normal
  `split_reply`/`append_message` path, then emit `{"proposal": {...}}`
  and `{"done": true}`. This ordering guarantees that any transcript
  which ends at a mechanical decision point has a recoverable proposal
  record, even if the SSE connection dies before the browser sees it.
- Delta suppression: once a ` ```roll ` opener appears in the buffer,
  stop emitting deltas (the UI must never render a half-fence); the
  suppressed text is re-examined when the fence closes or the stream
  ends.
- Unclosed opener at stream end: strip the partial fence from the
  persisted narration; parse what's recoverable; surface the proposal
  the same way (its `problems` will note the truncation).
- A second complete fence in the same buffer is ignored with a server
  log line.

### Parsing tolerance

Strict `json.loads` first; on failure a permissive pass (normalize single
quotes and trailing commas, then regex key extraction for the five known
keys). Actor resolution: exact `kind:id` against present cast, then
case-insensitive name match. The proposal payload sent to the UI:

```json
{"check": "athletics", "check_label": "Athletics",
 "actor": "characters:mara", "actor_label": "Mara",
 "difficulty": 15, "modifier": 0, "reason": "...",
 "available": {"characters:mara": [["athletics", "Athletics"], ...], ...},
 "problems": []}
```

`problems` entries (strings): unknown check id, actor unresolved, body
unparseable, fence truncated, check unavailable to this actor. Non-empty
`problems` → the chip opens in Modify state. A proposal is never silently
dropped.

### Proposal store (`store/proposals.py`)

`<campaign>/proposals.json` — one record per scene, rolls.json-style
(read/mutate/write whole file):

```json
{"<sid>": {"id": "pr-9f2c81d4a6e04b7f0c3a5d2e8b164f70", "status": "pending",
           "payload": {...the proposal payload above...},
           "created": "<iso>",
           "resolution": null}}
```

- Ids are **collision-resistant** (`"pr-" + uuid4().hex`, the full 122
  random bits — probabilistically unique, which is what lets a
  proposal-tag match on a roll entry be treated as proof), never a file
  counter — a corrupted/rebuilt proposals.json cannot re-mint an old id.
  All writes are **atomic** (temp file in the same directory,
  `os.replace`).
- `new(cid, sid, payload) -> record` — supersedes any existing
  non-terminal record for the scene (only the latest record per scene is
  kept, with its status).
- States: `pending` → `resolving` (claimed by an accept request) →
  `resolved` (accept: `resolution` holds the full resolution dict incl.
  `roll_id`) or `pending` → `declined`; `resolved`/`declined` →
  `narrated` (the continuation reply persisted). `superseded` is
  terminal.
- `get(cid, sid)` and `transition(cid, sid, pid, from_states, to,
  resolution=None) -> bool` — **every** state change is an atomic
  compare-and-set serialized by a per-campaign in-process lock (the app
  is single-process; the lock guards the read-mutate-write of
  proposals.json): the write happens only when the record carries that
  id AND its current status is in `from_states`. `claim(cid, sid, pid)`
  = `transition(..., ("pending",), "resolving")`. Legal transitions:
  `pending→resolving` (claim), `resolving→resolved`, `resolving→pending`
  (post-claim failure revert), `pending→declined`,
  `resolved|declined→narrated`, and supersede (any non-narrated →
  `superseded`). **A lost transition means someone else moved the record
  (e.g. a new send superseded it mid-resolve) — the route stops dead: no
  projection, no continuation, respond 409.** `resolve_check` runs only
  after a won claim — two overlapping accepts can never both roll, and a
  supersede racing a claimed resolve wins: the commit CAS
  (`resolving→resolved`) loses against `superseded` and the roll result
  is discarded unlogged (it was pure).
- **A new player send or a new fence in the same scene supersedes any
  non-`narrated` proposal** (`pending`, `resolving`, `resolved`,
  `declined` alike). A superseded `resolved` proposal's roll stands in
  the transcript as history — only its automatic continuation is
  cancelled; the next turn's narration sees the 🎲 line like any other
  message. This closes the out-of-order continuation path: Continue
  narration is only ever offered for the scene's latest, non-superseded
  record.
- Never raises on malformed file content (rebuilds as empty; house
  never-raise posture for reads).

**Recovery:** `GET /api/campaigns/{cid}/scenes/{sid}/roll-proposal`
returns the scene's record (or null). The frontend fetches it on scene
select and re-renders the chip whenever the status is `pending` — a
dropped stream, a reload, or a device switch all recover the
adjudication point. A `resolved`-but-not-`narrated` record re-renders as
the chip in a "roll made, narration pending" state offering only
**Continue narration**.

## Resolution — `store/checks.py`

New pure-stdlib module.

`resolve_check(cid, check_id, actor_ref, difficulty=None, modifier=0,
seed=None) -> dict`:

1. `modules.resolve(cid)` (None → `CheckError`); load the check def
   (unknown id → `CheckError`).
2. Parse `actor_ref` as `kind:id`; `sheets.read` the sheet (missing sheet
   or sheet errors → `CheckError` naming the problem); the sheet type
   must include every `requires` group (→ `CheckError`).
3. Expression scope = the sheet's numeric scope + computed derived values
   + reserved names `difficulty` (proposal → check default → module
   `_defaults.difficulty` → error if the template references it and no
   value resolved) and `modifier` (proposal, default 0).
4. Substitute `{expr}` placeholders in the check's `roll` template;
   `dice.roll(notation, seed)` (seeded, replayable). **`resolve_check` is
   pure beyond the RNG draw — it performs no writes** (no roll log, no
   transcript). Durable side effects belong to the caller's commit step,
   so a post-claim failure can always revert cleanly.
5. Evaluate outcome tiers against the roll scope: `total`, `natural`
   (first die's first raw roll), `margin` (`total - vs` when `vs`
   present, else absent), `successes`, `ones` (count of raw 1s), `dice`
   (die count). Tier source: check `outcomes` → `_defaults.outcomes` →
   engine fallback (`success`/`failure` from `outcome` in the dice
   result when `vs`/pool semantics produced one, else no tier). First
   match top-down; a tier whose `when` references an absent scope name
   simply doesn't match (evaluation error → skip, recorded in the
   returned `tier_warnings`).
6. Return `{"check", "check_label", "actor", "actor_label", "notation",
   "result", "tier", "difficulty", "modifier", "tier_warnings"}`
   (`roll_id` is assigned later, by the commit step's log projection).

`CheckError(Exception)` carries a user-facing message; routes map it to
400.

### Transcript line

Extends the Phase-2 formatter:
`🎲 **Mara — Vigor + Brawl (diff 6):** [7, 9, 2, 10, 3] → **2 successes** · *success*`
appended with `ROLL_SPEAKER`, so absorb and history treat it like manual
rolls.

## Pack format additions (validated at load)

- Roll templates may reference reserved names `difficulty` and `modifier`
  inside `{…}` placeholders (check-validation scope gains both).
- A check may carry `difficulty` (int) and `outcomes`
  (`[{"label": str, "when": expr}]` — labels non-empty, expressions parse,
  names ⊆ roll-scope ∪ nothing else).
- `checks.json` may carry a reserved `_defaults` entry
  (`{"difficulty": int, "outcomes": [...]}`), skipped by per-check
  validation, validated with the same rules.
- Both reference packs move targets into templates
  (`"{vigor + brawl + modifier}d10 t{difficulty}"`,
  `"1d20 + {athletics + str_mod + modifier} vs {difficulty}"`), gain
  `_defaults` (pool: difficulty 6 + WoD-style ladder incl. botch;
  d20: difficulty 12 + crit/fumble/success/failure ladder using
  `natural`/`margin`).

## Routes

- `POST /api/campaigns/{cid}/scenes/{sid}/roll-proposal` — body
  `{proposal: "<id>", action: "accept"|"decline", check?, actor?,
  difficulty?, modifier?}`; SSE stream (same frame protocol as `/chat`).
  **Idempotent by proposal id**, keyed to the scene's current record:
  - Unknown/mismatched id, or status `superseded` → 409 (the chip is
    stale; frontend re-fetches the record).
  - `pending` + accept: `claim()` first (atomic `pending → resolving`;
    a lost claim → 409 "adjudication in progress", the chip re-fetches).
    After a won claim:
    1. `resolve_check` (pure — see Resolution). **Any exception here —
       CheckError or otherwise — reverts `resolving → pending`**
       (catch-all), returns an error frame, and nothing durable
       happened.
    2. Commit: one CAS write (requires `resolving`, same id) sets status
       `resolved` with the full `resolution` (result incl. seed).
    3. Projection (idempotent, **each output independently recoverable**):
       (a) roll log — if no rolls.json entry carries this proposal id
       (`rolls.find_by_proposal`; ids are collision-resistant, so a tag
       match is proof), append one (entries carry a `proposal` field),
       then CAS-write `roll_id` into the resolution;
       (b) transcript — dedup by a **line intent**: before appending,
       CAS-write `line_intent` (the scene's current message count) into
       the resolution, then append the 🎲 line
       (`checks.format_check_roll`, a pure function of the stored
       resolution). Retry appends only when no `ROLL_SPEAKER` message
       with exactly that content exists at index ≥ `line_intent`.
       **The entire projection sequence runs while holding the
       per-campaign proposals lock** (it is pure file I/O, no LLM):
       concurrent retries of the same resolved proposal serialize, so
       find-and-append can never race itself — the second retry sees
       the first's roll tag and line inside the lock. Within any
       retryable state the projection is thus the scene's only writer
       (new sends supersede and end retryability; concurrent retries
       serialize), making intent-index + exact content a sound compound
       key; the sole residual collision — a *manual* roll landing at
       that index with byte-identical content after a crash — skips one
       visually identical line while the roll log keeps both entries,
       which is acceptable. Exactly one tagged roll entry and (up to
       that documented corner) exactly one 🎲 line survive any failure
       point. Two concurrent retries may both *stream* a continuation
       (duplicate LLM cost, bounded), but `commit_narration` lets only
       the first persist — the loser's text is dropped.
    4. Stream the continuation, then commit it atomically — see
       Continuation commit below.

    **Continuation commit.** The continuation's `_persist_reply` and the
    `→ narrated` transition happen together under the proposals
    per-campaign lock via `proposals.commit_narration(cid, sid, pid,
    persist) -> bool`: holding the lock, it (1) re-validates that the
    record still carries this id with status `resolved`/`declined`;
    (2) **crash recovery** — if the record already carries a
    `narration_intent` (a previous commit attempt crashed mid-persist),
    trim the scene's messages back to that index, discarding the
    partial continuation; (3) writes `narration_intent = <current
    message count>` into the record (atomic file replace); (4) invokes
    the persist callback (the transcript appends); (5) writes
    `narrated`. Any crash leaves the record retryable with an intent
    marker, and the retry's trim step removes whatever partial
    narration landed — continuation persistence is idempotent across
    both files without a cross-file transaction. A supersede that lands
    while the continuation is still streaming wins cleanly: the commit
    re-validation fails and the streamed text is **dropped, never
    persisted** — no stale narration can appear after newer player
    input. (New sends take the same lock for their supersede, so the
    two orders serialize; the lock is held only around trim + persist,
    never during LLM streaming.)

    **Trim safety.** Recovery removes messages at index ≥
    `narration_intent` **except `ROLL_SPEAKER` messages, which are
    preserved in order** (`scenes.trim_continuation(cid, sid,
    from_index)`). Rationale — the only writers that can touch the scene
    between a crashed commit attempt and its retry are: player
    sends/retry/regenerate (they supersede first, under the same lock,
    so the retry fails validation and never trims) and manual roll/check
    lines (always `ROLL_SPEAKER` — preserved by the trim rule). The
    partial continuation itself never carries `ROLL_SPEAKER`, so the
    trim removes exactly our own segments.

    **Follow-up fence handoff.** When the continuation itself emits a
    fence, the handoff is atomic under the same lock: (1) trim-recover
    if needed, (2) persist the continuation's pre-fence narration,
    (3) old record → `narrated`, (4) create the new `pending` record for
    the new fence, (5) emit the proposal event. The old lifecycle always
    completes with its narration persisted before the new proposal
    exists — a durable proposal always corresponds to a persisted
    decision point.
  - `resolving` (someone else holds the claim) → 409.
  - `pending` + decline: status `declined` (same CAS discipline); stream
    the declined continuation; then `narrated`.
  - `resolved`/`declined` (a retry after a dropped continuation): **never
    re-roll** — re-run the projection step if incomplete (see above),
    reuse the stored resolution, stream a fresh continuation (nothing
    was persisted by the dropped one; `_persist_reply` runs only at
    stream completion), then `narrated`.
  - `narrated`: no-op — emit `{"done": true}` immediately.
  - `CheckError` → an `{"error": {...}}` frame; status stays `pending`
    (the chip stays up for another attempt).
- `GET /api/campaigns/{cid}/scenes/{sid}/roll-proposal` — the scene's
  proposal record or null (recovery; see Proposal store).
- `POST /api/campaigns/{cid}/scenes/{sid}/check` — body
  `{check, actor, difficulty?, modifier?}`; non-streaming; runs the pure
  `resolve_check`, then itself appends the roll-log entry and 🎲 line
  (no proposal record involved); returns the resolution dict with
  `roll_id`. (Manual checks.)
- `GET /api/campaigns/{cid}/scenes/{sid}/checks` — the availability map
  for this scene: `{"actors": [{"ref", "label", "sheet_type",
  "checks": [["athletics", "Athletics"], ...]}]}` over present sheeted
  cast + sheeted current location. Feeds the 🎲 popover's Check mode and
  is the same computation the response-format context section and
  proposal `available` map use (one shared helper in `store/checks.py`:
  `available_checks(cid, sid)`).
- Registered before generic `{kind}` catch-alls per house rule.

## Continuation call

Build messages exactly like a normal turn (the persisted narration and 🎲
line are already in history), then append one ephemeral system message
(never persisted):

- Accept: the roll restated — check label, actor, dice, total/successes,
  margin/target, **tier label** — followed by the bodies of every
  `on_roll: true` rules doc and the check's linked `rules:` docs, then:
  "Continue the narration from where it stopped, incorporating this
  result. Do not roll again for this action."
- Decline: "The proposed check was declined by the player. Continue the
  narration without rolling; the action proceeds as narrated."

The continuation reply persists as a normal assistant message (normal
fence watching applies — a follow-up proposal in the continuation is
allowed and surfaces a new chip).

## Frontend

- **`components/RollProposal.tsx`** — chip between the stream area and
  the composer, driven by a new `proposal` field on `ChatEvent` handled
  in `runStream` **and re-hydrated on scene select** via
  `GET .../roll-proposal` (renders whenever the record's status is
  `pending`; a `resolved`-but-not-`narrated` record renders the
  "roll made, narration pending" state offering only **Continue
  narration**). Normal state: `🎲 **Athletics** — Mara · DC 15`, the
  reason line, **Roll it / Modify / Decline**. Modify state (auto when
  `problems` non-empty): check select (from `available[actor]`),
  difficulty + modifier inputs, actor fixed text (or an actor select when
  actor resolution failed). Every verdict POSTs `roll-proposal` with the
  **proposal id** and pipes the SSE response back through `runStream` so
  the continuation streams live; a 409 (stale chip) re-fetches the
  record. Chip clears on `done` or when a new send supersedes it.
- **🎲 popover** gains a Roll/Check mode toggle. Check mode: actor select
  (sheeted present cast), check select (available to that actor),
  difficulty/modifier inputs → `POST .../check` → transcript refresh.
- `ChatEvent` gains `proposal?: RollProposalPayload`; client gains
  `resolveProposal` (streaming) and `rollCheck` (plain) functions.

## Testing

- **Fence watcher** (unit, pure function over accumulated chunks): fence
  mid-stream, at start, unclosed at end, absent, split across delta
  boundaries (including the opener split mid-token), garbled JSON →
  `problems`, second fence ignored, delta suppression from opener
  onward.
- **`resolve_check`**: scope incl. derived + reserved names; difficulty
  ladder (proposal > check > `_defaults` > error-when-referenced);
  requires-gating; unknown check/actor/missing sheet/invalid sheet →
  `CheckError`; seeded determinism (same seed → same result); tier
  evaluation for both reference systems (d20 crit/fumble via `natural`,
  pool botch/exceptional via `successes`/`ones`); tier skip-on-eval-error
  → `tier_warnings`.
- **Pack validation**: `outcomes`/`_defaults` acceptance + rejection
  (bad label, unparseable `when`, unknown scope name); reserved
  `difficulty`/`modifier` in templates accepted; both reference packs
  still load clean.
- **Proposal store**: new/get/set_status round-trip; `claim` CAS
  (winner True, loser False; loser after decline False); supersede on
  new proposal and on new send **covering every non-narrated state**
  (pending/resolving/resolved/declined); malformed-file tolerance;
  monotonic ids; **concurrent accepts** — two threads racing `claim` on
  the same pending id yield exactly one winner and exactly one roll-log
  entry (threaded test through the route or the store lock).
- **Routes**: roll-proposal accept (roll logged + 🎲 line + streamed
  continuation + status walk pending→resolving→resolved→narrated),
  decline (no roll, streamed continuation), **idempotency** (second
  accept with the same id after `resolved` reuses the stored roll — the
  roll log gains no new entry — and re-streams a continuation; accept
  after `narrated` → immediate done; mismatched/superseded id → 409),
  **post-claim failure injection at every side-effect boundary**: an
  exception in `resolve_check` (any type) reverts to `pending`
  retryable; a crash between the resolved-commit and the projection
  heals on retry (exactly one roll-log entry, tagged with the proposal
  id; exactly one 🎲 line); recovery GET; CheckError → error frame +
  status back to pending; manual check round-trip; module-less
  campaign → 404/400 on both routes.
- **Context**: module-bound campaign renders all three sections
  (activation: always/sheet_types/keys with the cap; summaries incl.
  location; available-checks listing); unbound campaign renders none;
  `scripts/verify_templates.py` data contract updated if it enumerates
  section variables.
- **Frontend**: RollProposal normal/modify/problems/decline flows;
  runStream proposal handling (deltas stop, chip appears); popover Check
  mode. Existing CampaignView stream tests extended additively.
- **End state** (milestone verification): under the `verify` skill's
  mocked OpenRouter scripted to emit a roll fence — accept, confirm
  roll-log entry + 🎲 line + continuation; repeat with a pool check
  under `pool-basic`; confirm a module-less campaign shows zero
  mechanics sections and UI.

## Out of scope

Absorb/narration validation (Phase 5); pretty sheet rendering (Phase 6);
retry/regenerate special-casing (a retried turn that had emitted a
proposal simply regenerates; the chip clears); multi-fence turns beyond
ignore-and-log; per-campaign context budget configuration.

## Privacy note

All names in fixtures, tests, and docs are invented, per the repo privacy
rule.
