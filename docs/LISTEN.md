# Listening to a chat, casting its voices, and previewing a draft's context (116)

Sprint 116's group D1 puts two Wave 9 first slices on a screen:

- **Listen to the scene** (083, [VOICE_CASTING](VOICE_CASTING.md)): a chat's accepted text read aloud, each speaker
  in the local voice cast for them, with every voice choice explained. Casting is on the **Voices** page.
- **Preview context** (085, [INFERENCE_UI](INFERENCE_UI.md#draft-preview-085)): before posting, what one NPC's
  context would likely hold if the composer's unsent draft were posted now.

Both sit beside the chat page's composer (`#/chat/<ChatRef>`), as 073's *What might they say?* and 080's dictation
do. They are separate modules (`pages/chat/listen.js`, `pages/chat/preview.js`) that page.js only creates and
tells when the chat is drawn again. Neither writes a turn or a source, and neither calls a model.

Listen to the scene is not 067's **Read aloud** ([SPEECH](SPEECH.md)). Read aloud reads the latest reply in the
browser's own voice. Listen to the scene plays the whole chat in the voices of the casting, through 083's local
engines.

## Addresses

| Route | Owner | Path / query |
|---|---|---|
| `voices` | 116 | `?chat=<ChatRef>` (also that chat's speakers, each with what it can be cast as) |

The Voices page is in Settings, next to 061's **Voice** page (speech recognition), which it does not change. The
Listen panel links to it for the same chat (**Cast voices**). The Preview panel links to the Models and costs page
for the same chat (**Full context**), where 112's inspector shows the NPC's whole current context.

## Ops

The page uses 083's `voicecast.*` ops unchanged. 085's `providers.preview_context` gains one optional argument,
`mode` (`auto`, `speech` or `action`, as `play.post` takes; left out, `auto`), passed to the domain
`preview_context(..., mode=)` that already took it, so the draft is read in the composer's mode as Post would read
it ([INFERENCE_UI](INFERENCE_UI.md)). One op is new, in the `voices` adapter (`grimoire.app.adapters.voices`,
owner 116):

| Op | Args | Answer |
|---|---|---|
| `voices.review` | `kind` (`cast` or `uncast`), and the args `voicecast.cast` or `voicecast.uncast` takes, without `expected` | `{kind, target, ref, campaign, describes, before, after, change, available, etag, writes, removes_file, entries_after, written: false}` |

`change` is one of:

- `new`: nothing was cast there;
- `replace`: another voice was cast there;
- `same`: that voice is already cast there, `writes` is empty;
- `remove`: an uncast.

`before` and `after` are `{engine, voice, key, rights, note}` or null. `available` says whether this device has
that voice now (null for a removal). `etag` is the casting file's etag as the review read it (null: no file yet).
`writes` names the file the change writes (`config/voices/casting.json`). `removes_file` says that the change leaves
the casting empty, which removes the file.

The review reads the same casting file as the write, through 083's own `CastingStore` and `Casting`
(`with_entry`, `without`). It refuses as the write would, in the write's order and with the write's reasons:

1. `read_only`: the library is open read-only;
2. `invalid_field`, `invalid_target`, `invalid_voice`, `invalid_rights`, `rights_unrecorded` (a `licensed` or
   `consented` voice with no note): the target and the voice;
3. `no_character`, `no_actor`, `no_campaign`: a new voice for something this library does not hold;
4. `link`: the casting file, or a folder on its way, is a link, which is never followed; `unreadable_record`: the
   file cannot be decoded, and it is never written over;
5. `not_cast`: an uncast where nothing is cast.

One reason differs on purpose: a file the operating system will not let it read is `casting_unreadable` from the
review, where the write says `casting_unwritable`, because the review only reads.

It writes nothing. It keeps no record either: there is no review token, and the next review reads the file again.

### A change, made as reviewed

1. The page asks `voices.review` and shows its answer: the sentence, the file it writes, and a note when this
   device lacks the voice.
2. Only on **Make this change** does it call `voicecast.cast` or `voicecast.uncast` with the same arguments and
   `expected` set to the review's `etag`.
3. If the casting changed in between (another tab, another screen), the write is refused `changed` and writes
   nothing. The page reads the casting again and says so.
4. If the write's answer is lost (`network`, `bad_response`), the page does not say that nothing was written: it
   reads the casting again and says whether it now holds the reviewed change (*the change was made*) or not
   (*review it again*). If that read fails too, it says that it cannot tell, and to reload. A write retried after
   it was made is never made twice: the second one names an etag that has moved on, is refused `changed`, and the
   page, reading the casting again, says that the change was already made. *Nothing was written* is said only for
   a refusal, which writes nothing.
5. Each review is numbered. An answer is shown only for the latest review, while the form still asks for what was
   reviewed: changing a choice or the rights note, or cancelling, drops a review shown or on its way.

## Listen to the scene (083)

The panel is a closed disclosure beside the composer, **Listen to the scene**. Nothing is read until the person
asks.

- **Plan the scene** calls `voicecast.plan`. Each cue is listed in order with:
  - its speaker;
  - its voice (`engine/voice`), or **Text only** with the reason (nobody cast a voice for this speaker; an action,
    shown and not spoken; only stage directions);
  - the accepted text, exactly as it was kept;
  - a licensed or consented voice's rights and note.

  **Why this voice** shows the plan's `why` sentence and every rule it looked at (the actor, the revision, the
  character at any revision, the voice profile, the narrator of this campaign or of every campaign) with the voice
  each one held. A voice cast for two speakers is a warning (`shared`). A casting file that cannot be read is said:
  every speaker stays text until it is mended on the Voices page.
- **Play** renders the scene through `voicecast.render`, four cues at a time. Each render names the plan's
  fingerprint. The segments play in order, each base64 WAV on a `blob:` URL (the page's policy allows media from
  `blob:` only) through one `<audio>` element, its source swapped per cue. The Play press itself starts that
  element on a muted silent sample before anything is awaited, since some browsers (iOS Safari) let an element
  play without a tap only once a tap has started it.
  - The next window is asked for while the last cue of the window plays, so synthesis (which runs under the
    library's lock) overlaps listening.
  - The cue being read is marked (`aria-current`, a bar and a heavier weight), and its speaker and accepted text
    are shown above the list.
  - A cue that stays text is shown for a moment and then passed.
  - A sound the browser did not play is marked text, with that reason.
- **Stop** ends the sound at once, and leaving the chat does the same.
- **`changed`.** A window refused `changed` means the scene or its casting moved on since the plan. The scene is
  planned again and the panel says so. **Play from \<speaker\>** goes on from the cue that was next, found by its item
  in the new plan, or from the start. Nothing is ever played out of step with the plan it was asked for.
- **`turn_fork`.** A forked chat is refused, and the panel says plainly that its branches are not one scene until the
  fork is resolved. It offers no Play, whether the fork was found when planning or while playing (the plan is
  dropped).
- **Audio off.** The checkbox **Play the voices** off reads the scene as text: windows are rendered with
  `audio: false`, nothing is synthesized, and each cue shows *audio is off: read as text*. A browser with no audio
  element reads as text the same way, and says so.
- A chat drawn again with another scene marks the plan stale: other turns, posts, kept takes, a line's text, or an
  outcome decided. Play plans it again first.
- **Stop** shows only while the scene plays, and Play is disabled only while it plans or plays, so no control sits
  disabled at rest.

Every engine is local, free and offline (083), and no audio is kept. Planning opens the chat as the play page
does, so it may note 020's pending knowledge, as any open does.

## The Voices page (083's casting)

- **Speakers in this chat.** With `?chat=`, each speaker of the chat's plan is listed in the order they first
  speak, with how many cues they have and what voices them now. Without a chat, a picker of chats is shown.
  **Cast a voice…** opens a form:
  - **Cast for**: every target the speaker's cues looked at. These are this campaign's own choice for the actor;
    the character at revision rN only; the character at any revision; the voice profile a line was written with;
    the narrator of this campaign; the narrator of every campaign.
  - **Voice**: every voice of every engine this device has now.
  - **Rights**: the engine's own voice, licensed, or consented. A licensed or consented voice needs a **Rights
    note**, and the form says so before anything is reviewed.

  **Review** then shows the review, and **Make this change** makes it.
- **The casting.** Every entry: its voice, what it is cast for, its rights and note, and whether this device has the
  voice now. **Remove…** reviews an uncast, and the removal is made the same way.
- **Engines on this device**: each engine's voices (local, no network, free).
- After a change, focus moves to the line that says what happened. After a review, it moves to **Make this change**;
  a review that would change nothing says so and moves to **Cancel**. Changing a choice in the form keeps the focus
  on that choice, and typing in the rights note goes on where it was.

The casting is this device's configuration (`config/voices/casting.json`), not a source, campaign or chat record,
so it carries no audience. The plan the page lists speakers from is the table's own transcript (083 never cues a
private message, a whisper, a proposal or a line a player character did not hear).

## Preview context (085)

The panel is a closed disclosure beside the composer, **Preview context**.

- **Whose view.** What an NPC would have in mind (its memories, the lore it is given) is the director's. The
  chat page is a screen the table may be looking at, so the panel opens in the **Table view**, which says only
  that. The **Director view** is chosen here, each time the chat is opened: it is never remembered and never opened
  by a link. The op itself acts as the library owner, as 112's inspector does (docs/INFERENCE_UI.md: *it is the
  owner's tool on this device*). This view is the chat page's courtesy to a shared screen; the Models and costs page
  shows the same context to whoever opens it.
- **Whose context**: one of the chat's NPCs, in cast order (the op refuses anyone else `not_npc`).
- **Preview context** sends `{chat, actor, draft, mode}` to `providers.preview_context`, where the draft is the
  composer's text as it is when the button is pressed, trimmed as Post trims it, and `mode` is the composer's mode
  as Post sends it (`auto` where the chat posts through the core `post`, which has no modes). The composer is read,
  never written: nothing is posted, and the text stays as it was. With an empty composer the panel asks for a draft
  first and sends nothing.

The answer is shown as an estimate, and the panel says that nothing was posted, saved or sent to a model:

- **Lore the draft triggers** (`why: triggered`). This is exact: it was not a candidate without the draft.
- **Memories it likely recalls** (`why: shared_words`, with the words). This is an estimate: memory is ranked by
  recency and presence, never by the draft.
- **Left out by the budget**: recalls the budget would shed, first, as an alert with their reason. `shed_recalls`
  counts them.
- An NPC that would not hear the draft (a whisper to someone else) keeps its saved context, and the panel says so
  without saying who would hear it.
- A draft that `post` would refuse is refused with `post`'s own reason (`table_request`, `not_your_character`, an
  open turn...), and the panel says it.

**A late answer is dropped.** Each preview is numbered. An answer is shown only when it is the latest request, for
the NPC still chosen, for the composer's text still as it was sent (trimmed) and in the mode it was sent in.
Typing on, or switching the composer's mode, while a preview is built drops its answer and says so. Choosing
another NPC, going back to the table view, or the chosen NPC leaving the scene drops it, and clears what was
shown, silently.

## What is not on a screen, and why

- Pronunciation, prosody and expression: sprint 083's milestone 2, not built.
- Mood and inflection cues: milestone 3, not built.
- Syncing a casting between devices: a casting names this device's engines, and device sync does not carry it.
- Engines with licensed or consented voices: the casting records rights and notes, but no such engine ships.
- A listening run with real people, to judge whether the voices can be told apart by ear: not done.
- Two phrasings of a draft side by side, and previews as one types: 085's milestones 2 and 3.
- The director's lore and player-character packs, and a combined turn's several drafts: not in 085's slice.

The Voices page lists the first five under **Not available yet** with `ui.unavailable`, and offers no control for
them.

## Tests

- `backend/tests/test_app_listen.py`, through `AppApi` over a synthetic card chat. The chat has a narrator (a bound
  scenario), Mara Vell and Tamsin Hale as NPCs and Anya Rook as the persona, with a tone engine installed by
  `voicecast.configure`. The tests cover:
  - a review writes nothing, and says new, replace, same and remove;
  - every refusal is the write's own, in the write's order, and writes nothing; a broken casting file is left alone,
    and a linked one is refused `link` by both and never followed;
  - a change made after the review refuses it `changed`, and the same write retried is not made again;
  - the plan names each voice and why, and the uncast persona stays text;
  - windows render in order, a casting change mid-play refuses the next window `changed`, and audio off is text;
  - a private message is never cued;
  - the preview shows the triggered lore and the recalled memory, an NPC that would not hear keeps its context, the
    persona is refused, and nothing is written, posted or sent; the draft is read as speech or as an action in the
    mode given, and an unknown mode is refused;
  - the page registers.
- `backend/tests/js/listen.e2e.mjs`, launched from the Python test against a real `AppServer`. It runs the proving
  scenario: Mara is cast through the Voices page's review (a consented voice is held back without its note); the
  plan shows each voice and why; play renders windows in order, the next asked for while the last cue of one plays;
  another screen recasts Tamsin mid-play, so the next window is refused, planned again and said, and play goes on
  from the next cue in the new voice; audio off reads as text; a draft naming a lore keyword is previewed for Mara
  in the director's view, showing the lore and the recalled memory, while the composer keeps the draft. Python then
  checks that the library gained no turn and no file other than the casting.
- `backend/tests/js/listen.test.mjs`, `preview.test.mjs` and `voices.test.mjs`: the modules' logic and behaviour
  under fakedom with a scripted app. They cover the fingerprint and window order, `changed` and play going on, a
  fork when planning and while playing, Stop and leaving, a sound not played, no audio element, one audio element
  started from the Play press, a plan made stale by a corrected line or an outcome, a preview sent trimmed in the
  composer's mode, a late preview answer dropped for a changed draft, mode or NPC, an NPC leaving the scene, and
  the Voices page: its targets, arguments and review sentences, a late review dropped for a changed form, the
  focus kept on a changed choice, a review that changes nothing, and a write whose answer was lost read back.

## Not proved here

- No real browser, screen reader or phone ran these pages. The `<audio>` element is a stand-in under fakedom, so
  real playback, autoplay rules and the gap between windows on slow synthesis are not measured.
- Whether Flite's voices can be told apart by ear (083 names this as its own open question).
- Layout at phone width is reviewed from the CSS (everything wraps, and the cue list scrolls inside the panel), not
  measured.
