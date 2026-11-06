# Per-NPC generation (first slice of sprint 027)

**Status:** first slice of roadmap sprint 027, [Per Npc Generation Runtime](roadmap/sprints/per-npc-generation-runtime.md). Headless: it plans, briefs, issues and screens a turn's generation calls through the real provider adapter, with synthetic tests and scripted providers only. It **returns proposals and commits nothing**: no message, event, belief or effect is written. Persisting the lines and showing them is 035's (with 032), and reviewing a director reply is 035's `review_narration`. A finished turn can be recorded as a `story-quality/3` run for the offline gate ([STORY_QUALITY](evals/STORY_QUALITY.md#format-grimoirestory-quality3)).

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

### Imported voices

An imported character has no hand-authored profile, so the catalog for play is built from the snapshot: **`SnapshotVoices(snapshot, authored=None, readers=())`**. For every revision on a selected actor's pinned chain (leaf first, then its bases) with no profile in `authored`, `derive_voice(definition, readers)` builds one from **that revision's own text**. Nothing is stored and nothing is matched by name: the same revision always derives the same profile, and a new revision (a card update's `r2`, a changed V1 card) derives its own. An authored profile for the same revision wins.

Where the text comes from is a `VoiceReader`: a function from a revision's definition to its `VoiceTexts(examples, personality, description, imported, notes, speaker=None)` of plain strings (`speaker` is the name the examples label the character's own turns with; `None` means the revision's own, and a derived portrayal that re-derives from its base's examples keeps the base's, so a rename neither drops the base's turns nor keeps another speaker's), or `None` when the revision is not its kind. 027 is pure and parses nothing, so it reads only the 037 card shape itself (`card_voice_texts`). A V1 portrayal keeps its card verbatim in `extras.v1.text`, so its reader is the adapter `grimoire.import_v1.v1_voice_texts`, and the play catalog of a V1 import is `grimoire.import_v1.imported_voices(snapshot, authored=None)` (`SnapshotVoices` with that reader). Without it a V1 portrayal derives nothing and stays `no_profile`. A stored V1 text nested too deep for the JSON parser speaks from its description, noted `card_unreadable`; it never stops the rest of the catalog.

- `catalog` is a `SnapshotCatalog`: the 014 `VoiceCatalog` (authored plus derived) that `resolve_snapshot_voice` takes, plus `silent`, the revisions imported with no usable text (`gaps`). A profile cannot be empty, so a silent revision is a barrier instead: wherever it sits on an actor's chain, leaf or base, the walk stops there with **`MissingVoice("no_voice_text")`**, naming it, and never reaches a base's voice. Copying the profiles into a plain `VoiceCatalog` drops the barrier.
- `resolve(ref, binding=None)` is `resolve_snapshot_voice` over that catalog.
- `derived` maps each derived revision to its `DerivedVoice(portrayal, profile, notes, texts)`: `notes` are `VoiceNote(field, index, reason)` values, never text.

What is read, by payload shape (any other revision derives nothing):

| Revision | Reader | Examples | Anchors |
|---|---|---|---|
| 037 card character (`card.format` `chara_card_v3`) | built in | `card.texts.example-messages` | the `personality` section (or `card.unsectioned.personality`), the description |
| 026 V1 card portrayal (`extras.v1`) | `import_v1.v1_voice_texts` | the verbatim card's `data.mes_example` | its `data.personality`, the description |
| 026 V1 persona (`persona_md`), or a V1 card that does not parse | `import_v1.v1_voice_texts` | none | the description |

A card's system prompt, post-history instructions, scenario, greetings and creator notes are never read: they are not how the character sounds, and some are private.

- **Examples** (`example-<n>`, `n` the turn's place in `mes_example`): the text is split on `<START>`; a turn starts at a speaker label and runs to the next one. Only turns labelled `{{char}}`, `<BOT>` or the character's own name are kept. `{{char}}` becomes the name and `{{user}}` becomes `you`.
- **Labels fail closed, by construction.** A line wrongly kept would put someone else's words, maybe a secret, in this voice, so only a line that cannot be a label joins a turn. A label is a macro or one to four words, optionally wrapped in markup, brackets or quotes (`**{{user}}**`, `[Anon]`, `"{{char}}"`, `> {{user}}`), then an optional `(aside)` and `:` or the full-width `：`, with or without a space after it. An unlabelled line joins the turn above it **only** when it holds no macro or angle tag at all (`{{…}}`, `<USER>`, `<BOT>`…), has no colon or lookalike colon (`꞉ ˸ ∶`), pipe, `>`, en or em dash or spaced hyphen (` - `) in its first 64 characters, does not open with a wrapped token and a separator (`"Anon" - …`), and does not open with a bracketed token at all (`[Anon] …`, `【Anon】 …`, `「Anon」 …`). The decision reads the line NFKC-normalised, so full-width and presentation-form colons (`：︓﹕`) count as `:`; the kept text stays as written. Labels may be wrapped in CJK brackets too (`【{{char}}】：`). Any other line ends the turn, and what follows is dropped, never appended, until the next label. The labelled line itself is cut where another speaker's macro label starts (`Hi. [{{user}}]: …`). Losing some of the character's own lines is the price, and accepted: in `{{char}}: Listen.` / `Remember: the tide waits.` only `Listen.` is kept, and `*{{char}} smiles.*` or a line with an early dash is dropped too. One limit is inherent: another speaker's line with no label and no separator (`I tell you the code is 4471`) cannot be told from the character's own continuation, and is kept; 014's secret screening still applies to it. Text before the first label is not a turn. Every line is cut to 1200 characters (twice the example cap) before it is matched, and the patterns use single character-class runs, so a 64 KiB line costs no more than a short one.
- **Anchors**, in this order: the personality's sentences packed into anchors of at most 400 characters (`personality-<n>`, `register`); description sentences that are about speech (they name speaking, a voice, words, an accent, swearing and the like: `description-<n>`, `habit`). The rest of a description is biography, which the brief already shows as context. An anchor's aspect comes from the first rule its words match (`avoid`: never, refuses, won't…; `cadence`: sentences, clipped, pauses…; `register`: formal, blunt, wry…; `diction`: words, slang, accent…), else the default.
- **Caps** are 014's: at most 32 anchors and 32 examples, anchors of 400 and examples of 600 characters. A longer item is cut after its last whole sentence that fits (else at a space, else hard) and noted `truncated`; a blank item is skipped (`blank`), a repeat (`duplicate`) and one past the count cap (`over_cap`).
- **Speak anyway.** With no example and no anchor, the description's opening sentences (up to 400 characters) are one `register` anchor, `description-0`, noted `description_fallback`. Only a revision with no usable text at all (no example turn of its own, no personality, no description) has no profile: `no_voice_text`.
- **Derived layers.** A layer with no voice of its own inherits its base's as above, with one exception: when the base's voice was *derived* and a layer between it and the leaf authors or clears `personality`, or replaces or clears the description, that voice describes text the leaf no longer has. The leaf then derives its own from its effective personality and description and the base's examples. A hand-authored base voice is inherited unchanged, and a layer that authors or clears `voice-evidence` still stops the walk.
- **Export.** For a V3 card (037, or a V1 card whose spec is `chara_card_v3`), `imported` is an `ImportedVoiceSource` holding the exact `mes_example` and the card's extensions (`card_version` is the V1 card's `LegacyId`; `anchor_text` is empty). Extensions 014 cannot freeze are left out (`extensions_not_carried`); the verbatim card still holds them.
- **Screening is unchanged.** Derived examples carry no `reveals`; 014's marker denylist still omits any anchor or example that states a secret the speaker has not learned.

Not yet: a V1 record's `voice_anchor.md` (the world actor's or a campaign's own) is not read: 026 lists it as `actor_sidecar` ([IMPORT_V1](IMPORT_V1.md#known-limits)). The character's own name spelled otherwise is not recognised as a label of theirs, so that turn is dropped.

## Briefs

A brief is a sealed value: only its builder makes one.

**`npc_brief(view, voice, *, heard=(), secrets, sections=(), names=None, ..., context=None, snapshot=None, intention=None)`** builds one NPC's brief from its own `ActorView` (`actor_view` or `snapshot_actor_view`):

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
| `context_mismatch` | built as of another moment than `view.knowledge`, or from another projection of the actor's knowledge at that moment (`pack.knowledge != knowledge_digest(view.knowledge)`: a ledger reload quarantined or restored one of its records); `view` is not `snapshot`'s view of the actor (the actor is not captured there, or is captured or bound otherwise); or the pack's `basis` is not `selection_basis(snapshot, ...)` |

The `basis` ([CONTEXT](CONTEXT.md#the-selection-a-pack-is-built-over)) is the campaign, the actor's own bindings, every `lorebook` and `scenario` binding (as selected, as resolved, and the content etag of each revision read through it) and the captured instance's etag. So a pack from another chat that reuses the same actor and card (107's `reuse_actors`) but binds other lore is refused, and so is a pack from before a lorebook was attached, detached, re-pinned or moved to a new head, or edited by hand in place (same reference, new content), or before the actor's instance record changed. A campaign original, which has no card, is checked the same way. What is **not** checked: that `snapshot` is the chat the caller means to play. It is the caller's word for that, as `heard` is its word for what the actor heard.

**Screening.** Every line the brief shows from the pack counts as exposed to this actor, so a reply quoting common lore it was given is not `unlearned_secret`. Director-only lore is never in an actor's pack (031's audience rule, and the `foreign_context` check above), and a pack read over another selection is `context_mismatch`, so lore the brief's own selection does not give the actor as `common` can never be exposed this way. A pack is not sealed: the caller builds it with `pack_actor_context` from the actor's own binding, as it builds `heard` from what the actor heard.

### Intentions (039)

With `intention`, a `Choice` from 039's `choose` ([GOALS](GOALS.md)) for **this same actor from this very projection of its knowledge**, the brief ends with what the NPC is after ("What you are after right now:", the goal's aim), what it means to do about it ("What you mean to do about it:", the chosen approach's action) and what it will not do here ("What you will not do here, whatever it would gain you:", the rules of the limits it is refusing now). A choice with nothing to pursue and nothing refused adds nothing, byte for byte. A refused or outranked approach's action is never shown. This is the whole cost of an NPC's goals: the same one `npc_generation` call, no `planning` or `decision` call.

| Refusal | When |
|---|---|
| `bad_intention` | not a `Choice` (exactly; `Choice` cannot be subclassed) |
| `foreign_intention` | another actor's choice |
| `intention_mismatch` | chosen from another `ActorKnowledge` than `view.knowledge` (another moment, or a projection a ledger reload has since changed): it may rest on a belief the actor no longer holds, or miss one that would have stopped it |
| `intention_leak` | an aim, action or rule shown would state a hidden secret's marker that nothing the actor holds exposes. The agenda is the director's, so its text may name what this NPC has not learned. Gating an approach on `Holds` of that proposition makes **that approach's action** safe (the actor holds it by the time it is chosen). It does not make the goal's aim safe, which is shown whenever any approach of the goal is chosen, nor a limit's rule, which is shown whenever the limit refuses something: keep those free of what the NPC may not know. A disguised actor's real name in an action is caught the same way when its identity is registered as a `SecretFact`, as for labels |

Shown intention text is screened, not added to what the actor holds: it never states a hidden secret, so a reply repeating it is screened as any other reply.

`run_turn` refuses, before any call, a brief whose intention was chosen for another scene than the turn's plan has present (`intention_cast_mismatch`): the choice's `present` and `unrecognised` actors ([GOALS](GOALS.md#choosing)) must be exactly the plan's `present_actor_ids`. Someone the approach needs may have left, or someone a limit watches for may have arrived.

### The director's brief

**`director_brief(scenario, *, campaign_id, present, controls, names, heard=(), secrets, lore=None, snapshot=None)`** takes authored scenario text, 031's director lore and table-visible lines **only**: there is no knowledge parameter at all. The player characters are **derived from control** (every present actor with a human controller), never passed in; `names` must give each a nonblank display name (`bad_players`). The brief names them and forbids writing their words, thoughts, feelings or decisions. A present actor with no controller is `unassigned_actor`. Every secret is hidden from it, but the player characters' names are table-visible, so narration may name them.

`lore` is a `LorePack` from 031's `director_lore` ([CONTEXT](CONTEXT.md#director-lore)), so a scenario-only chat's narrator gets the bound lorebook's entries that table-visible text triggered. Its `common` entries are listed as "What anyone here may know:" and count as exposed, so narration may repeat them. Its `director` entries are listed as known only to the narrator, never to be stated outright, and are **hidden like secrets**, but only sentence by sentence. Each sentence of an entry with at least `MIN_LORE_MARKER_WORDS` (5) words, its edge punctuation trimmed, is a marker of a hidden `SecretFact`. Sentences end at `.`, `!`, `?`, `;` or `…`, after any closing quotes or brackets (`"`, `'`, `”`, `’`, `»`, `›`, `」`, `』`, `）`, `)`, `]`), followed by a space, or at a full-width `。`, `！` or `？` (with any closing quotes), so `"He sold it." "She knew."` is two sentences. A fragment of under two words before a stop (`Mr.`, `Dr.`, `Rain.`) does not end a sentence: it stays with the next sentence, and that sentence is also screened without it when it is long enough on its own. In scripts written without spaces, two characters count as a word. Narration that repeats such a sentence **word for word** (whole phrase, case- and width-insensitive, as every secret marker is matched) is `unlearned_secret`, whether or not the caller registered a secret for it, unless the scenario, common lore, a player's name, the table text the lore was triggered on (`LorePack.table_text`) or a heard line says it too. What is **not** caught: part of a sentence, a paraphrase, and any sentence shorter than five words. A shorter sentence ("It is late.") is ordinary prose, so screening it would reject innocent narration and halt the turn. An entry with no sentence of five words is left unscreened and listed in `DirectorBrief.unscreened` as `(source, entry id)` pairs, for the trusted caller to review.

With `lore`, `snapshot` is required: the chat's `EffectiveSnapshot` the brief is for.

| Refusal | When |
|---|---|
| `bad_lore` | not a `LorePack` (an actor's `ContextPack` included: its memory is one actor's own), or no `snapshot` given with it |
| `foreign_context` | lore built for another campaign than `campaign_id` |
| `context_mismatch` | the lore's `basis` is not `selection_basis(snapshot, campaign_id)`: another chat's, or this chat's from before a lore binding was attached, detached, re-pinned, moved or edited by hand in place, so a stale pack cannot show as `common` an entry the current revision makes the director's |

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

- **Checked before any call** (`GenerationError`): every planned call is `npc_generation` or `director_generation` (`unsupported_stage`), or a `decision` call that `decide` already ran on this meter (`decision_not_run`) and whose pick the NPC's brief carries (`decision_ignored`, [DECISIONS](DECISIONS.md#the-decision-call)), each `npc_generation` call has exactly one brief (`missing_brief`, `extra_brief`, `duplicate_brief`), a director brief is given exactly when a director reply is planned (`director_mismatch`), no planned call already ran on `meter` (`call_already_run`), every brief's actor is director-controlled **now**, so a hand-built plan cannot voice a player character, the director brief names the same player characters that the plan and control give now (`director_mismatch`; an unassigned present actor is `unassigned_actor`), and every NPC brief's intention was chosen for exactly the actors this plan has present (`intention_cast_mismatch`, 039).
- **Calls run in plan order**, one `execute` each. Each later call hears the lines accepted before it; a planned director reply runs after the speakers, and a speaker planned after it (a later phase added on the meter) is refused before any call (`director_not_last`).
- **An exception from the adapter or an approvals callback** ends the call `failed` (reason: the exception type) and the turn `halted`, keeping earlier lines.
- **Only accepted, screened text becomes a line.** A provider status other than `accepted`, or a screen rejection, ends the turn: later calls are `not_issued`. A rejected call is still billed.
- **Nothing streams to a consumer.** The runtime passes no `on_delta`: nothing is shown before acceptance, and a consumer would stop the adapter retrying a dropped stream. Streaming display is a later milestone.
- **Fence.** `RunFence.claim(key)` returns a `FenceToken`; a newer claim on the same caller-chosen key (one chat thread) makes it stale and cancels the run's token at once. The runtime also checks before each call and before returning. A stale run returns **no lines**. Staleness is checked only up to the return: a caller that persists lines (035) must re-check `token.stale` inside its own commit. Cross-process fencing is 030's.
- **Cancel.** One `CancelToken` for the run: the in-flight call ends `cancelled` and nothing runs after it.

`grimoire.runtime.record.recorded_turn(turn, meter, outcome, *, player_lines=())` hands a finished turn to the story-quality `/3` recorder; it lives in its own module, so `import grimoire.runtime` loads no eval recorder. It refuses (`outcome_mismatch`) an outcome that `run_turn` did not return for this very `meter` (`TurnOutcome.meter_token`; another swipe of the same plan has the same call ids), whose calls are not `meter`'s plan, whose call results disagree with `meter`'s call statuses, or whose lines are not its accepted calls', and a meter with a failed booking (`poisoned_meter`).

`TurnOutcome.status` is `complete` (every planned call produced a line), `halted`, `cancelled` or `stale`. `calls` holds every planned call's `CallOutcome` in plan order: `accepted`, `rejected` (with its screen reason), the adapter's `incomplete`/`failed`/`cancelled`/`refused`, or `not_issued`. `meter_token` is the `TurnMeter.token` of the meter the turn was billed to (an opaque per-meter id; not compared or shown); it is a plain string so an outcome pickles and keeping one never keeps its meter alive or blocks regenerating the turn on a new meter. Each `GeneratedLine` carries its call id, actor (`None` for the director, shown as `Narrator`), `actor_revision` (the actor's own selected revision, such as a derived portrayal), `voice_profile` (the profile revision its voice came from, maybe an ancestor's) and the text: an **unreviewed proposal**.

## Proofs

- Acceptance A to D and the refusals: `backend/tests/test_generation_runtime.py`.
- Planning from control: `backend/tests/test_generation_plan.py`. Briefs and screening: `backend/tests/test_generation_brief.py`. Snapshot voices: `backend/tests/test_generation_voice.py`.
- Imported voices, end to end from 026's and 037's real importers through a brief and a scripted call; anchors-only, nothing usable, caps, fail-closed speaker labels, derived layers that change personality or description, a V1 re-run from a library imported before voices, revision pinning and secret screening: `backend/tests/test_generation_imported_voice.py`.
- Briefs from 031's packs and the director's lore: `backend/tests/test_generation_context.py`; over the native store in a world-less, a world-based and a scenario-only chat: `backend/tests/test_context_scenarios.py`.
- Every provider fault, rejection, cancel and supersede point: `explore_generation_faults` ([TESTING](TESTING.md#generation-fault-invariants-grimoiretestinggeneration)).
- Recording turns as gateable `/3` runs (scenario-only and two-NPC): `backend/tests/test_runtime_record.py`.

## Not in this slice

| Item | Owner |
|---|---|
| Persisting generated lines as turns (035 first slice, [SCENES](SCENES.md)); as 020 delivery records | 035 M2, 032 |
| Streaming display, async UI | 035 |
| Cross-process run fencing | 030 |
| Director ledger knowledge in narration (bound director lore is in) | later |
| Detecting player-character authorship in generated text (035's first slice reviews each NPC line as that NPC's voluntary speech) | 035 M2 (`review_narration`) |
| Richer retrieval behind the same pack seam (recent history, unbound world records, embeddings) | 031 |
| Building each speaker's pack and the director's lore every turn, with the transcript window as scene text | 035 (done in its first slice, [SCENES](SCENES.md)) |
| Other optional stages in a generation turn (`select`/`decision` phases) | later (045 for decisions) |
| Choosing an NPC's intention every turn and giving the turn to an NPC that acts on its goals | 035 M2 |
