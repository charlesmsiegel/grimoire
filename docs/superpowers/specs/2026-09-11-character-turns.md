# Character responses and bounded automatic turn-taking

Approved design, 2026-09-11. This incorporates the user's revisions to the
adaptive-director proposal. Implementation proceeds on feature/character-turns.

## Objective

Generate each NPC's response from its own voice and knowledge while retaining
Grimoire's campaign continuity, mechanics, player control and editable transcript.
Choose subsequent speakers within the same call that writes a response. Avoid a
separate director call after every contribution.

## Generation and handoff

One model call writes one assigned character's response and a small structured
handoff. The logical result is response text plus a next action: an eligible NPC,
narration, or return control to the player. Stable actor references identify
speakers; generated display names never select a different character.

Prefer ordinary streamed prose with a trailing JSON control block withheld from
display and transcript, extending the existing hidden-metadata pattern. This
avoids requiring provider-specific JSON-schema support and preserves streaming.
A whole-response JSON envelope is an alternative worth evaluating, not required
for the orchestration contract. Metadata is acted on only after complete parsing,
validation and successful persistence of the response.

The engine validates the proposed handoff against the current round, scene cast,
NPC roles, run ownership, cancellation state, remaining response budget and
pending mechanics. Missing, malformed or ineligible handoffs stop the chain;
preserve usable prose and report the control issue in the inspector. Do not
automatically buy another call to repair routing metadata.

The first speaker comes from an explicit user choice or an initial small
selector call. If the caller already supplies an unambiguous target, skip that
call. The selector may choose narration or hand control back without an NPC
response. At most one selector plus one generation per actual contribution is
needed in the normal path, excluding provider retries and existing follow-ups.

The writer sees public facts about other potential speakers, sufficient for
conversational handoff. It does not need their private thoughts. Handoff predicts
who might respond; it must not script that person's reaction or invent knowledge.

## PC rounds and explicit controls

A new PC post starts an automatic response round. Each eligible NPC may contribute
at most once automatically in that round. Narration has a separate bounded slot;
it cannot reset NPC eligibility or create an endless narrator/NPC loop. A quiet
character is not required to speak. Stop when the player is addressed with a
decision, a roll is pending, a budget is exhausted, or no further response is needed.

An automatic Mara -> Winifred -> Mara chain is rejected by the engine even if the
model requests it. The eligibility rule is enforced in code, not only in a prompt.

Proposed explicit-control semantics:

- Continue chooses and generates exactly one additional contribution, permitting
  a previously responding NPC. It does not reset everyone's eligibility or start
  another automatic chain.
- Respond as X generates exactly one response from that selected present NPC,
  permitting a repeat, then returns control.
- Reroll replaces the selected response's variant; it does not consume another
  turn-taking slot, follow the replacement's handoff or trigger later replies.
- Delete removes the selected response only and does not restart generation.
- Stop prevents all future handoffs in the active run.

These defaults implement explicit permission to repeat narrowly: one requested
contribution rather than another unattended conversation cycle.

## Individual responses and recovery

Use stable response identities, grouped under the PC round and generation run.
Store the assigned speaker, response variant, completion status and handoff with
that identity. Message indices and the last user-post boundary alone cannot
identify a response reliably after earlier messages are edited or removed.

Reroll a response using context ending immediately before that response, preserving
its assigned character and avoiding knowledge from its future. Keep the previous
variant until a replacement is successfully accepted. Later responses stay in
place and are marked as following changed context. Offer explicit regeneration
from the edited point; do not silently remove or regenerate those responses.

Delete only the selected response. Invalidate or recompute derived summaries,
transient state and pending proposals that depended on removed/replaced content.
Preserve completed roll records as audit history; rerolling prose must not reroll
dice or apply a mechanical outcome again. Changes crossing applied mechanics
need explicit reconciliation behavior rather than pretending prose deletion
reverses the game state.

Persist each completed response before starting its successor. Reconnection and
retry resume the first unfinished step without duplicating completed responses.
Cancellation or invalid metadata cannot launch a successor. A partial response
remains visibly incomplete and must not trigger an automatic handoff. Reuse the
existing scene identity and ownership fences across the entire parent run.

A roll request pauses the chain immediately. Resolution continues the same
character's interrupted contribution, retaining its turn-taking slot. After that
contribution completes, validate a fresh handoff against the same round. The
pre-roll choice cannot pre-author outcomes for subsequent speakers.

Summary and scene-break follow-ups occur at the parent run's settlement boundary,
not after every individual NPC call. Record usage and prompt captures per call,
with aggregation to the PC round.

## Voice evidence and character-specific context

Reuse existing world/campaign dialogue examples through the current overlay
rules. Do not regenerate established examples across the library. Select a few
complete, relevant exchanges within a protected voice-evidence budget. Preserve
speaker attribution and keep examples separate from current events.

The current speaker receives their card, voice evidence, own private knowledge,
current state and goals, recent observable conversation, applicable relationships,
relevant history and mechanics. Other characters contribute public descriptions
and observable behavior. Private state from other characters is excluded.

Examples primarily demonstrate rhythm and expression. Explicit author constraints
and established campaign facts remain authoritative. Revisit the blanket rule
that a descriptive anchor always overrides dialogue examples; conflicting evidence
should be visible to the author rather than silently flattened.

## Emergent characters during play

Add Create character from this passage to narrator/Grimoire responses, including
selection of the particular person and relevant text when a passage contains
multiple people. Also support adding the material to an existing character to
avoid accidental duplicates.

Create a campaign-local character draft using the selected passage and a bounded
amount of neighboring dialogue. Reuse the existing creation and casting path.
Let the user confirm identity and review attributed speech. Preserve actual
dialogue as initial examples with source response identity and a text snapshot;
do not confuse narrator exposition or someone else's lines with the NPC's voice.

For sparse evidence, create a sparse record. If the person has not spoken, leave
examples empty. Richer card development and additional example drafting are
optional work during play or later, independent of scene closeout. Existing
closeout already requests examples when it proposes new characters; reuse or
merge those results without adding another mandatory closeout phase.

## Validation

Test one automatic contribution per NPC, repeated-speaker rejection, exactly one
response per explicit Continue/Respond action, stopping at player decisions,
invalid or missing metadata, split/truncated control blocks, stop during streaming,
unknown/PC/absent actor references and narrator-loop prevention. Test initial
selection and the single-NPC path without unnecessary director calls.

Test speaker-specific knowledge exclusion, inherited example reuse, source
attribution from multi-person narrator passages and empty examples when there is
no quoted speech. No new mandatory end-of-scene model calls.

Test individual delete/reroll in the middle of an exchange, stable identities,
unchanged later responses with changed-context indicators, roll continuation,
derived-state invalidation, cancellation, reconnection and failure between steps.

Use controlled GLM comparisons for voice naturalness and routing quality, with
continuity, player agency, latency, calls and token usage measured separately.
The observed single-NPC/group-scene difference supports investigating context
interference; it does not by itself prove the cause or the benefit of this design.

## Primary reference

Z.ai documents JSON-object mode and application-side schema validation:
https://docs.z.ai/guides/capabilities/struct-output
This design does not assume native strict JSON-schema enforcement across endpoints.

## Reviewed implementation decisions

- Persist frozen pre-response prompts independently of optional prompt captures:
  unprofiled context plus every finite model profile variant. Reroll never reads
  live character/state/history to reconstruct the past. A newly selected model
  uses the frozen unprofiled context if no historical profile matches.
- Legacy responses without such a snapshot remain individually deletable but
  individual reroll reports historical_context_unavailable; explicit replay from
  that point remains available. Never label reconstructed current state historical.
- Reject delete/reroll at or before an applied mechanics boundary with an
  actionable conflict. Completed dice and applied effects cannot be undone by
  changing prose. Existing explicit state correction/replay remain separate.
- A roll pause persists speaker/round/slot and transcript watermark, then releases
  the scene exclusion key. Mechanics resolution reacquires the key, validates
  identity and watermark, and resumes the same contribution. No background turn
  retains the key while waiting for the player.
- Other actors contribute stable reference and name plus observed transcript only.
  Raw descriptions/personality/dossiers are not proven public and are excluded
  from another NPC's writer and from the selector. Narrator uses explicit Grimoire
  scope with campaign context, constrained to observable narration and no NPC
  dialogue. NPCs use their own knowledge and public world information.
- Default new generation uses individual responses. Existing combined behavior
  remains a selectable configuration fallback for direct comparison and rollback.
- Keep source and templates portable to desktop and Android; no new dependencies.
  All prompt prose lives in editable templates. No private library data in source.

Spec review: independent adversarial review PASS after these resolutions.
