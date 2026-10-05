# Play controls IV — keep writing (continue the last reply)

Step 4 of the SillyTavern-parity play controls (programme table in
`2026-10-05-play-controls-swipes-design.md`). Decisions a brainstorm would have
put to the owner are recorded here with their reason; the owner asked for the
programme to run without stopping.

## The request

> Extend the last reply instead of replacing it, for when a good reply stops
> short. Send the partial reply as a prefill where the model supports it,
> otherwise as an instruction to continue. With per-character calls, continue
> applies to the last speaker. It differs from #188's continue-as, which writes
> a new post as a chosen character.

## Names

"Continue" is taken twice already: the composer's empty-send button reads
**Continue ▶** (a director turn, "Continue the scene."), and `continuation` is
the routing task and run kind of a roll's resumed narration. This control is
therefore **Keep writing** in the UI and **`extend`** in code (route, routing
task, run kind, meter task, prompt-log task, `made_by.task`).

## Behaviour

- **Target: the trailing response only.** The last response in the transcript
  (the swipe target from step 1). Continuing an earlier reply would rewrite
  history every later post was built on.
- **Result: a new variant.** The extended text is saved as a new variant of
  that response — `old + joiner + continuation` — and activated. The
  unextended take stays one swipe back, so a bad continuation costs nothing.
- **Same speaker.** The continuation is generated in the response's own
  per-character call (its frozen snapshot, its actor); speaker markers for
  anyone else are stripped exactly as a reroll strips them.
- **Refused** like a reroll: `editable` (no roll after it), a frozen snapshot
  must exist (`historical_context_unavailable`), the active variant must be
  complete, no unfinished round, scene free (`scene_busy`).

## Prompt

The frozen primary snapshot of the response (`PreparedMessages.from_snapshot`),
then one appended message, chosen by **mode**:

- **Prefill** — `{"role": "assistant", "content": <active variant text>}`
  appended last. The model continues the assistant turn.
- **Instruction** — a system message rendered from
  `templates/scene/extend_instruction.j2`, carrying the partial reply and the
  rule: continue exactly where it stops, do not repeat or summarise it, same
  voice, same speaker.

**Mode by connection kind.** `llm.PREFILL_KINDS = frozenset({"openrouter"})`.
OpenRouter forwards a trailing assistant message as a prefill. The Claude
agent SDK path flattens messages and always appends its own empty assistant
cue, which would turn a prefill into a second reply; strict OpenAI-compatible
endpoints rebuild message dicts and their servers differ on whether a final
assistant turn is continued. Both get the instruction.

**Fallback safety.** A prefill prompt must never be dispatched to a non-prefill
route. `PreparedMessages` gains `requires_prefill: bool`; `llm._usable_routes`
skips a fallback whose kind is not in `PREFILL_KINDS` when it is set (the same
shape as the existing `TEXT_ONLY_KINDS` filter). A prefill turn with no usable
route fails like any other exhausted dispatch, and the player can retry or pick
a route.

## Joining

`_normalise` strips and splits a reply; a continuation that starts mid-word or
mid-sentence would lose the whitespace that told us how to join. So the raw
continuation's **leading whitespace is read before normalising**:

- starts with a blank line → join with `\n\n` (a new paragraph);
- starts with a newline → `\n`;
- starts with other whitespace → ` `;
- starts with no whitespace → `""` if the old text ends in whitespace or the
  continuation starts with closing punctuation (`. , ; : ! ? ) ” ’ —`), else ` `.

An empty continuation after normalising is `replacement_incomplete` — the
active variant stays.

## Provenance

`made_by` as step 1 defines it, with `task: "extend"`, `composed: "primary"`,
the round's typed note, and two new keys: `extends` (the id of the variant
that was extended) and `mode` (`"prefill"` or `"instruction"`). Guidance, if
the player typed a steer, is appended to the instruction (instruction mode) or
as the step-1 steer system message before the prefill (prefill mode), and
recorded in the steering log like a reroll's.

## Route

`POST /campaigns/{cid}/scenes/{sid}/responses/{rid}/extend` with the
`RegenerateBody` shape (`guidance`, `connection_id`, `model`). A detached
`turn`-class run of kind `extend`, streamed and re-attachable exactly like
`regenerate_response` (`runs.reserve_turn`, `_reroll_frames`-style frames:
`response_start`, deltas, `response_end`, `done`). 409 `not_last_response` when
`rid` is not the trailing response. `store/routing.py`'s `scene` route claims
`extend`.

## Play view

- **Keep writing ▸** in the response's "Response actions" disclosure, beside
  Reroll response, on the trailing response only, with the steer box and route
  picker reused.
- **No bare key**: it spends money (`r` only opens the reroll box for the same
  reason).
- The live bubble streams the **whole** text: the client seeds the streamed
  accumulator with the old text, so the reply visibly grows rather than being
  replaced by its tail.
- Prompt-log label "Extend" (`turnLabels.ts`, `PromptEntry.task`).

## Testing

Backend:
- prefill mode on an OpenRouter connection appends an assistant message equal
  to the active text; instruction mode on a Claude-agent or strict
  OpenAI-compatible connection appends the rendered instruction and no trailing
  assistant message;
- a prefill prompt never dispatches to a non-prefill fallback;
- the saved variant is `old + joiner + continuation` for each joiner rule, is
  active, and the previous variant remains;
- `made_by` carries `task: "extend"`, `extends`, `mode`;
- refusals: not the trailing response, roll after it, no snapshot, incomplete
  active variant, open round, scene busy;
- empty continuation keeps the active variant;
- routing guard passes with the new task.

Frontend:
- Keep writing appears only on the trailing response's actions and calls the
  extend route; the streamed bubble starts from the old text; it is disabled
  under the same conditions as Reroll response.
