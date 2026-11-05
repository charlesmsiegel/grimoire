# Per-NPC generation (first slice of sprint 027)

**Status:** first slice of roadmap sprint 027, [Per Npc Generation Runtime](roadmap/sprints/per-npc-generation-runtime.md). Headless: it plans, briefs, issues and screens a turn's generation calls through the real provider adapter, with synthetic tests and scripted providers only. It **returns proposals and commits nothing**: no message, event, belief or effect is written. Persisting the lines and showing them is 035's (with 032), and reviewing a director reply is 035's `review_narration`.

The code is two packages:

- `grimoire.generation` is **pure** ([purity rules](../backend/tests/purity.py)): turn planning from control, snapshot voices, sealed briefs, prompt messages and reply screening.
- `grimoire.runtime` is an **adapter**: `GenerationRuntime` issues the planned calls through `grimoire.providers`, and `RunFence` makes an older run of the same chat stale.

## The rule

One independent RP-model call **per speaking NPC contribution**, each prompt built only from that actor's own perspective ([INFERENCE](INFERENCE.md#the-rule)). A present NPC that does not speak costs nothing. A scene-level director reply is a separate, **optional** stage, `director_generation`: never implicit, never an actor, at most one per turn ([INFERENCE](INFERENCE.md#the-scene-level-director-reply)).

## Planning a turn

`plan_generation_turn(campaign_id, turn_id, *, present, speakers, controls, director_reply=False, policy=None)` returns 004's `TurnInferencePlan`.

- **Player characters come from control.** `player_controlled_ids` is every present actor whose `ControlAssignment` names a human controller. The caller cannot pass it.
- **Speakers are director NPCs.** A speaker must be present (`absent_speaker`) and controlled by `DIRECTOR`. A player character is `player_speaker` and an actor nobody controls is `unassigned_speaker`. Two controllers for any present actor is `ambiguous_control`.
- **Director reply is asked for, never assumed.** `director_reply=True` adds an optional request. With a policy that has no cap for `director_generation` (the default), the request is declined as data (`plan.declined`, reason `disabled`) and nothing is planned.

Failures raise `GenerationError` with a `reason`.

## Voices of snapshot actors

`resolve_snapshot_voice(ref, snapshot, catalog, binding=None)` is the voice of an actor captured in a 022 `EffectiveSnapshot`, with or without a world. `resolve_voice` ([VOICE](VOICE.md)) stays the path for an index actor.

1. The actor's selected **leaf** revision's own profile, if the catalog has one. A leaf that authors a profile does not inherit.
2. Otherwise the **nearest ancestor's** profile along 022's pinned derivation chain (`snapshot.effective(leaf).chain`). The walk follows only that chain, so it never matches by name, never reaches a namesake, an unlinked derivation (the snapshot refuses it) or an alternate portrayal.
3. A layer whose own `derivation.clear_sections` names `voice-evidence` stops the walk above it: clearing the base's voice evidence clears its voice.
4. Then the campaign `VoiceBinding`'s replace/add/suppress, through the same merge as `resolve_voice` (`merge_binding`).

Nothing found is `MissingVoice("no_profile")`. A binding's alternate `portrayal` is refused here (`VoiceMismatch("alternate_portrayal")`): a snapshot actor's portrayal is its selection. Item-level portrayal deltas for voice are later work.

## Briefs

A brief is a sealed value: only its builder makes one.

**`npc_brief(view, voice, *, heard=(), secrets, sections=(), names=None, ...)`** builds one NPC's brief from its own `ActorView` (`actor_view` or `snapshot_actor_view`):

- It refuses `view.knowledge is None` (`no_knowledge`), and knowledge that is not this actor's own `ActorKnowledge` (`foreign_knowledge`), such as director knowledge or another actor's projection. A voice for another actor is `voice_mismatch`.
- **Known propositions** are the ids the actor holds: reports it received or sent, and its beliefs. A secret (`SecretFact`, keyed by proposition id) not among them is **hidden** from this actor.
- The voice fragment is 014's `speaker_fragment(voice, secrets=, known=)`, so an example revealing a hidden secret is screened out.
- Knowledge is rendered **without ids**, newest first within `knowledge_chars`, shown oldest first. The actor's effective description and composed sections (`snapshot.effective(...).sections`) are added as context, whole parts only, within `context_chars`.
- `heard` is what the actor heard before this turn's calls. A heard line stating a marker of a hidden secret that the actor's own projection and card do not hold is refused as `leaky_cue`: the caller screened the cue too little, and nothing is sent.

**`director_brief(scenario, *, heard=(), player_characters, secrets)`** takes authored scenario text and table-visible lines **only**: there is no knowledge parameter at all. It names the player characters and forbids writing their words, thoughts, feelings or decisions. Every secret is hidden from it.

`npc_messages(brief, spoken)` and `director_messages(brief, spoken)` render a brief and the lines spoken so far this turn as `(role, content)` messages.

`secrets` is the caller's director-side list. 012 has no marker source yet, so the caller supplies the markers.

## Screening a reply

`screen_reply(brief, text, *, spoken, voices)` returns a rejection reason or `None`:

| Reason | Meaning |
|---|---|
| `silent_reply` | Blank or punctuation only. |
| `copied_example` | Reproduces an authored example of any voice in the scene (014's `example_copies`). |
| `unlearned_secret` | States a marker of a secret hidden from the speaker that nothing it held or heard exposed: its projection, its card, its heard lines, or a line spoken earlier this turn. |

Every present actor hears every spoken line in this slice (no whispering), so a secret one NPC says aloud may be repeated by the next. A director reply is screened the same way against its own inputs.

## Running a turn

`GenerationRuntime(adapter, routes, handle, *, approvals=None, max_output_tokens=400)` holds fixed routing. The handle and approvals pass through to `ProviderAdapter.execute` unchanged; tests cover a trusted app handle and an untrusted one approved per attempt (which of the two the app uses is still the requester's open question).

`run_turn(meter, briefs, *, controls, director=None, token=None, cancel=None) -> TurnOutcome`:

- **Checked before any call** (`GenerationError`): every planned call is `npc_generation` or `director_generation` (`unsupported_stage`), each `npc_generation` call has exactly one brief (`missing_brief`, `extra_brief`, `duplicate_brief`), a director brief is given exactly when a director reply is planned (`director_mismatch`), no planned call already ran on `meter` (`call_already_run`), and every brief's actor is director-controlled **now**, so a hand-built plan cannot voice a player character.
- **Calls run in plan order**, one `execute` each. Each later call hears the lines accepted before it; a planned director reply runs after the speakers.
- **Only accepted, screened text becomes a line.** A provider status other than `accepted`, or a screen rejection, ends the turn: later calls are `not_issued`. A rejected call is still billed.
- **Nothing streams to a consumer.** The runtime passes no `on_delta`: nothing is shown before acceptance, and a consumer would stop the adapter retrying a dropped stream. Streaming display is a later milestone.
- **Fence.** `RunFence.claim(key)` returns a `FenceToken`; a newer claim on the same caller-chosen key (one chat thread) makes it stale and cancels the run's token at once. The runtime also checks before each call and before returning. A stale run returns **no lines**. Cross-process fencing is 030's.
- **Cancel.** One `CancelToken` for the run: the in-flight call ends `cancelled` and nothing runs after it.

`TurnOutcome.status` is `complete` (every planned call produced a line), `halted`, `cancelled` or `stale`. `calls` holds every planned call's `CallOutcome` in plan order: `accepted`, `rejected` (with its screen reason), the adapter's `incomplete`/`failed`/`cancelled`/`refused`, or `not_issued`. Each `GeneratedLine` carries its call id, actor (`None` for the director, shown as `Narrator`), the voice's portrayal and the text: an **unreviewed proposal**.

## Proofs

- Acceptance A to D and the refusals: `backend/tests/test_generation_runtime.py`.
- Planning from control: `backend/tests/test_generation_plan.py`. Briefs and screening: `backend/tests/test_generation_brief.py`. Snapshot voices: `backend/tests/test_generation_voice.py`.
- Every provider fault, rejection, cancel and supersede point: `explore_generation_faults` ([TESTING](TESTING.md#generation-fault-invariants-grimoiretestinggeneration)).

## Not in this slice

| Item | Owner |
|---|---|
| `story-quality/3`, recording director calls in eval runs | 027's next slice, with 015 |
| Persisting generated lines as messages | 035, 032 |
| Streaming display, async UI | 035 |
| Cross-process run fencing | 030 |
| Director knowledge in narration | later |
| Detecting player-character authorship in generated text | 035 (`review_narration`) |
| Trusted vs untrusted handle for the app | the requester's open question |
| Retrieval-backed context packing | 031 |
| Other optional stages in a generation turn (`select`/`decision` phases) | later |
