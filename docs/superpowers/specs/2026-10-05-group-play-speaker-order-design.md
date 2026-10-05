# Group play: speaker order controls

Approved design, 2026-10-05. Extends the character-turns engine
(`2026-09-11-character-turns.md`, `docs/character-responses.md`).

## What already exists, and what this adds

The per-character engine (`routes/character_turns.py`) already makes one call
per contribution, in sequence, inside one detached run per player post. Who
speaks is decided by the model, checked by code:

- the first speaker comes from a separate `response-selector` call
  (`templates/scene/response_selector.j2`), skipped when there are fewer than
  two NPCs;
- each later speaker comes from the hidden ```` ```handoff ```` block a
  contribution ends with, validated by `response_protocol.validate_handoff`;
- code enforces one automatic contribution per NPC per round plus one narrator
  slot — the round's only cap;
- Respond as (composer dropdown + button), Continue (empty send: exactly one
  contribution, no handoff followed) and Stop (cancel the run, block every
  successor, keep partial prose for Retry) already work.

What is missing: any order the player controls; per-character talkativeness;
sitting a present character out; a one-tap way to make a character reply; and
any continuation past the round one post opens. `store/context/speaker.py`'s
`nominate` computes "named first, else longest silent", but only as a prompt
hint behind the off-by-default `speaker_turn_taking` setting; it routes
nothing.

This design adds those, keeping today's model-directed flow as the default.

## Settings

Per scene, stored as one frontmatter field `group_play` on the scene file
holding compact JSON (frontmatter values are strings; one field keeps the
scene file's head readable and lets a rename carry the settings with the
file). Written by a new `store.scenes.set_group(cid, sid, settings)` under
`@_serialized`, taking the already-serialized string; routes read it through
`store.group_play.settings_of(scene_meta)` (the scenes package may not import
the planner, which would close an import cycle through `store.context`).

| Key | Values | Default |
|---|---|---|
| `order` | `directed` \| `manual` \| `list` \| `natural` | `directed` |
| `order_list` | actor refs (`kind:id`), may include `grimoire` | `[]` |
| `talkativeness` | `{ref: 0..100}` | for any ref absent: 50 in Natural, 100 (never filtered) in Directed |
| `sitting_out` | actor refs | `[]` |
| `auto_rounds` | integer `0..5` | `0` |

Reading is lenient: a missing or malformed field, an unknown `order`, a
non-integer or out-of-range value each fall back to the default for that key
rather than failing the scene. Writing validates and refuses (400) out-of-range
input. Refs need not be present: a character who leaves and returns keeps their
settings. A scene with no `group_play` field behaves exactly as today.

Routes: `GET /campaigns/{cid}/scenes/{sid}/group` returns the effective
settings; `PUT` replaces them (whole object, plain pydantic fields, dumped via
`routes.common._dump`). The PUT is deliberately **not** frozen by `scene_busy`:
it changes no transcript shape, and the point of sitting a character out is
that it can be done while an auto chain is running. Settings take effect at the
next speaker choice or round, never mid-contribution. The activity middleware
stamps the campaign write token as for any 2xx write.

## Who speaks: a pure planner

A new pure module (no store writes, so outside the lock domain) decides the
speakers for a round from: the mode and settings, the present non-player cast
(`character_turns.roster`), the round's trigger text, the scene history, and an
injected `random.Random`. Its output is stored on the round record (`mode`,
`plan`: the ordered refs still to speak), so a retry, a recovery after
reconnect, or a roll resume continues the same plan and never re-rolls.

"Named" reuses `speaker.py`'s whole-word matcher (`_name_labels`), extended
with a function returning **every** present NPC a text names, in order of first
mention (today's `_named` returns one, and nobody for two). A character naming
themselves does not count.

"Available" below means present, not player, and not in `sitting_out`.

### Player post (automatic round)

- **Directed** — today's selector and handoffs, over a filtered eligible list:
  available characters, then each one *not* named in the post kept with
  probability `talkativeness/100`. Directed treats an unset talkativeness as
  100, so only a character the player gave a value is ever filtered — saving
  any other setting never starts dropping the unnamed cast. If the roll
  removes every available character, the one with highest talkativeness (by
  those same effective values) is kept (ties: `nominate`'s longest-silent
  order). The filtered list is the round's `eligible`, so it is
  what the selector sees and what handoff candidates are drawn from.
- **Manual** — the post is appended and the round has an empty plan: no
  contribution is generated and the run ends with `done`. The player chooses who
  answers with the reply-as chips.
- **List** — every available character speaks once: `order_list` order first,
  then any available character the list omits, in cast order (a newcomer is
  never silently excluded). `grimoire` speaks at its list position if listed.
- **Natural** — named characters first, in mention order; then each other
  available character joins with probability `talkativeness/100`, in shuffled
  order; if nobody joins and nobody is named, the longest-silent available
  character (`nominate`'s ranking) speaks.

In List and Natural the prompt offers no handoff candidates (as an explicit
single response does today) and any handoff block is ignored; the plan alone
decides. If no character is available in any automatic mode except Manual,
Grimoire narrates — today's behaviour for a scene with no NPCs.

### Continue (empty send) and director notes

Continue still produces exactly one contribution and follows no handoff:

- **Directed / Manual** — the selector, as today.
- **List** — the next available character in list order after the most recent
  speaker, wrapping.
- **Natural** — the Natural rule applied to the most recent contribution's text,
  taking only its first pick, and never the speaker of that contribution.

A director note keeps going through the selector in every mode: the note may
itself say who should act, and the selector is what reads it.

### Explicit choice wins

Respond as / a reply-as chip names its actor, which skips planning entirely —
including for a character who is sitting out or who rolled out on
talkativeness. Semantics are unchanged: with an empty composer, exactly one
contribution; with text, that actor leads and the round continues by mode with
everyone but them.

### Mid-run changes

The loop already re-reads the roster before each speaker and ends the round if
the speaker has left. It additionally re-reads `sitting_out` and skips (does not
end on) a planned speaker who has started sitting out, moving to the next one in
the plan. In Directed mode a handoff to someone now sitting out is treated as
ineligible.

## Auto-continue

Opt-in, by `auto_rounds > 0`. It runs **server-side inside the same run** — the
same reason `streaming._fire_follow_up` moved off the client: a locked phone
must not stop a chain the player asked for. One run means one exclusion key
held throughout, Stop reaching every round, and one settlement boundary.

- When a round started by a player post (or one of its follow-on rounds)
  completes and rounds remain, a follow-on round starts in the same `_frames`
  loop: `new_round(automatic=True, post=<the original post>, note=<the Continue
  note>)`, planned by mode with the most recent contribution as its trigger
  text. Every NPC is eligible again. Usage stays attributed to the original
  player post.
- The round record carries `auto_remaining` (decremented as each follow-on
  starts) and `round_index`, so a roll resume or a retry knows what is left.
- The chain stops early on: Stop; a roll pause (the resumed round continues the
  chain from its stored `auto_remaining`); an error; a round that generated
  nothing; Manual mode (which never auto-continues); and, in Directed mode, an
  explicit `next: null` from a contribution or the selector — the model handing
  control back.
- In Directed mode a handoff naming someone who already spoke *this round* is
  today rejected as `ineligible or repeated speaker`. With rounds remaining it
  instead ends the round cleanly and starts the follow-on round with that
  character leading (`validate_handoff` reports a repeat distinctly from an
  ineligible ref). With no rounds remaining, today's rejection stands.
- Stop sets `auto_remaining` to 0, clears the round's remaining plan and marks
  the round stopped, so a later Retry finishes only the interrupted contribution
  and generates nothing after it, in any mode.
- A new SSE frame `round_start {"index": n, "of": total}` precedes each
  follow-on round's first `response_start`. Clients that ignore unknown frames
  are unaffected.
- The rolling summary and scene-break follow-ups fire once, after the whole run
  settles, exactly as now.

## UI

- **Reply-as chips** replace the Respond-as dropdown and button: a row above
  the composer, `Grimoire` plus one chip per present NPC. One tap sends today's
  Respond-as request for that ref (with the composer's text, if any). Chips for
  sitting-out characters are dimmed but enabled. Disabled under the same
  conditions as the current button (busy, rolling, scene locked, renames in
  flight). Works where the cast column is hidden.
- **Group popover**, opened from a chip in the composer bar beside the response
  chip (label shows the mode, e.g. `Order: Natural`): mode select; for List an
  ordered list with up/down buttons; per present character a talkativeness
  slider (shown for Directed and Natural) and a Sit out toggle; the auto-rounds
  number (0–5). Saves through `PUT /group`; available while a run is live.
- **Stop** is unchanged and ends the whole chain. During an auto chain the
  responding indicator reads `Round n/total`.

No new keyboard binding.

## Error handling

- Settings parse failures degrade per key to defaults (above); the route refuses
  invalid writes.
- A plan naming someone who left or now sits out skips them; an empty plan ends
  the round with no error (Manual's normal case, and List/Natural with no one
  available falls back to Grimoire before that can happen).
- The existing fences (`_fence`, scene identity, turn token) apply unchanged to
  every round of a chain; a conflict ends the run as today.
- A contribution that is empty, cancelled or paused never starts a follow-on
  round.

## Testing

Backend:

- Planner unit tests per mode with a seeded RNG: List order and newcomer append;
  Natural named-first in mention order, talkativeness 0/100 extremes, the
  nobody-joins fallback, self-naming ignored; Directed filtering keeps named
  characters and the highest-talkativeness fallback; sitting out excluded
  everywhere; Continue rules per mode.
- Engine tests (`llm_fakes.py` only): a List round generates in order with no
  selector call; Natural ignores handoff blocks; Manual post generates nothing
  and settles; sitting out mid-run skips the next planned speaker; auto rounds
  stop at the cap, on `next: null` (Directed), on a roll pause and on Stop; a
  Directed repeat handoff leads the next round; Stop zeroes `auto_remaining` and
  Retry starts no new round; a roll resume continues the plan and the chain;
  retry after an interrupted contribution does not re-roll talkativeness.
- Route tests for `/group`: defaults, round-trip, validation, accepted while a
  run holds the scene.
- Settings parse tests: malformed JSON and each bad key fall back.

Frontend: chip tap sends the ref with composer text; dimmed sitting-out chip
still sends; popover saves each control and shows the slider only where it
applies; the `Round n/total` indicator from `round_start` frames.

## Out of scope

- A card- or campaign-level talkativeness default (per scene only).
- Sitting out or reordering the narrator outside List mode.
- Changing the greeting opener's fixed-order fan-out.
- Changing the `speaker_turn_taking` prompt hint.
