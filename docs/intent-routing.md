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
2. Whole text in double quotes → speech; whole text in `*…*` → action (empty `""`, `**`, `(())`
   are `reject`/`empty`). Quoting is the escape
   hatch, so it's checked before any marker.
3. **Mixed lanes** → `uncertain`: a non-leading `Grimoire` followed by `,`/`:` or a command verb;
   a later sentence starting with a marker of a different lane, with or without punctuation
   ("I wave. Director make it rain."); or a later sentence
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
   the king" vs "I kiss the king", or "Please kill the king" vs "Kill the king"). Case and
   punctuation differences don't count.
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
pending = request_confirmation(intent, target="scene:s1", revision="rev7", now=now)  # ttl 30 s
result = resolve_confirmation(pending, Utterance("yes"), now=later, current_target="scene:s1",
                              current_revision="rev7", context=context)
```

`resolve_confirmation` is a **pure check**: it consumes nothing and does not know who is speaking.
The caller holds `pending` and discards it after any terminal result (`confirmed`, `declined`,
`expired`, `stale`). Single use comes from the executor: it acts on the exact target and revision a
`confirmed` result names and advances that revision, so a replayed "yes" is then `stale`. Checks
run in this order:

1. Expired (or not yet issued) → `expired`.
2. A different target or revision → `stale`.
3. Speech, action or director mode → `unclear`.
4. "no", "cancel", "never mind", "stop" or "no thanks" → `declined`.
5. "yes", "confirm", "yes, confirm", "do it", "yes, do it" or "yes, delete it" → `confirmed`. A questioning "Yes?" isn't
   consent and gives `unclear`. For voice, a yes also needs confidence at or above the
   threshold, and every alternative must be a plain (non-questioning) yes.
6. Anything else → `unclear`.

Only `confirmed` returns the command, target and revision. The caller must execute against that
exact target revision.

**A confirmation is not an authorization.** It names no principal, grant or approval, and the
router has no permission input: `RoutingContext` gates only the PC and director lanes, and a
command's `gate` is a catalog label. An executor must authorize every `ui_command` exactly as it
would the same click, through the application's permission path (see
[Tool authority](TOOL_AUTHORITY.md)), and a `confirm` command additionally needs this
confirmation. Binding a confirmation to the speaking user or session is not implemented; it needs a
speaker identity the router does not have (owners: the first executor, [first text
runner](roadmap/sprints/minimal-text-scene-runner.md); multi-speaker rules,
[multi-speaker table audio](roadmap/sprints/multi-speaker-table-audio.md)).

## Handing a PC intent to the authority policy

Routing classifies text; it does not decide who may speak for a PC. `RoutingContext.active_pc`
is the caller's claim, and an `Intent` names no speaker. A caller turns a PC or director intent into
a [player-sovereignty](PLAYER_SOVEREIGNTY.md) `PlayerContribution` with the **real submitter** and
this kind, and `submit_contribution` then refuses anyone who does not control that actor:

| Intent kind | Contribution kind |
|---|---|
| `pc_speech` | `speech` |
| `pc_action` | `action` |
| `director_instruction` | `directorial_request` |

No lane yields an `interiority` or `commitment` contribution: "I promise to return at dawn" routes
to `pc_action`, so a narration claim that the PC committed to something is refused rather than
authorized. Actor ids here are campaign-local segments (`CampaignActor.actor_id`), not reference
texts. Pinned by `backend/tests/test_wave1_integration.py`; owners: the first text runner (turn
submission) and [Local multi-PC](roadmap/sprints/local-multi-pc.md) (who is speaking).

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
- Only `Director,`/`GM:`/`Narrator,`/`OOC:` and `((…))` mark direction: "(OOC: …)" and
  "[OOC] …" read as speech. Use director mode for them.
- A line break is not a sentence break: "Hello there⏎Delete the scene" is one line of speech.
- Only one leading "please" is ignored: "please please delete the scene" reads as speech.
- Some repeats are deliberately conservative:
  - "Grimoire, pause. Grimoire, pause." is `uncertain` (mixed-lane detection runs first).
  - "Delete the scene. Delete the scene." is one `delete_scene` that still needs confirmation.
- Abbreviations aren't special-cased when splitting sentences ("Mr. Grimoire, …" may read as
  mixed, which is safe).
