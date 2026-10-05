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
   connection, served model, provider, the resolved response settings, and the
   guidance or director note it was given.
2. The **last response** in the transcript gets SillyTavern swipes: ‹ n/m ›
   arrows, a horizontal touch swipe, and ←/→ keys. Swiping right past the
   newest variant generates a new one.
3. A steer on a ledger reroll is recorded in the steering log, like the legacy
   path records it.

## Non-goals

- Swipes on earlier responses. They keep the **Response variants** disclosure:
  switching a mid-transcript response invalidates everything after it, which
  is not something a stray swipe should do.
- Back-filling provenance onto existing variants. Unknown stays unknown (below).
- Migrating legacy alternates into the ledger. Legacy replies keep the legacy
  arrows exactly as they work today.
- Sampler parameters (temperature, top-p). No adapter sends any; the app's
  "preset" is the response preset (style + length knobs), and that is what is
  recorded. When samplers arrive they join `made_by` as another key.
- Honouring a one-shot response override on a ledger reroll. The reroll
  streams the frozen snapshot, so the client's `response` field on that
  request is already ignored; this spec records that truthfully rather than
  changing it.

## Design

### 1. `made_by` on each variant

`save_variant` gains a keyword `made_by: dict | None`. When given, it is stored
on the variant as-is:

```json
"made_by": {
  "task": "regenerate",
  "connection": "conn-id",
  "model": "vendor/model-name",
  "provider": "openrouter",
  "settings": {"response_preset": "", "style_id": "...", "length_reply_words": "...", "...": "..."},
  "guidance": "make her colder",
  "note": ""
}
```

- **`task`** — the meter task the variant ran under: `chat`, `continuation` or
  `regenerate`. A director turn meters as `chat` and is told apart by `note`;
  the field records what the meter recorded, not a classification of its own.
- **`connection`, `model`, `provider`** — taken from the meter's finished usage
  row (`meter.row`) after `meter.done()`, which reflects the model that actually
  served the call, including a fallback. Where the row has no value for a key,
  the key is **absent** — never filled from `effective_model(conn)` as a guess.
- **`settings`** — the response settings the prompt was composed with: the
  complete dict `response_presets.resolve` returned (style id plus every length
  knob), plus `response_preset`, the preset id named by the narrowest scope
  that named one. Computed once, when the response's prompt is composed, and
  stored on the **response record** (`record["settings"]`) beside
  `snapshot_ref`, because every variant of a response is generated from that
  same frozen snapshot. Each variant's `made_by.settings` copies it. A response
  composed with a one-shot turn override records the overridden values — the
  override is part of the resolve.
- **`guidance`** — the reroll steer (`RegenerateBody.guidance`), empty for a
  first take.
- **`note`** — the round's director note (`round["note"]`), empty when none.

`settings` reaches the record through `responses.prepare(...)`, which gains a
`settings` argument; `_prepare` in `character_turns` gets it from the
composition. `compose_turn` / `compose_director_turn` already resolve it
(`assemble.py`, the `budget = response_presets.resolve(...)` line); they expose
it on the `PreparedMessages` they return (a `settings` attribute, alongside the
existing `breakdown`), so no second resolve can disagree with the one the
prompt used.

**Unknown is absent, never zero.** A variant written before this change has no
`made_by`; a key the meter did not report is missing. Readers show nothing for
an absent key. This is `alternates.py`'s rule ("`model` is '' for no record")
and the cost surfaces' rule ("a price nobody reported is never rendered as
zero"), applied to provenance.

**Where each path writes it:**

| Path | `task` | `guidance` | `note` |
|---|---|---|---|
| `_frames` → `_save` (turn, director turn, retry resume) | meter task | `""` | `round["note"]` |
| `_frames` with `continuation` (after a roll) | `continuation` | `""` | `round["note"]` |
| `_rescue` → `_save` (incomplete) | meter task | `""` | `round["note"]` |
| `_reroll_frames` → `_accept_reroll` | `regenerate` | `body.guidance` | `""` |

`_rescue` saves after `meter.done("error"|"aborted")`, so the row exists there
too. A roll continuation appends a part to an existing variant (`save_variant`
with `part`): the new variant's `made_by` describes the call that wrote the
newest part. Its earlier parts were written by an earlier call, which the
previous variant's `made_by` still describes.

The response record's public shape (`GET .../responses/{rid}`) carries
`made_by` on each variant and `settings` on the record. No route changes
shape otherwise.

### 2. Steering on ledger rerolls

`regenerate_response` calls `store.steering.record(cid, sid, body.guidance)`
when the guidance is non-empty, in the same lock hold that claims the turn —
the same place `_regenerate_run` records it on the legacy path. The steering
log is append-only by design (`store/steering.py`): it records where the
player's intent and the lore disagreed, which stays true after the variant it
steered is swiped away. That is player intent, not reply content, so it does
not breach "only the current variant reaches absorb" — the transcript, which
holds only the active variant, is still the only reply text absorb reads.

### 3. Swipes on the last response

**Which post.** The *swipe target* is the last message of the trailing
assistant generation (`rerollAt` in `CampaignView.tsx`) when it carries a
`response_id` and is the last message of that response (`lastOfResponse`). A
trailing message without a `response_id` keeps the legacy alternates arrows,
unchanged. Nothing else in the transcript gets swipe controls.

**Data.** When the swipe target's response id changes (scene open, a turn
lands, a variant activates), the view fetches `GET .../responses/{rid}` — the
existing route — scoped the way `getAlternates` is (stale responses for
another campaign, scene or window are dropped). From it: the complete variants
in list order, the active one's index, `can_reroll`, and each variant's
`made_by`. No scene-read change; the hot `GET .../scenes/{sid}` path stays as
it is.

**Arrows.** `‹ n/m ›` in the post's gutter, the same `.swipe-nav` markup the
legacy control uses. Shown when the response has at least one complete variant
and the post is otherwise actionable.

- **‹** steps to the previous complete variant; at the first, it does nothing
  (disabled).
- **›** steps to the next complete variant. At the newest, it **generates**: it
  calls the same `rerollResponse(id, "", NO_REROLL_ROUTE)` the reroll popover's
  plain submit calls — no guidance, the campaign's route. This is the
  SillyTavern gesture: swiping past the last take asks for another.
- Stepping calls the existing `activateVariant` (`POST
  .../responses/{rid}/variants/{vid}/activate`).
- Incomplete variants are skipped for stepping — `activate` refuses them — but
  still counted in the disclosure.

**Disabled exactly when the controls they stand for are.** Stepping uses
`responseDisabled` (busy, rolling, scene locked, editing, renames in flight).
Generate additionally requires what Reroll requires: `canReroll`, the
record's `can_reroll`, and no roll line after the response (the server's
`applied_mechanics` refusal, mirrored as the record being editable — the
client already knows from the transcript whether a roll line follows).

**Tooltip.** The counter's `title` lists the active variant's `made_by`:
`Guided: …`, `Note: …`, `Model: …`, `Via: <connection>`, `Preset: <id>` (or the
style id when no preset is named), one line each, absent keys skipped — the
same shape `altTitle` uses for legacy alternates.

**Touch swipe.** A small hook, `components/play/useSwipe.ts`, attached to the
swipe target's `.msg` element through React pointer handlers (element-scoped,
so the `no-restricted-syntax` key-listener rule is untouched). It fires only
for `pointerType === "touch"` and only when:

- horizontal travel is at least `SWIPE_MIN_PX` (56),
- horizontal travel is at least twice the vertical travel (a scroll is not a
  swipe),
- the gesture did not start inside an interactive element (`button`, `a`,
  `input`, `textarea`, `select`, `[contenteditable]`) or an edit form, and
- no text selection exists when it ends.

A left swipe (finger moving right-to-left) is **›** — next / generate; a right
swipe is **‹** — previous. That is SillyTavern's mapping. The constants are
structural (a thumb's travel, a scroll's angle) and should be tuned on a device
later. The Android shell needs nothing: it is the same frontend in a WebView,
and `touch-action: pan-y` on the swipe target keeps vertical scrolling native
while leaving horizontal motion to the hook.

**Keys.** Two `useHotkeys` bindings in `CampaignView`, labelled in group
`IN THIS SCENE` so the `?` sheet lists them: `left` → "Previous reply variant",
`right` → "Next reply variant (generates past the last)". Not `whileTyping` —
the caret in the composer keeps the arrows for the caret. `enabled` carries
the same conditions as the arrows (step: stepping conditions; right-at-newest:
the generate conditions). Neither binding exists today in the registry, so
there is no conflict to resolve.

**The disclosure stays.** **Response variants** remains for reading full texts
and reasoning and for switching an earlier response. It also gains the
`made_by` lines under each variant.

### 4. Error handling

- An `activate` that answers 409 (`applied_mechanics`, `variant_incomplete`)
  reloads the scene and the response record and surfaces the refusal the way
  `mutateResponse` already does.
- A generate that fails retains the previous variant (the server already
  answers `replacement_incomplete`); the counter does not move.
- A fetch of the response record that fails hides the arrows rather than
  showing a stale count.
- `made_by` collection is fail-soft: an exception building it is logged and the
  variant is saved without it. Provenance must never fail a turn.

## Testing

Backend (`test_character_turns.py`, `test_responses.py`,
`test_response_controls_routes.py`, with `llm_fakes`):

- a turn's first variant carries `made_by` with `task: "chat"`, the fake's
  served model and connection, `settings` equal to the resolve for that scene,
  empty `guidance`;
- a director turn's variant carries `note`;
- a reroll's variant carries `task: "regenerate"` and the guidance, and the
  steering log gains the guidance (and gains nothing for an empty one);
- a fallback-served call records the fallback model, not the configured one;
- a meter row with no model leaves `model` absent;
- a roll continuation's variant describes the continuation call;
- an existing variant with no `made_by` round-trips untouched;
- the record's `settings` survives a reroll (the reroll does not re-resolve).

Frontend (`CampaignView.test.tsx` with the shared `testkit/` harness;
`useSwipe.test.tsx`):

- arrows render on the last response only, with `n/m` from the record;
- ‹ / › activate the neighbouring complete variant and skip incomplete ones;
- › at the newest calls `regenerateResponse` with empty guidance; disabled when
  a roll line follows or `can_reroll` is false;
- ←/→ do the same, are listed in the shortcut sheet, and do nothing while the
  composer has the caret or while busy;
- the hook fires on a horizontal touch drag past the threshold, not on a
  vertical drag, not on a mouse drag, not when started on a button, and not
  with a selection;
- the tooltip lists `made_by` keys and omits absent ones;
- a trailing legacy reply (no `response_id`) still shows the legacy arrows.
