# Spoken and typed intent routing

**Status:** first slice of [Spoken Command And Intent](roadmap/sprints/spoken-command-and-intent.md)
(Wave 1, #008). The `grimoire.intent` package is a **headless, pure router**. It doesn't capture
audio, run speech recognition, show UI or execute anything. Typed text and fake transcriptions
stand in for speech until a voice front end exists.

## What it decides

A player's input goes into one of four lanes, and the router returns a **hypothesis**, never an
action:

| Kind | Meaning | Who acts on it |
|---|---|---|
| `ui_command` | Control the app ("Open the inventory") | Caller, through the same authorization path a click uses |
| `pc_speech` | What the player's character says | Scene runner, under player authority |
| `pc_action` | What the player's character does ("I open the chest") | Scene runner / adjudication |
| `director_instruction` | Out-of-character direction ("Director, make this tense") | Director tooling |
| `uncertain` | Not sure: nothing is done | UI may show `candidates` |
| `reject` | Not allowed or empty: nothing is done | UI shows why |

Every intent carries a stable `reason` code and a short `feedback` string naming its lane
("Command: …", "In character (speech): …", "Director: …", "Not sure what you meant — nothing was
done", "Ignored: …"), so mode errors are visible.

```python
from grimoire.intent import RoutingContext, Utterance, route

context = RoutingContext(active_pc="pc-1", can_direct=True)   # authority is never defaulted
route(Utterance("I open the chest"), context)                  # pc_action
route(Utterance("Delete the scene"), context).command.gate     # "confirm"
```

## Input modes

`Utterance.mode` is `auto`, `command`, `speech`, `action` or `director`. **An explicit mode
always beats wording:** speech mode "Delete the scene" is dialogue. Command mode never falls back
to speech; an unrecognised command is `uncertain`. In an explicit mode, a leading *separated*
marker for another lane ("Grimoire, pause" in speech mode) is `uncertain` (`lane_conflict`)
instead of silently becoming dialogue; unseparated words ("Director Vance, you're late") are just
words.

## Auto mode rules (first match wins)

1. Empty → `reject`.
2. Whole text in double quotes → speech; whole text in `*…*` → action. Quoting is the escape
   hatch, so it's checked before any marker.
3. **Mixed lanes** → `uncertain`: a non-leading `Grimoire` followed by `,`/`:` or a command verb;
   a later sentence starting with a separated marker of a different lane; or a later sentence
   that is a different full command ("Hello there. Delete the scene.").
4. Leading `Grimoire` (with or without punctuation) → command; unknown remainder is `uncertain`.
5. The same command repeated ("stop. stop!", "stop stop") → that command.
6. `Director,` / `GM:` / `Narrator,` / `OOC:` / `((…))` → director instruction.
   Without the separator ("director make this tense", common in speech recognition) →
   `uncertain` with director and speech candidates.
7. Starts with `I`, `I'm`, `I'll`, `I've`, `I'd` → action.
8. A whole catalog command → command.
9. "Could you / can you / would you / will you / let's" + command → `uncertain` (it may be said
   to an NPC), offering the command and speech.
10. Starts with a command verb but isn't a known command ("Delete everything", "option 12") →
    `uncertain`.
11. Anything else → speech.

Then authority: PC lanes need `active_pc`; director lanes need `can_direct`; otherwise `reject`.

## Commands and gates

The gate belongs to the catalog (`COMMANDS`), never to the modality. `Command.gate` is a
derived property, so a caller can't construct a weaker one.

| Command | Gate | Phrases |
|---|---|---|
| `navigate` | instant | open/show/go to/navigate to [the/my] inventory, map, journal, settings, scenes (scene list), characters (character list) |
| `open_character` | instant | open/show [the] character NAME; open/show NAME's sheet |
| `pause` | instant | pause |
| `resume` | instant | resume, continue, unpause |
| `stop` | instant | stop, stop audio/talking/generating, be quiet |
| `cancel` | instant | cancel, never mind |
| `review` | instant | open/show [the] review [queue], review changes |
| `undo` | authorized | undo [that] |
| `reroll` | authorized | reroll, regenerate, try again |
| `choose_option` | authorized | choose/pick/select [the] option N, option N, [choose] the Nth option (1–9) |
| `edit_draft` | authorized | edit [the] draft, edit my reply |
| `delete_scene` | confirm | delete/remove/erase [the/this] scene |

- **instant**: view or playback only; never discards user-authored text. `stop` halts audio and
  in-progress generation (partial generated text stays a discardable proposal). `cancel`
  dismisses a prompt, dialog or pending confirmation, not drafts.
- **authorized**: changes draft or game state; the caller applies the same authorization a
  mouse or keyboard action would.
- **confirm**: destructive; needs the confirmation step below, then the authorized path.

## Voice uncertainty

For `origin="voice"`, the top transcript and each alternative are routed the same way.

1. A `reject` stays a reject.
2. `stop`, `pause` and `cancel` go through at any confidence and despite alternatives. Acting
   on a mishearing only halts something.
3. If any alternative reads differently, the result is `uncertain` (`asr_disagreement`). That
   covers a different lane or command, or different words in a fiction/director line ("I kill
   the king" vs "I kiss the king"). Case and punctuation differences don't count.
4. Unknown confidence, or confidence below `min_confidence` (default 0.75), gives `uncertain`
   (`low_confidence`).

Callers should only pass alternatives their recogniser considers plausible.

## Candidates

`uncertain` candidates are **information, not actions**. Choosing one is a fresh input. Every
candidate's `text` re-routes to the same reading in its explicit mode: `command` for commands,
`speech`/`action`/`director` for the others. A destructive command is **never** a candidate. An
uncertain "delete the scene" must be said or clicked again unambiguously.

## Confirmation

```python
pending = request_confirmation(intent, target="scene:s1@rev7", now=now)        # ttl 30 s
result = resolve_confirmation(pending, Utterance("yes"), now=later,
                              current_target="scene:s1@rev7", context=context)
```

The caller holds `pending`, and each resolution consumes it. Checks run in this order:

1. Expired (or not yet issued) → `expired`.
2. A different target or revision → `stale`.
3. Speech, action or director mode → `unclear`.
4. "no", "cancel", "never mind", "stop" or "no thanks" → `declined`.
5. "yes", "confirm", "do it" or "yes, delete it" → `confirmed`. For voice, this also needs
   confidence at or above the threshold, and every alternative must be a yes.
6. Anything else → `unclear`.

Only `confirmed` returns the command and the target. The caller must execute against that exact
target.

## Known limitations

- English only, fixed grammar. No model-based inference: classification is free and
  deterministic.
- One speaker; who may confirm in multi-speaker sessions is open.
- Mixed utterances are detected, not split ("I open the chest. Grimoire, pause." is `uncertain`).
- Auto-mode misreads, each fixed by quoting or by using an explicit mode:
  - "Stop!" as dialogue reads as the app `stop`.
  - "Continue" reads as resume.
  - "I love you, Mara" reads as an action.
  - "Director, you're late." typed with a comma reads as direction.
  - "I read the grimoire, open on the table" reads as mixed.
- Some repeats are deliberately conservative:
  - "Grimoire, pause. Grimoire, pause." is `uncertain` (mixed-lane detection runs first).
  - "Delete the scene. Delete the scene." is one `delete_scene` that still needs confirmation.
- Abbreviations aren't special-cased when splitting sentences ("Mr. Grimoire, …" may read as
  mixed, which is safe).
