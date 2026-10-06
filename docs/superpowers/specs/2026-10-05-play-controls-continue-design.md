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

## Gate resolutions (binding; they override the text above where they differ)

Spec → planning gate: independent adversarial review (stand-in for
`/codex:adversarial-review`, Codex CLI unavailable; owner-approved).

1. **Instruction mode is the default; prefill is an explicit opt-in per
   connection.** Whether a trailing assistant message is continued depends on
   the upstream model, not the connection kind — current Claude models reject
   it with a 400, and most chat-template models treat it as history and write a
   fresh reply. So `PREFILL_KINDS` is dropped. A connection record gains
   `prefill: bool` (default false, editable in the connection form, any kind);
   `llm.prefill_capable(conn)` reads it. **The mode is chosen per attempt**:
   the extend prompt is a `PreparedMessages` that also carries both tails, and
   `_dispatch` asks it for the attempt's messages given the attempt's
   connection (`for_connection(conn)`, falling back to `for_model` for ordinary
   prompts), so a fallback to a non-prefill route simply gets the instruction
   tail — no route filtering, no lost flag across `with_appended`.
   `made_by.mode` records the mode the **served** attempt used.
2. **The two tails.** Both start with the partial reply as an assistant
   message, projected the way history is (`export.drop_images`; no speaker
   label — the model is continuing its own turn):
   - prefill: that assistant message is last;
   - instruction: followed by a **user** message rendered from
     `scene/extend_instruction.j2` ("continue exactly where your last message
     stops…"). User-role, not system, because the Claude agent path hoists
     system messages into its system prompt and the model would then write a
     new reply; a user turn after the partial reply works on all three
     adapters.
   A steer (guidance) goes into the instruction text in instruction mode, and
   as the step-1 steer message before the partial reply in prefill mode.
3. **Continue what is shown.** `old` is the response's **transcript** prose
   (the join `responses.get` uses), not the active variant's stored text — a
   hand-trimmed reply is continued from the trim, and the saved variant is
   `trimmed + joiner + continuation`.
4. **Multi-part responses** (a declined roll resumes a part without locking)
   continue from the latest resume snapshot (`composed: "resume"`, its
   settings), which contains the roll resolution the reply was written under.
5. **Joining.**
   - prefill: the model's own leading whitespace decides — blank line → `\n\n`,
     newline → `\n`, spaces → ` `, none → `""` (concatenation is the model's
     choice, e.g. finishing a word);
   - instruction: `\n\n`, unless the continuation's first character is
     lowercase or closing punctuation (`. , ; : ! ? ) ] ” ’ ' * … —`), then ` `.
   The perception fence the response protocol asks for is stripped before
   joining (the watcher handles it as for any reply); in prefill mode the
   watcher runs with perception off, since the model is mid-reply.
6. **Refusals, named as the code names them**: `not_last_response` (the
   response's messages must be the last non-synthetic messages of the
   transcript, checked inside the same campaign-lock hold as `editable`),
   `applied_mechanics`, `historical_context_unavailable`, `context_excluded`
   (step 2), `round_open`, `proposal_pending`, `review_pending` (a stored
   review would be invalidated by the new variant), and `run_in_flight` (what
   `reserve_turn` answers).
7. **A roll fence in the continuation** is refused with its own kind,
   `extend_roll_refused` ("A continuation cannot propose a roll — reroll the
   reply instead"), and the previous variant stays.
8. **Length.** The instruction asks for the rest of the beat, at most the
   response's continuation word target (from the record's settings); an empty
   continuation is `replacement_incomplete` and is tested in both modes.
9. **Metering**: task `extend`, carrying `post` and `response_id` like a
   reroll; not counted in `REROLL_TASKS` or the reroll rate. CLAUDE.md's
   detached-runs list gains the extend handler.
10. **The live bubble** keeps the old text as a separate render-only `seed`
    (never in the stream accumulator, so the Retry offer, speaker offsets and
    re-attach logic are untouched), hides the target message while streaming,
    and renders `seed + " " + stream`. The `response_start` frame carries
    `extend: {seed}` so a re-attached client rebuilds the same view. The landed
    variant (server-joined) replaces it on reload.
