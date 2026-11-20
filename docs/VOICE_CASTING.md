# Voice casting and character voice performance

**Status:** first useful slice of sprint 083 ([Character Voice Performance](roadmap/sprints/character-voice-performance.md)).
It is implemented as three parts:

- the pure package `grimoire.voicecast`, which holds the casting, the performance plan and rendering;
- the local engine `grimoire.voiceengines.flite`;
- the file adapter `grimoire.voicecaststore`.

The app's `voicecast` adapter ops (`backend/src/grimoire/app/adapters/voicecast.py`) put them together. A scene's
**accepted** text is read aloud, with each speaker in its own voice and every voice choice explained. The page
that plays it is sprint 116's: this slice exposes the typed API, which a client can already call.

This is not 014's *voice*. That is how an actor **writes** (docs/VOICE.md), and it reaches the model's prompt.
Nothing here reaches a prompt or a model. It is also not 061's speech recognition or the chat page's "Read aloud"
(docs/SPEECH.md). That reads one reply in the browser's own voice. This slice casts a whole scene.

## The casting

A casting binds a **speech voice** to a target. A speech voice is an engine, the engine's own name for the voice,
and its rights. There are three kinds of target:

| target | `ref` | who it covers |
|---|---|---|
| `narrator` | none; `campaign` names one campaign, or is null for every campaign | the narrator's lines |
| `character` | a definition `SourceRef`, unpinned (any revision) or pinned (`@r2`: that revision only) | every actor of that character |
| `actor` | a campaign `InstanceRef` | that one actor: the campaign's own choice |

Each casting holds one voice per target, and recasting a target replaces its voice. Engine and voice names are
`[a-z0-9][a-z0-9_.-]{0,63}`.

**Rights and consent are recorded with each entry.** A voice has one of three rights values:

- `engine`: a synthetic voice that ships with the engine.
- `licensed`: a voice under a licence.
- `consented`: a voice made from a person's recordings with their consent.

A `licensed` or `consented` voice needs a `note` (up to 500 characters) saying what was agreed and with whom.
Without one it is refused `rights_unrecorded`. Both values show wherever the voice is used.

A **new** voice is given only to a character, actor or campaign the library holds now (`no_character`,
`no_actor`, `no_campaign`). A voice whose engine or voice this device lacks can still be kept, and `read` shows it
as `available: false`.

### Files

The casting is `config/voices/casting.json` in the library's own `config` folder, which the native store reserves
and never reads. It works like the shelf (docs/SHELF.md):

- It is the person's configuration of how this device reads aloud: no source, campaign or chat record is written.
- A copy or backup of the library carries it. Device sync does not, because the voices it names are this device's
  engines.
- Every write names the etag it read (`expected`, or `null` for no file). A stale write is refused `changed`
  and writes nothing.
- A write is one temp file renamed over the target, and it never follows a link.
- A file that is not exactly a casting is reported, as `problem` and as a `casting_<reason>` plan problem, and is
  never written over (`unreadable_record`). Mend or remove that file first.
- An empty casting removes the file.

The writes are explored under the fault harness (`backend/tests/test_voicecaststore.py`).

## The plan: which voice speaks each cue

`voicecast.plan` reads a chat's turns as the play page opens them, adds each decided action's outcome line as the
table's transcript reads it, and makes one **cue** per item the table heard, in order:

- each non-private post of a turn, then a decided action's outcome line;
- then the kept take's shown lines, filtered as the table's transcript filters them (`table_lines`): on a staged
  take, only lines a player character heard.

The following are never cued:

- a private message;
- a whisper's text;
- a proposal or unkept take;
- a withdrawn or struck line;
- imported history.

**The text is never changed.** A cue carries the accepted `text` and its `digest` (sha256). It also carries a
derived `script`, which is what the voice says:

- In a character's line, `*stage directions*` are left unspoken. A direction never pairs across lines, and one never
  closed (an asterisk at the start of a line or after a space) runs to the end of its line. An asterisk inside a
  word (`5*3`) is only dropped.
- Narration is read with its asterisks dropped.
- An action post is text only (`action`), and so is a line that is all stage direction (`no_speech`).

Audio is never turned back into text, and pronunciation or prosody edits (sprint milestone 2) are not part of this
slice.

**A line keeps the voice of the revision it was spoken in.** A scene line pins two refs:

- `actor_revision`: the definition revision its actor spoke as;
- `voice_profile`: the voice profile it was written with. This is the revision's own profile or, for a derived
  portrayal with none of its own (an outfit, a disguise), the nearest base's (`generation/voice.py`).

The voice is chosen from those recorded refs, never from the source's head. Making a new revision changes no
earlier line's voice. (A card chat's opening greeting records neither ref, so it is voiced as a post is, below.) A pinned entry (`...@r2`) applies only to lines that recorded that exact revision, either as
the revision spoken or as the voice profile written with (so `wren@r1` also voices an outfit of Wren's written
with `wren@r1`'s profile).

For a character's line, the first rule that has a voice wins:

1. `actor`: the campaign's own choice for the actor.
2. `revision`: the line's recorded revision, exactly.
3. `character_any_revision`: that character at any revision.
4. `voice_profile`: the profile the line was written with, exactly, when it is another revision's.
5. `voice_profile_any_revision`: that profile's character at any revision.

So an outfit cast on its own speaks in its own voice, and an outfit nobody cast speaks in its base's voice.

A post records no revision of its own, and neither does a card chat's opening greeting. Their speaker is voiced
by the actor's own entry, then by the character the actor's campaign instance plays **now** (`revision` when the
instance pins one, then `character_any_revision`). So a post keeps its voice through an outfit switch only when the
character, or the actor, is cast at any revision; a post has no voice-profile fallback.

A decided action's outcome line, which the table reads after the action's posts, is a cue of kind `outcome`, read
by the narrator.

The narrator's rules are `narrator`, for this campaign, and then `narrator_every_campaign`.

A speaker nobody cast stays **text only** (`not_cast`). It never falls back to another speaker's voice.

Every cue lists each rule it `considered`, with the voice each one held, and answers `why` in a sentence. A
disputed choice can be traced to the entry that made it. The plan also lists any voice shared by more than one
speaker (`shared`), so a casting where two speakers sound alike can be seen and changed.

## Rendering

`voicecast.render` renders a window of cues: `start`, and `count` from 1 to 12 (default 6). For each voiced cue,
the cast voice's engine is called once:

- One cue is one call, so every speaker is its own synthesis.
- The cue's audio comes back as a base64 WAV, with its `start` and `duration`, laid end to end with a 0.3 s gap.
- `next` is where the following window starts, or `null` at the end.

A page plays one window while it asks for the next. One call covers at most 8000 characters of script, and starts
no new synthesis after 20 seconds. One cue's synthesis may take up to 30 seconds more, and asking an engine for its
voices up to 10, so a stuck engine holds the library about a minute at most.

`plan` and `render` answer the performance's `fingerprint`. This covers each cue's item, text digest, status and
voice, and each segment names its `turn` and `item`. A render that names a `fingerprint` is refused `changed` when
the scene or its casting has moved on since. A page then plans again, and never plays one window's cues out of
step with the next.

A chat whose turns fork is refused `turn_fork`. That happens when two devices each played the same turn number,
and its branches are not one scene.

A cue stays text, and the rest of the window still renders, in each of these cases:

| reason | what happened |
|---|---|
| `audio_off` | `audio: false` was asked: nothing is synthesized and the scene reads as text |
| `too_long` | the cue's script is over 4000 characters; it is never sent to an engine |
| `engine_unavailable` | this device has no such engine, or the engine has no voices now (an `ffmpeg` without Flite) |
| `voice_unavailable` | the engine does not have that voice |
| `synthesis_failed` | the engine failed; its message is not kept (it could quote the line) and the call is not retried |
| `invalid_audio` | the engine's answer is not a whole, bounded WAV |

Rendering never falls back to another engine or voice, and nothing that was rendered is kept. No op here writes a
turn or a source. Planning opens the chat as the play page does, so it may note 020's pending knowledge for an
accepted effect, as any open does.

### Engines

Engines are trusted configuration, never chosen by a request. Each engine reports `locality: local`,
`network: false` and `billing: free`.

The default engine is `flite`: `ffmpeg`'s built-in Flite voices (`kal`, `awb`, `rms`, `slt`...), which ship with
the engine and are cast with `rights: engine`.

- The line reaches `ffmpeg` as a file in a private temporary folder, named by a fixed relative name, so no part of
  the text is ever parsed as filter syntax. The folder is removed after each cue.
- An `ffmpeg` without Flite reports no voices, and every cue cast to it stays text. A voice list that was found is
  kept; an empty one is asked for again after five minutes, so a failed listing does not last and an `ffmpeg`
  without Flite is not run on every call.

`grimoire.app.adapters.voicecast.configure(api, synthesizers)` installs other local engines; tests use it to
install a tone engine. No provider, network or bill is involved anywhere in this slice. CI's `voice` job runs the Flite test, including in a
process with no network (`backend/tests/test_app_voicecast.py`).

## Ops

| op | arguments | answer |
|---|---|---|
| `voicecast.read` | none | `casting` (each entry with `describes` and `available`), `etag`, `problem`, `engines` with their voices, `targets`, `rights` |
| `voicecast.cast` | `target`, `ref` or `campaign`, `engine`, `voice`, `rights` (default `engine`), `note`, `expected` | the new `read` |
| `voicecast.uncast` | `target`, `ref` or `campaign`, `expected` | the new `read` (`not_cast` when nothing was cast there) |
| `voicecast.plan` | `chat` | `fingerprint`, `cues` (turn, item, text, digest, script, status, reason, voice, `considered`, `why`), `shared`, `engines`, `problems` |
| `voicecast.render` | `chat`, `start`, `count`, `audio`, `fingerprint` | `fingerprint`, `segments` (cue, turn, item, speaker, text, digest, voice, status, reason, start, duration, audio), `total`, `next`, `problems` |

`cast` and `uncast` are refused `read_only` on a library opened read-only.

## Not yet

The following are left for later:

- Author-controlled pronunciation, prosody and expression (sprint milestone 2).
- Mood and inflection cues (milestone 3).
- Splitting a cue longer than 4000 characters into several syntheses.
- Syncing a casting between devices.
- Engines with licensed or consented voices: the casting records their rights, but this slice ships no such engine.
- The page that plays a scene, which is sprint 116's.
- A run with real speakers and listeners, to judge whether the voices can be told apart by ear.
- Recording `actor_revision` on a card chat's opening greeting (the runner's change), so the greeting keeps the
  voice of the revision it was spoken in rather than following the instance's revision now.
