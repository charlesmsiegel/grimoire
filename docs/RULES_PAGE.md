# The Rules page (116)

The **Rules** page is Wave 9's UI exit for the two mechanics features that a person can already use: authoring a
mechanics module (086) and a campaign's recorded rolls (075). It has two addresses:

- `#/rules?module=<id>&revision=<sha256:...>` is the authoring page. With no `module`, it opens a module by its id
  or starts a draft. With `module`, it shows that draft and its published revisions. With `revision` too, it shows
  that revision read-only and lets you try it. It is listed in the rail under Library.
- `#/rules/<campaign id>?chat=<ChatRef>&audience=director` holds a campaign's recorded rolls for one chat, and
  links to the authoring page. A campaign's hub (113) links to it beside Prep, for the same chat and view.

The page is served by the `rules` adapter (`grimoire.app.adapters.rules`, owner 116). Like every adapter, it works
only over the existing domain services. It uses 086's `MechanicsStore` and `grimoire.mechanics` for modules, and
075's `RollLibrary` for rolls, with the cast, controllers and turns read from the campaign hub's own scene. It keeps
no store of its own and adds nothing to either domain. Every write is reviewed first: a `*_plan` op writes nothing
and answers what the write would do. The write itself must match the state that was read: a save or publish
sends the etag of the draft that was read (and a publish the revision it was shown), and a roll sends the digest of
the request the plan named. The server recomputes these and refuses `changed` on any difference. It does not issue
a review token, so a caller that skips the plan but sends matching values is not refused.

## Mechanics modules (086)

| Op | Args | Answer |
|---|---|---|
| `rules.authoring` | | `{templates[], listed: {drafts: false, modules: false, revisions: true}, unavailable[], not_saved, max_draft_bytes, max_module_bytes}` |
| `rules.module` | `module` | `{module, file, draft: {etag, bytes, text} \| null, revisions[{revision, file}]}` |
| `rules.template` | `module`, `template` (`d20_basic`, `pool_basic` or `blank`) | `{module, template, text}`, with the template's id set to the draft's; nothing is written |
| `rules.check` | `module`, and `text` (as typed) or none (the saved draft) | `{module, of: text\|draft, etag, problems[{path, message, hint}], publishable, revision, file, published, module}`; nothing is written |
| `rules.save_plan` | `module`, `text`, `expected` (the etag that was read, or null for a new draft) | `{plan: {module, file, bytes, before, creates, unchanged}, check}`; nothing is written |
| `rules.save` | the same args | `{done: {module, already}, ...rules.module}` |
| `rules.publish_plan` | `module`, `expected` | `{plan: {module, etag, revision, file, already, name, version, family}}`; nothing is written |
| `rules.publish` | `module`, `expected`, `revision` (the one reviewed) | `{done: {module, revision, already, file}, ...rules.module}` |
| `rules.revision` | `module`, `revision` | `{module, file, text, view: {sheets[], checks[], ...}, not_saved}` |
| `rules.sheet` | `module`, `revision`, `sheet_type`, `values` | `{module, revision, sheet_type, valid, problems[{path, message}], values, derived, not_saved, saved: false}` |
| `rules.grade` | the same, and `check`, `dice` (the faces as rolled), `modifier`, `difficulty` | `{module, revision, sheet_type, derived, record: {check, dice, modifier, difficulty, total, successes, outcome}, confirmed, not_saved, saved: false}` |

- **Opening.** 086's store reads one draft by its id and lists the revisions of one id. It lists no drafts and no
  module ids, so the page opens a module by the id you type. It says this, and shows the ids opened in this tab.
- **Starting.** A draft starts from the d20 or pool template, or blank. The review names the file it creates. A
  second start of the same id refuses `draft_exists`, and the page links to the draft.
- **Saving.** A draft keeps any text you save, even unfinished JSON. The review shows how many problems the text
  has as saved. Saving the same text again (a retry) writes nothing and answers `already`. A draft saved by someone
  else since it was read refuses `changed`: the page reads it again and keeps your text as an unsaved change.
  Unsaved text kept in the tab remembers the etag of the draft it was begun from, and a save sends that etag. If
  the draft was saved from somewhere else meanwhile, the page says so and saves over it only when you choose
  **Keep my text over it**.
- **Checking.** Each problem is named by its dotted path (`checks.stealth.total`), or `$` for the whole file. A
  misspelled name carries 086's suggestion as `hint`, shown as "Did you mean stealth?". If the text changes while a
  check is on its way, the answer is not shown.
- **Publishing.** Only the saved draft publishes, so the page refuses to publish text that is not saved. A draft
  with any problem refuses `invalid_module`, and the page shows its problems. The review shows the revision digest
  and the file it writes. Publishing needs the reviewed etag and revision: without them it refuses
  `review_required`, and a draft saved since the review refuses `changed`. Publishing the same content again
  (even with a different layout) is the same revision, and answers `already`.
- **A revision** is shown exactly as published: its sheet types, checks and file, read-only. A revision file
  edited by hand refuses `altered`.
- **Trying it.** You can fill a sheet of the revision (each field's problem by its path, then the derived values)
  and grade a check from dice you enter (086 rolls nothing). The graded check is confirmed under the revision
  again. Neither is saved, and the page says so beside each: `saved` is always false.

086's own refusals keep their reasons: `invalid_id`, `no_draft`, `unpublished`, `invalid_revision`, `altered`,
`too_large`, `invalid_draft`. A sheet that does not fit refuses grading as `invalid_sheet`, and a check 086
refuses (wrong dice, an unknown check) as `check_refused`.

## Recorded rolls (075)

| Op | Args | Answer |
|---|---|---|
| `rules.rolls` | `campaign`, `chat` (optional), `audience` | `{campaign, chat, audience, rolls[], actors[], profiles[], no_roll, module_note, pinned: null, ledger, unavailable[]}` |
| `rules.roll_plan` | `campaign`, `chat`, `audience`, `actor`, `family`, `request_id`, `action`, and `text` (a player's character) | `{plan: {attempt, request, actor, name, family, edition, module, action, dice, backing, turn, review, already, files}, module_note}`; no die is drawn |
| `rules.roll` | the same args, and `turn` and `review` from the plan | `{done: {attempt, drawn}, roll}` |

A roll in the list shows its actor, the action, the dice and faces, the result, the attempt, 003's resolution and,
for a player's roll, the action that backs it.

- **Whose view.** The director's view lists every recorded roll of the campaign, and lets the director roll for
  the chat's NPCs and their own characters. The table's view lists only rolls backed by the owner's own actions,
  and lets the table roll only for the owner's own characters. A roll for an NPC in the table's view refuses
  `director_only`. A character controlled by someone else refuses `not_your_character`, one with no controller
  `no_controller`, and one outside the chat's cast `no_actor`.
- **A player's roll** is backed by the owner's own action contribution, typed on the page. Its id is the request
  id, it is given in the chat's latest turn at the time of the review, and it is written with the owner's own
  handle. Its attempt is the request id. Without text it refuses `invalid_field`. A chat with no turn
  refuses `no_turn`, and a turn that is not in the chat refuses `invalid_field`.
- **The director's roll for an NPC** is backed by nothing. Its attempt is `director:<request id>`. Text given for
  an NPC refuses `invalid_field`.
- **The module.** No ruleset is pinned for a campaign yet (062), so a roll is made under 062's reference profile
  for the family you pick (D20, WoD or PbtA), as on Prep. The page says so, and each roll records its module. The
  DC, modifier, pool and Hunger are entered as given. Nothing reads them from a sheet. A season of Ars Magica
  study rolls no die and refuses `no_roll`.
- **Review first, then roll once.** The review draws nothing and writes nothing. It shows the dice, the attempt,
  what backs the roll, the ledger it appends to and a digest of the request. **Roll** sends that digest back.
  Without it the roll refuses `review_required`, and with any other request it refuses `changed`. 075 draws the
  dice once and records them before the answer is shown.
- **Asking again.** The same request asked again (a retry, or **Ask again**) reads the same roll back and draws no
  die (`drawn: false`). The page keeps the request id while the values stay the same, until a roll is shown; the
  next review after that is a new request. Another request under the same attempt refuses `already_rolled`.
- **Earlier attempts in a turn.** 075 does not yet limit how many attempts one actor makes in a turn
  ([ROLLS](ROLLS.md), "One roll per attempt, not per turn"), and this page adds no limit of its own. Instead the
  review lists the actor's earlier rolls in the same chat and turn (`plan.earlier`), as "Anya Rook already rolled
  in this turn: ...", so a second attempt is a visible choice. A director's roll carries no turn, so an NPC's
  earlier rolls are only in the list.
- **Text that cannot be stored.** Typed text (what the character does, an action's words, a sheet's values) that
  does not encode as UTF-8, such as a lone surrogate, refuses `invalid_field`.
- **The draw is never the request's.** The page sends no faces, and the op ignores any. Tests set a scripted draw
  with the test-only `grimoire.app.adapters.rules.configure(api, draw)`. Configuring it twice raises.

075's other refusals keep their reasons (`attempt_adjudicated`, `mechanics_disabled`, `module_mismatch` and
others). A request 062 does not take refuses `invalid_action`, and a ledger that does not replay refuses
`rolls_unreadable`.

## Not on this screen

Each of these is named on the page with its reason and is never offered as a control:

- saving a filled sheet (086 has no sheet store);
- selecting a published revision for a campaign or chat (086 has no campaign selection), so a recorded roll uses
  062's reference profiles, not an authored module;
- recording a graded check (086 has no durable roll journal);
- a list of the library's drafts and modules (086's store lists none);
- narration reviewed against a roll in a scene (075 milestone 3: 035's scene runner and 029's review are not yet
  joined to recorded rolls), so no take is checked against a roll here;
- applying a roll's effect (075 applies none);
- choosing a partial result's cost, and opposed rolls (075 milestone 2).

## Wave 9's slices on this page

| Slice | Feature | Where | Integration test | When it is not there |
|---|---|---|---|---|
| 086 | Mechanics module authoring | Rules (`#/rules`) | `test_app_rules.py`, `js/rules.e2e.mjs`, `js/rules.test.mjs` | a draft with a problem refuses `invalid_module` with each problem by its path; a stale save or publish refuses `changed` and writes nothing; a filled sheet and a graded check are shown as not saved; a list of drafts, sheet storage and campaign selection are named as not available |
| 075 | Recorded rolls | Rules (`#/rules/<campaign>`), linked from the hub | `test_app_rules.py`, `js/rules.e2e.mjs`, `js/rules.test.mjs` | an NPC's roll in the table's view refuses `director_only`; a roll that was not reviewed refuses `review_required`, and one that differs refuses `changed`; a retry draws no die; narration review and effects are named as not available |

## Not proved here

The page runs end to end in fakedom against a real app server, with a scripted draw. It has not been used in a
real browser, with a screen reader or on a phone. The operating-system draw (`system_draw`) is 075's and is not
exercised through the page.
