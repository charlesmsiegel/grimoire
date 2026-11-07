# Diegetic communication: who a message in the fiction reaches

**Status:** first slice of [Diegetic Communication](roadmap/sprints/diegetic-communication.md) (Sprint 032, Wave 4). It is pure, provider-free code in `grimoire.comms`:
- `model.py`: the scene's present cast, a message and its delivery;
- `transmit.py`: `transmit`, which turns one in-scene message into [012](KNOWLEDGE.md) records;
- `reader.py`: `read_message`, which reads a routed [008](intent-routing.md) PC intent as a message, or refuses.

Nothing here writes a file or calls a model. The records go to [020's](EVIDENCE.md) `EvidenceLibrary.append` unchanged. There is no app: 035's runner ([SCENES](SCENES.md)) reads posts and stores each message's audience on its turn; writing these records to 020 in the turn's commit is its milestone 2. All scenarios are synthetic.

## What it does

**Proving scenario** (`backend/tests/test_comms_scenarios.py`). The player speaks into a microphone: "I tell Emma telepathically the guards are listening". Ines, Emma and two guards are in the hall, and a spy is off stage.
- 008 routes the utterance to a `pc_action` for Ines.
- `read_message` reads it as a telepathic message from Ines to Emma, "the guards are listening".
- `transmit` makes one proposition and one report, held by Emma.
- 020 stores them. After a reopen:
  - Emma holds the message, received telepathically and attributed to Ines;
  - Ines recalls sending it (her `sent` bucket);
  - the guards and the spy hold nothing, and no stored file names a guard;
  - the proposition's standing is `reported`, never established.
- "Grimoire, open settings", spoken the same way, is a UI command. It is never dialogue, and nothing is written.

The sprint's three questions get these first-slice answers:
- **Unmarked dialogue** is heard by everyone present.
- **Receipt is certain** only for a present receiver on a channel that reaches them. In this slice that is every receiver, so reports are `confirmed`. Imperfect receipt is milestone 2.
- **Private channels never reach the table's transcript here**: nothing in this slice renders one. Redacting a transcript view is its renderer's (035, 065).

## Values

`Presence(campaign_id, scene_id, present, telepaths=())` is one scene's cast **as the director has accepted it**.
- `present` is at least one actor. `telepaths` is the subset that may *send* telepathically.
- Both are distinct exact `InstanceRef`s of the campaign, sorted by reference text, so equal casts compare equal.
- `scene_id` is a label of at most 200 characters; it becomes the moments' `scene_id`.
- Presence grants hearing what is said in the scene from now on. It never grants anything said earlier.
- Any instance kind may be present, as 012 lets any actor hold a report (a sentient sword can be told something). Only characters can *believe* it ([012](KNOWLEDGE.md#belief-authorship-player-sovereignty)).
- `telepaths` lives on `Presence` for now: who may send telepathically in this scene. When 025 abilities decide it (milestone 2), this field may move.
- Owners: [Party and Presence](PARTY.md) (033) owns the present cast. A runner (035) builds `present` from 033's staged `SceneCast`, so a reserved or accompanying member never hears a scene, and, when the chat has a 041 stage, from the stage's `presence` for each message, so a whisper never passes a shut door in play ([SCENES](SCENES.md#the-stage-041)); this slice does not import 033; [fictional abilities](ABILITIES.md) (025) will decide who is telepathic (milestone 2).

`Message(message_id, sender, channel, text, addressees=(), about=())` is one thing a sender communicates.
- `message_id` is an id segment of at most `MESSAGE_ID_MAX` (104) characters. It becomes the proposition's id and the prefix of each report's id, so the longest evidence id plus 020's `.json` still fits a store slot (tested by storing it). As for every stored file, a whole path may still exceed a system's limit, such as Windows' 260 characters, when the library lives deep in the file system; 020 then refuses the write.
- `channel` is `speech`, `whisper` or `telepathy`.
- `addressees` empty means the whole room, which only speech can reach.
- `text` is at most 20 000 characters (020's limit).
- `about` follows 012's rule: only what the text overtly refers to. A reader of player text cannot know it and leaves it empty.

`Transmission(message, proposition, reports)` is the result. It is **director-side**, since it names every receiver: hand only `records()` to 020. `receivers` lists the holders.

## Channels

| Channel | Who receives | Report channel | Addressed |
|---|---|---|---|
| `speech`, no addressees | everyone present but the sender | `told` | yes |
| `speech` to addressees | the addressees | `told` | yes |
|  | every other present actor | `overheard` | no |
| `whisper` | the addressees only | `whispered` | yes |
| `telepathy` | the addressees only; the sender must be a telepath | `telepathic` | yes |

Every transmission fits one 020 append, and every text and label it holds is storable.

Every report is `confirmed`, names the sender as `sender` and `attributed_to`, and is received at the send moment. An addressed report's `sent_at` is that moment too, so the sender's `sent` bucket recalls it. An overheard report is unaddressed and has no `sent_at`: the sender did not knowingly tell the bystander.

A **bystander of a whisper or telepathy gets no report**. So it has no evidence, and nothing about it enters anyone's `sent`. That is the slice's core guarantee.

## `transmit`

`transmit(message, presence, *, sequence, provenance, turn_id=None)` delivers the message at campaign `sequence` in the presence's scene. It returns:
- a new `Proposition`, with id `message_id`, the message's text and `about`;
- one `Report` per receiver, sorted by holder.

Evidence ids are `<message_id>.r-<16 hex>`: the first 16 hex digits of SHA-256 over the holder's reference text (`evidence_id`). They are deterministic and neutral. A receiver's id says nothing about how many others received the message.

It does **not** check who may speak for the sender. Player sovereignty ([003](PLAYER_SOVEREIGNTY.md)) and the runner's executor (035) do that. `transmit` decides only who receives.

**Refusals.** Each raises `TransmissionError(ValueError)` with a `.reason`, carries no text, and delivers nothing.

| reason | when |
|---|---|
| `invalid_record` | a malformed value: not a `Message`/`Presence`, a bad id, empty text, a repeated actor, a bad sequence, provenance or turn label, or text UTF-8 cannot hold |
| `campaign_mismatch` | an actor, or an instance subject in `about`, of another campaign |
| `unknown_channel` | a channel this slice does not know (letters and radio are milestone 2) |
| `too_long` | text over 20 000 characters |
| `telepath_absent` | a telepath that is not present |
| `sender_absent` / `addressee_absent` | the sender, or an addressee, is not present |
| `self_addressed` | the sender is among the addressees |
| `no_addressee` | a whisper or telepathy with no addressee |
| `not_telepathic` | a telepathic sender the scene does not list |
| `no_audience` | speech to the room with no one else present |
| `too_many_receivers` | more than `MAX_RECEIVERS` (499) receivers: the proposition and every report must fit one 020 append of at most 500 records |

## `read_message`

`read_message(intent, sender, names, *, message_id)` reads a routed PC intent as `sender`'s message, or returns `Unread(reason)`.

- It sees the `Intent` only, **never the utterance's origin**. Real spoken input to the app therefore never declares an in-world broadcast: a voice and a keyboard utterance read the same (tested).
- Every check runs twice: on the text as typed, and on its compatibility fold (Unicode NFKC, with invisible format and default-ignorable characters such as a soft hyphen or combining grapheme joiner dropped), so full-width brackets and letters ("（whispers）", "Ｗｈｉｓｐｅｒｉｎｇ") hide nothing. Both readings must succeed and agree on channel and addressee; the words kept are the ones typed. A bracket or quote mark of any script (Unicode Ps, Pe, Pi, Pf), a symbol such as an emoji (So) and asterisk look-alikes count as stage-direction marks. The fold does not map look-alike letters of other scripts (a Cyrillic "е" in "whisper").
- Every lane but `pc_speech` and `pc_action` is `not_dialogue`. So a UI command or a director instruction is never a message.
- The intent's actor must be the sender's `instance_id` (`wrong_speaker`).
- `names` maps display names to the actors they mean. A name is at most 200 characters (`invalid_record` otherwise). Matching ignores case (Unicode case folding, also where folding changes length: "STRASSE" is the name "Straße") and compatibility forms: a name and the text are both compared in NFKC, so "E\u0301mma" and "Émma" are one name and needs a word boundary after the name. It never guesses:
  - two actors fitting one name (including "Emma" inside "Emma Rose") is `ambiguous_addressee`;
  - a name followed by another capitalised word ("Emma Rose" when only Emma is known) is `unknown_addressee`;
  - in an action, words after the name need a `,`, `:`, manner word, `that` or a quote first. Otherwise the name may go on ("the first guard captain" when only "the first guard" is known, or "Emma again …"), and the line is `unknown_addressee` (or `unclear_content` when the words start like manner or punctuation).
- A name ends where a word ends, so "Emma)", "Emma—", "Emma…" and "Emma's" all contain the name "Emma".

**The asymmetry.** A private line misread as speech reaches the whole room, and 020's records cannot be taken back. A refusal costs the player a rephrase. So the reader produces speech only where 008 already decided the text is spoken words, and refuses whatever looks like narration.

**Speech** comes only from `pc_speech`, said to the room (no addressees).
- A line 008 read as wholly quoted (reason `quoted`), or a line wholly in double quotes, is taken as its words, spoken aloud, even if they mention whispering ("I'll whisper it to you later."). A re-entrant voice candidate that kept its quotes loses them. Quoted words may not hold a stage direction (a bracket, asterisk, underscore, tilde, angle bracket, `@`, arrow or guillemet, or a single quote without a letter on both sides, which opens or closes inner words as in `"Run,' I whisper to Emma, 'now"` or `"Run,'I whisper …"`; so an apostrophe inside quoted words must sit inside a word, "don't" but not "listenin'": `"(whispers) Emma, …"`, `"*leans close* …"`): `mixed_narration`. Single quotes are also apostrophes ("'Cause … listenin'"), so a line wholly in single quotes loses them and gets every unquoted rule below (`'Run, Emma, I murmur'` is refused).
- Otherwise the line is refused when it shows narration:
  - quotes mixed with other text, such as `"Run," I tell Emma` or `'Run,' I tell Emma`: `mixed_narration`;
  - a bracket, asterisk, underscore, tilde or angle bracket, the usual marks of a stage direction, such as "(low) Emma, run", "[to Emma] run", "\*sidles up to Emma\* run" or "\_shrugs\_ run": `mixed_narration` (or `unresolved_private` when the direction holds a marker);
  - a chat convention or header: a leading `/` ("/w Emma …"), an `@`, an arrow (`->`, `=>`, `>>`), a guillemet, or a colon not inside a clock time ("Listen: …" is refused too): `mixed_narration`;
  - a speech tag, a speech verb before or after "I", he, she, they, we or "my character" ("Run, I tell Emma.", "Run, my character tells Emma.", "…, I sign."): `mixed_narration`. A line ending in a tag of one to three words with any verb ("Run, Emma, I gasp quickly.") is refused the same way. Innocent words such as "I say we go" are refused too;
  - **a known name anywhere but as a vocative**: `mixed_narration`. A name said aloud stands at the start or after `,` `.` `!` `?` `;`, and before one of `,` `.` `!` `?` or the end ("Emma, run", "Good evening, Emma."). Any other place is narration: "Ines slips Emma a note …", "Run, says Ines.", "Run, I think at Emma", "Run, I send to Emma's", "Thank you Emma". Names are found in one pass over the line, however many there are;
  - **a short aside that says how or where**: `unresolved_private`. A piece of up to four words set off by `,` `;` `.` `!` or `?` that starts with a preposition (in, on, under, by, with, via, behind, off, without, through, for, to and the like), or a lone word ending in "ly", is refused by its shape, since its words vary without end: "Emma, in silence, …", "Emma, by sending spell, …", "Emma, magically, …". So are innocent ones ("Emma, in the morning, we leave");
  - a privacy marker anywhere, with `&` and `+` read as "and" ("between you & me"): `unresolved_private`. The markers are broad word families, not phrases, and an underscore around one does not hide it: whisper, telepathy, psychic, mentally, mind to mind, in (or into, for, to) someone's head, mind or thoughts, any word holding "hear" (hear, overhear, hearing, "don't hear"), ear or ears, earshot, any word holding "voice" and tone, low, lower or lowering, quiet, soft, breath or breathe, silent, hushed, undertone, sotto voce, privately, secretly, discreetly, in confidence, aside, alone, "no one else" or "nobody else", murmur, mutter, mouth, leaning in, psst, "between us" or "between you and me", a known name followed by "only", a link or bond, eyes, "just us", "only you", "you only", "for you", "between you and I" or "between me and you", "just for us", "can't tell", "won't tell", "don't mention", "nobody (needs to) know", "not a soul", conspiratorially, surreptitiously, faintly, stepping away, "out of sight", "only to her", keep mum, discretion, hiding it, "can't know", "find out", notice, "catch this", cupping, sidling, "don't tell", "tell no one", "not a word", confidential, under wraps, "mum's the word", "not so loud", "come closer", leaning toward, shh, ssh, shush, pst, "keep it down", "no one can know", "don't let", "don't repeat" and the like, "keep this from", "my lips are sealed", "my back to", turning away, "a word?", link, bond, confide, mumble, sub rosa, entre nous, thought, a note, letter or writing, signal, gesture, wink or nod, "saying", "meaning" or "reads", any word starting "priva" (private, privacy) or "secre" (secret, secrecy), discreet, clandestine, stealth, sneaking, hidden, intimate, silence, mute, mic, code, encrypted, cipher, privy, personal, exclusive, incognito, undercover, unseen, one-on-one, tête-à-tête, sending, spell, magic, dream, sign or signing, password, solitude, seclusion, isolation, on the sly, under the radar, sidelong, sideways, "just you", "but you", "you alone", closed doors, audible or inaudible, shh, covert, furtive, hiss, off the record, "to yourself", comms, radio, earpiece, channel, sign language, leaning over or in, drawing someone close, projecting one's thoughts, a line that starts "Message" or "Text", and DM, PM or msg. Innocent words in these families ("Did you hear?", "the river is low", "my heart") are refused too.
- The marker list is a list, not understanding. Other narration typed in the speech lane that names no known actor and holds no marker is spoken words, as 008 decided, and innocent speech holding a marker ("Take a breath") is refused.

**An action is read only as a private message**, in one first-person form:

```text
I [telepathically|mentally] (whisper to|tell|say to) <name>[,|:] [telepathically|mentally][,|:]
  (that <content> | "<content>" | <content>)
```

- `whisper to` makes a whisper, and a manner word makes telepathy. Both together is `conflicting_channel`. A bare "whisper" names no listener ("I whisper the guard is asleep" is not a whisper to the guard), so it is not the form.
- A told or said action with no manner word is `indirect_speech`. An action is never read as speech the room hears, however plain ("I tell Emma that dinner is ready"); the player puts the words in quotes instead. This is what keeps "I tell Emma, out of earshot of the guards, that …" or "I tell Emma, mind to mind, …" from reaching the guards.
- The words must read as words (`unclear_content`):
  - bare content may not start like manner or an instruction ("in a low voice …", "silently …", "to run"), and may not hold ", that " (a manner phrase before the words);
  - content may not start with punctuation ("I whisper to Emma. The guards …");
  - a quote may not be followed by more narration, and quoted words may not hold a stage direction (`"*winks* the guards …"`);
  - unquoted content (after `that` or bare) must be one plain clause: one sentence with no comma, semicolon, colon (a clock time aside), dash (including "--"), ellipsis (including "..."), underscore, tilde, bracket, asterisk, double quote, `+`, `&`, `=`, `^`, `%`, `#`, "then", "without" or "while", no privacy marker from the speech list, no "to", "tell" or "say" after a join (and, &, plus, then, but, also, or, nor, yet, so, while, though, however, instead and the like: a second listener, "… and to the guards we stay"), no third-person pronoun (he, him, his, she, her, they, them, their and the like, since "that I love her" reports words rather than giving them), and no second first-person clause (after "and", "then", "&", "+", or a mark such as ";" or "!!" even with no space). So "… listening. Then I shout …", "…, then shout it to the first guard", "run \*wink\*", "we run; she nods", "…, glancing at the door", "… so the guards don't hear", "… without looking at Emma", "that I love her" and "… and I follow" are refused. Anything longer goes in quotes, which may hold any punctuation.
- One addressee only. "Emma and the guard", "Emma plus Spy", "Emma as well as Spy", or bare content that starts with another known name ("Emma, Spy and I must run"), is `ambiguous_addressee`; build a `Message` directly for several. A known name anywhere in unquoted content is `ambiguous_addressee` too ("I whisper to Emma that we run and to the first guard that we stay" would hand Emma the guard's line), and a second "that" is `unclear_content`. Quote the words to say a name.
- Any other action is `not_directed` ("I draw my sword"), unless it holds a privacy marker, which is `unresolved_private` ("I mouth to Emma that …").

| `Unread` reason | meaning |
|---|---|
| `not_dialogue` | not a PC lane |
| `wrong_speaker` | the intent is another actor's |
| `not_directed` | an action with no message form and no marker |
| `indirect_speech` | a told or said action with no private channel |
| `unresolved_private` | a privacy marker in a line that is not the private form |
| `mixed_narration` | spoken words with narration in or around them |
| `unknown_addressee` / `ambiguous_addressee` | no actor, or more than one, fits the name |
| `conflicting_channel` | both whisper and telepathy |
| `unclear_content` | the words cannot be told from manner, instruction or narration |
| `no_content` | nothing to say |

**An `Unread` transmits nothing. A caller must never fall back to speech on one.**

Malformed arguments raise `TransmissionError`:
- `invalid_record`: not an `Intent`; a sender or name value that is not an `InstanceRef`; a blank name; a bad `message_id` (checked first, on every path); text UTF-8 cannot hold;
- `campaign_mismatch`: a name for an actor of another campaign;
- `too_long`: an intent text over 20 000 characters, checked before any parsing so a long paste costs nothing.

## Persistence and trust boundaries

`EvidenceLibrary.append(campaign, transmission.records())` stores a transmission ([EVIDENCE](EVIDENCE.md#append)). 020 then enforces what this slice does not:
- every receiver and the sender must be stored actors (`unknown_actor`);
- a reused `message_id` is `duplicate_id`;
- the sequence may not be backdated.

This follows [012's obligations for 032](KNOWLEDGE.md#consumer-obligations):
- reports carry an honest `sender`, `addressed`, `sent_at`, `attributed_to` and `confirmed`;
- a sender is named only when that actor really said it.

`Presence` is the caller's claim. A runner must build it from the accepted scene state, never from narration.

## Known limits and owners

- **Milestone 2:** languages and who understands, range, barriers and blocked channels (041's first slice builds a `Presence` from a scene stage, so a whisper never passes a shut door, and its `deliver` makes room speech heard through an open door `overheard`: [STAGING](STAGING.md#delivering-a-message-within-earshot-032)), imperfect or unconfirmed receipt, anonymous senders, and setting-specific channels such as letters, radio, sign and text messages. Who is telepathic should come from 025 abilities.
- **Milestone 3:** eavesdropping and interception, historical and multimodal messages, and coded messages.
- Unquoted private content is a list of refusals, like the speech markers. Narration with no punctuation, no listed word, no known name and no second "that", such as a subjectless second verb joined by a bare "and" ("I whisper to Emma: run and smile") or a trailing manner phrase ("… and smile"), cannot be told from words and is read as words. It reaches only the one addressee and names no other actor, but that addressee's report holds those words as the sender's; quotes make the words exact. A caller that needs exact words can show them to the player before it commits the turn.
- The reader is **English-only** and reads one sentence pattern. Non-English text that 008 routes as speech is taken as spoken words, as 008 decides. Addressed speech ("I tell Emma …") is refused rather than read; a later slice may read it once speech-tag parsing exists.
- Quoted words may hold a dash aside ("Run — no, wait — now"), read as a spoken pause, not a stage direction.
- Thought is interiority, a player-sovereignty domain ([003](PLAYER_SOVEREIGNTY.md)), and is never transmitted: "I think …" is `not_directed`.
- Rendering a transcript without private channels: 035's first slice keeps them out of the director's table lines and every listener's window ([SCENES](SCENES.md)); a UI view is 065's.
