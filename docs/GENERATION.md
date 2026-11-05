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
- **Speakers are director NPCs.** A speaker must be present (`absent_speaker`) and controlled by `DIRECTOR`. A player character is `player_speaker` and an actor nobody controls is `unassigned_speaker`. Two controllers for any present actor is `ambiguous_control`, and an actor listed twice is `duplicate_present` or `duplicate_speaker`.
- **Director reply is asked for, never assumed.** `director_reply=True` adds an optional request. It needs every present actor to have a controller (`unassigned_actor`): the director brief must name every player character, so control fails closed. With a policy that has no cap for `director_generation` (the default), the request is declined as data (`plan.declined`, reason `disabled`) and nothing is planned.

Failures raise `GenerationError` with a `reason`.

## Voices of snapshot actors

`resolve_snapshot_voice(ref, snapshot, catalog, binding=None)` is the voice of an actor captured in a 022 `EffectiveSnapshot`, with or without a world. `resolve_voice` ([VOICE](VOICE.md)) stays the path for an index actor.

1. The actor's selected **leaf** revision's own profile, if the catalog has one. A leaf that authors a profile does not inherit.
2. Otherwise the **nearest ancestor's** profile along 022's pinned derivation chain (`snapshot.effective(leaf).chain`). The walk follows only that chain, so it never matches by name, never reaches a namesake, an unlinked derivation (the snapshot refuses it) or an alternate portrayal.
3. A layer with no profile of its own stops the walk above it when it clears `voice-evidence` (`derivation.clear_sections`) or authors its own `voice-evidence` section: either way the base's voice no longer describes it. (022 refuses a clear of a section the base does not author, `stale_clear`.)
4. Then the campaign `VoiceBinding`'s replace/add/suppress, through the same merge as `resolve_voice` (`merge_binding`).

Nothing found is `MissingVoice("no_profile")`. A binding's alternate `portrayal` is refused here (`VoiceMismatch("alternate_portrayal")`): a snapshot actor's portrayal is its selection. Item-level portrayal deltas for voice are later work.

## Briefs

A brief is a sealed value: only its builder makes one.

**`npc_brief(view, voice, *, heard=(), secrets, sections=(), names=None, ..., context=None, snapshot=None)`** builds one NPC's brief from its own `ActorView` (`actor_view` or `snapshot_actor_view`):

- It refuses `view.knowledge is None` (`no_knowledge`), and knowledge that is not this actor's own `ActorKnowledge` (`foreign_knowledge`), such as director knowledge or another actor's projection. The actor's typed ref (a character, or a speaking item) comes from its own projection. A voice for another actor, or resolved for another selected revision than `view.resolved_source`, is `voice_mismatch`.
- Superseded beliefs keep their stance ("Earlier, you doubted: ...").
- **Known propositions** are the ids the actor holds: reports it received or sent, and its beliefs. A secret (`SecretFact`, keyed by proposition id) not among them is **hidden** from this actor.
- The voice fragment is 014's `speaker_fragment(voice, secrets=, known=)`, so an example revealing a hidden secret is screened out.
- Knowledge is rendered **without ids**, newest first within `knowledge_chars`, shown oldest first; one item too big for the budget alone is skipped, not the rest. The actor's effective description and composed sections (`snapshot.effective(...).sections`) are added as context, whole parts only, within `context_chars`.
- The card's `voice-evidence` section is **left out**: the voice reaches the prompt only through the screened fragment, so a suppressed binding item or an example revealing a hidden secret cannot return through the card.
- `heard` is what the actor heard **aloud** before this turn's calls. Its text counts as learned, as a line spoken earlier this turn does, so the caller passes only lines this actor heard (every present actor, in this slice).
- **Speaker labels are not learned.** A `HeardLine` may carry the speaker's `actor`; each listener labels it by its own `names`, else by `speaker`. A label stating a secret the listener has not learned (a disguised NPC's real name, say) is shown as `Someone`, to NPCs and to the director alike. The same holds for the names in the actor's knowledge ("X told you", "You told X"): only what its items say counts as learned, never who said them.

### From 031's packs

Without `context`, a brief is exactly 027's own minimal rendering above, byte for byte (a test pins the whole `system` text). With `context`, a `ContextPack` from 031's `pack_actor_context` ([CONTEXT](CONTEXT.md)) for **this same actor over the same snapshot**, the pack's lanes replace that rendering. `snapshot` is then required: the `EffectiveSnapshot` that `view` and `voice` were resolved from, which names the selection the brief is for:

- `portrayal` becomes "Who you are:" (the pack's description and composed sections, never its `You are Name.` line, which the brief's opening already words, and never a `voice-evidence` section, even from a pack built with `exclude_sections=()`);
- `lore` becomes "What anyone here may know:" (`common` entries only);
- `memory` becomes "What you know:", in the pack's rank order.

The pack is already within its own token budget, so `sections`, `context_chars` and `knowledge_chars` do not apply; passing `sections` too is `bad_sections`. `view`, `voice` and `secrets` keep every rule above: what counts as learned (`known_ids`), the hidden secrets, the screened voice fragment and the speaker labels still come from the actor's own projection.

| Refusal | When |
|---|---|
| `bad_context` | not a `ContextPack`, one that does not open with this actor's name line, or no `snapshot` given with it |
| `foreign_context` | a pack built for another actor (another chat's actor included), the director's `LorePack`, or a pack holding a lore item of any audience but `common` |
| `context_mismatch` | built as of another moment than `view.knowledge`; `view` is not `snapshot`'s view of the actor (the actor is not captured there, or is captured or bound otherwise); or the pack's `basis` is not `selection_basis(snapshot, ...)` |

The `basis` ([CONTEXT](CONTEXT.md#the-selection-a-pack-is-built-over)) is the campaign, the actor's own bindings, every `lorebook` and `scenario` binding (as selected and as resolved) and the captured instance's etag. So a pack from another chat that reuses the same actor and card (107's `reuse_actors`) but binds other lore is refused, and so is a pack from before a lorebook was attached, detached, re-pinned or moved to a new head, or before the actor's instance record changed. A campaign original, which has no card, is checked the same way. What is **not** checked: that `snapshot` is the chat the caller means to play. It is the caller's word for that, as `heard` is its word for what the actor heard.

**Screening.** Every line the brief shows from the pack counts as exposed to this actor, so a reply quoting common lore it was given is not `unlearned_secret`. Director-only lore is never in an actor's pack (031's audience rule, and the `foreign_context` check above), and a pack read over another selection is `context_mismatch`, so lore the brief's own selection does not give the actor as `common` can never be exposed this way. A pack is not sealed: the caller builds it with `pack_actor_context` from the actor's own binding, as it builds `heard` from what the actor heard.

### The director's brief

**`director_brief(scenario, *, campaign_id, present, controls, names, heard=(), secrets, lore=None, snapshot=None)`** takes authored scenario text, 031's director lore and table-visible lines **only**: there is no knowledge parameter at all. The player characters are **derived from control** (every present actor with a human controller), never passed in; `names` must give each a nonblank display name (`bad_players`). The brief names them and forbids writing their words, thoughts, feelings or decisions. A present actor with no controller is `unassigned_actor`. Every secret is hidden from it, but the player characters' names are table-visible, so narration may name them.

`lore` is a `LorePack` from 031's `director_lore` ([CONTEXT](CONTEXT.md#director-lore)), so a scenario-only chat's narrator gets the bound lorebook's entries that table-visible text triggered. Its `common` entries are listed as "What anyone here may know:" and count as exposed, so narration may repeat them. Its `director` entries are listed as known only to the narrator, never to be stated outright, and are **hidden like secrets**, but only sentence by sentence. Each sentence of an entry with at least `MIN_LORE_MARKER_WORDS` (5) words, its edge punctuation trimmed, is a marker of a hidden `SecretFact`. Sentences end at `.`, `!`, `?` or `;` followed by a space, or at a full-width `。`, `！` or `？`. A fragment of under two words before a stop (`Mr.`, `Dr.`, `Rain.`) does not end a sentence: it stays with the next sentence, and that sentence is also screened without it when it is long enough on its own. In scripts written without spaces, two characters count as a word. Narration that repeats such a sentence **word for word** (whole phrase, case- and width-insensitive, as every secret marker is matched) is `unlearned_secret`, whether or not the caller registered a secret for it, unless the scenario, common lore, a player's name, the table text the lore was triggered on (`LorePack.table_text`) or a heard line says it too. What is **not** caught: part of a sentence, a paraphrase, and any sentence shorter than five words. A shorter sentence ("It is late.") is ordinary prose, so screening it would reject innocent narration and halt the turn. An entry with no sentence of five words is left unscreened and listed in `DirectorBrief.unscreened` as `(source, entry id)` pairs, for the trusted caller to review.

With `lore`, `snapshot` is required: the chat's `EffectiveSnapshot` the brief is for.

| Refusal | When |
|---|---|
| `bad_lore` | not a `LorePack` (an actor's `ContextPack` included: its memory is one actor's own), or no `snapshot` given with it |
| `foreign_context` | lore built for another campaign than `campaign_id` |
| `context_mismatch` | the lore's `basis` is not `selection_basis(snapshot, campaign_id)`: another chat's, or this chat's from before a lore binding was attached, detached, re-pinned or moved, so a stale pack cannot show as `common` an entry the current revision makes the director's |

### Messages

`npc_messages(brief, spoken)` and `director_messages(brief, spoken)` render a brief and the lines spoken so far this turn as `(role, content)` messages.

`secrets` is the caller's director-side list. 012 has no marker source yet, so the caller supplies the markers.

## Screening a reply

`screen_reply(brief, text, *, spoken, voices)` returns a rejection reason or `None`:

| Reason | Meaning |
|---|---|
| `silent_reply` | Blank or punctuation only. |
| `too_long` | Longer than a heard line may be (`MAX_LINE_CHARS`, 2000 characters). |
| `copied_example` | Reproduces an authored example of any speaker's voice in this turn, or of a voice the caller passes as `run_turn(..., scene_voices=...)` (silent present NPCs; 014's `example_copies`). The caller owes those: a voice it leaves out is not checked. |
| `unlearned_secret` | States a marker of a secret hidden from the speaker that nothing it held or heard exposed: its projection, its card, its heard lines, or a line spoken earlier this turn. |

Every present actor hears every spoken line in this slice (no whispering), so a secret one NPC says aloud may be repeated by the next. A director reply is screened the same way against its own inputs.

## Running a turn

`GenerationRuntime(adapter, routes, handle, *, approvals=None, max_output_tokens=400)` holds fixed routing. The handle and approvals pass through to `ProviderAdapter.execute` unchanged; tests cover a trusted app handle and an untrusted one approved per attempt. The app uses a trusted `app:` handle (decided; the limits are in [TOOL_AUTHORITY](TOOL_AUTHORITY.md#requirements-on-later-sprints)).

`run_turn(meter, briefs, *, controls, director=None, token=None, cancel=None, scene_voices=()) -> TurnOutcome`:

- **Checked before any call** (`GenerationError`): every planned call is `npc_generation` or `director_generation` (`unsupported_stage`), each `npc_generation` call has exactly one brief (`missing_brief`, `extra_brief`, `duplicate_brief`), a director brief is given exactly when a director reply is planned (`director_mismatch`), no planned call already ran on `meter` (`call_already_run`), every brief's actor is director-controlled **now**, so a hand-built plan cannot voice a player character, and the director brief names the same player characters that the plan and control give now (`director_mismatch`; an unassigned present actor is `unassigned_actor`).
- **Calls run in plan order**, one `execute` each. Each later call hears the lines accepted before it; a planned director reply runs after the speakers, and a speaker planned after it (a later phase added on the meter) is refused before any call (`director_not_last`).
- **An exception from the adapter or an approvals callback** ends the call `failed` (reason: the exception type) and the turn `halted`, keeping earlier lines.
- **Only accepted, screened text becomes a line.** A provider status other than `accepted`, or a screen rejection, ends the turn: later calls are `not_issued`. A rejected call is still billed.
- **Nothing streams to a consumer.** The runtime passes no `on_delta`: nothing is shown before acceptance, and a consumer would stop the adapter retrying a dropped stream. Streaming display is a later milestone.
- **Fence.** `RunFence.claim(key)` returns a `FenceToken`; a newer claim on the same caller-chosen key (one chat thread) makes it stale and cancels the run's token at once. The runtime also checks before each call and before returning. A stale run returns **no lines**. Staleness is checked only up to the return: a caller that persists lines (035) must re-check `token.stale` inside its own commit. Cross-process fencing is 030's.
- **Cancel.** One `CancelToken` for the run: the in-flight call ends `cancelled` and nothing runs after it.

`TurnOutcome.status` is `complete` (every planned call produced a line), `halted`, `cancelled` or `stale`. `calls` holds every planned call's `CallOutcome` in plan order: `accepted`, `rejected` (with its screen reason), the adapter's `incomplete`/`failed`/`cancelled`/`refused`, or `not_issued`. Each `GeneratedLine` carries its call id, actor (`None` for the director, shown as `Narrator`), `actor_revision` (the actor's own selected revision, such as a derived portrayal), `voice_profile` (the profile revision its voice came from, maybe an ancestor's) and the text: an **unreviewed proposal**.

## Proofs

- Acceptance A to D and the refusals: `backend/tests/test_generation_runtime.py`.
- Planning from control: `backend/tests/test_generation_plan.py`. Briefs and screening: `backend/tests/test_generation_brief.py`. Snapshot voices: `backend/tests/test_generation_voice.py`.
- Briefs from 031's packs and the director's lore: `backend/tests/test_generation_context.py`; over the native store in a world-less, a world-based and a scenario-only chat: `backend/tests/test_context_scenarios.py`.
- Every provider fault, rejection, cancel and supersede point: `explore_generation_faults` ([TESTING](TESTING.md#generation-fault-invariants-grimoiretestinggeneration)).

## Not in this slice

| Item | Owner |
|---|---|
| `story-quality/3`, recording director calls in eval runs | 027's next slice, with 015 |
| Persisting generated lines as messages | 035, 032 |
| Streaming display, async UI | 035 |
| Cross-process run fencing | 030 |
| Director ledger knowledge in narration (bound director lore is in) | later |
| Detecting player-character authorship in generated text | 035 (`review_narration`) |
| Richer retrieval behind the same pack seam (recent history, unbound world records, embeddings) | 031 |
| Building each speaker's pack and the director's lore every turn, with the transcript window as scene text | 035 |
| Other optional stages in a generation turn (`select`/`decision` phases) | later |
