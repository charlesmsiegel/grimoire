# A campaign's chronicle (115)

The **Chronicle** page (`#/chronicle/<campaign id>?chat=<ChatRef>&actor=<ActorRef>&audience=director`) is Wave
8's UI exit for the features that a person can already use: a campaign's clock and deadlines (055) and what each
character remembers, with the evidence each account rests on (053). It also lists every other user-facing first
slice of Wave 8, and either links the screen it is on or says plainly that it is not on a screen yet. A
campaign's hub (113) links to it, for the same chat and view.

The page is served by the `chronicle` adapter (`grimoire.app.adapters.chronicle`, owner 115). Like every adapter,
it works only over the existing domain services: `CalendarLibrary` for the clock and `MemoryLibrary` for memory,
with the evidence a character may cite taken from the campaign hub's own `knowledge` perspective. It keeps no
store of its own.

## The clock (055)

The clock is the director's. In the table's view the page says so and offers nothing. Every op refuses
`director_only` unless `audience` is `director`.

| Op | Args | Answer |
|---|---|---|
| `chronicle.clock` | `campaign`, `audience` | `{campaign, etag, started, calendar, now, deadlines[], operations}` |
| `chronicle.clock_plan` | the change, `operation_id`, `expected` (the etag that was shown) | `{plan: {operation, already_applied, before, after, flags[], files}, clock}`; nothing is written |
| `chronicle.clock_commit` | the same args as the plan | `{done, clock}` |

A change is `initialize` (`provider` gregorian or hebrew, `date`, `minute`), `schedule` (`deadline`, `label`,
`kind` deadline/reminder/trigger, `date`, `minute`) or `advance` (`minutes`, at least 1). The page shows the
plan's sentence, the deadlines it would flag and the files it would write, then makes the change only when the
person presses **Make this change**. The commit plans the change again with the same operation id and etag:

- a clock changed by someone else since the review refuses `changed`, and the page reloads it;
- the same change made again is `already_applied` and writes nothing;
- 055's own refusals keep their reason: `past_deadline`, `operation_mismatch` (an id reused for another change),
  `uninitialized`, `scope_conflict`.

A flagged deadline is only a flag: the page says that nothing happens by itself.

## Memory (053)

`chronicle.memory` (`chat`, `actor`, `audience`) answers one character's dossier: its standing accounts by topic,
each with its status (current, contested, replaced, resting on corrected evidence, older than what it would
replace, or resting on evidence no longer held), the evidence it cites and its history; and the evidence the
person may cite for that character, newest first.

`chronicle.remember` (`chat`, `actor`, `audience`, `operation_id`, `topic`, `text`, `evidence[]`,
`supersedes`) writes one account.

- An NPC's memory is the director's: its key writes it, and only in the director's view (`director_only`
  otherwise). A player character's memory is its present controller's, written with the owner's own handle;
  any other character refuses `not_your_character`, and one outside the chat's cast `no_actor`.
- An account cites only evidence the hub shows for that character from that view (`unavailable_evidence`
  otherwise), so the page can never write one that rests on what the character did not perceive.
- It is recorded as of the newest evidence shown for that character, so an account resting on older evidence
  than the last one is still accepted; its basis is the evidence it cites. The same operation id again changes
  nothing; reused for another account it refuses `operation_conflict`. An account replaces only a current one of
  the same topic.
- An unsent account is kept per editor and character, so a character handed to another controller never shows
  the last one's draft. The page draws only the character chosen last, whatever answer comes back late.
- The scene runner already packs an NPC's standing accounts into its context ("You remember: ..."), so what the
  director writes here is what the NPC plays from.

## Wave 8's user-facing first slices

`chronicle.coverage` answers this table, and the page shows it. Slices with no user-facing behavior (066, 068,
069, 071) need no UI and are not listed.

| Slice | Feature | Where | Integration test | When it is not there |
|---|---|---|---|---|
| 053 | Sourced memory | Chronicle | `test_app_chronicle.py`, `js/chronicle.e2e.mjs` | an NPC's memory in the table's view refuses `director_only`; evidence not shown refuses `unavailable_evidence` |
| 055 | Campaign clock and deadlines | Chronicle | `test_app_chronicle.py`, `js/chronicle.e2e.mjs` | the table sees that the clock is the director's; a stale review refuses `changed` |
| 061 | Private local voice | `voice` | `test_app_voice.py` | no recognizer installed: the Voice page says so |
| 065 | Scene workspace | `chats` | `test_workspace.py`, `js/workspace.e2e.mjs` | |
| 067 | Voice accessibility | in a chat | `test_app_speech.py` | the browser has no speech: the controls say so |
| 070 | Circling a spot on a sketch | in a chat | `test_reference_intent_page.py`, `js/reference.e2e.mjs` | |
| 072 | VN cues | in a chat | `js/staging.test.mjs`, `js/staging_timeline.test.mjs` | no art: plain-text stage directions |
| 059 | Places and routes | `world` | `test_app_world.py`, `js/world.e2e.mjs` | a missing place refuses `missing_place`; a stale review refuses `changed`; weighted routes, hazards, travel and maps are named as not available |
| 063 | Rumors | `world` | `test_app_world.py`, `js/world.e2e.mjs` | a rumor still on its way refuses `not_delivered`; sending, delivering and faction or public spread are named as not available |
| 057 | Faction decisions | `world` | `test_app_world.py`, `js/world.e2e.mjs` | the table's view says only that the page is the director's; a player's character refuses `npc_authority`; nothing is saved |
| 060 | What an NPC thinks others think | `world` | `test_app_world.py`, `js/world.e2e.mjs` | evidence the NPC does not hold refuses `unavailable_evidence`; nothing is saved and nothing is inferred |
| 049 | Reconciling divergent offline scenes | not on a screen yet | | Devices and sync keeps one side of a conflicting file and keeps the other |
| 054 | Reviewing a fork or retcon | not on a screen yet | | |
| 050 | Captured companions | not on a screen yet | | |
| 051 | Several NPCs answering at once | not on a screen yet | | |
| 056 | Source catalogs | not on a screen yet | | a campaign opts in through its chats' bindings |
| 058 | Two players at one screen | not on a screen yet | | |
| 062 | Ruleset action contracts | not on a screen yet | | |
| 064 | Scenario preparation | not on a screen yet | | |

"Not on a screen yet" is said on the page, with the reason, and never offered as a control. Those rows are the
work left before Wave 8 is user-visible complete.

## Not proved here

The page runs end to end in fakedom against a real app server. It has not been used in a real browser, with a
screen reader or on a phone.
