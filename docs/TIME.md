# A campaign's time and commitments (116)

The **Time and commitments** page (`#/time/<campaign id>?chat=<ChatRef>&audience=director`) is Wave 9's UI exit
for three features on campaign time: long activities a character pursues (074, [DOWNTIME](DOWNTIME.md)), what the
factions do offscreen (076, [OFFSCREEN](OFFSCREEN.md)), and quests and promises (077,
[COMMITMENTS](COMMITMENTS.md)). It also lists every user-facing first slice of Wave 9 (073 to 087), and either links
the screen it is on or says plainly that it is not on a screen yet. A campaign's hub (113) links to it as **Time**,
beside the Chronicle, for the same chat and view.

The page is served by the `time` adapter (`grimoire.app.adapters.time`, a package, owner 116). Like every adapter,
it works only over the existing domain services: `DowntimeLibrary`, `OffscreenLibrary` and `CommitmentLibrary`, each
built over the app's open library with the owner's trusted handle. All three keep their state in
`campaigns/<id>/scope.json` beside 055's calendar, which the Chronicle page ([CHRONICLE](CHRONICLE.md)) starts,
advances and schedules. The adapter keeps no store of its own.

## Whose it is

All of it is the director's. In the table's view the page says so, offers nothing and reads nothing of it; every
op but `time.coverage` refuses `director_only` unless `audience` is `director`.

## Reading

`time.overview` (`campaign`, `audience`, optional `chat`) answers the following. Like every op here it runs inside
the app's one serialized call, under the library's writer lock, so nothing is written between its reads; each
section still answers the `etag` it read, and the page reviews a change against its own domain's section `etag`.

- `clock`: whether the campaign clock is started, its calendar, the present moment and its deadlines, each with
  whether a change has flagged it;
- `cast`: the chosen chat's cast, each with its controller and role (`npc`, `player`, `unseated`), and whether
  the page may run downtime for it (`downtime: {available, controller, reason?, detail?}`, below);
- `downtime`, `offscreen` and `commitments`: each domain's section (below). A section a domain refuses answers
  `{error: {reason, message}}` and the other sections still answer.

With no clock started, the page says so and points to the Chronicle page. Nothing here estimates a time: a span is
the exact days and hours the director gives.

## Changing anything

Each write is a pair, the same for all three domains:

| Op | Args | Answer |
|---|---|---|
| `time.<domain>_plan` | `campaign`, `chat`, `audience`, `operation`, `operation_id`, `expected` (the etag shown), and the change | `{plan: {..., files}, etag}`; nothing is written |
| `time.<domain>_commit` | the same args (for a commitment decision also `reviewed_controls` as its plan answered them, and `endorse: true` for a step resting on the owner's own key) | `{done}` |

`<domain>` is `downtime`, `offscreen` or `commitment`. The page shows what the plan says it would do and the files
it would write, in a group labelled **Review this change**, and makes it only when the person presses **Make this
change** (or **Make this change with my key**). The commit builds the same change again with the same operation id
and etag:

- anything that changed the scope since the review refuses `changed`; the page reads everything again and drops
  the review. One record backs all three domains, so a change to any of them makes every other review stale;
- the same commit made again (an answer lost on the way back) finds the domain's own receipt and is
  `already_applied`: no time passes and no effort or development is credited twice;
- an operation id reused for another request is the domain's `operation_mismatch`;
- every other refusal keeps the domain's own reason (`uninitialized`, `unknown_activity`, `self_owed`,
  `citation_missing`, `npc_authority`, `scope_conflict`, ...). A refusal writes nothing.

After a change the page reads everything again and puts the focus on the clock's present moment; that form starts
afresh. **Cancel**, or a commit refused as `changed`, keeps everything typed, ticked and chosen in every form, and
puts the focus back on the first field of the form the review was made from.

## Downtime (074)

`downtime` lists every activity with its actor, kind, intent, required effort, progress, what remains, its status
(active, complete, abandoned), its outcome, whose it is to run (`whose`, with a chat) and each operation that made it
so.

**Whose activity it is.** 074 does not yet check who chose an activity: [DOWNTIME](DOWNTIME.md) leaves "checking
that the actor's controller, rather than the director, chose the activity" to later work. This page is strict
instead, as 077 is: the director begins, pursues or abandons an activity only for an actor the director controls (an
NPC, or a character no one is seated as) or for the owner's own character. A character another person plays is
theirs to choose for; every new operation on it refuses `not_your_character` and writes nothing, and the page
names it as not available, in **Begin an activity** and on each of its activities, which are not offered to pursue
or abandon. Control recorded twice refuses `ambiguous_control`. Control is read from the chosen chat's records, so
every new operation names a chat; a retry of one already made is 074's to answer from its receipt.

- `begin` (`activity`, `actor`, `kind`, `intent`, `required` minutes) is for one of the chosen chat's cast
  (`no_actor` otherwise). It spends no time, so it is offered with no clock started.
- `pursue` (`activity`, `minutes`, optional `effort` of at most `minutes`, `note`) spends exactly that span on the
  campaign clock and credits the effort, in one write. The review names the effort credited, whether it completes
  the activity, the clock before and after, and every deadline the span crosses. A crossed deadline is a flag only:
  the page says that nothing happens by itself. With no clock started it is not offered (074 refuses
  `uninitialized`).
- `abandon` (`activity`, `note`) settles an activity with the director's reason.

## Offscreen plans (076)

`offscreen` lists the adopted plans with their group, goal, the leader's choice, priority, each step's due moment
and whether it has developed, and the deadline a plan races (met or missed); the calendar's advances not yet
applied (`pending`), the applied ones, and whether a plan can be adopted here.

- `skip` (`advance`, `budget` from 1 to 32) applies one pending advance, a Chronicle advance or a downtime
  pursuit, by its own id. Advances are applied in the order the clock made them, so only the first pending one is
  offered; the page says each later one waits for it (076 refuses one out of order as `skip_out_of_order`). The review names each development, what is deferred past the budget and each deadline
  met or missed. A development is the director's alone: it delivers no knowledge, event, report or rumor, and no
  character learns of it.
- `adopt` (`group`, `leader`, `goals[]`, `policy`, `choice`, optional `preferences[]`, `plan`, `goal`, `steps[]` of
  `{label, minutes}`, `priority`, optional `deadline`). 076 adopts a plan only against a 057 decision that
  authorized it, and 057 has no store, so the director makes that decision here in the same reviewed flow. The
  page does it only when that can be done honestly: the group is one of the campaign's group instances, its members
  are the canonical `member_of` relations that hold now between it and the chosen chat's cast, and its leader is a
  member the director controls. Otherwise adoption is named as not available, with why: `uninitialized`,
  `no_chat`, `no_group`, `no_leader`, `pending_skips` (apply the pending advances first) or `limit`. A leader who
  is someone's character refuses 057's `npc_authority`. Only the plan, with the decision's id and choice, is saved.

## Commitments (077)

`commitments` lists every quest and promise with its kind, who owes it to whom (each obligor's present
controller), its status, its deadline as the calendar holds it now (found or not, passed, overdue for an open one,
late for a fulfilment recorded after it), whether its history is certain, and every step with the events it cites,
their support, and what became of it (applied, or skipped because it cites undone fiction, follows only from a
skipped step, or was voided). It also lists the objective events the director's ledger holds and 077 reads as
supported, newest first (at most 100), each with its turn in this chat.

- `offer` (`commitment`, `kind`, `summary`, `owed_by[]`, `owed_to[]` of the campaign's actors, `cites[]` of 1 to 16
  events, optional `deadline`: `{link: <deadline id>}` or `{new: {deadline, label, date, minute}}`, which schedules
  it on the calendar in the same write).
- `record` (`commitment`, `status`, `cites[]`, `note`). An outcome (`partially_fulfilled`, `fulfilled`, `lapsed`)
  is the director's adjudication. A decision (`accepted`, `refused`, `abandoned`) is read against the chosen chat's
  control records (`no_controls` without a chat): for obligors the director controls it is the director's; for the
  owner's own character it rests on what the owner said, so it must cite an event of a turn in this chat in which
  that character spoke (`no_turn`, `other_chat`, `unknown_turn`, `no_backing` otherwise), and is made only with the
  owner's key (`player_key_required` without `endorse: true`). The review shows the words of the owner's that the
  key backs, each with its character and turn (`backing`), and says they become the recorded backing of the
  decision. A character someone else plays is theirs to decide for with their own key, which this screen does not
  take: the page names it as not available (`not_your_character`).
- Control lives in the chat's records, not in the scope, so the `etag` cannot see it change. A decision's plan
  answers the obligors' controls it was reviewed under (`reviewed_controls`, `[actor, controller]` pairs), and the
  commit is refused `review_required` without them and `changed` when they differ from the controls now; `endorse:
  true` on a step that no longer rests on the owner's key is refused `changed` too. A refusal writes nothing.
- `void` (`target`, `reason`) withdraws a step or an offer recorded in error. One a player other than the owner
  backed refuses `not_your_character`.

A refused, abandoned or lapsed commitment keeps its deadline and shows it passed, and is never overdue.

## Wave 9's user-facing first slices

`time.coverage` answers this table (`grimoire.app.adapters.time.FEATURES`), and the page shows it. A row whose
screen is a page is checked by `test_app_time.py` to be a registered page.

| Slice | Feature | Where | Integration test | When it is not there |
|---|---|---|---|---|
| 073 | Hypothetical reply | in a chat: the composer's Imagine (`chats`) | `test_imagine_page.py`, `js/imagine.e2e.mjs` | while play is held up, a turn is open or no model is set up, it asks nothing and says why |
| 074 | Downtime | Time | `test_app_time.py`, `js/time.e2e.mjs`, `js/time.test.mjs` | the table's view refuses `director_only`; no clock: pursuing is not offered (`uninitialized`); another player's character's activity is named as not available and refuses `not_your_character` (stricter than 074, which leaves who chose it to later work in DOWNTIME.md); a stale review refuses `changed`, keeping what was typed; a retry spends no time again |
| 075 | Recorded rolls | Rules, for this campaign (`rules`, `#/rules/<campaign>`) | `test_app_rules.py`, `js/rules.e2e.mjs`, `js/rules.test.mjs` | an NPC's roll in the table's view is `director_only`; an unreviewed roll is `review_required`; a retry draws no die |
| 076 | Offscreen faction plans | Time | `test_app_time.py`, `js/time.e2e.mjs`, `js/time.test.mjs` | the table's view refuses `director_only`; adoption with no group (`no_group`), no leader the director controls (`no_leader`) or a pending advance is named as not available; a player's character as leader refuses `npc_authority`; only the first pending advance is offered; a skip writes only the scope |
| 077 | Quests and commitments | Time | `test_app_time.py`, `js/time.e2e.mjs`, `js/time.test.mjs` | the table's view refuses `director_only`; another player's character's decision is named as not available (`not_your_character`); the owner's own is made on the page with **Make this change with my key**, its review showing the words it rests on, and needs the key (`player_key_required`); a decision committed without its reviewed controls refuses `review_required`, and under other controls, or with a key it no longer rests on, `changed` |
| 078 | Relationships and attitudes | not on a screen yet (a chat's Details, a later part of 116) | | named on the page as not available |
| 079 | Source upgrades | a source's Authoring page (`authoring`), from its page in Characters and lore (`sources`) | `test_app_authoring.py` | see [AUTHORING_PAGE.md](AUTHORING_PAGE.md) |
| 080 | Speech to draft | in a chat: Dictate in the composer (`chats`) | `test_app_dictation.py`, `js/dictate.e2e.mjs` | voice off, no microphone or no recognizer: the strip says why and shows no Dictate |
| 081 | Story threads | `story`, for this chat | `test_story_page.py`, `js/story.e2e.mjs` | an unknown thread refuses `no_thread`; with no chat chosen the row says to open one |
| 082 | Read-only world audits | a source's Authoring page (`authoring`), from its page in Characters and lore (`sources`) | `test_app_authoring.py` | see [AUTHORING_PAGE.md](AUTHORING_PAGE.md) |
| 083 | Character voice performance | in a chat (`chat`): Listen, the kept lines in their cast voices; voices cast on Voices (`voices`) | `test_app_listen.py` | with no voice or no audio, each line is shown as text for its time |
| 084 | Cross-campaign scene transfer | not on a screen yet (a chat's Details, a later part of 116) | | named on the page as not available |
| 085 | Live context preview | in a chat, the director's view (`chat`): Preview context in the composer; also Models and costs (`providers`) | `test_app_listen.py`, `test_context_preview.py` | nothing is posted, kept or sent; the table's view shows no NPC's context |
| 086 | Mechanics module authoring | Rules, in the rail under Library (`rules`, `#/rules`) | `test_app_rules.py`, `js/rules.e2e.mjs`, `js/rules.test.mjs` | a draft with problems is `invalid_module`, each problem by its path; a stale save or publish is `changed`; a filled sheet and a graded check are shown as not saved |
| 087 | World authoring proposals | a source's Authoring page (`authoring`), from its page in Characters and lore (`sources`) | `test_app_authoring.py` | see [AUTHORING_PAGE.md](AUTHORING_PAGE.md) |

A later part of Sprint 116 that builds one of these screens flips its row to `screen` with its route, and the
page-registration test then checks it.

## Not proved here

The page runs end to end in fakedom against a real app server (`js/time.e2e.mjs`, launched by
`test_app_time.py`), including an accessibility audit of both views. It has not been used in a real browser, with
a screen reader or on a phone.
