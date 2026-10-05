# Play controls I — swipes on the live response path

Step 1 of the SillyTavern-parity play controls. The whole programme, so later
steps have somewhere to point:

| Step | Control | Status |
|---|---|---|
| 1 | Swipes: provenance per variant, swipe/arrow/keys on the last response | **this spec** |
| 2 | Hide a post from context | later spec |
| 3 | Branching on one fork primitive (with #151 replay) | later spec |
| 4 | Continue the last reply (prefill / instruction) | later spec |
| 5 | Author's note with depth injection | later spec |
| 6 | Quick replies | later spec |

**Decided up front for step 3, recorded here because it constrains step 1:**
branching is hybrid. An *unabsorbed* scene branches cheaply inside the campaign
as a sibling scene file (siblings form a group; absorbing one closes the group,
the others turn read-only and unabsorbable). An *absorbed* scene branches by
campaign fork, extended to cut at a post, because absorbed state lives in
campaign files and only a copy can hold two pasts. No campaign-file snapshot
machinery is built. Nothing in step 1 may assume a variant outlives its scene
file, since a branch copies the scene, not the ledger scope (step 3 decides how
`responses.json` scopes follow a copy).

## What exists, and what is missing

Two swipe mechanisms live side by side.

- **The response ledger** (`store/responses.py`, `<campaign>/responses.json`)
  is the live path: `routes/character_turns.enabled()` is unconditionally true,
  so every turn and every reroll since character turns landed writes a response
  record with a `variants` list and one `active_variant`. Rerolling streams a
  new variant from the frozen prompt snapshot (`regenerate_response` →
  `_reroll_frames` → `_accept_reroll`) and `responses.activate` swaps it in,
  marking every later response `context_changed` and invalidating the rolling
  summary and scene break.
- **The legacy alternates sidecar** (`store/alternates.py`) serves replies that
  predate the ledger. It is the only one the transcript's ‹ n/m › arrows read
  (`CampaignView.tsx` `canSwipe` / `stepAlternate`, `GET .../alternates`).

So on the live path a player can only change variant by opening the
**Response actions** disclosure, pressing **Response variants**, and choosing
**Use variant N** (`components/ResponseControls.tsx`). There is no swipe, no
arrow pair, no key, and nothing on a touch screen.

And a ledger variant records nothing about how it was made. A variant is
`{id, content, part, part_content, status, handoff, issue, reasoning,
reasoning_parts, created}`: no model, connection, response settings or
guidance. `alternates.py` recorded `model` and `guidance` per run; the ledger
dropped both. The usage ledger has `model` and `connection` per call, keyed by
`response_id` but not by variant, so it cannot say which take a row produced.

The ledger path also never calls `store.steering.record`, which only the
legacy `_regenerate_run` does — so a steer typed into a character-turns reroll
never reaches absorb's steering snapshot.

## Goals

1. Every ledger variant written from now on records **what made it**: task,
   connection, served model, provider, which composition's response settings it
   was generated under, and the guidance or director note it was given.
2. The **last response** in the transcript gets SillyTavern swipes: ‹ n/m ›
   arrows, a horizontal touch swipe, and ←/→ keys. The **›** button and the
   touch swipe past the newest variant generate a new one; the → key there
   opens the reroll box instead (see *Keys*).
3. A steer on a ledger reroll is recorded in the steering log, like the legacy
   path records it.
4. A swipe on the trailing response does not throw away a rolling summary that
   never covered it.

## Non-goals

- Swipes on earlier responses. They keep the **Response variants** disclosure:
  switching a mid-transcript response invalidates everything after it, which
  is not something a stray swipe should do.
- Back-filling provenance onto existing variants. Unknown stays unknown (below).
- Migrating legacy alternates into the ledger. Legacy replies keep the legacy
  arrows exactly as they work today.
- Sampler parameters (temperature, top-p). No adapter sends any; the app's
  "preset" is the response style plus the length targets, and that is what is
  recorded. When samplers arrive they join the recorded settings.
- Honouring a one-shot response override on a ledger reroll. The reroll
  streams the frozen snapshot, so the client's `response` field on that
  request is already ignored. This spec stops the client from *spending* the
  player's pending chip on it (below) and otherwise leaves it.

## Design

### 1. Response settings on the record

What a prompt was told about prose is two resolves in `_assemble`
(`store/context/assemble.py`): `response_presets.resolve` for the **style**
(`budget["style_id"]`) and `response_targets.resolve` for the **length
targets**, of which the prompt uses one phase (`targets["opening" if
opening_narrator else "continuation"]`, rendered as `budget`). The length the
model saw comes from the second, not the first; recording the first's knobs
would name a length the prompt never asked for.

So the recorded settings are exactly what the prompt rendered:

```json
{"style_id": "terse", "phase": "continuation", "words": 300, "paragraphs": 2}
```

`_assemble` adds this dict to what it returns (`"response_settings"`), the
composers (`compose_turn`, `compose_director_turn`) attach it to the
`PreparedMessages` they build as a `settings` attribute, and
`character_turns._prepare` reads it there. It is **never** re-resolved later:
`PreparedMessages.from_snapshot` and `with_appended` do not carry extra
attributes, which is why it is persisted on the record rather than read off a
rebuilt prompt.

- A **primary** composition stores it as `record["settings"]`, written by
  `responses.prepare(..., settings=...)` beside `snapshot_ref`.
- A **resume** composition (a roll continuation recomposes; `_prepare`'s
  `pending and appended` branch) stores it as `record["resume_settings"]`,
  written by `responses.save_resume_prompt(..., settings=...)` beside
  `resume_snapshot_ref`. It can differ from the primary: the player may have
  changed a preset while the roll was pending.
- A retry that reuses a frozen snapshot (`_prepare`'s first branch) composes
  nothing and writes no settings.

Settings live on the record, not on each variant: every variant is generated
from one of the record's two snapshots, so a per-variant copy would only grow
`responses.json` — a campaign-wide file written whole several times per turn
and never pruned — by a constant per take.

### 2. `made_by` on each variant

`save_variant` gains a keyword `made_by: dict | None`, stored on the variant
when given:

```json
"made_by": {
  "task": "regenerate",
  "connection_id": "c-1a2b",
  "connection": "My OpenRouter",
  "model": "vendor/model-name",
  "provider": "openrouter",
  "composed": "primary",
  "guidance": "make her colder",
  "note": ""
}
```

- **`task`** — the meter task: `chat`, `continuation` or `regenerate`. A
  director turn meters as `chat` and is told apart by `note`.
- **`connection_id`, `connection`, `model`, `provider`** — read from the
  meter's `usage` holder after `meter.done()`. `llm._stamp` resets it per
  attempt and a provider may overwrite `model` with the dated snapshot it
  served, so it names the attempt that answered, including a fallback.
  `connection` is the connection's *label* (`llm._label`: its name, falling
  back to its id) as the usage ledger records it, and goes stale on a rename;
  `connection_id` is the served connection dict's `id` (`usage[ATTEMPTED]`),
  which does not. `meter.usage` rather than `meter.row`, because `row` is
  `None` when the ledger append itself failed (`usage.record` swallows
  `OSError`) while the holder still has the values.
- **`composed`** — `"primary"` or `"resume"`: which of the record's settings
  (section 1) and snapshots the call ran on.
- **`guidance`** — the reroll steer (`RegenerateBody.guidance`), clipped to
  `alternates.MAX_GUIDANCE_CHARS` (500), the same wire-input bound the legacy
  sidecar and the steering log apply. Empty for a first take.
- **`note`** — the director note **the player typed** for the round that
  produced the response. The round stores it as `typed_note` when it is
  created (`responses.new_round`), because the round's `note` is not always
  the player's words: an empty send ("next NPC round") and a replay turn store
  the `director_note.j2` template text there, and recording that as a note
  would put app wording in the player's mouth. **A reroll carries the note
  too**: it replays the primary snapshot, and for a director turn that
  snapshot already ends with the note as its final user message
  (`compose_director_turn`, `before_post`). A first take with no typed note,
  and a reroll of one, record `""`.

**Unknown is absent, never zero.** A variant written before this change has no
`made_by`; a key the holder lacks is missing (never filled from
`effective_model(conn)` as a guess). Readers show nothing for an absent key.
This is `alternates.py`'s rule ("`model` is '' for no record") and the cost
surfaces' rule ("a price nobody reported is never rendered as zero"), applied
to provenance.

**Building it is fail-soft.** A helper, `character_turns._made_by(meter, record,
task, composed, guidance)`, catches and logs any exception and returns `None`;
provenance must never fail a turn.

**Every path that saves a variant:**

| Path | When the holder is read | `task` | `composed` | `guidance` |
|---|---|---|---|---|
| `_frames` → `_save` (turn, director turn) | after `meter.done()`, **before** `meter = None` | `chat` | primary | `""` |
| `_frames` → `_save` with `continuation` | same | `continuation` | resume | `""` |
| `_frames` → `_save`, retry of a frozen snapshot | same | meter task | whichever snapshot `_prepare` reused | `""` |
| `_frames` → `_pause` → `_save("incomplete")` (prose before a roll fence) | same — `_frames` builds `made_by` before nulling the meter and passes it into `_pause` | meter task | as above | `""` |
| `_rescue` → `_save` / `_pause` | after `_rescue`'s own `meter.done(...)` | meter task | as above | `""` |
| `_reroll_frames` → `_accept_reroll` | after `meter.done()` | `regenerate` | primary | `body.guidance` (clipped) |

`_prepare` returns which snapshot it used alongside the record, so `composed`
is known without guessing. A roll continuation appends a part to the
response (`save_variant(part=...)`); the new variant's `made_by` describes the
call that wrote the newest part.

The public response record (`GET .../responses/{rid}`) carries `made_by` on
each variant and `settings` / `resume_settings` on the record. The TS
`ResponseRecord` type gains the fields.

### 3. Steering on ledger rerolls

`regenerate_response` calls `store.steering.record(cid, sid, body.guidance)`
when the guidance is non-empty, inside the hold that claims the turn and
**after** the `editable` and `historical_context_unavailable` checks — a
refused reroll records no steer. The steering log is append-only by design
(`store/steering.py`): it marks where the player's intent and the lore
disagreed, which stays true after the variant it steered is swiped away. That
is player intent, not reply content, so it does not breach "only the current
variant reaches absorb" — the transcript, which holds only the active variant,
is still the only reply text absorb reads.

### 4. A swipe keeps a summary that never covered it

`responses._invalidate` (run by `activate` and `delete`) resets the rolling
summary to `("", 0, "")` unconditionally. The summary records the length of
the prefix it folded (`rolling_at`) and that prefix's digest; a change at or
after `rolling_at` leaves both true. `_invalidate` gains the index of the first
message that changed and resets the summary only when that index is **before**
`rolling_at`. Swipes on the trailing response therefore stop forcing a re-fold
from post 0. Rounds and proposals are still superseded, and the scene-break
check still resets, as today.

### 5. The swipe read

A new lock-free route, `GET /campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe`,
backed by `responses.swipe_state(cid, sid, rid)`:

```json
{"active": 1,
 "variants": [{"id": "...", "status": "complete", "made_by": {...}}, ...],
 "settings": {...}, "resume_settings": {...},
 "can_reroll": true, "editable": true, "round_open": false}
```

- **Lock-free and write-free**, in the style of `variants_by_response`: one
  read of `responses.json` and the scene identity via `identity.scene_identity`
  (never `ensure_identity`), no snapshot reads. `responses.get` is not reused:
  it takes the campaign lock (waiting behind a turn), mints an identity, and
  reads the snapshots — the costs `migrate_if_needed` was written to keep off
  scene open.
- **Variant contents are not returned** — the arrows need ids, statuses and
  provenance; the disclosure still fetches full texts through `get`.
- **`editable`** is the server's own answer: `responses.editable`'s checks
  (`mechanically_locked`, the unknown audit boundary, a roll line at or after
  the response's first message), evaluated without raising. The client cannot
  see the audit boundary, so it does not try to reproduce it.
- **`round_open`** — an unfinished round (`pending`, `incomplete`, `paused`)
  exists in this scene. `activate`'s `_invalidate` supersedes such a round and
  its proposal; a swipe while one is open would destroy a paused roll or a
  Retry the player has not used.
- A response id the ledger does not know answers 404.

### 6. Swipes on the last response

**Which post.** The *swipe target* is the trailing assistant message
(`rerollAt`) when it carries a `response_id` and is the last message of that
response (`lastOfResponse`). Multi-part responses arise only from roll
continuations, which are mechanically locked, so the target is never a part
that `activate` would collapse. A trailing message without a `response_id`
keeps the legacy alternates arrows, unchanged.

**Fetching.** The view fetches the swipe read when the swipe target's
**response id or content** changes — the content changes on every activate
and every landed reroll, which keep the same `rid` — and after every
`activateVariant`, `rerollResponse` and settled turn. Responses for another
campaign, scene or window are dropped, the way `getAlternates`' are. A failed
fetch hides the arrows rather than showing a stale count.

**Arrows.** `‹ n/m ›` in the post's gutter, the `.swipe-nav` markup the legacy
control uses, where `n/m` counts **complete** variants. Shown when there are
two or more complete variants, or one and `can_reroll` (so there is somewhere
to go). A migrated reply with one variant and no snapshot shows no arrows.

- **‹** activates the previous complete variant; disabled at the first.
- **›** activates the next complete variant. At the newest it **generates**:
  `rerollResponse(id, "", NO_REROLL_ROUTE)`, the reroll popover's plain submit.
  This is the SillyTavern gesture. It sends `response: undefined` and does
  **not** clear `pendingResponse`: the server ignores the field on a ledger
  reroll, so clearing would silently spend the player's next-turn length chip.
- Incomplete variants are skipped; the disclosure still lists them.

**Disabled when.** Stepping and generating both require: `!responseDisabled`
(busy, rolling, scene locked, editing, renames in flight), `!absorb` (once a
review payload has landed, a swipe would change the content its watermark
digests and force the longest generation in the app to re-run), no live
`proposal`, `editable`, and `!round_open`. Generating additionally requires
`canReroll` and the record's `can_reroll`. These mirror every refusal the
client can know about. The server still answers 409 for a race (a turn landing
between read and click); that case is handled below, not prevented.

**Tooltip.** The counter's `title` lists the active variant's `made_by`:
`Guided: …`, `Note: …`, `Model: …`, `Via: <connection>`, and the settings it was
composed under (`Style: …`, `Length: ~N words, M paragraphs`), one line each,
absent keys skipped — the shape `altTitle` uses for legacy alternates.

**Touch swipe.** A hook, `components/play/useSwipe.ts`, wired to the swipe
target's `.msg` element through React pointer handlers (element-scoped, so the
`no-restricted-syntax` key-listener rule is untouched). It tracks one touch
pointer from `pointerdown` and decides on `pointerup`; `pointercancel` — what
the browser sends when it takes over a vertical pan — and a second pointer
discard the gesture. It fires only when:

- `pointerType === "touch"`,
- horizontal travel ≥ `SWIPE_MIN_PX` (56) and ≥ twice the vertical travel,
- the gesture did not start inside `button`, `a`, `input`, `textarea`,
  `select`, `[contenteditable]`, an edit form, or a horizontally scrollable
  element (a wide GFM table), and
- no text selection exists when it ends.

A left swipe (finger moving right-to-left) is **›**; a right swipe is **‹** —
SillyTavern's mapping. `touch-action: pan-y pinch-zoom` on the swipe target keeps vertical
scrolling native, and pinch-zoom stays available because the gesture already
discards a second pointer. The constants are structural (a thumb's travel, a scroll's
angle) and should be tuned on a device. The Android shell needs nothing: it is
the same frontend in a WebView.

**Keys.** Two `useHotkeys` bindings in `CampaignView`, group `IN THIS SCENE`,
so the `?` sheet lists them:

- `arrowleft` — "Previous reply variant". Steps only.
- `arrowright` — "Next reply variant". Steps only; at the newest variant it
  **opens the reroll box**, exactly as `r` does, rather than generating.

The chord names are `arrowleft` / `arrowright` because `shortcuts/keys.ts`
builds chords from `e.key.toLowerCase()`. The → key does not generate because
of the rule stated above that `useHotkeys` block — *nothing bound bare spends
money without a further confirmation*. A bare arrow is also easy to press
without meaning to: a focused tab button or any other non-typing control is
not a typing target, so the binding reaches it. The touch swipe and the ›
button are deliberate gestures on the post itself, which is why they keep the
generate behaviour. Not `whileTyping`. `enabled` carries the stepping
conditions above.

**The disclosure stays.** **Response variants** remains for reading full texts
and reasoning and for switching an earlier response, and gains the `made_by`
lines under each variant.

### 7. Error handling

- An `activate` that answers 409 (`applied_mechanics`, `variant_incomplete`,
  `scene_busy`) shows the refusal through `fail(err)`, as `mutateResponse`
  does today, and refetches the swipe read so the controls reflect the
  server's state.
- A generate that fails retains the previous variant (the server answers
  `replacement_incomplete`); the refetch after it shows the unchanged count.
- `made_by` collection is fail-soft (section 2).

## Testing

Backend (`test_character_turns.py`, `test_responses.py`,
`test_response_controls_routes.py`, with `llm_fakes`):

- a turn's variant carries `made_by` with `task: "chat"`, the served model,
  connection label and id, `composed: "primary"`, empty guidance; the record's
  `settings` equals what the prompt rendered (style id and the phase's
  targets), including a scene `response_continuation_words` and a one-shot turn
  override;
- a director turn's variant and a reroll of it both carry the note;
- a reroll carries `task: "regenerate"`, the clipped guidance, and the steering
  log gains it — and gains nothing for empty guidance or for a reroll refused
  with `historical_context_unavailable`;
- a fallback-served call records the fallback's model and connection id;
- a holder with no model leaves `model` absent; an `OSError` from the ledger
  append still yields `made_by`;
- the prose before a roll fence (`_pause`) and a rescued incomplete save both
  carry `made_by`;
- a roll continuation's variant has `composed: "resume"` and the record holds
  `resume_settings`;
- a variant with no `made_by` round-trips untouched;
- `swipe_state` takes no lock (holds while another thread holds the campaign
  lock), writes nothing, returns no contents, reports `editable: false` for a
  roll after the response and for the audit boundary, and `round_open: true`
  with a paused round;
- activating the trailing response keeps a rolling summary whose `rolling_at`
  is at or before it, and resets one that covered it.

Frontend (`CampaignView.test.tsx` with the shared `testkit/` harness;
`useSwipe.test.tsx`):

- arrows render on the last response only, with `n/m` from the swipe read;
  none for one variant without `can_reroll`;
- ‹ / › activate the neighbouring complete variant, skip incomplete ones, and
  the counter follows (the refetch fires on content change with the same rid);
- › at the newest calls `regenerateResponse` with empty guidance and no
  `response`, and leaves the pending chip set;
- all controls are disabled with a live proposal, `round_open`, `!editable`,
  or a landed review;
- `arrowleft` / `arrowright` step, → at the newest opens the reroll box and
  sends nothing, both are listed in the shortcut sheet, and neither fires while
  the composer has the caret;
- the hook fires on a horizontal touch drag past the threshold and not on a
  vertical drag, a mouse drag, a cancelled pointer, a drag started on a button
  or inside a scrollable table, or one ending with a selection;
- the tooltip lists `made_by` and settings and omits absent keys;
- a trailing reply without `response_id` (a mocked scene — real reads migrate
  every assistant post) still shows the legacy arrows.

## Gate record

Spec → planning gate: an independent adversarial review stood in for
`/codex:adversarial-review`, because the Codex CLI is not available in the
environment this was written in. The owner agreed to the substitution. Its
findings and how each was resolved:

- Settings recorded the wrong resolver (length comes from
  `response_targets`) → section 1 records what the prompt rendered.
- Key chords were `left`/`right` (never fire) → `arrowleft`/`arrowright`.
- A bare → generating breaks the "nothing bare spends money" rule → → opens
  the reroll box; the button and touch swipe generate.
- The fetch was keyed on a response id a swipe never changes → keyed on id
  and content, plus refetch after every mutation.
- Swiping supersedes a paused roll or an unfinished round → disabled on a
  live proposal and on `round_open`.
- Missing `!absorb`; `note: ""` on rerolls was false; the `_pause` path was
  missing; "disabled exactly when" ignored `mechanically_locked` and the audit
  boundary → all addressed in sections 2, 5 and 6.
- Connection was a label, `row` can be `None`, continuation settings differ,
  `responses.get` is costly on open, swipes wiped the rolling summary, a
  generate spent the pending chip, guidance was unbounded and settings
  per-variant bloated the ledger → sections 1, 2, 4, 5 and 6.

Plan gate: the same stand-in reviewed the implementation plan; its findings
were folded into the plan. The one that changed this spec: an empty send's
round `note` is template text, so `made_by.note` reads the round's new
`typed_note` instead (section 2).
