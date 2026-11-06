# Scenes: the first playable text chat

**Status:** first slice of [Minimal Text Scene Runner](roadmap/sprints/minimal-text-scene-runner.md) (Sprint 035, Wave 5). Headless: there is no UI, streaming or async yet. It is three packages:
- `grimoire.scene` (pure): a played turn, its payload codec (`grimoire.scene-turn/1`), and what each listener heard;
- `grimoire.scenestore` (adapter): the `turns/` record family over one open `LibraryStore`;
- `grimoire.play` (adapter): `SceneRunner`, which plays one chat through [027](GENERATION.md)'s runtime.

All scenarios are synthetic and every model call is scripted.

## What it does

**Proving scenario** (`backend/tests/test_scene_proof.py`). Two chats are played, closed and opened again with a new store, authority and model runtime, as an app restart would:
- **A migrated V1 campaign.** [026](IMPORT_V1.md)'s Drowned Ledger import: Winifred is the player's character; Mara speaks in an authored voice and Seraphine in the voice derived from her imported card. Over three turns the player asks a question, whispers to Mara, pauses and resumes, redirects one of Seraphine's lines, saves a post and closes the app before any call. After the restart the post is still there and is answered. Mara's prompt holds the whisper; Seraphine's holds neither the whisper nor her own withdrawn line.
- **A world-less card chat.** [037](CARDS.md)'s Mara Vell card with its lorebook, and Anya Rook as the persona. The card's first greeting opens the chat; asking about the lighthouse triggers the card's lore in Mara's prompt. After a whisper, a second card (Tamsin Hale) is attached mid-chat. Tamsin's first prompt holds what was said since she arrived, and nothing before.
- **A scenario-only chat** (Storm over Port Vell) with no NPC: the only generation is a director reply the player asks for, and the policy allows.

Each speaking NPC gets its own call, with its own voice, its own [031](CONTEXT.md) pack and only the lines it heard. Every call's attempts and token usage are stored with its take, even when the turn halted or went stale.

## A turn

`TurnRef(campaign_id, turn_id)` is `campaign:<c>#turn/<t>`, stored at `campaigns/<c>/turns/<t>.json` in the scope-record envelope ([STORAGE](STORAGE.md)). `turn_id` is `<chat_id>.<n:06d>`, numbered from 1 per chat. A chat id over 97 characters cannot be played (`invalid_field`), so the turn id still fits 032's message ids.

A `TurnRecord` holds:
- `origin`: `post` (a player's post), `prompt` (the table asked the cast to go on), `opening` (a card's greeting or a scenario's opening), or `imported` (reserved; 036's imported history lives in its own `transcripts/` family);
- `post`: the player's line, who submitted it, its kind (`speech` or `action`), channel, addressees and **audience**;
- `takes`: up to 16. A take is `running`, `proposed`, `kept`, `withdrawn`, `stale` or `superseded`. It records its cast, its lines, every planned call with its status and attempts, the policy's declined calls, any unscreened director lore, a redirect's direction, and who chose it;
- `paused` (and, kept only for `resume`, who paused), and `passed_by`.

A turn is open until one take is kept or somebody passes it. Lines are `<turn_id>.<take>.<n>` and are `shown`, `withdrawn` or `struck`. Unknown top-level payload keys survive a rewrite; inside a known structure an unknown or malformed field is refused, never dropped.

**A turn is transcript, never world state.** A kept line is what the table chose to show. No event, belief, relation, effect or ruling is written, so nothing said here becomes canon a later rule could read.

## Who heard what

- A post's audience is 032's `transmit` receivers plus the sender. Room speech with nobody else present is heard by the speaker alone. An action that is not a private message is seen by everyone present.
- An NPC line is heard by everyone present. A narrator line is heard by the table (the present player characters), never by an NPC.
- **Audience is fixed when a line is spoken.** An actor attached later was in no earlier audience, so it hears nothing that came before.
- Proposals, withdrawn lines and struck lines are heard by no one. A reroll never hears the take it replaces.
- `heard_for` gives an NPC's window (the last `MAX_HEARD` lines, each capped at 027's `MAX_LINE_CHARS`); it is both its brief's `heard` and its pack's `scene_text`. `table_lines` is the director's view: every post but a private one, and every kept line.

## The runner

`SceneRunner(store, authority, handle, *, owner, runtime=None, policy=None, secrets=(), voices=None, fence=None, budget=DEFAULT_BUDGET)`.
- `handle` is a trusted human handle `authority` issued (the library owner's). It writes turn files and records control at `seat`.
- `runtime` is the app's `GenerationRuntime`: a trusted app handle, the configured provider only, no approvals asked. Without one, nothing is generated (`no_runtime`).
- `policy` caps the calls a turn may plan; a director reply needs `director_generation`.
- `secrets` are screened in every brief. With none, only 031's director-lore markers are screened.
- `voices` are authored profiles; an imported character with none derives its voice from its card (027/026), and one with no example lines speaks anyway.
- `fence` makes an older run of the same chat stale.

Every method reads the chat afresh (one `LibraryView`, 022's chat snapshot, the turns, 010b's recorded control, 033's party) and writes at most one turn file.

| Method | What it does |
|---|---|
| `open(chat)` | The `Scene` as it stands: turns, cast, persona, names, controls and problems. Writes nothing. |
| `seat(chat, player)` | Records control for actors with none: the persona to `player`, every other bound character to the director. A recorded controller is never changed. |
| `begin(chat, by)` | The opening turn, as a proposed take: a scenario's first opening (narrated to the table), else the launch card's first greeting spoken by its character, else the chat's premise. `{{char}}` and `{{user}}` are filled. |
| `post(chat, by, text, *, mode="auto")` | The player's line, routed by 008, checked by 003's `submit_contribution`, read by 032's `read_message`, and saved as a new turn before anything is generated. The same post retried onto its own untouched turn returns that turn. |
| `generate(chat, by, *, speakers=None, director=False)` | One new proposed take for the open turn (a `prompt` turn is opened when none is). Writes a `running` take with its planned calls first, then the result. |
| `keep(chat, by, take, *, acknowledge_unscreened=False)` | Keeps one proposed take with a shown line; the turn closes. A take written with unscreened director lore needs the acknowledgement. |
| `strike(chat, by, line_id)` | Takes a shown line of a kept take out of the transcript. |
| `pass_turn(chat, by)` | Closes the open turn with nothing kept. |
| `request(chat, by, kind, targets=(), *, direction=None)` | 016's mid-session request with no session-zero configuration: `pause`, or `skip`, `redirect` and `rephrase` naming lines, which are withdrawn (kept or proposed). A direction is recorded on the take. |
| `resume(chat, by)` | Lifts a pause; only the player who paused may. |

**Who may act.** `by` is a handle the same authority issued, trusted and human. Its principal must be the library owner or the controller of a present player character (`not_at_table`). Only the persona's controller may post for it (`not_your_character`). The principal is stored as `submitted_by` or `by`.

**Generating.** Speakers default to every present, director-controlled NPC with a voice; named speakers must be present (`not_present`), NPCs (`not_an_npc`) and voiced (`no_voice`). Each is briefed from its own knowledge (020, as of the newest sequence), its own pack built from this turn's snapshot, and only what it heard; silent present NPCs' voices go to the screen as `scene_voices`. A director reply is asked for explicitly, needs a bound scenario or a premise (`no_scenario`), and sees only what the table saw plus triggered lore. Every NPC line passes `review_narration` as voluntary speech by that NPC; a rejected line is dropped and its call marked `rejected`. The run re-checks its fence inside the commit: a run made stale keeps its usage and drops its lines. A take found `running` when the chat opens is reported `interrupted` and superseded by the next.

**Refusals** (`SceneRefused(reason)`), none of which writes anything: `bad_handle`, `bad_runtime`, `invalid_field`, `no_chat`, `not_seated`, `not_authenticated`, `not_at_table`, `not_your_character`, `already_begun`, `no_opening`, `no_persona`, `persona_absent`, `no_content`, `uncertain` (with the router's feedback), `not_in_this_slice` (commands, direction, interiority), 032's unread reasons, `turn_open`, `no_runtime`, `session_zero_unreadable`, `party_unreadable`, `unassigned_actor`, `nothing_to_generate`, `too_many_takes`, `director_declined`, `no_scenario`, `not_present`, `not_an_npc`, `no_voice`, `not_proposed`, `empty_take`, `unscreened_lore`, `not_kept`, `unknown_line`, `no_targets`, `nothing_to_pause`, `already_paused`, `not_paused`, `not_yours`, `paused`, `no_open_turn`, `turns_unreadable`, `turn_changed`.

## Storage and sync

- `turns/` is a history family: `load()` never reads it, and `store.campaign_turns(campaign)` lists it with per-file problems. A turn is created once (create-only, so two posts can never both become turn *n*) and then changed only by compare-and-swap.
- A turn file of this chat that cannot be read stops every write to the chat (`turns_unreadable`), as does a gap in the numbering; a damaged turn of another chat does not.
- Every actor id a turn names stays taken for 107's allocators (`turn_instance_ids`), so a deleted actor's id never goes to a newcomer who would inherit what it heard.
- **Sync holds turns back** (`turn` is a held kind, [SYNC](SYNC.md)): two devices playing one chat offline would both write its next turn. Devices that differ get a `held` conflict, never a silent drop. Turn merges are 028's milestone, due before 044.

## Known limits

- **One turn file per write.** A post, a take or a mark is one compare-and-swap; a concurrent writer is refused `turn_changed`. A run that loses that race is written again as a usage-only stale take, up to three times.
- **No control history.** `seat` records control for the chat's current actors; reassigning a character mid-chat is 003's.
- **Party.** The cast is the chat's active bindings minus 033's reserved members. A member whose party state cannot be read is left out and reported (fail closed); any party problem refuses generation.
- **No `last_activity` update** per post (107's chat record would need a second write in the commit).
- **The director reply's scenario text** is the scenario's premise, setting and instructions, or the chat's premise.

## Later milestones (named owners)

| Item | Owner |
|---|---|
| Labelling kept narration into proposals; 029 framing and decision; `check_effect`; 021 acceptance; 030 recovery at open before the next acceptance | 035 M2, 029, 021, 030 |
| Writing 032's delivery records to 020 in the turn's commit, so NPC memory of a scene is knowledge, not only a window | 035 M2 |
| Executing a session-zero configuration (016 has no codec yet; a scope with one refuses generation) | 035 M2, 016 |
| Sending a redirect's or rephrase's direction to the model | 035 M2, 027 |
| UI commands, director instructions and interiority in play | 035 M2 |
| Detecting player-character authorship in NPC and narrator text | 035 M2, Narration Continuity Referee |
| UI, streaming, async, the chat list | 035 M2, 065 |
| Several human players at one table | 102 |
| Merging turns across devices | 028 milestone, before 044 |
| Continuing a chat from its imported history: 036 stores imported chats in its own immutable `transcripts/` family; the runner does not read it yet, so NPCs have heard none of it (fail closed) | 035 M2, 036 |
